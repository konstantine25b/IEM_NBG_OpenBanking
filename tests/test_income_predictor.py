"""Tests for income/income_predictor.py: the saved model, fed raw rows of nbg_synthetic_data/income_dataset.csv.

They use the model saved by `uv run python income/income_predictor.py train` and skip when it (or the data) is absent.
The accuracy tests score only the 20% test people the model never trained on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "income"))

import income_predictor as ip  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (ip.MODEL_PATH.is_file() and ip.DATA.is_file()), reason="saved model or income_dataset.csv is missing"
)


@pytest.fixture(scope="module")
def model() -> ip.IncomeModel:
    return ip.IncomeModel.load()


@pytest.fixture(scope="module")
def data() -> pd.DataFrame:
    return pd.read_csv(ip.DATA)


@pytest.fixture(scope="module")
def test_people(data) -> pd.DataFrame:
    _, test_idx, _, _ = ip.split_indices(data[ip.LABEL].to_numpy(float))
    return data.iloc[test_idx].reset_index(drop=True)


def test_output_shape_and_order(model, test_people):
    out = model.predict(test_people.head(200))
    assert list(out.columns) == ["cid", "p10", "p50", "p90", "band_share", "confidence"]
    assert len(out) == 200
    assert (out["p10"] <= out["p50"]).all() and (out["p50"] <= out["p90"]).all()
    assert (out["p10"] >= 0).all()
    assert out["confidence"].isin(ip.CONFIDENCE_LEVELS).all()
    assert out["cid"].tolist() == test_people["cid"].head(200).tolist()


def test_single_dict_equals_dataframe_row(model, test_people):
    row = json.loads(test_people.iloc[[0]].to_json(orient="records"))[0]  # NaN becomes null, like a JSON request
    one = model.predict(row)
    frame = model.predict(test_people.iloc[[0]])
    pd.testing.assert_frame_equal(one, frame)


def test_prediction_does_not_depend_on_batch(model, test_people):
    batch = model.predict(test_people.head(50))
    alone = pd.concat([model.predict(test_people.iloc[[i]]) for i in (0, 17, 49)], ignore_index=True)
    pd.testing.assert_frame_equal(batch.iloc[[0, 17, 49]].reset_index(drop=True), alone)


def test_ignores_label_and_loan_columns(model, test_people):
    row = test_people.head(20)
    changed = row.copy()
    changed[ip.LABEL] = 1.0
    changed["DISBURSED_AMOUNT"] = 999_999.0
    pd.testing.assert_frame_equal(model.predict(row), model.predict(changed))


def test_sparse_input_is_accepted_and_less_confident(model, test_people):
    sparse = model.predict({"Age": 30, "SEX": 1, "region_name": "Tbilisi", "banks": "tbc"}).iloc[0]
    full = model.predict(test_people.head(500))
    assert sparse["p10"] <= sparse["p50"] <= sparse["p90"]
    assert sparse["band_share"] > full["band_share"].median()


def test_unknown_category_does_not_crash(model, test_people):
    row = test_people.iloc[[0]].copy()
    row["region_name"] = "Atlantis"
    row["banks"] = "not_a_bank"
    out = model.predict(row)
    assert np.isfinite(out[["p10", "p50", "p90"]].to_numpy()).all()


def test_save_load_roundtrip(model, test_people, tmp_path):
    path = model.save(tmp_path / "m.joblib")
    again = ip.IncomeModel.load(path)
    pd.testing.assert_frame_equal(model.predict(test_people.head(30)), again.predict(test_people.head(30)))


def test_accuracy_on_held_out_people(model, test_people):
    out = model.predict(test_people)
    actual = test_people[ip.LABEL].to_numpy(float)
    mae = float(np.mean(np.abs(actual - out["p50"])))
    inside = float(np.mean((actual >= out["p10"]) & (actual <= out["p90"])))
    assert mae < 80, f"P50 MAE {mae:.1f} GEL"
    assert 0.75 <= inside <= 0.85, f"P10-P90 coverage {inside:.1%}"


def test_narrow_bands_are_more_accurate(model, test_people):
    out = model.predict(test_people)
    err = np.abs(test_people[ip.LABEL].to_numpy(float) - out["p50"].to_numpy())
    narrow = out["confidence"].isin(["very high", "high"]).to_numpy()
    wide = out["confidence"].isin(["low", "very low"]).to_numpy()
    assert narrow.any() and wide.any()
    assert err[narrow].mean() < err[wide].mean()
