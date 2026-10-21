"""Command line: `python -m obmock {serve|generate|banks|validate} ...`."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

from obmock.banks import BANKS
from obmock.config import (
    DEFAULT_AS_OF,
    DEFAULT_DATA_DIR,
    DEFAULT_SEED,
    OUTPUT_DIR,
    WorldConfig,
)


def _add_world_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--customers", type=int, default=240, help="simulated customers (default 240)")
    p.add_argument("--months", type=int, default=12, help="months of history (default 12)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--as-of", default=DEFAULT_AS_OF.isoformat(),
                   help="the world's 'today' (YYYY-MM-DD, or the word 'today'); fixed by default "
                        "so runs are reproducible")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                   help="folder with the supplied df_*.csv loan dataset")
    p.add_argument("--min-per-bank", type=int, default=3,
                   help="minimum customers per bank (so small banks still have data in tiny worlds)")
    p.add_argument("--profile", choices=("spec-complete", "evidence-only"), default="spec-complete",
                   help="spec-complete: fill the standard-coded fields a bank's schema defines "
                        "(bankTransactionCode, purposeCode, mandateId, balanceAfterTransaction); "
                        "evidence-only: leave those four empty, as no bank example shows them")


def _cfg(a: argparse.Namespace) -> WorldConfig:
    as_of = date.today() if a.as_of == "today" else date.fromisoformat(a.as_of)
    return WorldConfig(seed=a.seed, n_customers=a.customers, months=a.months,
                       as_of=as_of, data_dir=a.data_dir,
                       min_customers_per_bank=a.min_per_bank, profile=a.profile)


def _build(a: argparse.Namespace):
    from obmock.world import build_world

    t = time.time()
    world = build_world(_cfg(a))
    s = world.stats()
    print(f"world built in {time.time() - t:.1f}s: {s['customers']} customers, "
          f"{s['accounts']} accounts, {s['transactions']:,} transactions, {s['loans']} loans, "
          f"{s['deposits']} deposits", file=sys.stderr)
    return world


def cmd_banks(_: argparse.Namespace) -> int:
    print(f"{'bank':10} {'dialect':13} {'evidence':9} {'IBAN':4} {'BIC':9} {'AIS ms':>7}  notes")
    for k, b in BANKS.items():
        print(f"{k:10} {b.dialect:13} {b.evidence:9} {b.iban_code:4} {b.bic:9} "
              f"{b.kpi.ais_ms:7.0f}  {b.notes[:70]}")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    from obmock.server import create_app

    app = create_app(_build(a), auth_mode=a.auth, chaos=a.chaos, expose_truth=a.expose_truth)
    print(f"serving on http://{a.host}:{a.port}  (try: curl -X POST "
          f"http://{a.host}:{a.port}/_mock/session/tbc)", file=sys.stderr)
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")
    return 0


def cmd_generate(a: argparse.Namespace) -> int:
    from obmock.pipeline import generate

    out = Path(a.out)
    m = generate(_cfg(a), out, workers=a.workers, shard_size=a.shard_size,
                 api_sample=a.api_sample, api_sample_other=a.api_sample_other)
    print(f"wrote {out.resolve()}", file=sys.stderr)
    for k, v in sorted(m["rows"].items()):
        print(f"  {k:24} {v:>12,} rows", file=sys.stderr)
    print(f"  primary bank: {m['customers_by_primary_bank']}", file=sys.stderr)
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    from obmock.report import run_report

    text, ok = run_report(Path(a.dataset), workers=a.workers)
    print(text)
    return 0 if ok else 1


def cmd_calibration_doc(_: argparse.Namespace) -> int:
    from obmock.calibration_sources import render_markdown

    print(render_markdown(), end="")
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    from obmock.validate import run_all

    world = _build(a)
    return 0 if run_all(world, verbose=True) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="obmock", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the mock HTTP server for all 12 banks")
    _add_world_args(s)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--auth", choices=("strict", "off"), default="strict",
                   help="strict = Bearer + Consent-ID enforced; off = pick customer with "
                        "X-Mock-Customer / ?psu=")
    s.add_argument("--chaos", choices=("off", "errors", "kpi"), default="off",
                   help="inject failures (errors) and latency (kpi) from each bank's KPIs")
    s.add_argument("--expose-truth", action="store_true",
                   help="serve hidden ground truth at /_mock/truth/{cid} (evaluation only)")
    s.set_defaults(fn=cmd_serve)

    g = sub.add_parser("generate", help="write the dataset: API-derived tables + hidden truth (Parquet)")
    _add_world_args(g)
    g.set_defaults(customers=50_000)
    g.add_argument("--out", type=Path, default=OUTPUT_DIR / "dataset")
    g.add_argument("--workers", type=int, default=max(1, min(16, (os.cpu_count() or 2) - 2)),
                   help="worker processes (output is identical for any value)")
    g.add_argument("--shard-size", type=int, default=250, help="customers per shard")
    g.add_argument("--api-sample", type=int, default=200,
                   help="raw JSON API captures kept for this many customers of each of TBC and BoG")
    g.add_argument("--api-sample-other", type=int, default=30,
                   help="... and of each other bank")
    g.set_defaults(fn=cmd_generate)

    b = sub.add_parser("banks", help="print the bank/dialect table")
    b.set_defaults(fn=cmd_banks)

    r = sub.add_parser("report", help="quality report for a generated dataset (standards + calibration)")
    r.add_argument("--dataset", type=Path, default=OUTPUT_DIR / "dataset")
    r.add_argument("--workers", type=int, default=max(1, min(16, (os.cpu_count() or 2) - 2)))
    r.set_defaults(fn=cmd_report)

    cd = sub.add_parser("calibration-doc", help="print docs/CALIBRATION.md (targets, sources, status)")
    cd.set_defaults(fn=cmd_calibration_doc)

    v = sub.add_parser("validate", help="ledger invariants + spec-schema validation of responses")
    _add_world_args(v)
    v.set_defaults(fn=cmd_validate)

    args = p.parse_args(argv)
    return int(args.fn(args))
