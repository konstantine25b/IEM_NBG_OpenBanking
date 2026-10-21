"""Sharded dataset generation.

    plan_population (serial, cheap)  ->  N shards  ->  worker processes:
        simulate the shard's customers -> serve them through the bank dialects -> crawl like a TPP
        -> flatten the API bodies (harvest.py) -> write Parquet parts

Output layout (`out/`):
  api/<table>/part-*.parquet     accounts, balances, transactions: ONLY what the APIs returned
  truth/<table>/part-*.parquet   customers, accounts, ledger, loans, loan_schedule, deposits:
                                 the simulator's full state, for evaluation (never model features)
  captures/<bank>/<cid>.json     raw API conversation for a sample of customers
  MANIFEST.json, lineage.json    config, counts, column -> JSON-path lineage of the api tables

A customer's rows depend only on (config, its plan, the FX series), never on its shard mates, so the
result is identical for any number of workers.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from obmock.banks import BANKS
from obmock.calibration import LoanFrame
from obmock.config import WorldConfig
from obmock.engine.gateway import Gateway, Router
from obmock.export import capture_customer
from obmock.harvest import SCHEMAS, harvest, lineage, to_table
from obmock.world.model import Fx, World
from obmock.world.simulate import CustPlan, build_shard, make_fx, plan_population

PRIORITY_BANKS = ("tbc", "bog")  # the two banks that hold ~78% of the market
COMPRESSION = "zstd"

# ----------------------------------------------------------------------------- truth tables
TRUTH_SCHEMAS: dict[str, pa.Schema] = {
    "ledger": pa.schema([
        ("account_id", pa.string()), ("bank", pa.string()), ("cid", pa.string()),
        ("account_kind", pa.string()), ("entry_reference", pa.string()), ("txn_seq", pa.int64()),
        ("booking_date", pa.date32()), ("value_date", pa.date32()), ("ts", pa.timestamp("s")),
        ("amount_minor", pa.int64()), ("currency", pa.string()), ("status", pa.string()),
        ("kind", pa.string()), ("channel", pa.string()), ("narrative_ka", pa.string()),
        ("narrative_en", pa.string()), ("cp_name", pa.string()), ("cp_iban", pa.string()),
        ("cp_bic", pa.string()), ("merchant", pa.string()), ("mcc", pa.string()),
        ("city", pa.string()), ("pan_masked", pa.string()), ("e2e", pa.string()),
        ("fx_src_ccy", pa.string()), ("fx_rate", pa.float64()), ("fx_src_amount_minor", pa.int64()),
        ("balance_after_minor", pa.int64()),
    ]),
    "accounts": pa.schema([
        ("account_id", pa.string()), ("bank", pa.string()), ("cid", pa.string()),
        ("kind", pa.string()), ("currency", pa.string()), ("iban", pa.string()),
        ("product", pa.string()), ("opened", pa.date32()), ("status", pa.string()),
        ("usage", pa.string()), ("credit_limit_minor", pa.int64()),
        ("opening_balance_minor", pa.int64()), ("pan_masked", pa.string()),
        ("scheme", pa.string()), ("contract_no", pa.string()),
        ("closing_booked_minor", pa.int64()), ("interim_booked_minor", pa.int64()),
    ]),
    "loans": pa.schema([
        ("loan_id", pa.string()), ("bank", pa.string()), ("cid", pa.string()),
        ("account_id", pa.string()), ("funding_account_id", pa.string()), ("product", pa.string()),
        ("purpose_code", pa.int64()), ("currency", pa.string()), ("principal_minor", pa.int64()),
        ("rate", pa.float64()), ("rate_type", pa.string()), ("term_months", pa.int64()),
        ("start", pa.date32()), ("maturity", pa.date32()), ("pay_day", pa.int64()),
        ("instalment_minor", pa.int64()), ("outstanding_principal_minor", pa.int64()),
        ("overdue_principal_minor", pa.int64()), ("overdue_interest_minor", pa.int64()),
        ("overdue_count", pa.int64()), ("days_in_delinquency", pa.int64()),
        ("next_due", pa.date32()), ("next_amount_minor", pa.int64()), ("collateral", pa.bool_()),
        ("guarantee", pa.bool_()), ("restructured", pa.bool_()), ("refinanced", pa.bool_()),
    ]),
    "loan_schedule": pa.schema([
        ("loan_id", pa.string()), ("n", pa.int64()), ("due", pa.date32()),
        ("principal_minor", pa.int64()), ("interest_minor", pa.int64()),
        ("paid_on", pa.date32()), ("penalty_minor", pa.int64()),
    ]),
    "deposits": pa.schema([
        ("deposit_id", pa.string()), ("bank", pa.string()), ("cid", pa.string()),
        ("account_id", pa.string()), ("kind", pa.string()), ("currency", pa.string()),
        ("principal_minor", pa.int64()), ("rate", pa.float64()), ("term_months", pa.int64()),
        ("start", pa.date32()), ("maturity", pa.date32()), ("interest_paid_minor", pa.int64()),
        ("closed", pa.bool_()),
    ]),
}

CUSTOMER_BASE_FIELDS = [
    ("cid", pa.string()), ("customer_index", pa.int64()), ("person_id", pa.string()),
    ("first_en", pa.string()), ("last_en", pa.string()), ("first_ka", pa.string()),
    ("last_ka", pa.string()), ("sex", pa.int64()), ("birth_date", pa.date32()),
    ("msisdn", pa.string()), ("region_code", pa.int64()), ("region_name", pa.string()),
    ("city", pa.string()), ("segment", pa.string()), ("income_month_minor", pa.int64()),
    ("remittance_month_minor", pa.int64()), ("employer", pa.string()), ("sector", pa.int64()),
    ("salary_day", pa.int64()), ("banks", pa.string()), ("stress", pa.float64()),
    ("pd_pct", pa.float64()), ("score", pa.float64()), ("sim_tags", pa.string()),
]


def customers_schema(df_fields: tuple[tuple[str, str], ...]) -> pa.Schema:
    kinds = {"int": pa.int64(), "float": pa.float64(), "bool": pa.bool_(), "str": pa.string()}
    return pa.schema(CUSTOMER_BASE_FIELDS + [(f"df_{c}", kinds[k]) for c, k in df_fields])


def df_fields_of(frame: LoanFrame) -> tuple[tuple[str, str], ...]:
    out = []
    for col, dt in frame.df.dtypes.items():
        kind = {"i": "int", "u": "int", "f": "float", "b": "bool"}.get(dt.kind, "str")
        out.append((str(col), kind))
    return tuple(out)


def _table(schema: pa.Schema, columns: dict[str, list]) -> pa.Table:
    return pa.table({f.name: pa.array(columns[f.name], type=f.type) for f in schema}, schema=schema)


def truth_tables(world: World, df_fields: tuple[tuple[str, str], ...]) -> dict[str, pa.Table]:
    cols: dict[str, dict[str, list]] = {k: {f.name: [] for f in s} for k, s in TRUTH_SCHEMAS.items()}
    for a in world.accounts.values():
        bal = world.balances(a)
        row = {
            "account_id": a.id, "bank": a.bank, "cid": a.cid, "kind": a.kind,
            "currency": a.currency, "iban": a.iban, "product": a.product, "opened": a.opened,
            "status": a.status, "usage": a.usage, "credit_limit_minor": a.credit_limit,
            "opening_balance_minor": a.opening_balance, "pan_masked": a.pan_masked,
            "scheme": a.scheme, "contract_no": a.meta.get("contract_no"),
            "closing_booked_minor": bal["closingBooked"], "interim_booked_minor": bal["interimBooked"],
        }
        for k, v in row.items():
            cols["accounts"][k].append(v)
        led = cols["ledger"]
        for t in a.txns:
            led["account_id"].append(a.id)
            led["bank"].append(a.bank)
            led["cid"].append(a.cid)
            led["account_kind"].append(a.kind)
            led["entry_reference"].append(t.ref)
            led["txn_seq"].append(t.seq)
            led["booking_date"].append(t.booking_date)
            led["value_date"].append(t.value_date)
            led["ts"].append(t.ts)
            led["amount_minor"].append(t.amount)
            led["currency"].append(t.currency)
            led["status"].append(t.status)
            led["kind"].append(t.kind)
            led["channel"].append(t.channel)
            led["narrative_ka"].append(t.narrative_ka)
            led["narrative_en"].append(t.narrative_en)
            led["cp_name"].append(t.cp_name)
            led["cp_iban"].append(t.cp_iban)
            led["cp_bic"].append(t.cp_bic)
            led["merchant"].append(t.merchant)
            led["mcc"].append(t.mcc)
            led["city"].append(t.city)
            led["pan_masked"].append(t.pan_masked)
            led["e2e"].append(t.e2e)
            led["fx_src_ccy"].append(t.fx_src_ccy)
            led["fx_rate"].append(t.fx_rate)
            led["fx_src_amount_minor"].append(t.fx_src_amount)
            led["balance_after_minor"].append(t.balance_after)
    for ln in world.loans.values():
        row = {
            "loan_id": ln.id, "bank": ln.bank, "cid": ln.cid, "account_id": ln.account_id,
            "funding_account_id": ln.funding_account_id, "product": ln.product_key,
            "purpose_code": ln.purpose_code, "currency": ln.currency,
            "principal_minor": ln.principal, "rate": ln.rate, "rate_type": ln.rate_type,
            "term_months": ln.term_months, "start": ln.start, "maturity": ln.maturity,
            "pay_day": ln.pay_day, "instalment_minor": ln.instalment,
            "outstanding_principal_minor": ln.outstanding_principal,
            "overdue_principal_minor": ln.overdue_principal,
            "overdue_interest_minor": ln.overdue_interest, "overdue_count": ln.overdue_count,
            "days_in_delinquency": ln.days_in_delinquency, "next_due": ln.next_due,
            "next_amount_minor": ln.next_amount, "collateral": ln.collateral,
            "guarantee": ln.guarantee, "restructured": ln.restructured, "refinanced": ln.refinanced,
        }
        for k, v in row.items():
            cols["loans"][k].append(v)
        for i in ln.schedule:
            for k, v in {"loan_id": ln.id, "n": i.n, "due": i.due, "principal_minor": i.principal,
                         "interest_minor": i.interest, "paid_on": i.paid_on,
                         "penalty_minor": i.penalty}.items():
                cols["loan_schedule"][k].append(v)
    for x in world.deposits.values():
        row = {
            "deposit_id": x.id, "bank": x.bank, "cid": x.cid, "account_id": x.account_id,
            "kind": x.kind, "currency": x.currency, "principal_minor": x.principal, "rate": x.rate,
            "term_months": x.term_months, "start": x.start, "maturity": x.maturity,
            "interest_paid_minor": x.interest_paid, "closed": x.closed,
        }
        for k, v in row.items():
            cols["deposits"][k].append(v)
    out = {k: _table(TRUTH_SCHEMAS[k], cols[k]) for k in TRUTH_SCHEMAS}
    # customers: demographics, the df row, latent drivers; free-form simulator tags as JSON
    cs = customers_schema(df_fields)
    cc: dict[str, list] = {f.name: [] for f in cs}
    known = {f"df_{c}" for c, _ in df_fields} | {"stress", "pd_pct", "score"}
    for c in world.customers.values():
        base = {
            "cid": c.cid, "customer_index": c.index, "person_id": c.person_id,
            "first_en": c.first_en, "last_en": c.last_en, "first_ka": c.first_ka,
            "last_ka": c.last_ka, "sex": c.sex, "birth_date": c.birth_date, "msisdn": c.msisdn,
            "region_code": c.region_code, "region_name": c.region_name, "city": c.city,
            "segment": c.segment, "income_month_minor": c.income_month_cents,
            "remittance_month_minor": c.remit_month_cents, "employer": c.employer,
            "sector": c.sector, "salary_day": c.salary_day, "banks": "|".join(c.banks),
            "stress": c.truth.get("stress"), "pd_pct": c.truth.get("pd_pct"),
            "score": c.truth.get("score"),
            "sim_tags": json.dumps({k: v for k, v in c.truth.items() if k not in known},
                                   default=str, sort_keys=True),
        }
        for f in cs:
            cc[f.name].append(base[f.name] if f.name in base else c.truth.get(f.name))
    out["customers"] = _table(cs, cc)
    return out


# ----------------------------------------------------------------------------- shard worker
@dataclass(frozen=True)
class ShardTask:
    shard: int
    cfg: WorldConfig
    plans: tuple[CustPlan, ...]
    fx: Fx
    out: str
    df_fields: tuple[tuple[str, str], ...]
    captures: frozenset[tuple[int, str]]  # (customer ordinal, bank) pairs to keep as raw JSON


def _write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression=COMPRESSION)


def run_shard(task: ShardTask) -> dict[str, Any]:
    t0 = time.time()
    out = Path(task.out)
    world = build_shard(task.cfg, list(task.plans), task.fx)
    t_sim = time.time() - t0
    gw = Gateway(world, auth_mode="off")
    routers = {b: Router(d.routes()) for b, d in gw.dialects.items()}
    date_from = world.window_start.isoformat()
    rows: dict[str, list[dict]] = {"accounts": [], "balances": [], "transactions": []}
    n_captures = 0
    for p in task.plans:
        cid = f"C{p.n + 1:05d}"
        for bank in p.banks:
            cap = capture_customer(gw, bank, cid, date_from)
            d = gw.dialects[bank]
            got = harvest(cap, routers[bank], d.normalize_path)
            for k in rows:
                rows[k].extend(got[k])
            if (p.n, bank) in task.captures:
                f = out / "captures" / bank / f"{cid}.json"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(cap, ensure_ascii=False, indent=1))
                n_captures += 1
    name = f"part-{task.shard:05d}.parquet"
    counts: dict[str, int] = {}
    for table, r in rows.items():
        _write(to_table(table, r), out / "api" / table / name)
        counts[f"api.{table}"] = len(r)
    for table, tbl in truth_tables(world, task.df_fields).items():
        _write(tbl, out / "truth" / table / name)
        counts[f"truth.{table}"] = tbl.num_rows
    by_bank = Counter(r["bank"] for r in rows["transactions"])
    return {"shard": task.shard, "customers": len(task.plans), "counts": counts,
            "api_transactions_by_bank": dict(by_bank), "captures": n_captures,
            "seconds_simulate": round(t_sim, 2), "seconds_total": round(time.time() - t0, 2)}


# ----------------------------------------------------------------------------- orchestration
def capture_set(plans: list[CustPlan], n_priority: int, n_other: int) -> frozenset[tuple[int, str]]:
    """The first n customers (by ordinal) of each bank keep their raw API conversation."""
    quota = {b: (n_priority if b in PRIORITY_BANKS else n_other) for b in BANKS}
    out: set[tuple[int, str]] = set()
    for p in plans:
        for b in p.banks:
            if quota[b] > 0:
                out.add((p.n, b))
                quota[b] -= 1
    return frozenset(out)


def generate(cfg: WorldConfig, out: Path, *, workers: int = 8, shard_size: int = 250,
             api_sample: int = 200, api_sample_other: int = 30, verbose: bool = True) -> dict[str, Any]:
    t0 = time.time()
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if verbose else (lambda m: None)
    out.mkdir(parents=True, exist_ok=True)
    frame = LoanFrame.load(cfg.data_dir)
    plans = plan_population(cfg)
    fx = make_fx(cfg)
    df_fields = df_fields_of(frame)
    caps = capture_set(plans, api_sample, api_sample_other)
    shards = [plans[i:i + shard_size] for i in range(0, len(plans), shard_size)]
    tasks = [ShardTask(i, cfg, tuple(s), fx, str(out), df_fields,
                       frozenset(c for c in caps if s[0].n <= c[0] <= s[-1].n))
             for i, s in enumerate(shards)]
    log(f"planned {len(plans):,} customers in {len(tasks)} shards ({time.time() - t0:.1f}s); "
        f"workers={workers}")
    results: list[dict[str, Any]] = []
    if workers <= 1:
        for t in tasks:
            results.append(run_shard(t))
            log(f"  shard {t.shard + 1}/{len(tasks)} done")
    else:
        ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futs = [pool.submit(run_shard, t) for t in tasks]
            for k, f in enumerate(as_completed(futs), 1):
                results.append(f.result())
                if k % max(1, len(tasks) // 10) == 0 or k == len(tasks):
                    log(f"  {k}/{len(tasks)} shards done ({time.time() - t0:.0f}s)")
    results.sort(key=lambda r: r["shard"])
    totals: Counter[str] = Counter()
    by_bank: Counter[str] = Counter()
    for r in results:
        totals.update(r["counts"])
        by_bank.update(r["api_transactions_by_bank"])
    primary = Counter(p.banks[0] for p in plans)
    members = Counter(b for p in plans for b in p.banks)
    manifest = {
        "generator": "obmock", "synthetic": True, "profile": cfg.profile,
        "config": {"seed": cfg.seed, "customers": cfg.n_customers, "months": cfg.months,
                   "as_of": cfg.as_of.isoformat(), "shard_size": shard_size},
        "rows": dict(totals),
        "customers_by_primary_bank": dict(primary), "customers_by_membership": dict(members),
        "api_transactions_by_bank": dict(by_bank),
        "captures": sum(r["captures"] for r in results),
        "seconds": round(time.time() - t0, 1),
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    (out / "lineage.json").write_text(json.dumps(lineage(), indent=1))
    fx_rows = [(ccy, d, r) for ccy, series in fx.rates.items() for d, r in series.items()]
    _write(pa.table({"date": pa.array([d for _, d, _ in fx_rows], pa.date32()),
                     "currency": [c for c, _, _ in fx_rows],
                     "gel_per_unit": pa.array([r for _, _, r in fx_rows], pa.float64())}),
           out / "truth" / "fx_rates" / "part-00000.parquet")
    log(f"done in {manifest['seconds']}s: {totals.get('api.transactions', 0):,} API transactions, "
        f"{totals.get('truth.ledger', 0):,} ledger entries")
    return manifest


__all__ = ["generate", "run_shard", "ShardTask", "SCHEMAS", "TRUTH_SCHEMAS"]
