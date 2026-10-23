#!/usr/bin/env python3
"""Mock Revenue Service (RS) and Creditinfo (credit bureau) APIs, served from the already generated customers.

Nothing is generated here: every value is read from `openbanking_mock/output/dataset/truth/customers/` (the `df_*`
registry fields of nbg_synthetic_data/, one row per customer). The consent / token flow mirrors the mock bank APIs.

  RS          /rs/v1/...          legal form, citizenship, income items (the registry has only SOI04)
  Creditinfo  /creditinfo/v1/...  search counts (1/3/6/12 months), contract counts (open / closed)

Never served: INCOME_AMOUNT_TOTAL (the label) and PTI_RATIO.

    uv run python API/mock_registries.py --port 8001
    curl -X POST 'localhost:8001/_mock/session/rs?customer=C00012'        # pre-authorised consent + token
    curl localhost:8001/rs/v1/income-declarations -H 'Authorization: Bearer ...' -H 'Consent-ID: ...'
"""

from __future__ import annotations

import argparse
import secrets
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
TRUTH = HERE.parent / "openbanking_mock" / "output" / "dataset" / "truth" / "customers"
SHARD_SIZE = 250
MAX_CONSENT_DAYS = 90
SERVICES = ("rs", "creditinfo")

RS_COLUMNS = ["df_CLIENT_LEGAL_FORM", "df_GeoCitizen", "df_INCOME_AMOUNT_SOI04"]
CREDITINFO_COLUMNS = [
    "df_last1Month",
    "df_last3Month",
    "df_last6Month",
    "df_last12Month",
    "df_ExistingOperationsDebtor",
    "df_TerminatedOperationsDebtor",
]


# ----------------------------------------------------------------------------- customer lookup
def _shard_path(cid: str) -> Path:
    return TRUTH / f"part-{(int(cid.lstrip('C')) - 1) // SHARD_SIZE:05d}.parquet"


@lru_cache(maxsize=1)
def _person_index() -> dict[str, str]:
    """person_id -> cid, read once from the (small) id columns of every shard."""
    frames = [pd.read_parquet(p, columns=["cid", "person_id"]) for p in sorted(TRUTH.glob("part-*.parquet"))]
    both = pd.concat(frames)
    return dict(zip(both["person_id"], both["cid"], strict=True))


def resolve_cid(subject: str) -> str | None:
    """Accepts a cid ("C00012"), a person id ("09754421550") or the bank-API form ("PNOGE-09754421550")."""
    subject = subject.strip()
    if subject.startswith("C") and subject[1:].isdigit() and _shard_path(subject).exists():
        return subject
    return _person_index().get(subject.removeprefix("PNOGE-"))


def _row(cid: str, columns: list[str]) -> dict:
    frame = pd.read_parquet(_shard_path(cid), columns=["cid", "person_id", *columns])
    row = frame[frame["cid"] == cid].iloc[0]
    return {c: (None if pd.isna(row[c]) else row[c].item() if hasattr(row[c], "item") else row[c]) for c in row.index}


# ----------------------------------------------------------------------------- response bodies
def rs_declarations(cid: str) -> dict:
    row = _row(cid, RS_COLUMNS)
    return {
        "personId": row["person_id"],
        "taxpayer": {"legalForm": row["df_CLIENT_LEGAL_FORM"], "geoCitizen": row["df_GeoCitizen"] == "Yes"},
        "incomeItems": [
            {
                "code": "SOI04",
                "description": "Income from remittances",  # data dictionary: INCOME_AMOUNT_SOI04
                "amount": row["df_INCOME_AMOUNT_SOI04"],
                "currency": "GEL",
            }
        ],
    }


def creditinfo_report(cid: str) -> dict:
    row = _row(cid, CREDITINFO_COLUMNS)
    return {
        "personId": row["person_id"],
        "inquiries": {f"last{n}Month": row[f"df_last{n}Month"] for n in (1, 3, 6, 12)},
        "contracts": {
            "existingAsDebtor": row["df_ExistingOperationsDebtor"],
            "terminatedAsDebtor": row["df_TerminatedOperationsDebtor"],
        },
    }


# ----------------------------------------------------------------------------- consents
class Consents:
    """In-memory consent + token store, one per service. Resets on restart, like the mock banks."""

    def __init__(self) -> None:
        self.by_id: dict[str, dict] = {}

    def create(self, service: str, cid: str, valid_until: date) -> dict:
        consent = {
            "consentId": secrets.token_hex(8),
            "service": service,
            "cid": cid,
            "validUntil": valid_until,
            "status": "received",
            "token": None,
        }
        self.by_id[consent["consentId"]] = consent
        return consent

    def authorise(self, consent: dict) -> str:
        consent["status"] = "valid"
        consent["token"] = secrets.token_urlsafe(24)
        return consent["token"]

    def check(
        self, service: str, consent_id: str | None, authorization: str | None
    ) -> tuple[dict | None, JSONResponse | None]:
        def fail(status: int, code: str, text: str):
            body = {"tppMessages": [{"category": "ERROR", "code": code, "text": text}]}
            return None, JSONResponse(body, status_code=status)

        if not authorization or not consent_id:
            return fail(401, "TOKEN_INVALID", "Authorization and Consent-ID headers are required")
        consent = self.by_id.get(consent_id)
        if not consent or consent["service"] != service:
            return fail(401, "CONSENT_UNKNOWN", "unknown consent for this service")
        if consent["status"] != "valid" or authorization != f"Bearer {consent['token']}":
            return fail(401, "CONSENT_INVALID", f"consent is {consent['status']} or the token does not match")
        if consent["validUntil"] < date.today():
            consent["status"] = "expired"
            return fail(401, "CONSENT_EXPIRED", "consent has expired")
        return consent, None


# ----------------------------------------------------------------------------- app
def create_app() -> FastAPI:
    app = FastAPI(
        title="Mock RS + Creditinfo", description="Served from the generated customers; nothing is simulated."
    )
    consents = Consents()
    prefix = {"rs": "/rs/v1", "creditinfo": "/creditinfo/v1"}

    def consent_error(status: int, code: str, text: str) -> JSONResponse:
        return JSONResponse({"tppMessages": [{"category": "ERROR", "code": code, "text": text}]}, status_code=status)

    def add_consent_routes(service: str) -> None:
        base = prefix[service]

        @app.post(f"{base}/consents", status_code=201, name=f"{service}_create_consent")
        def create_consent(body: dict):
            cid = resolve_cid(str(body.get("personId", "")))
            if cid is None:
                return consent_error(400, "FORMAT_ERROR", "unknown personId")
            until = date.fromisoformat(
                body.get("validUntil") or (date.today() + timedelta(days=MAX_CONSENT_DAYS)).isoformat()
            )
            if until > date.today() + timedelta(days=MAX_CONSENT_DAYS):
                return consent_error(400, "PERIOD_INVALID", f"validUntil is more than {MAX_CONSENT_DAYS} days away")
            c = consents.create(service, cid, until)
            return {
                "consentId": c["consentId"],
                "consentStatus": c["status"],
                "_links": {"authorise": {"href": f"/_mock/consents/{service}/{c['consentId']}/authorise"}},
            }

        @app.get(f"{base}/consents/{{consent_id}}/status", name=f"{service}_consent_status")
        def consent_status(consent_id: str):
            c = consents.by_id.get(consent_id)
            if not c or c["service"] != service:
                return consent_error(404, "CONSENT_UNKNOWN", "unknown consent")
            return {"consentStatus": "expired" if c["validUntil"] < date.today() else c["status"]}

    for svc in SERVICES:
        add_consent_routes(svc)

    @app.get(f"{prefix['rs']}/income-declarations")
    def income_declarations(authorization: str | None = Header(None), consent_id: str | None = Header(None),
                            x_demo_failure: str | None = Header(None)):
        consent, error = consents.check("rs", consent_id, authorization)
        if not error and x_demo_failure == "unavailable":
            return JSONResponse({"error": "SIMULATED_RS_OUTAGE", "text": "Explicit demo fault: Revenue Service unavailable"}, status_code=503)
        return error or rs_declarations(consent["cid"])

    @app.get(f"{prefix['creditinfo']}/credit-report")
    def credit_report(authorization: str | None = Header(None), consent_id: str | None = Header(None)):
        consent, error = consents.check("creditinfo", consent_id, authorization)
        return error or creditinfo_report(consent["cid"])

    # ---- control plane (stands in for the customer's own login + SCA at the data holder) ----
    @app.post("/_mock/consents/{service}/{consent_id}/authorise")
    def authorise(service: str, consent_id: str):
        c = consents.by_id.get(consent_id)
        if service not in SERVICES or not c or c["service"] != service:
            return consent_error(404, "CONSENT_UNKNOWN", "unknown consent")
        return {"authorization": f"Bearer {consents.authorise(c)}", "consentId": consent_id}

    @app.post("/_mock/session/{service}")
    def session(service: str, customer: str):
        """Ready-made, already authorised consent + token for an existing customer (cid or person id)."""
        cid = resolve_cid(customer) if service in SERVICES else None
        if cid is None:
            return consent_error(404, "UNKNOWN", f"unknown service {service!r} or customer {customer!r}")
        c = consents.create(service, cid, date.today() + timedelta(days=MAX_CONSENT_DAYS))
        return {
            "consentId": c["consentId"],
            "authorization": f"Bearer {consents.authorise(c)}",
            "validUntil": c["validUntil"].isoformat(),
            "personId": _row(cid, [])["person_id"],
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
