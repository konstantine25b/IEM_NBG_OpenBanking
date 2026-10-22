"""Tests for synthgen.py (synthetic loan population).

The fast tests fit the generator on a small fake loan table that has the real 58 columns and dtypes (planted
delinquency regimes, an equality atom between term and instalment count, zero inflation, caps), so nothing here
reads nbg_synthetic_data/.  Two tests need more: the one that feeds the output to openbanking_mock builds a tiny world, and the
one marked `slow` fits on the real nbg_synthetic_data/ (skipped when it is absent):  uv run pytest -m slow
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGET = "addNegStatus"


def _load():
    spec = importlib.util.spec_from_file_location("synthgen", ROOT / "synthetic" / "synthgen.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["synthgen"] = module  # dataclasses need the module to be registered while it executes
    spec.loader.exec_module(module)
    return module


S = _load()

_COLUMN_NAMES = (
    "CLIENT_GEOGRAPHICAL_REGION CLIENT_LEGAL_FORM SEX Age INCOME_AMOUNT_TOTAL INCOME_AMOUNT_SOI04 "
    "DEBT_DISBURSED_REGION IS_NEW_FOR_BANK DISBURSED_AMOUNT PRINCIPAL_PAYMENT_FREQUENCY INTEREST_RATE_TYPE "
    "INTEREST_RATE_NOMINAL INTEREST_RATE_PAYMENT_FREQUENCY AVERAGE_MONTHLY_PAYMENT PRODUCT_PURPOSE ECONOMIC_SECTORS "
    "ECONOMIC_SECTORS_PURPOSE DEBT_SEGMENT PTI_RATIO LTV_RATIO instalmentType OverduePrincipalAm "
    "OverdueInterestCommission overdueInstalmentsCount overdueAmount MaximumNumberDelinquencyDays "
    "TotalNumberDelinquencyDays TotalNumberDelinquencyDaysLastYear TotalNumberOverdueInst OutstandingPrincipalAm "
    "outstandingAmount standardInstalmentCount standardInstalmentAmount RefinancingStatusBool "
    "RestructuringStatusBool LoanSegment RemainingTerm GeoCitizen last1Month last3Month last6Month last12Month "
    "ExistingOperationsDebtor TerminatedOperationsDebtor MaximumAmountOfInstalmentBySchedule probabilityOfDefault "
    "guaranteeAmount ContractTermMonth score daysInDeliquency ShortDaysInDeliquency loanType penaltySum penaltyPaid "
    "TotalOutstandingAmount TotalUnpaidInstalments TotalDebtOverdue addNegStatus"
)
COLUMNS = _COLUMN_NAMES.split()
_INT_NAMES = (
    "overdueInstalmentsCount MaximumNumberDelinquencyDays TotalNumberDelinquencyDays TotalNumberOverdueInst "
    "TotalNumberDelinquencyDaysLastYear standardInstalmentCount RemainingTerm last1Month last3Month last6Month "
    "last12Month ExistingOperationsDebtor TerminatedOperationsDebtor ContractTermMonth score daysInDeliquency "
    "ShortDaysInDeliquency TotalUnpaidInstalments addNegStatus SEX CLIENT_LEGAL_FORM"
)
INT_COLUMNS = _INT_NAMES.split()


def make_loans(n: int = 6000, seed: int = 0) -> pd.DataFrame:
    """A fake loan table with the real schema: three delinquency regimes, mortgages, a term = instalment-count
    atom for monthly loans, days left mostly within the contract, zero-inflated arrears, a capped income."""
    rng = np.random.default_rng(seed)
    regime = rng.choice(3, n, p=[0.6, 0.25, 0.15])
    stress = rng.normal(size=n) + 1.2 * (regime == 2)
    y = (rng.random(n) < np.select([regime == 0, regime == 1], [0.002, 0.01], 0.25)).astype(int)
    purpose = rng.choice([2, 4, 7, 10, 13], n, p=[0.6, 0.2, 0.12, 0.03, 0.05])
    mortgage = np.isin(purpose, [7, 10, 13])
    monthly = rng.random(n) < 0.85
    term = np.where(mortgage, rng.choice([120, 180, 240], n), rng.choice([12, 24, 36, 48, 60], n)).astype(int)
    atom = rng.random(n) < 0.45
    count = np.where(atom, term + rng.choice([-1, 0, 0, 1], n), rng.choice([12, 24, 36, 48, 60, 120, 180], n))
    left = np.floor(rng.random(n) * 31 * (term + 1) * np.where(rng.random(n) < 0.08, 1.5, 1.0)).astype(int)
    amount = np.exp(8.3 + 0.7 * rng.normal(size=n) + 1.3 * mortgage)
    history, current = regime >= 1, regime == 2
    days = np.where(history, rng.integers(1, 90, n) * (1 + 3 * current), 0)
    over_count = np.where(current, rng.integers(0, 10, n), 0)
    dust = rng.random(n)
    sector = np.where(rng.random(n) < 0.04, 0.0, rng.integers(1, 27, n)).astype(float)
    last1 = rng.poisson(1.0, n)
    last3 = last1 + rng.poisson(1.0, n)
    last6 = last3 + rng.poisson(1.5, n)
    overdue_p = np.where(current, np.exp(5 + rng.normal(size=n)), np.where(dust < 0.7, dust, 0.0))
    overdue_i = np.where(current, np.exp(4 + rng.normal(size=n)), np.where(dust < 0.5, dust, 0.0))
    outstanding = np.where(rng.random(n) < 0.07, 0.0, amount * rng.uniform(0.1, 1.0, n))
    closed = rng.random(n) < 0.03 + 0.12 * current  # run-off loans: the balance / schedule columns are 0 together
    y = np.where(closed, np.maximum(y, rng.random(n) < 0.5), y).astype(int)
    outstanding = np.where(closed, 0.0, outstanding)

    d = {
        "CLIENT_GEOGRAPHICAL_REGION": rng.choice(np.arange(1.0, 13.0), n, p=np.r_[0.4, np.full(11, 0.6 / 11)]),
        "CLIENT_LEGAL_FORM": rng.choice([1, 7], n, p=[0.06, 0.94]),
        "SEX": rng.choice([1, 2], n),
        "Age": np.clip(np.floor(38 + 11 * rng.normal(size=n)), 20, 79),
        "INCOME_AMOUNT_TOTAL": np.clip(np.exp(7.4 + 0.6 * rng.normal(size=n)), 300, 10_000),
        "INCOME_AMOUNT_SOI04": np.where(rng.random(n) < 0.45, 0.0, np.round(np.exp(6.5 + rng.normal(size=n)), 4)),
        "DEBT_DISBURSED_REGION": rng.choice(np.arange(1.0, 400.0) * 7, n),
        "IS_NEW_FOR_BANK": rng.random(n) < 0.12,
        "DISBURSED_AMOUNT": np.where(rng.random(n) < 0.03, 0.0, amount),
        "PRINCIPAL_PAYMENT_FREQUENCY": np.where(monthly, 2.0, rng.choice([1.0, 3.0, 6.0, 7.0], n)),
        "INTEREST_RATE_TYPE": rng.choice([1.0, 2.0, 3.0], n, p=[0.83, 0.05, 0.12]),
        "INTEREST_RATE_NOMINAL": np.clip(0.19 - 0.06 * mortgage + 0.03 * stress, 0.0, 0.4),
        "INTEREST_RATE_PAYMENT_FREQUENCY": np.where(monthly, 2.0, rng.choice([1.0, 3.0, 6.0, 7.0], n)),
        "AVERAGE_MONTHLY_PAYMENT": np.round(amount / np.maximum(term, 1) * 1.2, 4),
        "PRODUCT_PURPOSE": purpose.astype(float),
        "ECONOMIC_SECTORS": sector,
        "ECONOMIC_SECTORS_PURPOSE": np.where(rng.random(n) < 0.6, sector, 0.0),
        "DEBT_SEGMENT": rng.choice([1.0, 2.0, 3.0, 4.0], n, p=[0.01, 0.03, 0.15, 0.81]),
        "PTI_RATIO": np.where(rng.random(n) < 0.05, 0.0, np.exp(rng.normal(-0.7, 1.0, n))),
        "LTV_RATIO": np.where(mortgage, rng.uniform(1, 40, n), 0.0),
        "instalmentType": rng.choice(["Fixed", "Variable"], n, p=[0.95, 0.05]),
        "OverduePrincipalAm": overdue_p,
        "OverdueInterestCommission": overdue_i,
        "overdueInstalmentsCount": over_count,
        "overdueAmount": np.round(overdue_p + overdue_i, 4),
        "MaximumNumberDelinquencyDays": days,
        "TotalNumberDelinquencyDays": days + np.where(history, rng.integers(0, 30, n), 0),
        "TotalNumberDelinquencyDaysLastYear": np.where(history, rng.integers(0, 60, n), 0),
        "TotalNumberOverdueInst": np.where(history, over_count + rng.integers(1, 6, n), 0),
        "OutstandingPrincipalAm": outstanding,
        "outstandingAmount": outstanding * 1.02,
        "standardInstalmentCount": np.maximum(count, 0),
        "standardInstalmentAmount": np.round(amount / np.maximum(term, 1), 4),
        "RefinancingStatusBool": rng.random(n) < 0.1,
        "RestructuringStatusBool": rng.random(n) < 0.02,
        "LoanSegment": rng.choice(["Retail", "Business"], n, p=[0.98, 0.02]),
        "RemainingTerm": left,
        "GeoCitizen": rng.choice(["Yes", "No"], n, p=[0.97, 0.03]),
        "last1Month": last1,
        "last3Month": last3,
        "last6Month": last6,
        "last12Month": last6 + rng.poisson(2.0, n),
        "ExistingOperationsDebtor": 1 + rng.poisson(2.0, n),
        "TerminatedOperationsDebtor": rng.poisson(5.0, n),
        "MaximumAmountOfInstalmentBySchedule": np.round(amount / np.maximum(term, 1) * 1.3, 4),
        "probabilityOfDefault": np.round(np.clip(2.0 * np.exp(0.9 * stress), 0.24, 98.0), 2),
        "guaranteeAmount": np.where(mortgage, amount * 1.4, np.where(rng.random(n) < 0.3, amount * 0.5, 0.0)),
        "ContractTermMonth": term,
        "score": np.clip(np.round(250 - 40 * stress + 10 * rng.normal(size=n)), 94, 374).astype(int),
        "daysInDeliquency": np.where(current & (rng.random(n) < 0.8), np.maximum(days, 30), 0),
        "ShortDaysInDeliquency": np.where(history, rng.integers(0, 12, n), 0),
        "loanType": np.where(mortgage & (rng.random(n) < 0.95), "MortgageLoan", "ConsumerLoan"),
        "penaltySum": np.where(history, np.exp(1 + rng.normal(size=n)), 0.0),
        "penaltyPaid": np.where(history, np.exp(0.5 + rng.normal(size=n)), 0.0),
        "TotalOutstandingAmount": outstanding * 1.02 * rng.uniform(1.0, 1.6, n),
        "TotalUnpaidInstalments": np.where(current, rng.integers(0, 12, n), 0),
        "TotalDebtOverdue": np.where(current, np.exp(5.5 + rng.normal(size=n)), np.where(dust < 0.4, dust, 0.0)),
        TARGET: y,
    }
    df = pd.DataFrame({c: d[c] for c in COLUMNS})
    for c in ("standardInstalmentAmount", "MaximumAmountOfInstalmentBySchedule", "AVERAGE_MONTHLY_PAYMENT"):
        df.loc[closed, c] = 0.0
    df.loc[closed & (rng.random(n) < 0.6), "RemainingTerm"] = 0
    return df.astype({c: "int64" for c in INT_COLUMNS})


@pytest.fixture(scope="module")
def real() -> pd.DataFrame:
    return make_loans()


@pytest.fixture(scope="module")
def model(real) -> object:
    with pytest.MonkeyPatch.context() as mp:  # small data: smaller cells and a lighter moment matching
        mp.setattr(S, "MIN_CELL", 100)
        mp.setattr(S, "MSM_ROWS", 4000)
        return S.Synthesizer(real, block_counts=[2000, 2000, 2000])


@pytest.fixture(scope="module")
def syn(model) -> pd.DataFrame:
    return model.sample(4000, seed=3)


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# the generator
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_fake_table_has_the_real_schema(real):
    assert list(real.columns) == COLUMNS and len(COLUMNS) == 58
    assert real["PRODUCT_PURPOSE"].dtype == "float64" and real["SEX"].dtype == "int64"


def test_schema_dtypes_and_event_count_are_exact(real, syn):
    assert list(syn.columns) == list(real.columns)
    assert (syn.dtypes == real.dtypes).all()
    assert not syn.isna().any().any()
    assert len(syn) == 4000
    assert int(syn[TARGET].sum()) == round(4000 * real[TARGET].mean())


def test_regimes_become_cells_and_a_small_class_shares_its_regime_cell(model):
    keys = set(model.cells)
    assert {k[0] for k in keys} == {0, 1, 2}
    assert any(k[1] is None for k in keys if k[0] == 0)  # almost no events without overdue history: one cell
    assert any(k[1] == 1 for k in keys if k[0] == 2)  # events live in the currently-overdue regime
    assert sum(c.share for c in model.combos) == pytest.approx(1.0)


def test_same_seed_same_rows_other_seed_other_rows(model):
    a, b, c = model.sample(1500, seed=11), model.sample(1500, seed=11), model.sample(1500, seed=12)
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(c)


def test_values_stay_in_the_real_support(real, syn):
    assert S.schema_problems(real, syn) == []
    assert (syn["Age"] == syn["Age"].round()).all()
    assert (np.round(syn["probabilityOfDefault"], 2) == syn["probabilityOfDefault"]).all()  # two decimals, as real
    assert set(syn["PRODUCT_PURPOSE"].unique()) <= {2.0, 4.0, 7.0, 10.0, 13.0}
    assert syn["INCOME_AMOUNT_TOTAL"].between(300, 10_000).all()


def test_spikes_caps_and_zero_inflation_survive(real, syn):
    for c in ("INCOME_AMOUNT_SOI04", "TotalNumberDelinquencyDays", "guaranteeAmount", "DISBURSED_AMOUNT"):
        assert abs((real[c] == 0).mean() - (syn[c] == 0).mean()) < 0.03, c
    assert abs((real["INCOME_AMOUNT_TOTAL"] == 10_000).mean() - (syn["INCOME_AMOUNT_TOTAL"] == 10_000).mean()) < 0.01


def test_a_loan_without_overdue_history_has_no_arrears(syn):
    clean = syn[syn["TotalNumberDelinquencyDays"] == 0]
    assert len(clean) > 1000
    assert (clean["daysInDeliquency"] == 0).mean() > 0.99 and (clean["overdueInstalmentsCount"] == 0).mean() > 0.99


def test_codes_travel_together_like_in_the_real_rows(real, syn):
    """A code tuple is borrowed from one real row, so joint patterns (mortgage <-> loanType) survive."""
    real_share = (real["loanType"] == "MortgageLoan")[real["PRODUCT_PURPOSE"].isin(S.MORTGAGE_PURPOSES)].mean()
    syn_share = (syn["loanType"] == "MortgageLoan")[syn["PRODUCT_PURPOSE"].isin(S.MORTGAGE_PURPOSES)].mean()
    assert abs(real_share - syn_share) < 0.03


def test_rank_correlation_and_label_signal_are_reproduced(real, syn):
    q = S.quantity_cols(real)
    gap = np.abs(real[q].corr("spearman").to_numpy() - syn[q].corr("spearman").to_numpy())
    assert gap[np.triu_indices(len(q), 1)].mean() < 0.05
    auc = S.auc_gaps(real, syn)
    assert auc.loc["TotalNumberDelinquencyDays", "AUC synthetic"] > 0.7  # the arrears columns still carry the label
    assert auc["gap"].max() < 0.12  # ~160 events: each AUC is only known to about +-0.03


def test_no_synthetic_row_is_a_copy_of_a_real_row(real, syn):
    real_hashes = set(pd.util.hash_pandas_object(real, index=False))
    assert not pd.util.hash_pandas_object(syn, index=False).isin(real_hashes).any()
    assert not syn.duplicated().any()


def test_balance_columns_are_zero_together_like_in_the_real_rows(real, syn):
    """A closed loan has several balance / schedule columns at exactly 0; a copula alone would not make them
    zero together, and it would invent loans with almost all of them zero."""
    cols = [c for c in S.ZERO_TOGETHER if c in real.columns]
    kr, ks = (real[cols] == 0).sum(axis=1), (syn[cols] == 0).sum(axis=1)
    assert abs((kr >= 3).mean() - (ks >= 3).mean()) < 0.02 and (kr >= 3).mean() > 0.04  # the fake data has such loans
    assert ks.max() <= kr.max()  # no loan with more zeros than any real one has
    assert syn.groupby(ks >= 3)[TARGET].mean().loc[True] > 3 * syn.groupby(ks >= 3)[TARGET].mean().loc[False]


def test_the_run_off_profile_compares_event_rates_by_number_of_zeros(real, syn):
    profile = S.runoff_profile(real, syn)
    assert list(profile.columns) == [
        "rows real",
        "rows synthetic",
        "event rate real",
        "event rate synthetic",
        "event gap",
    ]
    assert profile.index.min() == 0 and profile["rows real"].sum() <= 1.0 + 1e-9
    assert profile["event gap"].max() < 0.25  # ~100 loans per row of the table: only a gross break fails


def test_event_rate_can_be_overridden(model):
    s = model.sample(2000, seed=1, event_rate=0.1)
    assert int(s[TARGET].sum()) == 200


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# helpers with known answers
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_rematch_keeps_the_multiset_and_orders_values_by_key():
    values = np.array([5, 1, 9, 3, 7])
    key = np.array([10, 40, 20, 50, 30])
    S.rematch(values, key, np.arange(5), np.zeros(5))
    assert sorted(values) == [1, 3, 5, 7, 9]
    assert list(values[np.argsort(key)]) == [1, 3, 5, 7, 9]  # smallest value on the smallest key


def test_restore_atom_reaches_the_rate_without_changing_the_values():
    rng = np.random.default_rng(0)
    term = rng.choice([12, 24, 36, 48, 60], 5000)
    count = rng.permutation(term)  # same multiset, unrelated pairing: about 20% within +-1
    monthly = np.ones(5000, dtype=bool)
    before = (np.abs(count - term) <= 1).mean()
    out = S.restore_atom(term, count, monthly, 0.5, rng.random(5000), rng)
    assert before < 0.3 and abs((np.abs(out - term) <= 1).mean() - 0.5) < 0.03
    assert sorted(out) == sorted(count)


def test_restore_order_reaches_the_rate_without_changing_the_values():
    rng = np.random.default_rng(1)
    term = rng.choice([12, 24, 36, 60], 5000)
    # every value fits under some arrangement (each is below its own row's bound) but a random pairing breaks it
    left = rng.permutation(np.floor(rng.random(5000) * 0.9 * 31 * (term + 1)).astype(int))
    before = (left > 31 * (term + 1)).mean()
    out = S.restore_order(left, 31 * (term + 1), 0.08, rng.random(5000), rng)
    assert before > 0.15 and abs((out > 31 * (term + 1)).mean() - 0.08) < 0.03
    assert sorted(out) == sorted(left)
    assert np.array_equal(out == 0, left == 0)  # a zero belongs to a closed loan: it never moves


def test_split_events_sums_exactly_and_respects_the_batch_sizes():
    rng = np.random.default_rng(2)
    sizes = np.array([500] * 19 + [37])
    for total in (0, 1, 340, 9537):
        c = S.split_events(sizes, total, 3.6, rng)
        assert c.sum() == total and (c >= 0).all() and (c <= sizes).all()


def test_violation_rates_use_the_eda_definitions():
    d = pd.DataFrame(
        {
            "last1Month": [1, 5, 2, 0], "last3Month": [2, 3, 2, 0],  # 1 of 4 breaks
            "penaltyPaid": [10.5, 3.0, 0.0, 2.5], "penaltySum": [10.0, 3.0, 5.0, 0.0],  # +0.5 is within 1 unit
            "PRINCIPAL_PAYMENT_FREQUENCY": [2.0, 2.0, 2.0, 6.0],
            "ContractTermMonth": [12, 12, 36, 12], "standardInstalmentCount": [12, 13, 20, 99],
            "RemainingTerm": [100, 31 * 13 + 1, 5, 10],
        }
    )  # fmt: skip
    r = S.violation_rates(d)
    assert r["last1Month <= last3Month"] == 0.25
    assert r["penaltyPaid <= penaltySum"] == 0.25  # only 2.5 > 0.0 + 1
    assert r["monthly loans: ContractTermMonth = standardInstalmentCount (+-1)"] == pytest.approx(1 / 3)
    assert r["RemainingTerm (days) <= whole contract"] == 0.25


def test_batch_balance_statistic_is_near_one_for_random_order_and_low_for_stratified_blocks():
    n = 10_000
    values = np.sort(np.random.default_rng(4).normal(size=n))
    shuffled = pd.DataFrame({"a": np.random.default_rng(5).permutation(values)})
    assert S.block_variance_ratio(shuffled, ["a"], [n]) == pytest.approx(1.0, abs=0.5)
    stratified = pd.DataFrame({"a": values.reshape(500, 20).T.ravel()})  # every 500-block: one value per stratum
    assert S.block_variance_ratio(stratified, ["a"], [n]) < 0.1


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# files, command line and the hand-over to openbanking_mock
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_write_and_read_round_trip_keeps_dtypes(real, syn, tmp_path):
    paths = S.write_loans(syn, tmp_path / "out", 1500, {"generator": "synthgen"})
    assert [p.name for p in paths] == ["df_1.csv", "df_2.csv", "df_3.csv"]
    back, sizes = S.read_loans(tmp_path / "out")
    assert sizes == [1500, 1500, 1000]
    assert (back.dtypes == real.dtypes).all()
    pd.testing.assert_frame_equal(back, syn)
    manifest = json.loads((tmp_path / "out" / S.MANIFEST).read_text())
    assert manifest["rows"] == 4000 and set(manifest["files"]) == {p.name for p in paths}


def test_a_rerun_replaces_only_its_own_files(syn, tmp_path):
    out = tmp_path / "out"
    S.write_loans(syn, out, 2000, {})
    S.write_loans(syn.iloc[:1000], out, 2000, {})  # fewer files this time: the stale df_2.csv must go
    assert sorted(p.name for p in out.glob("df_*.csv")) == ["df_1.csv"]
    (out / "notes.txt").write_text("mine")
    with pytest.raises(SystemExit):
        S.write_loans(syn, out, 2000, {})
    assert (out / "notes.txt").read_text() == "mine"


def test_cli_generate_then_check_on_a_small_table(real, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MIN_CELL", 100)
    monkeypatch.setattr(S, "MSM_ROWS", 4000)
    data = tmp_path / "Data"
    data.mkdir()
    for i in range(3):
        real.iloc[i * 2000 : (i + 1) * 2000].to_csv(data / f"df_{i + 1}.csv", index=False)
    out = tmp_path / "synthetic"
    args = ["--data-dir", str(data), "--out", str(out), "--rows", "3000", "--rows-per-file", "1000"]
    assert S.main(["generate", *args]) in (0, 1)  # the gates are tuned for the real data: files and report must exist
    assert len(list(out.glob("df_*.csv"))) == 3 and (out / S.REPORT).exists()
    assert S.main(["check", "--data-dir", str(data), "--synthetic", str(out)]) in (0, 1)
    again = tmp_path / "again"
    S.main([*args[:2], "--out", str(again), *args[4:], "--no-check"])  # no subcommand = generate
    assert json.loads((out / S.MANIFEST).read_text())["files"] == json.loads((again / S.MANIFEST).read_text())["files"]


def test_bank_never_writes_inside_openbanking_mock(tmp_path):
    inside = str(S.OBMOCK_DIR / "output" / "x")
    args = S.build_parser().parse_args(["bank", "--synthetic", str(tmp_path), "--out", inside])
    with pytest.raises(SystemExit, match="outside openbanking_mock"):
        S.cmd_bank(args)


def test_bank_refuses_more_customers_than_loans_and_a_folder_it_does_not_own(syn, tmp_path):
    S.write_loans(syn.iloc[:300], tmp_path / "loans", 300, {})
    base = ["bank", "--synthetic", str(tmp_path / "loans"), "--out", str(tmp_path / "b")]
    with pytest.raises(SystemExit, match="exceeds"):
        S.cmd_bank(S.build_parser().parse_args([*base, "--customers", "301"]))
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "mine.txt").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        S.cmd_bank(S.build_parser().parse_args([*base, "--customers", "10"]))
    assert (tmp_path / "b" / "mine.txt").exists()


def test_openbanking_mock_builds_a_world_from_the_synthetic_loans(syn, tmp_path):
    """The hand-over contract: obmock's own loader and simulator accept the files written here, unchanged."""
    S.write_loans(syn, tmp_path / "loans", 2000, {})
    sys.path.insert(0, str(S.OBMOCK_DIR))
    keep = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # never leave a __pycache__ inside openbanking_mock/
    try:
        from obmock.config import WorldConfig
        from obmock.world import build_world

        world = build_world(WorldConfig(n_customers=36, data_dir=tmp_path / "loans", min_customers_per_bank=1))
    finally:
        sys.dont_write_bytecode = keep
        sys.path.remove(str(S.OBMOCK_DIR))
    assert len(world.customers) == 36 and len(world.accounts) >= 36
    truth = next(iter(world.customers.values())).truth
    assert truth["df_addNegStatus"] in (0, 1) and "df_ContractTermMonth" in truth


@pytest.mark.slow
@pytest.mark.skipif(not (ROOT / "nbg_synthetic_data" / "df_1.csv").exists(), reason="nbg_synthetic_data/df_1.csv is not here")
def test_real_data_passes_every_gate_including_privacy_and_utility(tmp_path):
    out = tmp_path / "loans"
    code = S.main(["generate", "--out", str(out), "--rows", "30000", "--rows-per-file", "10000", "--full-check"])
    assert code == 0, (out / S.REPORT).read_text()
