"""ISO 20022 labels for the simulator's transaction kinds.

`bankTransactionCode` is the Domain-Family-SubFamily triple (ISO 20022 External Bank Transaction
Codes, written as in the BoG spec: `PMNT-CCRD-POSD`); `purposeCode` is an ExternalPurpose1Code.
The mapping from the simulator's own `Txn.kind` to these codes is the mock's choice: neither TBC nor
BoG publishes the codes it really uses. tests/test_iso20022.py checks every triple against the
official table vendored in specs/iso20022/.

Rules (ISO 20022 / Berlin Group practice):
  * a purposeCode is set only where an originator would set one (salary, rent, utilities, taxes,
    loan repayment...), never on card, cash, fee or interest entries;
  * a mandateId belongs to direct debits only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Iso:
    bktx: str  # Domain-Family-SubFamily
    purpose: str | None = None
    direct_debit: bool = False


# kind -> (code when the entry debits the account, code when it credits it)
_DEBIT_CARD = Iso("PMNT-CCRD-POSD")
_CREDIT_CARD = Iso("PMNT-CCRD-POSC")
_ATM = Iso("PMNT-CCRD-CWDL")
_OUT = Iso("PMNT-ICDT-DMCT")
_IN = Iso("PMNT-RCDT-DMCT")
_BOOK_OUT = Iso("PMNT-ICDT-BOOK")
_BOOK_IN = Iso("PMNT-RCDT-BOOK")
_FEE = Iso("ACMT-MDOP-CHRG")

_CARD_KINDS = ("grocery", "kiosk", "restaurant", "fastfood", "pharmacy", "transit", "fuel", "clothing",
               "health", "electronics", "rideshare", "delivery", "entertainment", "ecommerce",
               "gambling", "travel")

_BY_KIND: dict[str, tuple[Iso | None, Iso | None]] = {
    **{k: (_DEBIT_CARD, None) for k in _CARD_KINDS},
    "atm_cash": (_ATM, None),
    "cash_deposit": (None, Iso("PMNT-CCRD-CDPT")),  # ATM deposit (counter deposit is CNTR-CDPT)
    "utility": (Iso("PMNT-ICDT-DMCT", "UBIL"), None),
    "rent": (Iso("PMNT-ICDT-DMCT", "RENT"), None),
    "tax": (Iso("PMNT-ICDT-DMCT", "TAXS"), None),
    "p2p_out": (_OUT, None),
    "p2p_in": (None, _IN),
    "salary": (None, Iso("PMNT-RCDT-SALA", "SALA")),
    "business_income": (None, _IN),
    "remittance": (None, Iso("PMNT-RCDT-XBCT")),
    # the account owner's view of a mandate debit is a *Received* Direct Debit (RDDT)
    "loan_payment": (Iso("PMNT-RDDT-PMDD", "LOAR", direct_debit=True), None),
    "loan_disbursement": (None, Iso("PMNT-RCDT-BOOK", "LOAN")),
    "disbursement_out": (_OUT, None),  # the borrower pays a seller out of the loan proceeds
    "microloan_out": (Iso("PMNT-ICDT-DMCT", "LOAR"), None),
    "microloan_in": (None, Iso("PMNT-RCDT-DMCT", "LOAN")),
    "cc_repay_out": (_BOOK_OUT, None),
    "cc_repay_in": (None, _BOOK_IN),
    "own_transfer_out": (_BOOK_OUT, None),
    "own_transfer_in": (None, _BOOK_IN),
    "fee": (_FEE, None),
    "overdraft_fee": (_FEE, None),
    "loan_penalty": (_FEE, None),
    "interest": (None, Iso("ACMT-MCOP-INTR")),
    "cc_interest": (Iso("ACMT-MDOP-INTR"), None),
    "fx_buy": (None, _BOOK_IN),
    "fx_sell": (_BOOK_OUT, None),
    "fx_buy_gel": (None, _BOOK_IN),
    "fx_sell_gel": (_BOOK_OUT, None),
    # current-account side: a book transfer; savings-account side: the fixed-term-deposit contract
    "deposit_open": (_BOOK_OUT, None),
    "deposit_open_in": (None, Iso("LDAS-FTDP-DPST")),
    "deposit_close": (Iso("LDAS-FTDP-RPMT"), None),
    "deposit_close_in": (None, _BOOK_IN),
}


_NOT_BOOK = {"PMNT-ICDT-BOOK": "PMNT-ICDT-DMCT", "PMNT-RCDT-BOOK": "PMNT-RCDT-DMCT"}


def classify(kind: str, amount: int, account_kind: str = "current",
             internal: bool = True) -> Iso | None:
    """ISO 20022 code for a ledger entry, or None when the kind is unknown (never guess).

    `internal` says whether the other side of a transfer is at the same bank. An internal book
    transfer (BOOK) only exists inside one bank; across banks the same movement is a domestic
    credit transfer (DMCT).
    """
    debit, credit = _BY_KIND.get(kind, (None, None))
    iso = debit if amount < 0 else credit
    if iso is None:
        return None
    if iso is _DEBIT_CARD and account_kind == "card_credit":
        return _CREDIT_CARD
    if not internal and iso.bktx in _NOT_BOOK:
        return Iso(_NOT_BOOK[iso.bktx], iso.purpose, iso.direct_debit)
    return iso


def all_triples() -> set[str]:
    """Every bankTransactionCode this module can emit (used by the vendored-table test)."""
    out = {i.bktx for pair in _BY_KIND.values() for i in pair if i}
    out.add(_CREDIT_CARD.bktx)
    out.update(_NOT_BOOK.values())
    return out


def all_purposes() -> set[str]:
    return {i.purpose for pair in _BY_KIND.values() for i in pair if i and i.purpose}
