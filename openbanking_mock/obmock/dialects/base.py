"""Dialect base: the Georgian XS2A profile (NextGenPSD2 1.3.x as adapted by NBG, "0.8").

Everything shared by the banks lives here. A bank dialect overrides only what its published
contract/examples show to be different: routes, header names, field names, error body, which
products exist. Behaviour rules come from the Georgian XS2A Implementation Guide 0.8.RC3:
pages of <=50 entries with `_links.first/next`, `bookingStatus` required, `entryReferenceFrom`
(strictly-after), balances (`closingBooked`+`interimAvailable`), consents (<=4/day, 90 days).
"""
from __future__ import annotations

import base64
import hashlib
import inspect
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

from obmock.banks import BankProfile
from obmock.engine.gateway import (
    ApiError,
    Authorisation,
    Consent,
    Ctx,
    Payment,
    Request,
    Response,
    Route,
    new_id,
    utcnow,
)
from obmock.iso20022 import classify
from obmock.util import decode_state, encode_state, fmt_amount, stable_uuid
from obmock.world.model import Account, Txn, World

PIS_FINAL_OK = ("ACCC", "ACSC")
# v2 "extended" payment services: the authorisation creates a consent, execution happens later
# through /initiations (deferred, recurring, secured).
EXTENDED_SERVICES = (
    "deferred-payments", "secured-deferred-payments", "multiple-deferred-payments",
    "secured-multiple-deferred-payments", "recurring-payments", "multiple-recurring-payments",
    "secured-recurring-payments", "secured-payments",
)


@dataclass
class CardRes:
    id: str
    acct: Account
    kind: str  # debit | credit

    @property
    def pan(self) -> str:
        return self.acct.pan_masked or ""


def ok(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(status, body, headers or {})


# ----------------------------------------------------------------------------- route builders
def ais_routes(v: str = "/v1", cards: bool = True,
               detail: bool = True) -> list[tuple[str, str, str]]:
    r = [
        ("GET", f"{v}/accounts", "accounts_list"),
        ("GET", f"{v}/accounts/{{account-id}}", "account_get"),
        ("GET", f"{v}/accounts/{{account-id}}/balances", "account_balances"),
        ("GET", f"{v}/accounts/{{account-id}}/transactions", "account_transactions"),
    ]
    if detail:
        r.append(("GET", f"{v}/accounts/{{account-id}}/transactions/{{transactionId}}",
                  "account_transaction"))
    if cards:
        r += [
            ("GET", f"{v}/card-accounts", "cards_list"),
            ("GET", f"{v}/card-accounts/{{account-id}}", "card_get"),
            ("GET", f"{v}/card-accounts/{{account-id}}/balances", "card_balances"),
            ("GET", f"{v}/card-accounts/{{account-id}}/transactions", "card_transactions"),
        ]
    return r


def consent_routes(base: str = "/v1/consents", auth_base: str | None = None, put: bool = True,
                   post_auth: bool = True, auth_get: bool = True) -> list[tuple[str, str, str]]:
    ab = auth_base or base
    r = [
        ("POST", base, "consent_create"),
        ("GET", f"{base}/{{consentId}}", "consent_get"),
        ("DELETE", f"{base}/{{consentId}}", "consent_delete"),
        ("GET", f"{base}/{{consentId}}/status", "consent_status"),
    ]
    if post_auth:
        r.append(("POST", f"{ab}/{{consentId}}/authorisations", "consent_auth_start"))
    if auth_get:
        r += [("GET", f"{ab}/{{consentId}}/authorisations", "consent_auth_list"),
              ("GET", f"{ab}/{{consentId}}/authorisations/{{authorisationId}}", "consent_auth_get")]
    if put:
        r.append(("PUT", f"{ab}/{{consentId}}/authorisations/{{authorisationId}}",
                  "consent_auth_update"))
    return r


def pis_routes(v: str = "/v1", put: bool = True, cancel_auth: bool = True,
               auth: bool = True) -> list[tuple[str, str, str]]:
    b = f"{v}/{{payment-service}}/{{payment-product}}"
    r = [
        ("POST", b, "pis_create"),
        ("GET", f"{b}/{{paymentId}}", "pis_get"),
        ("GET", f"{b}/{{paymentId}}/status", "pis_status"),
        ("DELETE", f"{b}/{{paymentId}}", "pis_cancel"),
    ]
    if auth:
        r += [("POST", f"{b}/{{paymentId}}/authorisations", "pis_auth_start"),
              ("GET", f"{b}/{{paymentId}}/authorisations", "pis_auth_list"),
              ("GET", f"{b}/{{paymentId}}/authorisations/{{authorisationId}}", "pis_auth_get")]
        if put:
            r.append(("PUT", f"{b}/{{paymentId}}/authorisations/{{authorisationId}}",
                      "pis_auth_update"))
    if cancel_auth:
        r += [("POST", f"{b}/{{paymentId}}/cancellation-authorisations", "pis_cancel_auth_start"),
              ("GET", f"{b}/{{paymentId}}/cancellation-authorisations", "pis_cancel_auth_list"),
              ("GET", f"{b}/{{paymentId}}/cancellation-authorisations/{{authorisationId}}",
               "pis_cancel_auth_get")]
        if put:
            r.append(("PUT", f"{b}/{{paymentId}}/cancellation-authorisations/{{authorisationId}}",
                      "pis_cancel_auth_update"))
    return r


def oauth_routes() -> list[tuple[str, str, str]]:
    return [
        ("GET", "/.well-known/oauth-authorization-server", "oauth_metadata"),
        ("GET", "/openbanking/oauth/.well-known/oauth-authorization-server", "oauth_metadata"),
        ("GET", "/psd2/openbanking/oauth/authorize", "oauth_authorize"),
        ("POST", "/openbanking/oauth/token", "oauth_token"),
    ]


class Dialect:
    # ---- identity / URL space
    key = "georgian"
    prefixes: tuple[str, ...] = ("/0.8", "")  # accepted prefixes; first is canonical for links
    consent_family = "v1"  # v1 -> validUntil, v2 -> validTo/consentType
    consent_headers: tuple[str, ...] = ("consent-id",)  # accepted spellings, first is canonical
    require_psu_ip = False
    api_v = "/v1"  # version segment inside the API ("" when the base URL already ends in /v1)
    explicit_auth = False  # True: consent create returns startAuthorisation (TBC)
    sca_link = "scaOAuth"  # scaOAuth | scaRedirect
    oauth_meta = "/openbanking/oauth/.well-known/oauth-authorization-server"
    # ---- capabilities
    card_accounts = True
    txn_detail = True
    loans = False
    savings = False
    # ---- AIS behaviour
    booking_status_required = True
    page_size = 50
    paging = "ref"  # ref | state | offset
    links_in_report = True  # False: `_links` at response root (Paysera)
    amount_number = False  # True: amounts as JSON numbers (ProCredit)
    link_style = "path"  # path -> "/0.8/v1/..." ; relative -> "v1/..."
    balance_types_list = ("closingBooked", "interimAvailable")
    balance_types_embedded = ("interimAvailable", "interimBooked")
    tz_suffix = ""
    owner_key = "ownerName"
    max_consent_days = 90
    max_freq = 4
    payment_products: tuple[str, ...] = ("aspsp", "domestic", "foreign")
    payment_services: tuple[str, ...] = ("payments", "bulk-payments", "periodic-payments")
    cancel_supported = True
    # True when the bank's schema defines the standard-coded fields (bankTransactionCode,
    # purposeCode, mandateId, balanceAfterTransaction); the "evidence-only" profile still drops them.
    rich_fields = True
    # True: FX sub-accounts show the IBAN of the current account (one multi-currency account, as in
    # TBC's examples and BoG's spec examples); the account is then keyed by (iban, currency).
    shared_fx_iban = False

    def __init__(self, bank: BankProfile, world: World):
        self.bank, self.world = bank, world

    @property
    def rich(self) -> bool:
        return self.rich_fields and self.world.cfg.profile == "spec-complete"

    # ================================================================== routing
    def route_table(self) -> list[tuple]:
        v = self.api_v
        return (ais_routes(v) + consent_routes(f"{v}/consents") + pis_routes(v) + oauth_routes())

    def routes(self) -> list[Route]:
        """Entries are (method, template, op) or (method, template, op, fixed_params)."""
        out = []
        for e in self.route_table():
            out.append(Route(e[0], e[1], e[2], alias=e[2].endswith("_alias"),
                             fixed=dict(e[3]) if len(e) > 3 else {}))
        return out

    @property
    def canonical_prefix(self) -> str:
        return self.prefixes[0]

    @property
    def consent_header(self) -> str:
        return self.consent_headers[0]

    @property
    def accounts_path(self) -> str:
        return f"{self.api_v}/accounts"

    @property
    def consent_base(self) -> str:
        return f"{self.api_v}/consents"

    def check_extra_auth(self, ctx: Ctx) -> None:
        """Bank-specific gateway credentials beyond Bearer + consent (e.g. APIM key)."""

    def iban_of(self, a: Account) -> str | None:
        if self.shared_fx_iban and a.kind == "fx":
            cur = self.world.accounts_of(a.bank, a.cid, ("current",))
            return cur[0].iban if cur else a.iban
        return a.iban

    def res_id(self, a: Account) -> str:
        return a.id

    @property
    def consent_auth_base(self) -> str:
        return self.consent_base

    def normalize_path(self, path: str) -> str:
        path = "/" + path.strip("/") if path.strip("/") else "/"
        for p in sorted(self.prefixes, key=len, reverse=True):
            if p and (path == p or path.startswith(p + "/")):
                return path[len(p):] or "/"
        return path

    def auth_category(self, op: str) -> str:
        if op.startswith(("oauth_", "products", "kpi")):
            return "public"
        if op.startswith("consent_"):
            return "consent"
        if op.startswith(("pis", "payment")):
            return "pis"
        return "data"

    def dispatch(self, ctx: Ctx, route: Route) -> Response:
        fn = getattr(self, f"op_{route.op.removesuffix('_alias')}", None)
        if fn is None:
            raise ApiError(501, "SERVICE_INVALID", f"{route.op} is documented but not simulated")
        kwargs = {k.replace("-", "_"): v for k, v in ctx.params.items()}
        sig = inspect.signature(fn).parameters
        takes_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.values())
        kwargs.update({k: v for k, v in route.fixed.items() if takes_any or k in sig})
        out = fn(ctx, **kwargs)
        return out if isinstance(out, Response) else ok(out)

    # ================================================================== errors
    def error_response(self, err: ApiError, req: Request, rid: str) -> Response:
        body = {"tppMessages": [{"category": err.category, "code": err.code, "text": err.text}]}
        return Response(err.status, body, dict(err.headers))

    # ================================================================== small helpers
    def link(self, path: str) -> str:
        if self.link_style == "relative":
            return path.lstrip("/")
        return f"{self.canonical_prefix}{path}"

    def href(self, path: str) -> dict[str, str]:
        return {"href": self.link(path)}

    def money(self, cents: int, ccy: str) -> dict[str, Any]:
        amt: Any = round(cents / 100.0, 2) if self.amount_number else fmt_amount(cents)
        return {"currency": ccy, "amount": amt}

    def narr(self, t: Txn) -> str:
        return t.narrative_ka if self.bank.narrative_lang == "ka" else t.narrative_en

    def person_name(self, t: Txn) -> str | None:
        if self.bank.name_script == "ka" and t.cp_name_ka:
            return t.cp_name_ka
        return t.cp_name or t.merchant

    def owner_name(self, a: Account) -> str:
        c = self.world.customers[a.cid]
        return c.name_ka if self.bank.name_script == "ka" else c.name_en

    @staticmethod
    def q_bool(ctx: Ctx, name: str) -> bool:
        return str(ctx.req.query.get(name, "")).lower() == "true"

    @staticmethod
    def parse_date(v: str | None, field: str) -> date | None:
        if not v:
            return None
        try:
            return date.fromisoformat(v[:10])
        except ValueError as e:
            raise ApiError(400, "FORMAT_ERROR", f"{field} is invalid, expected YYYY-MM-DD") from e

    def iso_ts(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + self.tz_suffix

    @staticmethod
    def body(ctx: Ctx) -> dict:
        b = ctx.req.body
        if b is None:
            return {}
        if not isinstance(b, dict):
            raise ApiError(400, "FORMAT_ERROR", "request body must be a JSON object")
        return b

    # ================================================================== consent scope
    @staticmethod
    def _scan_ibans(obj: Any, out: set[str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "iban" and isinstance(v, str):
                    out.add(v)
                else:
                    Dialect._scan_ibans(v, out)
        elif isinstance(obj, list):
            for v in obj:
                Dialect._scan_ibans(v, out)

    def consent_scope(self, consent: Consent | None) -> tuple[bool, set[str]]:
        """(covers_everything, explicit identifiers) for a consent."""
        if consent is None:
            return True, set()
        acc = consent.access or {}
        if acc.get("availableAccounts") or acc.get("availableAccountsWithBalance") or \
                acc.get("allPsd2") or consent.consent_type in ("global", "aspspManaged"):
            return True, set()
        ibans: set[str] = set()
        self._scan_ibans(acc, ibans)
        return (not ibans), ibans

    def visible_accounts(self, ctx: Ctx, kinds: tuple[str, ...]) -> list[Account]:
        assert ctx.customer is not None
        accts = self.world.accounts_of(self.bank.key, ctx.customer.cid, kinds)
        everything, ids = self.consent_scope(ctx.consent)
        if everything:
            return accts
        return [a for a in accts
                if self.iban_of(a) in ids or a.iban in ids or a.meta.get("contract_no") in ids
                or a.id in ids]

    def get_account(self, ctx: Ctx, rid: str, kinds: tuple[str, ...]) -> Account:
        for a in self.visible_accounts(ctx, kinds):
            if self.res_id(a) == rid:
                return a
        raise ApiError(404, "RESOURCE_UNKNOWN", "account not found")

    # ================================================================== balances
    @staticmethod
    def last_change(a: Account) -> datetime | None:
        booked = [t.ts for t in a.txns if t.status == "booked"]
        return max(booked) if booked else None

    def ser_balance(self, a: Account, btype: str, cents: int) -> dict[str, Any]:
        as_of = self.world.as_of
        ref = as_of if btype in ("interimBooked", "interimAvailable", "expected") \
            else as_of - timedelta(days=1)
        b: dict[str, Any] = {"balanceAmount": self.money(cents, a.currency), "balanceType": btype}
        if btype == "interimAvailable":
            b["creditLimitIncluded"] = a.credit_limit > 0
        b["referenceDate"] = ref.isoformat()
        lc = self.last_change(a)
        if lc is not None and btype in ("interimBooked", "interimAvailable"):
            b["lastChangeDateTime"] = self.iso_ts(lc)
        return b

    def balance_list(self, a: Account, types: tuple[str, ...]) -> list[dict[str, Any]]:
        bal = self.world.balances(a)
        return [self.ser_balance(a, t, bal[t]) for t in types if t in bal]

    # ================================================================== accounts
    @staticmethod
    def account_kinds() -> tuple[str, ...]:
        return ("current", "fx")

    def account_links(self, a: Account) -> dict[str, Any]:
        base = f"{self.api_v}/accounts/{self.res_id(a)}"
        return {"balances": self.href(f"{base}/balances"),
                "transactions": self.href(f"{base}/transactions")}

    @staticmethod
    def cashtype(a: Account) -> str:
        return "CACC"

    def ser_account(self, ctx: Ctx, a: Account, with_balances: bool) -> dict[str, Any]:
        d: dict[str, Any] = {
            "resourceId": self.res_id(a), "iban": self.iban_of(a), "currency": a.currency,
            self.owner_key: self.owner_name(a), "name": a.name or self.owner_name(a),
            "product": a.product, "cashAccountType": self.cashtype(a), "status": "enabled",
            "bic": self.bank.bic, "usage": a.usage,
        }
        if with_balances:
            d["balances"] = self.balance_list(a, self.balance_types_embedded)
        d["_links"] = self.account_links(a)
        return d

    def account_entries(self, ctx: Ctx, accts: list[Account], with_balances: bool) -> list[dict]:
        return [self.ser_account(ctx, a, with_balances) for a in accts]

    def op_accounts_list(self, ctx: Ctx) -> dict:
        accts = self.visible_accounts(ctx, self.account_kinds())
        return {"accounts": self.account_entries(ctx, accts, self.q_bool(ctx, "withBalance"))}

    def op_account_get(self, ctx: Ctx, account_id: str) -> dict:
        a = self.get_account(ctx, account_id, self.account_kinds())
        return {"account": self.ser_account(ctx, a, self.q_bool(ctx, "withBalance"))}

    def op_account_balances(self, ctx: Ctx, account_id: str) -> dict:
        a = self.get_account(ctx, account_id, self.account_kinds())
        return {"account": {"iban": self.iban_of(a), "currency": a.currency},
                "balances": self.balance_list(a, self.balance_types_list)}

    # ================================================================== transactions
    def txid(self, a: Account, t: Txn) -> str:
        # same digits as entryReference: unique bank-wide, never derived from simulator internals
        return f"{'c' if t.amount > 0 else 'd'}_{t.ref}"

    def proprietary_code(self, t: Txn) -> str | None:
        """Bank-specific proprietary transaction code; None unless the bank published its scheme."""
        return None

    def fx_exchange(self, t: Txn) -> list[dict[str, Any]]:
        """Berlin Group `currencyExchange`. Rates are stored as GEL per one unit of the foreign
        currency, so the unit currency is whichever side of the conversion is not GEL."""
        unit = t.fx_src_ccy if t.fx_src_ccy != "GEL" else t.currency
        return [{
            "sourceCurrency": t.fx_src_ccy, "exchangeRate": f"{t.fx_rate:.4f}",
            "unitCurrency": unit, "targetCurrency": t.currency,
            "quotationDate": t.value_date.isoformat()}]

    @staticmethod
    def mandate_id(a: Account) -> str:
        return "MNDT" + hashlib.sha1(a.id.encode()).hexdigest()[:10].upper()

    def std_codes(self, a: Account, t: Txn) -> dict[str, Any]:
        """ISO 20022 bankTransactionCode / purposeCode / mandateId for spec-complete profiles."""
        if not self.rich:
            return {}
        # no counterparty information (e.g. an FX leg) means the other side is the same bank
        internal = not (t.cp_iban or t.cp_name) or (
            bool(t.cp_iban) and t.cp_iban[4:6] == self.bank.iban_code) or t.cp_name == self.bank.name_en
        iso = classify(t.kind, t.amount, a.kind, internal)
        if iso is None:
            return {}
        out: dict[str, Any] = {"bankTransactionCode": iso.bktx}
        if iso.purpose:
            out["purposeCode"] = iso.purpose
        if iso.direct_debit:
            out["mandateId"] = self.mandate_id(a)
        return out

    def balance_after(self, a: Account, t: Txn) -> dict[str, Any]:
        return {"balanceAmount": self.money(t.balance_after or 0, a.currency),
                "balanceType": "interimBooked", "referenceDate": t.booking_date.isoformat()}

    def ser_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d: dict[str, Any] = {
            "transactionId": self.txid(a, t), "entryReference": t.ref,
            "valueDate": t.value_date.isoformat(),
            "transactionAmount": self.money(t.amount, t.currency),
        }
        if t.status == "booked":  # a pending entry has no booking date yet
            d["bookingDate"] = t.booking_date.isoformat()
        if t.e2e:
            d["endToEndId"] = t.e2e
        name = self.person_name(t)
        side = "creditor" if t.amount < 0 else "debtor"
        if name:
            d[f"{side}Name"] = name
        if t.cp_iban:
            d[f"{side}Account"] = {"iban": t.cp_iban}
        if t.cp_bic:
            d[f"{side}Agent"] = t.cp_bic
        if t.fx_src_ccy and t.fx_rate:
            d["currencyExchange"] = self.fx_exchange(t)
        d["remittanceInformationUnstructured"] = self.narr(t)
        d.update(self.std_codes(a, t))
        if self.rich and t.balance_after is not None:
            d["balanceAfterTransaction"] = self.balance_after(a, t)
        code = self.proprietary_code(t)
        if code:
            d["proprietaryBankTransactionCode"] = code
        return d

    def _query_filters(self, ctx: Ctx) -> dict[str, Any]:
        q = ctx.req.query
        if "state" in q and self.paging == "state":
            try:
                return decode_state(q["state"])
            except Exception as e:  # noqa: BLE001
                raise ApiError(400, "FORMAT_ERROR", "state parameter is invalid") from e
        status = q.get("bookingStatus")
        if self.booking_status_required and not status:
            raise ApiError(400, "FORMAT_ERROR", "bookingStatus query parameter is required")
        status = status or "both"
        if status not in ("booked", "pending", "both", "information", "all"):
            raise ApiError(400, "FORMAT_ERROR", f"bookingStatus '{status}' is invalid")
        if status in ("information", "all"):
            raise ApiError(400, "PARAMETER_NOT_SUPPORTED", f"bookingStatus '{status}' not supported")
        df = self.parse_date(q.get("dateFrom"), "dateFrom")
        dt = self.parse_date(q.get("dateTo"), "dateTo")
        if df and dt and df > dt:
            raise ApiError(400, "PERIOD_INVALID", "dateFrom must not be after dateTo")
        try:
            off = int(q.get("offset", 0) or 0)
        except ValueError as e:
            raise ApiError(400, "FORMAT_ERROR", "offset must be an integer") from e
        return {"bs": status, "df": df.isoformat() if df else None,
                "dt": dt.isoformat() if dt else None, "wb": self.q_bool(ctx, "withBalance"),
                "ref": q.get("entryReferenceFrom"), "off": off}

    def _page(self, a: Account, f: dict[str, Any]) -> tuple[list[Txn], bool, dict]:
        df = date.fromisoformat(f["df"]) if f.get("df") else None
        dt = date.fromisoformat(f["dt"]) if f.get("dt") else None
        ents = self.world.entries(a, df, dt, f["bs"])
        start = 0
        if self.paging == "ref":
            if f.get("ref"):
                ents = [t for t in ents if t.ref > f["ref"]]
        else:
            start = int(f.get("off", 0))
        page = ents[start:start + self.page_size]
        more = len(ents) > start + self.page_size
        nf = dict(f)
        if more:
            if self.paging == "ref":
                nf["ref"] = page[-1].ref
            else:
                nf["off"] = start + self.page_size
        return page, more, nf

    def _txn_url(self, base_path: str, nf: dict[str, Any]) -> str:
        if self.paging == "state":
            return self.link(f"{base_path}?state={encode_state(nf)}")
        params = {"bookingStatus": nf["bs"]}
        if nf.get("df"):
            params["dateFrom"] = nf["df"]
        if nf.get("dt"):
            params["dateTo"] = nf["dt"]
        if nf.get("wb"):
            params["withBalance"] = "true"
        if self.paging == "ref" and nf.get("ref"):
            params["entryReferenceFrom"] = nf["ref"]
        if self.paging == "offset" and nf.get("off"):
            params["offset"] = str(nf["off"])
        return self.link(f"{base_path}?{urlencode(params)}")

    def transactions_response(self, ctx: Ctx, a: Account, base_path: str,
                              wrap: str = "transactions", ser: Any = None) -> dict:
        f = self._query_filters(ctx)
        page, more, nf = self._page(a, f)
        ser = ser or self.ser_txn
        first_f = dict(f)
        first_f["ref"], first_f["off"] = None, 0
        links: dict[str, Any] = {"account": self.href(f"{self.api_v}/accounts/{self.res_id(a)}"),
                                 "first": {"href": self._txn_url(base_path, first_f)}}
        if more:
            links["next"] = {"href": self._txn_url(base_path, nf)}
        report: dict[str, Any] = {}
        if f["bs"] in ("booked", "both"):
            report["booked"] = [ser(ctx, a, t) for t in page if t.status == "booked"]
        if f["bs"] in ("pending", "both"):
            report["pending"] = [ser(ctx, a, t) for t in page if t.status == "pending"]
        out: dict[str, Any] = {"account": {"iban": self.iban_of(a), "currency": a.currency}}
        if self.links_in_report:
            report["_links"] = links
        else:
            out["_links"] = links
        out[wrap] = report
        if f.get("wb"):
            out["balances"] = self.balance_list(a, self.balance_types_list)
        return out

    def op_account_transactions(self, ctx: Ctx, account_id: str) -> dict:
        a = self.get_account(ctx, account_id, self.account_kinds())
        return self.transactions_response(
            ctx, a, f"{self.api_v}/accounts/{self.res_id(a)}/transactions")

    def op_account_transaction(self, ctx: Ctx, account_id: str, transactionId: str) -> dict:
        a = self.get_account(ctx, account_id, self.account_kinds())
        for t in a.txns:
            if self.txid(a, t) == transactionId:
                return {"transactionsDetails": self.ser_txn(ctx, a, t)}
        raise ApiError(404, "RESOURCE_UNKNOWN", "transaction not found")

    # ================================================================== card accounts
    def cards(self, ctx: Ctx) -> list[CardRes]:
        out = []
        for a in self.visible_accounts(ctx, ("current", "card_credit")):
            if a.pan_masked:
                out.append(CardRes(stable_uuid("card", a.id), a,
                                   "credit" if a.kind == "card_credit" else "debit"))
        return out

    def get_card(self, ctx: Ctx, rid: str) -> CardRes:
        for c in self.cards(ctx):
            if c.id == rid:
                return c
        raise ApiError(404, "RESOURCE_UNKNOWN", "card account not found")

    def ser_card(self, ctx: Ctx, c: CardRes, with_balances: bool = True) -> dict[str, Any]:
        a = c.acct
        d: dict[str, Any] = {
            "resourceId": c.id, "maskedPan": c.pan, "currency": a.currency,
            "ownerName": self.owner_name(a), "name": self.owner_name(a),
            "product": f"{a.scheme} {'Credit' if c.kind == 'credit' else 'Debit'}",
            "status": "enabled", "usage": a.usage,
        }
        if a.credit_limit and c.kind == "credit":
            d["creditLimit"] = self.money(a.credit_limit, a.currency)
        if with_balances:
            d["balances"] = self.balance_list(a, self.balance_types_embedded)
        d["_links"] = {"balances": self.href(f"{self.api_v}/card-accounts/{c.id}/balances"),
                       "transactions": self.href(f"{self.api_v}/card-accounts/{c.id}/transactions")}
        return d

    def op_cards_list(self, ctx: Ctx) -> dict:
        return {"cardAccounts": [self.ser_card(ctx, c) for c in self.cards(ctx)]}

    def op_card_get(self, ctx: Ctx, account_id: str) -> dict:
        return {"cardAccount": self.ser_card(ctx, self.get_card(ctx, account_id))}

    def op_card_balances(self, ctx: Ctx, account_id: str) -> dict:
        c = self.get_card(ctx, account_id)
        return {"cardAccount": {"maskedPan": c.pan, "currency": c.acct.currency},
                "balances": self.balance_list(c.acct, self.balance_types_list)}

    def ser_card_txn(self, ctx: Ctx, a: Account, t: Txn) -> dict[str, Any]:
        d: dict[str, Any] = {
            "cardTransactionId": self.txid(a, t), "transactionDate": t.value_date.isoformat(),
            "transactionAmount": self.money(t.amount, t.currency),
            "acceptorTransactionDateTime": self.iso_ts(t.ts),
            "maskedPAN": t.pan_masked or a.pan_masked, "transactionDetails": self.narr(t),
        }
        if t.status == "booked":
            d["bookingDate"] = t.booking_date.isoformat()
        code = self.proprietary_code(t)
        if code:
            d["proprietaryBankTransactionCode"] = code
        if t.merchant:
            d["cardAcceptorId"] = (t.merchant or "")[:35]
        if t.mcc:
            d["merchantCategoryCode"] = t.mcc
        return d

    def op_card_transactions(self, ctx: Ctx, account_id: str) -> dict:
        c = self.get_card(ctx, account_id)
        a = c.acct
        view = Account(**{**a.__dict__, "txns": [t for t in a.txns if t.pan_masked]})
        out = self.transactions_response(
            ctx, view, f"{self.api_v}/card-accounts/{c.id}/transactions",
            wrap="cardTransactions", ser=self.ser_card_txn)
        out.pop("account", None)
        out["cardAccount"] = {"maskedPan": c.pan, "currency": a.currency}
        return out

    # ================================================================== loans / savings (v2)
    def loans_of(self, ctx: Ctx) -> list[Account]:
        return self.visible_accounts(ctx, ("loan",))

    def savings_of(self, ctx: Ctx) -> list[Account]:
        return self.visible_accounts(ctx, ("savings_term", "savings_flex"))

    # ================================================================== consents
    def _consent(self, ctx: Ctx, consent_id: str) -> Consent:
        c = ctx.sessions.consents.get(consent_id)
        if c is None or c.bank != self.bank.key:
            raise ApiError(403, "CONSENT_UNKNOWN", "unknown consent")
        return c

    def cbase(self, family: str) -> str:
        """Consent collection path for a consent family (v1/v2)."""
        return self.consent_base

    def cauth_base(self, family: str) -> str:
        """Path under which a consent's authorisation sub-resources live."""
        return self.consent_auth_base

    def v2_consent_label(self, c: Consent) -> str:
        return c.consent_type or "detailed"

    def consent_links(self, consent: Consent, auth: Authorisation | None) -> dict[str, Any]:
        base = f"{self.cbase(consent.family)}/{consent.id}"
        ab = f"{self.cauth_base(consent.family)}/{consent.id}/authorisations"
        if self.explicit_auth or auth is None:
            return {"startAuthorisation": self.href(ab)}
        return {"self": self.href(base), "status": self.href(f"{base}/status"),
                "scaStatus": self.href(f"{ab}/{auth.id}")}

    def _sca_href(self, ctx: Ctx, kind: str, parent_id: str, auth_id: str) -> dict[str, str]:
        base = ctx.req.base_url
        if self.sca_link == "scaRedirect":
            return {"href": f"{base}/_mock/sca/{kind}/{parent_id}/{auth_id}"}
        return {"href": f"{base}{self.oauth_meta}"}

    def op_consent_create(self, ctx: Ctx, family: str | None = None) -> Response:
        b = self.body(ctx)
        family = family or self.consent_family
        v2 = family == "v2"
        valid_key = "validTo" if v2 else "validUntil"
        required = ["access", "recurringIndicator", "frequencyPerDay"] + ([] if v2 else [valid_key])
        for f in required:
            if f not in b:
                raise ApiError(400, "FORMAT_ERROR", f"{f} is required")
        rec = b["recurringIndicator"]
        recurring = rec if isinstance(rec, bool) else str(rec).lower() == "true"
        try:
            valid = date.fromisoformat(str(b[valid_key])[:10]) if b.get(valid_key) \
                else date.today() + timedelta(days=self.max_consent_days)
        except ValueError as e:
            raise ApiError(400, "FORMAT_ERROR",
                           f"{valid_key[0].upper() + valid_key[1:]} parameter is invalid, "
                           "please check the date.") from e
        if valid < date.today():
            raise ApiError(400, "FORMAT_ERROR", f"{valid_key} is in the past")
        valid = min(valid, date.today() + timedelta(days=self.max_consent_days))
        try:
            freq = int(b["frequencyPerDay"])
        except (TypeError, ValueError) as e:
            raise ApiError(400, "FORMAT_ERROR", "frequencyPerDay must be an integer") from e
        if freq < 1:
            raise ApiError(400, "FORMAT_ERROR", "frequencyPerDay must be >= 1")
        freq = 1 if not recurring else min(freq, self.max_freq)
        consent = Consent(
            id=new_id(), bank=self.bank.key, family=family, status="received",
            access=b["access"] if isinstance(b["access"], dict) else {}, recurring=recurring,
            valid_until=valid, freq=freq, combined=bool(b.get("combinedServiceIndicator", False)),
            created=utcnow(), last_action=date.today(), redirect_uri=ctx.req.header("tpp-redirect-uri"),
            psu_id=ctx.req.header("psu-id"), consent_type=b.get("consentType"))
        ctx.sessions.consents[consent.id] = consent
        auth = None
        if not self.explicit_auth:
            auth = Authorisation(new_id(), consent.id, "consent")
            ctx.sessions.auths[auth.id] = auth
            consent.auth_ids.append(auth.id)
        links = self.consent_links(consent, auth)
        if auth is not None:
            links[self.sca_link] = self._sca_href(ctx, "consent", consent.id, auth.id)
        return ok({"consentStatus": "received", "consentId": consent.id, "_links": links}, 201,
                  {"Location": self.link(f"{self.cbase(family)}/{consent.id}"),
                   "ASPSP-SCA-Approach": "REDIRECT"})

    def op_consent_get(self, ctx: Ctx, consentId: str) -> dict:
        c = self._consent(ctx, consentId)
        if c.family == "v2":
            return {"access": c.access, "consentType": self.v2_consent_label(c),
                    "recurringIndicator": c.recurring, "validTo": c.valid_until.isoformat(),
                    "frequencyPerDay": c.freq, "consentStatus": c.status}
        return {"access": c.access, "recurringIndicator": c.recurring,
                "validUntil": c.valid_until.isoformat(), "frequencyPerDay": c.freq,
                "lastActionDate": c.last_action.isoformat(), "consentStatus": c.status,
                "_links": {"account": self.href(self.accounts_path)}}

    def op_consent_delete(self, ctx: Ctx, consentId: str) -> Response:
        c = self._consent(ctx, consentId)
        c.status = "terminatedByTpp"
        c.last_action = date.today()
        return Response(204, None)

    def op_consent_status(self, ctx: Ctx, consentId: str) -> dict:
        return {"consentStatus": self._consent(ctx, consentId).status}

    def op_consent_auth_start(self, ctx: Ctx, consentId: str) -> Response:
        c = self._consent(ctx, consentId)
        a = Authorisation(new_id(), c.id, "consent", "scaMethodSelected")
        ctx.sessions.auths[a.id] = a
        c.auth_ids.append(a.id)
        links = {self.sca_link: self._sca_href(ctx, "consent", c.id, a.id),
                 "scaStatus": self.href(f"{self.cauth_base(c.family)}/{c.id}/authorisations/{a.id}")}
        return ok({"scaStatus": a.sca_status, "authorisationId": a.id, "_links": links}, 201)

    def op_consent_auth_list(self, ctx: Ctx, consentId: str) -> dict:
        return {"authorisationIds": list(self._consent(ctx, consentId).auth_ids)}

    def op_consent_auth_get(self, ctx: Ctx, consentId: str, authorisationId: str) -> dict:
        self._consent(ctx, consentId)
        a = ctx.sessions.auths.get(authorisationId)
        if a is None or a.parent_id != consentId:
            raise ApiError(404, "RESOURCE_UNKNOWN", "authorisation not found")
        return {"scaStatus": a.sca_status}

    def op_consent_auth_update(self, ctx: Ctx, consentId: str, authorisationId: str) -> dict:
        c = self._consent(ctx, consentId)
        a = ctx.sessions.auths.get(authorisationId)
        if a is None or a.parent_id != consentId:
            raise ApiError(404, "RESOURCE_UNKNOWN", "authorisation not found")
        cust = ctx.sessions.pick_customer(self.bank.key, ctx.req.header("psu-id"), c.id)
        self.finalise(ctx, "consent", c.id, cust.cid, approve=True)
        return {"scaStatus": a.sca_status}

    # ================================================================== SCA completion + OAuth
    def finalise(self, ctx: Ctx, kind: str, parent_id: str, cid: str, approve: bool = True) -> str:
        s = ctx.sessions
        if kind == "consent":
            c = s.consents[parent_id]
            if approve:
                c.cid = cid
                if not self._has_scope(c.access):  # bank-offered consent: resolve at SCA time
                    if c.family == "v2":
                        c.consent_type = c.consent_type or "global"
                    else:
                        refs = [{"iban": self.iban_of(a), "currency": a.currency}
                                for a in self.world.accounts_of(self.bank.key, cid, ("current", "fx"))
                                if a.iban]
                        c.access = {**c.access, "accounts": refs, "balances": refs,
                                    "transactions": refs}
            c.status = "valid" if approve else "rejected"
            c.last_action = date.today()
            for aid in c.auth_ids:
                s.auths[aid].sca_status = "finalised" if approve else "failed"
            return c.status
        if kind == "payment":
            p = s.payments[parent_id]
            p.cid = cid
            for aid in p.auth_ids:
                s.auths[aid].sca_status = "finalised" if approve else "failed"
            if approve:
                self.settle(ctx, p)
            else:
                p.status = "RJCT"
            return p.status
        raise ApiError(404, "RESOURCE_UNKNOWN", "unknown authorisation parent")

    def _has_scope(self, acc: dict) -> bool:
        ids: set[str] = set()
        self._scan_ibans(acc, ids)
        return bool(ids) or bool(acc.get("availableAccounts") or acc.get("allPsd2"))

    def op_oauth_metadata(self, ctx: Ctx) -> dict:
        b = ctx.req.base_url
        return {
            "issuer": b, "authorization_endpoint": f"{b}/psd2/openbanking/oauth/authorize",
            "token_endpoint": f"{b}/openbanking/oauth/token",
            "token_endpoint_auth_methods_supported": ["tls_client_auth", "client_secret_basic"],
            "scopes_supported": ["openid", "profile", "AIS:<consentId>", "PIS:<paymentId>"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "ui_locales_supported": ["en", "ka"],
            "x-mock-note": "mock OAuth server: authorize auto-approves; use login_hint=<customerId> "
                           "to choose the PSU, mock_action=reject to simulate denial",
        }

    def op_oauth_authorize(self, ctx: Ctx) -> Response:
        q = ctx.req.query
        redirect = q.get("redirect_uri")
        if q.get("response_type") != "code" or not redirect:
            raise ApiError(400, "FORMAT_ERROR", "response_type=code and redirect_uri are required")
        kind, _, pid = q.get("scope", "").partition(":")
        s = ctx.sessions
        if kind == "AIS" and pid in s.consents and s.consents[pid].bank == self.bank.key:
            kname = "consent"
        elif kind == "PIS" and pid in s.payments and s.payments[pid].bank == self.bank.key:
            kname = "payment"
        else:
            raise ApiError(400, "FORMAT_ERROR", "scope must be AIS:<consentId> or PIS:<paymentId>")
        cust = s.pick_customer(self.bank.key, q.get("login_hint") or q.get("psu"), pid)
        approve = q.get("mock_action") != "reject"
        self.finalise(ctx, kname, pid, cust.cid, approve)
        sep = "&" if urlparse(redirect).query else "?"
        state = f"&state={q['state']}" if q.get("state") else ""
        if not approve:
            return Response(302, None, {"Location": f"{redirect}{sep}error=access_denied{state}"})
        code = new_id().replace("-", "")
        s.codes[code] = {"scope": q["scope"], "cid": cust.cid, "challenge": q.get("code_challenge"),
                         "redirect_uri": redirect, "client_id": q.get("client_id", "client")}
        return Response(302, None, {"Location": f"{redirect}{sep}code={code}{state}"})

    def op_oauth_token(self, ctx: Ctx) -> dict:
        b = self.body(ctx)
        grant = b.get("grant_type")
        s = ctx.sessions
        if grant == "authorization_code":
            info = s.codes.pop(str(b.get("code")), None)
            if info is None:
                raise ApiError(400, "FORMAT_ERROR", "invalid_grant: unknown or used code")
            if info["challenge"]:
                digest = base64.urlsafe_b64encode(
                    hashlib.sha256(str(b.get("code_verifier", "")).encode()).digest()
                ).decode().rstrip("=")
                if digest != info["challenge"]:
                    raise ApiError(400, "FORMAT_ERROR", "invalid_grant: PKCE verification failed")
            tok = s.issue_token(self.bank.key, info["cid"], info["scope"], info["client_id"])
        elif grant == "refresh_token":
            old = s.tokens.get(s.refresh.get(str(b.get("refresh_token")), ""))
            if old is None:
                raise ApiError(400, "FORMAT_ERROR", "invalid_grant: unknown refresh token")
            tok = s.issue_token(self.bank.key, old.cid, old.scope, old.client_id)
        elif grant == "client_credentials":
            tok = s.issue_token(self.bank.key, None, "PSP_AI", str(b.get("client_id", "client")))
        else:
            raise ApiError(400, "FORMAT_ERROR", "unsupported_grant_type")
        now_ms = str(int(utcnow().timestamp() * 1000))
        return {"access_token": tok.access_token, "token_type": "Bearer",
                "refresh_token": tok.refresh_token, "refresh_token_issued_at": now_ms,
                "refresh_token_expires_in": "7775999", "refresh_count": 0, "scope": tok.scope,
                "issued_at": now_ms, "expires_in": tok.expires_in}

    # ================================================================== payments (generic, stateful)
    def _service(self, service: str, product: str) -> None:
        if service not in self.payment_services or product not in self.payment_products:
            raise ApiError(404, "RESOURCE_UNKNOWN",
                           f"unknown payment service/product {service}/{product}")

    def _payment(self, ctx: Ctx, pid: str) -> Payment:
        p = ctx.sessions.payments.get(pid)
        if p is None or p.bank != self.bank.key:
            raise ApiError(403, "RESOURCE_UNKNOWN", "unknown payment")
        return p

    def pbase(self, p: Payment) -> str:
        return f"{self.api_v}/{p.service}/{p.product}/{p.id}"

    def op_pis_create(self, ctx: Ctx, payment_service: str, payment_product: str) -> Response:
        self._service(payment_service, payment_product)
        b = self.body(ctx)
        ia = b.get("instructedAmount") or {}
        cred = (b.get("creditorAccount") or {}).get("iban")
        if not ia or not cred:
            raise ApiError(400, "FORMAT_ERROR",
                           "instructedAmount and creditorAccount.iban are required")
        try:
            amt = round(float(ia.get("amount", ia.get("value"))) * 100)
        except (TypeError, ValueError) as e:
            raise ApiError(400, "FORMAT_ERROR", "instructedAmount.amount is invalid") from e
        if amt <= 0:
            raise ApiError(400, "FORMAT_ERROR", "instructedAmount must be positive")
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

    def op_pis_get(self, ctx: Ctx, payment_service: str, payment_product: str,
                   paymentId: str) -> dict:
        p = self._payment(ctx, paymentId)
        out = {k: v for k, v in p.body.items() if v not in (None, "")}
        out["transactionStatus"] = p.status
        return out

    def op_pis_status(self, ctx: Ctx, payment_service: str, payment_product: str,
                      paymentId: str) -> dict:
        p = self._payment(ctx, paymentId)
        out: dict[str, Any] = {"transactionStatus": p.status}
        if p.status in ("RCVD", "ACTC", "ACWC", "ACCP"):
            out["fundsAvailable"] = True
        return out

    def op_pis_cancel(self, ctx: Ctx, payment_service: str, payment_product: str,
                      paymentId: str) -> Response:
        if not self.cancel_supported:
            raise ApiError(405, "SERVICE_INVALID", "payment cancellation is not supported")
        p = self._payment(ctx, paymentId)
        if p.settled or p.status in PIS_FINAL_OK:
            raise ApiError(405, "SERVICE_INVALID", "payment can no longer be cancelled")
        p.status = "CANC"
        return Response(204, None)

    @staticmethod
    def _pauth(ctx: Ctx, p: Payment, cancel: bool) -> Authorisation:
        a = Authorisation(new_id(), p.id, "payment-cancel" if cancel else "payment", "started")
        ctx.sessions.auths[a.id] = a
        (p.cancel_auth_ids if cancel else p.auth_ids).append(a.id)
        return a

    def op_pis_auth_start(self, ctx: Ctx, payment_service: str, payment_product: str,
                          paymentId: str) -> Response:
        p = self._payment(ctx, paymentId)
        a = self._pauth(ctx, p, False)
        links = {self.sca_link: self._sca_href(ctx, "payment", p.id, a.id),
                 "scaStatus": self.href(f"{self.pbase(p)}/authorisations/{a.id}")}
        return ok({"scaStatus": a.sca_status, "authorisationId": a.id, "_links": links}, 201)

    def op_pis_auth_list(self, ctx: Ctx, payment_service: str, payment_product: str,
                         paymentId: str) -> dict:
        return {"authorisationIds": list(self._payment(ctx, paymentId).auth_ids)}

    def op_pis_auth_get(self, ctx: Ctx, payment_service: str, payment_product: str,
                        paymentId: str, authorisationId: str) -> dict:
        self._payment(ctx, paymentId)
        a = ctx.sessions.auths.get(authorisationId)
        if a is None:
            raise ApiError(404, "RESOURCE_UNKNOWN", "authorisation not found")
        return {"scaStatus": a.sca_status}

    def op_pis_auth_update(self, ctx: Ctx, payment_service: str, payment_product: str,
                           paymentId: str, authorisationId: str) -> dict:
        p = self._payment(ctx, paymentId)
        cust = ctx.sessions.pick_customer(self.bank.key, ctx.req.header("psu-id"), p.id)
        self.finalise(ctx, "payment", p.id, cust.cid, True)
        return {"scaStatus": ctx.sessions.auths[authorisationId].sca_status}

    def op_pis_cancel_auth_start(self, ctx: Ctx, payment_service: str, payment_product: str,
                                 paymentId: str) -> Response:
        p = self._payment(ctx, paymentId)
        a = self._pauth(ctx, p, True)
        return ok({"scaStatus": a.sca_status, "authorisationId": a.id, "_links": {
            "scaStatus": self.href(f"{self.pbase(p)}/cancellation-authorisations/{a.id}")}}, 201)

    def op_pis_cancel_auth_list(self, ctx: Ctx, payment_service: str, payment_product: str,
                                paymentId: str) -> dict:
        return {"cancellationIds": list(self._payment(ctx, paymentId).cancel_auth_ids)}

    def op_pis_cancel_auth_get(self, ctx: Ctx, payment_service: str, payment_product: str,
                               paymentId: str, authorisationId: str) -> dict:
        return self.op_pis_auth_get(ctx, payment_service, payment_product, paymentId,
                                    authorisationId)

    def op_pis_cancel_auth_update(self, ctx: Ctx, payment_service: str, payment_product: str,
                                  paymentId: str, authorisationId: str) -> dict:
        p = self._payment(ctx, paymentId)
        p.status = "CANC"
        ctx.sessions.auths[authorisationId].sca_status = "finalised"
        return {"scaStatus": "finalised"}

    def settle(self, ctx: Ctx, p: Payment) -> None:
        """After SCA: single payments execute now; extended services only become ACTC."""
        if p.service in EXTENDED_SERVICES:
            p.status = "ACTC"  # consent authorised; money moves via /initiations
            return
        p.status = self._execute(p, p.amount, p.body)
        p.settled = p.status in PIS_FINAL_OK

    def _execute(self, p: Payment, amount: int, body: dict) -> str:
        """Debit the PSU's account for `amount`; returns a Berlin Group transactionStatus."""
        assert p.cid
        accts = self.world.accounts_of(self.bank.key, p.cid, ("current", "fx"))
        deb = body.get("debtorAccount") or {}
        deb_iban, deb_ccy = deb.get("iban"), deb.get("currency")
        # one IBAN can carry several currency sub-accounts: the account is keyed by (iban, currency)
        same_iban = [a for a in accts if self.iban_of(a) == deb_iban]
        src = next((a for a in same_iban if a.currency == (deb_ccy or p.currency)), None) or \
            next((a for a in same_iban if not deb_ccy), None) or \
            next((a for a in accts if a.currency == p.currency and not same_iban), None)
        if src is None or src.currency != p.currency:
            return "RJCT"
        if self.world.balances(src)["interimAvailable"] < amount:
            return "RJCT"
        cred_iban = body["creditorAccount"]["iban"]
        p.debtor_account_id = src.id
        narr = str(body.get("remittanceInformationUnstructured") or "")
        # Append without re-sorting: existing entryReferences must stay stable, otherwise a client
        # paging with entryReferenceFrom across this payment would skip or repeat entries.
        last_booked = next((t for t in reversed(src.txns) if t.status == "booked"), None)
        prev_balance = last_booked.balance_after if last_booked else src.opening_balance
        entry = Txn(
            seq=self.world.next_seq(), account_id=src.id, booking_date=self.world.as_of,
            value_date=self.world.as_of,
            ts=datetime.combine(self.world.as_of, utcnow().time()),  # inside the as_of day
            amount=-amount, currency=p.currency,
            kind="pis_payment", status="booked", narrative_ka=narr or "გადახდა",
            narrative_en=narr or "Payment",
            cp_name=str(body.get("creditorName") or "BENEFICIARY"), cp_iban=cred_iban,
            channel="TRANSFER", e2e=str(body.get("endToEndIdentification") or "") or None)
        entry.ref = f"{max((int(t.ref) for t in src.txns), default=0) + 1:010d}"
        entry.balance_after = (prev_balance or 0) - amount
        src.txns.append(entry)
        return "ACCC" if cred_iban[4:6] == self.bank.iban_code else "ACSC"

    # ================================================================== docs helper
    def describe(self) -> dict[str, Any]:
        return {
            "dialect": self.key, "bank": self.bank.key, "prefixes": list(self.prefixes),
            "consent_header": self.consent_header, "paging": self.paging,
            "page_size": self.page_size,
            "capabilities": {"card_accounts": self.card_accounts, "loans": self.loans,
                             "savings": self.savings, "transaction_detail": self.txn_detail},
            "routes": [{"method": m, "path": t, "op": o} for m, t, o in self.route_table()],
        }
