"""Income estimation API end to end, in process: portal -> service -> mock banks + RS + Creditinfo.

Runs on a 240-customer dataset that the tests build themselves (tests/conftest.py).
The checks that matter: what the service pulls over the bank APIs equals the stored api/ parquet rows, and the estimate
equals what the model gives on the stored data."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 22)  # the history window of the estimates below ends here, not at the model's date

CID = "C00012"


@pytest.fixture(scope="module")
def modules(mini_dataset, mock_registries_on_mini):
    import mock_banks
    import service
    from obmock.config import WorldConfig

    return mock_banks, mock_registries_on_mini, service, WorldConfig


@pytest.fixture(scope="module")
def clients(modules):
    mock_banks, mock_registries, _, WorldConfig = modules
    banks = TestClient(mock_banks.create_app(mock_banks.Population(WorldConfig())), base_url="http://banks")
    registries = TestClient(mock_registries.create_app(), base_url="http://registries")
    return banks, registries


@pytest.fixture(scope="module")
def stack(modules, clients):
    return TestClient(modules[2].create_app(*clients, as_of=AS_OF))


def _consents(api, who):
    r = api.post("/v1/consents", json={"personal_id": who})
    assert r.status_code == 201, r.text
    return r.json()


def _connected(table):
    return [b["bank"] for b in table["banks"] if b["status"] == "connected"]


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


def _record(api, table):
    return api.app.state.service.consent_sets[table["consent_set_id"]]


@pytest.fixture(scope="module")
def multi_bank_customer(mini_dataset):
    t = pd.read_parquet(mini_dataset / "truth" / "customers" / "part-00000.parquet", columns=["cid", "banks"])
    t["n"] = t["banks"].str.count(r"\|") + 1
    return t.sort_values("n", ascending=False).iloc[0]["cid"]


def test_consent_table_matches_where_the_client_has_accounts(stack, mini_dataset, multi_bank_customer):
    cid = multi_bank_customer
    table = _consents(stack, cid)
    accounts = pd.read_parquet(mini_dataset / "api" / "accounts" / "part-00000.parquet", columns=["psu_id", "bank"])
    has_account = set(accounts[accounts["psu_id"] == cid]["bank"])
    assert set(_connected(table)) == has_account and len(table["banks"]) == 12
    assert all(b["status"] == "no_relationship" for b in table["banks"] if b["bank"] not in has_account)
    assert table["rs"]["status"] == table["creditinfo"]["status"] == "connected"
    assert "authorization" not in str(table)  # tokens never leave the service
    # lookup by personal id gives the same client
    assert set(_connected(_consents(stack, table["kyc"]["personId"]))) == has_account


def _stored_window(cid, dataset):
    """The stored api/ rows of a client, cut to the same 12 months back from AS_OF that the service asks for."""
    from income_pipeline import load_open_banking

    tables = load_open_banking(cid, dataset / "api")
    start = pd.Timestamp(AS_OF) - pd.DateOffset(months=12)
    booked = pd.to_datetime(tables["transactions"]["booking_date"])
    tables["transactions"] = tables["transactions"][(booked >= start) | booked.isna()]
    return tables


def test_estimate_equals_the_model_on_the_stored_data(stack, mini_dataset, multi_bank_customer):
    from income_pipeline import predict_person, registry_of

    for cid in (CID, multi_bank_customer):
        table = _consents(stack, cid)
        state = _estimate(stack, table["consent_set_id"])
        assert state["status"] == "done", state
        kyc = table["kyc"]
        registry = {
            **registry_of(cid, mini_dataset / "truth" / "customers"),
            "Age": kyc["age"], "region_name": kyc["region_name"], "city": kyc["city"],
        }  # fmt: skip
        direct = predict_person(registry, _stored_window(cid, mini_dataset), as_of=AS_OF.isoformat())
        income = state["result"]["income"]
        assert (income["p10"], income["p50"], income["p90"]) == (direct["p10"], direct["p50"], direct["p90"])
        assert all(s["state"] == "done" for s in state["progress"])


def test_fetched_rows_equal_the_stored_api_tables(stack, mini_dataset, modules):
    service = modules[2]
    svc = stack.app.state.service
    record = _record(stack, _consents(stack, CID))
    entries = {b["bank"]: b for b in record["banks"] if b["status"] == "connected"}
    gateway = svc._gateway_for(entries)
    stored = pd.read_parquet(mini_dataset / "api" / "transactions" / "part-00000.parquet")
    stored = stored[stored["psu_id"] == CID]
    for bank in entries:
        rows = service.fetch_bank(gateway, bank, CID, "2025-10-19")["transactions"]
        expected = stored[stored["bank"] == bank]
        assert len(rows) == len(expected)
        assert sum(float(r["amount"]) for r in rows) == pytest.approx(float(expected["amount"].sum()))


def test_unknown_client_and_unknown_ids(stack):
    assert stack.post("/v1/consents", json={"personal_id": "00000000000"}).status_code == 404
    assert stack.post("/v1/income-estimates", json={"consent_set_id": "nope"}).status_code == 404
    assert stack.get("/v1/income-estimates/nope").status_code == 404


def test_no_bank_data_gives_very_low_confidence_with_a_warning(stack):
    table = _consents(stack, CID)
    for b in _record(stack, table)["banks"]:
        b["status"] = "no_relationship"
    result = _estimate(stack, table["consent_set_id"])["result"]
    assert result["confidence"]["level"] == "very low" and result["data"]["banks_used"] == []
    assert any("no bank data" in r for r in result["confidence"]["downgrade_reasons"])
    assert result["confidence"]["measured"] is None and result["confidence"]["score"] <= 79
    assert result["warnings"]


def test_thin_history_is_judged_very_low(modules):
    judge = modules[2].judge_confidence
    features = {"ob_inflow_month": 900.0, "ob_salary_month": 800.0}
    assert judge("very high", 12.0, 600, features, 2) == ("very high", [], [])
    level, reasons, _ = judge("very high", 1.5, 12, features, 1)
    assert level == "very low" and len(reasons) == 2
    _, reasons, warnings = judge("high", 12.0, 600, {"ob_inflow_month": 900.0}, 1)
    assert reasons == [] and "salary" in warnings[0]  # no salary credits is a warning, not a downgrade


def test_a_dead_bank_consent_is_reported_and_the_others_still_count(stack, multi_bank_customer):
    table = _consents(stack, multi_bank_customer)
    banks = _connected(table)
    assert len(banks) >= 2
    dead, others = banks[0], banks[1:]
    next(b for b in _record(stack, table)["banks"] if b["bank"] == dead)["consent_id"] = "deadbeef"
    state = _estimate(stack, table["consent_set_id"])
    assert next(p for p in state["progress"] if p["source"] == dead)["state"] == "failed"
    assert state["status"] == "done" and dead not in state["result"]["data"]["banks_used"]
    assert set(state["result"]["data"]["banks_used"]) == set(others)
    assert any("consent" in w for w in state["result"]["warnings"])


@pytest.mark.parametrize("registry", ["rs", "creditinfo"])
def test_a_registry_without_consent_is_skipped_with_a_warning(stack, registry):
    table = _consents(stack, CID)
    record = _record(stack, table)
    record[registry] = {"source": registry, "status": "error", "consent_id": None, "message": "refused"}  # no token
    state = _estimate(stack, table["consent_set_id"])
    assert state["status"] == "done", state  # used to die with a KeyError and leave the row on "running"
    step = next(p for p in state["progress"] if p["source"] == registry)
    assert step["state"] == "failed" and all(p["state"] in ("done", "failed") for p in state["progress"])
    assert state["result"]["sources_used"][registry] is False
    assert any(registry == "rs" and "Revenue Service" in w or "Creditinfo" in w for w in state["result"]["warnings"])


def test_result_has_shap_drivers_and_measured_confidence(stack):
    result = _estimate(stack, _consents(stack, CID)["consent_set_id"])["result"]
    explanation = result["explanation"]
    # TreeSHAP is additive: the starting point plus every driver is the model's P50
    total = explanation["starting_point_gel"] + sum(d["effect_gel"] for d in explanation["drivers"])
    assert abs(total - result["income"]["p50"]) <= 8  # rounding of each term to whole GEL
    assert explanation["drivers"][0]["driver"] and len(explanation["drivers"]) <= 7
    measured = result["confidence"]["measured"]
    assert measured is None or 0 <= measured["inside_p10_p90_pct"] <= 100


def test_response_carries_only_what_the_portal_shows(stack):
    result = _estimate(stack, _consents(stack, CID)["consent_set_id"])["result"]
    assert not {"cleaning", "loan_request", "rs_declared"} & set(result)
    assert result["sources_used"] == {"rs": True, "creditinfo": True}
    assert len(result["data"]["banks_used_names"]) == len(result["data"]["banks_used"])


def test_confidence_is_read_off_the_band_width():
    from confidence import LEVELS, level_of, score_of

    assert [level_of(b) for b in (0.0, 0.03, 0.031, 0.06, 0.10, 0.20, 0.21)] == [
        "very high", "very high", "high", "high", "medium", "low", "very low",
    ]  # fmt: skip
    assert score_of(0.174) == 83 and score_of(0.0) == 100 and score_of(1.5) == 0
    assert LEVELS[0] == "very high" and LEVELS[-1] == "very low"


def test_result_confidence_matches_its_band(stack):
    from confidence import level_of, score_of

    c = _estimate(stack, _consents(stack, CID)["consent_set_id"])["result"]["confidence"]
    assert c["level"] == level_of(c["band_share"]) and c["score"] == score_of(c["band_share"])
    assert c["plus_minus_pct"] == pytest.approx(50 * c["band_share"], abs=0.06)


def test_sources_lists_every_consent_target(stack):
    sources = stack.get("/v1/sources").json()
    assert [s["key"] for s in sources if s["kind"] == "registry"] == ["rs", "creditinfo"]
    assert sum(s["kind"] == "bank" for s in sources) == 12


def test_demo_clients_file_is_ten_distinct_clients():
    clients = json.loads((ROOT / "API" / "demo_clients.json").read_text())
    assert len(clients) == 10 and len({c["personal_id"] for c in clients}) == 10
    assert all({"title", "story", "facts", "banks", "has_deposit", "personal_id"} <= set(c) for c in clients)


def test_history_window_follows_as_of_or_else_where_the_bank_data_ends(stack, modules, clients):
    health = stack.get("/health").json()
    assert health["history_window_ends"] == AS_OF.isoformat() and health["model_trained_to"].startswith("2026-10-19")
    default = TestClient(modules[2].create_app(*clients))  # no --as-of: the mock banks say their data ends 2026-10-19
    assert default.get("/health").json()["history_window_ends"] == "2026-10-19"


def test_profile_has_name_age_and_where_each_part_came_from(stack):
    kyc = _consents(stack, CID)["kyc"]
    born = date.fromisoformat(kyc["birth_date"])
    assert kyc["age"] == AS_OF.year - born.year - ((AS_OF.month, AS_OF.day) < (born.month, born.day))
    assert kyc["name_en"] and kyc["name_ka"]
    assert {"name", "birth_date", "age", "place", "personal_id", "name_check"} <= set(kyc["provenance"])
    assert "demo_note" not in kyc["provenance"]  # developer notes do not belong in a bank user's screen
    assert kyc["name_check"] and all(c["matches"] and c["bank_name"] for c in kyc["name_check"])


def test_one_failing_bank_does_not_fail_the_consent_call(stack, modules):
    svc = stack.app.state.service
    victim = _connected(_consents(stack, CID))[0]
    original = svc.banks.post

    def flaky(url, *args, **kwargs):
        if f"/_mock/session/{victim}" in str(url):
            raise modules[2].httpx.ConnectError(f"{victim} is down")
        return original(url, *args, **kwargs)

    svc.banks.post = flaky
    try:
        table = _consents(stack, CID)
    finally:
        svc.banks.post = original
    failed = next(b for b in table["banks"] if b["bank"] == victim)
    assert failed["status"] == "error" and f"{victim} is down" in failed["message"]
    assert table["connected_banks"] >= 0 and len(table["banks"]) == 12  # every other bank still answered
