"""The 12 banks listed on https://nbg.gov.ge/en/page/open-banking-detailed-data-and-documentation.

`dialect` says which wire format the mock speaks for the bank. `evidence` says how well that
format is known:
  spec      - machine-readable OpenAPI downloaded from the bank/vendor portal
  examples  - bank-published response examples (no full spec obtained)
  inferred  - same vendor portal as a verified sibling; not independently confirmed
  doc-only  - prose/PDF documentation only
Fields ending in `_verified` are False unless an authoritative source was seen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_PRODUCTS = {
    "current": "Current account", "fx": "Foreign currency account",
    "savings_term": "Term deposit", "savings_flex": "Savings account",
    "debit_card": "Visa Debit", "credit_card": "Visa Credit",
    "consumer": "Consumer loan", "instalment": "Instant instalment",
    "mortgage": "Mortgage loan", "mortgage_construction": "Mortgage (construction)",
    "mortgage_renovation": "Mortgage (renovation)", "overdraft": "Overdraft",
}


@dataclass(frozen=True)
class Kpi:
    """Published (or assumed) operational KPIs; used to simulate latency and failures."""

    ais_ms: float = 250.0
    pis_ms: float = 120.0
    cbpii_ms: float = 80.0
    uptime_pct: float = 99.8
    error_pct: float = 0.05
    source: str = "ASSUMED (NBG/EBA recommended: AIS<750ms, uptime>=99.5%, errors<=0.5%)"


@dataclass(frozen=True)
class BankProfile:
    key: str
    name_en: str
    name_ka: str
    iban_code: str
    bic: str
    # Share of retail customers by primary bank. DERIVED, not published: BoG+TBC = 0.78 is the mean of
    # their NBG loan (75.0%), asset (76.6%) and individual-deposit (81.4%) shares; the split follows
    # retail deposits/loans/digital users; the smaller banks are loan-share based, adjusted by
    # judgement (docs/CALIBRATION.md).
    weight: float
    dialect: str
    vendor: str
    evidence: str
    api_doc_url: str
    kpi_url: str
    sandbox_url: str | None = None
    prod_url: str | None = None
    spec_files: tuple[str, ...] = ()
    # All 12 IBAN codes/BICs match the NBG table https://nbg.gov.ge/en/payment-system/iban
    # (11 BICs also match the schwifty registry; Paysera's BIC is from NBG only).
    iban_code_verified: bool = True
    bic_verified: bool = True
    name_script: str = "latin"  # script used in ownerName/creditorName
    narrative_lang: str = "ka"
    kpi: Kpi = field(default_factory=Kpi)
    products: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        # every bank gets a complete product-name map; overrides win
        object.__setattr__(self, "products", {**DEFAULT_PRODUCTS, **self.products})


def _p(**kw: str) -> dict[str, str]:
    return dict(kw)


BANKS: dict[str, BankProfile] = {b.key: b for b in [
    BankProfile(
        "tbc", "TBC Bank", "თიბისი ბანკი", "TB", "TBCBGE22", 0.32, "tbc",
        "TBC in-house (ReadMe portal)", "examples",
        "https://developers.tbcbank.ge/docs/open-banking-overview",
        "https://developers.tbcbank.ge/docs/open-banking-statistics",
        sandbox_url="https://test-openbanking.tbcbank.ge", iban_code_verified=True,
        bic_verified=True, name_script="ka",
        prod_url="https://openbanking.tbcbank.ge/0.8",
        spec_files=("tbc/openbanking-services-account-information-service-ais.assembled.json",
                    "tbc/openbanking-service-for-loans-and-savings.assembled.json",
                    "tbc/openbanking-services-payments.assembled.json",
                    "tbc/openbanking-services-payments-v2.assembled.json"),
        kpi=Kpi(650.0, 420.0, 535.0, 99.13, 0.10,
                "TBC statistics page, 2026 months (AIS 597-969ms; error-rate unit unstated)"),
        products=_p(debit_card="Concept", credit_card="Visa Gold Credit"),
        notes="Berlin Group 1.3.6 base + v2 extensions (loans, savings, deferred/recurring "
              "payments). Published loan schema has no instalment/overdue fields."),
    BankProfile(
        "bog", "Bank of Georgia", "საქართველოს ბანკი", "BG", "BAGAGE22", 0.46, "bog",
        "BoG in-house (Redocusaurus)", "spec",
        "https://api.bog.ge/docs/openbanking", "https://api.bog.ge/docs/openbanking#tag/KPI",
        sandbox_url="https://xs2a-sandbox.bog.ge/0.8", name_script="latin",
        spec_files=("bog/plugin-redoc-0.yaml",),
        kpi=Kpi(297.5, 113.1, 0.0, 99.951, 0.0486, "BoG KPI table in its OpenAPI description"),
        products=_p(debit_card="Visa Classic", credit_card="Visa Platinum Credit"),
        notes="NextGenPSD2 1.3.9 spec v0.8: AIS+PIS+consents plus public /banking/products."),
    BankProfile(
        "terabank", "TeraBank", "თერაბანკი", "KS", "TEBAGE22", 0.02, "altasoft_v08", "AltaSoft",
        "spec", "https://portal-ob.terabank.ge/", "https://terabank.ge/ka/retail/statistics",
        sandbox_url="https://portal-ob.terabank.ge", spec_files=("terabank/swagger.yaml",),
        notes="AltaSoft PSD2 portal, NextGenPSD2 v0.8 (AccountDetails.owner)."),
    BankProfile(
        "liberty", "Liberty Bank", "ლიბერთი ბანკი", "LB", "LBRTGE22", 0.08, "altasoft_v08",
        "AltaSoft", "spec", "https://openbanking-sandbox.lb.ge/",
        "https://libertybank.ge/en/produqtebi/chemtvis/distantsiuri-momsakhureba/ghia-bankingi",
        sandbox_url="https://openbanking-sandbox.lb.ge", spec_files=("liberty/swagger.yaml",),
        notes="AltaSoft PSD2 portal, v0.8 (AccountDetails.ownerName). Differs from TeraBank "
              "by one field."),
    BankProfile(
        "silk", "Silk Bank", "სილქ ბანკი", "BT", "DISNGE22", 0.005, "altasoft_v2", "AltaSoft",
        "spec", "https://psd2-portal.silkbank.ge/?culture=ka-GE",
        "https://silkbank.ge/open-banking/",
        sandbox_url="https://psd2-portal.silkbank.ge", spec_files=("silk/swagger.yaml",),
        notes="AltaSoft PSD2 portal, v2.0: adds /v2/loans, /v2/savings, /v2/cards, /openfinance."),
    BankProfile(
        "cartu", "Cartu Bank", "ქართუ ბანკი", "CR", "CRTUGE22", 0.01, "altasoft_v2", "AltaSoft",
        "spec", "https://developerportal.cartubank.ge/?culture=ka-GE",
        "https://www.cartubank.ge/index.php?m=539&lng=eng",
        sandbox_url="https://developerportal.cartubank.ge", spec_files=("cartu/swagger.yaml",),
        notes="Same contract as Silk (diff = whitespace and example values only)."),
    BankProfile(
        "pasha", "PASHA Bank Georgia", "პაშა ბანკი საქართველო", "PB", "PAHAGE22", 0.009,
        "altasoft_v2", "AltaSoft", "spec", "https://psd2-portal.pashabank.ge/",
        "https://www.pashabank.ge/en/home", sandbox_url="https://psd2-portal.pashabank.ge",
        spec_files=("cartu/swagger.yaml",),
        notes="Portal is behind Cloudflare for scripts; opened in a normal browser, its "
              "swagger.yaml is byte-identical to Cartu's (v2.0, 169 operations). Production "
              "base URL not published."),
    BankProfile(
        "credo", "Credo Bank", "კრედო ბანკი", "CD", "JSCRGE22", 0.04, "obhome",
        "OB Home (shared SPA)", "spec", "https://openbanking.credo.ge/ob-home/app/analytics",
        "https://openbanking.credo.ge/ob-home/app/",
        sandbox_url="https://openbankgw.credo.ge/sandbox/0.8/v1",
        prod_url="https://openbankgw.credo.ge/0.8/v1",
        spec_files=("obhome/psd2-api_1.3.9_2021-05-04v1.yaml",),
        kpi=Kpi(189.0, 62.5, 66.2, 99.99, 0.017,
                "Credo /ob-home/api-analytics/get-analytics-kpi: median of 12 full prod months"),
        notes="One BG 1.3.9 YAML shared with Halyk and Basisbank (hash-identical)."),
    BankProfile(
        "halyk", "Halyk Bank Georgia", "ჰალიკ ბანკი საქართველო", "HB", "HABGGE22", 0.01,
        "obhome", "OB Home (shared SPA)", "spec",
        "https://openbank.hbg.ge/ob-home/app/analytics",
        "https://halykbank.ge/en/financial-reporting",
        sandbox_url="https://openbankgw.hbg.ge/sandbox/0.8/v1",
        prod_url="https://openbankgw.hbg.ge/0.8/v1",
        spec_files=("obhome/psd2-api_1.3.9_2021-05-04v1.yaml",),
        kpi=Kpi(2993.0, 139.0, 80.0, 99.997, 0.126,
                "Halyk /ob-home/api-analytics: median of 12 full prod months (AIS ~3 s)"),
        notes="Same OB Home spec as Credo/Basisbank; slowest published AIS latency."),
    BankProfile(
        "basisbank", "Basisbank", "ბაზისბანკი", "BS", "CBASGE22", 0.025, "obhome",
        "OB Home (shared SPA)", "spec", "https://openbank.bb.ge/ob-home/app/api-doc",
        "https://openbank.bb.ge/ob-home/app/analytics",
        sandbox_url="https://openbankgw.bb.ge/sandbox/0.8/v1",
        prod_url="https://openbankgw.bb.ge/0.8/v1",
        spec_files=("obhome/psd2-api_1.3.9_2021-05-04v1.yaml",),
        kpi=Kpi(1718.0, 3640.0, 80.0, 99.994, 0.146,
                "Basisbank /ob-home/api-analytics: median of 12 full prod months"),
        notes="NBG page link is malformed ('https:/openbank.bb.ge/ob-home/app/api-doc%20')."),
    BankProfile(
        "procredit", "ProCredit Bank Georgia", "პროკრედიტ ბანკი", "PC", "MIBGGE22", 0.015,
        "procredit", "ProCredit (Azure APIM + Redocly portal)", "examples",
        "https://www.procreditbank.ge/en/open-banking",
        "https://www.procreditbank.ge/en/open-banking",
        sandbox_url="https://online-test.procreditbank.ge/0.8/v1",
        prod_url="https://api.procreditbank.ge/0.8/v1",
        spec_files=("procredit/procredit_openapi_normalized.json",),
        kpi=Kpi(903.0, 5394.0, 0.0, 99.93, 0.25,
                "ProCredit KPI xlsx (2024; units unlabelled, probably ms)"),
        notes="12 operations; spec has examples only (no schemas). APIM subscription key + "
              "mandatory JWS headers."),
    BankProfile(
        "paysera", "Paysera Bank Georgia", "პეისერა ბანკი საქართველო", "PS", "PSRAGE22", 0.006,
        "paysera", "Paysera in-house", "doc-only",
        "https://developers.paysera.com/api/open-banking-georgia",
        "https://status.paysera.com/uptime-history/130797",
        prod_url="https://open-banking-api.paysera.com/xs2a/georgia/0.8/v1",
        spec_files=("paysera/paysera_georgia_endpoint_inventory.json",),
        kpi=Kpi(250.0, 150.0, 0.0, 99.8458, 0.05,
                "Paysera status page: Open banking 99.8458% over 90 days (latency ASSUMED)"),
        notes="NBG-listed URL is 404 (docs site rebuilt). No OpenAPI published; 25 endpoint pages. "
              "mTLS (QWAC) + OAuth, no request signing, no sandbox."),
]}

BANK_KEYS = list(BANKS)
