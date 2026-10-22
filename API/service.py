#!/usr/bin/env python3
"""Income estimation API: what the lender's portal calls.

  POST /v1/consents                     personal id -> consent requests to all 12 banks + RS + Creditinfo, in parallel
  GET  /v1/consents/{consent_set_id}    the same table again
  POST /v1/income-estimates             consent_set_id (+ optional loan_request) -> 202 {job_id}
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
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (HERE, REPO / "income", REPO / "eda", REPO / "openbanking_mock"):
    sys.path.insert(0, str(p))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from confidence import LOWEST, level_of, score_of  # noqa: E402
from confidence import scale as confidence_scale  # noqa: E402
from explain import explain_p50  # noqa: E402
from fastapi import FastAPI  # noqa: E402
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
DEMO_CLIENTS = HERE / "demo_clients.json"  # API/pick_demo_clients.py
BANK_LABELS = {"tbc": "TBC", "bog": "Bank of Georgia"}


# ----------------------------------------------------------------------------- the TPP side: one bank over HTTP
class HttpGateway:
    """Quacks like obmock's Gateway for `capture_customer`, but every call is a real HTTP request with the consent."""

    def __init__(self, client: httpx.Client, credentials: dict[str, tuple[str, str]]) -> None:
        self.client = client
        self.credentials = credentials  # bank -> (Authorization header, consent id)
        self.dialects = build_dialects(World(WorldConfig()))  # the routes of each bank's API, no data

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
        r = self.client.request(req.method, f"/{bank}{req.path}", params=req.query, headers=headers)
        return Response(r.status_code, r.json() if r.content else None)


def fetch_bank(gateway: HttpGateway, bank: str, subject: str, date_from: str) -> dict[str, list[dict]]:
    """accounts / balances / transactions rows of one bank, flattened exactly like the dataset's api/ tables."""
    capture = capture_customer(gateway, bank, subject, date_from)
    denied = [c for c in capture["calls"] if c["status"] in (401, 403)]
    if denied:
        raise PermissionError(f"{bank} refused the consent ({denied[0]['status']})")
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
    model_level: str, months: float, n_tx: int, features: dict, banks_used: int
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
    if salary is None or not np.isfinite(salary) or salary <= 0:
        warnings.append(
            "No salary credits found. The bank that pays the salary may not be connected; ask the client to connect it."
        )
    return (LOWEST if reasons else model_level), reasons, warnings


# ----------------------------------------------------------------------------- the service
class EstimateRequest(BaseModel):
    consent_set_id: str
    applicant: dict[str, Any] | None = None  # overrides the KYC record: age, sex, region_name, city
    loan_request: dict[str, Any] | None = None  # echoed back; the affordability step is not part of this API yet


class ConsentRequest(BaseModel):
    personal_id: str


class Service:
    def __init__(self, banks: httpx.Client, registries: httpx.Client, model: IncomeModel | None = None) -> None:
        self.banks, self.registries = banks, registries
        self.model = model or IncomeModel.load()
        self.calibration = json.loads(CALIBRATION.read_text()) if CALIBRATION.exists() else None
        self.pool = ThreadPoolExecutor(max_workers=24)
        self.consent_sets: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self._catalog: list[dict] | None = None

    # ---- catalogue of banks ----
    def catalog(self) -> list[dict]:
        if self._catalog is None:
            self._catalog = self.banks.get("/_mock/banks").json()
        return self._catalog

    def _gateway_for(self, entries: dict[str, dict]) -> HttpGateway:
        return HttpGateway(self.banks, {b: (e["authorization"], e["consent_id"]) for b, e in entries.items()})

    # ---- phase 1: consents ----
    def _bank_consent(self, bank: dict, personal_id: str) -> dict:
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
        out["status"] = "connected" if accounts else "no_relationship"  # no account: drop the bank
        return out

    def _registry_consent(self, service: str, personal_id: str) -> dict:
        out = {"source": service, "status": "error", "consent_id": None}
        created = self.registries.post(f"/{service}/v1/consents", json={"personId": personal_id})
        if created.status_code != 201:
            out["status"] = "unknown_client" if created.status_code == 400 else "error"
            return out
        consent_id = created.json()["consentId"]
        approved = self.registries.post(f"/_mock/consents/{service}/{consent_id}/authorise").json()  # skip mode
        out.update(status="connected", consent_id=consent_id, authorization=approved["authorization"])
        return out

    def create_consents(self, personal_id: str) -> dict | None:
        banks = self.catalog()
        futures = [self.pool.submit(self._bank_consent, b, personal_id) for b in banks]
        registry_futures = [self.pool.submit(self._registry_consent, s, personal_id) for s in ("rs", "creditinfo")]
        bank_results = [f.result() for f in futures]
        rs, creditinfo = (f.result() for f in registry_futures)
        if all(b["status"] == "unknown_client" for b in bank_results):
            return None
        kyc = self.banks.get(f"/_mock/kyc/{personal_id}")
        record = {
            "consent_set_id": uuid.uuid4().hex[:12],
            "personal_id": personal_id,
            "banks": sorted(bank_results, key=lambda b: (b["status"] != "connected", -b["accounts"], b["bank"])),
            "rs": rs,
            "creditinfo": creditinfo,
            "kyc": kyc.json() if kyc.status_code == 200 else None,
        }
        self.consent_sets[record["consent_set_id"]] = record
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
    def start(self, req: EstimateRequest) -> dict | None:
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
            "loan_request": req.loan_request,
            "result": None,
            "error": None,
        }
        self.jobs[job_id] = job
        self.pool.submit(self._run, job, record, req.applicant)
        return job

    @staticmethod
    def _set(job: dict, source: str, state: str, detail: str = "") -> None:
        for step in job["progress"]:
            if step["source"] == source:
                step["state"], step["detail"] = state, detail

    def _check_consent(self, client: httpx.Client, path: str) -> str | None:
        reply = client.get(path, headers={"X-Request-ID": str(uuid.uuid4())})
        if reply.status_code != 200:
            return f"consent status call answered {reply.status_code}"
        status = reply.json().get("consentStatus")
        return None if status == "valid" else f"consent is {status}"

    def _pull_bank(self, job: dict, gateway: HttpGateway, entry: dict, date_from: str, personal_id: str):
        bank = entry["bank"]
        self._set(job, bank, "running", "checking consent")
        d = gateway.dialects[bank]
        status_route = next((t for m, t, op in d.route_table() if m == "GET" and op == "consent_status"), None)
        problem = (
            self._check_consent(self.banks, f"/{bank}{status_route.format(consentId=entry['consent_id'])}")
            if status_route
            else None
        )
        if problem:
            self._set(job, bank, "failed", problem)
            return bank, None
        self._set(job, bank, "running", "fetching accounts and transactions")
        try:
            rows = fetch_bank(gateway, bank, personal_id, date_from)
        except Exception as e:  # noqa: BLE001  one failing bank must not sink the estimate
            self._set(job, bank, "failed", str(e))
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
        return bank, rows

    def _pull_registry(self, job: dict, service: str, entry: dict, path: str):
        self._set(job, service, "running", "checking consent")
        headers = {"Authorization": entry["authorization"], "Consent-ID": entry["consent_id"]}
        problem = self._check_consent(self.registries, f"/{service}/v1/consents/{entry['consent_id']}/status")
        if problem:
            self._set(job, service, "failed", problem)
            return None
        reply = self.registries.get(path, headers=headers)
        if reply.status_code != 200:
            self._set(job, service, "failed", f"answered {reply.status_code}")
            return None
        self._set(job, service, "done", "data gathered")
        return reply.json()

    def _run(self, job: dict, record: dict, applicant: dict | None) -> None:
        try:
            job["status"], job["stage"] = "running", "fetching"
            as_of = pd.Timestamp(self.model.state["ob_window"]["as_of"])
            date_from = (as_of - pd.DateOffset(months=HISTORY_MONTHS)).date().isoformat()
            connected = {b["bank"]: b for b in record["banks"] if b["status"] == "connected"}
            gateway = self._gateway_for(connected)
            pid = record["personal_id"]
            bank_jobs = [self.pool.submit(self._pull_bank, job, gateway, e, date_from, pid) for e in connected.values()]
            rs_job = self.pool.submit(self._pull_registry, job, "rs", record["rs"], "/rs/v1/income-declarations")
            ci_job = self.pool.submit(
                self._pull_registry, job, "creditinfo", record["creditinfo"], "/creditinfo/v1/credit-report"
            )
            pulled = dict(f.result() for f in bank_jobs)
            rs_body, ci_body = rs_job.result(), ci_job.result()

            job["stage"] = "cleaning"
            got = {b: r for b, r in pulled.items() if r is not None}
            tables = {
                t: to_table(t, [row for r in got.values() for row in r[t]]).to_pandas()
                for t in ("accounts", "balances", "transactions")
            }
            cleaning = clean(tables)

            job["stage"] = "scoring"
            kyc = {**(record["kyc"] or {}), **(applicant or {})}
            registry = to_registry(kyc, rs_body, ci_body)
            open_banking = tables if got else None
            row = build_row(registry, open_banking, self.model, as_of=as_of.date().isoformat())
            matrix = self.model.transform(self.model.prepare(row))
            out = self.model.predict(row).iloc[0]
            features = {k: v for k, v in row.items() if k.startswith("ob_")}
            explanation = explain_p50(self.model, matrix, float(out["p50"]))

            tx = tables["transactions"]
            booked = (
                tx[(tx["status"] == "booked") & ~tx["is_card_mirror"].fillna(False).astype(bool)] if len(tx) else tx
            )
            months = float((as_of - pd.to_datetime(booked["booking_date"]).min()).days / 30.44) if len(booked) else 0.0
            band = float(out["band_share"])
            level, reasons, warnings = judge_confidence(level_of(band), months, len(booked), features, len(got))
            for step in job["progress"]:
                if step["state"] == "failed":
                    warnings.append(f"{step['label']}: {step['detail']}")
            if not rs_body:
                warnings.append("No Revenue Service data: the estimate uses bank data only.")
            job["result"] = {
                "income": {k: float(out[k]) for k in ("p10", "p50", "p90")} | {"currency": "GEL", "period": "monthly"},
                "confidence": {
                    "level": level,
                    "model_level": level_of(band),
                    "band_share": band,
                    "plus_minus_pct": round(50 * band, 1),
                    "scale": confidence_scale(),
                    "downgrade_reasons": reasons,
                    "score": self._score(band, bool(reasons)),
                    "measured": self._measured(level, reasons),
                },
                "explanation": explanation,
                "warnings": warnings,
                "data": {
                    "banks_used": sorted(got),
                    "months_covered": round(months, 1),
                    "transactions_used": cleaning["used_for_income"],
                },
                "cleaning": cleaning,
                "rs_declared": {"SOI04": registry.get("INCOME_AMOUNT_SOI04")} if rs_body else None,
                "loan_request": job["loan_request"],
            }
            job["status"], job["stage"] = "done", "done"
        except Exception as e:  # noqa: BLE001
            job["status"], job["stage"], job["error"] = "failed", "failed", f"{type(e).__name__}: {e}"

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
        return {k: v for k, v in job.items() if k not in ("created", "loan_request")}


def create_app(
    banks: httpx.Client | None = None, registries: httpx.Client | None = None, model: IncomeModel | None = None
) -> FastAPI:
    svc = Service(
        banks or httpx.Client(base_url=BANKS_URL, timeout=60),
        registries or httpx.Client(base_url=REGISTRIES_URL, timeout=60),
        model,
    )
    app = FastAPI(
        title="Income estimation API", description="Open banking + RS + Creditinfo -> P10 / P50 / P90 monthly income."
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.service = svc

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "model_window": svc.model.state["ob_window"]["as_of"],
            "today": date.today().isoformat(),
        }

    @app.get("/v1/demo/clients")
    def demo_clients():
        return json.loads(DEMO_CLIENTS.read_text()) if DEMO_CLIENTS.exists() else []

    @app.post("/v1/consents", status_code=201)
    def create_consents(body: ConsentRequest):
        record = svc.create_consents(body.personal_id.strip())
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
    def create_estimate(body: EstimateRequest):
        job = svc.start(body)
        if job is None:
            return JSONResponse({"error": "UNKNOWN_CONSENT_SET"}, status_code=404)
        return {"job_id": job["job_id"], "status": job["status"], "status_url": f"/v1/income-estimates/{job['job_id']}"}

    @app.get("/v1/income-estimates/{job_id}")
    def get_estimate(job_id: str):
        job = svc.jobs.get(job_id)
        return svc.public_job(job) if job else JSONResponse({"error": "UNKNOWN_JOB"}, status_code=404)

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--banks-url", default=BANKS_URL)
    parser.add_argument("--registries-url", default=REGISTRIES_URL)
    args = parser.parse_args()
    app = create_app(
        httpx.Client(base_url=args.banks_url, timeout=120), httpx.Client(base_url=args.registries_url, timeout=60)
    )
    uvicorn.run(app, host=args.host, port=args.port)
