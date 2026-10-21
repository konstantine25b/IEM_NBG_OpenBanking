"""Validation: ledger invariants + schema validation of every dialect against its own spec.

Two layers:
  1. check_ledger(world)      accounting identities that every API view inherits
  2. check_dialects(world)    replays real client call sequences against each bank's gateway and
                              validates each JSON body with jsonschema against that bank's own
                              OpenAPI response schema (specs/, see specs/MANIFEST.tsv)

Spec defects are normalised first (documented in docs/BANK_ANALYSIS.md): AltaSoft models string
wrappers (Max70Text, StrAmount...) as objects with a `length` property; TBC's schemas say `links`
while its examples use `_links`; several specs mark every field required. The default ("lenient")
pass drops `required`/`additionalProperties`; the "strict" pass keeps them and is informational.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from obmock.banks import BANKS
from obmock.config import SPEC_DIR
from obmock.engine.gateway import ApiError, Gateway, Request
from obmock.export import capture_customer
from obmock.util import is_valid_iban
from obmock.world.model import World

HTTP = ("get", "post", "put", "delete")


# ============================================================================== ledger invariants
@dataclass
class Check:
    name: str
    total: int = 0
    failed: int = 0
    examples: list[str] = field(default_factory=list)

    def add(self, ok: bool, detail: str = "") -> None:
        self.total += 1
        if not ok:
            self.failed += 1
            if len(self.examples) < 3:
                self.examples.append(detail)


def check_ledger(world: World) -> list[Check]:
    as_of = world.as_of
    month_start, prev = as_of.replace(day=1), as_of - timedelta(days=1)
    c_iban = Check("IBANs are valid (mod-97) and unique")
    c_run = Check("running balance_after == opening + sum(booked)")
    c_month = Check("openingBooked + month's booked entries == closingBooked")
    c_dates = Check("no entry before account opening; booked <= as_of < pending")
    c_ccy = Check("entry currency == account currency")
    c_xfer = Check("own-account transfer legs net to zero")
    c_loan = Check("loan repayment debits == paid instalments in the window")
    c_sal = Check("median salary credit within 20-25% of declared net income (variable pay is noisy)")
    c_lim = Check("credit-card balance within limit; deposits never negative")
    seen: set[str] = set()
    legs: dict[str, list[Any]] = defaultdict(list)
    for a in world.accounts.values():
        if a.iban:
            c_iban.add(is_valid_iban(a.iban) and a.iban not in seen, a.iban)
            seen.add(a.iban)
        booked = [t for t in a.txns if t.status == "booked"]
        exp = a.opening_balance + sum(t.amount for t in booked)
        got = booked[-1].balance_after if booked else a.opening_balance
        c_run.add(exp == got, f"{a.id}: {exp} != {got}")
        b = world.balances(a)
        in_month = sum(t.amount for t in booked if month_start <= t.booking_date <= prev)
        c_month.add(b["openingBooked"] + in_month == b["closingBooked"], a.id)
        for t in a.txns:
            ok = t.booking_date >= a.opened and (
                (t.status == "booked" and t.booking_date <= as_of)
                or (t.status == "pending" and t.booking_date > as_of))
            c_dates.add(ok, f"{a.id} {t.kind} {t.booking_date} {t.status} opened {a.opened}")
            c_ccy.add(t.currency == a.currency, f"{a.id}")
            if t.e2e and t.kind.startswith(("own_transfer", "cc_repay")):
                legs[t.e2e].append(t)
        if a.kind == "card_credit":
            # purchases are blocked at the limit, but capitalised interest on a customer who barely
            # repays can carry the balance past it (an over-limit card); bound that overshoot
            c_lim.add(b["interimBooked"] >= -a.credit_limit * 1.25, f"{a.id} {b['interimBooked']}")
        if a.kind.startswith("savings"):
            c_lim.add(b["interimBooked"] >= 0, f"{a.id} {b['interimBooked']}")
    for e2e, ts in legs.items():
        if len(ts) == 2 and ts[0].currency == ts[1].currency:
            c_xfer.add(ts[0].amount + ts[1].amount == 0, e2e)
    for loan in world.loans.values():
        funding = world.accounts[loan.funding_account_id]
        tag = loan.id[:8].upper()
        paid = [i for i in loan.schedule
                if i.paid_on and world.window_start <= i.paid_on <= as_of]
        debits = [t for t in funding.txns if t.kind == "loan_payment" and tag in t.narrative_en]
        c_loan.add(len(debits) == len(paid) and
                   -sum(t.amount for t in debits) == sum(i.total for i in paid),
                   f"{loan.id}: {len(debits)} debits vs {len(paid)} paid instalments")
    for c in world.customers.values():
        if c.segment != "PRIV" or c.income_month_cents <= 0:
            continue
        prim = world.accounts_of(c.banks[0], c.cid, ("current",))[0]
        sal = [t.amount for t in prim.txns if t.kind == "salary"]
        if len(sal) >= 3:
            r = statistics.median(sal) / (c.income_month_cents - c.remit_month_cents)
            # 25% of customers have variable pay (sigma 0.12), so the median of a few credits is
            # noisy: allow ~4.5 standard errors, which still catches a real amount bug.
            tol = 0.25 if len(sal) < 6 else 0.20
            c_sal.add(abs(r - 1.0) <= tol, f"{c.cid}: ratio {r:.2f}")
    return [c_iban, c_run, c_month, c_dates, c_ccy, c_xfer, c_loan, c_sal, c_lim]


# ============================================================================== spec handling
def _ptr(s: str) -> str:
    return s.replace("~", "~0").replace("/", "~1")


def norm_path(p: str) -> str:
    p = re.sub(r"^/(0\.8|sandbox/0\.8)(?=/)", "", p)
    return re.sub(r"\{[^}]*\}", "{}", p).rstrip("/") or "/"


def prep_node(n: Any, lenient: bool) -> Any:
    """Normalise known spec defects so jsonschema can validate real-looking payloads."""
    if isinstance(n, list):
        return [prep_node(x, lenient) for x in n]
    if not isinstance(n, dict):
        return n
    props = n.get("properties")
    if n.get("type") == "object" and isinstance(props, dict) and set(props) == {"length"}:
        return {"type": "string"}  # AltaSoft string wrappers modelled as objects
    if (n.get("type") == "object" and not props and n.get("additionalProperties") is False
            and not any(k in n for k in ("allOf", "oneOf", "anyOf"))):
        return {"type": "string"}  # AltaSoft ISODate/ISODateTime (.NET date structs) as {}
    out: dict[str, Any] = {}
    for k, v in n.items():
        if (k == "description" and isinstance(v, str)) or k in ("example", "examples"):
            continue
        if lenient and ((k == "required" and isinstance(v, list))
                        or (k == "additionalProperties" and isinstance(v, bool))):
            continue
        out[k] = prep_node(v, lenient)
    if out.get("nullable") is True:
        out.pop("nullable")
        t = out.get("type")
        if isinstance(t, str):
            out["type"] = [t, "null"]
            if "enum" in out and None not in out["enum"]:
                out["enum"] = [*out["enum"], None]
        elif "$ref" in out:
            out = {"anyOf": [{"$ref": out.pop("$ref")}, {"type": "null"}], **out}
    return out


@lru_cache(maxsize=32)
def _load_doc(path: str, lenient: bool) -> dict[str, Any]:
    p = Path(path)
    if p.suffix == ".json":
        doc = json.loads(p.read_text(encoding="utf-8"))
    else:
        doc = yaml.load(p.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    return prep_node(doc, lenient)


class SpecIndex:
    """Looks up response schemas by (method, normalised path) across one or more spec files."""

    def __init__(self, files: list[Path], lenient: bool = True):
        self.registry = Registry()
        self.ops: dict[tuple[str, str], tuple[str, str, str, dict]] = {}
        for i, f in enumerate(files):
            doc = _load_doc(str(f), lenient)
            uri = f"urn:spec:{int(lenient)}:{f.name}:{i}"
            self.registry = self.registry.with_resource(
                uri, Resource.from_contents(doc, default_specification=DRAFT202012))
            for path, item in (doc.get("paths") or {}).items():
                for m, op in item.items():
                    if m in HTTP and isinstance(op, dict):
                        self.ops[(m.upper(), norm_path(path))] = (uri, path, m, op)

    def validate(self, method: str, template: str, status: int, body: Any) -> list[str] | None:
        """None = operation/status not described by the spec; [] = valid; else error strings."""
        entry = self.ops.get((method, norm_path(template)))
        if entry is None:
            return None
        uri, path, m, op = entry
        resp = (op.get("responses") or {}).get(str(status))
        if not isinstance(resp, dict):
            return None
        content = resp.get("content") or {}
        media = next((k for k in content if "json" in k.lower()), None)
        if media is None or "schema" not in (content[media] or {}):
            return []
        ptr = f"/paths/{_ptr(path)}/{m}/responses/{status}/content/{_ptr(media)}/schema"
        try:
            v = Draft202012Validator({"$ref": f"{uri}#{ptr}"}, registry=self.registry)
            errs = sorted(v.iter_errors(body), key=lambda e: [str(x) for x in e.path])
        except Exception as e:  # noqa: BLE001 - unresolvable spec refs are reported, not fatal
            return [f"schema error: {type(e).__name__}: {str(e)[:120]}"]
        return [f"{'/'.join(str(x) for x in e.path) or '<root>'}: {e.message[:130]}"
                for e in errs[:5]]


def spec_files(bank: str) -> list[Path]:
    return [SPEC_DIR / f for f in BANKS[bank].spec_files
            if (SPEC_DIR / f).exists() and not f.startswith("paysera/")]


# ============================================================================== dialect replay
def _flows(gw: Gateway, world: World, bank: str, cid: str) -> list[tuple[str, str, int, Any]]:
    """Consent + payment sequences a TPP would run; returns (method, path, status, body)."""
    d = gw.dialects[bank]
    calls: list[tuple[str, str, int, Any]] = []

    def call(method: str, path: str, body: Any = None) -> Any:
        h = {"x-request-id": "validate", "psu-ip-address": "203.0.113.7",
             "tpp-redirect-uri": "https://tpp.example/cb", "x-mock-customer": cid}
        r = gw.handle(bank, Request(method, path, {}, h, body, f"http://x/{bank}"))
        calls.append((method, path, r.status, r.body))
        return r.body

    valid = (date.today() + timedelta(days=30)).isoformat()
    families = ["v1", "v2"] if bank == "tbc" else [d.consent_family]
    for fam in families:
        base, ab = d.cbase(fam), d.cauth_base(fam)
        if fam == "v2":
            body = {"access": {"loans": [], "savings": []}, "consentType": "global",
                    "recurringIndicator": True, "validTo": valid, "frequencyPerDay": 4}
        else:
            body = {"access": {"availableAccounts": "allAccounts"}, "recurringIndicator": True,
                    "validUntil": valid, "frequencyPerDay": 4, "combinedServiceIndicator": False}
        created = call("POST", base, body)
        if not isinstance(created, dict) or "consentId" not in created:
            continue
        cons = created["consentId"]
        call("GET", f"{base}/{cons}")
        call("GET", f"{base}/{cons}/status")
        if d.explicit_auth:
            call("POST", f"{ab}/{cons}/authorisations", {})
        call("GET", f"{ab}/{cons}/authorisations")
        gw.complete_sca(bank, "consent", cons, cid)
        call("GET", f"{base}/{cons}/status")
    accts = world.accounts_of(bank, cid, ("current",))
    other = next(a for b in BANKS if b != bank
                 for a in world.accounts_of(b, world.customers_of(b)[0], ("current",)))
    if accts:
        svc, prod = d.payment_services[0], "domestic"
        amount = ({"currency": "GEL", "value": 1.0} if d.amount_number
                  else {"currency": "GEL", "amount": "1.00"})  # ProCredit sends a numeric `value`
        pbody = {"debtorAccount": {"iban": accts[0].iban}, "instructedAmount": amount,
                 "creditorAccount": {"iban": other.iban}, "creditorName": "TEST BENEFICIARY",
                 "remittanceInformationUnstructured": "validate"}
        pay = call("POST", f"{d.api_v}/{svc}/{prod}", pbody)
        if isinstance(pay, dict) and "paymentId" in pay:
            pid = pay["paymentId"]
            call("GET", f"{d.api_v}/{svc}/{prod}/{pid}/status")
            gw.complete_sca(bank, "payment", pid, cid)
            call("GET", f"{d.api_v}/{svc}/{prod}/{pid}/status")
            call("GET", f"{d.api_v}/{svc}/{prod}/{pid}")
    return calls


@dataclass
class BankReport:
    bank: str
    evidence: str
    validated: int = 0
    unspecified: int = 0
    violations: list[tuple[str, str, list[str]]] = field(default_factory=list)
    strict_violations: int = 0
    skipped_reason: str = ""


def check_dialects(world: World, per_bank: int = 4,
                   banks: list[str] | None = None) -> list[BankReport]:
    gw = Gateway(world, auth_mode="off")
    date_from = world.window_start.isoformat()
    reports = []
    for bank in banks or list(BANKS):
        d = gw.dialects[bank]
        rep = BankReport(bank, BANKS[bank].evidence)
        files = spec_files(bank)
        if not files:
            rep.skipped_reason = "no machine-readable spec"
            reports.append(rep)
            continue
        lenient, strict = SpecIndex(files, True), SpecIndex(files, False)
        cids = sorted(world.customers_of(bank), key=lambda c: -sum(
            len(a.txns) for a in world.accounts_of(bank, c)))[:per_bank]
        for n, cid in enumerate(cids):
            calls = [(c["method"], c["path"], c["status"], c["body"])
                     for c in capture_customer(gw, bank, cid, date_from)["calls"]]
            if n == 0:
                calls += _flows(gw, world, bank, cid)
            for method, path, status, body in calls:
                if status >= 300 or body is None:
                    continue
                try:
                    route, _ = gw.routers[bank].match(method, d.normalize_path(path))
                except ApiError:
                    continue
                tmpl = route.template
                if route.alias:  # validate against the *published* path, not our slashed alias
                    base_op = route.op.removesuffix("_alias")
                    tmpl = next((r.template for r in d.routes() if r.op == base_op), tmpl)
                errs = lenient.validate(method, tmpl, status, body)
                if errs is None:
                    rep.unspecified += 1
                    continue
                rep.validated += 1
                if errs:
                    rep.violations.append((f"{method} {tmpl}", str(status), errs))
                if strict.validate(method, tmpl, status, body):
                    rep.strict_violations += 1
        reports.append(rep)
    return reports


# ============================================================================== entry point
def run_all(world: World, verbose: bool = True) -> bool:
    ok = True
    print("== ledger invariants")
    for c in check_ledger(world):
        mark = "ok  " if c.failed == 0 else "FAIL"
        print(f"  {mark} {c.name}: {c.total - c.failed}/{c.total}")
        if c.failed:
            ok = False
            for e in c.examples:
                print(f"       e.g. {e}")
    print("== schema validation (each response vs the bank's own OpenAPI)")
    print(f"  {'bank':10} {'evidence':9} {'validated':>9} {'unspec.':>8} "
          f"{'violations':>10} {'strict':>7}")
    for r in check_dialects(world):
        if r.skipped_reason:
            print(f"  {r.bank:10} {r.evidence:9} {'-':>9} {'-':>8} {'-':>10} {'-':>7}  "
                  f"({r.skipped_reason})")
            continue
        print(f"  {r.bank:10} {r.evidence:9} {r.validated:>9} {r.unspecified:>8} "
              f"{len(r.violations):>10} {r.strict_violations:>7}")
        if r.violations:
            ok = False
            for op, st, errs in r.violations[:4]:
                print(f"       {op} -> {st}: {errs[0]}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok
