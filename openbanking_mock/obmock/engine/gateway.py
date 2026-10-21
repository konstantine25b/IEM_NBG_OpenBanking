"""HTTP-agnostic gateway: routing, auth, consent/token/payment state, chaos.

The gateway never touches FastAPI. `Gateway.handle(bank, Request) -> Response` is pure Python, so
the live server, the in-process dataset dumper and the test-suite all exercise the same code.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import numpy as np
from obmock.banks import BANKS, BankProfile
from obmock.world.model import Customer, World

if TYPE_CHECKING:
    from obmock.dialects.base import Dialect


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================ http primitives
class ApiError(Exception):
    def __init__(self, status: int, code: str, text: str = "", category: str = "ERROR",
                 headers: dict[str, str] | None = None):
        super().__init__(f"{status} {code} {text}")
        self.status, self.code, self.text, self.category = status, code, text, category
        self.headers = headers or {}


@dataclass
class Request:
    method: str
    path: str  # path below /{bank}, starting with "/"
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased names
    body: Any = None
    base_url: str = ""  # e.g. http://localhost:8000/tbc

    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name.lower(), default)


@dataclass
class Response:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/json"


_PARAM = re.compile(r"\{([^}]+)\}")


@dataclass
class Route:
    method: str
    template: str  # as published, without version prefix such as /0.8
    op: str
    alias: bool = False  # an unpublished convenience alias (e.g. the "fixed" loans path)
    fixed: dict[str, str] = field(default_factory=dict)  # literal path parts passed to the op
    regex: re.Pattern = field(init=False, repr=False)
    names: tuple[str, ...] = field(init=False)
    literal: int = field(init=False)

    def __post_init__(self) -> None:
        parts = _PARAM.split(self.template)  # literal, name, literal, name, ...
        names: list[str] = []
        pattern = ""
        for i, part in enumerate(parts):
            if i % 2 == 0:
                pattern += re.escape(part)
            else:
                pattern += f"(?P<p{len(names)}>[^/]+)"
                names.append(part)
        self.regex = re.compile("^" + pattern + "$")
        self.names = tuple(names)
        self.literal = len([s for s in self.template.split("/") if s and "{" not in s])


class Router:
    def __init__(self, routes: list[Route]):
        self.routes = sorted(routes, key=lambda r: (-r.literal, -len(r.template)))

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]]:
        allowed: set[str] = set()
        for r in self.routes:
            m = r.regex.match(path)
            if not m:
                continue
            if r.method == method:
                return r, {n: unquote(m.group(f"p{i}")) for i, n in enumerate(r.names)}
            allowed.add(r.method)
        if allowed:
            raise ApiError(405, "SERVICE_INVALID", "method not allowed for this resource",
                           headers={"Allow": ", ".join(sorted(allowed))})
        raise ApiError(404, "RESOURCE_UNKNOWN", f"no such endpoint: {path}")


# ============================================================================ state
def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Consent:
    id: str
    bank: str
    family: str  # v1 | v2
    status: str
    access: dict
    recurring: bool
    valid_until: date
    freq: int
    combined: bool
    created: datetime
    last_action: date
    cid: str | None = None
    redirect_uri: str | None = None
    psu_id: str | None = None
    consent_type: str | None = None
    auth_ids: list[str] = field(default_factory=list)
    uses: dict[date, int] = field(default_factory=dict)


@dataclass
class Authorisation:
    id: str
    parent_id: str
    parent_kind: str  # consent | payment | payment-cancel
    sca_status: str = "received"
    psu_name: str | None = None


@dataclass
class Token:
    access_token: str
    refresh_token: str
    bank: str
    scope: str
    cid: str | None
    client_id: str
    issued: datetime
    expires_in: int = 3599

    @property
    def expired(self) -> bool:
        return utcnow() > self.issued + timedelta(seconds=self.expires_in)


@dataclass
class Payment:
    id: str
    bank: str
    service: str
    product: str
    body: dict
    status: str
    debtor_account_id: str | None
    amount: int
    currency: str
    created: datetime
    cid: str | None = None
    auth_ids: list[str] = field(default_factory=list)
    cancel_auth_ids: list[str] = field(default_factory=list)
    initiations: dict[str, dict] = field(default_factory=dict)
    settled: bool = False


class Sessions:
    """In-memory consent / OAuth / payment state (resets when the process restarts)."""

    def __init__(self, world: World):
        self.world = world
        self.consents: dict[str, Consent] = {}
        self.auths: dict[str, Authorisation] = {}
        self.tokens: dict[str, Token] = {}
        self.refresh: dict[str, str] = {}
        self.codes: dict[str, dict] = {}
        self.payments: dict[str, Payment] = {}

    def pick_customer(self, bank: str, hint: str | None, seed: str) -> Customer:
        cids = self.world.customers_of(bank)
        if not cids:
            raise ApiError(500, "SERVICE_BLOCKED", f"no customers at bank {bank}")
        if hint and hint in cids:
            return self.world.customers[hint]
        h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
        return self.world.customers[cids[h % len(cids)]]

    def issue_token(self, bank: str, cid: str | None, scope: str, client_id: str) -> Token:
        t = Token(secrets.token_urlsafe(24), secrets.token_urlsafe(24), bank, scope, cid,
                  client_id, utcnow())
        self.tokens[t.access_token] = t
        self.refresh[t.refresh_token] = t.access_token
        return t

    def bearer(self, header: str | None) -> Token:
        if not header or not header.lower().startswith("bearer "):
            raise ApiError(401, "TOKEN_INVALID", "missing bearer token")
        tok = self.tokens.get(header[7:].strip())
        if tok is None:
            raise ApiError(401, "TOKEN_INVALID", "unknown access token")
        if tok.expired:
            raise ApiError(401, "TOKEN_EXPIRED", "access token expired")
        return tok


# ============================================================================ chaos
class Chaos:
    """Optional latency / failure injection calibrated to each bank's published KPIs."""

    def __init__(self, mode: str, seed: int = 7):
        if mode not in ("off", "errors", "kpi"):
            raise ValueError("chaos must be off | errors | kpi")
        self.mode = mode
        self.rng = np.random.default_rng(seed)

    def before(self, prof: BankProfile, op: str, forced: str | None) -> None:
        if forced:
            code = "SERVICE_BLOCKED" if forced == "503" else "INTERNAL_ERROR"
            raise ApiError(int(forced), code, "forced by X-Mock-Force-Status")
        if self.mode == "off":
            return
        k = prof.kpi
        if self.mode == "kpi":
            ms = k.pis_ms if op.startswith("pis") else k.ais_ms
            time.sleep(float(self.rng.lognormal(np.log(max(ms, 1.0)), 0.45)) / 1000.0)
        minute = int(time.time() // 60)
        h = int(hashlib.sha1(f"{prof.key}:{minute}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if h * 100.0 < 100.0 - k.uptime_pct:
            raise ApiError(503, "SERVICE_BLOCKED", "service temporarily unavailable")
        if self.rng.random() * 100.0 < k.error_pct:
            raise ApiError(500, "INTERNAL_ERROR", "unexpected server error")


# ============================================================================ context + gateway
@dataclass
class Ctx:
    bank: BankProfile
    world: World
    sessions: Sessions
    req: Request
    params: dict[str, str]
    dialect: Dialect
    auth_mode: str
    request_id: str
    customer: Customer | None = None
    consent: Consent | None = None
    token: Token | None = None


def _valid_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


class Gateway:
    def __init__(self, world: World, auth_mode: str = "strict", chaos: str = "off",
                 dialects: dict[str, Dialect] | None = None):
        from obmock.dialects.registry import build_dialects

        if auth_mode not in ("strict", "off"):
            raise ValueError("auth_mode must be 'strict' or 'off'")
        self.world = world
        self.auth_mode = auth_mode
        self.sessions = Sessions(world)
        self.chaos = Chaos(chaos)
        self.dialects = dialects or build_dialects(world)
        self.routers = {k: Router(d.routes()) for k, d in self.dialects.items()}
        self._lock = threading.RLock()  # the ledger/session state is plain Python; serialise access

    def handle(self, bank: str, req: Request) -> Response:
        with self._lock:
            return self._handle(bank, req)

    def _handle(self, bank: str, req: Request) -> Response:
        prof = BANKS.get(bank)
        if prof is None:
            return Response(404, {"error": f"unknown bank {bank}", "banks": list(BANKS)})
        dialect = self.dialects[bank]
        rid = req.header("x-request-id") or new_id()
        try:
            path = dialect.normalize_path(req.path)
            route, params = self.routers[bank].match(req.method.upper(), path)
            self.chaos.before(prof, route.op, req.header("x-mock-force-status"))
            ctx = Ctx(prof, self.world, self.sessions, req, params, dialect, self.auth_mode, rid)
            self._authenticate(ctx, route.op)
            resp = dialect.dispatch(ctx, route)
        except ApiError as e:
            resp = dialect.error_response(e, req, rid)
        resp.headers.setdefault("X-Request-ID", rid)
        resp.headers.setdefault("X-Mock-Bank", bank)
        return resp

    # ------------------------------------------------------------------ auth
    def _authenticate(self, ctx: Ctx, op: str) -> None:
        cat = ctx.dialect.auth_category(op)
        req, s = ctx.req, ctx.sessions
        if ctx.auth_mode == "off":
            if cat == "data":
                hint = req.header("x-mock-customer") or req.query.get("psu")
                ctx.customer = s.pick_customer(ctx.bank.key, hint, "off")
            return
        if cat in ("public", "pis"):
            return
        if not req.header("x-request-id"):
            raise ApiError(400, "FORMAT_ERROR", "X-Request-ID header is missing")
        if cat == "consent":
            return
        tok = s.bearer(req.header("authorization"))  # data endpoints: bearer token + consent
        if tok.bank != ctx.bank.key:
            raise ApiError(401, "TOKEN_INVALID", "token was issued by another bank")
        cname = ctx.dialect.consent_header
        cid_hdr = next((v for h in ctx.dialect.consent_headers if (v := req.header(h))), None)
        if not cid_hdr:
            raise ApiError(400, "FORMAT_ERROR", f"{cname} header is missing")
        ctx.dialect.check_extra_auth(ctx)
        consent = s.consents.get(cid_hdr)
        if consent is None or consent.bank != ctx.bank.key:
            raise ApiError(403, "CONSENT_UNKNOWN", "unknown consent")
        if consent.status == "expired" or consent.valid_until < date.today():
            consent.status = "expired"
            raise ApiError(401, "CONSENT_EXPIRED", "consent expired")
        if consent.status != "valid":
            raise ApiError(401, "CONSENT_INVALID", f"consent status is {consent.status}")
        scope_ok = (not tok.scope) or consent.id in tok.scope or tok.scope in ("AIS", "offline_access")
        if tok.cid != consent.cid or not scope_ok:
            raise ApiError(403, "CONSENT_UNKNOWN", "token does not match consent")
        psu_ip = req.header("psu-ip-address")
        if not psu_ip:  # unattended access is rate limited by the consent's frequencyPerDay
            today = date.today()
            consent.uses[today] = consent.uses.get(today, 0) + 1
            if consent.uses[today] > consent.freq:
                raise ApiError(429, "ACCESS_EXCEEDED", "frequency limit per day exceeded")
            if ctx.dialect.require_psu_ip:
                raise ApiError(400, "FORMAT_ERROR", "PSU-IP-Address header is missing")
        elif not _valid_ip(psu_ip):
            raise ApiError(400, "FORMAT_ERROR", "PSU-IP-Address header is invalid")
        ctx.token, ctx.consent = tok, consent
        ctx.customer = self.world.customers[consent.cid]  # type: ignore[index]

    # ------------------------------------------------------------------ mock control plane
    def complete_sca(self, bank: str, kind: str, parent_id: str, psu: str | None = None,
                     approve: bool = True) -> dict[str, Any]:
        """Finish a redirect-style SCA (the page a PSU would see at the bank)."""
        s = self.sessions
        store: dict[str, Any] = s.consents if kind == "consent" else s.payments
        if kind not in ("consent", "payment") or parent_id not in store \
                or store[parent_id].bank != bank:
            raise ApiError(404, "RESOURCE_UNKNOWN", "unknown SCA resource")
        cust = s.pick_customer(bank, psu, parent_id)
        ctx = Ctx(BANKS[bank], self.world, s, Request("GET", "/"), {}, self.dialects[bank],
                  self.auth_mode, new_id())
        status = self.dialects[bank].finalise(ctx, kind, parent_id, cust.cid, approve)
        return {"status": status, "customer": cust.cid,
                "redirect_uri": getattr(store[parent_id], "redirect_uri", None)}

    def quick_session(self, bank: str, cid: str | None = None, scope: str = "AIS",
                      days: int = 90) -> dict[str, Any]:
        """Create an already-authorised consent + bearer token (skips the SCA redirect)."""
        s = self.sessions
        cust = s.pick_customer(bank, cid, f"{bank}:{len(s.consents)}")
        dialect = self.dialects[bank]
        refs = [{"iban": a.iban} for a in self.world.accounts_of(
            bank, cust.cid, ("current", "fx", "savings_term", "savings_flex")) if a.iban]
        consent = Consent(
            id=new_id(), bank=bank, family=dialect.consent_family, status="valid",
            access={"accounts": refs, "balances": refs, "transactions": refs,
                    "availableAccounts": "allAccounts"},
            recurring=True, valid_until=date.today() + timedelta(days=days), freq=10_000,
            combined=False, created=utcnow(), last_action=date.today(), cid=cust.cid,
            consent_type="global",
        )
        s.consents[consent.id] = consent
        tok = s.issue_token(bank, cust.cid, f"{scope}:{consent.id}", "mock-client")
        return {
            "bank": bank, "customer": cust.cid, "consentId": consent.id,
            "consentHeader": {dialect.consent_header: consent.id},
            "authorization": f"Bearer {tok.access_token}", "refreshToken": tok.refresh_token,
            "expiresIn": tok.expires_in, "validUntil": consent.valid_until.isoformat(),
            "example": {
                "method": "GET",
                "path": f"/{bank}{dialect.canonical_prefix}{dialect.accounts_path}",
                "headers": {"Authorization": f"Bearer {tok.access_token}",
                            dialect.consent_header: consent.id, "X-Request-ID": new_id(),
                            "PSU-IP-Address": "203.0.113.7"},
            },
        }
