"""Registry of the real-world numbers the generator is calibrated to.

Every target carries a status so nobody mistakes an assumption for a fact:
  VERIFIED    read in the cited primary or press source
  DERIVED     computed from verified figures (formula in the label/source)
  UNVERIFIED  seen only in a search snippet or recollection
  ASSUMED     the mock's own choice, no source
`gate=True` targets fail the report when the generated data misses them; the others are reported
for information (usually because they come from the supplied loan dataset on purpose).
Rendered into docs/CALIBRATION.md by `python -m obmock calibration-doc`.
"""
from __future__ import annotations

from dataclasses import dataclass

from obmock.banks import BANKS


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    target: float
    tol: float  # absolute, in `unit`
    unit: str
    status: str
    source: str
    gate: bool = True


NBG_FSR = "NBG Financial Sector Review, Sep 2026 (data to 2026-08)"
GEOSTAT = "Geostat, Household Income and Expenditure 2025 (25.05.2026); shares via georgiatoday.ge"
GROUP_SHARES_GEOSTAT = {"food": 38.7, "transport": 11.4, "housing_utilities": 11.3, "health": 11.0,
                        "clothing": 5.8, "durables": 5.1}  # + education 2.9 and other 13.7 (not modelled)
_MODELLED = sum(GROUP_SHARES_GEOSTAT.values())


def _targets() -> list[Target]:
    out: list[Target] = []
    for key, b in BANKS.items():
        out.append(Target(f"share.primary.{key}", f"{b.name_en}: share of customers by primary bank",
                          round(b.weight * 100, 3), 0.1, "%", "DERIVED",
                          "weights in banks.py: BoG+TBC 78% = mean of NBG loan (75.0), asset (76.6) and "
                          "individual-deposit (81.4) shares; split by retail deposits/loans/digital users; "
                          "others loan-share based (gbc.ge Q1-2026 ranking, georgiatoday.ge)"))
    out += [
        Target("share.primary.bog_tbc", "BoG + TBC: share of customers by primary bank", 78.0, 0.2, "%",
               "DERIVED", "mean of NBG loan 75.0 (gbc.ge Q1-2026), asset 76.6 (8M2025) and individuals' "
               "deposit 81.4 (Aug-2025) shares"),
        Target("share.api_rows.bog_tbc", "BoG + TBC: share of API transaction rows (card mirrors removed)",
               78.0, 5.0, "%", "DERIVED", "customer share; row share also depends on how many endpoints each "
               "bank exposes (TBC: cards, loans, savings; BoG: accounts only)"),
        Target("cards.debit_visa", "Visa share of debit cards (rest Mastercard)", 34.0, 4.0, "%",
               "UNVERIFIED", "NBG via trend.az Jul-2025: debit Mastercard >5.59m, Visa >2.84m (snippet)"),
        Target("cards.credit_visa", "Visa share of Visa+Mastercard credit cards", 75.0, 6.0, "%",
               "VERIFIED", "trend.az Jul-2025: credit cards Visa 299,481 vs Mastercard 99,642"),
        Target("cards.credit_holders", "customers holding a credit card", 23.0, 5.0, "%", "DERIVED",
               "761,484 credit cards (trend.az Jul-2025) / ~3m adults, ~1 card per holder"),
        Target("cards.atm_share", "ATM withdrawals as share of card transactions (count)", 7.0, 2.0, "%",
               "UNVERIFIED", "GBC snippet: 93/7 card-payment/cash split held for two years"),
        Target("deposits.term_fx_share", "foreign-currency share of term deposits (by count)", 50.0, 10.0,
               "%", "DERIVED", f"{NBG_FSR}, Fig 2.2: term LC 10.7% vs term FC 10.7% of deposits"),
        Target("deposits.usd_of_fx", "USD share of foreign-currency deposits (rest EUR)", 79.0, 8.0, "%",
               "VERIFIED", "1tv.ge, July 2026: USD 77.16%, EUR 20.30%"),
        Target("deposits.rate_gel", "average GEL term-deposit rate", 8.9, 0.9, "% p.a.", "VERIFIED",
               "1tv.ge, July 2026: weighted average GEL 8.91%"),
        Target("deposits.rate_fx", "average foreign-currency term-deposit rate", 2.5, 0.7, "% p.a.",
               "VERIFIED", "1tv.ge, July 2026: weighted average FC 2.52%"),
        Target("fees.paid_sms", "TBC/BoG primary customers paying the GEL 1/month SMS fee", 60.0, 6.0, "%",
               "ASSUMED", "the GEL 1 fee is VERIFIED (gbc.ge, 'TBC also cancels free SMS bank'); the "
               "share keeping the service is not published"),
    ]
    for g, v in GROUP_SHARES_GEOSTAT.items():
        out.append(Target(f"spend.{g}", f"{g.replace('_', ' ')}: share of modelled spend",
                          round(100 * v / _MODELLED, 1), 3.0, "%", "VERIFIED",
                          f"{GEOSTAT}: {v}% of cash consumption, renormalised over the six groups the "
                          "simulator models (education 2.9% and other 13.7% are not)"))
    out += [
        Target("info.income_mean", "mean monthly income in the supplied loan dataset vs Geostat mean wage "
               "(Q2-2026 GEL 2,389)", 2389.0, 0.0, "GEL", "VERIFIED", "georgiatoday.ge, Geostat Q2-2026; "
               "df incomes are kept as supplied (borrowers earn more than the all-employee average)",
               gate=False),
        Target("info.rate_gel_consumer", "mean GEL consumer-loan rate vs NBG Mar-2026 (16.7%)", 16.7, 0.0,
               "% p.a.", "VERIFIED", "NBG Credit Conditions Survey Q1-2026; loan rates come from the df",
               gate=False),
        Target("info.rate_gel_mortgage", "mean GEL mortgage rate vs NBG Mar-2026 (12.0%)", 12.0, 0.0,
               "% p.a.", "VERIFIED", "NBG Credit Conditions Survey Q1-2026; loan rates come from the df",
               gate=False),
        Target("info.loans_over_nbg_pti", "loans whose instalment exceeds the NBG PTI limit", 0.0, 0.0, "%",
               "VERIFIED", "nbg.gov.ge PTI rules: 25% (income < GEL 1,500) / 50% (>= 1,500) for GEL loans; "
               "the df loans are the label source and are not edited to comply", gate=False),
        Target("info.overdue_customers", "customers with an overdue loan (df-driven; real retail NPL "
               "is 1.3-1.5%)", 1.4, 0.0, "%", "VERIFIED", "BoG/TBC 2Q26 results: retail NPL 1.3% / 1.5%",
               gate=False),
    ]
    return out


TARGETS: dict[str, Target] = {t.key: t for t in _targets()}

# Choices that have no source at all; listed so the docs state them plainly.
ASSUMPTIONS = [
    ("Multi-banking", "30% of customers also hold a second bank, 3% a third; the second/third bank is drawn "
     "with the same weights as the primary bank"),
    ("Statement narratives", "Georgian/English wording of statement lines; portals with real samples were "
     "not browsed"),
    ("Merchants, MCC, ticket sizes", "merchant lists, ticket-size distributions and MCC per category"),
    ("Salary pay-day mix and holiday shift", "day-of-month distribution; a salary due on a weekend or "
     "holiday is paid the previous business day"),
    ("Remittance currency and operators", "corridor shares are NBG Aug-2026; the currency each lands in and "
     "the provider lists are assumed"),
    ("ISO 20022 code assignment", "which internal transaction kind maps to which bankTransactionCode / "
     "purposeCode (every code itself is validated against the official ISO table); neither TBC nor BoG "
     "publishes the codes it really uses"),
    ("TBC proprietary codes", "GMI (credit) / GMB (debit) are published only on example transfers and card "
     "transactions; here they appear on transfers and card entries only"),
    ("IBAN internal structure", "GE + check + bank code + 16 digits is standard; the digit layout inside the "
     "16 digits is random with a product digit"),
    ("Optional-field population", "which optional schema fields TBC/BoG really fill in production"),
    ("Loan default prevalence", "taken from the supplied df labels, far above real retail NPL, by design"),
]


def render_markdown() -> str:
    lines = ["# Calibration targets", "",
             "Generated by `python -m obmock calibration-doc`; do not edit by hand.", "",
             "| key | what | target | tolerance | status | source |", "|---|---|---|---|---|---|"]
    for t in TARGETS.values():
        tol = f"±{t.tol:g} {t.unit}" if t.gate else "info"
        lines.append(f"| `{t.key}` | {t.label} | {t.target:g} {t.unit} | {tol} | {t.status} | {t.source} |")
    lines += ["", "## Assumptions with no source", "", "| choice | detail |", "|---|---|"]
    lines += [f"| {a} | {b} |" for a, b in ASSUMPTIONS]
    return "\n".join(lines) + "\n"
