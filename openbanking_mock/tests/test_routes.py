"""Every route each bank documents must answer with a success status for real, resolved ids."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from obmock.banks import BANKS
from obmock.config import WorldConfig
from obmock.engine.gateway import Authorisation, Gateway, Request, new_id
from obmock.world import build_world

OK = {200, 201, 204, 302}
# Operations a bank documents as refused, or that legitimately refuse in the tested state.
EXPECTED_REFUSAL = {
    "paysera": {"consent_auth_start": {405}, "consent_auth_update": {405}, "pis_auth_start": {405},
                "pis_auth_update": {405}, "pis_cancel": {405}, "pis_cancel_auth_start": {405},
                "pis_cancel_auth_list": {405}, "pis_cancel_auth_get": {405},
                "pis_cancel_auth_update": {405}},
    # an executed initiation cannot be cancelled (405); a rejected one can (204)
    "*": {"pis2_init_cancel": {204, 405}},
}


class NoData(Exception):
    pass


@pytest.fixture(scope="module")
def mworld():
    return build_world(WorldConfig(n_customers=90))


class Exerciser:
    def __init__(self, gw: Gateway, world, bank: str, cid: str | None = None):
        self.gw, self.w, self.bank = gw, world, bank
        self.d = gw.dialects[bank]
        self.cache: dict = {}
        self.method = "GET"
        self.cid = cid or self._pick_customer()

    def _pick_customer(self) -> str:
        def score(cid: str) -> int:
            accts = self.w.accounts_of(self.bank, cid)
            s = sum(len(a.txns) for a in accts if a.kind == "current")
            s += 100_000 * sum(a.kind == "loan" for a in accts)
            s += 50_000 * sum(a.kind.startswith("savings") for a in accts)
            s += 10_000 * sum(bool(a.pan_masked) for a in accts)
            return -s

        return sorted(self.w.customers_of(self.bank), key=score)[0]

    # ---------------------------------------------------------------- plumbing
    def call(self, method: str, path: str, query=None, body=None):
        h = {"x-mock-customer": self.cid, "x-request-id": "route-test",
             "psu-ip-address": "203.0.113.7", "tpp-redirect-uri": "https://tpp.example/cb"}
        return self.gw.handle(self.bank, Request(method, path, dict(query or {}), h, body,
                                                 f"http://t/{self.bank}"))

    def res(self, *ops: str) -> str:
        for e in self.d.route_table():
            if e[0] == "GET" and e[2] in ops:
                body = self.call("GET", e[1]).body or {}
                items = next((v for v in body.values() if isinstance(v, list)), [])
                if items:
                    return items[0]["resourceId"]
        raise NoData(ops)

    def other_iban(self) -> str:
        for b in BANKS:
            if b != self.bank:
                for a in self.w.accounts_of(b, self.w.customers_of(b)[0], ("current",)):
                    return a.iban
        raise AssertionError

    def own_iban(self) -> str:
        return self.w.accounts_of(self.bank, self.cid, ("current",))[0].iban

    # ---------------------------------------------------------------- bodies
    def amount(self, value: str = "1.00") -> dict:
        if self.d.amount_number:
            return {"currency": "GEL", "value": float(value)}
        return {"currency": "GEL", "amount": value}

    @staticmethod
    def consent_body(family: str) -> dict:
        valid = (date.today() + timedelta(days=30)).isoformat()
        if family == "v2":
            return {"access": {"loans": [], "savings": []}, "consentType": "global",
                    "recurringIndicator": True, "validTo": valid, "frequencyPerDay": 4}
        return {"access": {"availableAccounts": "allAccounts"}, "recurringIndicator": True,
                "validUntil": valid, "frequencyPerDay": 4, "combinedServiceIndicator": False}

    def payment_body(self) -> dict:
        return {"debtorAccount": {"iban": self.own_iban()}, "instructedAmount": self.amount(),
                "creditorAccount": {"iban": self.other_iban()}, "creditorName": "ROUTE TEST",
                "remittanceInformationUnstructured": "route test"}

    # ---------------------------------------------------------------- resources
    def family_of(self, tmpl: str) -> str:
        if tmpl.startswith("/v2/consents") and (self.bank == "tbc" or self.d.consent_family == "v2"):
            return "v2"
        return "v1" if self.bank == "tbc" else self.d.consent_family

    def consent(self, family: str, fresh: bool = False) -> str:
        key = ("consent", family)
        if key not in self.cache or fresh:
            r = self.call("POST", self.d.cbase(family), body=self.consent_body(family))
            assert r.status == 201, r.body
            cid = r.body["consentId"]
            if self.d.explicit_auth:
                assert self.call("POST", f"{self.d.cauth_base(family)}/{cid}/authorisations",
                                 body={}).status == 201
            self.cache[key] = cid
        return self.cache[key]

    def is_ext(self, tmpl: str) -> bool:
        return tmpl.startswith("/openfinance") or (self.bank == "tbc" and tmpl.startswith("/v2/{"))

    def ext_prefix(self) -> str:
        return "/v2" if self.bank == "tbc" else "/openfinance/v2"

    def payment(self, ext: bool, fresh: bool = False, kind: str = "main") -> dict:
        key = ("pay", ext, kind)  # "cancel" payments stay separate: cancelling ends the lifecycle
        if key in self.cache and not fresh:
            return self.cache[key]
        if ext:
            svc = "recurring-payments"
            r = self.call("POST", f"{self.ext_prefix()}/{svc}/domestic",
                          body={**self.payment_body(), "frequency": "Monthly"})
        else:
            svc = self.d.payment_services[0]
            r = self.call("POST", f"{self.d.api_v}/{svc}/domestic", body=self.payment_body())
        assert r.status == 201, r.body
        pay = {"pid": r.body["paymentId"], "svc": svc, "prod": "domestic"}
        if not fresh:  # fresh ones stay unauthorised so DELETE can succeed
            self.gw.complete_sca(self.bank, "payment", pay["pid"], self.cid)
            self.cache[key] = pay
        return pay

    def value(self, name: str, tmpl: str) -> str:
        n = name
        if n in ("account-id", "card-id", "accountId", "resourceId"):
            if "/loans" in tmpl:
                return self.res("loans_list")
            if "/savings" in tmpl:
                return self.res("savings_list")
            if "/card" in tmpl:
                return self.res("cards_list", "cards2_list")
            return self.res("accounts_list")
        if n == "transactionId":
            acct = self.res("accounts_list")
            body = self.call("GET", f"{self.d.accounts_path}/{acct}/transactions",
                             {"bookingStatus": "both"}).body["transactions"]
            return body["booked"][0]["transactionId"]
        if n == "consentId":
            return self.consent(self.family_of(tmpl), fresh=self.method == "DELETE")
        if n == "authorisationId":
            if "consents" in tmpl:
                return self.gw.sessions.consents[self.consent(self.family_of(tmpl))].auth_ids[0]
            ext = self.is_ext(tmpl)
            kind = "cancel" if "cancellation-authorisations" in tmpl else "main"
            p = self.gw.sessions.payments[self.payment(ext, kind=kind)["pid"]]
            if "cancellation-authorisations" in tmpl:
                if not p.cancel_auth_ids:  # seed directly: not every bank documents the POST
                    a = Authorisation(new_id(), p.id, "payment-cancel", "started")
                    self.gw.sessions.auths[a.id] = a
                    p.cancel_auth_ids.append(a.id)
                return p.cancel_auth_ids[0]
            return p.auth_ids[0]
        kind = "cancel" if "cancellation-authorisations" in tmpl else "main"
        if n == "payment-service":
            return self.payment(self.is_ext(tmpl), kind=kind)["svc"]
        if n == "payment-product":
            return "domestic"
        if n == "paymentId":
            fresh = self.method == "DELETE" and "initiations" not in tmpl
            return self.payment(self.is_ext(tmpl), fresh=fresh, kind=kind)["pid"]
        if n == "initiationId":
            if "initiation" not in self.cache:
                pay = self.payment(True)
                r = self.call("POST", f"{self.ext_prefix()}/{pay['svc']}/domestic/{pay['pid']}"
                                      "/initiations", body={"instructedAmount": self.amount()})
                assert r.status == 201, r.body
                self.cache["initiation"] = r.body["initiationId"]
            return self.cache["initiation"]
        if n == "x":
            return "en"
        if n == "family":
            return "term-deposits"
        raise AssertionError(f"unresolved path parameter {name} in {tmpl}")

    def fill(self, tmpl: str) -> str:
        return re.sub(r"\{([^}]+)\}", lambda m: str(self.value(m.group(1), tmpl)), tmpl)

    def default_family(self) -> str:
        return "v1" if self.bank == "tbc" else self.d.consent_family

    # ---------------------------------------------------------------- one route
    def exercise(self, route):
        self.method = route.method
        path, query, body = self.fill(route.template), {}, None
        op = route.op
        if op.endswith("transactions"):
            query = {"bookingStatus": "both"}
        if op == "consent_create":
            body = self.consent_body(self.family_of(route.template))
        elif op == "pis_create":
            body = self.payment_body()
        elif op == "pis2_create":
            body = {**self.payment_body(), "frequency": "Monthly"}
        elif op == "pis2_init_create":
            body = {"instructedAmount": self.amount()}
        elif op in ("consent_auth_start", "consent_auth_update", "pis_auth_start",
                    "pis_auth_update", "pis_cancel_auth_start", "pis_cancel_auth_update", "kpi"):
            body = {}
        elif op == "oauth_authorize":
            query = {"response_type": "code", "client_id": "app", "redirect_uri": "https://t/cb",
                     "scope": f"AIS:{self.consent(self.default_family())}", "state": "s"}
        elif op == "oauth_token":
            cons = self.consent(self.default_family(), fresh=True)
            auth_path = next(e[1] for e in self.d.route_table() if e[2] == "oauth_authorize")
            loc = self.call("GET", auth_path, {
                "response_type": "code", "client_id": "app", "scope": f"AIS:{cons}",
                "redirect_uri": "https://t/cb"}).headers["Location"]
            body = {"grant_type": "authorization_code", "client_id": "app",
                    "code": loc.split("code=")[1].split("&")[0]}
        return self.call(route.method, path, query, body)


@pytest.mark.parametrize("bank", list(BANKS))
def test_every_documented_route_answers(bank, mworld):
    gw = Gateway(mworld, auth_mode="off")
    ex = Exerciser(gw, mworld, bank)
    expected = {**EXPECTED_REFUSAL["*"], **EXPECTED_REFUSAL.get(bank, {})}
    failures, skipped, passed = [], [], 0
    routes = sorted(gw.dialects[bank].routes(), key=lambda r: (r.method == "DELETE", r.template))
    for route in routes:
        try:
            resp = ex.exercise(route)
        except NoData:
            # the best-all-round customer may lack this resource while another customer owns it
            kind = "loan" if "/loans" in route.template else \
                "savings" if "/savings" in route.template else None
            owner = next((c for c in mworld.customers_of(bank) if kind and any(
                a.kind.startswith(kind) for a in mworld.accounts_of(bank, c))), None)
            try:
                resp = Exerciser(gw, mworld, bank, owner).exercise(route) if owner else None
            except NoData:
                resp = None
            if resp is None:
                skipped.append(route.template)
                continue
        if resp.status in expected.get(route.op, OK):
            passed += 1
        else:
            failures.append(f"{route.method} {route.template} -> {resp.status} {resp.body}")
    assert not failures, "\n".join(failures)
    assert passed >= 8
    # a route may only be skipped when the world truly holds no such resource at this bank
    for t in skipped:
        kind = "loan" if "/loans" in t else "savings" if "/savings" in t else None
        assert kind, f"unexpected skip: {t}"
        have = any(a.kind.startswith(kind) for c in mworld.customers_of(bank)
                   for a in mworld.accounts_of(bank, c))
        assert not have, f"{t} skipped although the world has {kind} accounts at {bank}"
