"""Behavioural tests: determinism, ledger, spec validation, auth, paging, payments, fidelity."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from obmock.banks import BANKS
from obmock.config import FIXTURE_DIR, SPEC_DIR, WorldConfig
from obmock.dialects.registry import DIALECTS
from obmock.engine.gateway import Gateway, Request
from obmock.export import capture_customer
from obmock.server import create_app
from obmock.validate import check_dialects, check_ledger
from obmock.world import build_world


def req(gw, bank, method, path, query=None, body=None, **headers):
    h = {k.lower().replace("_", "-"): v for k, v in headers.items()}
    return gw.handle(bank, Request(method, path, query or {}, h, body, f"http://t/{bank}"))


# ----------------------------------------------------------------------------- world
def test_world_is_deterministic():
    cfg = WorldConfig(n_customers=25, min_customers_per_bank=2)
    a, b = build_world(cfg), build_world(cfg)
    assert a.stats() == b.stats()

    def digest(w):
        h = hashlib.sha256()
        for acc in sorted(w.accounts.values(), key=lambda x: x.id):
            h.update(acc.id.encode())
            for t in acc.txns:
                h.update(f"{t.booking_date}{t.amount}{t.kind}{t.ref}".encode())
        return h.hexdigest()

    assert digest(a) == digest(b)
    other = build_world(WorldConfig(n_customers=25, min_customers_per_bank=2, seed=1))
    assert digest(a) != digest(other)


def test_ledger_invariants(world):
    failed = [(c.name, c.failed, c.examples) for c in check_ledger(world) if c.failed]
    assert not failed, failed


def test_every_bank_has_customers_and_valid_ibans(world):
    for key, prof in BANKS.items():
        cids = world.customers_of(key)
        assert len(cids) >= world.cfg.min_customers_per_bank
        for cid in cids:
            for a in world.accounts_of(key, cid):
                if a.iban:
                    assert a.iban[4:6] == prof.iban_code and len(a.iban) == 22


def test_banks_registry():
    assert len(BANKS) == 12
    assert len({b.iban_code for b in BANKS.values()}) == 12
    assert all(len(b.bic) == 8 and b.dialect in DIALECTS for b in BANKS.values())
    for b in BANKS.values():
        if b.evidence in ("spec", "examples"):
            assert all((SPEC_DIR / f).exists() for f in b.spec_files), b.key


def test_hidden_truth_never_in_api_payloads(world, gw_off):
    banned = ("pd_pct", "stress", "df_", "probabilityOfDefault", "sim_outstanding", "loan_visible")
    for bank in ("tbc", "silk", "credo"):
        cid = world.customers_of(bank)[0]
        blob = json.dumps(capture_customer(gw_off, bank, cid, world.window_start.isoformat()))
        assert not [w for w in banned if w in blob]


# ----------------------------------------------------------------------------- schemas
def test_responses_validate_against_each_banks_own_spec(world):
    for rep in check_dialects(world, per_bank=2):
        if rep.skipped_reason:
            assert rep.bank == "paysera"
            continue
        assert rep.validated > 10, rep.bank
        assert not rep.violations, (rep.bank, rep.violations[:2])


# ----------------------------------------------------------------------------- auth & consent
def test_strict_auth_rejects_and_accepts(world):
    gw = Gateway(world, auth_mode="strict")
    base = dict(x_request_id="r1", psu_ip_address="203.0.113.7")
    assert req(gw, "tbc", "GET", "/0.8/v1/accounts", **base).status == 401
    s = gw.quick_session("tbc")
    good = {**base, "authorization": s["authorization"], "consent_id": s["consentId"]}
    assert req(gw, "tbc", "GET", "/0.8/v1/accounts", **good).status == 200
    bad = {**good, "consent_id": "00000000-0000-4000-8000-000000000000"}
    r = req(gw, "tbc", "GET", "/0.8/v1/accounts", **bad)
    assert (r.status, r.body["code"]) == (403, "CONSENT_UNKNOWN")
    # TBC requires PSU-IP-Address on data calls
    assert req(gw, "tbc", "GET", "/0.8/v1/accounts", authorization=s["authorization"],
               consent_id=s["consentId"], x_request_id="r1").status == 400
    # a token issued by another bank is refused
    other = gw.quick_session("bog")
    r = req(gw, "tbc", "GET", "/0.8/v1/accounts",
            **{**base, "authorization": other["authorization"], "consent_id": s["consentId"]})
    assert r.status == 401


def test_consent_frequency_limit_applies_to_unattended_access(world):
    gw = Gateway(world, auth_mode="strict")
    s = gw.quick_session("bog")
    gw.sessions.consents[s["consentId"]].freq = 2
    h = dict(authorization=s["authorization"], consent_id=s["consentId"], x_request_id="r")
    codes = [req(gw, "bog", "GET", "/0.8/v1/accounts", **h).status for _ in range(3)]
    assert codes == [200, 200, 429]
    attended = {**h, "psu_ip_address": "203.0.113.7"}  # attended access is not rate limited
    assert req(gw, "bog", "GET", "/0.8/v1/accounts", **attended).status == 200


def test_oauth_pkce_flow_end_to_end(world):
    gw = Gateway(world, auth_mode="strict")
    body = {"access": {"availableAccounts": "allAccounts"}, "recurringIndicator": True,
            "validUntil": (date.today() + timedelta(days=60)).isoformat(), "frequencyPerDay": 4,
            "combinedServiceIndicator": False}
    h = dict(x_request_id="r", psu_ip_address="203.0.113.7", tpp_redirect_uri="https://t/cb")
    created = req(gw, "tbc", "POST", "/0.8/v1/consents", body=body, **h)
    assert created.status == 201 and "startAuthorisation" in created.body["_links"]
    cons = created.body["consentId"]
    assert req(gw, "tbc", "POST", f"/0.8/v1/consents/{cons}/authorisations", body={},
               **h).status == 201
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    q = {"response_type": "code", "client_id": "app", "scope": f"AIS:{cons}",
         "redirect_uri": "https://t/cb", "state": "xyz", "code_challenge": challenge}

    def new_code():
        loc = req(gw, "tbc", "GET", "/psd2/openbanking/oauth/authorize", q).headers["Location"]
        return parse_qs(urlsplit(loc).query)["code"][0]

    wrong = req(gw, "tbc", "POST", "/openbanking/oauth/token", body={
        "grant_type": "authorization_code", "code": new_code(), "code_verifier": "x" * 64})
    assert wrong.status == 400  # PKCE mismatch
    tok = req(gw, "tbc", "POST", "/openbanking/oauth/token", body={
        "grant_type": "authorization_code", "code": new_code(), "code_verifier": verifier})
    assert tok.status == 200 and tok.body["token_type"] == "Bearer"
    status = req(gw, "tbc", "GET", f"/0.8/v1/consents/{cons}/status", x_request_id="r")
    assert status.body == {"consentStatus": "valid"}
    auth = dict(authorization=f"Bearer {tok.body['access_token']}", consent_id=cons, **h)
    assert req(gw, "tbc", "GET", "/0.8/v1/accounts", **auth).status == 200
    # the PSU can also refuse
    c2 = req(gw, "tbc", "POST", "/0.8/v1/consents", body=body, **h).body["consentId"]
    r = req(gw, "tbc", "GET", "/psd2/openbanking/oauth/authorize",
            {**q, "scope": f"AIS:{c2}", "mock_action": "reject"})
    assert "error=access_denied" in r.headers["Location"]
    status = req(gw, "tbc", "GET", f"/0.8/v1/consents/{c2}/status", x_request_id="r")
    assert status.body == {"consentStatus": "rejected"}


# ----------------------------------------------------------------------------- paging
def _all_pages(gw, bank, cid, first_path, query):
    out, path, q = [], first_path, query
    for _ in range(200):
        r = req(gw, bank, "GET", path, q, x_mock_customer=cid, x_request_id="p")
        assert r.status == 200, r.body
        rep = r.body["transactions"]
        out += rep.get("booked", []) + rep.get("pending", [])
        nxt = (rep.get("_links") or r.body.get("_links") or {}).get("next")
        if not nxt:
            return out
        parts = urlsplit(nxt["href"])
        path, q = "/" + parts.path.lstrip("/"), {k: v[0] for k, v in parse_qs(parts.query).items()}
    raise AssertionError("paging did not terminate")


def test_three_paging_styles_return_every_entry_once(world, gw_off, monkeypatch):
    for bank in ("tbc", "terabank", "paysera"):  # entryReferenceFrom / state token / offset
        d = gw_off.dialects[bank]
        cid, acct = max(((c, a) for c in world.customers_of(bank)
                         for a in world.accounts_of(bank, c, ("current",))),
                        key=lambda x: len(x[1].txns))
        # small banks have few entries in a test world: shrink the page so every style pages >= 3 times
        monkeypatch.setattr(d, "page_size", min(d.page_size, max(2, len(acct.txns) // 3)))
        assert len(acct.txns) > 2 * d.page_size, bank
        listed = req(gw_off, bank, "GET", d.accounts_path, x_mock_customer=cid).body["accounts"]
        rid = next(a["resourceId"] for a in listed
                   if a.get("iban") == acct.iban and a.get("currency") == "GEL")
        got = _all_pages(gw_off, bank, cid, f"{d.accounts_path}/{rid}/transactions",
                         {"bookingStatus": "both"})
        keys = [t.get("entryReference") or t["transactionId"] for t in got]
        assert len(got) == len(acct.txns) and len(set(keys)) == len(keys), bank


# ----------------------------------------------------------------------------- payments
def test_payment_moves_money_and_shows_up_in_the_ledger(fresh_world):
    gw = Gateway(fresh_world, auth_mode="off")
    bank = "tbc"
    cid, acct = next((c, a) for c in fresh_world.customers_of(bank)
                     for a in fresh_world.accounts_of(bank, c, ("current",))
                     if fresh_world.balances(a)["interimAvailable"] > 50_000)
    other = next(a for b in BANKS if b != bank
                 for a in fresh_world.accounts_of(b, fresh_world.customers_of(b)[0], ("current",)))
    before = fresh_world.balances(acct)["interimBooked"]

    def pay(amount):
        body = {"debtorAccount": {"iban": acct.iban}, "creditorAccount": {"iban": other.iban},
                "instructedAmount": {"currency": "GEL", "amount": amount}, "creditorName": "X"}
        r = req(gw, bank, "POST", "/v1/payments/domestic", body=body, x_request_id="p")
        assert r.status == 201 and r.body["transactionStatus"] == "RCVD"
        pid = r.body["paymentId"]
        gw.complete_sca(bank, "payment", pid, cid)
        return req(gw, bank, "GET", f"/v1/payments/domestic/{pid}/status", x_request_id="p").body

    assert pay("10.00")["transactionStatus"] in ("ACSC", "ACCC")
    assert fresh_world.balances(acct)["interimBooked"] == before - 1000
    made = [t for t in acct.txns if t.kind == "pis_payment"]
    assert len(made) == 1 and made[0].amount == -1000
    assert made[0].ts.date() == made[0].booking_date == fresh_world.as_of
    huge = f"{fresh_world.balances(acct)['interimAvailable'] / 100 + 1000:.2f}"
    assert pay(huge)["transactionStatus"] == "RJCT"  # insufficient funds


# ----------------------------------------------------------------------------- fidelity
def test_tbc_responses_cover_every_field_of_its_published_examples(world, gw_off):
    ex = json.loads((FIXTURE_DIR / "tbc" / "tbc_example_outputs.json").read_text())["records"]
    by_path = {(r["method"], r["path"]): r["body"] for r in ex if r.get("body")}
    cid = max(world.customers_of("tbc"), key=lambda c: len(world.accounts_of("tbc", c, ("fx",)))
              * 100_000 + sum(len(a.txns) for a in world.accounts_of("tbc", c)))
    h = dict(x_mock_customer=cid, x_request_id="g")
    accts = req(gw_off, "tbc", "GET", "/0.8/v1/accounts", {"withBalance": "true"},
                **h).body["accounts"]
    want = {k for a in by_path[("GET", "/v1/accounts")]["accounts"] for k in a}
    have = {k for a in accts for k in a}
    assert want <= have, want - have
    first = next(a for a in accts if a["currency"] != "XXX")
    txns = _all_pages(gw_off, "tbc", cid, f"/0.8/v1/accounts/{first['resourceId']}/transactions",
                      {"bookingStatus": "booked"})
    want = {k for t in by_path[("GET", "/v1/accounts/{account-id}/transactions")][
        "transactions"]["booked"] for k in t}
    have = {k for t in txns for k in t}
    assert want <= have, want - have
    bal = req(gw_off, "tbc", "GET", f"/0.8/v1/accounts/{first['resourceId']}/balances", **h).body
    assert {"interimAvailable", "closingBooked"} <= {b["balanceType"] for b in bal["balances"]}
    cards = req(gw_off, "tbc", "GET", "/0.8/v1/card-accounts", **h).body["cardAccounts"]
    want = {k for c in by_path[("GET", "/v1/card-accounts")]["cardAccounts"] for k in c}
    assert want <= {k for c in cards for k in c}


def test_tbc_publishes_the_loan_balances_path_exactly_and_hides_delinquency(world, gw_off):
    templates = [e[1] for e in gw_off.dialects["tbc"].route_table()]
    assert "/v2/loans{resourceId}/balances" in templates
    cid = next(c for c in world.customers_of("tbc") if world.loans_of("tbc", c))
    loans = req(gw_off, "tbc", "GET", "/v2/loans", x_mock_customer=cid).body["accounts"]
    def keys(o):  # field names only: a product *name* may legitimately say "instalment"
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    blob = " ".join(keys(loans)).lower()
    assert not any(w in blob for w in ("instal", "overdue", "arrear", "penalt", "delinq"))
    lid = loans[0]["resourceId"]
    r = req(gw_off, "tbc", "GET", f"/v2/loans{lid}/balances", x_mock_customer=cid)
    assert r.status == 200 and r.body["balances"][0]["balanceType"] == "ClosingBooked"


def test_altasoft_siblings_differ_only_where_their_specs_differ(world, gw_off):
    def first_account(bank):
        cid = world.customers_of(bank)[0]
        return req(gw_off, bank, "GET", gw_off.dialects[bank].accounts_path,
                   x_mock_customer=cid).body["accounts"][0]

    assert "owner" in first_account("terabank") and "ownerName" not in first_account("terabank")
    assert "ownerName" in first_account("liberty") and "owner" not in first_account("liberty")
    busiest = max(world.customers_of("silk"),
                  key=lambda c: sum(len(a.txns) for a in world.accounts_of("silk", c)))
    cap = capture_customer(gw_off, "silk", busiest, world.window_start.isoformat())
    assert '"creditor"' in json.dumps(cap) or '"debtor"' in json.dumps(cap)  # nested v2 parties


# ----------------------------------------------------------------------------- HTTP layer
def test_http_server_session_and_data(world):
    client = TestClient(create_app(world), follow_redirects=False)
    idx = client.get("/").json()
    assert [b["bank"] for b in idx["banks"]] == list(BANKS)
    s = client.post("/_mock/session/silk").json()
    h = {"Authorization": s["authorization"], **s["consentHeader"], "X-Request-ID": "x",
         "PSU-IP-Address": "203.0.113.7"}
    r = client.get("/silk/v2/accounts", headers=h)
    assert r.status_code == 200 and r.json()["accounts"]
    assert client.get("/silk/v2/accounts").status_code == 400  # X-Request-ID missing
    assert client.get("/silk/v2/accounts", headers={"X-Request-ID": "x"}).status_code == 401
    assert client.get("/nosuchbank/x").status_code == 404
    assert client.post("/tbc/0.8/v1/consents", content=b"{not json").status_code == 400
