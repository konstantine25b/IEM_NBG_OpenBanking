"""Fidelity of what BoG and TBC return: only fields their schemas define, standard identifiers and
codes, Georgia-sized market shares. (Complements test_mock.py, which checks behaviour.)"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
from obmock.banks import BANKS
from obmock.config import WorldConfig
from obmock.engine.gateway import Gateway, Request
from obmock.export import capture_customer
from obmock.world.simulate import _plan_banks

PRIORITY = ("tbc", "bog")
TEXT_KEYS = ("additionalInformation", "remittanceInformationUnstructured", "transactionDetails")


def _walk(o, key=None):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _walk(v, k)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key)
    else:
        yield key, o


def _captures(world, gw, bank, n=6):
    cids = sorted(world.customers_of(bank),
                  key=lambda c: -sum(len(a.txns) for a in world.accounts_of(bank, c)))[:n]
    return [capture_customer(gw, bank, c, world.window_start.isoformat()) for c in cids]


def _bodies(cap):
    return [c["body"] for c in cap["calls"] if c["status"] == 200]


def test_no_card_details_in_account_text(world, gw_off):
    """MCC, city and PAN are not defined in TBC's or BoG's account-transaction schema."""
    for bank in PRIORITY:
        for cap in _captures(world, gw_off, bank):
            for body in _bodies(cap):
                for key, val in _walk(body):
                    if key in TEXT_KEYS and isinstance(val, str):
                        assert "MCC" not in val and not re.search(r"\d{6}\*{4,6}\d{4}", val), \
                            (bank, key, val)


def test_pending_entries_have_value_date_only(world, gw_off):
    seen = 0
    for bank in PRIORITY:
        for cap in _captures(world, gw_off, bank):
            for body in _bodies(cap):
                rep = body.get("transactions") or body.get("cardTransactions") or {}
                for t in rep.get("pending", []):
                    seen += 1
                    # account entries carry valueDate, card entries transactionDate
                    assert "bookingDate" not in t and (t.get("valueDate") or t["transactionDate"]), \
                        (bank, t)
                for t in rep.get("booked", []):
                    assert t["bookingDate"], (bank, t)
    assert seen > 0, "test world should contain pending card entries"


def test_currency_exchange_unit_is_the_foreign_currency(world, gw_off):
    seen = 0
    for bank in PRIORITY:
        for cap in _captures(world, gw_off, bank, n=40):
            for body in _bodies(cap):
                rep = body.get("transactions") or {}
                for t in rep.get("booked", []) + rep.get("pending", []):
                    for fx in t.get("currencyExchange", []):
                        seen += 1
                        foreign = fx["sourceCurrency"] if fx["sourceCurrency"] != "GEL" \
                            else fx["targetCurrency"]
                        assert fx["unitCurrency"] == foreign != "GEL", fx
                        assert 1.5 < float(fx["exchangeRate"]) < 6.0, fx  # GEL per 1 USD/EUR/GBP
    assert seen > 0


def test_transaction_ids_are_unique_and_share_the_entry_reference(world, gw_off):
    for bank in PRIORITY:
        d = gw_off.dialects[bank]
        ids, refs = [], []
        for cid in world.customers_of(bank):
            for a in world.accounts_of(bank, cid):
                for t in a.txns:
                    ids.append(d.txid(a, t))
                    refs.append(t.ref)
                    assert d.txid(a, t)[2:] == t.ref and len(t.ref) == 11
        assert len(set(ids)) == len(ids) and len(set(refs)) == len(refs), bank


def test_fx_sub_account_shares_the_iban_and_a_payment_debits_its_own_currency(fresh_world):
    gw = Gateway(fresh_world, auth_mode="off")
    for bank in PRIORITY:
        pair = next(((c, a) for c in fresh_world.customers_of(bank)
                     for a in fresh_world.accounts_of(bank, c, ("fx",))
                     if fresh_world.balances(a)["interimAvailable"] > 20_000), None)
        if pair is None:
            continue
        cid, fx = pair
        d = gw.dialects[bank]
        cur = fresh_world.accounts_of(bank, cid, ("current",))[0]
        assert d.iban_of(fx) == cur.iban != fx.iban  # one multi-currency IBAN on the wire
        other = next(a for b in BANKS if b != bank
                     for a in fresh_world.accounts_of(b, fresh_world.customers_of(b)[0],
                                                      ("current",)))
        gel_before = fresh_world.balances(cur)["interimBooked"]
        fx_before = fresh_world.balances(fx)["interimBooked"]
        body = {"debtorAccount": {"iban": d.iban_of(fx), "currency": fx.currency},
                "creditorAccount": {"iban": other.iban},
                "instructedAmount": {"currency": fx.currency, "amount": "5.00"},
                "creditorName": "X"}
        r = gw.handle(bank, Request("POST", "/v1/payments/domestic", {},
                                    {"x-mock-customer": cid, "x-request-id": "p"}, body,
                                    f"http://t/{bank}"))
        assert r.status == 201, r.body
        gw.complete_sca(bank, "payment", r.body["paymentId"], cid)
        assert fresh_world.balances(fx)["interimBooked"] == fx_before - 500
        assert fresh_world.balances(cur)["interimBooked"] == gel_before
        return
    raise AssertionError("no TBC/BoG customer with a funded FX account in the test world")


def test_primary_bank_shares_follow_the_sourced_weights():
    n = 4000
    plans = _plan_banks(WorldConfig(n_customers=n), np.random.default_rng(1))
    primary = Counter(p[0] for p in plans)
    for key, bank in BANKS.items():
        assert abs(primary[key] - bank.weight * n) <= 1.0, (key, primary[key], bank.weight * n)
    assert (primary["bog"] + primary["tbc"]) / n > 0.77
