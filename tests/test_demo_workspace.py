"""Run the actual demo APIs without requiring the uncommitted 77M-row transaction dump."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ("API", "income", "eda", "openbanking_mock")]

import mock_banks  # noqa: E402
import mock_registries  # noqa: E402
import service  # noqa: E402
from case_store import CaseStore, canonical  # noqa: E402
from income_predictor import IncomeModel  # noqa: E402


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    store = CaseStore(tmp_path_factory.mktemp("case-store") / "cases.sqlite3")
    banks = TestClient(mock_banks.create_app(), base_url="http://banks")
    registries = TestClient(mock_registries.create_app(), base_url="http://registries")
    model = IncomeModel.load()
    api = TestClient(service.create_app(banks, registries, model, store))
    assert api.post("/v1/auth/login", json={"username": "officer", "password": "demo-review-only"}).status_code == 200
    yield api, banks, registries, model, store
    api.app.state.service.pool.shutdown(wait=True)
    api.close()
    banks.close()
    registries.close()


def estimate(api, scenario="none", personal_id="91038618469"):
    consent = api.post("/v1/consents", json={"personal_id": personal_id})
    assert consent.status_code == 201, consent.text
    created = api.post(
        "/v1/income-estimates", json={"consent_set_id": consent.json()["consent_set_id"], "demo_failure": scenario}
    )
    assert created.status_code == 202, created.text
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = api.get(created.json()["status_url"])
        assert state.status_code == 200, state.text
        job = state.json()
        if job["status"] in ("done", "failed"):
            assert job["status"] == "done", job
            return job
        time.sleep(0.05)
    raise AssertionError("Demo estimate timed out")


@pytest.fixture(scope="module")
def normal(stack):
    return estimate(stack[0])


def test_default_model_and_transaction_evidence(normal):
    result = normal["result"]
    assert result["income"] == {"p10": 1746.0, "p50": 1878.0, "p90": 2104.0, "currency": "GEL", "period": "monthly"}
    evidence = result["evidence"]
    assert evidence["service_payments"]["count"] == 76
    assert evidence["service_payments"]["total_gel"] == 21694.69
    assert evidence["salary_baseline"]["status"] == "abstains"
    assert sum(r["classification"] == "Service payment" for r in evidence["transactions"]) == 76
    assert result["confidence"]["score"] == 81
    assert {d["driver"]: d["effect_gel"] for d in result["explanation"]["drivers"]}["RS remittance income (SOI04)"] == 5
    assert normal["processing_ms"] > 0
    assert any(e["stage"] == "Bank API call" and e["detail"]["http_status"] == 200 for e in normal["trace"])


@pytest.mark.parametrize(
    "cid", ["C60184", "C67705", "C37777", "C52528", "C40788", "C83158", "C02186", "C46126", "C78014"]
)
def test_other_existing_demo_clients(stack, cid):
    api = stack[0]
    demo = next(d for d in api.get("/v1/demo/clients").json() if d["id"] == cid)
    case = estimate(api, personal_id=demo["personal_id"])
    assert case["subject"] == cid
    assert case["result"]["outcome"] == "estimated"
    assert case["result"]["income"]["p50"] > 0
    assert case["result"]["evidence"]["transactions"]


def test_review_is_enforced_by_api(stack, normal):
    api = stack[0]
    url = f"/v1/cases/{normal['job_id']}/review"
    review = {"decision": "accepted", "reason": "Reviewed the service payments and the prediction uncertainty."}
    assert api.post(url, json=review).status_code == 409
    assert api.post(url, json={**review, "acknowledge_uncertainty": True}).status_code == 200
    assert api.get(f"/v1/cases/{normal['job_id']}").json()["review"]["reviewer"] == "officer"


def test_authentication_roles_and_cross_origin(stack, normal):
    app = stack[0].app
    with TestClient(app) as anonymous:
        assert anonymous.get(f"/v1/cases/{normal['job_id']}").status_code == 401
        assert anonymous.post("/v1/consents", json={"personal_id": "C50811"}).status_code == 401
        assert anonymous.post("/v1/auth/login", json={"username": "officer", "password": "wrong"}).status_code == 401
        assert (
            anonymous.post("/v1/auth/login", json={"username": "auditor", "password": "demo-audit-only"}).status_code
            == 200
        )
        assert anonymous.get(f"/v1/cases/{normal['job_id']}/export").status_code == 200
        assert (
            anonymous.post(
                f"/v1/cases/{normal['job_id']}/review",
                json={"decision": "rejected", "reason": "Auditor cannot decide this case."},
            ).status_code
            == 403
        )
    assert (
        stack[0]
        .post("/v1/consents", json={"personal_id": "C50811"}, headers={"Origin": "https://untrusted.example"})
        .status_code
        == 403
    )


def test_real_mock_source_outage_and_abstention(stack):
    partial = estimate(stack[0], "rs_unavailable")
    assert next(p for p in partial["progress"] if p["source"] == "rs")["state"] == "failed"
    assert partial["result"]["income"] is not None
    assert partial["result"]["rs_declared"] is None
    assert partial["result"]["confidence"]["measured"] is None
    assert any("HTTP 503" in w for w in partial["result"]["warnings"])
    empty = estimate(stack[0], "banks_unavailable")
    assert empty["result"]["outcome"] == "abstained"
    assert empty["result"]["income"] is None
    response = stack[0].post(
        f"/v1/cases/{empty['job_id']}/review",
        json={"decision": "accepted", "reason": "Acknowledged missing bank data.", "acknowledge_uncertainty": True},
    )
    assert response.status_code == 409


def test_persistence_audit_integrity_and_export_privacy(stack, normal):
    api, banks, registries, model, store = stack
    reopened = TestClient(service.create_app(banks, registries, model, CaseStore(store.path)))
    try:
        reopened.post("/v1/auth/login", json={"username": "officer", "password": "demo-review-only"})
        assert reopened.get(f"/v1/cases/{normal['job_id']}").json()["result"]["income"]["p50"] == 1878
        exported = reopened.get(f"/v1/cases/{normal['job_id']}/export")
        assert exported.json()["audit"]["chain_valid"]
        assert exported.json()["audit"]["case_matches_audit"]
        for secret in ("91038618469", "GE39LB1567090146776894", "Bearer ", "demo-tpp-key"):
            assert secret not in exported.text
    finally:
        reopened.app.state.service.pool.shutdown(wait=True)
        reopened.close()


def test_manual_timer_requires_correct_answer(stack, normal):
    api = stack[0]
    prefix = f"/v1/cases/{normal['job_id']}/benchmark"
    task = api.post(prefix + "/start")
    assert task.status_code == 200
    assert "classification" not in task.json()["transactions"][0]
    assert api.post(prefix + "/finish", json={"payment_count": 0, "total_gel": 0}).status_code == 422
    response = api.post(prefix + "/finish", json={"payment_count": 76, "total_gel": 21694.69})
    assert response.status_code == 200, response.text
    assert response.json()["manual_ms"] > 0
    assert response.json()["automated_ms"] > 0


def test_hash_chain_detects_changes(tmp_path):
    store = CaseStore(tmp_path / "audit.sqlite3")
    store.append("case1", "test", "officer", {"value": 1})
    assert store.audit("case1")["chain_valid"]
    with store.connect() as db:
        db.execute("UPDATE events SET payload=?", (json.dumps({"changed": True}),))
    assert not store.audit("case1")["chain_valid"]


def test_audit_binds_the_saved_result(tmp_path):
    import hashlib

    store = CaseStore(tmp_path / "case.sqlite3")
    case = {"job_id": "case1", "result": {"income": {"p50": 1878}}, "review": None}
    store.save(case, "officer")
    digest = hashlib.sha256(canonical(case["result"]).encode()).hexdigest()
    store.append("case1", "Case ready for human review", "system", {"detail": {"result_sha256": digest}})
    assert store.audit("case1")["case_matches_audit"]
    case["result"]["income"]["p50"] = 9999
    store.save(case, "officer")
    assert store.audit("case1")["chain_valid"]
    assert not store.audit("case1")["case_matches_audit"]
