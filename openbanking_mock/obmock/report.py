"""Quality report for a generated dataset.

Two different questions, answered on two different layers:
  * Is what a TPP can retrieve (api/ tables) faithful and standard?   -> checks that must be 100%
  * Does the simulated world (truth/ tables) match the sourced Georgian targets? -> calibration_sources
Each shard (a customer's accounts never span shards) is analysed independently, in parallel, so the
report stays memory-light at 50,000 customers.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

from obmock.banks import BANKS
from obmock.calibration_sources import TARGETS, Target
from obmock.config import SPEC_DIR
from obmock.util import georgian_holidays, is_valid_iban

ISO_4217 = {"GEL", "USD", "EUR", "GBP", "XXX"}
BALANCE_TYPES = {s.lower() for s in (
    "closingBooked", "expected", "openingBooked", "interimAvailable", "interimBooked", "forwardAvailable",
    "nonInvoiced", "information")}
SPEND_GROUPS = {
    "food": ("grocery", "kiosk", "restaurant", "fastfood", "delivery"),
    "transport": ("fuel", "rideshare", "transit", "travel"),
    "housing_utilities": ("utility", "rent"),
    "health": ("pharmacy", "health"),
    "clothing": ("clothing",),
    "durables": ("electronics", "ecommerce"),
}
CARD_KINDS = {"grocery", "kiosk", "restaurant", "fastfood", "pharmacy", "transit", "fuel", "clothing",
              "health", "electronics", "rideshare", "delivery", "entertainment", "ecommerce", "gambling",
              "travel", "atm_cash"}
IBAN_CODE = {k: b.iban_code for k, b in BANKS.items()}


def _official() -> tuple[set[str], set[str]]:
    d = SPEC_DIR / "iso20022"
    with (d / "bank_transaction_codes.csv").open(encoding="utf-8") as fh:
        triples = {f"{r['domain']}-{r['family']}-{r['subfamily']}" for r in csv.DictReader(fh)}
    with (d / "external_purpose_codes.csv").open(encoding="utf-8") as fh:
        purposes = {r["code"] for r in csv.DictReader(fh)}
    return triples, purposes


class Checks:
    def __init__(self) -> None:
        self.ok: Counter[str] = Counter()
        self.n: Counter[str] = Counter()
        self.ex: dict[str, list[str]] = {}

    def add(self, name: str, ok: pd.Series | int, total: int | None = None,
            examples: pd.Series | None = None) -> None:
        """Record a check: `ok` is a boolean Series (one flag per row) or a pass count + `total`."""
        if isinstance(ok, pd.Series):
            total, good = len(ok), int(ok.sum())
            if examples is not None and good < total:
                self.ex.setdefault(name, []).extend(str(x) for x in list(examples[~ok])[:3])
        else:
            good = ok
        self.ok[name] += good
        self.n[name] += int(total or 0)


def _read(root: Path, area: str, table: str, i: int, columns: list[str] | None = None) -> pd.DataFrame:
    return pd.read_parquet(root / area / table / f"part-{i:05d}.parquet", columns=columns)


def analyse_shard(root_s: str, i: int) -> dict[str, Any]:
    root = Path(root_s)
    triples, purposes = _official()
    c = Checks()
    m: Counter[str] = Counter()
    tx = _read(root, "api", "transactions", i)
    acc = _read(root, "api", "accounts", i)
    bal = _read(root, "api", "balances", i)

    # ------------------------------------------------------------------ API layer: identifiers
    ib = acc.dropna(subset=["iban"])
    c.add("IBAN passes ISO 13616 mod-97", ib["iban"].map(is_valid_iban), examples=ib["iban"])
    c.add("IBAN is GE + 2 check + 2 letters + 16 digits",
          ib["iban"].str.fullmatch(r"GE\d{2}[A-Z]{2}\d{16}"), examples=ib["iban"])
    bic = acc["bic"].dropna()
    c.add("BIC matches ISO 9362", bic.str.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?"), examples=bic)
    for name, s in (("accounts", acc["currency"]), ("balances", bal["currency"]),
                    ("transactions", tx["currency"])):
        s = s.dropna()
        c.add(f"{name}: currency is ISO 4217", s.isin(ISO_4217), examples=s)
    c.add("no duplicate entry within a (bank, endpoint)",
          int((~tx.duplicated(["bank", "source_endpoint", "entry_key"])).sum()), len(tx))
    bt = bal["balance_type"].dropna()
    c.add("balance type is a Berlin Group / bank-published type", bt.str.lower().isin(BALANCE_TYPES),
          examples=bt)

    # ------------------------------------------------------------------ API layer: transactions
    pend, booked = tx[tx["status"] == "pending"], tx[tx["status"] == "booked"]
    c.add("pending entry has no bookingDate", pend["booking_date"].isna(), examples=pend["entry_key"])
    c.add("booked entry has a bookingDate", booked["booking_date"].notna(), examples=booked["entry_key"])
    codes = tx["bank_transaction_code"].dropna()
    c.add("bankTransactionCode is an official ISO 20022 triple", codes.isin(triples), examples=codes)
    pc = tx["purpose_code"].dropna()
    c.add("purposeCode is an official ISO 20022 code", pc.isin(purposes), examples=pc)
    with_pc = tx.dropna(subset=["purpose_code"])
    c.add("purposeCode only on credit transfers / direct debits",
          with_pc["bank_transaction_code"].fillna("").str.startswith(("PMNT-ICDT", "PMNT-RCDT", "PMNT-RDDT")),
          examples=with_pc["bank_transaction_code"])
    with_md = tx.dropna(subset=["mandate_id"])
    c.add("mandateId only on direct debits", with_md["bank_transaction_code"] == "PMNT-RDDT-PMDD",
          examples=with_md["bank_transaction_code"])
    fx = tx.dropna(subset=["fx_rate"])
    foreign = fx["fx_source_currency"].where(fx["fx_source_currency"] != "GEL", fx["fx_target_currency"])
    c.add("currencyExchange.unitCurrency is the foreign currency", fx["fx_unit_currency"] == foreign,
          examples=fx["entry_key"])
    c.add("exchange rate is a plausible GEL price (1.5-6)", fx["fx_rate"].between(1.5, 6.0),
          examples=fx["fx_rate"])
    # card-account entries carry cardTransactionId but no entryReference, so test account entries only
    pri = tx[tx["bank"].isin(["tbc", "bog"]) & tx["transaction_id"].notna()
             & (tx["source_endpoint"] == "accounts")]
    c.add("TBC/BoG transactionId = c_/d_ + entryReference",
          pri["transaction_id"].str[2:] == pri["entry_reference"], examples=pri["transaction_id"])
    c.add("credit/debit prefix matches the sign",
          (pri["transaction_id"].str[0] == "c") == (pri["amount"].astype(float) > 0),
          examples=pri["transaction_id"])

    # balanceAfterTransaction is the running balance; embedded interimBooked equals the last one
    a = booked[(booked["source_endpoint"] == "accounts") & booked["balance_after_amount"].notna()].copy()
    if len(a):
        a["amt"], a["bal"] = a["amount"].astype(float), a["balance_after_amount"].astype(float)
        a = a.sort_values(["bank", "psu_id", "account_resource_id", "entry_reference"])
        g = a.groupby(["bank", "psu_id", "account_resource_id"])
        resid = (g["bal"].diff() - a["amt"]).abs().dropna()
        c.add("balanceAfterTransaction moves by exactly the booked amount", resid < 5e-4)
        last = g.tail(1).set_index(["bank", "account_resource_id"])["bal"]
        emb = bal[(bal["origin"] == "embedded") & (bal["balance_type"] == "interimBooked")]
        emb = emb.set_index(["bank", "resource_id"])["amount"].astype(float)
        common = last.index.intersection(emb.index)
        c.add("embedded interimBooked equals the balance after the last booked entry",
              (last.loc[common] - emb.loc[common]).abs() < 5e-4)

    # ------------------------------------------------------------------ truth layer
    cust = _read(root, "truth", "customers", i, ["cid", "banks", "income_month_minor"])
    tacc = _read(root, "truth", "accounts", i, ["cid", "bank", "kind", "scheme"])
    led = _read(root, "truth", "ledger", i, ["cid", "bank", "account_kind", "booking_date", "amount_minor",
                                              "kind", "narrative_en", "cp_iban", "status"])
    loans = _read(root, "truth", "loans", i)
    deps = _read(root, "truth", "deposits", i)
    m["customers"] = len(cust)
    prim = cust["banks"].str.split("|").str[0]
    for b, n in prim.value_counts().items():
        m[f"primary.{b}"] += int(n)
    m["income_sum"] += int(cust["income_month_minor"].sum())
    cur = tacc[tacc["kind"] == "current"]
    m["debit_cards"], m["debit_visa"] = len(cur), int((cur["scheme"] == "VISA").sum())
    cc = tacc[tacc["kind"] == "card_credit"]
    m["credit_holders"] = cc["cid"].nunique()
    m["credit_cards"], m["credit_visa"] = len(cc), int((cc["scheme"] == "VISA").sum())
    for b, n in tx[~tx["is_card_mirror"].astype(bool)]["bank"].value_counts().items():
        m[f"api_rows.{b}"] += int(n)
    for b, v in led.assign(v=led["amount_minor"].abs()).groupby("bank")["v"].sum().items():
        m[f"ledger_value.{b}"] += int(v)

    card = led[led["kind"].isin(CARD_KINDS)]
    m["card_txns"], m["atm_txns"] = len(card), int((card["kind"] == "atm_cash").sum())
    out = led[(led["amount_minor"] < 0) & led["account_kind"].isin(["current", "card_credit"])]
    for g_, kinds in SPEND_GROUPS.items():
        m[f"spend.{g_}"] += int(-out[out["kind"].isin(kinds)]["amount_minor"].sum())
    pri_ids = set(cust["cid"][prim.isin(["tbc", "bog"])])
    m["tbcbog_primary"] = len(pri_ids)
    sms = led[(led["kind"] == "fee") & (led["narrative_en"] == "Service fee") & (led["amount_minor"] == -100)]
    m["paid_sms"] = len(set(sms["cid"]) & pri_ids)

    t = deps[deps["kind"] == "term"]
    m["term_deposits"], m["term_fx"] = len(t), int((t["currency"] != "GEL").sum())
    m["fx_deposits_usd"] = int((deps["currency"] == "USD").sum())
    m["fx_deposits_eur"] = int((deps["currency"] == "EUR").sum())
    for key, sel in (("gel", t[t["currency"] == "GEL"]), ("fx", t[t["currency"] != "GEL"])):
        m[f"rate_sum_{key}"] += float(sel["rate"].sum())
        m[f"rate_n_{key}"] += len(sel)
    for key, sel in (("consumer", loans[loans["product"] == "consumer"]),
                     ("mortgage", loans[loans["product"].str.startswith("mortgage")])):
        sel = sel[sel["currency"] == "GEL"]
        m[f"loan_rate_sum_{key}"] += float(sel["rate"].sum())
        m[f"loan_rate_n_{key}"] += len(sel)
    lj = loans.merge(cust[["cid", "income_month_minor"]], on="cid")
    lj = lj[lj["income_month_minor"] > 0]
    limit = (lj["income_month_minor"] < 150_000).map({True: 0.25, False: 0.50})  # NBG PTI, GEL loans
    m["pti_loans"], m["pti_over"] = len(lj), int((lj["instalment_minor"] / lj["income_month_minor"] > limit).sum())
    m["overdue_customers"] = loans[loans["overdue_count"] > 0]["cid"].nunique()

    # settlement: interbank credits, salaries and card purchases are booked on business days only
    d = pd.to_datetime(led["booking_date"])
    bday = (d.dt.weekday < 5) & ~d.dt.date.isin(georgian_holidays())
    own = led["cp_iban"].fillna("").str[4:6] == led["bank"].map(IBAN_CODE)
    interbank = led["cp_iban"].notna() & ~own  # a same-bank transfer posts at once, any day
    settle = (led["kind"] == "salary") | (led["kind"].isin(["business_income", "p2p_in"]) & interbank)
    c.add("interbank credits and salaries are booked on business days", bday[settle], examples=d[settle])
    pos = (led["kind"].isin(CARD_KINDS - {"atm_cash"})) & (led["status"] == "booked")
    c.add("card purchases are booked on business days", bday[pos], examples=d[pos])

    return {"ok": dict(c.ok), "n": dict(c.n), "ex": c.ex, "m": dict(m),
            "ibans": list(zip(acc["bank"], acc["iban"].fillna(""), acc["currency"].fillna(""),
                              acc["source_endpoint"], strict=True))}


# ----------------------------------------------------------------------------- reduction
def _pct(a: float, b: float) -> float:
    return 100.0 * a / b if b else float("nan")


def measure(m: Counter) -> dict[str, float]:
    n = m["customers"]
    out: dict[str, float] = {}
    for k in BANKS:
        out[f"share.primary.{k}"] = _pct(m[f"primary.{k}"], n)
    out["share.primary.bog_tbc"] = out["share.primary.bog"] + out["share.primary.tbc"]
    rows = sum(m[f"api_rows.{k}"] for k in BANKS)
    out["share.api_rows.bog_tbc"] = _pct(m["api_rows.bog"] + m["api_rows.tbc"], rows)
    out["cards.debit_visa"] = _pct(m["debit_visa"], m["debit_cards"])
    out["cards.credit_visa"] = _pct(m["credit_visa"], m["credit_cards"])
    out["cards.credit_holders"] = _pct(m["credit_holders"], n)
    out["cards.atm_share"] = _pct(m["atm_txns"], m["card_txns"])
    out["deposits.term_fx_share"] = _pct(m["term_fx"], m["term_deposits"])
    out["deposits.usd_of_fx"] = _pct(m["fx_deposits_usd"], m["fx_deposits_usd"] + m["fx_deposits_eur"])
    out["deposits.rate_gel"] = 100 * m["rate_sum_gel"] / max(1, m["rate_n_gel"])
    out["deposits.rate_fx"] = 100 * m["rate_sum_fx"] / max(1, m["rate_n_fx"])
    out["fees.paid_sms"] = _pct(m["paid_sms"], m["tbcbog_primary"])
    spend = sum(m[f"spend.{g}"] for g in SPEND_GROUPS)
    for g in SPEND_GROUPS:
        out[f"spend.{g}"] = _pct(m[f"spend.{g}"], spend)
    out["info.income_mean"] = m["income_sum"] / 100.0 / max(1, n)
    out["info.rate_gel_consumer"] = 100 * m["loan_rate_sum_consumer"] / max(1, m["loan_rate_n_consumer"])
    out["info.rate_gel_mortgage"] = 100 * m["loan_rate_sum_mortgage"] / max(1, m["loan_rate_n_mortgage"])
    out["info.loans_over_nbg_pti"] = _pct(m["pti_over"], m["pti_loans"])
    out["info.overdue_customers"] = _pct(m["overdue_customers"], n)
    return out


def _grade(t: Target, v: float) -> tuple[str, str]:
    if not t.gate:
        return "info", "info"
    return ("PASS" if abs(v - t.target) <= t.tol else "FAIL"), f"±{t.tol:g} {t.unit}"


def run_report(root: Path, workers: int = 8, write: bool = True) -> tuple[str, bool]:
    shards = sorted(int(p.stem.split("-")[1]) for p in (root / "api" / "transactions").glob("part-*.parquet"))
    ok: Counter[str] = Counter()
    n: Counter[str] = Counter()
    ex: dict[str, list[str]] = {}
    m: Counter[str] = Counter()
    ibans: Counter[tuple] = Counter()
    if workers <= 1:
        results = [analyse_shard(str(root), i) for i in shards]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(analyse_shard, [str(root)] * len(shards), shards))
    for r in results:
        ok.update(r["ok"])
        n.update(r["n"])
        m.update(r["m"])
        for k, v in r["ex"].items():
            ex.setdefault(k, []).extend(v)
        ibans.update(t for t in r["ibans"] if t[1])
    dup = sum(1 for v in ibans.values() if v > 1)
    uniq = "IBAN unique per (bank, iban, currency, endpoint) across the whole dataset"
    ok[uniq], n[uniq] = len(ibans) - dup, len(ibans)

    lines = [f"# Quality report: {root.name}", "",
             f"{m['customers']:,} customers, {len(shards)} shards.", "",
             "## 1. API tables: fidelity and standards (must be 100%)", "",
             "| check | passed | total | result |", "|---|---:|---:|---|"]
    all_ok = True
    for name in sorted(n):
        good = ok[name] == n[name]
        all_ok &= good
        e = "" if good else f" e.g. {ex.get(name, [''])[:2]}"
        lines.append(f"| {name} | {ok[name]:,} | {n[name]:,} | {'PASS' if good else 'FAIL' + e} |")
    measured = measure(m)
    lines += ["", "## 2. Simulated world vs sourced Georgian targets (truth layer)", "",
              "| target | measured | target | tolerance | status of the source | result |",
              "|---|---:|---:|---|---|---|"]
    for key, t in TARGETS.items():
        v = measured.get(key, float("nan"))
        res, tol = _grade(t, v)
        if t.gate and res == "FAIL":
            all_ok = False
        lines.append(f"| {t.label} | {v:.2f} {t.unit} | {t.target:g} | {tol} | {t.status} | {res} |")
    by_rows = {b: m[f"api_rows.{b}"] for b in BANKS}
    by_val = {b: m[f"ledger_value.{b}"] for b in BANKS}
    lines += ["", "## 3. Where the data comes from, by bank", "",
              "| bank | customers (primary) | API rows (no card mirrors) | share of API rows | "
              "share of ledger value |", "|---|---:|---:|---:|---:|"]
    tr, tv = sum(by_rows.values()), sum(by_val.values())
    for b in BANKS:
        lines.append(f"| {b} | {m[f'primary.{b}']:,} | {by_rows[b]:,} | {_pct(by_rows[b], tr):.1f}% | "
                     f"{_pct(by_val[b], tv):.1f}% |")
    lines += ["", f"**Overall: {'PASS' if all_ok else 'FAIL'}**"]
    text = "\n".join(lines) + "\n"
    if write:
        (root / "QUALITY_REPORT.md").write_text(text)
        (root / "quality.json").write_text(json.dumps(
            {"pass": all_ok, "checks": {k: [ok[k], n[k]] for k in n}, "measured": measured}, indent=1))
    return text, all_ok


__all__ = ["run_report", "analyse_shard", "measure"]
