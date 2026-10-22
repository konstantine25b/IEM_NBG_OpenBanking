"""Why the model says what it says: TreeSHAP contributions of the P50 model, grouped into readable drivers.

XGBoost computes exact TreeSHAP values (`pred_contribs`); they add up to the prediction, so
    starting point (the average client) + sum(effects) = P50.
Hundreds of one-hot and bank flags would be noise, so features are summed into a few named groups.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xgboost as xgb

# (group label, feature-name prefixes); the first matching group wins.
GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Salary credits", ("ob_salary",)),
    ("Total money coming in", ("ob_inflow",)),
    ("Transfers and remittances", ("ob_remit", "ob_p2p")),
    ("RS declared income", ("INCOME_AMOUNT_SOI04",)),
    (
        "Credit bureau (Creditinfo)",
        ("last1Month", "last3Month", "last6Month", "last12Month", "ExistingOperations", "TerminatedOperations"),
    ),
    (
        "Spending pattern",
        (
            "ob_spend",
            "ob_card",
            "ob_atm",
            "ob_bills",
            "ob_net_flow",
            "ob_n_merchants",
            "ob_gambling",
            "ob_rent",
            "ob_fee",
        ),
    ),
    ("Balances and savings", ("ob_bal", "ob_end_balance", "ob_savings", "ob_has_savings")),
    (
        "Loans and debt service",
        ("ob_loan", "ob_debt", "ob_quick", "ob_penalty", "ob_overdraft", "ob_credit_limit", "ob_has_credit"),
    ),
    ("Banking footprint", ("ob_n_", "ob_tx_per", "bank_")),
    ("Age and profile", ("Age", "SEX", "GeoCitizen", "CLIENT_LEGAL_FORM", "segment")),
    ("Location", ("region_name", "city")),
]


def group_of(feature: str) -> str:
    return next((label for label, prefixes in GROUPS if feature.startswith(prefixes)), "Other signals")


def explain_p50(model, matrix: pd.DataFrame, p50: float, top: int = 6) -> dict:
    """Contributions for one client. `matrix` is the model matrix (IncomeModel.transform) of that client."""
    booster = model.booster.get_booster()
    contribs = booster.predict(xgb.DMatrix(matrix, feature_names=list(matrix.columns)), pred_contribs=True)[0]
    # one row per quantile head, last column is the bias; pick the head whose total is the P50 (the middle one)
    totals = contribs.sum(axis=1)
    head = contribs[int(np.argsort(totals)[1])]
    base, per_feature = float(head[-1]), head[:-1]
    grouped: dict[str, float] = {}
    for name, value in zip(matrix.columns, per_feature, strict=True):
        grouped[group_of(name)] = grouped.get(group_of(name), 0.0) + float(value)
    ranked = sorted(grouped.items(), key=lambda kv: abs(kv[1]), reverse=True)
    shown, rest = ranked[:top], ranked[top:]
    drivers = [{"driver": k, "effect_gel": round(v)} for k, v in shown]
    if rest:
        drivers.append({"driver": f"{len(rest)} smaller factors", "effect_gel": round(sum(v for _, v in rest))})
    return {
        "method": "TreeSHAP on the P50 model",
        "starting_point_gel": round(base),
        "drivers": drivers,
        "model_p50_gel": round(float(totals[int(np.argsort(totals)[1])])),
        "reported_p50_gel": round(p50),
    }
