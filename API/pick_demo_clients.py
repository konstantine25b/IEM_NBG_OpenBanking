#!/usr/bin/env python3
"""Choose the demo clients: ten different kinds of person, taken from the 20% the model never trained on.

For each archetype, among the held-out clients that fit it, take the one whose P50 is closest to the registry income.
So these are the best-case examples of each type, picked on purpose for the demo: do not read them as typical accuracy
(the measured accuracy per confidence level is in income/models/confidence_calibration.json).

    uv run python API/pick_demo_clients.py     ->  API/demo_clients.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "income"), str(HERE.parent / "eda")]
from income_predictor import DATA, LABEL, IncomeModel, split_indices  # noqa: E402

OUT = HERE / "demo_clients.json"
BIG_CITIES = {"Tbilisi", "Batumi", "Kutaisi", "Rustavi"}


def archetypes(t: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    salary = t["ob_salary_month"].fillna(0) > 0
    income = t[LABEL]
    return [
        (
            "Salaried, Tbilisi, one bank",
            "Steady salary credits at one bank.",
            salary & (t.region_name == "Tbilisi") & (t.ob_n_banks == 1) & income.between(2000, 3500),
        ),
        (
            "Salaried, two banks",
            "Salary at one bank, everyday spending at another.",
            salary & (t.ob_n_banks == 2) & income.between(3500, 6000),
        ),
        ("Salaried, three banks", "Uses three banks at once.", salary & (t.ob_n_banks == 3)),
        ("Young, low income", "Under 26, early in working life.", (t.Age < 26) & (income < 1500)),
        (
            "Lives outside the big cities",
            "Small town or village, regional salary.",
            salary & ~t.city.isin(BIG_CITIES) & income.between(1000, 3000),
        ),
        ("Sole proprietor", "Registered individual entrepreneur.", (t.CLIENT_LEGAL_FORM == 1) & (t.ob_n_banks >= 1)),
        ("No salary credits", "Income does not arrive as a salary: the hard, informal case.", ~salary & (income > 800)),
        ("Receives remittances", "Regular transfers from abroad.", (t.ob_remit_month.fillna(0) > 100) & salary),
        ("High earner", "Top of the income range.", salary & (income > 8000)),
        (
            "Wide, uncertain band",
            "Model is unsure: shows a low confidence label.",
            t.confidence.isin(["low", "very low"]) & salary,
        ),
    ]


def main() -> None:
    model, df = IncomeModel.load(), pd.read_csv(DATA)
    _, test_idx, _, _ = split_indices(df[LABEL].to_numpy(float))
    t = df.iloc[test_idx].reset_index(drop=True)
    t = pd.concat([t, model.predict(t)[["p10", "p50", "p90", "band_share", "confidence"]]], axis=1)
    t["error_pct"] = 100 * (t.p50 - t[LABEL]).abs() / t[LABEL]

    picked: list[dict] = []
    used: set[str] = set()
    for title, story, mask in archetypes(t):
        pool = t[mask & ~t.cid.isin(used)].sort_values("error_pct")
        if pool.empty:
            print(f"no client for: {title}")
            continue
        r = pool.iloc[0]
        used.add(r.cid)
        picked.append(
            {
                "id": r.cid,
                "title": title,
                "story": story,
                "facts": f"age {int(r.Age)}, {r.city}, {int(r.ob_n_banks)} bank{'s' if r.ob_n_banks != 1 else ''}",
                "model_confidence": r.confidence,
            }
        )
        print(f"{r.cid}  {title:32s} actual {r[LABEL]:7.0f}  P50 {r.p50:7.0f}  err {r.error_pct:4.1f}%  {r.confidence}")
    OUT.write_text(json.dumps(picked, indent=2, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
