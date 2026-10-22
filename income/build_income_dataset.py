#!/usr/bin/env python3
"""One row per person for the 100,000-customer open-banking dataset.

Label: INCOME_AMOUNT_TOTAL (declared monthly income, GEL).

Features: the loan-file columns (person, RS remittances, credit history) plus the
open-banking aggregates computed from every api/ shard. Card-account copies are
dropped. Foreign amounts are converted at the mean NBG rate over the 12 months.

Left out on purpose:
  income_month_minor, remittance_month_minor  copies of the label and of SOI04
  PTI_RATIO                                   payment / income, so it contains the label
  stress, sim_tags, names, phone, person id   simulator bookkeeping, not an input
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import multiprocessing as mp
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from EDA import MOCK_FEATURE_NAMES, scan_shard

ROOT = REPO / "openbanking_mock" / "output" / "dataset"
OUT = HERE / "income_dataset.csv"

# Registry columns that are the label, or the label divided into something else.
DROP_DF = {"df_INCOME_AMOUNT_TOTAL", "df_PTI_RATIO"}
# truth.customers columns worth keeping beside the df_* registry fields.
KEEP_PERSON = ["segment", "region_name", "city", "banks"]


def window_of(root: Path) -> dict:
    man = json.loads((root / "MANIFEST.json").read_text())
    cfg = man["config"]
    as_of = pd.Timestamp(cfg["as_of"])
    months = int(cfg["months"])
    start = as_of - pd.DateOffset(months=months)
    fx = pd.read_parquet(root / "truth" / "fx_rates" / "part-00000.parquet")
    fx["date"] = pd.to_datetime(fx["date"])
    in_window = fx[(fx["date"] >= start) & (fx["date"] <= as_of)]
    rates = {"GEL": 1.0}
    rates.update((in_window if len(in_window) else fx).groupby("currency")["gel_per_unit"].mean().to_dict())
    return {"as_of": as_of, "months": months, "start": start, "fx": rates}


def shard_features(shard: int, root: str, window: dict) -> pd.DataFrame:
    base = Path(root)
    name = f"part-{shard:05d}.parquet"
    cids = pd.read_parquet(base / "truth" / "customers" / name, columns=["cid"])["cid"].tolist()
    feats, _ = scan_shard(
        (
            base / "api" / "transactions" / name,
            base / "api" / "accounts" / name,
            base / "api" / "balances" / name,
            cids,
            window,
        )
    )
    return feats


def main() -> int:
    t0 = time.perf_counter()
    if not (ROOT / "MANIFEST.json").exists():
        print(f"missing {ROOT / 'MANIFEST.json'}", file=sys.stderr)
        return 1
    window = window_of(ROOT)
    n_shards = len(list((ROOT / "truth" / "customers").glob("part-*.parquet")))
    missing = [
        f"{layer}/{table}/part-{i:05d}.parquet"
        for layer, table in (("api", "transactions"), ("api", "accounts"), ("api", "balances"))
        for i in range(n_shards)
        if not (ROOT / layer / table / f"part-{i:05d}.parquet").is_file()
    ]
    if missing:
        print(f"missing {len(missing)} shards, first: {missing[0]}", file=sys.stderr)
        return 1
    print(f"{n_shards} shards, window {window['start'].date()} .. {window['as_of'].date()}", flush=True)

    parts: list[pd.DataFrame] = []
    ctx = mp.get_context("fork")
    workers = min(8, n_shards)
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futs = [pool.submit(shard_features, i, str(ROOT), window) for i in range(n_shards)]
        for k, fut in enumerate(as_completed(futs), 1):
            parts.append(fut.result())
            if k % 40 == 0 or k == n_shards:
                print(f"  {k}/{n_shards} shards  ({time.perf_counter() - t0:.0f}s)", flush=True)
    ob = pd.concat(parts).sort_index()
    ob.index.name = "cid"
    ob.columns = [f"ob_{c}" for c in ob.columns]
    missing = [f"ob_{c}" for c in MOCK_FEATURE_NAMES if f"ob_{c}" not in ob.columns]
    if missing:
        raise RuntimeError(f"open-banking features missing: {missing}")

    people = pd.read_parquet(ROOT / "truth" / "customers")
    people = people.set_index("cid")
    label = people["df_INCOME_AMOUNT_TOTAL"].rename("INCOME_AMOUNT_TOTAL")
    df_cols = [c for c in people.columns if c.startswith("df_") and c not in DROP_DF]
    keep = people[KEEP_PERSON + df_cols]
    keep = keep.rename(columns={c: c.removeprefix("df_") for c in df_cols})
    out = keep.join(ob, how="inner")
    out.insert(0, "INCOME_AMOUNT_TOTAL", label)
    if len(out) != len(people) or out["INCOME_AMOUNT_TOTAL"].isna().any():
        raise RuntimeError(f"join failed: {len(out)} rows, people {len(people)}")
    out = out.reset_index()
    out.to_csv(OUT, index=False)
    print(
        f"wrote {OUT}  {len(out):,} rows x {out.shape[1]} columns  ({time.perf_counter() - t0:.0f}s)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
