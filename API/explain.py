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
    ("RS remittance income (SOI04)", ("INCOME_AMOUNT_SOI04",)),
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


# readable names for the model's own columns (anything else is cleaned up by `label_of`)
FEATURE_LABELS = {
    "ob_salary_month": "Average monthly salary credits", "ob_salary_months": "Months with a salary credit",
    "ob_salary_cv": "How much the salary varies", "ob_salary_day_std": "Spread of the salary pay day",
    "ob_inflow_month": "Average money in per month", "ob_inflow_cv": "How much money in varies",
    "ob_inflow_trend": "Trend of money in", "ob_spend_month": "Average spending per month",
    "ob_spend_cv": "How much spending varies", "ob_spend_to_inflow": "Spending as a share of money in",
    "ob_net_flow_ratio": "Net flow (money in minus out)", "ob_card_spend_month": "Card spending per month",
    "ob_card_ticket": "Average card purchase", "ob_atm_share": "Share of cash withdrawals",
    "ob_bills_share": "Share spent on bills", "ob_rent_month": "Rent paid per month",
    "ob_n_merchants": "Number of different merchants", "ob_gambling_n": "Gambling payments",
    "ob_remit_month": "Remittances received per month", "ob_p2p_in_share": "Share of money in from person-to-person transfers",
    "ob_p2p_out_share": "Share of spending on person-to-person transfers", "ob_bal_min_ratio": "Lowest balance (vs. income)",
    "ob_bal_mean_ratio": "Average balance (vs. income)", "ob_bal_neg_share": "Share of time overdrawn",
    "ob_end_balance_ratio": "Current balance (vs. income)", "ob_savings_ratio": "Savings (vs. income)",
    "ob_has_savings": "Has a savings account", "ob_loan_visible": "A loan is visible at a bank",
    "ob_loan_repay_month": "Loan repayments per month", "ob_debt_service_ratio": "Debt payments (vs. income)",
    "ob_quick_loan_n": "Quick loans taken", "ob_penalty_n": "Penalty charges", "ob_overdraft_fee_n": "Overdraft fees",
    "ob_fee_share": "Share of spending on fees", "ob_credit_limit_ratio": "Credit card limit (vs. income)",
    "ob_has_credit_card": "Has a credit card", "ob_n_banks": "Number of banks", "ob_n_current_accounts": "Current accounts",
    "ob_n_fx_accounts": "Foreign-currency accounts", "ob_n_cards": "Cards", "ob_tx_per_month": "Transactions per month",
    "INCOME_AMOUNT_SOI04": "RS remittance income (SOI04)", "last1Month": "Credit searches, last month",
    "last3Month": "Credit searches, last 3 months", "last6Month": "Credit searches, last 6 months",
    "last12Month": "Credit searches, last 12 months", "ExistingOperationsDebtor": "Open loans and contracts",
    "TerminatedOperationsDebtor": "Closed loans and contracts", "Age": "Age",
}  # fmt: skip


def label_of(feature: str) -> str:
    if feature in FEATURE_LABELS:
        return FEATURE_LABELS[feature]
    for prefix, text in (("bank_", "Uses "), ("region_name_", "Region: "), ("city_", "City: ")):
        if feature.startswith(prefix):
            return text + feature.removeprefix(prefix).replace("_", " ").title()
    if feature.endswith("_missing"):
        return f"{label_of(feature.removesuffix('_missing'))} is not available"
    return feature.removeprefix("ob_").replace("_", " ").capitalize()


MIN_FEATURE_GEL = 0.5  # features that move the estimate less than this are only counted
MAX_FEATURES_PER_GROUP = 12


def explain_p50(model, matrix: pd.DataFrame, p50: float, top: int = 6) -> dict:
    """Contributions for one client. `matrix` is the model matrix (IncomeModel.transform) of that client.

    `drivers` is the short list (the top groups, the rest summed); `all_drivers` has every group with the model
    features inside it. `share_pct` is a factor's size (absolute effect) as a percentage of all factors' sizes added
    together, so the shares of all groups sum to 100; `pct_of_p50` is its effect as a percentage of the estimate."""
    booster = model.booster.get_booster()
    contribs = booster.predict(xgb.DMatrix(matrix, feature_names=list(matrix.columns)), pred_contribs=True)[0]
    # one row per quantile head, last column is the bias; pick the head whose total is the P50 (the middle one)
    totals = contribs.sum(axis=1)
    head = contribs[int(np.argsort(totals)[1])]
    base, per_feature = float(head[-1]), head[:-1]
    values = matrix.iloc[0]

    groups: dict[str, list[tuple[str, float]]] = {}
    for name, value in zip(matrix.columns, per_feature, strict=True):
        groups.setdefault(group_of(name), []).append((name, float(value)))
    effect = {g: sum(v for _, v in feats) for g, feats in groups.items()}
    size = sum(abs(v) for v in effect.values()) or 1.0

    def row(label: str, gel: float) -> dict:
        return {
            "driver": label,
            "effect_gel": round(gel),
            "share_pct": round(100 * abs(gel) / size, 1),
            "pct_of_p50": round(100 * gel / p50, 1) if p50 else None,
        }

    ranked = sorted(effect.items(), key=lambda kv: abs(kv[1]), reverse=True)
    drivers = [row(g, v) for g, v in ranked[:top]]
    if ranked[top:]:
        drivers.append(row(f"{len(ranked) - top} smaller factors", sum(v for _, v in ranked[top:])))

    all_drivers = []
    for g, v in ranked:
        feats = sorted(groups[g], key=lambda fv: abs(fv[1]), reverse=True)
        shown = [fv for fv in feats if abs(fv[1]) >= MIN_FEATURE_GEL][:MAX_FEATURES_PER_GROUP]
        value_of = lambda n: None if not np.isfinite(values[n]) else round(float(values[n]), 2)  # noqa: E731
        all_drivers.append(
            row(g, v)
            | {
                "features": [
                    {
                        "feature": n,
                        "label": label_of(n),
                        "value": value_of(n),
                        "effect_gel": round(f, 1),
                        "share_pct": round(100 * abs(f) / size, 1),
                    }
                    for n, f in shown
                ],  # fmt: skip
                "features_not_shown": len(feats) - len(shown),
            }
        )
    return {
        "method": "TreeSHAP on the P50 model",
        "starting_point_gel": round(base),
        "drivers": drivers,
        "all_drivers": all_drivers,
        "model_p50_gel": round(float(totals[int(np.argsort(totals)[1])])),
        "reported_p50_gel": round(p50),
    }
