#!/usr/bin/env python3
"""Fairness check of the current income model on the held-out 20% of clients. The model is not changed.

    uv run python income/fairness_check.py     ->  income/models/fairness_report.json

(a) P10-P90 coverage and median absolute % error by sex, region, citizenship and legal form;
(b) how much SEX and GeoCitizen matter: XGBoost total_gain share and rank, mean |SHAP| of the P50 head in GEL and rank;
(c) flip test: set every client's SEX to the other value, predict again, and report the change in P50.

Every number is computed here from the saved model and the synthetic data; none is typed in. Synthetic data only:
the figures compare groups with each other and say nothing about real-world accuracy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from income_predictor import DATA, LABEL, ONE_HOT, IncomeModel, _clean_names, split_indices  # noqa: E402

OUT = HERE / "models" / "fairness_report.json"
SEX_NAMES = {1: "male", 2: "female"}  # codes as in the loan data dictionary
LEGAL_FORM_NAMES = {1: "sole proprietor", 7: "other legal form"}
P50_HEAD = 1  # quantile_alpha = [0.1, 0.5, 0.9]
MIN_GROUP = 100  # smaller groups are listed but not used for the min-max range


def group_stats(df: pd.DataFrame, inside: np.ndarray, error: np.ndarray, by: str, names: dict | None = None) -> list[dict]:
    rows = []
    for value, idx in df.groupby(by, dropna=False).indices.items():
        rows.append(
            {
                "group": (names or {}).get(value, str(value)),
                "n": int(len(idx)),
                "inside_p10_p90_pct": round(100 * float(inside[idx].mean()), 1),
                "median_error_pct": round(100 * float(np.median(error[idx])), 2),
            }
        )
    return sorted(rows, key=lambda r: -r["n"])


def coverage_range(rows: list[dict]) -> dict:
    big = [r for r in rows if r["n"] >= MIN_GROUP] or rows
    low, high = min(big, key=lambda r: r["inside_p10_p90_pct"]), max(big, key=lambda r: r["inside_p10_p90_pct"])
    return {
        "min_pct": low["inside_p10_p90_pct"], "min_group": low["group"],
        "max_pct": high["inside_p10_p90_pct"], "max_group": high["group"],
        "min_group_size": MIN_GROUP,
    }  # fmt: skip


def variable_of(column: str) -> str:
    """Model column -> input variable: one-hot columns (SEX_1, SEX_2) belong to SEX; the rest stand alone."""
    for var in ONE_HOT:
        if column.startswith(_clean_names(pd.Index([var + "_"]))[0] + "_") or column.startswith(var + "_"):
            return var
    return column


def importance(model: IncomeModel, matrix: pd.DataFrame) -> dict:
    columns = list(matrix.columns)
    gain = model.booster.get_booster().get_score(importance_type="total_gain")
    gain = pd.Series({c: float(gain.get(c, 0.0)) for c in columns})
    contribs = model.booster.get_booster().predict(
        xgb.DMatrix(matrix, feature_names=columns), pred_contribs=True
    )[:, P50_HEAD, :-1]
    shap = pd.Series(np.abs(contribs).mean(axis=0), index=columns)
    variable = pd.Series({c: variable_of(c) for c in columns})
    by_var = pd.DataFrame({"gain": gain.groupby(variable).sum(), "shap": shap.groupby(variable).sum()})
    by_var["gain_share_pct"] = 100 * by_var.gain / by_var.gain.sum()
    by_var["gain_rank"] = by_var.gain.rank(ascending=False, method="min").astype(int)
    by_var["shap_rank"] = by_var.shap.rank(ascending=False, method="min").astype(int)
    out = {}
    for var in ("SEX", "GeoCitizen"):
        row = by_var.loc[var]
        out[var] = {
            "gain_share_pct": round(float(row.gain_share_pct), 3),
            "gain_rank": int(row.gain_rank),
            "mean_abs_shap_p50_gel": round(float(row.shap), 2),
            "shap_rank": int(row.shap_rank),
            "of_features": int(len(by_var)),
            "columns": [c for c in columns if variable[c] == var],
        }
    return out


def main() -> None:
    model, df = IncomeModel.load(), pd.read_csv(DATA)
    _, test_idx, _, _ = split_indices(df[LABEL].to_numpy(float))
    test = df.iloc[test_idx].reset_index(drop=True)
    pred = model.predict(test)
    actual = test[LABEL].to_numpy(float)
    inside = ((actual >= pred.p10) & (actual <= pred.p90)).to_numpy()
    error = (np.abs(pred.p50 - actual) / actual).to_numpy()

    by_region = group_stats(test, inside, error, "region_name")
    report = {
        "basis": f"{len(test):,} held-out clients (20% of the data, never seen in training); synthetic data",
        "interval": "P10-P90 (nominal 80%)",
        "overall": {
            "n": int(len(test)),
            "inside_p10_p90_pct": round(100 * float(inside.mean()), 1),
            "median_error_pct": round(100 * float(np.median(error)), 2),
        },
        "by_sex": group_stats(test, inside, error, "SEX", SEX_NAMES),
        "by_region": by_region,
        "by_region_range": coverage_range(by_region),
        "by_citizenship": group_stats(test, inside, error, "GeoCitizen", {"Yes": "Georgian citizen", "No": "not a citizen"}),
        "by_legal_form": group_stats(test, inside, error, "CLIENT_LEGAL_FORM", LEGAL_FORM_NAMES),
    }

    matrix = model.transform(model.prepare(test))
    report["importance"] = importance(model, matrix)

    flipped = test.assign(SEX=3 - test["SEX"])  # 1 <-> 2
    change = 100 * (model.predict(flipped).p50 - pred.p50).abs() / pred.p50.clip(lower=1)
    report["sex_flip_test"] = {
        "n": int(len(test)),
        "median_pct_change": round(float(change.median()), 3),
        "p95_pct_change": round(float(change.quantile(0.95)), 3),
        "max_pct_change": round(float(change.max()), 3),
        "share_with_any_change_pct": round(100 * float((change > 0).mean()), 1),
    }
    sole = next(r for r in report["by_legal_form"] if r["group"] == "sole proprietor")
    other = next(r for r in report["by_legal_form"] if r["group"] != "sole proprietor")
    report["sole_proprietor_gap"] = {
        "coverage_gap_pts": round(other["inside_p10_p90_pct"] - sole["inside_p10_p90_pct"], 1),
        "median_error_sole_pct": sole["median_error_pct"],
        "median_error_other_pct": other["median_error_pct"],
        "note": "Known limitation: income of sole proprietors is estimated less accurately than that of other clients.",
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
