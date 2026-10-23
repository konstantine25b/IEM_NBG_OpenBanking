"""Mock RS / Creditinfo: consent gating, values equal the generated customers, adapter output matches the registry."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

CID = "C00012"


@pytest.fixture(scope="module")
def mr(mock_registries_on_mini):
    return mock_registries_on_mini


@pytest.fixture(scope="module")
def client(mr):
    return TestClient(mr.create_app())


def _truth(mr):
    return pd.read_parquet(mr._shard_path(CID)).set_index("cid").loc[CID]


def _session(client, service, customer=CID):
    s = client.post(f"/_mock/session/{service}", params={"customer": customer}).json()
    return {"Authorization": s["authorization"], "Consent-ID": s["consentId"]}


def test_rs_and_creditinfo_return_the_stored_values(client, mr):
    truth = _truth(mr)
    rs = client.get("/rs/v1/income-declarations", headers=_session(client, "rs")).json()
    ci = client.get("/creditinfo/v1/credit-report", headers=_session(client, "creditinfo")).json()
    assert rs["incomeItems"][0]["amount"] == pytest.approx(truth["df_INCOME_AMOUNT_SOI04"])
    assert rs["taxpayer"]["legalForm"] == truth["df_CLIENT_LEGAL_FORM"]
    assert ci["inquiries"]["last12Month"] == truth["df_last12Month"]
    assert ci["contracts"]["existingAsDebtor"] == truth["df_ExistingOperationsDebtor"]
    assert "INCOME_AMOUNT_TOTAL" not in str(rs) + str(ci)  # the label is never served


def test_adapter_reproduces_the_registry_fields(client, mr):
    from registry_adapter import to_registry

    truth = _truth(mr)
    rs = client.get("/rs/v1/income-declarations", headers=_session(client, "rs")).json()
    ci = client.get("/creditinfo/v1/credit-report", headers=_session(client, "creditinfo")).json()
    row = to_registry({"age": truth["df_Age"], "sex": truth["df_SEX"]}, rs, ci)
    numeric = (
        "CLIENT_LEGAL_FORM", "INCOME_AMOUNT_SOI04", "last1Month", "last3Month", "last6Month", "last12Month",
        "ExistingOperationsDebtor", "TerminatedOperationsDebtor", "Age", "SEX",
    )  # fmt: skip
    for column in numeric:
        assert row[column] == pytest.approx(truth[f"df_{column}"]), column
    assert row["GeoCitizen"] == truth["df_GeoCitizen"]


def test_consent_is_enforced(client):
    assert client.get("/rs/v1/income-declarations").status_code == 401
    rs = _session(client, "rs")
    # an RS consent is not a bureau consent
    assert client.get("/creditinfo/v1/credit-report", headers=rs).status_code == 401
    assert client.get("/rs/v1/income-declarations", headers={**rs, "Authorization": "Bearer nope"}).status_code == 401


def test_real_flow_and_person_id_lookup(client, mr):
    person = _truth(mr)["person_id"]
    created = client.post("/rs/v1/consents", json={"personId": f"PNOGE-{person}"})
    assert created.status_code == 201 and created.json()["consentStatus"] == "received"
    cid = created.json()["consentId"]
    headers = {"Consent-ID": cid, "Authorization": "Bearer x"}
    assert client.get("/rs/v1/income-declarations", headers=headers).status_code == 401  # not authorised yet
    token = client.post(f"/_mock/consents/rs/{cid}/authorise").json()["authorization"]
    assert client.get(f"/rs/v1/consents/{cid}/status").json() == {"consentStatus": "valid"}
    got = client.get("/rs/v1/income-declarations", headers={**headers, "Authorization": token}).json()
    assert got["personId"] == person
    assert client.post("/rs/v1/consents", json={"personId": person, "validUntil": "2099-01-01"}).status_code == 400
