"""The sharded pipeline: determinism across shard layouts, API-only tables, standards on the data."""
from __future__ import annotations

import csv
import json
import re

import pandas as pd
import pyarrow.dataset as ds
import pytest
from obmock.config import SPEC_DIR, WorldConfig
from obmock.harvest import COLUMNS, lineage
from obmock.pipeline import generate
from obmock.util import is_valid_iban

N = 120
# names that exist only in the simulator and must never reach the API tables
TRUTH_ONLY = {"mcc", "merchant", "city", "kind", "channel", "stress", "pd_pct", "score", "balance_after",
              "income_month", "birth_date", "sex", "region_code", "narrative_ka", "narrative_en",
              "overdue_principal", "days_in_delinquency", "instalment", "person_id"}
ISO_4217 = {"GEL", "USD", "EUR", "GBP", "XXX"}


def _load(root, area, table) -> pd.DataFrame:
    return ds.dataset(root / area / table, format="parquet").to_table().to_pandas()


def _canon(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    return df.sort_values(keys, kind="stable").reset_index(drop=True)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("ds")
    generate(WorldConfig(n_customers=N), out, workers=1, shard_size=40, api_sample=3,
             api_sample_other=1, verbose=False)
    return out


def test_result_is_independent_of_workers_and_shard_layout(dataset, tmp_path):
    other = tmp_path / "other"
    generate(WorldConfig(n_customers=N), other, workers=3, shard_size=50, api_sample=0,
             api_sample_other=0, verbose=False)
    for table, keys in (("transactions", ["bank", "psu_id", "source_endpoint", "transaction_id"]),
                        ("accounts", ["bank", "psu_id", "source_endpoint", "resource_id"]),
                        ("balances", ["bank", "psu_id", "source_endpoint", "resource_id",
                                      "origin", "balance_type"])):
        a = _canon(_load(dataset, "api", table), keys)
        b = _canon(_load(other, "api", table), keys)
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    a = _canon(_load(dataset, "truth", "ledger"), ["account_id", "entry_reference"])
    b = _canon(_load(other, "truth", "ledger"), ["account_id", "entry_reference"])
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_api_tables_hold_only_lineage_columns_and_no_truth(dataset):
    lin = lineage()
    for table, cols in COLUMNS.items():
        df = _load(dataset, "api", table)
        assert list(df.columns) == [c.name for c in cols] == list(lin[table])
        assert not TRUTH_ONLY & set(df.columns), table
    truth = _load(dataset, "truth", "customers")
    assert {"stress", "pd_pct", "person_id", "birth_date"} <= set(truth.columns)
    assert len(truth) == N
    manifest = json.loads((dataset / "MANIFEST.json").read_text())
    assert manifest["rows"]["truth.customers"] == N


def test_api_values_follow_banking_standards(dataset):
    acc = _load(dataset, "api", "accounts")
    ibans = acc.dropna(subset=["iban"])
    assert ibans["iban"].map(is_valid_iban).all()
    assert ibans["iban"].str.fullmatch(r"GE\d{2}[A-Z]{2}\d{16}").all()  # ISO 13616, Georgia: 22 chars
    assert acc["bic"].dropna().str.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?").all()  # ISO 9362
    assert set(acc["currency"].dropna()) <= ISO_4217
    # one IBAN may carry several currency sub-accounts, but (bank, iban, currency) is unique
    assert not ibans.duplicated(["bank", "iban", "currency", "source_endpoint"]).any()

    tx = _load(dataset, "api", "transactions")
    assert set(tx["currency"].dropna()) <= ISO_4217
    # entry_key falls back to entryReference where a bank has no transactionId (ProCredit)
    assert not tx.duplicated(["bank", "source_endpoint", "entry_key"]).any()
    pend = tx[tx["status"] == "pending"]
    assert len(pend) > 0 and pend["booking_date"].isna().all()  # a pending entry has no booking date
    assert tx[tx["status"] == "booked"]["booking_date"].notna().all()
    with (SPEC_DIR / "iso20022" / "bank_transaction_codes.csv").open(encoding="utf-8") as fh:
        official = {f"{r['domain']}-{r['family']}-{r['subfamily']}" for r in csv.DictReader(fh)}
    used = set(tx["bank_transaction_code"].dropna())
    assert used and used <= official, sorted(used - official)
    fx = tx.dropna(subset=["fx_rate"])
    assert len(fx) > 0
    foreign = fx["fx_source_currency"].where(fx["fx_source_currency"] != "GEL", fx["fx_target_currency"])
    assert (fx["fx_unit_currency"] == foreign).all()  # the rate is quoted per unit of foreign currency


def test_balance_after_transaction_is_the_running_balance(dataset):
    """balanceAfterTransaction (an API field) moves by exactly each booked amount."""
    tx = _load(dataset, "api", "transactions")
    tx = tx[(tx["source_endpoint"] == "accounts") & (tx["status"] == "booked")
            & tx["balance_after_amount"].notna()].copy()
    tx["amt"], tx["bal"] = tx["amount"].astype(float), tx["balance_after_amount"].astype(float)
    tx = tx.sort_values(["bank", "psu_id", "account_resource_id", "entry_reference"])
    step = tx.groupby(["bank", "psu_id", "account_resource_id"])["bal"].diff()
    resid = (step - tx["amt"]).dropna().abs()
    assert len(resid) > 1000 and (resid < 5e-4).all(), resid.max()


def test_quality_report_api_section_passes(dataset):
    """Every fidelity/standards check of `obmock report` passes on a freshly generated dataset.

    (The calibration section needs thousands of customers to hit its tolerances, so only the
    API-layer section is asserted here.)
    """
    from obmock.report import run_report

    text, _ = run_report(dataset, workers=1, write=False)
    api_section = text.split("## 2.")[0]
    assert "| FAIL" not in api_section and "PASS" in api_section, api_section


def test_evidence_only_profile_drops_exactly_the_four_standard_coded_fields(dataset, tmp_path):
    from obmock.report import run_report

    out = tmp_path / "evidence"
    generate(WorldConfig(n_customers=80, profile="evidence-only"), out, workers=1, shard_size=40,
             api_sample=0, api_sample_other=0, verbose=False)
    ev = _load(out, "api", "transactions")
    rich = _load(dataset, "api", "transactions")
    dropped = ["bank_transaction_code", "purpose_code", "mandate_id", "balance_after_amount"]
    assert len(ev) > 1000 and ev[dropped].isna().all().all()
    assert rich[dropped].notna().any().all()  # the default profile fills each of them somewhere
    kept = [c for c in ev.columns if c not in dropped]
    assert ev[kept].notna().any().sum() > 20  # everything else is still populated
    text, _ = run_report(out, workers=1, write=False)
    assert "| FAIL" not in text.split("## 2.")[0]


def test_entry_numbers_do_not_reveal_other_banks(dataset):
    """A bank's entry numbering must not depend on how many other banks the customer uses."""
    tx = _load(dataset, "api", "transactions")
    tx = tx[(tx["source_endpoint"] == "accounts") & tx["entry_reference"].notna()]
    first = tx.assign(n=tx["entry_reference"].astype("int64"))
    first["block"] = (first["n"] % 400_000) // 20_000
    # the first current account of every (customer, bank) sits in block 0, wherever the bank ranks
    lowest = first.groupby(["psu_id", "bank"])["block"].min()
    assert (lowest == 0).all()


def test_raw_captures_are_kept_for_a_sample(dataset):
    files = sorted((dataset / "captures").rglob("*.json"))
    assert files
    cap = json.loads(files[0].read_text())
    assert {"bank", "customer", "calls"} <= set(cap)
    assert re.fullmatch(r"C\d{5}", cap["customer"])
