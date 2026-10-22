#!/usr/bin/env python3
"""What each confidence level means in numbers, measured on the held-out 20% of clients (never seen in training).

The level (income/confidence.py) is read off the P10-P90 band width. This script predicts the
held-out clients and, per level, records how often the real income fell inside [P10, P90] and how far P50 was off.
The API shows these next to the label.   uv run python income/confidence_calibration.py   ->  models/confidence_calibration.json

All numbers come from synthetic data (income is generated from the same simulator that makes the bank data), so they are
far better than anything a real bank would see: use them to compare levels, not as real-world accuracy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from confidence import LEVELS, level_of, scale  # noqa: E402
from income_predictor import DATA, LABEL, IncomeModel, split_indices  # noqa: E402

OUT = HERE / "models" / "confidence_calibration.json"


def measure(model: IncomeModel, df: pd.DataFrame) -> dict:
    y = df[LABEL].to_numpy(float)
    _, test_idx, _, _ = split_indices(y)
    test = df.iloc[test_idx].reset_index(drop=True)
    pred = model.predict(test)
    actual = test[LABEL].to_numpy(float)
    inside = (actual >= pred["p10"].to_numpy()) & (actual <= pred["p90"].to_numpy())
    error = np.abs(pred["p50"].to_numpy() - actual) / actual

    def stats(mask: np.ndarray) -> dict:
        return {
            "n": int(mask.sum()),
            "inside_p10_p90_pct": round(100 * float(inside[mask].mean()), 1),
            "median_error_pct": round(100 * float(np.median(error[mask])), 2),
            "within_10pct_pct": round(100 * float((error[mask] <= 0.10).mean()), 1),
            "median_band_pct": round(100 * float(np.median(pred["band_share"].to_numpy()[mask])), 1),
        }

    levels = np.array([level_of(b) for b in pred["band_share"]])
    return {
        "basis": f"{len(test):,} held-out clients (20% of the data, never seen in training); synthetic data",
        "interval": "P10-P90 (nominal 80%)",
        "overall": stats(np.ones(len(test), bool)),
        "scale": scale(),
        "levels": {lv: stats(levels == lv) for lv in LEVELS},
    }


if __name__ == "__main__":
    result = measure(IncomeModel.load(), pd.read_csv(DATA))
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
