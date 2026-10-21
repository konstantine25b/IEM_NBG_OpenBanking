"""Tests for EDA.py.

Fast tests check the statistical helpers against cases with known answers and run the whole
script on a small synthetic dataset that has the real schema (so nothing here reads ./Data).
The one test that also trains models is marked `slow`:  uv run pytest -m slow
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


def _load_eda():
    spec = importlib.util.spec_from_file_location("EDA", ROOT / "EDA.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["EDA"] = module  # dataclasses need the module to be registered while it executes
    spec.loader.exec_module(module)
    return module


EDA = _load_eda()


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# a synthetic dataset with the real column names / dtypes, planted so the target depends on a few columns
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def make_files(folder: Path, n_files: int = 3, rows: int = 1500, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    for i in range(1, n_files + 1):
        d = {}
        latent = rng.normal(size=rows)  # drives the event, delinquency columns and balances
        for col, (family, kind, _) in EDA.FEATURES.items():
            if col == EDA.DEFAULT_TARGET:
                continue
            if col in ("GeoCitizen",):
                d[col] = rng.choice(["Yes", "No"], rows, p=[0.97, 0.03])
            elif col == "LoanSegment":
                d[col] = rng.choice(["Retail", "Business"], rows, p=[0.95, 0.05])
            elif col == "instalmentType":
                d[col] = rng.choice(["Fixed", "Variable"], rows, p=[0.95, 0.05])
            elif col == "loanType":
                d[col] = rng.choice(["ConsumerLoan", "MortgageLoan"], rows, p=[0.8, 0.2])
            elif col in ("IS_NEW_FOR_BANK", "RefinancingStatusBool", "RestructuringStatusBool"):
                d[col] = rng.random(rows) < 0.1
            elif kind == "binary":
                d[col] = rng.integers(1, 3, rows)
            elif kind == "nominal":
                d[col] = rng.integers(1, 6, rows).astype(float)
            elif family == "delinquency":
                d[col] = np.where(
                    latent > 1.2,
                    rng.integers(100, 1500, rows),
                    np.where(rng.random(rows) < 0.2, rng.integers(0, 30, rows), 0),
                ).astype(float)
            elif kind in ("amount", "days"):
                d[col] = np.exp(rng.normal(6, 1, rows)) * (1 - 0.3 * (latent > 1.2))
            elif kind in ("rate", "ratio", "score"):
                d[col] = rng.random(rows) * 100 if col in ("score", "probabilityOfDefault") else rng.random(rows)
            else:
                d[col] = rng.poisson(8, rows)
        d[EDA.DEFAULT_TARGET] = ((latent > 1.7) | (rng.random(rows) < 0.005)).astype(int)
        pd.DataFrame(d)[list(EDA.FEATURES)].to_csv(folder / f"df_{i}.csv", index=False)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("Data")
    make_files(folder)
    return folder


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# statistical helpers with known answers
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_wilson_interval_contains_the_estimate_and_is_valid_at_zero():
    lo, hi = EDA.wilson(np.array([0, 5, 50]), np.array([100, 100, 100]))
    assert (lo >= 0).all()
    assert (hi <= 1).all()
    assert lo[0] == 0.0
    assert hi[0] == pytest.approx(0.037, abs=0.002)  # classic 0 / 100 Wilson upper bound
    assert (lo <= np.array([0, 0.05, 0.5])).all()
    assert (hi >= np.array([0, 0.05, 0.5])).all()


def test_drift_matrix_is_quiet_for_identical_files_and_loud_for_a_shifted_one():
    rng = np.random.default_rng(1)
    x = rng.normal(size=6000)
    groups = np.repeat([0, 1, 2], 2000)
    same = pd.DataFrame({"x": x})
    shifted = pd.DataFrame({"x": np.where(groups == 2, x + 2.0, x)})  # file 2 is shifted by two standard deviations
    assert EDA.drift_matrix(same, ["x"], {"x": False}, groups, 3).to_numpy().max() < 0.05
    assert EDA.drift_matrix(shifted, ["x"], {"x": False}, groups, 3).loc["x", 2] > 0.25


def test_woe_iv_separates_informative_from_random():
    rng = np.random.default_rng(2)
    y = (rng.random(8000) < 0.1).astype(int)
    informative = pd.Series(rng.normal(size=8000) + 2.5 * y)
    noise = pd.Series(rng.normal(size=8000))
    assert EDA.woe_iv(informative, y)[1] > 0.5
    assert EDA.woe_iv(noise, y)[1] < 0.05


def test_oof_signal_auc_reads_nominal_codes_as_categories_not_numbers():
    rng = np.random.default_rng(3)
    code = pd.Series(rng.integers(0, 8, 6000))
    y = (rng.random(6000) < np.where(code.isin([2, 6]), 0.4, 0.03)).astype(int)  # risk lives in two non-adjacent levels
    assert EDA.safe_auc(y, code.to_numpy()) < 0.62  # as an ordered number the code looks useless ...
    assert EDA.oof_signal_auc(code, y) > 0.7  # ... but per level it is clearly informative


def test_runs_test_detects_alternating_order_and_accepts_random_order():
    assert EDA.runs_z(np.tile([0, 1], 500)) > 10
    rng = np.random.default_rng(4)
    assert abs(EDA.runs_z(rng.integers(0, 2, 2000))) < 3.5


def test_hanley_mcneil_standard_error_shrinks_with_more_events():
    assert EDA.auc_se(0.8, 4000, 96000) < EDA.auc_se(0.8, 400, 9600)


def test_copula_null_matches_independence_and_perfect_dependence():
    rng = np.random.default_rng(5)
    a, b = rng.normal(size=20000), rng.normal(size=20000)
    assert EDA.copula_violation(a, b) == pytest.approx(0.5, abs=0.05)  # unrelated columns violate a <= b half the time
    assert (
        EDA.copula_violation(a, a + 3.0) < 0.02
    )  # a column that sits clearly above the other (and moves with it) essentially never violates


def test_best_single_cut_finds_the_threshold_of_a_planted_rule():
    rng = np.random.default_rng(10)
    x = rng.integers(0, 3000, 20000).astype(float)
    y = ((x > 1700) & (rng.random(20000) < 0.9)).astype(int)
    df = pd.DataFrame({"days": x, "noise": rng.normal(size=20000)})
    cut = EDA.best_single_cut(df, ["days", "noise"], y)
    assert cut["column"] == "days"
    assert cut["sign"] == ">"
    assert abs(cut["threshold"] - 1700) <= 25
    assert cut["precision"] == pytest.approx(0.9, abs=0.03)
    assert cut["recall"] > 0.9


def test_positive_level_is_the_rarer_level_and_true_for_booleans():
    assert (
        EDA.positive_level(pd.Series([1] * 90 + [2] * 10)) == 2
    )  # the rarer code, not the code that happens to be called 1
    assert EDA.positive_level(pd.Series(["Yes"] * 97 + ["No"] * 3)) == "No"
    assert EDA.positive_level(pd.Series([False] * 70 + [True] * 30)) is True
    assert EDA.to_binary_target(pd.Series([0] * 95 + [1] * 5)).sum() == 5


def test_missing_values_become_their_own_bin_instead_of_crashing():
    rng = np.random.default_rng(11)
    x = pd.Series(rng.normal(size=5000))
    y = (rng.random(5000) < 0.1).astype(int)
    x[:50] = np.nan
    assert 0.4 < EDA.oof_signal_auc(x, y) < 0.65
    table, iv = EDA.woe_iv(x, y)
    assert "missing" in table.index
    assert np.isfinite(iv)


def test_label_and_number_formatting():
    assert EDA.label_of("PRINCIPAL_PAYMENT_FREQUENCY", 2.0) == "2 monthly"
    assert EDA.label_of("SEX", 1.0) == "1"
    assert EDA.label_of("IS_NEW_FOR_BANK", True) == "yes"
    assert EDA.num(10000.0) == "10,000"
    assert EDA.pct(0.0356, 1) == "3.6%"
    assert "<b>x</b>" in EDA.md("**x**") and "<li>a</li>" in EDA.md("- a\n- b")


def test_batch_balance_flags_evenly_mixed_blocks_but_not_random_order():
    rng = np.random.default_rng(6)
    n, b = 10000, 500
    random_col = rng.normal(size=n)
    balanced = np.concatenate(
        [rng.permutation(np.linspace(-2, 2, b)) + rng.normal(0, 0.05, b) for _ in range(n // b)]
    )  # every block has the same spread
    df = pd.DataFrame({EDA.FILE_COL: "df_1", "random": random_col, "balanced": balanced})
    out = EDA.batch_balance(df, ["random"], ["df_1"], [b])
    assert out["median variance ratio"].iloc[0] == pytest.approx(out["expected if order is random"].iloc[0], abs=0.6)
    out = EDA.batch_balance(df, ["balanced"], ["df_1"], [b])
    assert out["median variance ratio"].iloc[0] < 0.3


def test_high_event_band_finds_an_interior_pile_of_events():
    rng = np.random.default_rng(7)
    x = pd.Series(rng.normal(size=20000))
    y = (rng.random(20000) < np.where(x.between(-0.1, 0.1), 0.6, 0.01)).astype(
        int
    )  # events live in the MIDDLE, not at an end
    band = EDA.high_event_band(x, y)
    assert x[band].abs().max() < 0.3
    assert y[band].mean() > 0.3


def test_marker_count_is_honest_and_predictive():
    rng = np.random.default_rng(8)
    n = 12000
    code = rng.integers(0, 6, n)
    zero = rng.random(n) < 0.05
    y = ((code == 3) | zero | (rng.random(n) < 0.002)).astype(int)
    df = pd.DataFrame(
        {"code": code, "amount": np.where(zero, 0.0, rng.lognormal(3, 1, n)), "noise": rng.normal(size=n)}
    )
    ctx = type("Ctx", (), {})()
    ctx.df, ctx.y = df, y
    ctx.plot_as = {"code": "categorical", "amount": "numeric", "noise": "numeric"}
    markers = EDA.find_markers(ctx, ["code", "amount", "noise"], np.arange(n))
    assert {c for c, _ in markers} == {"code", "amount"}
    assert EDA.safe_auc(y, EDA.marker_counts(ctx, markers, np.arange(n))) > 0.9


def test_tree_rules_merge_repeated_splits_into_one_range():
    rng = np.random.default_rng(9)
    x = pd.DataFrame({"days": rng.integers(0, 2000, 6000).astype(float), "n": rng.integers(0, 5, 6000).astype(float)})
    y = ((x["days"] > 1200) & (x["days"] < 1700)).astype(int).to_numpy()
    tree = EDA.DecisionTreeClassifier(max_depth=3, random_state=0).fit(x, y)
    rules = EDA.tree_rules(tree, x, y)
    best = rules.iloc[0]
    assert best["precision"] > 0.95
    assert best["rule"].count("days") == 1  # '1,199 < days <= 1,699', not 'days > a AND days <= b AND days > c ...'


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# the whole script, end to end
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def run_eda(data_dir: Path, out: Path, *extra: str) -> int:
    return EDA.main(["--data-dir", str(data_dir), "--out-dir", str(out), "--fast", *extra])


def test_end_to_end_without_models(data_dir, tmp_path):
    assert run_eda(data_dir, tmp_path, "--no-models") == 0
    html = (tmp_path / "eda_report.html").read_text(encoding="utf-8")
    assert "Executive summary" in html
    assert "This section crashed" not in html
    for n in range(1, 11):
        assert f"id='s{n}'" in html, f"section {n} is missing"
    roles = pd.read_csv(tmp_path / "feature_roles.csv", index_col="feature")
    assert len(roles) == len(EDA.FEATURES) - 1  # every feature, not the target
    assert {"role", "recommended_use", "in_A_application", "single_column_AUC"} <= set(roles.columns)
    assert len(list((tmp_path / "figures").glob("*.png"))) >= 10
    assert len(list((tmp_path / "tables").glob("*.csv"))) >= 10


def test_other_binary_target_and_binarize(data_dir, tmp_path):
    assert run_eda(data_dir, tmp_path / "a", "--no-models", "--target", "RestructuringStatusBool") == 0
    assert run_eda(data_dir, tmp_path / "b", "--no-models", "--target", "score", "--binarize", "<=20") == 0
    assert "score&lt;=20" in (tmp_path / "b" / "eda_report.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "args",
    [["--target", "no_such_column"], ["--target", "Age"], ["--target", "score", "--binarize", "banana"]],
)
def test_bad_arguments_fail_with_a_readable_message(data_dir, tmp_path, args):
    with pytest.raises(SystemExit) as exc:
        run_eda(data_dir, tmp_path, "--no-models", *args)
    assert exc.value.code not in (0, None)


def test_bad_arguments_leave_the_previous_output_untouched(data_dir, tmp_path):
    out = tmp_path / "keep"
    assert run_eda(data_dir, out, "--no-models") == 0
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    for args in (
        ["--target", "no_such_column"],
        ["--target", "score", "--binarize", "<=-1"],
        ["--binarize", ">=1"],
        ["--target", "GeoCitizen", "--binarize", ">=1"],
    ):
        with pytest.raises(SystemExit):
            run_eda(data_dir, out, "--no-models", *args)
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert after == before


def test_only_files_written_by_the_script_are_replaced(data_dir, tmp_path):
    out = tmp_path / "mine"
    assert run_eda(data_dir, out, "--no-models") == 0
    (out / "figures" / "99_my_own_chart.png").write_bytes(b"not ours")
    (out / "tables" / "99_my_notes.csv").write_text("a,b\n1,2\n")
    assert run_eda(data_dir, out, "--no-models") == 0
    assert (out / "figures" / "99_my_own_chart.png").read_bytes() == b"not ours"
    assert (out / "tables" / "99_my_notes.csv").exists()
    assert (out / ".eda_manifest.json").exists()


def test_missing_values_and_an_id_column_do_not_crash_any_section(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    make_files(folder, n_files=2, rows=1200)
    frame = pd.read_csv(folder / "df_1.csv")
    frame.loc[::97, "DISBURSED_AMOUNT"] = np.nan  # about 1% of one numeric column
    frame["LOAN_ID"] = [f"L{i:07d}" for i in range(len(frame))]  # a text identifier with one value per row
    frame.to_csv(folder / "df_1.csv", index=False)
    other = pd.read_csv(folder / "df_2.csv")
    other["LOAN_ID"] = [f"M{i:07d}" for i in range(len(other))]
    other.to_csv(folder / "df_2.csv", index=False)
    assert run_eda(folder, tmp_path / "out", "--no-models") == 0
    html = (tmp_path / "out" / "eda_report.html").read_text(encoding="utf-8")
    assert "LOAN_ID" in html and "left out" in html
    assert "This section crashed" not in html


def test_empty_data_dir_is_reported(tmp_path):
    with pytest.raises(SystemExit):
        run_eda(tmp_path, tmp_path / "out", "--no-models")


@pytest.mark.slow
def test_end_to_end_with_models(data_dir, tmp_path):
    assert run_eda(data_dir, tmp_path) == 0
    html = (tmp_path / "eda_report.html").read_text(encoding="utf-8")
    assert "This section crashed" not in html
    assert "The AUC ladder" in html


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# the mock / synthetic bank data (sections 11-14): a tiny dataset with the real folder layout and column names
# ──────────────────────────────────────────────────────────────────────────────────────────────────
SENTINELS = ("SENTINEL_NAME", "GE00SENTINELIBAN", "SENTINEL_PHONE")  # sit in columns the EDA must never read
FORBIDDEN = ("first_en", "last_en", "first_ka", "last_ka", "person_id", "msisdn", "birth_date", "iban", "owner_name")
USD_RATE = 2.7  # the one NBG rate in the fixture's fx_rates table


def make_mock(folder: Path, data_dir: Path, shards: int = 2, per_shard: int = 120) -> dict:
    """A miniature `openbanking_mock/output/dataset`: customers are rows of the test Data/ (as in the real mock), their
    stress follows the real formula, and the transactions use the real Georgian texts / ISO codes.  Every customer gets
    planted flows, and the function returns what the features must reproduce for each of them:
    - salary on the 5th (2 of 3 customers) or on the 20th / 18th (1 of 3: next to the window's start day, moved by a weekend)
    - 3 card purchases a month, every 7th customer also one purchase in USD
    - a 200 GEL loan repayment a month, penalties for stressed customers
    - every 4th customer: a credit card (card-accounts endpoint, purchases without text or MCC) repaid from the account
    - every 10th customer: a loan paid out as a lump sum that leaves again the same week
    - a card mirror and a pending entry per customer (never counted)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    loans = pd.read_csv(data_dir / "df_1.csv").iloc[: shards * per_shard].reset_index(drop=True)
    stress = (
        0.65 * loans["probabilityOfDefault"].rank(pct=True)
        + 0.15 * np.minimum(1, loans["MaximumNumberDelinquencyDays"] / 90)
        + 0.2 * loans[EDA.DEFAULT_TARGET]
    )
    banks = ["tbc", "bog", "liberty", "silk"]
    base = [pd.Timestamp("2025-11-01") + pd.DateOffset(months=k) for k in range(12)]  # Nov 2025 ... Oct 2026
    for sub in ("api/accounts", "api/balances", "api/transactions", "truth/customers", "truth/loans", "truth/fx_rates"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    manifest = {"config": {"as_of": "2026-10-19", "customers": len(loans), "months": 12, "seed": 1}, "generator": "obmock",
                "profile": "test", "synthetic": True, "seconds": 3.0, "customers_by_primary_bank": dict.fromkeys(banks, 1)}  # fmt: skip
    (folder / "MANIFEST.json").write_text(json.dumps(manifest))
    fx = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01"] * 2).date, "currency": ["USD", "EUR"], "gel_per_unit": [USD_RATE, 3.1]}
    )
    pq.write_table(pa.Table.from_pandas(fx, preserve_index=False), folder / "truth/fx_rates/part-00000.parquet")

    def money(values) -> pa.Array:
        return pa.array(np.round(np.asarray(values, float), 3)).cast(pa.decimal128(18, 3))

    expect: dict[str, dict] = {}
    for s in range(shards):
        idx = np.arange(s * per_shard, (s + 1) * per_shard)
        cids = [f"C{i + 1:05d}" for i in idx]
        cust = pd.DataFrame({"cid": cids, "customer_index": idx, "segment": "PRIV",
                             "income_month_minor": (loans.loc[idx, "INCOME_AMOUNT_TOTAL"] * 100).round().astype("int64").to_numpy(),
                             "remittance_month_minor": 0, "sector": 1, "salary_day": [20 if i % 3 == 0 else 5 for i in idx],
                             "banks": [f"{banks[i % 4]}|{banks[(i + 1) % 4]}" if i % 3 == 0 else banks[i % 4] for i in idx],
                             "stress": stress[idx].round(4).to_numpy(), "pd_pct": loans.loc[idx, "probabilityOfDefault"].to_numpy(),
                             "score": loans.loc[idx, "score"].to_numpy(),
                             "sim_tags": [json.dumps({"late_rate": 0.1, "loan_bank": banks[i % 4] if i % 5 else "EXT", "loan_visible": True}) for i in idx],
                             "first_en": SENTINELS[0], "iban": SENTINELS[1], "msisdn": SENTINELS[2]})  # fmt: skip
        for c in loans.columns:
            cust[f"df_{c}"] = loans.loc[idx, c].to_numpy()
        pq.write_table(
            pa.Table.from_pandas(cust, preserve_index=False), folder / f"truth/customers/part-{s:05d}.parquet"
        )

        tx_rows, acc_rows, bal_rows, loan_rows = [], [], [], []
        for i, cid in zip(idx, cids, strict=True):
            bank, income = banks[i % 4], float(loans.loc[i, "INCOME_AMOUNT_TOTAL"])
            has_card, has_usd, has_payout = i % 4 == 0, i % 7 == 0, i % 10 == 0
            visible_loan = bank in ("tbc", "silk") and i % 5 != 0

            def add(
                ep,
                status,
                date,
                amount,
                text,
                code,
                mcc=None,
                bal=1000.0,
                mirror=False,
                key="",
                ccy="GEL",
                bank=bank,
                cid=cid,
                rows=tx_rows,
            ):
                rows.append(
                    (bank, cid, ep, status, date, amount, ccy, text, code, mcc, bal, mirror, f"{bank}:{cid}:{key}")
                )

            # accounts: TBC types its current account CARD, the others CACC; the credit card has no type at all
            acc_rows.append({"bank": bank, "psu_id": cid, "source_endpoint": "accounts", "resource_id": f"acc-{cid}", "currency": "GEL", "product": "Current account", "cash_account_type": "CARD" if bank == "tbc" else "CACC", "status": "Enabled"})  # fmt: skip
            bal_rows.append({"psu_id": cid, "source_endpoint": "accounts", "resource_id": f"acc-{cid}", "balance_type": "closingBooked", "amount": 1.5 * income, "currency": "GEL"})  # fmt: skip
            if has_card:
                acc_rows.append({"bank": bank, "psu_id": cid, "source_endpoint": "card-accounts", "resource_id": f"card-{cid}", "currency": "GEL", "product": "VISA Credit", "credit_limit": 1000.0, "status": "Enabled"})  # fmt: skip
            if visible_loan:
                acc_rows.append({"bank": bank, "psu_id": cid, "source_endpoint": "loans", "resource_id": f"loan-{cid}", "currency": "GEL", "product": "Consumer loan", "cash_account_type": "LOAN", "status": "Enabled",
                                 "contract_start_date": pd.Timestamp("2025-01-01"), "contract_end_date": pd.Timestamp("2028-01-01"), "interest_rate_pct": 15.0})  # fmt: skip
                sign = -1.0 if bank == "silk" else 1.0  # some banks list a loan balance as a liability (negative)
                for bt, amt in (("ClosingBooked", 2000.0), ("OpeningBooked", 5000.0)):
                    bal_rows.append({"psu_id": cid, "source_endpoint": "loans", "resource_id": f"loan-{cid}", "balance_type": bt, "amount": sign * amt, "currency": "GEL"})  # fmt: skip
            loan_rows.append({"loan_id": f"L{i}", "bank": bank, "cid": cid, "start": pd.Timestamp("2025-01-01") if i % 2 else pd.Timestamp("2026-03-01"), "instalment_minor": 20000, "principal_minor": 500000})  # fmt: skip

            spend, fees = 0.0, 0.0
            for k in range(12):
                # the 5th of Nov 2025 ... Oct 2026, or the 20th / 18th of Oct 2025 ... Sep 2026 (the 18th = a weekend moved it)
                pay = base[k] + pd.Timedelta(days=4) if i % 3 else base[k] - pd.Timedelta(days=11 if k % 2 else 12)
                add("accounts", "booked", pay, income, f"ხელფასი {k}", "PMNT-RCDT-SALA", key=f"s{k}")
                for j in range(3):
                    amt = -(8.0 + 3.0 * j + i % 7)
                    spend += -amt
                    add("accounts", "booked", base[k] + pd.Timedelta(days=j + 1), amt, "ბარათით გადახდა - CARREFOUR" if j % 2 else "ბარათით გადახდა - ADJARABET", "PMNT-CCRD-POSD", key=f"c{k}{j}")  # fmt: skip
                add(
                    "accounts",
                    "booked",
                    base[k] + pd.Timedelta(days=10),
                    -200.0,
                    f"სესხის დაფარვა {k}",
                    "PMNT-ICDT-DMCT",
                    key=f"l{k}",
                )
                if stress[i] > 0.7 and k % 2:
                    fees += 15.0
                    add(
                        "accounts",
                        "booked",
                        base[k] + pd.Timedelta(days=9),
                        -15.0,
                        "ჯარიმა ვადაგადაცილებაზე",
                        "ACMT-MDOP-CHRG",
                        key=f"p{k}",
                    )
                if has_card:  # a credit-card purchase (no text, no MCC) and the repayment that pays for it
                    spend += 50.0
                    add(
                        "card-accounts",
                        "booked",
                        base[k] + pd.Timedelta(days=5),
                        -50.0,
                        None,
                        None,
                        bal=-50.0,
                        key=f"cc{k}",
                    )
                    add(
                        "accounts",
                        "booked",
                        base[k] + pd.Timedelta(days=12),
                        -50.0,
                        "საკრედიტო ბარათის დაფარვა",
                        "PMNT-ICDT-DMCT",
                        key=f"r{k}",
                    )
            if has_usd:
                spend += 10.0 * USD_RATE
                add(
                    "accounts",
                    "booked",
                    base[0] + pd.Timedelta(days=15),
                    -10.0,
                    "ბარათით გადახდა - CARREFOUR",
                    "PMNT-CCRD-POSD",
                    key="usd",
                    ccy="USD",
                )
            if has_payout:  # 3000 GEL arrives as a loan and leaves again two days later: not income, not spending
                add(
                    "accounts",
                    "booked",
                    base[3] + pd.Timedelta(days=20),
                    3000.0,
                    "სესხის გაცემა",
                    "PMNT-RCDT-BOOK",
                    key="in",
                )
                add(
                    "accounts",
                    "booked",
                    base[3] + pd.Timedelta(days=22),
                    -3000.0,
                    "გადახდა ხელშეკრულებით",
                    "PMNT-ICDT-DMCT",
                    key="out",
                )
            add(
                "card-accounts",
                "booked",
                base[0] + pd.Timedelta(days=1),
                -(8.0 + i % 7),
                "ბარათით გადახდა - ADJARABET",
                "PMNT-CCRD-POSD",
                mirror=True,
                key="mirror",
            )  # a copy
            add("accounts", "pending", pd.NaT, -4.0, None, None, mcc="5411", bal=None, key="pending")
            inflow = 12 * income
            expect[cid] = {
                "inflow_month": income,
                "spend_month": spend / 12,
                "net_flow_ratio": float(np.clip((inflow - spend - 2400.0 - fees) / inflow, -5, 1)),
                "n_cards": float(has_card),
                "has_credit_card": float(has_card),
                "loan_visible": float(visible_loan),
                "penalty_n": 6.0 if stress[i] > 0.7 else 0.0,
                "gambling_n": 24.0,
                "bank": bank,
            }
        tx = pd.DataFrame(tx_rows, columns=["bank", "psu_id", "source_endpoint", "status", "booking_date", "amount", "currency", "remittance_information", "bank_transaction_code", "merchant_category_code", "balance_after_amount", "is_card_mirror", "entry_key"])  # fmt: skip
        tx["booking_date"] = tx["booking_date"].dt.date
        table = pa.Table.from_pandas(tx.drop(columns=["amount", "balance_after_amount"]), preserve_index=False)
        table = table.append_column("amount", money(tx["amount"])).append_column(
            "balance_after_amount", money(tx["balance_after_amount"].fillna(0))
        )
        schema = pa.schema([(f.name, pa.date32() if f.name == "booking_date" else f.type) for f in table.schema])
        pq.write_table(table.cast(schema), folder / f"api/transactions/part-{s:05d}.parquet")
        for name, rows in (("accounts", acc_rows), ("balances", bal_rows), ("loans", loan_rows)):
            frame = pd.DataFrame(rows)
            for c in ("contract_start_date", "contract_end_date", "start"):
                if c in frame:
                    frame[c] = frame[c].dt.date
            sub = "truth/loans" if name == "loans" else f"api/{name}"
            pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), folder / f"{sub}/part-{s:05d}.parquet")
    return expect


@pytest.fixture(scope="module")
def mock(tmp_path_factory, data_dir) -> tuple[Path, dict]:
    folder = tmp_path_factory.mktemp("mock")
    return folder, make_mock(folder, data_dir)


@pytest.fixture(scope="module")
def mock_dir(mock) -> Path:
    return mock[0]


def tree_bytes(folder: Path) -> dict:
    return {p.relative_to(folder): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def mock_args(folder: Path, **kw):
    return type("A", (), {"no_mock": False, "mock_dir": str(folder), "data_dir": "x", "fast": False} | kw)()


def test_text_kinds_read_the_georgian_remittance_texts():
    texts = pd.Series(
        [
            "ხელფასი მაისი",
            "ბარათით გადახდა - CARREFOUR",
            "სესხის დაფარვა 12",
            "ჯარიმა ვადაგადაცილებაზე",
            "სწრაფი სესხი",
            "???",
            None,
        ]
    )
    assert EDA.text_kinds(texts).tolist() == ["salary", "card", "loan_repay", "penalty", "quick_loan", "other", "none"]
    merchants = EDA.merchants_of(texts)
    assert pd.isna(merchants.iloc[0]) and merchants.iloc[1] == "CARREFOUR"  # only card purchases name a merchant
    assert EDA.text_kinds(pd.Series(["Coffee shop", "Service fee"])).tolist() == [
        "other",
        "fee",
    ]  # 'fee' is a word, not a suffix


def test_bin_codes_keeps_missing_values_as_their_own_level():
    x = pd.Series([1.0, 2.0, np.nan, 1.0, np.nan, 3.0])  # few levels AND missing values (pandas 3 gave -1 here)
    codes = EDA.bin_codes(x)
    assert (codes >= 0).all() and codes[2] == codes[4] != codes[0]


def scan_first_shard(mock_dir: Path) -> pd.DataFrame:
    world, why = EDA.open_mock(mock_args(mock_dir))
    assert world is not None and not why and world.shards == [0, 1]
    cids = EDA.read_frame(world.path("truth", "customers", 0), ["cid"])["cid"].tolist()
    window = {
        "as_of": pd.Timestamp("2026-10-19"),
        "months": 12,
        "start": pd.Timestamp("2025-10-19"),
        "fx": {"GEL": 1.0, "USD": USD_RATE},
    }
    paths = (
        world.path("api", "transactions", 0),
        world.path("api", "accounts", 0),
        world.path("api", "balances", 0),
        cids,
        window,
    )
    feats, counts = EDA.scan_shard(paths)
    assert list(feats.columns) == EDA.MOCK_FEATURE_NAMES and len(feats) == len(cids)
    assert counts["hygiene"]["mirrors"] == len(cids) and counts["hygiene"]["pending"] == len(
        cids
    )  # planted once each, never summed
    assert counts["hygiene"]["duplicate_entries"] == 0 and counts["hygiene"]["no_fx_rate"] == 0
    return feats


def test_scan_shard_reproduces_the_planted_flows(mock):
    mock_dir, expect = mock
    feats = scan_first_shard(mock_dir)
    exp = pd.DataFrame(expect).T.loc[feats.index]
    for col in (
        "inflow_month",
        "spend_month",
        "net_flow_ratio",
        "n_cards",
        "has_credit_card",
        "loan_visible",
        "penalty_n",
        "gambling_n",
    ):
        np.testing.assert_allclose(
            feats[col].to_numpy(float), exp[col].to_numpy(float), rtol=2e-4, atol=1e-4, err_msg=col
        )
    # every pay day - the 5th, and the 20th / 18th next to the window's start day - lands once in each of the 12 months
    assert feats["salary_months"].eq(12).all() and feats["inflow_cv"].max() < 1e-3
    # the account type column is filled differently by every bank: TBC types its current account CARD, yet everybody has 1
    assert feats["n_current_accounts"].eq(1).all()
    # a loan balance listed as a negative number (liability) must not flip the sign of the loan features
    visible = feats["loan_visible"] > 0
    assert visible.any() and (feats.loc[visible, "loan_outstanding_ratio"] > 0).all()
    np.testing.assert_allclose(feats.loc[visible, "loan_repaid_share"], 0.6, rtol=1e-4)
    assert feats["loan_repay_month"].eq(200.0).all()  # 'სესხის დაფარვა' debits are read as loan repayments


def test_credit_card_repayment_and_a_loan_paid_straight_out_are_not_spending(mock):
    mock_dir, expect = mock
    feats = scan_first_shard(mock_dir)
    cc = next(c for c, e in expect.items() if e["has_credit_card"] and c in feats.index and int(c[1:]) % 10 != 1)
    payout = next(
        c for c in feats.index if (int(c[1:]) - 1) % 10 == 0 and (int(c[1:]) - 1) % 4 != 0
    )  # a payout customer without a card
    # the credit card: 12 purchases of 50 are spending, the 12 repayments of 50 are NOT subtracted a second time
    assert feats.loc[cc, "spend_month"] == pytest.approx(expect[cc]["spend_month"], rel=1e-4)
    assert feats.loc[cc, "net_flow_ratio"] == pytest.approx(expect[cc]["net_flow_ratio"], abs=1e-4)
    # ... but they are still debt service
    assert feats.loc[cc, "debt_service_ratio"] > feats.loc[feats["n_cards"].eq(0).idxmax(), "debt_service_ratio"]
    # 3000 GEL arrives as a loan and leaves again: neither inflow nor spending
    assert feats.loc[payout, "inflow_month"] == pytest.approx(expect[payout]["inflow_month"], rel=1e-4)
    assert feats.loc[payout, "spend_month"] == pytest.approx(expect[payout]["spend_month"], rel=1e-4)


def test_mock_sections_end_to_end_read_only_and_private(data_dir, mock_dir, tmp_path):
    before = tree_bytes(mock_dir)
    assert run_eda(data_dir, tmp_path, "--no-models", "--mock-dir", str(mock_dir), "--sections", "11,12,13,14") == 0
    assert tree_bytes(mock_dir) == before  # the mock folder is only read
    html = (tmp_path / "eda_report.html").read_text(encoding="utf-8")
    assert "This section crashed" not in html and "could not be computed" not in html
    for n in (11, 12, 13, 14):
        assert f"id='s{n}'" in html
    assert "contains the label" in html  # the planted stress formula is found
    link = pd.read_csv(next((tmp_path / "tables").glob("*mock_link.csv")))
    row = link[link["check"].str.startswith("... found among")].iloc[0]
    assert row["result"] == "100.0%"  # every mock customer IS a row of the supplied loan table
    blob = html + "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (tmp_path / "tables").glob("*.csv"))
    assert not any(s in blob for s in SENTINELS)  # names, IBANs and phone numbers never reach the report
    assert (
        str(mock_dir) not in html.split("<footer>")[0]
    )  # no absolute paths in a report that gets shared (the footer echoes the user's own command)
    assert not (tmp_path / "mock_customer_features.csv").exists()  # row-level export is opt-in


def test_customer_identifiers_are_never_read_from_the_mock(data_dir, mock_dir, tmp_path, monkeypatch):
    asked: list[str] = []
    real = EDA.read_frame

    def spy(path, columns):
        asked.extend(columns)
        return real(path, columns)

    monkeypatch.setattr(EDA, "read_frame", spy)
    assert run_eda(data_dir, tmp_path, "--no-models", "--mock-dir", str(mock_dir), "--sections", "11") == 0
    assert asked and not set(asked) & set(FORBIDDEN)
    allowed = {
        *EDA.TRUTH_CUSTOMER_COLS,
        *EDA.MOCK_ACC_COLS,
        *EDA.MOCK_BAL_COLS,
        "date",
        "currency",
        "gel_per_unit",
        "shard",
    }
    loan_cols = {"loan_id", "bank", "cid", "product", "principal_minor", "rate", "term_months", "start", "maturity", "instalment_minor", "outstanding_principal_minor", "overdue_principal_minor", "overdue_count", "days_in_delinquency"}  # fmt: skip
    assert all(c.startswith("df_") or c in allowed | loan_cols for c in asked), sorted(set(asked) - allowed - loan_cols)


def test_shards_are_found_by_their_number_not_by_counting(mock_dir, tmp_path):
    import shutil

    copy = tmp_path / "gappy"
    shutil.copytree(mock_dir, copy)
    for p in copy.glob("*/*/part-00001.parquet"):
        p.rename(p.with_name("part-00005.parquet"))
    world, _ = EDA.open_mock(mock_args(copy))
    assert world.shards == [0, 5] and world.n_shards == 2
    (copy / "api/balances/part-00000.parquet").unlink()  # a shard missing from ONE table is not usable
    world, _ = EDA.open_mock(mock_args(copy))
    assert world.shards == [5]


def test_export_mock_features_is_opt_in_and_never_stale(data_dir, mock_dir, tmp_path):
    base = ["--no-models", "--mock-dir", str(mock_dir), "--sections", "14"]
    assert run_eda(data_dir, tmp_path, *base, "--export-mock-features") == 0
    out = pd.read_csv(tmp_path / "mock_customer_features.csv", index_col="cid")
    assert {"target", "shard", *EDA.MOCK_FEATURE_NAMES} <= set(out.columns) and len(out) == 240
    assert set(out["shard"]) == {0, 1}
    assert run_eda(data_dir, tmp_path, *base) == 0
    assert not (tmp_path / "mock_customer_features.csv").exists()  # a run without the flag removes the old export


def test_mock_part_models_run_on_the_mini_dataset(data_dir, mock_dir, tmp_path):
    assert run_eda(data_dir, tmp_path, "--mock-dir", str(mock_dir), "--sections", "11,14") == 0
    html = (tmp_path / "eda_report.html").read_text(encoding="utf-8")
    assert "could not be computed" not in html and "How much do the bank-data features add?" in html


def test_without_a_mock_dataset_the_mock_sections_are_skipped_cleanly(data_dir, tmp_path):
    assert (
        run_eda(data_dir, tmp_path, "--no-models") == 0
    )  # a non-default --data-dir never auto-detects the real folder
    html = (tmp_path / "eda_report.html").read_text(encoding="utf-8")
    assert all(f"id='s{n}'" not in html for n in (11, 12, 13, 14))


@pytest.mark.parametrize(
    "args",
    [["--mock-dir", "/no/such/folder"], ["--sections", "12"], ["--no-mock", "--mock-dir", "x", "--sections", "13"]],
)
def test_asking_for_missing_mock_data_fails_before_touching_the_output(data_dir, tmp_path, args):
    out = tmp_path / "keep"
    assert run_eda(data_dir, out, "--no-models", "--sections", "1") == 0
    before = tree_bytes(out)
    with pytest.raises(SystemExit) as exc:
        run_eda(data_dir, out, "--no-models", *args)
    assert exc.value.code not in (0, None)
    assert tree_bytes(out) == before


def test_no_mock_switch_skips_an_existing_mock(data_dir, mock_dir, tmp_path):
    assert run_eda(data_dir, tmp_path, "--no-models", "--mock-dir", str(mock_dir), "--no-mock") == 0
    assert "id='s11'" not in (tmp_path / "eda_report.html").read_text(encoding="utf-8")
