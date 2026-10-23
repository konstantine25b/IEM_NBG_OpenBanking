"""Held-out synthetic validation and subgroup diagnostics. No fitting and no production-fairness claim."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from income_predictor import DATA, LABEL, IncomeModel, split_indices

OUT = Path(__file__).resolve().parents[1] / ".runtime" / "model-evaluation.json"


def evaluate(model: IncomeModel, df: pd.DataFrame) -> dict:
    _, test_idx, _, _ = split_indices(df[LABEL].to_numpy(float))
    test = df.iloc[test_idx].reset_index(drop=True)
    pred = model.predict(test)
    actual = test[LABEL].to_numpy(float)
    error = np.abs(pred.p50.to_numpy() - actual)
    inside = (pred.p10.to_numpy() <= actual) & (actual <= pred.p90.to_numpy())
    salary = test.ob_salary_month.to_numpy(float)
    covered = np.isfinite(salary) & (salary > 0)

    def metrics(mask):
        n = int(mask.sum())
        return {
            "n": n,
            "mae_gel": round(float(error[mask].mean()), 2) if n else None,
            "median_error_pct": round(float(np.median(error[mask] / np.clip(actual[mask], 1, None))) * 100, 2)
            if n
            else None,
            "interval_coverage_pct": round(float(inside[mask].mean()) * 100, 2) if n else None,
            "small_sample": n < 100,
        }

    dimensions = {
        "Income profile": pd.Series(
            np.where(test.CLIENT_LEGAL_FORM.eq(1), "Self-employed / legal form 1", "Other / legal form 7")
        ),
        "Sex code (synthetic)": test.SEX.astype(str),
        "Age band": pd.cut(test.Age, [0, 25, 40, 60, np.inf], labels=["Under 26", "26–40", "41–60", "61+"]).astype(str),
        "Region": test.region_name.astype(str),
    }
    groups = [
        {"dimension": dim, "group": group, **metrics(values.eq(group).to_numpy())}
        for dim, values in dimensions.items()
        for group in sorted(values.unique())
    ]
    group_gaps = []
    for dimension in dimensions:
        large = [g for g in groups if g["dimension"] == dimension and not g["small_sample"]]
        if large:
            group_gaps.append(
                {
                    "dimension": dimension,
                    "mae_range_gel": round(max(g["mae_gel"] for g in large) - min(g["mae_gel"] for g in large), 2),
                    "coverage_range_pp": round(
                        max(g["interval_coverage_pct"] for g in large) - min(g["interval_coverage_pct"] for g in large),
                        2,
                    ),
                }
            )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_sha256": model.artifact_sha256,
        "basis": "20% held-out customers selected with the training recipe's fixed split; no retraining",
        "population": len(test),
        "overall": metrics(np.ones(len(test), bool)),
        "groups": groups,
        "group_gaps": group_gaps,
        "salary_baseline": {
            "covered": int(covered.sum()),
            "abstained": int((~covered).sum()),
            "coverage_pct": round(float(covered.mean()) * 100, 2),
            "salary_mae_on_covered_gel": round(float(np.abs(salary[covered] - actual[covered]).mean()), 2),
            "model_mae_on_same_covered_gel": metrics(covered)["mae_gel"],
        },
        "feature_risks": "The model uses age, sex, citizenship and location. Feature justification and demographic sensitivity "
        "review are required before banking use; subgroup metrics alone do not establish fairness.",
        "limitations": "Synthetic data only; bank behaviour and targets share a generator. These are engineering diagnostics, "
        "not independent real-world validation, a fairness certification, or evidence of increased approvals.",
    }


def generate(model: IncomeModel | None = None) -> dict:
    report = evaluate(model or IncomeModel.load(), pd.read_csv(DATA))
    report["dataset_sha256"] = hashlib.sha256(DATA.read_bytes()).hexdigest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(OUT)
    return report


if __name__ == "__main__":
    report = generate()
    print(json.dumps({"output": str(OUT), "held_out_customers": report["population"], "overall": report["overall"]}))
