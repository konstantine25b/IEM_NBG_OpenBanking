"""FastAPI front-end. `/{bank}/...` speaks that bank's dialect; `/_mock/...` is the control plane.

Examples (default port 8000):
  GET  /                              list the 12 banks and how to call them
  POST /_mock/session/tbc             ready-made consent + Bearer token for a random TBC customer
  GET  /tbc/0.8/v1/accounts           (send the Authorization + Consent-ID headers from the session)
  GET  /_mock/banks/silk/routes       every route the Silk dialect serves
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi import Request as HttpRequest
from fastapi.responses import JSONResponse
from fastapi.responses import Response as HttpResponse
from starlette.concurrency import run_in_threadpool

from obmock.banks import BANKS
from obmock.config import WorldConfig
from obmock.engine.gateway import ApiError, Gateway, Request, Response
from obmock.world import World, build_world


def _to_http(resp: Response) -> HttpResponse:
    if resp.body is None:
        return HttpResponse(status_code=resp.status, headers=resp.headers)
    return JSONResponse(resp.body, status_code=resp.status, headers=resp.headers)


def _unknown_bank(bank: str) -> JSONResponse:
    return JSONResponse({"error": f"unknown bank {bank}", "banks": list(BANKS)}, status_code=404)


async def _parse_body(request: HttpRequest) -> Any:
    raw = await request.body()
    if not raw:
        return None
    if "application/x-www-form-urlencoded" in request.headers.get("content-type", ""):
        return {k: v[0] for k, v in parse_qs(raw.decode()).items()}
    return json.loads(raw)  # ValueError handled by the caller


def create_app(world: World | None = None, cfg: WorldConfig | None = None,
               auth_mode: str = "strict", chaos: str = "off",
               expose_truth: bool = False) -> FastAPI:
    world = world or build_world(cfg or WorldConfig())
    gw = Gateway(world, auth_mode=auth_mode, chaos=chaos)
    app = FastAPI(title="Georgian Open Banking mock", version="0.1.0",
                  description="Mock PSD2/XS2A APIs of the 12 banks listed by the NBG.")
    app.state.gateway = gw

    def banks_overview(base: str) -> list[dict[str, Any]]:
        out = []
        for key, b in BANKS.items():
            d = gw.dialects[key]
            out.append({
                "bank": key, "name": b.name_en, "dialect": b.dialect, "evidence": b.evidence,
                "vendor": b.vendor, "customers": len(world.customers_of(key)),
                "base_url": f"{base}/{key}{d.canonical_prefix}",
                "accounts": f"{base}/{key}{d.canonical_prefix}{d.accounts_path}",
                "loans": d.loans, "savings": d.savings, "card_accounts": d.card_accounts,
                "consent_header": d.consent_header, "real_sandbox": b.sandbox_url,
                "real_prod": b.prod_url, "docs": b.api_doc_url, "notes": b.notes})
        return out

    @app.get("/", include_in_schema=False)
    async def index(request: HttpRequest) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        return {
            "about": "Mock Open Banking APIs for the 12 NBG-listed Georgian banks. All data is "
                     "synthetic. Start with POST /_mock/session/{bank}.",
            "auth_mode": gw.auth_mode, "world": world.stats(), "banks": banks_overview(base),
            "control_plane": ["/_mock/banks", "/_mock/banks/{bank}/routes",
                              "/_mock/customers/{bank}", "POST /_mock/session/{bank}",
                              "/_mock/stats"] + (["/_mock/truth/{cid}"] if expose_truth else []),
        }

    @app.get("/_mock/banks")
    async def banks(request: HttpRequest) -> list[dict[str, Any]]:
        return banks_overview(str(request.base_url).rstrip("/"))

    @app.get("/_mock/banks/{bank}/routes")
    async def routes(bank: str) -> Any:
        if bank not in BANKS:
            return _unknown_bank(bank)
        return gw.dialects[bank].describe()

    @app.get("/_mock/customers/{bank}")
    async def customers(bank: str) -> Any:
        if bank not in BANKS:
            return _unknown_bank(bank)
        return [{"customer": c.cid, "name": c.name_en, "name_ka": c.name_ka,
                 "segment": c.segment, "region": c.region_name,
                 "accounts": [{"kind": a.kind, "currency": a.currency, "iban": a.iban}
                              for a in world.accounts_of(bank, c.cid)]}
                for c in (world.customers[i] for i in world.customers_of(bank))]

    @app.api_route("/_mock/session/{bank}", methods=["GET", "POST"])
    async def session(bank: str, customer: str | None = None, scope: str = "AIS") -> Any:
        if bank not in BANKS:
            return _unknown_bank(bank)
        return gw.quick_session(bank, customer, scope)

    @app.get("/_mock/stats")
    async def stats() -> dict[str, Any]:
        return world.stats()

    if expose_truth:
        @app.get("/_mock/truth/{cid}")
        async def truth(cid: str) -> Any:
            c = world.customers.get(cid)
            if c is None:
                return JSONResponse({"error": "unknown customer"}, status_code=404)
            return {"customer": cid, "banks": c.banks, "truth": c.truth}

    @app.get("/{bank}/_mock/sca/{kind}/{parent_id}/{auth_id}")
    async def sca_page(bank: str, kind: str, parent_id: str, auth_id: str,
                       psu: str | None = None, action: str = "approve") -> Any:
        """What the PSU would see at the bank for redirect-style SCA (auto-approves)."""
        if bank not in BANKS:
            return _unknown_bank(bank)
        try:
            res = gw.complete_sca(bank, kind, parent_id, psu, action != "reject")
        except ApiError as e:
            return JSONResponse({"error": e.code, "text": e.text}, status_code=e.status)
        if res["redirect_uri"]:
            return HttpResponse(status_code=302, headers={"Location": res["redirect_uri"]})
        return res

    @app.api_route("/{bank}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def bank_api(bank: str, path: str, request: HttpRequest) -> HttpResponse:
        if bank not in BANKS:
            return _unknown_bank(bank)
        headers = {k.lower(): v for k, v in request.headers.items()}
        base = f"{str(request.base_url).rstrip('/')}/{bank}"
        try:
            body = await _parse_body(request)
        except ValueError:
            req = Request(request.method, "/" + path, {}, headers, None, base)
            err = ApiError(400, "FORMAT_ERROR", "request body is not valid JSON")
            return _to_http(gw.dialects[bank].error_response(err, req, "-"))
        req = Request(request.method, "/" + path, dict(request.query_params), headers, body, base)
        return _to_http(await run_in_threadpool(gw.handle, bank, req))

    return app
