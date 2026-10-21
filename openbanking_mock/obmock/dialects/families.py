"""Bank dialects. Each class overrides only what that bank's published contract shows.

Evidence per dialect (see docs/BANK_ANALYSIS.md for sources):
  tbc           reconstructed OpenAPI fragments + 46 published examples (v1 AIS, v2 loans/savings)
  bog           OpenAPI 0.8 (BG 1.3.9 + /banking/products)
  altasoft_v08  OpenAPI v0.8 from the AltaSoft PSD2 portal (TeraBank, Liberty)
  altasoft_v2   OpenAPI v2.0 from the AltaSoft PSD2 portal (Silk, Cartu, PASHA - identical bytes)
  obhome        BG 1.3.9 YAML shared by Credo/Halyk/Basisbank + Georgian guide 0.8.RC3
  procredit     Redocly-embedded OpenAPI (12 ops, examples only)
  paysera       hand-written docs "Open Banking for Georgia v0.8" (no OpenAPI published)
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import numpy as np
from obmock.dialects.base import (
    EXTENDED_SERVICES,
    Dialect,
    ais_routes,
    consent_routes,
    oauth_routes,
    ok,
    pis_routes,
)
from obmock.engine.gateway import (
    ApiError,
    Authorisation,
    Ctx,
    Payment,
    Request,
    Response,
    new_id,
    utcnow,
)
from obmock.reference import DEPOSIT_RATES
from obmock.util import fmt_amount, make_iban, stable_uuid
from obmock.world.model import Account, Loan, Txn

Routes = list[tuple]


def with_fixed(routes: Routes, **fixed: str) -> Routes:
    return [(m, t, op, fixed) for m, t, op in routes]


def pis2_routes(v: str) -> Routes:
    """Extended (v2/openfinance) payments: deferred, recurring, secured + initiations."""
    b = f"{v}/{{payment-service}}/{{payment-product}}"
    r: Routes = [e for e in pis_routes(v) if e[2] != "pis_create"]
    r += [
        ("POST", b, "pis2_create"),
        ("POST", f"{b}/{{paymentId}}/initiations", "pis2_init_create"),
        ("GET", f"{b}/{{paymentId}}/initiations", "pis2_init_list"),
        ("GET", f"{b}/{{paymentId}}/initiations/{{initiationId}}", "pis2_init_get"),
        ("GET", f"{b}/{{paymentId}}/initiations/{{initiationId}}/status", "pis2_init_status"),
        ("DELETE", f"{b}/{{paymentId}}/initiations/{{initiationId}}", "pis2_init_cancel"),
    ]
    return r


class ExtendedPaymentsMixin:
    """Generic stateful implementation of deferred/recurring/secured payments (v2)."""

    def op_pis2_create(self: Any, ctx: Ctx, payment_service: str,
                       payment_product: str) -> Response:
        if payment_service not in EXTENDED_SERVICES or payment_product not in self.payment_products:
            raise ApiError(404, "RESOURCE_UNKNOWN",
                           f"unknown payment service/product {payment_service}/{payment_product}")
        b = self.body(ctx)
        ia = b.get("instructedAmount") or b.get("limitAmount") or {}
        try:
            amt = round(float(ia.get("amount", 0)) * 100)
        except (TypeError, ValueError) as e:
            raise ApiError(400, "FORMAT_ERROR", "amount is invalid") from e
        p = Payment(new_id(), self.bank.key, payment_service, payment_product, b, "RCVD", None,
                    amt, str(ia.get("currency", "GEL")), utcnow())
        a = Authorisation(new_id(), p.id, "payment")
        ctx.sessions.auths[a.id] = a
        p.auth_ids.append(a.id)
        ctx.sessions.payments[p.id] = p
        base = self.pbase(p)
        links = {self.sca_link: self._sca_href(ctx, "payment", p.id, a.id), "self": self.href(base),
                 "status": self.href(f"{base}/status"),
                 "scaStatus": self.href(f"{base}/authorisations/{a.id}")}
        return ok({"transactionStatus": p.status, "paymentId": p.id, "_links": links}, 201,
                  {"Location": self.link(base), "ASPSP-SCA-Approach": "REDIRECT"})

    def op_pis2_init_create(self: Any, ctx: Ctx, payment_service: str, payment_product: str,
                            paymentId: str) -> Response:
        p = self._payment(ctx, paymentId)
        if p.status != "ACTC":
            raise ApiError(409, "STATUS_INVALID",
                           f"payment consent is not authorised (status {p.status})")
        b = self.body(ctx)
        ia = b.get("instructedAmount") or {}
        try:
            amt = round(float(ia.get("amount")) * 100)
        except (TypeError, ValueError) as e:
            raise ApiError(400, "FORMAT_ERROR", "instructedAmount.amount is required") from e
        merged = {**p.body, **b}
        if not (merged.get("creditorAccount") or {}).get("iban"):
            raise ApiError(400, "FORMAT_ERROR", "creditorAccount.iban is required")
        status = self._execute(p, amt, merged)
        iid = new_id()
        p.initiations[iid] = {"initiationId": iid, "transactionStatus": status,
                              "instructedAmount": ia, "createdAt": utcnow().isoformat()}
        base = f"{self.pbase(p)}/initiations/{iid}"
        return ok({"initiationId": iid, "transactionStatus": status,
                   "_links": {"self": self.href(base), "status": self.href(f"{base}/status")}}, 201)

    def op_pis2_init_list(self: Any, ctx: Ctx, payment_service: str, payment_product: str,
                          paymentId: str) -> dict:
        return {"initiations": list(self._payment(ctx, paymentId).initiations.values())}

    def _init(self: Any, ctx: Ctx, paymentId: str, initiationId: str) -> dict:
        i = self._payment(ctx, paymentId).initiations.get(initiationId)
        if i is None:
            raise ApiError(404, "RESOURCE_UNKNOWN", "initiation not found")
        return i

    def op_pis2_init_get(self: Any, ctx: Ctx, payment_service: str, payment_product: str,
                         paymentId: str, initiationId: str) -> dict:
        return self._init(ctx, paymentId, initiationId)

    def op_pis2_init_status(self: Any, ctx: Ctx, payment_service: str, payment_product: str,
                            paymentId: str, initiationId: str) -> dict:
        return {"transactionStatus": self._init(ctx, paymentId, initiationId)["transactionStatus"]}

    def op_pis2_init_cancel(self: Any, ctx: Ctx, payment_service: str, payment_product: str,
                            paymentId: str, initiationId: str) -> Response:
        i = self._init(ctx, paymentId, initiationId)
        if i["transactionStatus"] in ("ACCC", "ACSC"):
            raise ApiError(405, "SERVICE_INVALID", "an executed initiation cannot be cancelled")
        i["transactionStatus"] = "CANC"
        return Response(204, None)


# ============================================================================== TBC
class TbcDialect(ExtendedPaymentsMixin, Dialect):
    """TBC Bank. Berlin Group 1.3.x base; v2 adds loans, savings, extended payments."""

    key = "tbc"
    prefixes = ("/0.8", "")
    shared_fx_iban = True  # one multi-currency IBAN: `XXX` aggregate + per-currency children
    consent_headers = ("consent-id", "consentid")  # loans/savings spec names it `consentId`
    require_psu_ip = True
    explicit_auth = True
    txn_detail = False
    loans = savings = True
    booking_status_required = False
    payment_services = ("payments", "bulk-payments")

    def route_table(self) -> Routes:
        r = ais_routes("/v1", detail=False)
        r += consent_routes("/v1/consents", put=False)
        r += with_fixed(consent_routes("/v2/consents/account-access", put=False), family="v2")
        r += [
            ("GET", "/v2/loans", "loans_list"), ("GET", "/v2/loans/{resourceId}", "loan_get"),
            # published exactly as documented, missing slash included (intent unverified)
            ("GET", "/v2/loans{resourceId}/balances", "loan_balances"),
            ("GET", "/v2/loans/{resourceId}/balances", "loan_balances_alias"),
            ("GET", "/v2/savings", "savings_list"),
            ("GET", "/v2/savings/{resourceId}", "saving_get"),
            ("GET", "/v2/savings/{resourceId}/balances", "saving_balances"),
        ]
        r += pis_routes("/v1", put=False) + pis2_routes("/v2") + oauth_routes()
        return r

    def link(self, path: str) -> str:
        # v1 examples carry the /0.8 prefix; v2 examples (savings, loans) use relative hrefs
        if path.startswith("/v2"):
            return path.lstrip("/")
        return super().link(path)

    def cbase(self, family: str) -> str:
        return "/v2/consents/account-access" if family == "v2" else "/v1/consents"

    def cauth_base(self, family: str) -> str:
        return self.cbase(family)

    def v2_consent_label(self, c: Any) -> str:
        return "V2Consent"  # as in TBC's published GET example

    def error_response(self, err: ApiError, req: Request, rid: str) -> Response:
        titles = {400: "Invalid Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                  405: "Method Not Allowed", 409: "Conflict", 429: "Too Many Requests",
                  500: "Internal Server Error", 503: "Service Unavailable"}
        trace = hashlib.sha1(f"{rid}{req.path}".encode()).hexdigest()
        body = {"code": err.code, "traceId": f"00-{trace[:32]}-{trace[32:40]}00000000-01",
                "type": f"https://developers.tbcbank.ge/docs/error-type-{err.status}",
                "title": titles.get(err.status, "Error"), "status": err.status,
                "detail": err.text, "instance": req.path}
        return Response(err.status, body, dict(err.headers))

    def proprietary_code(self, t: Txn) -> str | None:
        # GMI (credit) / GMB (debit) are published only on TBC's example transfers and card
        # transactions, so fees, interest and FX legs carry none.
        if t.channel in ("TRANSFER", "POS", "ECOM", "ATM"):
            return "GMI" if t.amount > 0 else "GMB"
        return None

    # ---- accounts: one multi-currency IBAN, `XXX` aggregate + per-currency children
    def cashtype(self, a: Account) -> str:
        return "CARD" if a.pan_masked else "CACC"

    def ser_account(self, ctx: Ctx, a: Account, with_balances: bool) -> dict[str, Any]:
        d = super().ser_account(ctx, a, with_balances)
        c = self.world.customers[a.cid]
        d["msisdn"] = c.msisdn
        d["ownerIdentification"] = {"privateId": {"others": [
            {"identification": f"PNOGE-{c.person_id}"}]}}
        d["displayName"] = self.bank.products["debit_card"] if a.kind == "current" else a.product
        return d

    def get_account(self, ctx: Ctx, rid: str, kinds: tuple[str, ...]) -> Account:
        for a in self.visible_accounts(ctx, ("current",)):
            if stable_uuid("aggregate", a.id) == rid:
                return a
        return super().get_account(ctx, rid, kinds)

    def account_entries(self, ctx: Ctx, accts: list[Account], with_balances: bool) -> list[dict]:
        fx = [a for a in accts if a.kind == "fx"]
        out: list[dict] = []
        for a in accts:
            if a.kind != "current":
                continue
            if fx:
                parent = self.ser_account(ctx, a, False)
                parent.update(resourceId=stable_uuid("aggregate", a.id), currency="XXX",
                              cashAccountType="CACC")
                parent["_links"] = {**parent["_links"], "account": self.href(
                    f"/v1/accounts/{parent['resourceId']}")}
                if with_balances:
                    bl: list[dict] = []
                    for x in [a, *fx]:
                        bl += self.balance_list(x, self.balance_types_embedded)
                    parent["balances"] = bl
                out.append(parent)
            out.append(self.ser_account(ctx, a, with_balances))
        out += [self.ser_account(ctx, a, with_balances) for a in fx]
        return out

    def op_account_get(self, ctx: Ctx, account_id: str) -> dict:
        a = self.get_account(ctx, account_id, self.account_kinds())
        d = self.ser_account(ctx, a, self.q_bool(ctx, "withBalance"))
        if stable_uuid("aggregate", a.id) == account_id:
            d.update(resourceId=account_id, currency="XXX", cashAccountType="CACC")
        return {"account": d}

    def ser_card(self, ctx: Ctx, c: Any, with_balances: bool = True) -> dict[str, Any]:
        d = super().ser_card(ctx, c, with_balances)
        if "creditLimit" in d:  # TBC's schema types the limit as an amount string
            d["creditLimit"] = fmt_amount(c.acct.credit_limit)
        return d

    def ser_card_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d = super().ser_card_txn(ctx, a, t)
        d["maskedPan"] = d.pop("maskedPAN")  # TBC spells it maskedPan and has no MCC field
        d.pop("merchantCategoryCode", None)
        return d

    def ser_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d = super().ser_txn(ctx, a, t)
        c = self.world.customers[a.cid]
        side = "creditor" if t.amount < 0 else "debtor"
        if t.cp_person_id:
            d[f"{side}Identification"] = {"privateId": {"others": [
                {"identification": f"PNOGE-{t.cp_person_id}"}]}}
        if t.amount < 0:
            d["debtorIdentification"] = {"privateId": {"others": [
                {"identification": f"PNOGE-{c.person_id}"}]}}
        if "additionalInformation" not in d:
            d["additionalInformation"] = self.narr(t)
        return d

    # ---- loans (limited schema: TBC publishes no instalment / overdue / arrears fields)
    @staticmethod
    def _dt(d: date) -> str:
        return f"{d.isoformat()}T00:00:00"

    def _loan_balances(self, loan: Loan) -> list[dict[str, Any]]:
        return [
            {"balanceAmount": {"currency": "GEL",
                               "amount": round(loan.outstanding_principal / 100, 2)},
             "balanceType": "ClosingBooked", "referenceDate": self._dt(self.world.as_of)},
            {"balanceAmount": {"currency": "GEL", "amount": round(loan.principal / 100, 2)},
             "balanceType": "OpeningBooked", "referenceDate": self._dt(loan.start)},
        ]

    def ser_loan(self, ctx: Ctx, a: Account) -> dict[str, Any]:
        loan = self.world.loans[a.meta["loan_id"]]
        return {
            "resourceId": a.id, "currency": "GEL",
            "relatedDates": {"contractStartDate": self._dt(loan.start),
                             "contractEndDate": self._dt(loan.maturity)},
            "balances": self._loan_balances(loan), "status": "Enabled",
            "interest": {"rate": {"percentage": round(loan.rate * 100, 2)}}, "product": a.product,
            # the schema also declares `_links.transactions`, but no TBC operation serves it
            "_links": {"balances": self.href(f"/v2/loans/{a.id}/balances")},
        }

    def op_loans_list(self, ctx: Ctx) -> dict:
        return {"accounts": [self.ser_loan(ctx, a) for a in self.loans_of(ctx)]}

    def op_loan_get(self, ctx: Ctx, resourceId: str) -> dict:
        return {"account": self.ser_loan(ctx, self.get_account(ctx, resourceId, ("loan",)))}

    def op_loan_balances(self, ctx: Ctx, resourceId: str) -> dict:
        a = self.get_account(ctx, resourceId, ("loan",))
        return {"balances": self._loan_balances(self.world.loans[a.meta["loan_id"]])}

    op_loan_balances_alias = op_loan_balances

    # ---- savings (schema + example: `links`, string amounts, PascalCase balance types)
    def _saving_balances(self, a: Account) -> list[dict[str, Any]]:
        bal = self.world.balances(a)
        as_of = self.world.as_of
        return [
            {"balanceAmount": self.money(bal["openingBooked"], a.currency),
             "balanceType": "OpeningBooked", "referenceDate": as_of.replace(day=1).isoformat()},
            {"balanceAmount": self.money(bal["closingBooked"], a.currency),
             "balanceType": "ClosingBooked",
             "referenceDate": (as_of - timedelta(days=1)).isoformat()},
        ]

    def ser_saving(self, ctx: Ctx, a: Account) -> dict[str, Any]:
        dep = self.world.deposit_of(a.id)
        return {
            "resourceId": a.id, "product": "Term" if a.kind == "savings_term" else "Flexible",
            "currency": a.currency, "status": "Closed" if dep and dep.closed else "Active",
            "period": a.meta.get("term_months") or 0,
            "contractStartDate": a.opened.isoformat(), "ownerName": self.owner_name(a),
            "cashAccountType": "SVGS", "balances": self._saving_balances(a),
            "links": {"balances": self.href(f"/v2/savings/{a.id}/balances")},
        }

    def op_savings_list(self, ctx: Ctx) -> dict:
        return {"accounts": [self.ser_saving(ctx, a) for a in self.savings_of(ctx)]}

    def op_saving_get(self, ctx: Ctx, resourceId: str) -> dict:
        return self.ser_saving(ctx, self.get_account(
            ctx, resourceId, ("savings_term", "savings_flex")))

    def op_saving_balances(self, ctx: Ctx, resourceId: str) -> dict:
        a = self.get_account(ctx, resourceId, ("savings_term", "savings_flex"))
        return {"resourceId": a.id, "balances": self._saving_balances(a)}


# ============================================================================== Bank of Georgia
class BogDialect(Dialect):
    """Bank of Georgia: BG 1.3.9 XS2A (base /0.8) plus the public /banking/products service."""

    key = "bog"
    prefixes = ("/0.8", "")
    card_accounts = False
    shared_fx_iban = True  # the spec's examples show currency sub-accounts under one IBAN
    families = ("trans-and-savings-accounts", "term-deposits", "residential-mortgages",
                "pers-loans", "cred-and-chrg-cards", "business-loans")

    def route_table(self) -> Routes:
        return (ais_routes("/v1", cards=False) + consent_routes("/v1/consents") +
                pis_routes("/v1") + oauth_routes() + [
                    ("GET", "/banking/products", "products"),
                    ("GET", "/banking/products/{x}", "products"),
                    ("GET", "/banking/products/{x}/{family}", "products"),
                ])

    def catalog(self, family: str, locale: str) -> list[dict[str, Any]]:
        ka = locale == "ka"
        rng = np.random.default_rng(
            int(hashlib.sha1(f"{self.bank.key}{family}".encode()).hexdigest()[:8], 16))
        rows: list[dict[str, Any]] = []

        def add(name_en: str, name_ka: str, **extra: Any) -> None:
            ccy = extra.pop("currency", "GEL")
            rows.append({"id": stable_uuid("product", family, name_en), "family": family,
                         "name": name_ka if ka else name_en, "currency": ccy, **extra})

        if family == "term-deposits":
            for ccy, tbl in DEPOSIT_RATES.items():
                for months, rate in tbl.items():
                    add(f"Term deposit {months}m {ccy}", f"ვადიანი ანაბარი {months}თ {ccy}",
                        currency=ccy, termMonths=months, annualRate=round(rate * 100, 2),
                        minAmount=100)
        elif family == "residential-mortgages":
            for n, r, c in (("Mortgage GEL", 0.115, "GEL"), ("Mortgage USD", 0.092, "USD")):
                add(n, "იპოთეკური სესხი", annualRate=round(r * 100, 2), maxTermMonths=240,
                    maxLtv=80, currency=c)
        elif family == "pers-loans":
            for n, r in (("Consumer loan", 0.19), ("Instant instalment", 0.0), ("Cash loan", 0.24)):
                add(n, "სამომხმარებლო სესხი",
                    annualRate=round(r * 100 + float(rng.uniform(0, 1)), 2), maxTermMonths=60)
        elif family == "business-loans":
            add("Working capital", "საბრუნავი საშუალებები", annualRate=15.5, maxTermMonths=36)
            add("Investment loan", "საინვესტიციო სესხი", annualRate=14.0, maxTermMonths=84)
        elif family == "cred-and-chrg-cards":
            add("Visa Platinum Credit", "Visa Platinum", annualRate=34.0, annualFee=60, graceDays=55)
            add("Mastercard Gold Credit", "Mastercard Gold", annualRate=34.0, annualFee=40,
                graceDays=45)
        else:
            add("Current account", "მიმდინარე ანგარიში", monthlyFee=0.0, cardIncluded=True)
            add("Savings account", "დამზოგავი ანგარიში", annualRate=2.5)
        return rows

    def op_products(self, ctx: Ctx, x: str | None = None, family: str | None = None) -> dict:
        q = ctx.req.query
        locale = x if x in ("en", "ka") else "en"
        fam = family or (x if x in self.families else None)
        if x and x not in ("en", "ka") and x not in self.families:  # /banking/products/{id}
            for f in self.families:
                for row in self.catalog(f, locale):
                    if row["id"] == x:
                        return row
            raise ApiError(404, "RESOURCE_UNKNOWN", "product not found")
        data = [r for f in ([fam] if fam else list(self.families)) for r in self.catalog(f, locale)]
        size = max(1, min(100, int(q.get("page-size", 10) or 10)))
        page = max(0, int(q.get("page", 0) or 0))
        return {"data": data[page * size:(page + 1) * size],
                "meta": {"page": page, "pageSize": size, "totalRecords": len(data)}}


# ============================================================================== AltaSoft v0.8
class AltaSoftV08Dialect(Dialect):
    """AltaSoft PSD2 portal, NextGenPSD2 v0.8 (TeraBank, Liberty). Relative links, `state` paging."""

    key = "altasoft_v08"
    prefixes = ("/0.8", "")
    link_style = "relative"
    paging = "state"
    explicit_auth = True
    booking_status_required = False

    @property
    def owner_key(self) -> str:  # type: ignore[override]
        return "owner" if self.bank.key == "terabank" else "ownerName"

    def route_table(self) -> Routes:
        return (ais_routes("/v1") + consent_routes("/v1/consents") + pis_routes("/v1") +
                oauth_routes())


# ============================================================================== AltaSoft v2
class AltaSoftV2Dialect(ExtendedPaymentsMixin, Dialect):
    """AltaSoft PSD2 portal v2.0 (Silk, Cartu, PASHA): nested party objects, loans/savings/cards."""

    key = "altasoft_v2"
    prefixes = ("",)
    consent_family = "v2"
    link_style = "relative"
    paging = "state"
    explicit_auth = True
    booking_status_required = False
    api_v = "/v2"
    loans = savings = True

    @property
    def consent_base(self) -> str:  # type: ignore[override]
        return "/v2/consents/account-access"

    @property
    def consent_auth_base(self) -> str:  # type: ignore[override]
        return "/v2/consents"

    def route_table(self) -> Routes:
        r = ais_routes("/v2")
        r += [("GET", "/v2/cards", "cards2_list"), ("GET", "/v2/cards/{card-id}", "cards2_get"),
              ("GET", "/v2/cards/{card-id}/balances", "cards2_balances"),
              ("GET", "/v2/cards/{card-id}/transactions", "cards2_transactions")]
        for fam, k in (("loans", "loan"), ("savings", "saving")):
            r += [("GET", f"/v2/{fam}", f"{fam}_list"),
                  ("GET", f"/v2/{fam}/{{accountId}}", f"{k}_get"),
                  ("GET", f"/v2/{fam}/{{accountId}}/balances", f"{k}_balances"),
                  ("GET", f"/v2/{fam}/{{accountId}}/transactions", f"{k}_transactions")]
        r += consent_routes("/v2/consents/account-access", auth_base="/v2/consents")
        r += pis_routes("/v2") + pis2_routes("/openfinance/v2") + oauth_routes()
        # published as-is: the extended-payment DELETE carries a stray literal `payments` segment
        r.append(("DELETE", "/openfinance/v2/{payment-service}/payments/{payment-product}/{paymentId}",
                  "pis_cancel"))
        return r

    # ---- nested BG-2.x style transaction
    def txid(self, a: Account, t: Txn) -> str:
        return f"{a.iban or a.id}_{int(t.ref)}"

    def ser_account(self, ctx: Ctx, a: Account, with_balances: bool) -> dict[str, Any]:
        d = super().ser_account(ctx, a, with_balances)
        d["displayName"] = a.product
        # the v2 schema requires `other` next to `iban`; use the 16-digit account number
        d["other"] = {"identification": (a.iban or a.id)[6:], "issuer": self.bank.name_en}
        return d

    def ser_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d: dict[str, Any] = {
            "transactionId": self.txid(a, t), "entryReference": t.ref,
            "valueDate": t.value_date.isoformat(),
            "transactionAmount": self.money(t.amount, t.currency),
        }
        if t.status == "booked":
            d["bookingDate"] = t.booking_date.isoformat()
        side = "creditor" if t.amount < 0 else "debtor"
        name = self.person_name(t)
        if name:
            d[side] = {"name": name}
        if t.cp_iban:
            d[f"{side}Account"] = {"iban": t.cp_iban}
        if t.cp_bic:
            d[f"{side}Agent"] = {"financialInstitutionId": {"bicfi": t.cp_bic}}
        if t.fx_src_ccy and t.fx_rate:
            d["currencyExchange"] = self.fx_exchange(t)
        d["remittanceInformationUnstructured"] = [self.narr(t)]
        codes = self.std_codes(a, t)
        codes.pop("mandateId", None)  # not defined in the Silk/Cartu/PASHA contract
        d.update(codes)
        if self.rich and t.balance_after is not None:
            d["balanceAfterTransaction"] = self.balance_after(a, t)
        if t.channel in ("POS", "ECOM", "ATM"):
            d["cardTransaction"] = {
                "paymentContext": {"cardPresent": t.channel != "ECOM"},
                "sequenceNumber": str(t.seq)[-10:], "transactionDateTime": self.iso_ts(t.ts)}
            if t.merchant:
                d["additionalTransactionInformation"] = t.merchant
        if t.e2e:
            d["references"] = {"endToEndId": t.e2e}
        return d

    # ---- cards (/v2/cards)
    def op_cards2_list(self, ctx: Ctx) -> dict:
        return {"cards": [self.ser_card(ctx, c) for c in self.cards(ctx)]}

    def op_cards2_get(self, ctx: Ctx, card_id: str) -> dict:
        return {"cardAccount": self.ser_card(ctx, self.get_card(ctx, card_id))}

    def op_cards2_balances(self, ctx: Ctx, card_id: str) -> dict:
        return self.op_card_balances(ctx, card_id)

    def op_cards2_transactions(self, ctx: Ctx, card_id: str) -> dict:
        return self.op_card_transactions(ctx, card_id)

    # ---- loans (AccountDetails with cashAccountType LOAN; richer than TBC's)
    def _loan_of(self, a: Account) -> Loan:
        return self.world.loans[a.meta["loan_id"]]

    def _pseudo_iban(self, a: Account) -> str:
        digits = "".join(str(int(c, 16) % 10) for c in hashlib.sha1(a.id.encode()).hexdigest()[:15])
        return make_iban(f"{self.bank.iban_code}4{digits}")

    def _loan_balances(self, loan: Loan) -> list[dict[str, Any]]:
        ref = self.world.as_of

        def bal(btype: str, cents: int, d: date, sub: str | None = None) -> dict[str, Any]:
            b = {"balanceAmount": self.money(cents, "GEL"), "balanceType": btype,
                 "referenceDate": d.isoformat()}
            if sub:
                b["balanceSubTypeProprietary"] = sub
            return b

        out = [bal("closingBooked", -loan.outstanding_principal, ref - timedelta(days=1))]
        if loan.next_due:
            out.append(bal("expected", -loan.next_amount, loan.next_due))
        if loan.overdue_count:
            out.append(bal("information", -(loan.overdue_principal + loan.overdue_interest), ref,
                           "OVERDUE_AMOUNT"))
        return out

    def ser_loan(self, ctx: Ctx, a: Account) -> dict[str, Any]:
        loan = self._loan_of(a)
        d = self.ser_account(ctx, a, False)
        d.update(
            iban=self._pseudo_iban(a),
            other={"identification": a.meta["contract_no"], "schemeNameProprietary": "LOAN_CONTRACT",
                   "issuer": self.bank.name_en},
            cashAccountType="LOAN", product=a.product, balances=self._loan_balances(loan),
            interest=[{"type": "FIXD" if loan.rate_type == "Fixed" else "INDE",
                       "rate": [{"percentage": f"{loan.rate * 100:.2f}"}]}],
            relatedDates=[{"contractStartDate": loan.start.isoformat(),
                           "contractEndDate": loan.maturity.isoformat()}],
            collateralsInvolved=loan.collateral, guaranteeInvolved=loan.guarantee,
            tariffs=f"{loan.term_months} monthly annuity instalments of "
                    f"{fmt_amount(loan.instalment)} GEL due on day {loan.pay_day}")
        d["_links"] = {"balances": self.href(f"/v2/loans/{a.id}/balances"),
                       "transactions": self.href(f"/v2/loans/{a.id}/transactions")}
        return d

    def op_loans_list(self, ctx: Ctx) -> dict:
        return {"accounts": [self.ser_loan(ctx, a) for a in self.loans_of(ctx)]}

    def op_loan_get(self, ctx: Ctx, accountId: str) -> dict:
        return {"account": self.ser_loan(ctx, self.get_account(ctx, accountId, ("loan",)))}

    def op_loan_balances(self, ctx: Ctx, accountId: str) -> dict:
        a = self.get_account(ctx, accountId, ("loan",))
        return {"account": {"iban": self._pseudo_iban(a), "currency": "GEL"},
                "balances": self._loan_balances(self._loan_of(a))}

    def op_loan_transactions(self, ctx: Ctx, accountId: str) -> dict:
        a = self.get_account(ctx, accountId, ("loan",))
        loan = self._loan_of(a)
        booked = []
        for i in loan.schedule:
            if i.paid_on and self.world.window_start <= i.paid_on <= self.world.as_of:
                booked.append({
                    "transactionId": f"{a.meta['contract_no']}_{i.n}",
                    "entryReference": f"{i.n:010d}", "bookingDate": i.paid_on.isoformat(),
                    "valueDate": i.due.isoformat(),
                    "transactionAmount": self.money(i.total, "GEL"),
                    "remittanceInformationUnstructured": [f"Loan repayment instalment {i.n}"],
                    "additionalTransactionInformation":
                        f"principal {fmt_amount(i.principal)}; interest {fmt_amount(i.interest)}"})
        return {"account": {"iban": self._pseudo_iban(a), "currency": "GEL"},
                "transactions": {"booked": booked, "pending": [],
                                 "_links": {"account": self.href(f"/v2/loans/{a.id}")}}}

    # ---- savings
    def ser_saving(self, ctx: Ctx, a: Account) -> dict[str, Any]:
        d = self.ser_account(ctx, a, True)
        dep = self.world.deposit_of(a.id)
        d["cashAccountType"] = "SVGS"
        if dep:
            d["interest"] = [{"type": "FIXD", "rate": [{"percentage": f"{dep.rate * 100:.2f}"}]}]
            d["relatedDates"] = [{"contractStartDate": dep.start.isoformat(), **(
                {"contractEndDate": dep.maturity.isoformat()} if dep.maturity else {})}]
        d["_links"] = {"balances": self.href(f"/v2/savings/{a.id}/balances"),
                       "transactions": self.href(f"/v2/savings/{a.id}/transactions")}
        return d

    def op_savings_list(self, ctx: Ctx) -> dict:
        return {"accounts": [self.ser_saving(ctx, a) for a in self.savings_of(ctx)]}

    def op_saving_get(self, ctx: Ctx, accountId: str) -> dict:
        a = self.get_account(ctx, accountId, ("savings_term", "savings_flex"))
        return {"account": self.ser_saving(ctx, a)}

    def op_saving_balances(self, ctx: Ctx, accountId: str) -> dict:
        a = self.get_account(ctx, accountId, ("savings_term", "savings_flex"))
        return {"account": {"iban": a.iban, "currency": a.currency},
                "balances": self.balance_list(a, ("closingBooked", "interimAvailable"))}

    def op_saving_transactions(self, ctx: Ctx, accountId: str) -> dict:
        a = self.get_account(ctx, accountId, ("savings_term", "savings_flex"))
        return self.transactions_response(ctx, a, f"/v2/savings/{a.id}/transactions")


# ============================================================================== OB Home family
class ObHomeDialect(Dialect):
    """Credo / Halyk / Basisbank: one shared BG 1.3.9 spec; base URL already ends in /0.8/v1."""

    key = "obhome"
    prefixes = ("/0.8/v1", "/sandbox/0.8/v1", "/v1", "")
    api_v = ""
    txn_detail = False
    oauth_meta = "/ob-oidc/.well-known/openid-configuration"

    def route_table(self) -> Routes:
        pis = [e for e in pis_routes("", put=False) if e[2] != "pis_cancel_auth_start"]
        return (ais_routes("", detail=False) +
                consent_routes("/consents", put=False, post_auth=False) + pis + [
                    ("GET", "/ob-oidc/.well-known/openid-configuration", "oauth_metadata"),
                    ("GET", "/ob-oidc/authorize", "oauth_authorize"),
                    ("POST", "/ob-oidc/token", "oauth_token"),
                    ("POST", "/ob-home/api-analytics/get-analytics-kpi", "kpi"),
                ])

    def op_oauth_metadata(self, ctx: Ctx) -> dict:
        b = ctx.req.base_url
        return {
            "issuer": f"{b}/ob-oidc", "authorization_endpoint": f"{b}/ob-oidc/authorize",
            "token_endpoint": f"{b}/ob-oidc/token", "scopes_supported": ["openid"],
            "response_types_supported": ["code"], "subject_types_supported": ["pairwise"],
            "grant_types_supported": ["authorization_code", "refresh_token",
                                      "urn:openid:params:grant-type:ciba"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            "code_challenge_methods_supported": ["S256"], "ui_locales_supported": ["en", "ka"],
            "id_token_signing_alg_values_supported": ["RS512"],
        }

    def op_kpi(self, ctx: Ctx) -> dict:
        """Portal analytics feed (POST /ob-home/api-analytics/get-analytics-kpi)."""
        k = self.bank.kpi
        rng = np.random.default_rng(int(hashlib.sha1(self.bank.key.encode()).hexdigest()[:8], 16))
        today = self.world.as_of

        def rows(scale: float) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            y, m = today.year, today.month - 1  # JS-style 0-based month, as the real feed
            for _ in range(13):
                up = float(min(100.0, max(97.0, rng.normal(k.uptime_pct, 0.25))))
                out.append({
                    "year": y, "month": m, "upTime": up, "downTime": 100.0 - up,
                    "averageAISResponseTime": float(k.ais_ms * scale * rng.lognormal(0, 0.2)),
                    "averagePISResponseTime": float(k.pis_ms * scale * rng.lognormal(0, 0.2)),
                    "errorPercent": float(max(0.0, rng.normal(k.error_pct * scale, 0.01)))})
                m -= 1
                if m < 0:
                    m, y = 11, y - 1
            return out

        return {"sandboxData": rows(0.4), "productionData": rows(1.0)}


# ============================================================================== ProCredit
class ProCreditDialect(Dialect):
    """ProCredit Bank Georgia: 12 documented operations, JSON-number amounts, APIM key + JWS."""

    key = "procredit"
    prefixes = ("/0.8/v1", "/v1", "")
    api_v = ""
    rich_fields = False  # examples only: no bankTransactionCode/purposeCode/balance-after published
    amount_number = True
    card_accounts = False
    txn_detail = False
    balance_types_list = ("interimAvailable", "closingBooked")
    payment_services = ("payments", "bulk-payments")
    payment_products = ("domestic", "foreign")

    def route_table(self) -> Routes:
        pis = pis_routes("", auth=False, cancel_auth=False)
        return (ais_routes("", cards=False, detail=False) +
                consent_routes("/consents", post_auth=False, auth_get=False, put=False) +
                pis + oauth_routes())

    def check_extra_auth(self, ctx: Ctx) -> None:
        key = ctx.req.header("ocp-apim-subscription-key") or ctx.req.query.get("subscription-key")
        if not key:
            raise ApiError(401, "UNAUTHORIZED", "Access denied due to missing subscription key. "
                           "Make sure to include subscription key when making requests to an API.")

    def error_response(self, err: ApiError, req: Request, rid: str) -> Response:
        if err.code == "UNAUTHORIZED":
            return Response(401, {"statusCode": 401, "message": err.text})
        return super().error_response(err, req, rid)

    def op_pis_get(self, ctx: Ctx, payment_service: str, payment_product: str,
                   paymentId: str) -> dict:
        out = super().op_pis_get(ctx, payment_service, payment_product, paymentId)
        ia = out.get("instructedAmount")
        if isinstance(ia, dict):  # request uses `value`, the GET response uses `amount` (numbers)
            out["instructedAmount"] = {"currency": ia.get("currency", "GEL"),
                                       "amount": float(ia.get("amount", ia.get("value", 0)))}
        return out

    def res_id(self, a: Account) -> str:
        return str(int(hashlib.sha1(a.id.encode()).hexdigest()[:8], 16) % 100_000_000)

    def ser_account(self, ctx: Ctx, a: Account, with_balances: bool) -> dict[str, Any]:
        return {
            "resourceId": self.res_id(a), "iban": a.iban, "currency": a.currency, "name": a.iban,
            "product": "მიმდინარე ანგარიში" if a.kind == "current" else "ვალუტის ანგარიში",
            "status": "enabled", "bic": self.bank.bic,
            "balances": self.balance_list(a, self.balance_types_embedded) if with_balances else [],
            "_links": self.account_links(a),
        }

    def ser_balance(self, a: Account, btype: str, cents: int) -> dict[str, Any]:
        return {"balanceAmount": self.money(cents, a.currency), "balanceType": btype}

    def narr(self, t: Txn) -> str:
        stamp = t.ts.strftime("%d/%m/%Y %H:%M:%S")
        if t.channel in ("POS", "ECOM"):
            return f"ყიდვა/Purchase via POS terminal, {t.merchant}, {t.pan_masked}, {stamp}.-."
        if t.channel == "ATM":
            return f"ნაღდი ფულის გაცემა/ATM cash withdrawal, {t.merchant}, {t.pan_masked}, {stamp}.-."
        return f"{t.narrative_ka}/{t.narrative_en}"

    def ser_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d: dict[str, Any] = {
            "entryReference": t.ref, "valueDate": t.value_date.isoformat(),
            "transactionAmount": self.money(t.amount, t.currency)}
        if t.status == "booked":  # a pending entry has no booking date yet
            d["bookingDate"] = t.booking_date.isoformat()
        name = self.person_name(t)
        side = "creditor" if t.amount < 0 else "debtor"
        if name:
            d[f"{side}Name"] = name
        if t.cp_iban:
            d[f"{side}Account"] = {"iban": t.cp_iban}
        d["remittanceInformationUnstructured"] = self.narr(t)
        return d


# ============================================================================== Paysera
class PayseraDialect(Dialect):
    """Paysera Bank Georgia (Open Banking for Georgia v0.8, hand-written docs, mTLS, no signing)."""

    key = "paysera"
    prefixes = ("/xs2a/georgia/0.8/v1", "/0.8/v1", "/v1", "")
    api_v = ""
    rich_fields = False  # hand-written docs only: no standard-coded transaction fields
    card_accounts = False
    paging = "offset"
    links_in_report = False
    tz_suffix = "+03:00"
    balance_types_list = ("interimAvailable", "interimBooked")
    balance_types_embedded = ("interimAvailable", "interimBooked")
    payment_services = ("payments",)
    payment_products = ("domestic", "foreign")
    cancel_supported = False
    oauth_meta = "/.well-known/oauth-authorization-server"

    def route_table(self) -> Routes:
        b = "/payments/{payment-product}"
        fx = {"payment_service": "payments"}
        pis = [
            ("POST", b, "pis_create", fx), ("GET", f"{b}/{{paymentId}}", "pis_get", fx),
            ("GET", f"{b}/{{paymentId}}/status", "pis_status", fx),
            ("DELETE", f"{b}/{{paymentId}}", "pis_cancel", fx),
            ("GET", f"{b}/{{paymentId}}/authorisations", "pis_auth_list", fx),
            ("GET", f"{b}/{{paymentId}}/authorisations/{{authorisationId}}", "pis_auth_get", fx),
            ("POST", f"{b}/{{paymentId}}/authorisations", "pis_auth_start", fx),
            ("PUT", f"{b}/{{paymentId}}/authorisations/{{authorisationId}}", "pis_auth_update", fx),
            # documented but answered with 405 SERVICE_INVALID ("cancellation not supported")
            ("POST", f"{b}/{{paymentId}}/cancellation-authorisations", "pis_cancel_auth_start", fx),
            ("GET", f"{b}/{{paymentId}}/cancellation-authorisations", "pis_cancel_auth_list", fx),
            ("GET", f"{b}/{{paymentId}}/cancellation-authorisations/{{authorisationId}}",
             "pis_cancel_auth_get", fx),
            ("PUT", f"{b}/{{paymentId}}/cancellation-authorisations/{{authorisationId}}",
             "pis_cancel_auth_update", fx),
        ]
        return (ais_routes("", cards=False) + consent_routes("/consents") + pis + [
            ("GET", "/.well-known/oauth-authorization-server", "oauth_metadata"),
            ("GET", "/oauth/authorize", "oauth_authorize"),
            ("POST", "/oauth/token", "oauth_token")])

    def op_oauth_metadata(self, ctx: Ctx) -> dict:
        # Paysera publishes host-relative URLs; under the mock's /paysera prefix they must be
        # prefixed or a client following them would leave the mock.
        b = ctx.req.base_url
        return {"issuer": b, "authorization_endpoint": f"{b}/oauth/authorize",
                "token_endpoint": f"{b}/oauth/token", "jwks_uri": f"{b}/.well-known/jwks.json",
                "scopes_supported": ["profile", "AIS:<consentId>", "PIS:<paymentId>", "PSP_AI",
                                     "PSP_PI"],
                "response_types_supported": ["code", "token"],
                "response_modes_supported": ["query"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"]}

    def op_consent_auth_start(self, ctx: Ctx, **_: str) -> Response:
        raise ApiError(405, "SERVICE_INVALID", "with redirect over OAuth the authorisation is "
                       "created together with the consent")

    op_consent_auth_update = op_consent_auth_start

    def op_pis_auth_start(self, ctx: Ctx, **_: str) -> Response:
        raise ApiError(405, "SERVICE_INVALID", "with redirect over OAuth the authorisation is "
                       "created together with the payment")

    op_pis_auth_update = op_pis_auth_start

    def op_pis_cancel_auth_start(self, ctx: Ctx, **_: str) -> Response:
        raise ApiError(405, "SERVICE_INVALID", "payment cancellation is not supported")

    op_pis_cancel_auth_list = op_pis_cancel_auth_start
    op_pis_cancel_auth_get = op_pis_cancel_auth_start
    op_pis_cancel_auth_update = op_pis_cancel_auth_start

    def ser_account(self, ctx: Ctx, a: Account, with_balances: bool) -> dict[str, Any]:
        d: dict[str, Any] = {
            "resourceId": a.id, "iban": a.iban, "currency": a.currency,
            "product": "Current Account", "cashAccountType": "CACC",
            "name": "Main Account" if a.kind == "current" else "FX Account"}
        if with_balances:
            d["balances"] = self.balance_list(a, self.balance_types_embedded)
        d["_links"] = self.account_links(a)
        return d

    def _query_filters(self, ctx: Ctx) -> dict[str, Any]:
        for bad in ("entryReferenceFrom", "deltaList", "withBalance"):
            if bad in ctx.req.query:
                raise ApiError(400, "PARAMETER_NOT_SUPPORTED", f"parameter {bad} is not supported")
        return super()._query_filters(ctx)

    def ser_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d = super().ser_txn(ctx, a, t)
        for k in ("endToEndId", "additionalInformation", "entryReference", "currencyExchange",
                  "creditorAgent", "debtorAgent"):
            d.pop(k, None)
        if t.channel == "TRANSFER":
            same = bool(t.cp_iban) and t.cp_iban[4:6] == self.bank.iban_code
            d["proprietaryBankTransactionCode"] = ("transfer:internal" if same
                                                   else "transfer:ge_nbg_rtgs")
        else:
            d.pop("proprietaryBankTransactionCode", None)
        return d


__all__ = ["TbcDialect", "BogDialect", "AltaSoftV08Dialect", "AltaSoftV2Dialect", "ObHomeDialect",
           "ProCreditDialect", "PayseraDialect"]
