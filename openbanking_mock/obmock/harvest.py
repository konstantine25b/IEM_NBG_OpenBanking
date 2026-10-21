"""Flatten what a TPP downloads from a bank's API into typed tables.

The dataset is built ONLY from API response bodies: every column below is read from one or more JSON
paths of the bank's own responses (`Col.paths`), so a value the API does not return cannot appear.
Anything else the simulator knows (MCC, merchant city, spend category, loan arrears, demographics...)
lives in the hidden truth tables instead.

Three tables cover the Berlin Group resource model:
  accounts      one row per account resource a TPP can list: payment accounts, card accounts, loans,
                savings (`source_endpoint` says which endpoint listed it)
  balances      one row per balance of such a resource (long format)
  transactions  one row per booked/pending entry of an account or card account
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from obmock.engine.gateway import Router

# --------------------------------------------------------------------------- column definitions
AMOUNT = pa.decimal128(18, 3)  # Berlin Group: -?[0-9]{1,14}(\.[0-9]{1,3})?


@dataclass(frozen=True)
class Col:
    name: str
    typ: pa.DataType
    paths: tuple[str, ...] = ()  # JSON paths tried in order; () = derived by the harvester


S, D, B, F = pa.string(), pa.date32(), pa.bool_(), pa.float64()


def _c(name: str, typ: pa.DataType, *paths: str) -> Col:
    return Col(name, typ, paths)


KEYS = (_c("bank", S), _c("psu_id", S), _c("source_endpoint", S))

ACCOUNT_COLS = KEYS + (
    _c("resource_id", S, "resourceId"),
    _c("iban", S, "iban"), _c("bban", S, "bban"), _c("msisdn", S, "msisdn"),
    _c("currency", S, "currency"),
    _c("owner_name", S, "ownerName", "owner"),
    _c("owner_identification", S, "ownerIdentification.privateId.others.0.identification"),
    _c("name", S, "name"), _c("display_name", S, "displayName"), _c("product", S, "product"),
    _c("cash_account_type", S, "cashAccountType"), _c("status", S, "status"),
    _c("bic", S, "bic"), _c("usage", S, "usage"), _c("details", S, "details"),
    _c("masked_pan", S, "maskedPan", "maskedPAN"),
    _c("credit_limit", AMOUNT, "creditLimit", "creditLimit.amount"),
    _c("contract_start_date", D, "contractStartDate", "relatedDates.contractStartDate"),
    _c("contract_end_date", D, "relatedDates.contractEndDate"),
    _c("period_months", pa.int32(), "period"),
    _c("interest_rate_pct", F, "interest.rate.percentage"),
)

BALANCE_COLS = KEYS + (
    _c("resource_id", S),  # derived: the account resource the balance belongs to
    _c("origin", S),  # derived: embedded | balances_endpoint
    _c("balance_type", S, "balanceType"),
    _c("amount", AMOUNT, "balanceAmount.amount"),
    _c("currency", S, "balanceAmount.currency"),
    _c("credit_limit_included", B, "creditLimitIncluded"),
    _c("reference_date", D, "referenceDate"),
    _c("last_change_datetime", S, "lastChangeDateTime"),
    _c("balance_sub_type", S, "balanceSubTypeProprietary"),
)

TXN_COLS = KEYS + (
    _c("account_resource_id", S),  # derived: the resource whose transaction list returned the entry
    _c("status", S),  # derived: booked | pending (the report section)
    _c("transaction_id", S, "transactionId", "cardTransactionId"),
    _c("entry_reference", S, "entryReference"),
    _c("end_to_end_id", S, "endToEndId", "references.endToEndId"),
    _c("mandate_id", S, "mandateId"),
    _c("booking_date", D, "bookingDate"),
    _c("value_date", D, "valueDate", "transactionDate"),
    _c("amount", AMOUNT, "transactionAmount.amount"),
    _c("currency", S, "transactionAmount.currency"),
    _c("creditor_name", S, "creditorName", "creditor.name"),
    _c("creditor_iban", S, "creditorAccount.iban"),
    _c("creditor_agent", S, "creditorAgent", "creditorAgent.financialInstitutionId.bicfi"),
    _c("creditor_identification", S, "creditorIdentification.privateId.others.0.identification"),
    _c("debtor_name", S, "debtorName", "debtor.name"),
    _c("debtor_iban", S, "debtorAccount.iban"),
    _c("debtor_agent", S, "debtorAgent", "debtorAgent.financialInstitutionId.bicfi"),
    _c("debtor_identification", S, "debtorIdentification.privateId.others.0.identification"),
    _c("remittance_information", S, "remittanceInformationUnstructured"),
    _c("additional_information", S, "additionalInformation"),
    _c("purpose_code", S, "purposeCode"),
    _c("bank_transaction_code", S, "bankTransactionCode"),
    _c("proprietary_code", S, "proprietaryBankTransactionCode", "bankTransactionCodeProprietary"),
    _c("balance_after_amount", AMOUNT, "balanceAfterTransaction.balanceAmount.amount"),
    _c("fx_source_currency", S, "currencyExchange.0.sourceCurrency"),
    _c("fx_target_currency", S, "currencyExchange.0.targetCurrency"),
    _c("fx_unit_currency", S, "currencyExchange.0.unitCurrency"),
    _c("fx_rate", F, "currencyExchange.0.exchangeRate"),
    # card-account entries only
    _c("masked_pan", S, "maskedPan", "maskedPAN"),
    _c("terminal_id", S, "terminalId"),
    _c("card_acceptor_id", S, "cardAcceptorId"),
    _c("transaction_details", S, "transactionDetails"),
    _c("merchant_category_code", S, "merchantCategoryCode"),
    _c("acceptor_datetime", S, "acceptorTransactionDateTime"),
    # derived
    _c("entry_key", S),  # bank:transaction_id; identical for an entry listed under two endpoints
    _c("is_card_mirror", B),  # card-accounts entry that is also listed under /accounts
)

COLUMNS = {"accounts": ACCOUNT_COLS, "balances": BALANCE_COLS, "transactions": TXN_COLS}
SCHEMAS = {t: pa.schema([pa.field(c.name, c.typ) for c in cols]) for t, cols in COLUMNS.items()}

# operation -> (source_endpoint, what the response holds)
OPS: dict[str, tuple[str, str]] = {
    "accounts_list": ("accounts", "list"), "account_balances": ("accounts", "balances"),
    "account_transactions": ("accounts", "tx"),
    "cards_list": ("card-accounts", "list"), "card_balances": ("card-accounts", "balances"),
    "card_transactions": ("card-accounts", "tx"),
    "cards2_list": ("card-accounts", "list"), "cards2_balances": ("card-accounts", "balances"),
    "cards2_transactions": ("card-accounts", "tx"),
    "loans_list": ("loans", "list"), "loan_balances": ("loans", "balances"),
    "loan_balances_alias": ("loans", "balances"), "loan_transactions": ("loans", "tx"),
    "savings_list": ("savings", "list"), "saving_balances": ("savings", "balances"),
    "saving_transactions": ("savings", "tx"),
}


def lineage() -> dict[str, dict[str, Any]]:
    """column -> JSON paths it is read from (or 'derived') for each table."""
    return {t: {c.name: list(c.paths) if c.paths else "derived" for c in cols}
            for t, cols in COLUMNS.items()}


# --------------------------------------------------------------------------- value access
def _get(obj: Any, path: str) -> Any:
    for seg in path.split("."):
        if isinstance(obj, list):
            if not seg.isdigit() or int(seg) >= len(obj):
                return None
            obj = obj[int(seg)]
        elif isinstance(obj, dict):
            obj = obj.get(seg)
        else:
            return None
        if obj is None:
            return None
    return obj


def _convert(v: Any, typ: pa.DataType) -> Any:
    if v is None:
        return None
    if pa.types.is_string(typ):
        if isinstance(v, list):
            return " | ".join(str(x) for x in v)
        return v if isinstance(v, str) else json.dumps(v) if isinstance(v, dict) else str(v)
    if pa.types.is_decimal(typ):
        try:
            if isinstance(v, dict):  # {"currency":..,"amount":..}
                v = v.get("amount")
            return Decimal(str(v)).quantize(Decimal("0.001"))
        except (InvalidOperation, ValueError, TypeError):
            return None
    if pa.types.is_date(typ):
        return date.fromisoformat(str(v)[:10])
    if pa.types.is_floating(typ):
        return float(v)
    if pa.types.is_integer(typ):
        return int(v)
    if pa.types.is_boolean(typ):
        return bool(v)
    return v


def _row(item: dict, cols: Iterable[Col], base: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for c in cols:
        if c.paths:
            val = next((v for v in (_get(item, p) for p in c.paths) if v is not None), None)
            out[c.name] = _convert(val, c.typ)
    return out


def _first_list(body: Any) -> list[dict]:
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []


def _report(body: Any) -> dict | None:
    """The booked/pending report object (its key differs per dialect: transactions, cardTransactions...)."""
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, dict) and ("booked" in v or "pending" in v):
                return v
    return None


# --------------------------------------------------------------------------- harvesting
def harvest(capture: dict[str, Any], router: Router, normalize: Any) -> dict[str, list[dict]]:
    """Rows of one (bank, customer) capture. `router`/`normalize` come from the bank's dialect."""
    bank, psu = capture["bank"], capture["customer"]
    out: dict[str, list[dict]] = {"accounts": [], "balances": [], "transactions": []}
    aggregate_ids: set[str] = set()
    seen_tx: set[tuple[str, str]] = set()
    tx_of_accounts: set[str] = set()
    for call in capture["calls"]:
        if call["status"] != 200 or call["method"] != "GET":
            continue
        try:
            route, params = router.match("GET", normalize(call["path"]))
        except Exception:  # noqa: BLE001  (a link the dialect does not route is simply not data)
            continue
        info = OPS.get(route.op)
        if info is None:
            continue
        source, what = info
        base = {"bank": bank, "psu_id": psu, "source_endpoint": source}
        body = call["body"]
        if what == "list":
            for it in _first_list(body):
                acc = _row(it, ACCOUNT_COLS, base)
                out["accounts"].append(acc)
                if acc.get("currency") == "XXX":  # multi-currency aggregate: an index over children
                    aggregate_ids.add(acc["resource_id"])
                for b in it.get("balances") or []:
                    out["balances"].append(_row(b, BALANCE_COLS, {
                        **base, "resource_id": acc["resource_id"], "origin": "embedded"}))
            continue
        rid = next(iter(params.values()), None)
        if rid is None or rid in aggregate_ids:
            continue  # the aggregate's balances/entries repeat its currency children
        if what == "balances":
            for b in (body or {}).get("balances") or []:
                out["balances"].append(_row(b, BALANCE_COLS, {
                    **base, "resource_id": rid, "origin": "balances_endpoint"}))
        elif what == "tx":
            rep = _report(body) or {}
            for status in ("booked", "pending"):
                for it in rep.get(status) or []:
                    row = _row(it, TXN_COLS, {**base, "account_resource_id": rid, "status": status})
                    tid = row.get("transaction_id") or row.get("entry_reference")
                    if (source, str(tid)) in seen_tx:
                        continue  # same page fetched twice
                    seen_tx.add((source, str(tid)))
                    row["entry_key"] = f"{bank}:{tid}"
                    if source == "accounts":
                        tx_of_accounts.add(row["entry_key"])
                    out["transactions"].append(row)
    for row in out["transactions"]:
        row["is_card_mirror"] = row["source_endpoint"] == "card-accounts" \
            and row["entry_key"] in tx_of_accounts
    return out


def to_table(name: str, rows: list[dict]) -> pa.Table:
    schema = SCHEMAS[name]
    return pa.Table.from_pylist(
        [{f.name: r.get(f.name) for f in schema} for r in rows], schema=schema)
