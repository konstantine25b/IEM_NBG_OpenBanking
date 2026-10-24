#!/usr/bin/env python3
"""Income estimation API: what the lender's portal calls.

  POST /v1/consents                     personal id -> consent requests to all 12 banks + RS + Creditinfo, in parallel
  GET  /v1/consents/{consent_set_id}    the same table again
  POST /v1/income-estimates             consent_set_id -> 202 {job_id}
  GET  /v1/income-estimates/{job_id}    progress per source, then the P10 / P50 / P90 result with confidence

The three processes of the demo (each one also works alone):
  API/mock_banks.py       :8000  12 bank APIs, all customers          (--banks-url)
  API/mock_registries.py  :8001  RS + Creditinfo                      (--registries-url)
  API/service.py          :9000  this API

The fetch is what a TPP does: for every bank with an approved consent, accounts -> balances -> every transaction page
(+ cards, loans, savings where the bank has them), using the bank dialects' own routes. The responses are flattened
by obmock.harvest and turned into features by the same code that built income_dataset.csv, so the model sees what it
was trained on.

    uv run python API/service.py --port 9000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Literal

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (HERE, REPO / "income", REPO / "eda", REPO / "openbanking_mock"):
    sys.path.insert(0, str(p))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from case_store import CaseStore, canonical, timestamp  # noqa: E402
from confidence import LOWEST, level_of, score_of  # noqa: E402
from confidence import scale as confidence_scale  # noqa: E402
from demo_evidence import mask, transaction_evidence  # noqa: E402
from explain import explain_p50  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi import Request as WebRequest  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from income_pipeline import build_row  # noqa: E402
from income_predictor import IncomeModel  # noqa: E402
from obmock.config import WorldConfig  # noqa: E402
from obmock.dialects.registry import build_dialects  # noqa: E402
from obmock.engine.gateway import Request, Response, Router  # noqa: E402
from obmock.export import capture_customer  # noqa: E402
from obmock.harvest import harvest, to_table  # noqa: E402
from obmock.world.model import World  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from registry_adapter import to_registry  # noqa: E402

from EDA import CODE_KINDS, text_kinds  # noqa: E402

BANKS_URL = "http://127.0.0.1:8000"
REGISTRIES_URL = "http://127.0.0.1:8001"
PSU_IP = "203.0.113.7"
HISTORY_MONTHS = 12
# Below this the data is too thin to say anything: confidence is forced to "very low".
MIN_MONTHS_COVERED = 3.0
MIN_TRANSACTIONS = 30
TPP_KEYS = {"procredit": {"Ocp-Apim-Subscription-Key": "demo-tpp-key"}}  # issued to the TPP at onboarding (APIM)
CALIBRATION = REPO / "income" / "models" / "confidence_calibration.json"  # income/confidence_calibration.py
FAIRNESS = REPO / "income" / "models" / "fairness_report.json"  # income/fairness_check.py
OFFICER = "demo-officer"
DECISIONS = ("accept", "request_more_evidence", "reject")
MIN_REASON = 10
DEMO_CLIENTS = HERE / "demo_clients.json"  # API/pick_demo_clients.py
BANK_LABELS = {"tbc": "TBC", "bog": "Bank of Georgia"}
REGISTRY_LABELS = {"rs": "Revenue Service", "creditinfo": "Creditinfo"}


# ----------------------------------------------------------------------------- the TPP side: one bank over HTTP
class HttpGateway:
    """Quacks like obmock's Gateway for `capture_customer`, but every call is a real HTTP request with the consent."""

    def __init__(self, client: httpx.Client, credentials: dict[str, tuple[str, str]]) -> None:
        self.client = client
        self.credentials = credentials  # bank -> (Authorization header, consent id)
        self.dialects = build_dialects(World(WorldConfig()))  # the routes of each bank's API, no data
        self.demo_unavailable = False
        self.on_call = None

    def handle(self, bank: str, req: Request) -> Response:
        authorization, consent_id = self.credentials[bank]
        headers = {
            "Authorization": authorization,
            self.dialects[bank].consent_header: consent_id,
            "X-Request-ID": str(uuid.uuid4()),
            "PSU-IP-Address": PSU_IP,
            "Accept": "application/json",
            **TPP_KEYS.get(bank, {}),
        }
        if self.demo_unavailable:
            headers["X-Demo-Failure"] = "unavailable"
        start = time.perf_counter()
        r = self.client.request(req.method, f"/{bank}{req.path}", params=req.query, headers=headers)
        if self.on_call:
            self.on_call({"source": bank, "method": req.method,
                          "route": re.sub(r"/[0-9a-f-]{20,}", "/{resource}", req.path),
                          "http_status": r.status_code, "duration_ms": round((time.perf_counter()-start)*1000, 2)})
        return Response(r.status_code, r.json() if r.content else None)


def fetch_bank(gateway: HttpGateway, bank: str, subject: str, date_from: str) -> dict[str, list[dict]]:
    """accounts / balances / transactions rows of one bank, flattened exactly like the dataset's api/ tables."""
    capture = capture_customer(gateway, bank, subject, date_from)
    denied = [c for c in capture["calls"] if c["status"] in (401, 403)]
    if denied:
        raise PermissionError(f"{bank} refused the consent ({denied[0]['status']})")
    if any(c["status"] >= 500 for c in capture["calls"]):
        raise ConnectionError(f"{bank} returned an unavailable response")
    d = gateway.dialects[bank]
    return harvest(capture, Router(d.routes()), d.normalize_path)


# ----------------------------------------------------------------------------- cleaning
def clean(tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """What in the received transactions is not income or spending, counted from the data itself.

    The model's feature code (EDA.scan_shard) leaves exactly these out of the income and spending features, so the
    rows stay in the tables it reads (they still count as activity, as in training): removing them here would feed
    the model something it was not trained on."""
    tx, acc = tables["transactions"], tables["accounts"]
    if tx.empty:
        return {"received": 0, "pending": 0, "card_mirrors": 0, "own_account_transfers": 0, "loan_disbursements": 0,
                "refunds": 0, "used_for_income": 0}  # fmt: skip
    mirror = tx["is_card_mirror"].fillna(False).astype(bool)
    booked = (tx["status"] == "booked") & ~mirror
    own_ibans = set(acc["iban"].dropna())
    by_iban = tx["creditor_iban"].isin(own_ibans) | tx["debtor_iban"].isin(own_ibans)
    kind = text_kinds(tx["remittance_information"])
    by_code = tx["bank_transaction_code"].map(CODE_KINDS).to_numpy(object)
    kind = np.where(np.isin(kind, ("none", "other")), np.where(pd.notna(by_code), by_code, "other"), kind)
    own = booked & (by_iban | (kind == "own"))
    loan = booked & ((kind == "loan_in") | (tx["purpose_code"] == "LOAN") | (kind == "quick_loan"))
    return {
        "received": len(tx),
        "pending": int((tx["status"] == "pending").sum()),
        "card_mirrors": int(mirror.sum()),
        "own_account_transfers": int(own.sum()),
        "own_account_transfers_matched_by_iban": int((booked & by_iban).sum()),
        "loan_disbursements": int(loan.sum()),
        "refunds": 0,  # the bank APIs have no refund entry type: nothing to remove
        "used_for_income": int((booked & ~own & ~loan).sum()),
    }


# ----------------------------------------------------------------------------- confidence
def judge_confidence(
    model_level: str, months: float, n_tx: int, features: dict, banks_used: int, banks_failed: int = 0
) -> tuple[str, list[str], list[str]]:
    """(level, reasons for a downgrade, warnings). Thin data forces 'very low'; a missing salary is only a warning,
    because self-employed clients legitimately have none."""
    reasons, warnings = [], []
    if banks_used == 0:
        reasons.append("no bank data could be retrieved")
    if months < MIN_MONTHS_COVERED:
        reasons.append(f"only {months:.1f} months of history (need {MIN_MONTHS_COVERED:g})")
    if n_tx < MIN_TRANSACTIONS:
        reasons.append(f"only {n_tx} transactions (need {MIN_TRANSACTIONS})")
    inflow = features.get("ob_inflow_month")
    if inflow is None or not np.isfinite(inflow) or inflow <= 0:
        reasons.append("no income-like inflow found")
    salary = features.get("ob_salary_month")
    if (salary is None or not np.isfinite(salary) or salary <= 0) and not banks_failed:  # a failed bank may pay it
        warnings.append(
            "No salary credits found. The bank that pays the salary may not be connected; ask the client to connect it."
        )
    return (LOWEST if reasons else model_level), reasons, warnings


# ----------------------------------------------------------------------------- the service
def mask_name(text: str | None) -> str | None:
    """Show the first two letters of every word and hide the rest: 'Salome Abashidze' -> 'Sa**** Ab*******'.
    Works for any script (Georgian included). The full name stays inside the service."""
    if not text:
        return text
    return " ".join(w[: (2 if len(w) > 2 else 1)] + "*" * (len(w) - (2 if len(w) > 2 else 1)) for w in str(text).split())


def _same_name(kyc: dict, owner_name: str | None) -> bool:
    """Does a bank's account owner name match the KYC name (either script, any word order)?"""
    words = lambda text: sorted(str(text).replace(",", " ").upper().split())  # noqa: E731
    return bool(owner_name) and words(owner_name) in (words(kyc["name_en"]), words(kyc["name_ka"]))


class EstimateRequest(BaseModel):
    consent_set_id: str
    applicant: dict[str, Any] | None = None  # overrides the KYC record: age, sex, region_name, city
    demo_failure: Literal["none", "rs_unavailable", "banks_unavailable"] = "none"


class ConsentRequest(BaseModel):
    personal_id: str


class ReviewRequest(BaseModel):
    decision: Literal["accept", "request_more_evidence", "reject"]
    reason: str = ""
    acknowledged_uncertainty: bool = False


class Service:
    def __init__(
        self,
        banks: httpx.Client,
        registries: httpx.Client,
        model: IncomeModel | None = None,
        as_of: date | None = None,
        store: CaseStore | None = None,
    ) -> None:
        self.banks, self.registries = banks, registries
        self.as_of = as_of  # None: the window ends where the banks' data ends
        self.model = model or IncomeModel.load()
        self.store = store or CaseStore(REPO / ".runtime" / "audit.sqlite3")
        self.calibration = json.loads(CALIBRATION.read_text()) if CALIBRATION.exists() else None
        self.pool = ThreadPoolExecutor(max_workers=24)
        self.consent_sets: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self._catalog: list[dict] | None = None
        self._data_end: date | None = None

    # ---- catalogue of banks ----
    def catalog(self) -> list[dict]:
        if self._catalog is None:
            self._catalog = self.banks.get("/_mock/banks").json()
        return self._catalog

    def _gateway_for(self, entries: dict[str, dict]) -> HttpGateway:
        return HttpGateway(self.banks, {b: (e["authorization"], e["consent_id"]) for b, e in entries.items()})

    # ---- phase 1: consents ----
    def _bank_consent(self, bank: dict, personal_id: str) -> dict:
        """One bank's consent. Whatever goes wrong marks this bank as failed; the others carry on."""
        try:
            return self._bank_consent_call(bank, personal_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return {
                "bank": bank["bank"], "name": bank["name"], "status": "error", "consent_id": None, "accounts": 0,
                "message": f"{bank['name']} is temporarily unavailable. The other banks continued.",
            }  # fmt: skip

    def _bank_consent_call(self, bank: dict, personal_id: str) -> dict:
        key = bank["bank"]
        out = {"bank": key, "name": bank["name"], "status": "error", "consent_id": None, "accounts": 0}
        r = self.banks.post(f"/_mock/session/{key}", params={"customer": personal_id})
        if r.status_code == 404:
            out["status"] = "unknown_client"
            return out
        r.raise_for_status()
        session = r.json()
        out.update(
            consent_id=session["consentId"], authorization=session["authorization"], valid_until=session["validUntil"]
        )
        gateway = self._gateway_for({key: {"authorization": out["authorization"], "consent_id": out["consent_id"]}})
        d = gateway.dialects[key]
        reply = gateway.handle(key, Request("GET", d.accounts_path, {}, {}, None, ""))
        if reply.status != 200:
            out["status"] = "error"
            out["message"] = f"accounts call answered {reply.status}"
            return out
        accounts = next((v for v in reply.body.values() if isinstance(v, list)), [])
        out["accounts"] = len(accounts)
        # the account owner as this bank knows them, used to confirm the client's name
        out["owner_name"] = next(
            (a["ownerName"] for a in accounts if isinstance(a, dict) and a.get("ownerName")), None
        )  # only the owner field: some banks put an IBAN or a label in `name`
        out["status"] = "connected" if accounts else "no_relationship"  # no account: drop the bank
        return out

    def _registry_consent(self, service: str, personal_id: str) -> dict:
        try:
            return self._registry_consent_call(service, personal_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"source": service, "status": "error", "consent_id": None, "message": f"{REGISTRY_LABELS[service]} is temporarily unavailable."}

    def _registry_consent_call(self, service: str, personal_id: str) -> dict:
        out = {"source": service, "status": "error", "consent_id": None}
        created = self.registries.post(f"/{service}/v1/consents", json={"personId": personal_id})
        if created.status_code != 201:
            out["status"] = "unknown_client" if created.status_code == 400 else "error"
            return out
        consent_id = created.json()["consentId"]
        approved = self.registries.post(f"/_mock/consents/{service}/{consent_id}/authorise")  # skip mode
        if approved.status_code != 200:
            return out
        out.update(status="connected", consent_id=consent_id, authorization=approved.json()["authorization"])
        return out

    def create_consents(self, personal_id: str) -> dict | None:
        banks = self.catalog()
        futures = [self.pool.submit(self._bank_consent, b, personal_id) for b in banks]
        registry_futures = [self.pool.submit(self._registry_consent, s, personal_id) for s in ("rs", "creditinfo")]
        bank_results = [f.result() for f in futures]
        rs, creditinfo = (f.result() for f in registry_futures)
        if all(b["status"] == "unknown_client" for b in bank_results):
            return None
        record = {
            "consent_set_id": uuid.uuid4().hex[:12],
            "personal_id": personal_id,
            "banks": sorted(bank_results, key=lambda b: (b["status"] != "connected", -b["accounts"], b["bank"])),
            "rs": rs,
            "creditinfo": creditinfo,
            "kyc": self._kyc(personal_id, bank_results),
        }
        self.consent_sets[record["consent_set_id"]] = record
        return record

    def today(self) -> date:
        """End of the history window: --as-of if given, else the day the banks' data ends. Real banks have data up to
        today; the mock banks stop on the day they were generated, so asking them for later days only finds gaps."""
        if self.as_of:
            return self.as_of
        if self._data_end is None:
            try:
                self._data_end = date.fromisoformat(self.banks.get("/_mock/clock").json()["as_of"])
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self._data_end = date.today()
        return self._data_end

    def _kyc(self, personal_id: str, bank_results: list[dict]) -> dict | None:
        """The client's profile for the lender, and where each part of it comes from."""
        try:
            reply = self.banks.get(f"/_mock/kyc/{personal_id}")
            record = reply.json() if reply.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            record = None
        if record is None:
            return None
        today = self.today()
        born = date.fromisoformat(record["birth_date"])
        record["age"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        seen = [
            {
                "bank": b["bank"],
                "bank_name": b["name"],
                "owner_name": b["owner_name"],
                "matches": _same_name(record, b["owner_name"]),
            }
            for b in bank_results
            if b["status"] == "connected" and b.get("owner_name")
        ]
        # the names were compared in full above; from here on only the partly hidden form leaves the service
        record["name_check"] = [{**c, "owner_name": mask_name(c["owner_name"])} for c in seen]
        record["name_en"], record["name_ka"] = mask_name(record["name_en"]), mask_name(record["name_ka"])
        record["provenance"] = {
            "name": "Lender's KYC record (ID card at onboarding); shown partly hidden",
            "birth_date": "Lender's KYC record (ID card at onboarding)",
            "age": f"Calculated from the date of birth on {today.isoformat()}",
            "place": "Lender's KYC record (registered address)",
            "personal_id": "Entered by the loan officer",
            "name_check": "Account owner name returned by each connected bank's open banking API",
        }
        return record

    @staticmethod
    def public_consents(record: dict) -> dict:
        hide = {"authorization"}
        scrub = lambda e: {k: v for k, v in e.items() if k not in hide}  # noqa: E731
        return {
            "consent_set_id": record["consent_set_id"],
            "personal_id": record["personal_id"],
            "banks": [scrub(b) for b in record["banks"]],
            "rs": scrub(record["rs"]),
            "creditinfo": scrub(record["creditinfo"]),
            "kyc": record["kyc"],
            "connected_banks": sum(b["status"] == "connected" for b in record["banks"]),
        }

    # ---- phase 2: the estimate ----
    def start(self, req: EstimateRequest, owner: str = "demo") -> dict | None:
        record = self.consent_sets.get(req.consent_set_id)
        if record is None:
            return None
        job_id = uuid.uuid4().hex[:12]
        sources = [b for b in record["banks"] if b["status"] == "connected"]
        job = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "created": time.time(),
            "progress": [
                {"source": s["bank"], "label": BANK_LABELS.get(s["bank"], s["name"]), "state": "pending", "detail": ""}
                for s in sources
            ]
            + [
                {"source": name, "label": label, "state": "pending", "detail": ""}
                for name, label in (("rs", "Revenue Service"), ("creditinfo", "Creditinfo"))
            ],
            "result": None,
            "error": None,
            "subject": next((d["id"] for d in json.loads(DEMO_CLIENTS.read_text()) if d["personal_id"] == record["personal_id"]), mask(record["personal_id"])),
            "scenario": req.demo_failure,
            "model_sha256": getattr(self.model, "artifact_sha256", "unavailable"),
            "trace": [], "review": None,
            "consent_summary": {"mode": "Simulated automatic authorization; subsequent source calls validate consent",
                                "scope": ["accounts", "balances", "transactions (12 months)", "RS remittances", "Creditinfo"],
                                "sources": [{"source": b["bank"], "status": b["status"], "accounts": b["accounts"],
                                             "consent": mask(b.get("consent_id")), "valid_until": b.get("valid_until")}
                                            for b in record["banks"]] +
                                    [{"source": key, "status": record[key]["status"], "consent": mask(record[key].get("consent_id"))}
                                     for key in ("rs", "creditinfo")]},
            "_owner": owner, "_started": time.perf_counter(), "_lock": threading.RLock(),
        }
        self.jobs[job_id] = job
        self._save(job)
        self._event(job, "Case created", {"scenario": req.demo_failure, "model_sha256": job["model_sha256"],
                                          "consents": job["consent_summary"]})
        self.pool.submit(self._run, job, record, req.applicant)
        return job

    def _save(self, job: dict) -> None:
        self.store.save(self.public_job(job), job["_owner"])

    def _event(self, job: dict, stage: str, detail: dict | None = None) -> None:
        with job["_lock"]:
            event = {"at": timestamp(), "elapsed_ms": round((time.perf_counter() - job["_started"]) * 1000, 2),
                     "stage": stage, "detail": detail or {}}
            job["trace"].append(event)
            self.store.append(job["job_id"], stage, "system", event)
            self._save(job)

    @staticmethod
    def _set(job: dict, source: str, state: str, detail: str = "") -> None:
        for step in job["progress"]:
            if step["source"] == source:
                step["state"], step["detail"] = state, detail

    def _check_consent(self, client: httpx.Client, path: str, extra_headers: dict | None = None) -> str | None:
        try:
            reply = client.get(path, headers={"X-Request-ID": str(uuid.uuid4()), **(extra_headers or {})})
        except httpx.HTTPError as exc:
            return type(exc).__name__
        if reply.status_code != 200:
            return f"consent status call answered {reply.status_code}"
        status = reply.json().get("consentStatus")
        return None if status == "valid" else f"consent is {status}"

    def _pull_bank(self, job: dict, gateway: HttpGateway, entry: dict, date_from: str, personal_id: str):
        bank = entry["bank"]
        try:
            return self._pull_bank_call(job, gateway, entry, date_from, personal_id)
        except Exception as e:  # noqa: BLE001  one failing bank must not sink the estimate
            self._set(job, bank, "failed", "temporarily unavailable")
            self._event(job, "Source request failed", {"source": bank, "reason": type(e).__name__})
            return bank, None

    def _pull_bank_call(self, job: dict, gateway: HttpGateway, entry: dict, date_from: str, personal_id: str):
        start = time.perf_counter()
        bank = entry["bank"]
        self._set(job, bank, "running", "checking consent")
        self._event(job, "Source request started", {"source": bank})
        d = gateway.dialects[bank]
        status_route = next((t for m, t, op in d.route_table() if m == "GET" and op == "consent_status"), None)
        problem = (
            self._check_consent(self.banks, f"/{bank}{status_route.format(consentId=entry['consent_id'])}",
                                {"X-Demo-Failure": "unavailable"} if job["scenario"] == "banks_unavailable" else {})
            if status_route
            else None
        )
        if problem:
            self._set(job, bank, "failed", "temporarily unavailable")
            self._event(job, "Source request failed", {"source": bank, "reason": problem,
                                                       "duration_ms": round((time.perf_counter()-start)*1000, 2)})
            return bank, None
        self._set(job, bank, "running", "fetching accounts and transactions")
        try:
            rows = fetch_bank(gateway, bank, personal_id, date_from)
        except Exception as e:  # noqa: BLE001  one failing bank must not sink the estimate
            self._set(job, bank, "failed", "temporarily unavailable")
            self._event(job, "Source request failed", {"source": bank, "reason": type(e).__name__})
            return bank, None
        n_acc = len(
            {
                r["resource_id"]
                for r in rows["accounts"]
                if r["source_endpoint"] == "accounts" and r["currency"] != "XXX"
            }
        )
        n_tx = sum(1 for r in rows["transactions"] if not r["is_card_mirror"] and r["status"] == "booked")
        self._set(job, bank, "done", f"{n_acc} account{'' if n_acc == 1 else 's'}, {n_tx} transactions")
        self._event(job, "Source request completed", {"source": bank, "accounts": n_acc, "transactions": n_tx,
                                                     "balances": len(rows["balances"]),
                                                     "duration_ms": round((time.perf_counter()-start)*1000, 2)})
        return bank, rows

    def _pull_registry(self, job: dict, service: str, entry: dict, path: str):
        start = time.perf_counter()
        self._set(job, service, "running", "checking consent")
        self._event(job, "Source request started", {"source": service})
        try:
            if entry.get("status") != "connected":
                raise PermissionError("consent was not granted")
            headers = {"Authorization": entry["authorization"], "Consent-ID": entry["consent_id"]}
            if service == "rs" and job["scenario"] == "rs_unavailable":
                headers["X-Demo-Failure"] = "unavailable"
            problem = self._check_consent(self.registries, f"/{service}/v1/consents/{entry['consent_id']}/status")
            if problem:
                raise PermissionError(problem)
            reply = self.registries.get(path, headers=headers)
            reply.raise_for_status()
            body = reply.json()
        except Exception as exc:
            detail = "consent not granted" if isinstance(exc, PermissionError) else "unavailable"
            self._set(job, service, "failed", detail)
            self._event(job, "Source request failed", {"source": service, "reason": detail,
                                                       "duration_ms": round((time.perf_counter()-start)*1000, 2)})
            return None
        self._set(job, service, "done", "data gathered")
        self._event(job, "Source request completed", {"source": service, "http_status": reply.status_code,
                                                     "duration_ms": round((time.perf_counter()-start)*1000, 2)})
        return body

    def _run(self, job: dict, record: dict, applicant: dict | None) -> None:
        try:
            job["status"], job["stage"] = "running", "fetching"
            collection_start = time.perf_counter()
            self._event(job, "Parallel collection started", {"sources": [p["source"] for p in job["progress"]]})
            as_of = pd.Timestamp(self.today())
            date_from = (as_of - pd.DateOffset(months=HISTORY_MONTHS)).date().isoformat()
            connected = {b["bank"]: b for b in record["banks"] if b["status"] == "connected"}
            gateway = self._gateway_for(connected)
            gateway.demo_unavailable = job["scenario"] == "banks_unavailable"
            gateway.on_call = lambda detail: self._event(job, "Bank API call", detail)
            pid = record["personal_id"]
            bank_jobs = [self.pool.submit(self._pull_bank, job, gateway, e, date_from, pid) for e in connected.values()]
            rs_job = self.pool.submit(self._pull_registry, job, "rs", record["rs"], "/rs/v1/income-declarations")
            ci_job = self.pool.submit(
                self._pull_registry, job, "creditinfo", record["creditinfo"], "/creditinfo/v1/credit-report"
            )
            pulled = dict(f.result() for f in bank_jobs)
            rs_body, ci_body = rs_job.result(), ci_job.result()
            self._event(job, "Parallel collection completed", {"duration_ms": round((time.perf_counter()-collection_start)*1000, 2)})

            job["stage"] = "cleaning"
            normalize_start = time.perf_counter()
            got = {b: r for b, r in pulled.items() if r is not None}
            tables = {
                t: to_table(t, [row for r in got.values() for row in r[t]]).to_pandas()
                for t in ("accounts", "balances", "transactions")
            }
            self._event(job, "Bank dialects normalized", {"rows": {t: len(frame) for t, frame in tables.items()},
                                                        "duration_ms": round((time.perf_counter()-normalize_start)*1000, 2)})
            evidence_start = time.perf_counter()
            cleaning = clean(tables)
            evidence = transaction_evidence(tables, {**self.model.state["ob_window"], "as_of": as_of.date().isoformat()})
            evidence["registries"] = {
                "rs": {key: rs_body[key] for key in ("taxpayer", "incomeItems")} if rs_body else None,
                "creditinfo": {key: ci_body[key] for key in ("inquiries", "contracts")} if ci_body else None,
            }
            evidence["aggregation_ms"] = round((time.perf_counter()-evidence_start)*1000, 3)
            self._event(job, "Transaction evidence generated", {"cleaning": cleaning,
                                                              "service_payments": evidence["service_payments"]["count"],
                                                              "snapshot_sha256": evidence["snapshot_sha256"],
                                                              "duration_ms": evidence["aggregation_ms"]})

            job["stage"] = "scoring"
            feature_start = time.perf_counter()
            kyc = {**(record["kyc"] or {}), **(applicant or {})}
            registry = to_registry(kyc, rs_body, ci_body)
            open_banking = tables if got else None
            row = build_row(registry, open_banking, self.model, as_of=as_of.date().isoformat())
            features = {k: v for k, v in row.items() if k.startswith("ob_")}
            self._event(job, "Model features generated", {
                "feature_count": len(self.model.state["feature_names"]),
                "salary_credits_gel": float(row["ob_salary_month"]) if pd.notna(row.get("ob_salary_month")) else None,
                "rs_remittances_gel": registry.get("INCOME_AMOUNT_SOI04"),
                "duration_ms": round((time.perf_counter()-feature_start)*1000, 2)})

            tx = tables["transactions"]
            booked = (
                tx[(tx["status"] == "booked") & ~tx["is_card_mirror"].fillna(False).astype(bool)] if len(tx) else tx
            )
            months = float((as_of - pd.to_datetime(booked["booking_date"]).min()).days / 30.44) if len(booked) else 0.0
            _, reasons, warnings = judge_confidence(
                LOWEST, months, len(booked), features, len(got),
                sum(1 for p in job["progress"] if p["state"] == "failed" and p["source"] not in REGISTRY_LABELS),
            )
            if reasons:
                income, explanation, band, level = None, None, None, LOWEST
                self._event(job, "Inference withheld", {"reasons": reasons})
            else:
                infer_start = time.perf_counter()
                out = self.model.predict(row).iloc[0]
                income = {k: float(out[k]) for k in ("p10", "p50", "p90")} | {"currency": "GEL", "period": "monthly"}
                band = float(out["band_share"])
                level = level_of(band)
                self._event(job, "ML inference completed", {"duration_ms": round((time.perf_counter()-infer_start)*1000, 2),
                                                          "p50_gel": income["p50"]})
                explain_start = time.perf_counter()
                matrix = self.model.transform(self.model.prepare(row))
                explanation = explain_p50(self.model, matrix, income["p50"])
                self._event(job, "TreeSHAP explanation completed", {"duration_ms": round((time.perf_counter()-explain_start)*1000, 2)})
            failed_banks = [s for s in job["progress"] if s["state"] == "failed" and s["source"] not in REGISTRY_LABELS]
            if failed_banks and not got:
                warnings.append("No bank could be reached, so the estimate was withheld.")
            else:
                warnings.extend(f"{s['label']} is temporarily unavailable. The other banks continued." for s in failed_banks)
            if not rs_body:
                warnings.append("Revenue Service data unavailable. Based on bank data only.")
            if not ci_body:
                warnings.append("Creditinfo data unavailable: existing loans were not considered.")
            job["result"] = {
                "income": income,
                "outcome": "abstained" if reasons else "estimated",
                "confidence": {
                    "level": level,
                    "model_level": level if band is not None else None,
                    "band_share": band,
                    "plus_minus_pct": round(50 * band, 1) if band is not None else None,
                    "scale": confidence_scale(),
                    "downgrade_reasons": reasons,
                    "score": self._score(band, bool(reasons)) if band is not None else None,
                    "measured": self._measured(level, reasons) if all(p["state"] == "done" for p in job["progress"]) else None,
                },
                "explanation": explanation,
                "warnings": warnings,
                "data": {
                    "banks_used": sorted(got),
                    "banks_used_names": [connected[b]["name"] for b in sorted(got)],
                    "months_covered": round(months, 1),
                    "transactions_used": cleaning["used_for_income"],
                },
                "sources_used": {"rs": bool(rs_body), "creditinfo": bool(ci_body)},
                "cleaning": cleaning,
                "rs_declared": {"SOI04": registry.get("INCOME_AMOUNT_SOI04")} if rs_body else None,
                "evidence": evidence,
                "partial_sources": any(p["state"] == "failed" for p in job["progress"]),
            }
            job["processing_ms"] = round((time.perf_counter()-job["_started"])*1000, 2)
            self._event(job, "Estimate withheld" if reasons else "Estimate completed", {"outcome": job["result"]["outcome"], "processing_ms": job["processing_ms"],
                                                           "result_sha256": hashlib.sha256(canonical(job["result"]).encode()).hexdigest()})
            job["status"], job["stage"] = "done", "done"
            self._save(job)
        except Exception as e:  # noqa: BLE001
            job["status"], job["stage"] = "failed", "failed"
            job["error"] = (
                "A data source cannot be reached right now. Try again in a moment."
                if isinstance(e, httpx.HTTPError)
                else "The assessment could not be completed. Try again or contact support."
            )
            self._event(job, "Case failed", {"error": re.sub(r"\d{11}", "[redacted]", f"{type(e).__name__}: {e}")[:200]})

    @staticmethod
    def _score(band_share: float, downgraded: bool) -> int:
        """100% minus the band width (income/confidence.py). Thin data forces the 'very low' label, so the score
        is also kept inside that label's range (a band wider than 20%, i.e. below 80%)."""
        score = score_of(band_share)
        return min(score, 79) if downgraded else score

    def _measured(self, level: str, downgrade_reasons: list[str]) -> dict | None:
        """What this confidence level has meant on held-out clients. A downgrade for thin data was never measured."""
        if not self.calibration or downgrade_reasons:
            return None
        return {
            **self.calibration["levels"][level],
            "interval": self.calibration["interval"],
            "basis": self.calibration["basis"],
        }

    @staticmethod
    def public_job(job: dict) -> dict:
        return {k: v for k, v in job.items() if not k.startswith("_") and k != "created"}


def create_app(
    banks: httpx.Client | None = None, registries: httpx.Client | None = None, model: IncomeModel | None = None,
    store: CaseStore | None = None,
    *, as_of: date | None = None,
) -> FastAPI:
    svc = Service(
        banks or httpx.Client(base_url=BANKS_URL, timeout=60),
        registries or httpx.Client(base_url=REGISTRIES_URL, timeout=60),
        model,
        store=store, as_of=as_of,
    )
    app = FastAPI(
        title="Income estimation API", description="Open banking + RS + Creditinfo -> P10 / P50 / P90 monthly income."
    )
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.state.service = svc
    def case_for(case_id: str) -> dict:
        case = svc.jobs.get(case_id) or svc.store.get(case_id)
        if case is None:
            raise HTTPException(404, "Case not found")
        return case

    def check_write_origin(request: WebRequest) -> None:
        if request.headers.get("origin") and request.headers["origin"] not in {
            "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:9000", "http://127.0.0.1:9000",
            str(request.base_url).rstrip("/"),  # compiled frontend served by this API in production
        }:
            raise HTTPException(403, "Cross-origin write rejected")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "model_trained_to": svc.model.state["ob_window"]["as_of"],
            "model_window": svc.model.state["ob_window"]["as_of"],
            "history_window_ends": svc.today().isoformat(),
            "today": date.today().isoformat(),
            "service": "income-estimation",
        }

    @app.get("/v1/sources")
    def sources():
        """Everything a consent request goes to, so a portal can show all of them while it waits."""
        banks = [{"key": b["bank"], "label": b["name"], "kind": "bank"} for b in svc.catalog()]
        return banks + [
            {"key": "rs", "label": "Revenue Service", "kind": "registry"},
            {"key": "creditinfo", "label": "Creditinfo", "kind": "registry"},
        ]

    @app.get("/v1/demo/clients")
    def demo_clients():
        return json.loads(DEMO_CLIENTS.read_text()) if DEMO_CLIENTS.exists() else []

    @app.post("/v1/consents", status_code=201)
    def create_consents(body: ConsentRequest, request: WebRequest):
        check_write_origin(request)
        try:
            record = svc.create_consents(body.personal_id.strip())
        except httpx.HTTPError:
            return JSONResponse(
                {"error": "GATEWAY_UNREACHABLE", "text": "The banks cannot be reached right now. Try again in a moment."},
                status_code=502,
            )
        if record is None:
            return JSONResponse({"error": "UNKNOWN_CLIENT", "text": "no bank knows this personal id"}, status_code=404)
        return svc.public_consents(record)

    @app.get("/v1/consents/{consent_set_id}")
    def get_consents(consent_set_id: str):
        record = svc.consent_sets.get(consent_set_id)
        return (
            svc.public_consents(record) if record else JSONResponse({"error": "UNKNOWN_CONSENT_SET"}, status_code=404)
        )

    @app.post("/v1/income-estimates", status_code=202)
    def create_estimate(body: EstimateRequest, request: WebRequest):
        check_write_origin(request)
        job = svc.start(body)
        if job is None:
            return JSONResponse({"error": "UNKNOWN_CONSENT_SET"}, status_code=404)
        return {"job_id": job["job_id"], "status": job["status"], "status_url": f"/v1/income-estimates/{job['job_id']}"}

    @app.get("/v1/income-estimates/{job_id}")
    def get_estimate(job_id: str):
        return svc.public_job(case_for(job_id))

    @app.get("/v1/cases/{case_id}")
    def case(case_id: str):
        return svc.public_job(case_for(case_id))

    @app.get("/v1/cases/{case_id}/audit")
    def audit(case_id: str):
        case_for(case_id)
        return svc.store.audit(case_id)

    @app.get("/v1/model/fairness")
    def fairness():
        if not FAIRNESS.exists():
            raise HTTPException(404, "Run income/fairness_check.py to create the fairness report")
        return json.loads(FAIRNESS.read_text())

    @app.post("/v1/cases/{case_id}/review")
    def review(case_id: str, body: ReviewRequest, request: WebRequest):
        """The officer decides. The model only suggests: a decision is stored once, in the hash-chained audit."""
        check_write_origin(request)
        reason = body.reason.strip()
        with svc.store.lock:
            case = case_for(case_id)
            if case["status"] != "done":
                raise HTTPException(409, "Only a completed assessment can be decided")
            if case.get("review"):
                raise HTTPException(409, "This case already has an officer decision")
            result = case["result"]
            withheld = result["income"] is None
            if body.decision == "accept" and withheld:
                raise HTTPException(422, "No estimate was produced, so there is nothing to accept")
            risky = withheld or result["confidence"]["level"] in ("low", "very low") or result["partial_sources"]
            if risky and len(reason) < MIN_REASON:
                raise HTTPException(
                    422, f"Give a reason of at least {MIN_REASON} characters: confidence is low or evidence is missing"
                )
            if risky and not body.acknowledged_uncertainty:
                raise HTTPException(422, "Confirm that you reviewed the uncertainty and the evidence")
            decision = {
                "decision": body.decision, "reason": reason, "officer": OFFICER, "at": timestamp(),
                "acknowledged_uncertainty": body.acknowledged_uncertainty,
                "scope": "Income estimate decision, not loan approval",
            }  # fmt: skip
            case["review"] = decision
            svc.store.append(case_id, "officer_decision", OFFICER, decision)
            svc.store.save(svc.public_job(case), case.get("_owner", case.get("owner")))
        return decision

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--banks-url", default=BANKS_URL)
    parser.add_argument("--registries-url", default=REGISTRIES_URL)
    parser.add_argument(
        "--as-of", type=date.fromisoformat, help="end of the history window (default: the day the bank data ends)"
    )
    args = parser.parse_args()
    app = create_app(
        httpx.Client(base_url=args.banks_url, timeout=120),
        httpx.Client(base_url=args.registries_url, timeout=60),
        as_of=args.as_of,
    )
    uvicorn.run(app, host=args.host, port=args.port)
