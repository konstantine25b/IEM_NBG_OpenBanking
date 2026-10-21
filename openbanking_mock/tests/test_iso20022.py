"""Every ISO 20022 code the mock can emit must exist in the official tables vendored in
specs/iso20022/ (a triple is validated as a whole: a valid domain, family and sub-family can still
form an invalid combination)."""
from __future__ import annotations

import csv

from obmock.config import SPEC_DIR
from obmock.iso20022 import all_purposes, all_triples, classify
from obmock.validate import _load_doc

ISO = SPEC_DIR / "iso20022"


def _rows(name):
    with (ISO / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _find_enum(node, key):
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict) and "enum" in node[key]:
            return set(node[key]["enum"])
        for v in node.values():
            found = _find_enum(v, key)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_enum(v, key)
            if found:
                return found
    return None


def test_every_emitted_triple_is_an_official_combination():
    valid = {f"{r['domain']}-{r['family']}-{r['subfamily']}" for r in _rows("bank_transaction_codes.csv")}
    assert len(valid) > 1000
    assert all_triples() <= valid, sorted(all_triples() - valid)


def test_every_emitted_purpose_code_is_official_and_in_bogs_enum():
    official = {r["code"] for r in _rows("external_purpose_codes.csv")}
    assert all_purposes() <= official, sorted(all_purposes() - official)
    bog = _find_enum(_load_doc(str(SPEC_DIR / "bog" / "plugin-redoc-0.yaml"), True), "purposeCode")
    assert bog and len(bog) == 286
    assert all_purposes() <= bog, sorted(all_purposes() - bog)


def test_purpose_codes_and_mandates_only_where_an_originator_would_set_them():
    for kind in ("grocery", "atm_cash", "fee", "interest", "cc_interest", "overdraft_fee"):
        for sign in (-1, 1):
            iso = classify(kind, sign * 100)
            assert iso is None or (iso.purpose is None and not iso.direct_debit), (kind, sign)
    assert classify("salary", 100).purpose == "SALA"
    assert classify("loan_payment", -100).direct_debit
    assert not classify("rent", -100).direct_debit


def test_book_transfers_exist_only_inside_one_bank():
    assert classify("own_transfer_out", -100, internal=True).bktx == "PMNT-ICDT-BOOK"
    assert classify("own_transfer_out", -100, internal=False).bktx == "PMNT-ICDT-DMCT"
    # a loan paid out by another bank arrives as a domestic credit transfer with purpose LOAN
    iso = classify("loan_disbursement", 100, internal=False)
    assert (iso.bktx, iso.purpose) == ("PMNT-RCDT-DMCT", "LOAN")
    # paying a seller from loan proceeds is an ordinary transfer, not a loan-account drawdown
    assert classify("disbursement_out", -100).bktx == "PMNT-ICDT-DMCT"


def test_credit_card_purchase_uses_the_credit_card_sub_family():
    assert classify("grocery", -100, "current").bktx == "PMNT-CCRD-POSD"
    assert classify("grocery", -100, "card_credit").bktx == "PMNT-CCRD-POSC"
