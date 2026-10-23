"""Explain API transaction evidence without reading simulator labels or inventing payer classifications."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

import pandas as pd

from EDA import CODE_KINDS, text_kinds

SERVICE = re.compile(r"payment for services|გადახდა მომსახურებაზე", re.I)
REFUND = re.compile(r"refund|reversal|დაბრუნება", re.I)


def mask(value) -> str:
    text = str(value) if pd.notna(value) else ""
    return "••••" + text[-4:] if text else "Not supplied"


def transaction_evidence(tables: dict, window: dict) -> dict:
    tx, accounts = tables["transactions"], tables["accounts"]
    own = set(accounts["iban"].dropna())
    months = window["months"]
    start = pd.Timestamp(window["as_of"]) - pd.DateOffset(months=months)
    end = pd.Timestamp(window["as_of"])
    fx = window["fx"]
    rows, service, salaries, monthly = [], [], [], defaultdict(lambda: {"count": 0, "total_gel": 0.0})
    kinds = text_kinds(tx["remittance_information"]) if len(tx) else []
    for i, item in enumerate(tx.to_dict("records")):
        description = str(item["remittance_information"]) if pd.notna(item.get("remittance_information")) else ""
        kind = kinds[i]
        if kind in ("none", "other"):
            kind = CODE_KINDS.get(item.get("bank_transaction_code"), "other")
        amount = float(item["amount"])
        currency = item["currency"]
        gel = round(amount * fx[currency], 2) if currency in fx else None
        when = pd.Timestamp(item["booking_date"]) if pd.notna(item["booking_date"]) else None
        own_move = item.get("debtor_iban") in own or item.get("creditor_iban") in own or kind in ("own", "cc_repay")
        reason = None
        if item["status"] != "booked":
            reason = "Pending / not booked"
        elif bool(item["is_card_mirror"]):
            reason = "Card mirror (duplicate view)"
        elif when is None or not start <= when <= end:
            reason = "Outside observation window"
        elif own_move:
            reason = "Own-account or card repayment movement"
        elif kind in ("loan_in", "quick_loan") or item.get("purpose_code") == "LOAN":
            reason = "Loan movement"
        elif gel is None:
            reason = "Currency has no configured conversion"
        is_service = (
            reason is None and amount > 0 and bool(SERVICE.search(description)) and not REFUND.search(description)
        )
        is_salary = reason is None and amount > 0 and kind == "salary"
        payer = next((item[k] for k in ("debtor_iban", "debtor_name") if pd.notna(item.get(k))), None)
        row = {
            "reference": next(
                (str(item[k]) for k in ("entry_reference", "transaction_id") if pd.notna(item.get(k))),
                f"statement-row-{i + 1}",
            ),
            "bank": item["bank"],
            "date": when.date().isoformat() if when is not None else None,
            "amount": amount,
            "currency": currency,
            "amount_gel": gel,
            "description": description,
            "counterparty": mask(payer),
            "classification": "Service payment"
            if is_service
            else "Salary credit"
            if is_salary
            else "Excluded activity"
            if reason
            else "Other inflow"
            if amount > 0
            else "Spending / outflow",
            "exclusion_reason": reason,
            "retained": reason is None,
        }
        rows.append(row)
        if is_service:
            service.append(row)
            bucket = monthly[row["date"][:7]]
            bucket["count"] += 1
            bucket["total_gel"] = round(bucket["total_gel"] + gel, 2)
        if is_salary:
            salaries.append(row)
    total = round(sum(r["amount_gel"] for r in service), 2)
    salary_total = round(sum(r["amount_gel"] for r in salaries), 2)
    snapshot = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "window": {"from": start.date().isoformat(), "to": end.date().isoformat(), "months": months},
        "service_payments": {
            "count": len(service),
            "total_gel": total,
            "monthly_gel": round(total / months, 2),
            "months_with_payments": len(monthly),
            "monthly": dict(sorted(monthly.items())),
        },
        "salary_baseline": {
            "status": "observed" if salaries else "abstains",
            "count": len(salaries),
            "monthly_gel": round(salary_total / months, 2) if salaries else None,
            "message": "Salary-credit-only rule; no inference from other income."
            if salaries
            else "No salary credits identified. This baseline cannot estimate this customer's income.",
        },
        "rules": "Booked external credits explicitly described as Payment for services / გადახდა მომსახურებაზე. "
        "Pending items, mirrors, own transfers, loans, refunds and unsupported currencies do not qualify. "
        "Cash deposits and generic P2P receipts are not classified as service income. Receipts are before expenses/tax; "
        "foreign currencies use the model window's mean FX rate. Partial calendar months are included.",
        "transactions": rows,
        "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest(),
    }
