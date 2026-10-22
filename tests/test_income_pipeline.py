"""Tests for income/income_pipeline.py: a person as a bank would receive them -> income estimate.

The registry side comes from the supplied loan files nbg_synthetic_data/df_*.csv (not from income_dataset.csv), the
bank side is that person's raw API rows (api/transactions, accounts, balances). The pipeline must rebuild the row of
income_dataset.csv and predict from it.  Skipped when the saved model, the data or the generated dataset is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "income"))

import income_pipeline as pipe  # noqa: E402
import income_predictor as pred  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (pred.MODEL_PATH.is_file() and pred.DATA.is_file() and (pipe.API / "transactions").is_dir()),
    reason="saved model, income_dataset.csv or the generated api/ dataset is missing",
)
LOAN_FILES = sorted((ROOT / "nbg_synthetic_data").glob("df_[0-9]*.csv"))


@pytest.fixture(scope="module")
def model() -> pred.IncomeModel:
    return pred.IncomeModel.load()


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    return pd.read_csv(pred.DATA)


@pytest.fixture(scope="module")
def loan_rows() -> pd.DataFrame:
    """All supplied loan files, keyed by DISBURSED_AMOUNT (unique per loan) to find a customer's registry row."""
    frames = [pd.read_csv(f).assign(source_file=f.name) for f in LOAN_FILES]
    rows = pd.concat(frames, ignore_index=True)
    rows["key"] = rows["DISBURSED_AMOUNT"].round(6)
    return rows.drop_duplicates("key").set_index("key")


def registry_from_loan_file(loan_rows, income_row: pd.Series) -> dict:
    """The person's row in df_*.csv: found by loan amount, so nothing comes from income_dataset.csv."""
    found = loan_rows.loc[round(float(income_row["DISBURSED_AMOUNT"]), 6)]
    return found.drop(labels=["source_file"]).to_dict()


def test_registry_row_exists_in_loan_files(dataset, loan_rows):
    row = dataset.iloc[0]  # C00001 is a row of df_1.csv
    registry = registry_from_loan_file(loan_rows, row)
    assert registry["Age"] == row["Age"] and registry["SEX"] == row["SEX"]
    assert loan_rows.loc[round(float(row["DISBURSED_AMOUNT"]), 6), "source_file"] == "df_1.csv"


def test_open_banking_features_match_the_dataset(dataset, model):
    ob_cols = [c for c in dataset.columns if c.startswith("ob_")]
    for i in (0, 1, 4, 11, 40):
        row = dataset.iloc[i]
        built = pipe.open_banking_features(pipe.load_open_banking(row["cid"]), model)
        a = pd.Series({c: built[c] for c in ob_cols}, dtype=float)
        b = row[ob_cols].astype(float)
        assert (a.isna() == b.isna()).all(), row["cid"]
        np.testing.assert_allclose(a.fillna(0), b.fillna(0), rtol=1e-3, atol=1e-2, err_msg=row["cid"])


def test_built_row_matches_dataset_row(dataset, model, loan_rows):
    row = dataset.iloc[2]
    registry = registry_from_loan_file(loan_rows, row)
    built = pipe.build_row(registry, pipe.load_open_banking(row["cid"]), model)
    assert built["region_name"] == row["region_name"]
    assert built["segment"] == row["segment"]
    assert set(built["banks"].split("|")) <= set(row["banks"].split("|"))
    assert built["Age"] == row["Age"]


def test_pipeline_prediction_equals_dataset_prediction(dataset, model, loan_rows):
    for i in (0, 1, 2, 3):
        row = dataset.iloc[i]
        registry = registry_from_loan_file(loan_rows, row)
        via_pipeline = pipe.predict_person(registry, pipe.load_open_banking(row["cid"]), model)
        via_dataset = model.predict(dataset.iloc[[i]]).iloc[0]
        # city is not in the registry, so a small drift is allowed; the estimate must stay in the same place
        assert abs(via_pipeline["p50"] - via_dataset["p50"]) <= 0.05 * via_dataset["p50"] + 20, row["cid"]


def test_output_contract(model, dataset, loan_rows):
    row = dataset.iloc[3]
    out = pipe.predict_person(registry_from_loan_file(loan_rows, row), pipe.load_open_banking(row["cid"]), model)
    assert set(out) == {"p10", "p50", "p90", "band_share", "confidence"}
    assert 0 <= out["p10"] <= out["p50"] <= out["p90"]
    assert out["confidence"] in pred.CONFIDENCE_LEVELS


def test_open_banking_as_plain_records(model, dataset, loan_rows):
    """A TPP gets JSON, not DataFrames: lists of dicts with ISO date strings must give the same estimate."""
    row = dataset.iloc[1]
    registry = registry_from_loan_file(loan_rows, row)
    frames = pipe.load_open_banking(row["cid"])
    records = {t: f.astype(object).where(f.notna(), None).to_dict("records") for t, f in frames.items()}
    for t in records:
        for r in records[t]:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
    a = pipe.predict_person(registry, frames, model)
    b = pipe.predict_person(registry, records, model)
    assert a == b


def test_no_bank_data_still_predicts_with_a_wider_band(model, dataset, loan_rows):
    row = dataset.iloc[3]
    registry = registry_from_loan_file(loan_rows, row)
    without = pipe.predict_person(registry, None, model)
    with_ob = pipe.predict_person(registry, pipe.load_open_banking(row["cid"]), model)
    assert 0 <= without["p10"] <= without["p50"] <= without["p90"]
    assert without["band_share"] > with_ob["band_share"]


def test_end_to_end_accuracy_on_held_out_people(model, dataset, loan_rows):
    _, test_idx, _, _ = pred.split_indices(dataset[pred.LABEL].to_numpy(float))
    sample = dataset.iloc[test_idx[:60]]
    errors, inside = [], []
    for _, row in sample.iterrows():
        out = pipe.predict_person(registry_from_loan_file(loan_rows, row), pipe.load_open_banking(row["cid"]), model)
        errors.append(abs(out["p50"] - row[pred.LABEL]))
        inside.append(out["p10"] <= row[pred.LABEL] <= out["p90"])
    assert np.mean(errors) < 120, f"MAE {np.mean(errors):.0f} GEL"
    assert np.mean(inside) >= 0.65, f"coverage {np.mean(inside):.0%}"
