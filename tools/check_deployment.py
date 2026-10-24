"""Smoke-test a running Docker/Render URL with stdlib HTTP; never generates datasets or trains a model.

Usage: python tools/check_deployment.py http://127.0.0.1:18080 [--all-clients]
Creates real synthetic assessments and an officer decision in that running service.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request


def check(base: str, all_clients: bool = False) -> dict:
    base = base.rstrip("/")

    def call(path, body=None, *, status=200, origin=None):
        headers = {"Origin": origin or base}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                code, raw, content_type = response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            code, raw, content_type = error.code, error.read(), error.headers.get("Content-Type", "")
        assert code == status, f"{path}: wanted {status}, got {code}: {raw[:500]!r}"
        return json.loads(raw) if "application/json" in content_type else raw.decode("utf-8")

    html = call("/")
    assert '<div id="root">' in html
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    assert assets, "No compiled frontend assets"
    for asset in assets:
        assert call(asset)
    for path in ("/health", "/api/health"):
        assert call(path)["status"] == "ok"
    for path in ("/docs", "/api/docs", "/redoc"):
        assert "html" in call(path).lower()
    for path in ("/openapi.json", "/api/openapi.json"):
        assert "/v1/consents" in call(path)["paths"]
    call("/api/does-not-exist", status=404)
    call("/_mock/clock", status=404)
    sources = call("/api/v1/sources")
    assert len(sources) == 14
    clients = call("/api/v1/demo/clients")
    primary = next(c for c in clients if c["id"] == "C50811")
    call("/api/v1/consents", {"personal_id": primary["personal_id"]}, origin="https://unrelated.invalid", status=403)

    def estimate(client, scenario="none"):
        consent = call("/api/v1/consents", {"personal_id": client["personal_id"]}, status=201)
        assert len(consent["banks"]) == 12
        assert consent["rs"]["status"] == consent["creditinfo"]["status"] == "connected"
        started = call("/api/v1/income-estimates", {"consent_set_id": consent["consent_set_id"],
                                                  "demo_failure": scenario}, status=202)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = call("/api" + started["status_url"])
            if job["status"] in ("done", "failed"):
                assert job["status"] == "done", job.get("error")
                return consent, job
            time.sleep(0.1)
        raise AssertionError("Estimate timed out")

    consent, job = estimate(primary)
    assert consent["connected_banks"] == 1
    assert [b["bank"] for b in consent["banks"] if b["status"] == "connected"] == ["liberty"]
    result = job["result"]
    assert result["income"] == {"p10": 1746.0, "p50": 1878.0, "p90": 2104.0, "currency": "GEL", "period": "monthly"}
    assert result["confidence"]["level"] == "low" and result["confidence"]["score"] == 81
    assert result["evidence"]["service_payments"]["count"] == 76
    assert result["evidence"]["service_payments"]["total_gel"] == 21694.69
    assert result["sources_used"] == {"rs": True, "creditinfo": True}
    assert result["explanation"]["drivers"]
    assert {d["driver"]: d["effect_gel"] for d in result["explanation"]["drivers"]}["RS remittance income (SOI04)"] == 5
    case_id = job["job_id"]
    call(f"/api/v1/cases/{case_id}/review", {"decision": "accept"}, status=422)
    review = call(f"/api/v1/cases/{case_id}/review", {
        "decision": "request_more_evidence", "reason": "Deployment check: reviewed uncertainty and supporting evidence.",
        "acknowledged_uncertainty": True,
    })
    assert review["decision"] == "request_more_evidence"
    audit = call(f"/api/v1/cases/{case_id}/audit")
    assert audit["chain_valid"] and audit["case_matches_audit"]
    assert audit["events"][-1]["kind"] == "officer_decision"
    _, partial = estimate(primary, "rs_unavailable")
    assert partial["result"]["income"] and partial["result"]["partial_sources"]
    _, withheld = estimate(primary, "banks_unavailable")
    assert withheld["result"]["income"] is None
    assert withheld["result"]["outcome"] == "abstained"
    tested = [primary["id"]]
    if all_clients:
        for client in clients:
            if client["id"] == primary["id"]:
                continue
            _, other = estimate(client)
            assert other["result"]["income"] and other["result"]["explanation"]["drivers"]
            tested.append(client["id"])
    return {"passed": True, "clients_tested": tested, "sources": len(sources), "frontend_assets": len(assets),
            "income": result["income"], "confidence": result["confidence"]["level"],
            "service_payments": result["evidence"]["service_payments"], "processing_ms": job["processing_ms"],
            "model_sha256": job["model_sha256"], "audit_verified": True,
            "rs_outage": "partial estimate", "bank_outage": "estimate withheld"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--all-clients", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check(args.url, args.all_clients), indent=2))
