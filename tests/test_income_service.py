"""Income estimation API end to end, in process: portal -> service -> mock banks (all customers, on demand) + RS + Creditinfo.

The checks that matter: what the service pulls over the bank APIs equals the stored api/ parquet rows, and the estimate
equals what the model gives on the stored data."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "openbanking_mock" / "output" / "dataset"
sys.path[:0] = [str(ROOT / "API"), str(ROOT / "income"), str(ROOT / "eda"), str(ROOT / "openbanking_mock")]

pytestmark = pytest.mark.skipif(
    not (DATASET / "api" / "transactions" / "part-00000.parquet").exists(), reason="dataset missing"
)

import mock_banks  # noqa: E402
import mock_registries  # noqa: E402
import service  # noqa: E402
from income_pipeline import load_open_banking, predict_person, registry_of  # noqa: E402


@pytest.fixture(scope="module")
def stack():
    banks = TestClient(mock_banks.create_app(), base_url="http://banks")
    registries = TestClient(mock_registries.create_app(), base_url="http://registries")
    api = TestClient(service.create_app(banks, registries))
    return api


def _consents(api, who):
    r = api.post("/v1/consents", json={"personal_id": who})
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(api, consent_set_id, timeout=60):
    job = api.post("/v1/income-estimates", json={"consent_set_id": consent_set_id})
    assert job.status_code == 202
    url = job.json()["status_url"]
    end = time.time() + timeout
    while time.time() < end:
        state = api.get(url).json()
        if state["status"] in ("done", "failed"):
            return state
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def _multi_bank_customer() -> str:
    t = pd.read_parquet(DATASET / "truth" / "customers" / "part-00000.parquet", columns=["cid", "banks"])
    t["n"] = t["banks"].str.count(r"\|") + 1
    return t.sort_values("n", ascending=False).iloc[0]["cid"]


def test_consent_table_matches_where_the_client_has_accounts(stack):
    cid = _multi_bank_customer()
    table = _consents(stack, cid)
    accounts = pd.read_parquet(DATASET / "api" / "accounts" / "part-00000.parquet", columns=["psu_id", "bank"])
    has_account = set(accounts[accounts["psu_id"] == cid]["bank"])
    connected = {b["bank"] for b in table["banks"] if b["status"] == "connected"}
    assert connected == has_account and len(table["banks"]) == 12
    assert all(b["status"] == "no_relationship" for b in table["banks"] if b["bank"] not in has_account)
    assert table["rs"]["status"] == table["creditinfo"]["status"] == "connected"
    assert "authorization" not in str(table)  # tokens never leave the service
    # lookup by personal id gives the same client
    by_person = _consents(stack, table["kyc"]["personId"])
    assert {b["bank"] for b in by_person["banks"] if b["status"] == "connected"} == has_account


def test_estimate_equals_the_model_on_the_stored_data(stack):
    for cid in ("C00012", _multi_bank_customer()):
        state = _estimate(stack, _consents(stack, cid)["consent_set_id"])
        assert state["status"] == "done", state
        kyc = _consents(stack, cid)["kyc"]
        direct = predict_person(
            {**registry_of(cid), "region_name": kyc["region_name"], "city": kyc["city"]}, load_open_banking(cid)
        )
        income = state["result"]["income"]
        assert (income["p10"], income["p50"], income["p90"]) == (direct["p10"], direct["p50"], direct["p90"])
        assert all(s["state"] == "done" for s in state["progress"])


def test_fetched_rows_equal_the_stored_api_tables(stack):
    cid = "C00012"
    svc = stack.app.state.service
    record = svc.consent_sets[_consents(stack, cid)["consent_set_id"]]
    entries = {b["bank"]: b for b in record["banks"] if b["status"] == "connected"}
    gateway = svc._gateway_for(entries)
    stored = pd.read_parquet(DATASET / "api" / "transactions" / "part-00000.parquet")
    stored = stored[stored["psu_id"] == cid]
    for bank in entries:
        rows = service.fetch_bank(gateway, bank, cid, "2025-10-19")["transactions"]
        expected = stored[stored["bank"] == bank]
        assert len(rows) == len(expected)
        assert sum(float(r["amount"]) for r in rows) == pytest.approx(float(expected["amount"].sum()))


def test_unknown_client_and_unknown_ids(stack):
    assert stack.post("/v1/consents", json={"personal_id": "00000000000"}).status_code == 404
    assert stack.post("/v1/income-estimates", json={"consent_set_id": "nope"}).status_code == 404
    assert stack.get("/v1/income-estimates/nope").status_code == 404


def test_thin_data_gives_very_low_confidence_with_a_warning(stack):
    table = _consents(stack, "C00012")
    record = stack.app.state.service.consent_sets[table["consent_set_id"]]
    for b in record["banks"]:
        if b["bank"] != "bog":  # the client's big banks (salary) are not connected: only BoG is
            b["status"] = "no_relationship"
    state = _estimate(stack, table["consent_set_id"])
    result = state["result"]
    assert result["confidence"]["level"] == "very low"
    assert any("transactions" in r for r in result["confidence"]["downgrade_reasons"])
    assert result["data"]["banks_used"] == ["bog"]
    assert result["warnings"]


def test_a_dead_consent_is_reported_and_the_others_still_count(stack):
    table = _consents(stack, "C00012")
    record = stack.app.state.service.consent_sets[table["consent_set_id"]]
    next(b for b in record["banks"] if b["bank"] == "liberty")["consent_id"] = "deadbeef"
    state = _estimate(stack, table["consent_set_id"])
    liberty = next(p for p in state["progress"] if p["source"] == "liberty")
    assert liberty["state"] == "failed"
    assert state["status"] == "done" and "liberty" not in state["result"]["data"]["banks_used"]
    assert any("Liberty" in w for w in state["result"]["warnings"])


def test_result_has_shap_drivers_and_measured_confidence(stack):
    result = _estimate(stack, _consents(stack, "C00012")["consent_set_id"])["result"]
    explanation = result["explanation"]
    # TreeSHAP is additive: the starting point plus every driver is the model's P50
    total = explanation["starting_point_gel"] + sum(d["effect_gel"] for d in explanation["drivers"])
    assert abs(total - result["income"]["p50"]) <= 8  # rounding of each term to whole GEL
    assert explanation["drivers"][0]["driver"] and len(explanation["drivers"]) <= 7
    measured = result["confidence"]["measured"]
    assert measured is None or 0 <= measured["inside_p10_p90_pct"] <= 100


def test_demo_clients_are_ten_known_clients(stack):
    clients = stack.get("/v1/demo/clients").json()
    assert len(clients) == 10 and len({c["id"] for c in clients}) == 10
    assert _consents(stack, clients[0]["id"])["connected_banks"] >= 1


def test_confidence_is_read_off_the_band_width():
    from confidence import LEVELS, level_of, score_of

    assert [level_of(b) for b in (0.0, 0.03, 0.031, 0.06, 0.10, 0.20, 0.21)] == [
        "very high", "very high", "high", "high", "medium", "low", "very low",
    ]  # fmt: skip
    assert score_of(0.174) == 83 and score_of(0.0) == 100 and score_of(1.5) == 0
    assert LEVELS[0] == "very high" and LEVELS[-1] == "very low"


def test_result_confidence_matches_its_band(stack):
    from confidence import level_of, score_of

    c = _estimate(stack, _consents(stack, "C00012")["consent_set_id"])["result"]["confidence"]
    assert c["level"] == level_of(c["band_share"]) and c["score"] == score_of(c["band_share"])
    assert c["plus_minus_pct"] == pytest.approx(50 * c["band_share"], abs=0.06)
