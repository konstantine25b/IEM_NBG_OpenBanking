"""Turn the RS and Creditinfo API responses (plus the lender's own KYC) into the `registry` dict of income_pipeline.

    from registry_adapter import to_registry
    registry = to_registry(applicant, rs_body, creditinfo_body)
    predict_person(registry, open_banking)

Column names are those of nbg_synthetic_data/df_*.csv, as income_predictor expects them.
Age, sex and region are the lender's own KYC, not RS or bureau data, so they come from `applicant`.
"""

from __future__ import annotations

SOI04 = "SOI04"


def to_registry(applicant: dict, rs: dict | None, creditinfo: dict | None) -> dict:
    """applicant: {"age", "sex", "region_name"?, "region_code"?, "city"?}. A missing source leaves its columns out
    (the model reads absent columns as "not reported")."""
    row: dict = {"Age": applicant.get("age"), "SEX": applicant.get("sex")}
    for key, column in (
        ("region_name", "region_name"),
        ("region_code", "CLIENT_GEOGRAPHICAL_REGION"),
        ("city", "city"),
    ):
        if applicant.get(key) is not None:
            row[column] = applicant[key]

    if rs:
        row["CLIENT_LEGAL_FORM"] = rs["taxpayer"]["legalForm"]
        row["GeoCitizen"] = "Yes" if rs["taxpayer"]["geoCitizen"] else "No"
        row["INCOME_AMOUNT_SOI04"] = next((i["amount"] for i in rs["incomeItems"] if i["code"] == SOI04), None)

    if creditinfo:
        for n in (1, 3, 6, 12):
            row[f"last{n}Month"] = creditinfo["inquiries"][f"last{n}Month"]
        row["ExistingOperationsDebtor"] = creditinfo["contracts"]["existingAsDebtor"]
        row["TerminatedOperationsDebtor"] = creditinfo["contracts"]["terminatedAsDebtor"]
    return row
