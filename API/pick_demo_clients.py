#!/usr/bin/env python3
"""Choose the demo clients: ten different kinds of person, taken from the 20% the model never trained on.

For each kind of person, among the held-out clients that fit, the pick is a typical one: the confidence level most
of them get, and an error close to the middle of that group (not the luckiest case). The list is ordered from the
tightest band to the widest, so it reads as a gradient from easy to hard cases.
The measured accuracy per confidence level is in income/models/confidence_calibration.json.

    uv run python API/pick_demo_clients.py     ->  API/demo_clients.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "income"), str(HERE.parent / "eda")]
from confidence import level_of  # noqa: E402
from income_predictor import DATA, LABEL, IncomeModel, split_indices  # noqa: E402

OUT = HERE / "demo_clients.json"
TRUTH = HERE.parent / "openbanking_mock" / "output" / "dataset" / "truth" / "customers"
SHARD_SIZE = 250
BIG_CITIES = {"Tbilisi", "Batumi", "Kutaisi", "Rustavi"}


BANK_NAMES = {"tbc": "TBC", "bog": "BoG", "basisbank": "Basisbank", "terabank": "TeraBank", "procredit": "ProCredit"}


def personal_id_of(cid: str) -> str:
    """The client's 11-digit personal number, as the lender would have it from the ID card."""
    shard = TRUTH / f"part-{(int(cid[1:]) - 1) // SHARD_SIZE:05d}.parquet"
    people = pd.read_parquet(shard, columns=["cid", "person_id"]).set_index("cid")
    return str(people.loc[cid, "person_id"])


def bank_list(banks: str) -> list[str]:
    return [BANK_NAMES.get(b, b.title()) for b in str(banks).split("|") if b]


def archetypes(t: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    salary = t["ob_salary_month"].fillna(0) > 0
    income = t[LABEL]
    banks = t["banks"].fillna("").str.split("|")
    uses = lambda k: banks.apply(lambda lst: k in lst)  # noqa: E731
    deposit = (t.ob_has_savings == 1) & (t.ob_savings_ratio > 1)
    return [
        (
            "Salaried, Tbilisi, one bank",
            "Steady salary credits at one bank.",
            salary & (t.region_name == "Tbilisi") & (t.ob_n_banks == 1) & income.between(2000, 3500),
        ),
        (
            "Saver with a deposit",
            "Salary plus a savings / deposit account.",
            salary & deposit & uses("tbc") & income.between(2500, 6000),
        ),
        ("Three banks, not TBC", "Uses three banks, none of them TBC.", salary & (t.ob_n_banks == 3) & ~uses("tbc")),
        ("Young, low income", "Under 26, early in working life.", (t.Age < 26) & (income < 1500)),
        (
            "Outside the big cities, not BoG",
            "Small town or village; Bank of Georgia is not one of the banks.",
            salary & ~t.city.isin(BIG_CITIES) & ~uses("bog") & income.between(1000, 3000),
        ),
        ("Sole proprietor", "Registered individual entrepreneur.", (t.CLIENT_LEGAL_FORM == 1) & (t.ob_n_banks >= 1)),
        ("No salary credits", "Income does not arrive as a salary: the hard, informal case.", ~salary & (income > 800)),
        ("Receives remittances", "Regular transfers from abroad.", (t.ob_remit_month.fillna(0) > 100) & salary),
        (
            "Small banks only, with a deposit",
            "No TBC, no BoG, no Liberty: only smaller banks, and a savings account.",
            salary & deposit & ~uses("tbc") & ~uses("bog") & ~uses("liberty"),
        ),
        (
            "Hardest case: very wide band",
            "The model finds little to go on, so the band is very wide.",
            t.band_share > 0.20,
        ),
    ]


def main() -> None:
    model, df = IncomeModel.load(), pd.read_csv(DATA)
    _, test_idx, _, _ = split_indices(df[LABEL].to_numpy(float))
    t = df.iloc[test_idx].reset_index(drop=True)
    t = pd.concat([t, model.predict(t)[["p10", "p50", "p90", "band_share", "confidence"]]], axis=1)
    t["error_pct"] = 100 * (t.p50 - t[LABEL]).abs() / t[LABEL]

    t["level"] = [level_of(b) for b in t.band_share]
    picked: list[dict] = []
    used: set[str] = set()
    for title, story, mask in archetypes(t):
        pool = t[mask & ~t.cid.isin(used)]
        if pool.empty:
            print(f"no client for: {title}")
            continue
        modal = pool.level.value_counts().idxmax()  # what most people of this kind get
        pool = pool[pool.level == modal]
        r = pool.iloc[(pool.error_pct - pool.error_pct.median()).abs().argsort().iloc[0]]
        used.add(r.cid)
        picked.append(
            {
                "id": r.cid,
                "personal_id": personal_id_of(r.cid),
                "title": title,
                "story": story,
                "facts": f"age {int(r.Age)}, {r.city}",
                "banks": bank_list(r.banks),
                "has_deposit": bool(r.ob_has_savings == 1),
                "band_share": round(float(r.band_share), 4),
                "model_confidence": r.level,
            }
        )
        print(
            f"{r.cid}  {title:32s} actual {r[LABEL]:7.0f}  P50 {r.p50:7.0f}  err {r.error_pct:4.1f}%  "
            f"band {100 * r.band_share:4.1f}%  {r.level}"
        )
    picked.sort(key=lambda c: c["band_share"])
    OUT.write_text(json.dumps(picked, indent=2, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
