#!/usr/bin/env python3
"""Choose the demo clients: ten different kinds of person, taken from the 20% the model never trained on.

For each kind of person, among the held-out clients that fit, the pick is a typical one: the confidence level most
of them get, and an error close to the middle of that group (not the luckiest case). The P10-P90 range holds the real
income about 8 times in 10 on held-out clients, so eight of the ten picks are hits and two (MISSES) are near-misses:
the demo should never suggest that the range always contains the real income. The list is ordered from the
tightest band to the widest, so it reads as a gradient from easy to hard cases.
The measured accuracy per confidence level is in income/models/confidence_calibration.json.

    uv run python API/pick_demo_clients.py     ->  API/demo_clients.json
"""

from __future__ import annotations

import collections
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
MISSES = {"Receives remittances", "Hardest case: very wide band"}  # shown as near-misses


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
    # the common real-life case: BoG and TBC plus one more bank
    trio = (t.ob_n_banks == 3) & uses("tbc") & uses("bog")
    small = ~uses("tbc") & ~uses("bog") & ~uses("liberty")
    return [
        (
            "Salaried, three banks",
            "Salary credits; BoG and TBC plus a third bank.",
            trio & salary & income.between(1500, 6000),
        ),
        (
            "Saver with a deposit",
            "Three banks and a savings / deposit account.",
            trio & salary & (t.ob_has_savings == 1),
        ),
        ("Young, low income", "Under 26, early in working life; three banks.", trio & (t.Age < 26)),
        ("Outside the big cities", "Small town or village; three banks.", trio & salary & ~t.city.isin(BIG_CITIES)),
        (
            "Receives remittances",
            "Regular transfers from abroad; three banks.",
            trio & (t.ob_remit_month.fillna(0) > 100),
        ),
        ("Sole proprietor", "Registered individual entrepreneur; three banks.", trio & (t.CLIENT_LEGAL_FORM == 1)),
        ("No salary credits", "Income does not arrive as a salary: the hard, informal case.", trio & ~salary),
        ("Three banks, not TBC", "Uses three banks, none of them TBC.", salary & (t.ob_n_banks == 3) & ~uses("tbc")),
        (
            "Small banks only, with a deposit",
            "No TBC, no BoG, no Liberty: smaller banks and a savings account.",
            (t.ob_n_banks >= 1) & small & deposit & salary,
        ),
        (
            "Hardest case: very wide band",
            "The model finds little to go on, so the band is very wide.",
            (t.ob_n_banks >= 2) & (t.band_share > 0.25),
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
    used_banks: set[str] = set()  # banks other than TBC and BoG already shown
    for title, story, mask in archetypes(t):
        pool = t[mask & ~t.cid.isin(used)]
        if pool.empty:
            print(f"no client for: {title}")
            continue
        modal = pool.level.value_counts().idxmax()  # what most people of this kind get
        pool = pool[pool.level == modal]
        fresh = pool[[not (set(b.split("|")) - {"tbc", "bog"} <= used_banks) for b in pool.banks]]
        pool = fresh if len(fresh) else pool
        hit = (pool[LABEL] >= pool.p10) & (pool[LABEL] <= pool.p90)
        want = title not in MISSES
        pool = pool[hit == want] if (hit == want).any() else pool
        if not want and (pool.error_pct <= 10).any():
            pool = pool[pool.error_pct <= 10]  # a near-miss, not a wild one
        pool = pool[pool.ob_n_banks == pool.ob_n_banks.max()]  # as many banks as that kind of client has here
        r = pool.iloc[(pool.error_pct - pool.error_pct.median()).abs().argsort().iloc[0]]
        used.add(r.cid)
        used_banks |= set(r.banks.split("|")) - {"tbc", "bog"}
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
                "range_holds_actual": bool(r[LABEL] >= r.p10 and r[LABEL] <= r.p90),
                "model_confidence": r.level,
            }
        )
        print(
            f"{r.cid}  {title:32s} actual {r[LABEL]:7.0f}  P50 {r.p50:7.0f}  err {r.error_pct:4.1f}%  "
            f"band {100 * r.band_share:4.1f}%  {r.level}  {'inside' if r[LABEL] >= r.p10 and r[LABEL] <= r.p90 else 'OUTSIDE'}"
        )
    previous = json.loads(OUT.read_text()) if OUT.exists() else []
    catalog = {c["id"]: c for c in previous}  # the list keeps its order; new picks go to the end
    for c in picked:
        catalog.setdefault(c["id"], c)
    clients = list(catalog.values())
    titles = collections.Counter(c["title"] for c in clients)
    for c in clients:  # one archetype can match several clients: tell them apart by city (then by first bank)
        if titles[c["title"]] > 1 and not c["title"].endswith(")"):
            c["title"] += f" ({c['facts'].split(', ', 1)[1]})"
    titles = collections.Counter(c["title"] for c in clients)
    for c in clients:
        if titles[c["title"]] > 1:
            c["title"] = c["title"][:-1] + f", {c['banks'][0]})"
    OUT.write_text(json.dumps(clients, indent=2, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
