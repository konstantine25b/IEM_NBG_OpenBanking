"""Mock RS / Creditinfo: consent gating, values equal the generated customers, adapter output matches the registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "API"), str(ROOT / "income")]

import mock_registries as mr  # noqa: E402
from registry_adapter import to_registry  # noqa: E402

pytestmark = pytest.mark.skipif(not mr._shard_path("C00001").exists(), reason="generated dataset not present")

CID = "C00012"


@pytest.fixture(scope="module")
def client():
    return TestClient(mr.create_app())


def _session(client, service, customer=CID):
    s = client.post(f"/_mock/session/{service}", params={"customer": customer}).json()
    return {"Authorization": s["authorization"], "Consent-ID": s["consentId"]}


def test_rs_and_creditinfo_return_the_stored_values(client):
    truth = pd.read_parquet(mr._shard_path(CID)).set_index("cid").loc[CID]
    rs = client.get("/rs/v1/income-declarations", headers=_session(client, "rs")).json()
    ci = client.get("/creditinfo/v1/credit-report", headers=_session(client, "creditinfo")).json()
    assert rs["incomeItems"][0]["amount"] == pytest.approx(truth["df_INCOME_AMOUNT_SOI04"])
    assert rs["taxpayer"]["legalForm"] == truth["df_CLIENT_LEGAL_FORM"]
    assert ci["inquiries"]["last12Month"] == truth["df_last12Month"]
    assert ci["contracts"]["existingAsDebtor"] == truth["df_ExistingOperationsDebtor"]
    assert "INCOME_AMOUNT_TOTAL" not in str(rs) + str(ci)  # the label is never served


def test_adapter_reproduces_the_registry_fields(client):
    truth = pd.read_parquet(mr._shard_path(CID)).set_index("cid").loc[CID]
    rs = client.get("/rs/v1/income-declarations", headers=_session(client, "rs")).json()
    ci = client.get("/creditinfo/v1/credit-report", headers=_session(client, "creditinfo")).json()
    row = to_registry({"age": truth["df_Age"], "sex": truth["df_SEX"]}, rs, ci)
    for column in (
        "CLIENT_LEGAL_FORM",
        "GeoCitizen",
        "INCOME_AMOUNT_SOI04",
        "last1Month",
        "last3Month",
        "last6Month",
        "last12Month",
        "ExistingOperationsDebtor",
        "TerminatedOperationsDebtor",
        "Age",
        "SEX",
    ):
        assert (
            row[column] == pytest.approx(truth[f"df_{column}"])
            if column != "GeoCitizen"
            else row[column] == truth["df_GeoCitizen"]
        )


def test_consent_is_enforced(client):
    assert client.get("/rs/v1/income-declarations").status_code == 401
    rs = _session(client, "rs")
    assert (
        client.get("/creditinfo/v1/credit-report", headers=rs).status_code == 401
    )  # RS consent is not a bureau consent
    assert client.get("/rs/v1/income-declarations", headers={**rs, "Authorization": "Bearer nope"}).status_code == 401


def test_real_flow_and_person_id_lookup(client):
    person = pd.read_parquet(mr._shard_path(CID)).set_index("cid").loc[CID, "person_id"]
    created = client.post("/rs/v1/consents", json={"personId": f"PNOGE-{person}"})
    assert created.status_code == 201 and created.json()["consentStatus"] == "received"
    cid = created.json()["consentId"]
    headers = {"Consent-ID": cid, "Authorization": "Bearer x"}
    assert client.get("/rs/v1/income-declarations", headers=headers).status_code == 401  # not authorised yet
    token = client.post(f"/_mock/consents/rs/{cid}/authorise").json()["authorization"]
    assert client.get(f"/rs/v1/consents/{cid}/status").json() == {"consentStatus": "valid"}
    assert (
        client.get("/rs/v1/income-declarations", headers={**headers, "Authorization": token}).json()["personId"]
        == person
    )
    too_long = client.post("/rs/v1/consents", json={"personId": person, "validUntil": "2099-01-01"})
    assert too_long.status_code == 400
