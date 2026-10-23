#!/usr/bin/env python3
"""The 12 mock Georgian bank APIs for all 100,000 generated customers, built on demand.

`obmock serve` keeps one in-memory world and is meant for a few thousand customers. The dataset has 100,000, and a
customer's data depends only on (config, its plan, the FX series): so this server reads the generator's own config
from output/dataset/MANIFEST.json, replays the plan of the customer that is asked for (about 10 ms), and serves it
through the unchanged obmock gateway and bank dialects. tests/test_income_service.py checks that what comes back
equals the stored api/ parquet rows.

  POST /_mock/session/{bank}?customer=C00012|09754421550   consent + token, already approved (skip mode)
  GET  /_mock/banks                                        the 12 banks: base URL, accounts URL, consent header
  GET  /_mock/kyc/{customer}                               the lender's own KYC record (age, sex, region, city)
  ANY  /{bank}/...                                         the bank's XS2A routes; needs Authorization + Consent-ID

A customer with no account at a bank gets a valid consent and an empty account list, like a real bank would answer.
Real consent / SCA / OAuth routes are not wired up here (the consent-id header picks the customer): use the session.

    uv run python API/mock_banks.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "openbanking_mock"))

from fastapi import FastAPI  # noqa: E402
from fastapi import Request as HttpRequest  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from obmock.banks import BANKS  # noqa: E402
from obmock.config import WorldConfig  # noqa: E402
from obmock.engine.gateway import ApiError, Consent, Gateway, Request, new_id, utcnow  # noqa: E402
from obmock.server import _parse_body, _to_http, _unknown_bank  # noqa: E402
from obmock.world.simulate import build_shard, make_fx, plan_population  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

MANIFEST = REPO / "openbanking_mock" / "output" / "dataset" / "MANIFEST.json"
CONSENT_HEADERS = ("consent-id", "consentid")  # TBC's loans / savings servers use consentId
MAX_CACHED_CUSTOMERS = 500
ACCOUNT_KINDS = ("current", "fx", "savings_term", "savings_flex")


def generator_config() -> WorldConfig:
    cfg = json.loads(MANIFEST.read_text())["config"]
    return WorldConfig(
        seed=cfg["seed"], n_customers=cfg["customers"], months=cfg["months"], as_of=date.fromisoformat(cfg["as_of"])
    )


class Population:
    """The generated customers, simulated lazily: one small gateway per customer that has been asked for."""

    def __init__(self, cfg: WorldConfig | None = None) -> None:
        self.cfg = cfg or generator_config()
        self.plans = plan_population(self.cfg)
        self.fx = make_fx(self.cfg)
        self.index_of_person = {p.pid: p.n for p in self.plans}
        self.gateways: OrderedDict[str, Gateway] = OrderedDict()
        self.owner_of_consent: dict[str, str] = {}
        self.lock = threading.Lock()

    def resolve(self, subject: str) -> str | None:
        """cid ("C00012"), person id ("09754421550") or the bank-API form ("PNOGE-09754421550") -> cid."""
        subject = subject.strip()
        if subject.startswith("C") and subject[1:].isdigit() and 1 <= int(subject[1:]) <= len(self.plans):
            return subject
        n = self.index_of_person.get(subject.removeprefix("PNOGE-"))
        return None if n is None else f"C{n + 1:05d}"

    def gateway(self, cid: str) -> Gateway:
        with self.lock:
            if cid in self.gateways:
                self.gateways.move_to_end(cid)
                return self.gateways[cid]
            world = build_shard(self.cfg, [self.plans[int(cid[1:]) - 1]], self.fx)
            gateway = Gateway(world, auth_mode="strict")
            self.gateways[cid] = gateway
            while len(self.gateways) > MAX_CACHED_CUSTOMERS:
                self.gateways.popitem(last=False)
            return gateway

    def session(self, bank: str, cid: str) -> dict[str, Any]:
        """Approved consent + bearer token (what Gateway.quick_session does), bound to this customer even when the
        customer has no account at `bank`."""
        gw = self.gateway(cid)
        s, dialect = gw.sessions, gw.dialects[bank]
        refs = [{"iban": a.iban} for a in gw.world.accounts_of(bank, cid, ACCOUNT_KINDS) if a.iban]
        consent = Consent(
            id=new_id(), bank=bank, family=dialect.consent_family, status="valid",
            access={"accounts": refs, "balances": refs, "transactions": refs, "availableAccounts": "allAccounts"},
            recurring=True, valid_until=date.today() + timedelta(days=90), freq=10_000, combined=False,
            created=utcnow(), last_action=date.today(), cid=cid, consent_type="global",
        )  # fmt: skip
        s.consents[consent.id] = consent
        token = s.issue_token(bank, cid, f"AIS:{consent.id}", "mock-client")
        self.owner_of_consent[consent.id] = cid
        return {
            "bank": bank,
            "consentId": consent.id,
            "consentHeader": {dialect.consent_header: consent.id},
            "authorization": f"Bearer {token.access_token}",
            "validUntil": consent.valid_until.isoformat(),
        }

    def kyc(self, cid: str) -> dict[str, Any]:
        plan = self.plans[int(cid[1:]) - 1]
        customer = self.gateway(cid).world.customers[cid]
        return {
            "age": plan.row["Age"],
            "sex": customer.sex,
            "region_name": customer.region_name,
            "city": customer.city,
            "personId": customer.person_id,
        }


def create_app(population: Population | None = None) -> FastAPI:
    pop = population or Population()
    app = FastAPI(title="Georgian Open Banking mock (all customers, on demand)")
    app.state.population = pop

    def error(status: int, code: str, text: str) -> JSONResponse:
        return JSONResponse({"tppMessages": [{"category": "ERROR", "code": code, "text": text}]}, status_code=status)

    @app.get("/_mock/banks")
    def banks(request: HttpRequest):
        gw = pop.gateway("C00001")
        base = str(request.base_url).rstrip("/")
        return [
            {
                "bank": key,
                "name": b.name_en,
                "base_url": f"{base}/{key}{gw.dialects[key].canonical_prefix}",
                "accounts_path": gw.dialects[key].accounts_path,
                "consent_header": gw.dialects[key].consent_header,
            }
            for key, b in BANKS.items()
        ]

    @app.api_route("/_mock/session/{bank}", methods=["GET", "POST"])
    def session(bank: str, customer: str):
        if bank not in BANKS:
            return _unknown_bank(bank)
        cid = pop.resolve(customer)
        if cid is None:
            return error(404, "UNKNOWN_CLIENT", f"no customer {customer!r}")
        return pop.session(bank, cid)

    @app.get("/_mock/kyc/{customer}")
    def kyc(customer: str):
        cid = pop.resolve(customer)
        return pop.kyc(cid) if cid else error(404, "UNKNOWN_CLIENT", f"no customer {customer!r}")

    @app.api_route("/{bank}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def bank_api(bank: str, path: str, request: HttpRequest):
        if bank not in BANKS:
            return _unknown_bank(bank)
        headers = {k.lower(): v for k, v in request.headers.items()}
        consent_id = next((headers[h] for h in CONSENT_HEADERS if h in headers), None)
        if consent_id is None:  # consent-collection routes carry the id in the path (GET /consents/{id}/status)
            consent_id = next((part for part in path.split("/") if part in pop.owner_of_consent), None)
        cid = pop.owner_of_consent.get(consent_id or "")
        if cid is None:
            return error(401, "CONSENT_UNKNOWN", "send Authorization and Consent-ID from POST /_mock/session/{bank}")
        if headers.get("x-demo-failure") == "unavailable":
            return error(503, "SIMULATED_BANK_OUTAGE", "Explicit demo fault: bank unavailable")
        base = f"{str(request.base_url).rstrip('/')}/{bank}"
        try:
            body = await _parse_body(request)
        except ValueError:
            return error(400, "FORMAT_ERROR", "request body is not valid JSON")
        req = Request(request.method, "/" + path, dict(request.query_params), headers, body, base)
        gateway = pop.gateway(cid)
        try:
            return _to_http(await run_in_threadpool(gateway.handle, bank, req))
        except ApiError as e:  # handle() answers errors itself; this is a safety net
            return error(e.status, e.code, e.text)

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)
