#!/usr/bin/env python3
"""
EDA.py - pre-modeling exploratory data analysis of the bank loan-portfolio dataset
===================================================================================

What is this?
    A one-command EDA of the credit-registry style loan data in ./Data (10 CSV files x
    10,000 rows + a Georgian data dictionary), written from a *data scientist before
    modeling* point of view:  What is in here?  Can I trust it?  What is the target?
    What leaks?  How should I split / encode / validate?  What score is realistic?

How to run (uv creates and manages the virtual environment for you):
    uv sync                                  # once: build .venv from uv.lock
    uv run python EDA.py                     # full run   -> reports/eda_report.html  (a few minutes)
    uv run python EDA.py --fast              # 20k-row smoke run (about a minute)
    uv run python EDA.py --sections 1,2,8    # only some sections
    uv run python EDA.py --target RestructuringStatusBool        # another binary column
    uv run python EDA.py --target score --binarize "<=150"       # binarise a numeric column
    uv run python EDA.py --no-mock                               # skip the mock / synthetic bank data (sections 11-14)

What you get (always inside ./reports, never inside ./Data or ./openbanking_mock):
    eda_report.html    ONE self-contained page: executive summary + one block per section
    summary.md         the same executive summary as plain text
    feature_roles.csv  hand-off table: every column's role, advice and feature sets A / A_strict / B / C
    figures/*.png      every chart as a stand-alone image
    tables/*.csv       every table (AUC ladder, IV / WoE, state tracking, ...)
    mock_customer_features.csv   only with --export-mock-features: one row per mock customer (synthetic, row-level)

Map of this file (search for the banners):
    0  CONFIG             thresholds ("rules of thumb") and the chart look
    1  KNOWLEDGE BASE     English glossary of the 58 columns, families, roles (the only hand-written facts)
    2  SMALL HELPERS      formatting, statistics (AUC, IV, PSI, copula ...), model helpers, plot helpers
    3  REPORT ENGINE      collects findings / figures / tables; writes the console text and the HTML page
    4  LOADING            pool the files, read the dictionary, choose the target, classify the columns
    SECTION 1  Overview & inventory       SECTION 6  Features vs the target
    SECTION 2  Data-quality scorecard     SECTION 7  Multivariate structure
    SECTION 3  Can the files be pooled    SECTION 8  Leakage & feature roles
    SECTION 4  The target                 SECTION 9  Baseline signal check (models)
    SECTION 5  Feature distributions      SECTION 10 Modeling blueprint
    PART II    mock & synthetic bank data (openbanking_mock/, read-only; skipped when absent or with --no-mock)
    SECTION 11 What the generated dataset is and how it links to the loans   SECTION 13 Realistic or planted?
    SECTION 12 What the bank APIs show a lender, and what they hide          SECTION 14 Bank-data features and how to use them
    99 MAIN               build_story() writes the executive summary, main() runs everything
    A section is one function marked @register(number, title, question); add a section by writing one.

Design rules: every number in the narrative is computed at run time (the rules of thumb live in CONFIG);
only the feature metadata (English glossary, families, roles) is hand-written.  Roles are hypotheses from
the dictionary: section 8 re-tests them against the data and reports where they fail.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import os
import platform
import re
import sys
import textwrap
import time
import traceback
import webbrowser
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import openpyxl
import pandas as pd
import scipy
import sklearn
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
from scipy import stats
from scipy.cluster import hierarchy
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial.distance import squareform
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupKFold, KFold, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier
from threadpoolctl import threadpool_limits

# ══════════════════════════════════════════════════════════════════════════════════
# 0. CONFIG - the only knobs you normally touch
# ══════════════════════════════════════════════════════════════════════════════════
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "Data"
DEFAULT_OUT_DIR = ROOT / "reports"
DEFAULT_TARGET = "addNegStatus"  # dictionary: "negative record statuses" (binary, ~3.6% = 1)
SEED = 42
FILE_COL, ROW_COL = "source_file", "row_in_file"  # added by this script - never used as features
FAST_ROWS = 20_000  # size of the stratified sample used by --fast
N_FOLDS = 5  # cross-validation folds used everywhere

# Rules of thumb used by the checks (also quoted in the report)
LEAK_AUC = 0.90  # a single feature with AUC above this is "too good to be true"
IV_STRONG, IV_SUSPICIOUS = 0.30, 0.50  # information-value bands used in credit scoring
CORR_REDUNDANT = 0.90  # |Spearman| above this -> the two columns say the same thing
ORDER_TOL = 1e-6  # a <= b is violated only when a exceeds b by more than this (non-money columns)
CAT_NOTE_SHARE = 0.01  # a category check that flags more than this share of the rows is worth a note
SPIKE_SHARE = 0.02  # one value holding more than 2% of the rows is a spike / sentinel
MATERIAL = 1.0  # money amounts below 1 currency unit are noise ("dust"), so accounting rules get this much tolerance
MAX_LEVELS_AS_CATEGORY = 12  # numeric columns with <= this many levels are drawn as categories
MAX_NOMINAL_LEVELS = 40  # an unordered code with more levels than this is encoded as log-frequency + a coarse bucket
# a text column with more levels looks like an ID, a date or free text and is left out of the features
MAX_TEXT_LEVELS = 60
MIN_EVENTS_PER_FOLD = 5  # fewer events than this per CV fold -> the model-based checks are skipped
RULE_TOL = 0.05  # a dictionary relationship broken in more than this share of rows counts as violated
MIN_BAD_RULES = 3  # this many violated relationships = "the columns do not obey their own definitions"
UNREALISTIC_AUC = 0.85  # an application-time AUC above this is a red flag for credit risk
RARE_EVENT = 0.10  # a positive rate below this is a rare-event problem
ID_LIKE = re.compile(r"(^id$|_id$|^id_|date|timestamp)", re.I)  # column names that look like an identifier or a date
EPV_COMFORT = 10  # events per candidate feature: below this, expect overfitting unless the model is regularised
# what a good application-time credit model usually reaches (rule of thumb quoted in the report)
REALISTIC_AUC = (0.65, 0.80)
# the best single application-time variable usually lands in the first two; above the third, look for leakage
SINGLE_COLUMN_AUC = (0.60, 0.70, 0.75)
# a column that separates look-alike non-events from clean non-events at least this well follows the loan's state
STATE_TRACKING_AUC = 0.75
# ... or one of its levels is this much more (or less) common among look-alikes than among clean loans (a single code can move without a high AUC)
STATE_LEVEL_GAP = 0.25
# the median single-column AUC at which the delinquency columns count as 'fingerprints of the label'
TIER0_CONFIRMED_AUC = 0.80
CLEAR_GAP = 0.01  # AUC difference between two models that is large next to fold-to-fold noise at this sample size

# ── chart look (dataviz rules: fixed categorical order, 2 hues for a binary target,
#    diverging = blue / grey / red, recessive grid, ink colours for all text) ─────────
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1"
# class 0 (no event) = blue, class 1 (event) = orange - used ONLY where the two classes are compared
C0, C1 = "#2a78d6", "#eb6834"
# everything that is not a class: single series, shares of rows, reference points
NEUTRAL, NEUTRAL_LIGHT = "#6b7280", "#c9ced6"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
STATUS = {"critical": "#d03b3b", "warning": "#fab219", "ok": "#0ca30c", "info": "#2a78d6"}
CMAP_SEQ = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
CMAP_DIV = LinearSegmentedColormap.from_list("div_blue_red", ["#1c5cab", "#f0efec", "#c43130"])

STYLE = {
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": "#cfcec9", "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.labelsize": 9.5, "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
    "text.color": INK, "grid.color": GRID, "grid.linewidth": 0.7, "axes.grid": True,
    "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 8.5,
    "font.size": 9.5, "lines.linewidth": 2.0, "figure.dpi": 100, "savefig.dpi": 120,
    "axes.prop_cycle": matplotlib.cycler(color=CAT),
}  # fmt: skip


def setup_matplotlib() -> None:
    """Draw to files only (no display needed) and apply the chart look. Called once by main(), never at import."""
    plt.switch_backend("Agg")
    plt.rcParams.update(STYLE)


# ══════════════════════════════════════════════════════════════════════════════════
# 1. KNOWLEDGE BASE - the only hand-written part: what each column *means*
#    (translated from the Georgian "Dictionary of Variables.xlsx").  Everything that
#    is a *number* in the report is computed from the data, never typed in here.
# ══════════════════════════════════════════════════════════════════════════════════
# family -> (display name, one-line meaning).  Order = order in the report.
FAMILIES: dict[str, tuple[str, str]] = {
    "customer": ("Customer profile", "who the borrower is"),
    "income": ("Income", "declared income of the borrower"),
    "loan_terms": ("Loan terms & product", "what was lent, to whom, on which conditions"),
    "instalments": ("Instalment schedule", "size and shape of the repayment schedule"),
    "balances": ("Balances & term-to-go", "what is still outstanding today"),
    "affordability": ("Affordability & collateral", "payment-to-income, loan-to-value, collateral"),
    "inquiries": ("Credit-bureau inquiries", "how often other lenders looked the borrower up"),
    "obligations": ("Borrower's other contracts", "how many contracts the borrower has / had"),
    "loan_events": ("Loan events", "refinancing / restructuring flags"),
    "delinquency": ("Delinquency & penalties", "overdue amounts, days past due, penalties"),
    "incumbent": (
        "Supplied score / PD",
        "a score and a probability of default shipped with the data (producer not stated)",
    ),
    "target": ("Target", "the column we want to predict"),
}

# column -> (family, kind, English description).
# kind: binary | nominal (unordered code) | ordinal | count | amount | ratio | rate | days | score
FEATURES: dict[str, tuple[str, str, str]] = {
    "CLIENT_GEOGRAPHICAL_REGION": ("customer", "nominal", "Client's region code (1-12; name mapping not provided)"),
    "CLIENT_LEGAL_FORM": ("customer", "nominal", "Client legal form (1 = sole proprietor, 7 = private individual; see lookup)"),
    "SEX": ("customer", "binary", "Sex code (1 / 2; mapping not provided)"),
    "Age": ("customer", "count", "Age in years"),
    "GeoCitizen": ("customer", "binary", "Citizen of Georgia (Yes / No)"),
    "IS_NEW_FOR_BANK": ("customer", "binary", "Customer is new to the bank"),
    "INCOME_AMOUNT_TOTAL": ("income", "amount", "Income amount (total); period, currency and gross/net are not stated"),
    "INCOME_AMOUNT_SOI04": ("income", "amount", "Income received from remittances (the dictionary code is 'SOI04', not explained)"),
    "DEBT_DISBURSED_REGION": ("loan_terms", "nominal", "Code of the city / district centre where the loan was disbursed (thousands of distinct codes; the thousands digit gives about 12 buckets)"),
    "DISBURSED_AMOUNT": ("loan_terms", "amount", "Amount disbursed, in nominal terms (currency / unit not stated)"),
    "ContractTermMonth": ("loan_terms", "count", "Contractual term in months"),
    "INTEREST_RATE_TYPE": ("loan_terms", "nominal", "Interest-rate type (1 fixed, 2 variable, 3 mixed)"),
    "INTEREST_RATE_NOMINAL": ("loan_terms", "rate", "Nominal interest rate fixed at disbursement (fraction)"),
    "INTEREST_RATE_PAYMENT_FREQUENCY": ("loan_terms", "nominal", "How often interest is paid (1 twice a month ... 7 other)"),
    "PRINCIPAL_PAYMENT_FREQUENCY": ("loan_terms", "nominal", "How often principal is repaid (1 twice a month ... 7 other)"),
    "PRODUCT_PURPOSE": ("loan_terms", "nominal", "Product / purpose code (2 consumer, 4 instant instalment, 7/10/13 mortgage)"),
    "ECONOMIC_SECTORS": ("loan_terms", "nominal", "Activity sector of the repayment source (National Bank list 0-26)"),
    "ECONOMIC_SECTORS_PURPOSE": ("loan_terms", "nominal", "Sector the loan is used for (the sheet has one sector list, assumed to apply)"),
    "DEBT_SEGMENT": ("loan_terms", "nominal", "Obligation segment (1 corporate, 2 SME, 3 micro, 4 retail)"),
    "LoanSegment": ("loan_terms", "binary", "Loan segment (Retail / Business)"),
    "loanType": ("loan_terms", "binary", "Contract sub-type (ConsumerLoan / MortgageLoan)"),
    "instalmentType": ("loan_terms", "binary", "Regular-payment type (Fixed / Variable)"),
    "AVERAGE_MONTHLY_PAYMENT": ("instalments", "amount", "Average monthly payment"),
    "standardInstalmentAmount": ("instalments", "amount", "Standard instalment amount"),
    "standardInstalmentCount": ("instalments", "count", "Number of instalments in the original schedule"),
    "MaximumAmountOfInstalmentBySchedule": ("instalments", "amount", "Largest instalment in the schedule"),
    "OutstandingPrincipalAm": ("balances", "amount", "Not-overdue principal still owed"),
    "outstandingAmount": ("balances", "amount", "Balance = not-overdue principal + interest / commission"),
    "TotalOutstandingAmount": ("balances", "amount", "Borrower's total current balance over all loans"),
    "RemainingTerm": ("balances", "days", "Days left until the contractual end date"),
    "PTI_RATIO": ("affordability", "ratio", "Ratio of monthly payment(s) to income (which payments are included is not stated)"),
    "LTV_RATIO": ("affordability", "ratio", "Ratio of the loan to its security / collateral (basis and direction not stated)"),
    "guaranteeAmount": ("affordability", "amount", "Collateral / security amount"),
    "last1Month": ("inquiries", "count", "Number of searches (inquiries) in the last 1 month; who searched is not stated"),
    "last3Month": ("inquiries", "count", "Number of searches (inquiries) in the last 3 months"),
    "last6Month": ("inquiries", "count", "Number of searches (inquiries) in the last 6 months"),
    "last12Month": ("inquiries", "count", "Number of searches (inquiries) in the last 12 months"),
    "ExistingOperationsDebtor": ("obligations", "count", "Current contracts where the subject is (co-)debtor; the minimum is 1 in every row, so it counts this loan too"),
    "TerminatedOperationsDebtor": ("obligations", "count", "Closed contracts where the subject was (co-)debtor"),
    "RefinancingStatusBool": ("loan_events", "binary", "Refinancing status (direction - this loan refinanced or was refinanced - is not stated)"),
    "RestructuringStatusBool": ("loan_events", "binary", "Restructuring status (a forbearance / credit-event flag)"),
    "OverduePrincipalAm": ("delinquency", "amount", "Current overdue principal"),
    "OverdueInterestCommission": ("delinquency", "amount", "Current overdue interest + commission"),
    "overdueInstalmentsCount": ("delinquency", "count", "Number of overdue instalments"),
    "overdueAmount": ("delinquency", "amount", "Overdue amount (dictionary: overdue principal + interest / commission)"),
    "MaximumNumberDelinquencyDays": ("delinquency", "days", "Maximum number of overdue days on this loan ('consecutive' is not stated)"),
    "TotalNumberDelinquencyDays": ("delinquency", "days", "Total overdue days since the loan started"),
    "TotalNumberDelinquencyDaysLastYear": ("delinquency", "days", "Total overdue days in the last 12 months"),
    "TotalNumberOverdueInst": ("delinquency", "count", "Instalments ever paid late (closed + current)"),
    "daysInDeliquency": ("delinquency", "days", "Overdue days of the latest 30+ day delinquency (the data contain only 0, 1 and values of 30 or more)"),
    "ShortDaysInDeliquency": ("delinquency", "days", "Days of minor overdue (short delinquency; unit assumed to be days)"),
    "penaltySum": ("delinquency", "amount", "Penalties accrued on the loan"),
    "penaltyPaid": ("delinquency", "amount", "Penalties paid on the loan"),
    "TotalUnpaidInstalments": ("delinquency", "count", "Total unpaid payments (the dictionary does not say whether this loan only or all of the borrower's loans)"),
    "TotalDebtOverdue": ("delinquency", "amount", "Total overdue debt (this loan only or all of the borrower's loans: not stated)"),
    "probabilityOfDefault": ("incumbent", "rate", "Probability of default in PERCENT (producer, horizon and default definition not stated)"),
    "score": ("incumbent", "score", "Credit score (producer, scale and horizon not stated; lower = riskier in this data)"),
    "addNegStatus": ("target", "binary", "Negative-record / additional status flag (candidate target)"),
}  # fmt: skip

# Roles describe how a column may be used when modeling.  The delinquency / incumbent roles
# are hypotheses from the dictionary; SECTION 8 re-tests them against the data every run.
ROLES: dict[str, tuple[str, str]] = {
    "target": ("Target", "what we predict"),
    "tier0_target_defining": ("Delinquency columns (tier 0)", "arrears facts recorded at the same snapshot as the label (no dates): a model that uses them detects the negative status instead of predicting it"),
    "incumbent_model_output": ("Supplied score / PD", "a score / PD shipped with the data (producer unknown): a benchmark, not a feature"),
    "post_origination_state": ("Measured at the snapshot", "balances, contract schedule and loan events as they stand at the snapshot; many change as a loan ages and their timing at application is unverified"),
    "snapshot_unverified": ("Bureau snapshot - timing unknown", "inquiry / other-contract counts measured at the snapshot date, not necessarily at application"),
    "origination_time": ("Known at origination", "terms, product, income and region fixed when the loan was granted"),
    "sensitive_attribute": ("Sensitive attribute", "legally or ethically delicate: audit fairness, usually exclude"),
}  # fmt: skip
ROLE_OF_FAMILY = {
    "delinquency": "tier0_target_defining",
    "incumbent": "incumbent_model_output",
    "balances": "post_origination_state",
    "instalments": "post_origination_state",
    "loan_events": "post_origination_state",  # refinancing / restructuring status at the snapshot (timing unverified)
    "inquiries": "snapshot_unverified",  # no date column: we cannot tell whether these counts predate the application
    "obligations": "snapshot_unverified",
    "target": "target",
}
SENSITIVE = ("SEX", "Age", "GeoCitizen")  # overrides the family default

# Readable labels for the small code sets (translated from the dictionary look-up sheet).
CODE_LABELS: dict[str, dict[float, str]] = {
    # The dictionary lists the NAMES in this order but never the numbers; codes 2-5 match the instalment-count / term ratio
    # in the data (1.0, 0.4, 0.27, 0.18), code 1 behaves like 'monthly' in the data, so its name is marked unverified (?).
    "PRINCIPAL_PAYMENT_FREQUENCY": {1: "twice a month (unverified)", 2: "monthly", 3: "quarterly", 4: "semi-annual", 5: "annual", 6: "seasonal", 7: "other"},
    "INTEREST_RATE_PAYMENT_FREQUENCY": {1: "twice a month (unverified)", 2: "monthly", 3: "quarterly", 4: "semi-annual", 5: "annual", 6: "seasonal", 7: "other"},
    "INTEREST_RATE_TYPE": {1: "fixed", 2: "variable", 3: "mixed"},
    "DEBT_SEGMENT": {1: "corporate", 2: "SME", 3: "micro", 4: "retail"},
    "CLIENT_LEGAL_FORM": {0: "other", 1: "sole proprietor", 2: "general partnership", 3: "limited partnership", 4: "LLC", 5: "joint-stock co.", 6: "cooperative", 7: "natural person (not a sole proprietor)", 8: "public-law entity", 9: "non-commercial legal entity", 10: "partnership"},
    "PRODUCT_PURPOSE": {1: "vehicle / auto loan", 2: "consumer loan", 3: "quick loan (pay-day)", 4: "instant instalment", 5: "overdraft", 6: "credit card", 7: "mortgage: buy finished property", 10: "mortgage: construction / property under construction", 13: "mortgage: renovation", 17: "student loan", 49: "other retail product", 51: "working capital", 52: "fixed assets", 99: "other business product"},
}  # fmt: skip
# Where do the numbers behind a code column come from?  (shown in the column catalog, so nobody mistakes a guess for a fact)
CODE_MAPPING_STATUS = {
    "PRINCIPAL_PAYMENT_FREQUENCY": "names listed in the dictionary, numbers assigned in list order (unverified; code 1 behaves like a distress state, not 'twice a month')",
    "INTEREST_RATE_PAYMENT_FREQUENCY": "names listed in the dictionary, numbers assigned in list order (unverified)",
    "INTEREST_RATE_TYPE": "names listed in the dictionary, numbers assigned in list order (unverified)",
    "DEBT_SEGMENT": "from the dictionary's look-up sheet",
    "CLIENT_LEGAL_FORM": "from the dictionary's look-up sheet",
    "PRODUCT_PURPOSE": "from the dictionary's look-up sheet (only a subset of its ids is translated here)",
    "ECONOMIC_SECTORS": "from the dictionary's look-up sheet (National Bank list)",
    "ECONOMIC_SECTORS_PURPOSE": "from the dictionary's look-up sheet (assumed to be the same list)",
    "CLIENT_GEOGRAPHICAL_REGION": "mapping not provided",
    "SEX": "mapping not provided",
    "DEBT_DISBURSED_REGION": "mapping not provided",
}
SECTOR_LABELS = {
    0: "without business", 1: "state bodies", 2: "financial institutions", 3: "wholesale pawnshop loans", 4: "real-estate development",
    5: "real-estate management", 6: "construction companies", 7: "construction materials", 8: "consumer-goods trade",
    9: "consumer-goods production", 10: "durable consumer goods", 11: "footwear, clothing, textile", 12: "trade (other)",
    13: "manufacturing (other)", 14: "hotels & tourism", 15: "restaurants, bars, cafes", 16: "heavy industry",
    17: "petrol stations / fuel importers", 18: "energy", 19: "car dealers", 20: "healthcare", 21: "pharma",
    22: "telecom", 23: "services", 24: "agriculture", 25: "other", 26: "remittance",
}  # fmt: skip
CODE_LABELS["ECONOMIC_SECTORS"] = SECTOR_LABELS
CODE_LABELS["ECONOMIC_SECTORS_PURPOSE"] = SECTOR_LABELS

FAMILIES["other"] = ("Other columns", "not described in the knowledge base")
# One colour per role, used by EVERY chart so colour always means the same thing.
# (blue / orange belong to the two classes, so no role uses them; every role is ALSO written next to the label, never colour alone)
ROLE_COLORS = {
    "origination_time": CAT[2],  # aqua
    "snapshot_unverified": CAT[3],  # yellow
    "post_origination_state": CAT[6],  # violet
    "tier0_target_defining": CAT[7],  # red
    "sensitive_attribute": CAT[4],  # magenta
    "incumbent_model_output": INK2,  # dark grey: an outside model's output
    "target": C1,
}
ROLE_TAG = {
    "origination_time": "origination", "snapshot_unverified": "bureau snapshot", "post_origination_state": "snapshot state",
    "tier0_target_defining": "delinquency", "sensitive_attribute": "sensitive", "incumbent_model_output": "supplied score", "target": "target",
}  # fmt: skip


def family_of(col: str) -> str:
    return FEATURES.get(col, ("other",))[0]


def describe(col: str) -> str:
    return FEATURES.get(col, ("other", "", ""))[2]


GLOSSARY: dict[str, str] = {
    "Event / positive": "A loan with the target = 1 (here: a negative record status). Everything else is a non-event.",
    "Delinquency / arrears": "Payments that are overdue. 'Days past due' = how many days late.",
    "Origination / snapshot": "Origination = the moment the loan was granted. The snapshot is the date the data was extracted (not given in the files).",
    "pp": "Percentage points: the difference between two percentages (12% vs 10% = 2 pp).",
    "AUC (ROC-AUC)": "The chance that a randomly chosen event is scored higher than a randomly chosen non-event. 0.5 = coin flip, 1.0 = perfect. Gini = 2 x AUC - 1.",
    "PR-AUC (average precision)": "Like AUC but focused on the rare class. A useless model scores the event rate itself (about 0.036 here), so it is always much lower than AUC.",
    "KS": "The largest gap between the score distributions of events and non-events: 0 = identical, 1 = completely separated.",
    "Lift": "Event rate in a group divided by the overall event rate. Lift 9x = nine times riskier than average.",
    "Cross-validation / out-of-fold": "Split the rows into folds, train on all but one and score the held-out fold, repeat. 'Out-of-fold' predictions were never seen by the model that made them, so they are an honest estimate.",
    "Stratified": "Every fold keeps the same event rate as the whole data - essential when events are rare.",
    "IV / WoE": "Weight of evidence (WoE) = the log of how much a bin shifts the odds of the event. Information value (IV) adds it up over bins: < 0.02 useless, 0.1-0.3 medium, > 0.5 very strong (common here because the label is easy to separate).",
    "PSI": "Population stability index: how different two distributions are. 0 = identical, > 0.25 = clearly shifted.",
    "VIF": "Variance inflation factor: how well a column is explained by all the others. Above 10 the column is nearly redundant (matters for linear models).",
    "Calibration": "Do predicted probabilities mean what they say? Of the loans scored 10%, about 10% should have the event. Brier score = mean squared error of the probabilities (lower is better).",
    "Data leakage": "Using information that would not be available when the prediction is made (or that defines the label itself). It inflates scores and fails in production.",
    "Tier 0 (delinquency columns)": "Arrears facts recorded at the same snapshot as the label. A model that uses them detects the negative status rather than predicting it; whether that is allowed is a question for the task rules.",
    "Supplied score / PD": "A credit score and a probability of default shipped as columns in the data. Who produced them, and with which horizon and default definition, is not stated.",
    "Sentinel value": "A special value (0, 1, 150 ...) that stands for 'none', 'unknown' or a rating floor rather than a real measurement.",
    "Fingerprint / repeats the same few values": "A column whose value (almost) gives the label away, or a column in which a few exact values repeat far more often than real histories would.",
    "Cohort": "A group of loans that share the same values on several columns.",
    "Generator (copula / GAN)": "Software that produces fake tables with realistic-looking columns. If the broken rules in this data are what such a generator would produce, the data may be synthetic.",
    "Dust": "Tiny positive values (below 1 currency unit) in a column where the true value is zero - typical of added noise.",
    "Run-off": "The late stage of a loan's life: almost nothing left to repay, no remaining term, no scheduled instalment.",
    "Gaussian copula": "A recipe for generating fake data that keeps each column's distribution and the rank correlations between columns - and nothing else (no accounting rules).",
    "Permutation importance": "Shuffle one column on held-out rows and see how much the AUC falls: a big fall = the model leans on that column.",
    "Mock / synthetic data": "Data generated by a program instead of recorded from real customers. Here: a simulated population of the supplied loans plus the bank-account histories their owners would have at 12 Georgian banks (sections 11-14).",
    "TPP / open banking": "A third-party provider that reads a customer's bank data through the bank's API after the customer consents (PSD2 style). The data comes in each bank's own format, with different fields filled.",
    "Latent stress": "A hidden 0-1 score the generator gives each simulated customer; it decides how they spend, save and pay. Section 13 shows it is a formula that includes the label.",
    "Card mirror": "The same card entry listed twice in the mock APIs (under the account and under the card account). Drop the mirrored copy before summing.",
}


# ══════════════════════════════════════════════════════════════════════════════════
# 2. SMALL HELPERS - formatting, statistics, plotting utilities
# ══════════════════════════════════════════════════════════════════════════════════
def pct(x: float, digits: int = 1) -> str:
    """0.0356 -> '3.6%'."""
    return f"{100 * x:.{digits}f}%"


def num(x: float) -> str:
    """Compact human number: 1234567 -> '1.23M', 0.0356 -> '0.0356'."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    a = abs(x)
    if float(x).is_integer() and a < 1e12:
        return f"{x:,.0f}"
    if a >= 1e9:
        return f"{x / 1e9:.2f}B"
    if a >= 1e6:
        return f"{x / 1e6:.2f}M"
    if a >= 1e4:
        return f"{x / 1e3:.1f}k"
    if a >= 100:
        return f"{x:,.0f}"
    if a >= 1:
        return f"{x:.2f}"
    return f"{x:.4g}"


def label_of(col: str, code) -> str:
    """'PRINCIPAL_PAYMENT_FREQUENCY', 2.0 -> '2 monthly' (falls back to the raw code)."""
    if isinstance(code, bool | np.bool_):
        return "yes" if code else "no"
    try:
        key = float(code)
        key = int(key) if key.is_integer() else key
    except (TypeError, ValueError):
        return str(code)
    name = CODE_LABELS.get(col, {}).get(key)
    return f"{key} {name}" if name else str(key)


def safe_auc(y: np.ndarray, score: np.ndarray) -> float:
    """ROC-AUC that returns 0.5 (instead of raising) when it is undefined."""
    score = np.asarray(score, dtype=float)
    if len(np.unique(y)) < 2 or not np.isfinite(score).all() or np.ptp(score) == 0:
        return 0.5
    return float(roc_auc_score(y, score))


def ks_stat(y: np.ndarray, score: np.ndarray) -> float:
    """Kolmogorov-Smirnov distance between the score distributions of class 1 and class 0."""
    s = np.asarray(score, dtype=float)
    return float(stats.ks_2samp(s[y == 1], s[y == 0]).statistic)


def best_single_cut(df: pd.DataFrame, cols: Sequence[str], y: np.ndarray) -> dict:
    """The one threshold on one numeric column that best reproduces the label (maximum Jaccard overlap).

    Every column is sorted once and all cut points are scanned in both directions with cumulative counts,
    so this is O(n log n) per column.  The cut is chosen in-sample, but it has a single free parameter on
    100,000 rows, so the optimism is negligible."""
    n_pos = int(y.sum())
    best: dict = {"jaccard": 0.0}
    for c in cols:
        x = df[c].to_numpy(float)
        order = np.argsort(x, kind="stable")
        xs, ys = x[order], y[order]
        cut_at = np.flatnonzero(np.diff(xs) != 0)  # a threshold may only sit between two different values
        if not len(cut_at):
            continue
        tp_low = np.cumsum(ys)[cut_at]
        n_low = cut_at + 1
        for sign, n_flag, tp in ((">", len(xs) - n_low, n_pos - tp_low), ("<=", n_low, tp_low)):
            jac = tp / np.maximum(n_flag + n_pos - tp, 1)
            i = int(np.argmax(jac))
            if jac[i] > best["jaccard"]:
                best = {
                    "jaccard": float(jac[i]),
                    "column": c,
                    "sign": sign,
                    "threshold": float(xs[cut_at[i]]),
                    "precision": float(tp[i] / max(n_flag[i], 1)),
                    "recall": float(tp[i] / max(n_pos, 1)),
                    "flagged": int(n_flag[i]),
                }
    return best


def bin_codes(x: pd.Series, q: int = 20, as_cat: bool = False) -> np.ndarray:
    """Integer bin id per row.  Unordered codes / text / few-level columns keep ONE BIN PER LEVEL (so a code is
    never read as an ordered number).  Numeric columns: every value that holds >= 1/q of the rows (a zero spike,
    a cap) is its own bin, the rest is cut into quantile bins."""
    if as_cat or not is_num(x) or x.nunique() <= q:
        return pd.factorize(x.astype(str), use_na_sentinel=False)[0]  # a missing value is its own level, never -1
    v = x.to_numpy(float)
    missing = np.isnan(v)
    if missing.any():  # missing values form their own bin instead of crashing the binning
        out = np.full(len(v), -1)
        out[~missing] = bin_codes(pd.Series(v[~missing]), q)
        out[missing] = out.max() + 1
        return out
    share = pd.Series(v).value_counts(normalize=True)
    mass = sorted(share[share >= 1 / q].index)
    codes = np.full(len(v), -1)
    for i, m in enumerate(mass):
        codes[v == m] = i
    rest = codes == -1
    if rest.any():
        n_rest = max(2, int(round(q * rest.mean())))
        codes[rest] = len(mass) + pd.qcut(pd.Series(v[rest]).rank(method="first"), n_rest, labels=False).to_numpy()
    return codes


def oof_signal_auc(
    x: pd.Series, y: np.ndarray, *, q: int = 20, k: float = 20.0, seed: int = SEED, as_cat: bool = False
) -> float:
    """Direction-free AUC of ONE feature that is honest about nominal codes.

    The column is turned into bins (see `bin_codes`: one bin per level for codes / text, quantile bins plus
    separate bins for spikes for numbers).  Each bin is scored by its (smoothed) target rate learned on the
    TRAINING folds only and evaluated on the held-out fold, so non-monotone patterns are captured and a feature
    cannot look good by memorising rare levels.  Returns max(AUC, 1-AUC)."""
    g = bin_codes(x, q, as_cat)
    n_lvl = int(g.max()) + 1
    oof = np.zeros(len(y))
    for tr, te in StratifiedKFold(N_FOLDS, shuffle=True, random_state=seed).split(g, y):
        prior = y[tr].mean()
        n = np.bincount(g[tr], minlength=n_lvl)
        s = np.bincount(g[tr], weights=y[tr], minlength=n_lvl)
        rate = (s + k * prior) / (n + k)  # smoothed target rate per level
        oof[te] = rate[g[te]]
    auc = safe_auc(y, oof)
    return max(auc, 1 - auc)


def woe_iv(x: pd.Series, y: np.ndarray, *, max_bins: int = 10, as_cat: bool = False) -> tuple[pd.DataFrame, float]:
    """Weight-of-evidence table and information value (credit-scoring standard).

    Bins: codes / text (`as_cat`) keep their levels with rare levels (< 1%) merged into 'other'; numbers get a
    separate bin for 0 when it holds >= 5% of the rows, quantile bins for the rest and a 'missing' bin if needed.
    IV bands (rule of thumb from scorecards): < 0.02 useless, 0.02-0.1 weak, 0.1-0.3 medium, 0.3-0.5 strong, > 0.5 very strong."""
    d = pd.DataFrame({"x": x.to_numpy(), "y": y})
    if not as_cat and is_num(x) and x.nunique() > max_bins:
        miss = d["x"].isna()
        d["bin"] = "missing"
        v = d.loc[~miss, "x"]
        zero = v == 0
        if zero.mean() >= 0.05:
            cut = pd.qcut(v[~zero], q=max_bins - 1, duplicates="drop")
            d.loc[v.index[zero.to_numpy()], "bin"] = "0"
            d.loc[v.index[~zero.to_numpy()], "bin"] = cut.astype(str).to_numpy()
            order = ["0", *[str(c) for c in cut.cat.categories]]
        else:
            cut = pd.qcut(v, q=max_bins, duplicates="drop")
            d.loc[v.index, "bin"] = cut.astype(str).to_numpy()
            order = [str(c) for c in cut.cat.categories]
        if miss.any():
            order.append("missing")
    else:
        vc = d["x"].astype(str).value_counts(normalize=True)
        keep = vc[vc >= 0.01].index
        d["bin"] = d["x"].astype(str).where(d["x"].astype(str).isin(keep), "other")
        order = sorted(d["bin"].unique(), key=lambda s: (s == "other", s))
    g = d.groupby("bin", observed=True)["y"].agg(["size", "sum"]).reindex(order).dropna()
    g.columns = ["n", "events"]
    g["non_events"] = g["n"] - g["events"]
    e_tot, ne_tot = g["events"].sum(), g["non_events"].sum()
    g["event_rate"] = g["events"] / g["n"]
    g["woe"] = np.log(((g["events"] + 0.5) / (e_tot + 0.5)) / ((g["non_events"] + 0.5) / (ne_tot + 0.5)))
    g["iv_part"] = ((g["events"] + 0.5) / (e_tot + 0.5) - (g["non_events"] + 0.5) / (ne_tot + 0.5)) * g["woe"]
    return g, float(g["iv_part"].sum())


def psi_from_counts(actual: np.ndarray, expected: np.ndarray) -> np.ndarray:
    """Population stability index from count tables (last axis = bins; 0 = identical, > 0.25 = clearly shifted)."""
    a = np.clip(actual / actual.sum(-1, keepdims=True), 1e-4, None)
    e = np.clip(expected / expected.sum(-1, keepdims=True), 1e-4, None)
    return ((a - e) * np.log(a / e)).sum(-1)


def skew_of(x: pd.Series) -> float:
    return float(stats.skew(x.to_numpy(dtype=float), nan_policy="omit")) if x.nunique() > 2 else 0.0


def legend_top(ax: plt.Axes, ncol: int = 3, handles=None, **kw) -> None:
    """Legend above the plot, left-aligned: never covers data.  Pass explicit `handles` (Patch / Line2D) when
    the entries are not artists of the axes themselves - an empty ax.barh() would draw every swatch blue."""
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 1.04), ncol=ncol, borderaxespad=0.2, **kw)


def role_handles(roles) -> list[Patch]:
    return [Patch(facecolor=ROLE_COLORS[r], label=ROLES[r][0]) for r in roles]


def wilson(k, n, z: float = 1.96) -> tuple[np.ndarray, np.ndarray]:
    """Wilson score interval for proportions k/n (works for arrays)."""
    k, n = np.asarray(k, float), np.asarray(n, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(n > 0, k / n, 0.0)
        denom = 1 + z**2 / n
        centre = (p + z**2 / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    # always lo <= p <= hi
    return np.clip(np.minimum(centre - half, p), 0, 1), np.clip(np.maximum(centre + half, p), 0, 1)


def is_num(s: pd.Series) -> bool:
    """A real number column: numeric and not boolean (pandas counts bool as numeric)."""
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def to_numeric_frame(df: pd.DataFrame, cols: Sequence[str], *, fill_missing: bool = False) -> pd.DataFrame:
    """Model-ready numeric copy: bool -> 0/1, text -> integer codes, numbers unchanged.  `fill_missing` replaces
    NaN by the column median (for statistics that cannot handle NaN, such as correlations and AUC)."""
    out = {}
    for c in cols:
        s = df[c]
        if pd.api.types.is_bool_dtype(s):
            out[c] = s.astype("int8")
        elif pd.api.types.is_numeric_dtype(s):
            out[c] = s.astype("float32")
        else:
            out[c] = pd.Series(pd.factorize(s)[0], index=s.index, dtype="int32")
    frame = pd.DataFrame(out, index=df.index)
    return frame.fillna(frame.median()) if fill_missing else frame


def make_hgb(**kw) -> HistGradientBoostingClassifier:
    """The one gradient-boosting configuration used everywhere (fast, no early stopping,
    deterministic).  Pass e.g. max_iter=60 to make it lighter."""
    params = {
        "max_iter": 150,
        "learning_rate": 0.1,
        "max_leaf_nodes": 24,
        "min_samples_leaf": 40,
        "l2_regularization": 1.0,
        "early_stopping": False,
        "random_state": SEED,
    }
    return HistGradientBoostingClassifier(**{**params, **kw})


def cv_predict(
    X: pd.DataFrame, y: np.ndarray, *, folds: int = N_FOLDS, seed: int = SEED, **hgb_kw
) -> tuple[np.ndarray, list[float]]:
    """Out-of-fold probabilities + per-fold AUCs of a stratified K-fold gradient-boosting model."""
    oof, aucs = np.zeros(len(y)), []
    for tr, te in StratifiedKFold(folds, shuffle=True, random_state=seed).split(X, y):
        p = make_hgb(**hgb_kw).fit(X.iloc[tr], y[tr]).predict_proba(X.iloc[te])[:, 1]
        oof[te] = p
        aucs.append(safe_auc(y[te], p))
    return oof, aucs


def runs_z(bits: np.ndarray) -> float:
    """Wald-Wolfowitz runs-test z-score of a 0/1 sequence (|z| > 3: not random order)."""
    n1 = int(bits.sum())
    n0 = len(bits) - n1
    if n1 == 0 or n0 == 0:
        return 0.0
    runs = 1 + np.count_nonzero(np.diff(bits))
    mu = 1 + 2 * n1 * n0 / (n1 + n0)
    var = 2 * n1 * n0 * (2 * n1 * n0 - n1 - n0) / ((n1 + n0) ** 2 * (n1 + n0 - 1))
    return float((runs - mu) / np.sqrt(var))


def drift_matrix(
    df: pd.DataFrame, cols: Sequence[str], as_cat: dict[str, bool], groups: np.ndarray, n_groups: int
) -> pd.DataFrame:
    """PSI of every group (file) against all the OTHER groups, per column.  Vectorised with
    contingency tables: rows = columns, columns = groups."""
    out = {}
    for c in cols:
        s = df[c]
        if as_cat[c] or not pd.api.types.is_numeric_dtype(s):
            codes = pd.factorize(s)[0]
        else:
            x = s.to_numpy(float)
            share = pd.Series(x).value_counts(normalize=True)
            # a value holding >= 10% of the rows is its own bin (zero spike, cap ...)
            mass = sorted(share[share >= 0.1].index)
            codes = np.full(len(x), -1)
            for i, v in enumerate(mass):
                codes[x == v] = i
            rest = codes == -1
            if rest.any():
                edges = np.unique(np.quantile(x[rest], np.linspace(0, 1, 11)[1:-1]))
                codes[rest] = len(mass) + np.digitize(x[rest], edges)
        tab = np.zeros((n_groups, int(codes.max()) + 1))
        np.add.at(tab, (groups, codes), 1)
        out[c] = psi_from_counts(tab, tab.sum(0) - tab)
    return pd.DataFrame(out).T


def model_frame(ctx: Ctx, cols: Sequence[str]) -> tuple[pd.DataFrame, list[str]]:
    """Gradient-boosting-ready frame.  Numbers stay numbers; text -> integer codes; unordered
    low-cardinality codes (region, sector, frequency ...) are returned in `categorical` so the
    model never reads code 12 as 'twelve'; a code with thousands of levels (disbursement region)
    becomes a log-frequency plus a coarse bucket."""
    df = ctx.df
    out: dict[str, pd.Series] = {}
    cat: list[str] = []
    for c in cols:
        s = df[c]
        if ctx.kinds[c] == "nominal" and s.nunique() > 40:
            out[f"{c}__log_freq"] = np.log1p(s.map(s.value_counts())).astype("float32")
            num_ = pd.to_numeric(s, errors="coerce")
            # round: codes 2990-3014 form ONE bump
            bucket = (
                (num_ / 1000).round() if num_.max() >= 1000 else pd.qcut(num_.rank(method="first"), 20, labels=False)
            )
            out[f"{c}__bucket"] = np.clip(bucket.fillna(0), 0, 254).astype("int16")
            cat.append(f"{c}__bucket")
            # codes piled up at the edges are often sentinels
            for tag, edge in (("is_min", num_.min()), ("is_max", num_.max())):
                if (num_ == edge).mean() >= 0.005:
                    out[f"{c}__{tag}"] = (num_ == edge).astype("int8")
        elif ctx.kinds[c] == "nominal":
            out[c] = pd.Series(pd.factorize(s)[0], index=s.index).astype("int32")
            cat.append(c)
        elif pd.api.types.is_bool_dtype(s):
            out[c] = s.astype("int8")
        elif pd.api.types.is_numeric_dtype(s):
            out[c] = s.astype("float32")
        else:
            out[c] = pd.Series(pd.factorize(s)[0], index=s.index, dtype="int32")
    return pd.DataFrame(out, index=df.index), cat


def eval_set(ctx: Ctx, cols: Sequence[str], *, rows: np.ndarray | None = None, folds: int = N_FOLDS, **kw) -> dict:
    """Cross-validated gradient boosting on a column set -> mean / std AUC, average precision, OOF scores."""
    x, cat = model_frame(ctx, cols)
    idx = np.arange(ctx.n) if rows is None else rows
    xs, ys = x.iloc[idx].reset_index(drop=True), ctx.y[idx]
    oof, aucs = cv_predict(xs, ys, folds=folds, categorical_features=cat or None, **kw)
    return {
        "auc": float(np.mean(aucs)),
        "std": float(np.std(aucs)),
        "ap": float(average_precision_score(ys, oof)),
        "oof": oof,
        "rows": idx,
        "n_features": len(cols),
        "fold_aucs": aucs,
    }


def stratified_rows(ctx: Ctx, n: int) -> np.ndarray:
    """A stratified random subset of row positions (all rows if n >= len)."""
    if n >= ctx.n:
        return np.arange(ctx.n)
    rng = np.random.default_rng(SEED)
    pos, neg = np.flatnonzero(ctx.y == 1), np.flatnonzero(ctx.y == 0)
    k_pos = max(int(round(n * ctx.base_rate)), 50)
    return np.sort(
        np.concatenate(
            [
                rng.choice(pos, min(k_pos, len(pos)), replace=False),
                rng.choice(neg, min(n - k_pos, len(neg)), replace=False),
            ]
        )
    )


def copula_violation(
    a: np.ndarray, b: np.ndarray, *, tol: float = ORDER_TOL, n: int = 40_000, seed: int = SEED
) -> float:
    """Violation rate of the rule 'a <= b' that a *generator knowing only* the two marginal
    distributions and their Spearman correlation would produce (Gaussian copula).  The latent
    correlation is found by bisection so the simulated Spearman matches the observed one (tie-aware)."""
    rng = np.random.default_rng(seed)
    z1, z2 = rng.standard_normal(n), rng.standard_normal(n)
    sa, sb = np.sort(a), np.sort(b)
    target = float(stats.spearmanr(a, b)[0])
    lo, hi = -0.999, 0.999
    for _ in range(12):
        r = (lo + hi) / 2
        ua, ub = stats.norm.cdf(z1), stats.norm.cdf(r * z1 + np.sqrt(1 - r * r) * z2)
        xa = sa[np.minimum((ua * len(sa)).astype(int), len(sa) - 1)]
        xb = sb[np.minimum((ub * len(sb)).astype(int), len(sb) - 1)]
        if float(stats.spearmanr(xa, xb)[0]) < target:
            lo = r
        else:
            hi = r
    return float(np.mean(xa > xb + tol))


def tree_rules(tree: DecisionTreeClassifier, X: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    """Leaves of a fitted decision tree as plain-English rules with n / events / precision / share of all events."""
    t = tree.tree_
    names = list(X.columns)
    conds: dict[int, list[tuple[str, str, float]]] = {}

    def walk(node: int, path: list[tuple[str, str, float]]) -> None:
        if t.children_left[node] == -1:
            conds[node] = path
            return
        f, thr = names[t.feature[node]], float(t.threshold[node])
        walk(t.children_left[node], [*path, (f, "<=", thr)])
        walk(t.children_right[node], [*path, (f, ">", thr)])

    def merge(path: list[tuple[str, str, float]]) -> str:  # several splits on one column -> one range
        lo, hi = {}, {}
        for f, op, thr in path:
            if op == ">":
                lo[f] = max(lo.get(f, -np.inf), thr)
            else:
                hi[f] = min(hi.get(f, np.inf), thr)
        parts = []
        for f in dict.fromkeys(f for f, _, _ in path):
            if f in lo and f in hi:
                parts.append(f"{lo[f]:,.4g} < {f} <= {hi[f]:,.4g}")
            elif f in lo:
                parts.append(f"{f} > {lo[f]:,.4g}")
            else:
                parts.append(f"{f} <= {hi[f]:,.4g}")
        return " AND ".join(parts)

    walk(0, [])
    leaf = tree.apply(X)
    rows = []
    for node, path in conds.items():
        m = leaf == node
        n, ev = int(m.sum()), int(y[m].sum())
        rows.append(
            {
                "rule": merge(path),
                "rows": n,
                "events": ev,
                "precision": ev / max(n, 1),
                "share of all events": ev / max(int(y.sum()), 1),
            }
        )
    return pd.DataFrame(rows).sort_values(["precision", "events"], ascending=False).reset_index(drop=True)


def count_profile(ctx: Ctx, counts: np.ndarray, xlabel: str) -> tuple[plt.Figure, pd.DataFrame]:
    """Left: how many rows have each count.  Right: event rate (95% CI) at each count."""
    t = pd.DataFrame({"k": counts, "y": ctx.y}).groupby("k")["y"].agg(n="size", events="sum")
    t = t[t["n"] >= 30]
    t["rate"] = t["events"] / t["n"]
    t["lo"], t["hi"] = wilson(t["events"], t["n"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.4, 3.6))
    labels = t.index.astype(str)
    rot = 30 if max(len(x) for x in labels) > 6 else 0  # long group names are tilted instead of overprinting each other
    a1.bar(labels, t["n"] / t["n"].sum() * 100, color=NEUTRAL, width=0.62)
    a1.tick_params(axis="x", rotation=rot)
    if rot:
        plt.setp(a1.get_xticklabels(), ha="right")
    a1.set_xlabel(xlabel)
    a1.set_ylabel("share of rows (%)")
    a1.set_title("how common is each count", fontsize=9.5)
    a2.errorbar(
        range(len(t)),
        t["rate"] * 100,
        yerr=[(t["rate"] - t["lo"]) * 100, (t["hi"] - t["rate"]) * 100],
        fmt="o-",
        color=C1,
        ecolor=C1,
        capsize=3,
        ms=6,
    )
    a2.axhline(ctx.base_rate * 100, color=INK2, ls="--", lw=1)
    a2.set_xticks(range(len(t)), labels, rotation=rot, ha="right" if rot else "center")
    a2.set_xlabel(xlabel)
    a2.set_ylabel("event rate (%)")
    a2.set_title("event rate (dashed = overall)", fontsize=9.5)
    fig.tight_layout()
    return fig, t


def find_markers(
    ctx: Ctx, cols: Sequence[str], rows: np.ndarray, *, k: int = 6, min_share: float = 0.005, min_lift: float = 4.0
) -> list[tuple[str, object]]:
    """Simple 'markers' (column == level, or column == 0) that are rare, strongly event-linked and not
    copies of each other.  Chosen on `rows` only, so they can be evaluated honestly on other rows."""
    y_, base = ctx.y[rows], float(ctx.y[rows].mean())
    cands = []
    for c in cols:
        s_ = ctx.df[c].iloc[rows]
        if ctx.plot_as[c] == "categorical" or s_.nunique() <= 60:
            levels = [v for v, n in s_.value_counts().items() if n >= min_share * len(s_)]
        elif (s_ == 0).mean() >= min_share:
            levels = [0]
        else:
            continue
        for v in levels:
            m = (s_ == v).to_numpy()
            if m.sum() >= min_share * len(m) and y_[m].mean() >= min_lift * base:
                cands.append((c, v, m, int(y_[m].sum())))
    cands.sort(key=lambda t: -t[3])
    chosen, covered = [], np.zeros(len(rows), bool)
    for c, v, m, _ in cands:
        # one marker per column, little overlap
        if len(chosen) < k and (m & covered).sum() <= 0.5 * m.sum() and c not in {x[0] for x in chosen}:
            chosen.append((c, v))
            covered |= m
    return chosen


def marker_counts(ctx: Ctx, markers: Sequence[tuple[str, object]], rows: np.ndarray) -> np.ndarray:
    return sum((ctx.df[c].iloc[rows] == v).to_numpy().astype(int) for c, v in markers)


def batch_balance(df: pd.DataFrame, cols: Sequence[str], files: Sequence[str], sizes: Sequence[int]) -> pd.DataFrame:
    """Are the rows of a file 'too evenly mixed' inside consecutive blocks?  For each block size b the
    variance of the block means (of normal scores) is divided by what random order would give; a ratio
    far below 1 means every block is more balanced than chance (batch-wise generation / stratified assembly)."""
    z_by_file = {}
    for f in files:
        sub_ = df[df[FILE_COL] == f]
        z_by_file[f] = {c: stats.norm.ppf((stats.rankdata(sub_[c].to_numpy(float)) - 0.5) / len(sub_)) for c in cols}
    rows = []
    for b in sizes:
        per_col = []
        for c in cols:
            vrs = []
            for f in files:
                z = z_by_file[f][c]
                n_, k_ = len(z), len(z) // b
                if k_ >= 4 and z.std() > 0:
                    means = z[: k_ * b].reshape(k_, b).mean(axis=1)
                    vrs.append(means.var(ddof=1) / (z.var(ddof=1) / b * (n_ - b) / (n_ - 1)))
            if vrs:
                per_col.append(float(np.median(vrs)))
        k_typ = max(len(df) // len(files) // b, 2)
        rows.append(
            {
                "block size (rows)": b,
                "blocks per file": k_typ,
                "median variance ratio": float(np.median(per_col)),
                "expected if order is random": k_typ / (k_typ - 1),
                "columns clearly too balanced (< 0.7)": float(np.mean(np.array(per_col) < 0.7)),
            }
        )
    return pd.DataFrame(rows).set_index("block size (rows)")


def high_event_band(s: pd.Series, y: np.ndarray, *, bins: int = 50, mult: float = 5.0) -> np.ndarray:
    """Rows whose value falls in a quantile bin whose event rate is >= `mult` x the overall rate: the
    'bucket' a model output assigns to troubled loans (a floor, a sentinel mass)."""
    cut = pd.qcut(s, bins, duplicates="drop")
    rate = pd.Series(y).groupby(cut.to_numpy(), observed=True).mean()
    hot = set(rate[rate >= mult * y.mean()].index)
    return cut.isin(hot).to_numpy()


# ══════════════════════════════════════════════════════════════════════════════════
# 3. REPORT ENGINE - collects everything and renders console text + one HTML page
# ══════════════════════════════════════════════════════════════════════════════════
CALLOUT_LABELS = {"info": "Note", "good": "Good news", "warn": "Watch out", "bad": "Problem"}
SEVERITIES = {  # key: (rank, label, console icon)
    "critical": (0, "CRITICAL", "✖"),
    "warning": (1, "WARNING", "▲"),
    "info": (2, "INFO", "ℹ"),
    "ok": (3, "OK", "✔"),
}


@dataclass
class Finding:
    """One thing the reader should know.  Severity is always shown as icon + word."""

    severity: str
    section: str
    title: str
    detail: str = ""
    action: str = ""
    priority: int = 3  # 1-2 = listed first in the executive summary, 3+ = folded away
    sec_id: str = ""  # anchor of the section, so the summary can link to it


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\*\w])\*([^\*\n]+?)\*(?![\*\w])", r"<i>\1</i>", s)  # *italic*
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)


def md(text: str) -> str:
    """Tiny markdown subset -> HTML: **bold**, `code`, blank-line paragraphs, '- ' bullets."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and lines[0].startswith("### "):
            out.append(f"<h3>{_inline(lines[0][4:])}</h3>")
            lines = lines[1:]
            if not lines:
                continue
        if lines and all(ln.startswith("- ") for ln in lines):
            out.append("<ul>" + "".join(f"<li>{_inline(ln[2:])}</li>" for ln in lines) + "</ul>")
        else:
            out.append(f"<p>{_inline(' '.join(lines))}</p>")
    return "".join(out)


def cell(v) -> str:
    """Format one table cell for humans."""
    if v is None or (isinstance(v, float | np.floating) and np.isnan(v)):
        return "–"
    if isinstance(v, bool | np.bool_):
        return "yes" if v else "no"
    if isinstance(v, int | np.integer):
        return f"{v:,}"
    if isinstance(v, float | np.floating):
        a = abs(v)
        if not np.isfinite(v):
            return str(v)
        if a == 0:
            return "0"
        if a >= 1000:
            return f"{v:,.0f}"
        if a >= 100:
            return f"{v:.1f}"
        if a >= 1:
            return f"{v:.2f}"
        return f"{v:.4f}" if a >= 0.0001 else f"{v:.1e}"
    return html.escape(str(v))


def df_to_html(df: pd.DataFrame, *, fmt: dict | None = None, bars: dict | None = None, index: bool = True) -> str:
    """DataFrame -> HTML table.  `fmt[col]` = format string or callable, `bars[col]` = (lo, hi)
    draws an in-cell data bar so a column can be scanned at a glance."""
    fmt, bars = fmt or {}, bars or {}
    numeric = {c: is_num(df[c]) for c in df.columns}
    head = "".join(f"<th class='{'num' if numeric[c] else ''}'>{html.escape(str(c))}</th>" for c in df.columns)
    head = f"<th>{html.escape(str(df.index.name or ''))}</th>{head}" if index else head
    rows = []
    for idx, *vals in df.itertuples(index=True, name=None):
        tds = [f"<th class='rowhead'>{html.escape(str(idx))}</th>"] if index else []
        for c, v in zip(df.columns, vals, strict=True):
            f = fmt.get(c)
            txt = f(v) if callable(f) else (f.format(v) if f and pd.notna(v) else cell(v))
            style, cls = "", ""
            if isinstance(v, int | float | np.number) and not isinstance(v, bool | np.bool_):
                cls = "num"
                if c in bars and np.isfinite(v):
                    lo, hi = bars[c]
                    frac = 0.0 if hi == lo else min(max((v - lo) / (hi - lo), 0.0), 1.0)
                    style, cls = f" style='--w:{frac * 100:.0f}%'", "num bar"
            tds.append(f"<td class='{cls}'{style}>{txt}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def console_float(v: float) -> str:
    """Console formatting of a float: 55,100 not 5.51e+04; 4 significant digits below 1,000."""
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}"


class Report:
    """Collects sections / findings / figures / tables as the script runs and writes them
    out as (a) live console text and (b) one self-contained HTML file."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.fig_dir, self.tab_dir = out_dir / "figures", out_dir / "tables"
        self.sections: list[dict] = []
        self.findings: list[Finding] = []
        self.failures: list[str] = []  # sub-analyses / sections that crashed (they make the exit code non-zero)
        self._n_fig = self._n_tab = 0
        self.t0 = time.perf_counter()

    MANIFEST = ".eda_manifest.json"

    def clear_outputs(self) -> None:
        """Remove the figures / tables written by the PREVIOUS run so the folder matches this run.
        Call it only after the arguments are validated.  Only files this script wrote are removed: they are
        listed in a manifest; an older output folder without one is recognised by its eda_report.html."""
        manifest = self.out_dir / self.MANIFEST
        old: list[Path] = []
        if manifest.exists():
            try:
                old = [self.out_dir / name for name in json.loads(manifest.read_text(encoding="utf-8"))]
            except (ValueError, OSError):
                old = []
        elif (self.out_dir / "eda_report.html").exists():
            old = [*self.fig_dir.glob("[0-9][0-9]_*.png"), *self.tab_dir.glob("[0-9][0-9]_*.csv")]
        for f in old:
            if f.is_file() and f.parent in (self.fig_dir, self.tab_dir):
                f.unlink()
        # hand-off files of the previous run must not outlive a failed run
        for name in ("eda_report.html", "feature_roles.csv", "summary.md", "mock_customer_features.csv"):
            (self.out_dir / name).unlink(missing_ok=True)
        self.fig_dir.mkdir(parents=True, exist_ok=True)
        self.tab_dir.mkdir(parents=True, exist_ok=True)

    def write_manifest(self) -> None:
        names = sorted(
            f"{d.name}/{f.name}" for d in (self.fig_dir, self.tab_dir) for f in d.glob("[0-9][0-9]_*") if f.is_file()
        )
        (self.out_dir / self.MANIFEST).write_text(json.dumps(names), encoding="utf-8")

    # ── console ────────────────────────────────────────────────────────────────
    def say(self, msg: str = "") -> None:
        print(msg, flush=True)

    @property
    def _blocks(self) -> list:
        return self.sections[-1]["blocks"]

    @property
    def _sec(self) -> str:
        return f"{self.sections[-1]['num']}. {self.sections[-1]['title']}" if self.sections else ""

    def section(self, num: int, title: str, question: str) -> None:
        """Open a new section: a title and the one question it answers."""
        self.sections.append(
            {"id": f"s{num}", "num": num, "title": title, "question": question, "blocks": [], "secs": 0.0}
        )
        self.say(f"\n{'═' * 84}\n SECTION {num} · {title}\n ❓ {question}\n{'═' * 84}")

    @contextmanager
    def timed(self) -> Iterator[None]:
        t = time.perf_counter()
        try:
            yield
        finally:
            self.sections[-1]["secs"] = time.perf_counter() - t
            self.say(f"   ⏱  {self.sections[-1]['secs']:.1f}s")

    # ── content blocks ─────────────────────────────────────────────────────────
    def text(self, markdown: str, *, console: bool = True) -> None:
        self._blocks.append(("text", md(markdown)))
        if console:
            plain = re.sub(r"[*`]", "", markdown).strip()
            for ln in plain.splitlines():
                self.say(f"   {ln.strip()}" if ln.strip() else "")

    @contextmanager
    def group(self, title: str, *, open_: bool = False) -> Iterator[None]:
        """Wrap the blocks written inside the `with` in a collapsible <details> (for advanced material)."""
        self._blocks.append(("group_open", title, open_))
        try:
            yield
        finally:
            self._blocks.append(("group_close",))

    def code(self, title: str, source: str) -> None:
        """A collapsible, copy-pasteable code block."""
        self._blocks.append(("code", title, source))

    def callout(self, kind: str, markdown: str) -> None:
        """kind: info | good | warn | bad."""
        if kind not in CALLOUT_LABELS:
            raise ValueError(f"unknown callout kind {kind!r}; use one of {sorted(CALLOUT_LABELS)}")
        self._blocks.append(("callout", kind, md(markdown)))

    def finding(self, severity: str, title: str, detail: str = "", action: str = "", *, priority: int = 3) -> None:
        if severity not in SEVERITIES:
            raise ValueError(f"unknown severity {severity!r}; use one of {sorted(SEVERITIES)}")
        f = Finding(
            severity, self._sec, title, detail, action, priority, self.sections[-1]["id"] if self.sections else ""
        )
        self.findings.append(f)
        self._blocks.append(("finding", f))
        self.say(f"   {SEVERITIES[severity][2]} [{SEVERITIES[severity][1]}] {title}")
        if detail:
            self.say(f"        {re.sub(r'[*`]', '', detail)}")

    def table(
        self,
        df: pd.DataFrame,
        name: str,
        title: str = "",
        note: str = "",
        *,
        fmt: dict | None = None,
        bars: dict | None = None,
        index: bool = True,
        max_rows: int = 30,
        console: int = 0,
    ) -> None:
        """Add a table (HTML shows `max_rows`, the CSV in reports/tables has everything)."""
        self._n_tab += 1
        csv = self.tab_dir / f"{self._n_tab:02d}_{name}.csv"
        index = index and not isinstance(df.index, pd.RangeIndex)  # a bare 0..n-1 row number says nothing
        df.to_csv(csv, index=index)
        shown = df.head(max_rows)
        body = df_to_html(shown, fmt=fmt, bars=bars, index=index)
        more = ""
        if len(df) > max_rows:
            rest = df_to_html(df.iloc[max_rows:], fmt=fmt, bars=bars, index=index)
            more = f"<details><summary>show the other {len(df) - max_rows} rows</summary><div class='tbl'>{rest}</div></details>"
        body = f"<div class='tbl{' tall' if len(shown) > 14 else ''}'>{body}</div>"
        self._blocks.append(("table", title, note, body + more, csv.name))
        if console:
            with pd.option_context(
                "display.width",
                200,
                "display.max_columns",
                30,
                "display.max_colwidth",
                70,
                "display.float_format",
                console_float,
            ):
                self.say("\n".join("   " + ln for ln in df.head(console).to_string(index=index).splitlines()))

    def figure(self, fig: plt.Figure, name: str, title: str, read: str, takeaway: str = "") -> None:
        """Save the figure as PNG, embed it in the HTML, and attach a how-to-read + takeaway."""
        self._n_fig += 1
        png = self.fig_dir / f"{self._n_fig:02d}_{name}.png"
        buf = io.BytesIO()
        # embedded copy: the HTML already has the title above it
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.15)
        fig.canvas.draw()
        top = fig.get_tightbbox(fig.canvas.get_renderer()).y1  # highest thing drawn (legend, title ...), in inches
        fig.text(
            0.01,
            (top + 0.06) / fig.get_figheight(),
            title.replace("`", ""),
            ha="left",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )
        fig.savefig(png, bbox_inches="tight", pad_inches=0.15)  # stand-alone copy: carries its own title
        plt.close(fig)
        self._blocks.append(("figure", title, read, takeaway, base64.b64encode(buf.getvalue()).decode(), png.name))
        if takeaway:
            self.say(f"   📈 {title}: {re.sub(r'[*`]', '', takeaway)}")

    @contextmanager
    def attempt(self, what: str) -> Iterator[None]:
        """Run one sub-analysis; if it fails, say so (console, report AND summary) and carry on with the rest."""
        try:
            yield
        except Exception as exc:
            plt.close("all")  # a half-drawn figure must not leak into the next sub-analysis
            self.failures.append(f"{self._sec}: {what}")
            self.say(f"   ▲ {what} could not be computed: {exc!r}\n{''.join(traceback.format_exception(exc)).strip()}")
            self._blocks.append(
                (
                    "callout",
                    "warn",
                    f"<p><b>{html.escape(what)} could not be computed</b> ({html.escape(repr(exc))}). The rest of the section is unaffected.</p>",
                )
            )
            self.findings.append(
                Finding(
                    "warning",
                    self._sec,
                    f"{what} could not be computed",
                    repr(exc),
                    "the rest of the report is unaffected; run the script in a terminal to see the traceback",
                    2,
                    self.sections[-1]["id"],
                )
            )

    def error(self, exc: BaseException) -> None:
        plt.close("all")
        # reports get shared: no local paths
        tb = "".join(traceback.format_exception(exc)).strip().replace(str(Path.home()), "~")
        self.failures.append(self._sec)
        self.say(f"   ✖ this section crashed: {exc!r}\n{tb}")
        self._blocks.append(
            (
                "callout",
                "bad",
                f"<p><b>This section crashed</b> ({html.escape(repr(exc))}); the rest of the report is unaffected.</p><pre>{html.escape(tb[-1500:])}</pre>",
            )
        )
        self.findings.append(
            Finding(
                "warning",
                self._sec,
                "A section crashed",
                repr(exc),
                "see the traceback in the section",
                1,
                self.sections[-1]["id"],
            )
        )

    # ── HTML ────────────────────────────────────────────────────────────────────
    def render_html(self, header: dict) -> str:
        parts = [HTML_HEAD.format(title=html.escape(header["title"]), css=HTML_CSS)]
        nav = "".join(f"<li><a href='#{s['id']}'>{s['num']}. {html.escape(s['title'])}</a></li>" for s in self.sections)
        parts.append(
            f"<nav><div class='brand'>EDA report</div><ol><li><a href='#summary'>Executive summary</a></li>{nav}</ol></nav><main>"
        )
        chips = "".join(
            f"<span class='chip'>{html.escape(k)}: <b>{html.escape(str(v))}</b></span>"
            for k, v in header["chips"].items()
        )
        parts.append(
            f"<header><h1>{html.escape(header['title'])}</h1><p class='sub'>{html.escape(header['subtitle'])}</p><div class='chips'>{chips}</div></header>"
        )
        parts.append(self._summary_html(header))
        for s in self.sections:
            parts.append(
                f"<section id='{s['id']}'><h2><span class='num'>{s['num']}</span>{html.escape(s['title'])}</h2>"
            )
            parts.append(f"<p class='question'>❓ {html.escape(s['question'])}</p>")
            parts.extend(self._block_html(b) for b in s["blocks"])
            parts.append("</section>")
        parts.append(f"<footer>{html.escape(header['footer'])}</footer></main></body></html>")
        return "".join(parts)

    def _summary_html(self, header: dict) -> str:
        counts = {k: sum(f.severity == k for f in self.findings) for k in SEVERITIES}
        chips = "".join(
            f"<span class='cnt sev-{k}'><b>{counts[k]}</b> {SEVERITIES[k][2]} {SEVERITIES[k][1].lower()}</span>"
            for k in SEVERITIES
        )

        def item(f: Finding) -> str:
            act = f"<div class='act'>→ {_inline(f.action)}</div>" if f.action else ""
            where = f"<a href='#{f.sec_id}'>{html.escape(f.section)}</a>" if f.sec_id else html.escape(f.section)
            return (
                f"<li class='find sev-{f.severity}'><span class='badge'>{SEVERITIES[f.severity][2]} {SEVERITIES[f.severity][1]}</span>"
                f"<div><b>{_inline(f.title)}</b><div class='det'>{_inline(f.detail)}</div>{act}<div class='where'>{where}</div></div></li>"
            )

        ordered = sorted(self.findings, key=lambda f: (f.priority, SEVERITIES[f.severity][0]))
        is_urgent = lambda f: f.severity in ("critical", "warning") and f.priority <= 2  # noqa: E731
        urgent = [item(f) for f in ordered if is_urgent(f)]
        calm = [item(f) for f in ordered if not is_urgent(f)]
        more = (
            f"<details><summary>{len(calm)} more findings (lower-priority warnings, info, ok)</summary><ul class='finds'>{''.join(calm)}</ul></details>"
            if calm
            else ""
        )
        top, extra = header.get("story") or [], header.get("story_more") or []
        story = (
            f"<h3>Five things to know</h3><ol class='five'>{''.join(f'<li>{_inline(t)}</li>' for t in top)}</ol>"
            if top
            else ""
        )
        story_more = (
            f"<details><summary>More findings, in plain language ({len(extra)})</summary><ul class='morestory'>{''.join(f'<li>{_inline(t)}</li>' for t in extra)}</ul></details>"
            if extra
            else ""
        )
        gloss = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in GLOSSARY.items())
        guide = (
            "<p class='guide'><b>How to read this page.</b> 5 minutes: the five points below, then sections 8 and 10. 20 minutes: add sections 4, 6 and 9. "
            "Every chart has a <i>How to read</i> line and a <i>Takeaway</i>. Findings are tagged ✖ critical · ▲ warning · ℹ info · ✔ ok. Unknown word? Open the glossary at the end of this summary.</p>"
        )
        return (
            f"<section id='summary'><h2>Executive summary</h2>{guide}{story}{story_more}<div class='counts'>{chips}</div>"
            f"<h3>Needs your attention</h3><ul class='finds'>{''.join(urgent) or '<li>Nothing critical.</li>'}</ul>{more}"
            f"<details class='gloss'><summary>Glossary - the terms used in this report</summary><dl>{gloss}</dl></details></section>"
        )

    @staticmethod
    def _block_html(b: tuple) -> str:
        kind = b[0]
        if kind == "group_open":
            return f"<details class='group'{' open' if b[2] else ''}><summary>{_inline(b[1])}</summary>"
        if kind == "group_close":
            return "</details>"
        if kind == "text":
            return f"<div class='prose'>{b[1]}</div>"
        if kind == "callout":
            label = CALLOUT_LABELS[b[1]]
            return f"<div class='callout {b[1]}'><span class='cl'>{label}</span>{b[2]}</div>"
        if kind == "code":
            return f"<details class='code' open><summary>{html.escape(b[1])}</summary><pre><code>{html.escape(b[2])}</code></pre></details>"
        if kind == "finding":
            f = b[1]
            act = f"<div class='act'>→ {_inline(f.action)}</div>" if f.action else ""
            return (
                f"<div class='callout sev-{f.severity}'><span class='cl'>{SEVERITIES[f.severity][2]} {SEVERITIES[f.severity][1]}</span>"
                f"<b>{_inline(f.title)}</b> {_inline(f.detail)}{act}</div>"
            )
        if kind == "table":
            _, title, note, body, csv = b
            t = f"<h4>{_inline(title)}</h4>" if title else ""
            n = f"<p class='note'>{_inline(note)}</p>" if note else ""
            return f"<div class='block'>{t}{n}{body}<div class='src'>full table: tables/{csv}</div></div>"
        _, title, read, takeaway, b64, png = b
        tk = f"<p class='take'><b>Takeaway.</b> {_inline(takeaway)}</p>" if takeaway else ""
        return (
            f"<figure class='block'><h4>{_inline(title)}</h4><div class='fig'><img alt='{html.escape(title.replace(chr(96), ''), quote=True)}' src='data:image/png;base64,{b64}'></div>"
            f"<figcaption><p class='read'><b>How to read.</b> {_inline(read)}</p>{tk}<span class='src'><a href='figures/{png}' target='_blank'>open full size</a> · figures/{png}</span></figcaption></figure>"
        )


HTML_HEAD = "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{title}</title><style>{css}</style></head><body>"
HTML_CSS = (f":root{{--bg:{SURFACE};--panel:#fff;--ink:{INK};--ink2:{INK2};--muted:{MUTED};--line:{GRID};--code:#f3f2ee;--accent:{C0};"
            f"--crit:{STATUS['critical']};--warn:{STATUS['warning']};--ok:{STATUS['ok']};--info:{STATUS['info']}}}") + """
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--panel:#222221;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;--line:#383835;--code:#2b2b29;--accent:#3987e5}}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;display:grid;grid-template-columns:270px minmax(0,1fr)}
nav{position:sticky;top:0;height:100vh;overflow:auto;padding:22px 18px;border-right:1px solid var(--line);font-size:13.5px}
nav .brand{font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:12px;color:var(--muted);margin-bottom:10px}
nav ol{list-style:none;margin:0;padding:0}nav li{margin:2px 0}nav a{display:block;padding:5px 8px;border-radius:6px;color:var(--ink2);text-decoration:none}
nav a:hover{background:var(--line);color:var(--ink)}
main{max-width:1240px;padding:28px 36px 80px;min-width:0}
header h1{margin:0 0 4px;font-size:28px;letter-spacing:-.01em}.sub{margin:0 0 12px;color:var(--ink2)}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{background:var(--code);border-radius:99px;padding:3px 10px;font-size:12.5px;color:var(--ink2)}
section{margin-top:42px;scroll-margin-top:12px}h2{font-size:22px;margin:0 0 4px;display:flex;align-items:center;gap:10px}
h2 .num{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:8px;background:var(--accent);color:#fff;font-size:15px}
.question{margin:0 0 14px;color:var(--ink2);font-style:italic}h4{margin:0 0 6px;font-size:15px}h3{font-size:17px;margin:26px 0 4px}
.prose p,.callout p{margin:.45em 0}.prose ul,.callout ul{margin:.4em 0;padding-left:22px}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.92em}pre{background:var(--code);padding:10px;border-radius:8px;overflow:auto;font-size:12px}
.sev-critical{--c:var(--crit)}.sev-warning{--c:var(--warn)}.sev-ok{--c:var(--ok)}.sev-info{--c:var(--info)}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 4px}.cnt{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--c);border-radius:10px;padding:4px 12px;font-size:13.5px}
.guide{background:var(--code);border-radius:10px;padding:8px 14px;font-size:14px;color:var(--ink2);margin:10px 0}
.five{list-style:none;counter-reset:five;padding:0;margin:8px 0 14px;display:grid;gap:10px}
.five>li{counter-increment:five;position:relative;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px 12px 58px;overflow-wrap:anywhere}
.five>li::before{content:counter(five);position:absolute;left:16px;top:12px;width:28px;height:28px;border-radius:50%;background:var(--accent);color:#fff;display:grid;place-items:center;font-weight:700;font-size:14px}
a{color:var(--accent)}.morestory{margin:6px 0;padding-left:20px}.morestory li{margin:6px 0;overflow-wrap:anywhere}.where a{color:var(--muted)}
.finds{list-style:none;padding:0;margin:0;display:grid;gap:8px}.find{display:flex;gap:12px;background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--c);border-radius:10px;padding:10px 14px}
.badge{flex:none;align-self:flex-start;font-size:11px;font-weight:700;letter-spacing:.05em;padding:2px 8px;border-radius:99px;background:var(--c);color:#fff;white-space:nowrap}
.sev-warning .badge{color:#1a1a19}.det{color:var(--ink2);font-size:14px}.find>div{min-width:0;overflow-wrap:anywhere}code,b{overflow-wrap:anywhere}.act{margin-top:3px;font-size:14px}.where{margin-top:3px;font-size:12px;color:var(--muted)}
.callout{position:relative;background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--c,var(--info));border-radius:10px;padding:8px 14px;margin:12px 0}
.callout.good{--c:var(--ok)}.callout.warn{--c:var(--warn)}.callout.bad{--c:var(--crit)}.callout .cl{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;margin-right:8px;color:var(--ink2)}
.block{margin:20px 0}.fig{background:#fcfcfb;border:1px solid var(--line);border-radius:12px;padding:8px;overflow:auto}.fig img{max-width:100%;height:auto;display:block;margin:0 auto}
figcaption p{margin:.4em 0;font-size:14px}.read{color:var(--ink2)}.src{font-size:11.5px;color:var(--muted)}.note{color:var(--ink2);margin:0 0 6px;font-size:14px}
.tbl{overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.tbl.tall{max-height:560px}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:5px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}td{max-width:34em}td.num,th.rowhead,td:first-child{white-space:nowrap}th.num{text-align:right}
thead th{position:sticky;top:0;background:var(--code);z-index:1}td.num{text-align:right;font-variant-numeric:tabular-nums}th.rowhead{background:var(--panel);font-weight:600;position:sticky;left:0;z-index:1}
td.bar{background:linear-gradient(90deg,rgba(42,120,214,.28) var(--w),transparent var(--w))}details{margin:6px 0}summary{cursor:pointer;color:var(--accent);font-size:13px}
.gloss dl{display:grid;grid-template-columns:minmax(150px,230px) 1fr;gap:6px 18px;margin:10px 0}.gloss dt{font-weight:600}.gloss dd{margin:0;color:var(--ink2)}
@media (max-width:700px){.gloss dl{grid-template-columns:1fr}}
pre code{padding:0;background:none}details.code summary{font-weight:600;margin-bottom:4px}details.group{border:1px dashed var(--line);border-radius:10px;padding:6px 14px;margin:18px 0}details.group>summary{font-weight:600;font-size:14.5px}details.code pre{margin:6px 0 0;max-height:520px}
footer{margin-top:60px;color:var(--muted);font-size:12.5px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:900px){body{grid-template-columns:1fr}nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:8px 12px}nav .brand{display:none}nav ol{display:flex;gap:6px;overflow-x:auto}nav a{white-space:nowrap;border:1px solid var(--line)}main{padding:18px}.find{flex-direction:column;gap:6px}.five>li{padding-left:50px}}
@media (max-width:700px){.fig img{max-width:none;width:820px}}
@media print{nav{display:none}body{display:block}.tbl{max-height:none}}
"""  # fmt: skip

SECTIONS: list[tuple[int, str, str, Callable[[Ctx], None]]] = []


def register(num: int, title: str, question: str):
    """Decorator that adds an analysis to the report: one function = one section.
    `question` is the single question the section answers (shown under its title)."""

    def deco(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
        SECTIONS.append((num, title, question, fn))
        return fn

    return deco


# ══════════════════════════════════════════════════════════════════════════════════
# 4. LOADING - pool the files, read the Georgian dictionary, decide the target
# ══════════════════════════════════════════════════════════════════════════════════
@dataclass
class Ctx:
    """Everything the sections share (so each section is a plain function of ctx)."""

    args: argparse.Namespace
    rep: Report
    df: pd.DataFrame  # pooled working frame (features + target + FILE_COL + ROW_COL)
    target: str
    y: np.ndarray  # 0 / 1 target as a numpy array
    features: list[str]  # every column except target / FILE_COL / ROW_COL
    defs_ka: dict[str, str]  # Georgian definitions from the xlsx
    file_info: pd.DataFrame  # one row per CSV file
    target_is_default: bool  # True when the target is the documented default and not chosen with --target
    kinds: dict[str, str] = field(default_factory=dict)  # column -> kind (see FEATURES)
    plot_as: dict[str, str] = field(default_factory=dict)  # column -> 'numeric' | 'categorical'
    roles: dict[str, str] = field(default_factory=dict)  # column -> role key (see ROLES)
    store: dict = field(default_factory=dict)  # results one section hands to the next

    @property
    def n(self) -> int:
        return len(self.df)

    @property
    def base_rate(self) -> float:
        return float(self.y.mean())

    def cols(
        self, *, family: str | None = None, role: str | None = None, kind: Sequence[str] | None = None
    ) -> list[str]:
        """Feature names filtered by family / role / kind (order = order in the file)."""
        out = []
        for c in self.features:
            fam = family_of(c)
            if (
                (family is None or fam == family)
                and (role is None or self.roles.get(c) == role)
                and (kind is None or self.kinds.get(c) in kind)
            ):
                out.append(c)
        return out


def discover_files(data_dir: Path) -> list[Path]:
    files = sorted(data_dir.glob("df_*.csv"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not files:
        sys.exit(f"No df_*.csv files found in {data_dir}. Pass --data-dir /path/to/Data.")
    return files


def load_dictionary(data_dir: Path) -> dict[str, str]:
    """Read the Georgian variable definitions (first sheet of the xlsx): column -> text."""
    path = next(iter(sorted(data_dir.glob("Dictionary*.xlsx"))), None)
    if path is None:
        return {}
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        defs = {
            str(r[0]).strip(): str(r[1]).strip()
            for r in wb.worksheets[0].iter_rows(min_row=2, values_only=True)
            if r[0] and r[1]
        }
        wb.close()
    except Exception as exc:  # the dictionary is only a glossary: a broken download must not stop the analysis
        print(f"(the dictionary {path.name} could not be read: {exc!r}; continuing without the Georgian definitions)")
        return {}
    return defs


def pool_files(files: list[Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read every CSV, tag rows with their file, stack them.  Returns (pooled, file_info)."""
    frames, info = [], []
    base_cols = None
    for f in files:
        d = pd.read_csv(f)
        base_cols = base_cols or list(d.columns)
        info.append(
            {
                "file": f.name,
                "rows": len(d),
                "columns": d.shape[1],
                "size_MB": f.stat().st_size / 1e6,
                "same_schema": list(d.columns) == base_cols,
            }
        )
        d.insert(0, ROW_COL, np.arange(len(d)))
        d.insert(0, FILE_COL, f.stem)
        frames.append(d)
    pooled = pd.concat(frames, ignore_index=True)
    return pooled, pd.DataFrame(info).set_index("file")


def candidate_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Scan for outcome-like columns: binary, rare-ish, with a telling name."""
    rows = []
    for c in df.columns:
        if c in (FILE_COL, ROW_COL):
            continue
        s = df[c]
        if s.nunique() == 2:
            pos = s.value_counts(normalize=True).min()
            hint = bool(re.search(r"neg|default|status|bad|fraud|churn|delinq|overdue", c, re.I))
            rows.append({"column": c, "positive_rate": pos, "name_hint": hint, "family": FEATURES.get(c, ("?",))[0]})
    out = pd.DataFrame(rows)
    if len(out):
        out["score"] = out["name_hint"].astype(int) * 2 + out["positive_rate"].between(0.005, 0.30).astype(int)
        out = out.sort_values(["score", "positive_rate"], ascending=[False, True]).reset_index(drop=True)
    return out


def positive_level(s: pd.Series):
    """The level coded 1: True for a boolean column, otherwise the rarer level."""
    return True if pd.api.types.is_bool_dtype(s) else s.value_counts().index[-1]


def to_binary_target(s: pd.Series) -> np.ndarray:
    """Map a 2-valued column to 0/1 where 1 = the rarer level (True for a boolean column)."""
    return (s == positive_level(s)).astype(int).to_numpy()


def build_context(args: argparse.Namespace, rep: Report) -> Ctx:
    data_dir = Path(args.data_dir)
    if args.binarize and not args.target:
        sys.exit("--binarize needs --target COLUMN (the numeric column to turn into 0/1)")
    files = discover_files(data_dir)
    df, file_info = pool_files(files)
    defs_ka = load_dictionary(data_dir)
    candidates = candidate_targets(df)

    # --- the target (default, or user supplied; optionally a binarised numeric column) ---
    target_is_default = args.target is None
    target = args.target or DEFAULT_TARGET
    source_col = None  # the column a binarised target is derived from (it must not be a feature)
    if target not in df.columns:
        hint = ", ".join(candidates["column"].head(5)) if len(candidates) else "none found"
        sys.exit(f"Target '{target}' is not a column. Binary candidates: {hint}")
    n_unlabelled = int(df[target].isna().sum())
    if n_unlabelled:  # a missing label is not "class 0": drop those rows and say so
        rep.say(f"{n_unlabelled:,} rows have no value in '{target}' and are dropped")
        df = df[df[target].notna()].reset_index(drop=True)
    if args.binarize:
        m = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*", args.binarize)
        if not m:
            sys.exit("--binarize expects e.g. '<=150' or '>=50'")
        if not is_num(df[target]):
            sys.exit(f"--binarize needs a numeric column; '{target}' is {df[target].dtype}")
        op, thr = m.group(1), float(m.group(2))
        flag = {
            ">=": df[target] >= thr,
            "<=": df[target] <= thr,
            "==": df[target] == thr,
            "!=": df[target] != thr,
            ">": df[target] > thr,
            "<": df[target] < thr,
        }[op]
        if int(flag.sum()) in (0, len(flag)):
            sys.exit(
                f"--binarize '{args.binarize}' leaves a single class on '{target}' (range {df[target].min():g} .. {df[target].max():g}); pick another threshold"
            )
        new = f"{target}{op}{m.group(2)}"
        df[new] = flag.astype(int)
        source_col, target, target_is_default = target, new, False
    elif df[target].nunique() != 2:
        sys.exit(
            f"Target '{target}' has {df[target].nunique()} distinct values; use --binarize '<op><number>' or pick a binary column."
        )
    if args.fast and len(df) > FAST_ROWS:  # stratified subsample: keeps the class balance
        y_all = to_binary_target(df[target])
        keep = df.groupby(y_all, group_keys=False).sample(frac=FAST_ROWS / len(df), random_state=SEED).index
        df = df.loc[keep].sort_index().reset_index(drop=True)
    y = to_binary_target(df[target])
    # the documented outcome is not an input for another outcome
    other_outcome = (
        DEFAULT_TARGET
        if target != DEFAULT_TARGET and DEFAULT_TARGET in df.columns and source_col != DEFAULT_TARGET
        else None
    )
    wide_text = [
        c
        for c in df.columns
        if c not in (target, FILE_COL, ROW_COL, source_col, other_outcome)
        and not pd.api.types.is_numeric_dtype(df[c])
        and not pd.api.types.is_bool_dtype(df[c])
        and df[c].nunique() > MAX_TEXT_LEVELS
    ]
    features = [c for c in df.columns if c not in (target, FILE_COL, ROW_COL, source_col, other_outcome, *wide_text)]
    ctx = Ctx(args, rep, df, target, y, features, defs_ka, file_info, target_is_default)
    ctx.store["candidates"] = candidates
    ctx.store["positive_label"] = positive_level(df[target]) if not args.binarize else 1
    if source_col:
        ctx.store["derived_from"] = source_col
    if other_outcome:
        ctx.store["other_outcome"] = other_outcome
    if wide_text:
        ctx.store["wide_text"] = wide_text
    if n_unlabelled:
        ctx.store["unlabelled"] = n_unlabelled
    n_pos = int(y.sum())
    if min(n_pos, len(y) - n_pos) < MIN_EVENTS_PER_FOLD * N_FOLDS:
        rep.say(
            f"only {min(n_pos, len(y) - n_pos)} rows in the smaller class: too few for {N_FOLDS}-fold cross-validation, the model-based checks are skipped"
        )
        ctx.store["too_few_events"] = True
        args.no_models = True
    classify_features(ctx)
    return ctx


def classify_features(ctx: Ctx) -> None:
    """Decide for every column: kind (from the knowledge base, else inferred), how to plot
    it, and which modeling role it plays."""
    for c in ctx.features:
        s = ctx.df[c]
        if c in FEATURES:
            kind = FEATURES[c][1]
        elif s.nunique() == 2 or pd.api.types.is_bool_dtype(s):
            kind = "binary"
        elif not pd.api.types.is_numeric_dtype(s):
            kind = "nominal"
        else:
            integral = bool((s.dropna() % 1 == 0).all())
            kind = (
                "count" if integral and s.nunique() > MAX_LEVELS_AS_CATEGORY else ("ordinal" if integral else "amount")
            )
        ctx.kinds[c] = kind
        numeric = is_num(s)
        few = s.nunique() <= MAX_LEVELS_AS_CATEGORY
        ctx.plot_as[c] = (
            "categorical"
            if kind in ("binary", "nominal") or ((not numeric or few) and s.nunique() <= 60)
            else "numeric"
        )
        ctx.roles[c] = "sensitive_attribute" if c in SENSITIVE else ROLE_OF_FAMILY.get(family_of(c), "origination_time")


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 1 · Overview & inventory
# ══════════════════════════════════════════════════════════════════════════════════
@register(1, "Overview & inventory", "What exactly did we receive - and does it match the documentation?")
def section_overview(ctx: Ctx) -> None:
    """What did we receive?  Files, schema, missing values, the column catalog with roles, and a map of the column families."""
    rep, df, fi, feats = ctx.rep, ctx.df, ctx.file_info, ctx.features
    n_files, rows_total, same = len(fi), int(fi["rows"].sum()), bool(fi["same_schema"].all())
    n_missing = int(df[[*feats, ctx.target]].isna().sum().sum())
    n_dups = int(df.duplicated(subset=feats).sum())
    mode = f"  *(--fast: working on a stratified {ctx.n:,}-row sample)*" if ctx.args.fast else ""

    rep.callout(
        "info",
        "**How to read this report.** Every section opens with the one question it answers. Each chart carries a "
        "*How to read* line and a computed *Takeaway*. Findings are tagged ✖ critical · ▲ warning · ℹ info · ✔ ok and "
        "are collected in the executive summary at the top. Everything is also saved as PNG / CSV in `reports/`.",
    )
    rep.text(
        f"**{n_files} files · {rows_total:,} rows · {int(fi['columns'].iloc[0])} columns each.**{mode}\n\n"
        f"Pooled into one table this is {ctx.n:,} rows × {len(feats)} candidate features plus the target `{ctx.target}`. "
        f"Missing values: **{n_missing:,}**. Exact duplicate rows: **{n_dups:,}**. Every file has "
        f"{'the same' if same else 'a DIFFERENT'} schema."
    )

    total = pd.DataFrame(
        {
            "rows": [rows_total],
            "columns": [int(fi["columns"].iloc[0])],
            "size_MB": [fi["size_MB"].sum()],
            "same_schema": [same],
        },
        index=["TOTAL"],
    )
    inv = pd.concat([fi, total])
    inv.index.name = "file"
    rep.table(inv, "file_inventory", "Files received", fmt={"size_MB": "{:.1f}", "rows": "{:,}"}, console=3)

    if "derived_from" in ctx.store:
        rep.finding(
            "info",
            f"`{ctx.store['derived_from']}` was removed from the features",
            f"The target `{ctx.target}` is defined from it, so keeping it would be a trivial leak.",
            "",
        )
    if "unlabelled" in ctx.store:
        rep.finding(
            "warning",
            f"{ctx.store['unlabelled']:,} rows had no value in `{ctx.target}` and were dropped",
            "A missing label is not class 0; the event rate is computed on the labelled rows only.",
            "check why the label is missing",
        )
    if "wide_text" in ctx.store:
        rep.finding(
            "warning",
            f"{len(ctx.store['wide_text'])} text columns with more than {MAX_TEXT_LEVELS} levels were left out",
            ", ".join(ctx.store["wide_text"][:6]) + ": they look like IDs, dates or free text.",
            "parse dates / drop IDs before modeling; they are not used as features here",
        )
    if ctx.store.get("too_few_events"):
        rep.finding(
            "warning",
            "Too few events for cross-validated models",
            f"fewer than {MIN_EVENTS_PER_FOLD} events per fold, so adversarial validation and section 9 were skipped.",
            "collect more positives or use a simpler analysis",
        )
    if "other_outcome" in ctx.store:
        rep.finding(
            "info",
            f"`{ctx.store['other_outcome']}` (the documented outcome) was removed from the features",
            f"You are analysing `{ctx.target}`; the documented outcome is another result of the same process, not an input.",
            "",
        )

    # ---- contract checks ------------------------------------------------------------------------------------------
    rep.finding(
        "ok" if same and n_files > 1 else "warning",
        f"{n_files} files share one schema" if same else "Files have different schemas",
        f"{rows_total:,} rows in total.",
        "" if same else "align the columns before pooling",
    )
    if n_missing == 0:
        rep.finding(
            "ok",
            "No missing values",
            "Every cell of every column is filled.",
            "Do not trust it blindly: see section 2 for sentinel values that may be hiding missingness.",
        )
    else:
        worst = df[feats].isna().mean().sort_values(ascending=False).head(3)
        rep.finding(
            "warning",
            f"{n_missing:,} missing cells",
            ", ".join(f"{c} ({pct(v)})" for c, v in worst.items()),
            "impute inside the CV loop, add missing-flags",
        )
    id_like = [
        c for c in feats if ID_LIKE.search(c) or (df[c].nunique() == ctx.n and pd.api.types.is_integer_dtype(df[c]))
    ]
    if not id_like:
        rep.finding(
            "info",
            "No ID and no date column",
            "One row is one loan snapshot, but we cannot see whether a borrower appears twice, and there is no time axis.",
            "No out-of-time validation is possible; use stratified CV and ask the organisers how borrowers / dates were handled.",
        )
    float_codes = [
        c for c in feats if ctx.kinds[c] in ("binary", "nominal", "ordinal") and pd.api.types.is_float_dtype(df[c])
    ]
    if float_codes:
        rep.finding(
            "warning",
            f"{len(float_codes)} code columns are stored as floats",
            ", ".join(float_codes[:6]) + (" …" if len(float_codes) > 6 else ""),
            "cast to category / int so no model reads code 12.0 as 'twelve'",
        )
    if ctx.defs_ka:
        missing_def = [c for c in [*feats, ctx.target] if c not in ctx.defs_ka and c in FEATURES]
        extra_def = [c for c in ctx.defs_ka if c not in df.columns]
        rep.finding(
            "ok" if not (missing_def or extra_def) else "warning",
            "Data columns match the dictionary" if not (missing_def or extra_def) else "Data and dictionary disagree",
            f"{len(ctx.defs_ka)} documented variables."
            if not (missing_def or extra_def)
            else f"undocumented: {missing_def}; documented but absent: {extra_def}",
        )

    # ---- the column catalog ---------------------------------------------------------------------------------------
    rows = []
    for c in [ctx.target, *feats] if ctx.target in FEATURES else feats:
        role = "target" if c == ctx.target else ctx.roles[c]
        rows.append(
            {
                "column": c,
                "family": FAMILIES[family_of(c)][0],
                "kind": ctx.kinds.get(c, FEATURES.get(c, ("", "", ""))[1]),
                "dtype": str(df[c].dtype),
                "unique": int(df[c].nunique()),
                "role (hypothesis)": ROLES[role][0],
                "meaning": describe(c),
                "where the code numbers come from": CODE_MAPPING_STATUS.get(c, ""),
                "dictionary (Georgian)": ctx.defs_ka.get(c, "")[:200]
                + ("…" if len(ctx.defs_ka.get(c, "")) > 200 else ""),
            }
        )
    catalog = pd.DataFrame(rows).set_index("column")
    role_legend = pd.DataFrame(
        [
            {"role": ROLES[k][0], "what it means": ROLES[k][1], "columns": sum(ctx.roles[c] == k for c in feats)}
            for k in ROLES
            if k != "target" and any(ctx.roles[c] == k for c in feats)
        ]
    ).set_index("role")
    rep.table(
        role_legend,
        "role_legend",
        "The roles used throughout this report",
        "A role says how a column may be used when modeling. Roles are hypotheses from the dictionary; section 8 tests them against the data.",
        console=0,
    )
    rep.table(
        catalog,
        "column_catalog",
        "Column catalog (English glossary + Georgian original)",
        "The English text is a translation of the Georgian dictionary; wording such as 'assumed' or 'not stated' marks what the dictionary does not say. The role is a hypothesis; section 8 tests it.",
        max_rows=70,
    )

    # ---- chart: what kind of information do we have? ---------------------------------------------------------------
    fam_order = [k for k in FAMILIES if any(family_of(c) == k for c in feats)]
    role_order = [r for r in ROLE_COLORS if r != "target"]
    counts = pd.crosstab(
        pd.Series([family_of(c) for c in feats], name="family"), pd.Series([ctx.roles[c] for c in feats], name="role")
    )
    counts = counts.reindex(index=fam_order, columns=role_order, fill_value=0)
    fig, ax = plt.subplots(figsize=(8.6, 0.46 * len(counts) + 1.3))
    left = np.zeros(len(counts))
    for role in role_order:
        v = counts[role].to_numpy()
        if v.sum():
            ax.barh(
                range(len(counts)),
                v,
                left=left,
                color=ROLE_COLORS[role],
                edgecolor=SURFACE,
                linewidth=1.5,
                height=0.64,
                label=ROLES[role][0],
            )
            left += v
    ax.set_yticks(range(len(counts)), [FAMILIES[f][0] for f in counts.index])
    ax.invert_yaxis()
    for i, tot in enumerate(left):
        ax.text(tot + 0.15, i, f"{int(tot)}", va="center", color=INK2, fontsize=9)
    ax.set_xlabel("number of columns")
    ax.grid(axis="y", visible=False)
    legend_top(ax, ncol=3)
    n_after = sum(
        ctx.roles[c] in ("tier0_target_defining", "incumbent_model_output", "post_origination_state") for c in feats
    )
    rep.figure(
        fig,
        "feature_families",
        "What kind of information do the columns carry?",
        "One bar per family of columns (length = how many columns); colour = the role we expect that column to play when modeling.",
        f"About **{n_after} of {len(feats)}** columns are probably measured at the snapshot, not at application (delinquency, balances, schedule, loan events) or are the supplied score; "
        f"only **{len(feats) - n_after}** are plausibly known when the loan is granted - and even those carry no dates.",
    )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 2 · Data-quality scorecard
# ══════════════════════════════════════════════════════════════════════════════════
# Relationships the data dictionary promises.  (label, needed columns, applies-to mask or None,
# violation mask, why it must hold).  Each is evaluated only if its columns exist.
# rule label -> (a, b, tolerance) for every 'a <= b' rule (used by the generator test)
ORDER_PAIRS: dict[str, tuple[str, str, float]] = {}


def _le(a: str, b: str, why: str):
    """Rule 'a <= b'.  Two money columns get a tolerance of MATERIAL (1 unit) because sub-unit 'dust' is not an accounting breach."""
    tol = (
        MATERIAL if FEATURES.get(a, ("", ""))[1] == "amount" and FEATURES.get(b, ("", ""))[1] == "amount" else ORDER_TOL
    )
    ORDER_PAIRS[f"{a} <= {b}"] = (a, b, tol)
    return (f"{a} <= {b}", (a, b), None, lambda d: d[a] > d[b] + tol, why)


IDENTITY_RULES = [
    ("overdueAmount = OverduePrincipalAm + OverdueInterestCommission", ("overdueAmount", "OverduePrincipalAm", "OverdueInterestCommission"), None,
     lambda d: (d["overdueAmount"] - d["OverduePrincipalAm"] - d["OverdueInterestCommission"]).abs() > MATERIAL, "the dictionary defines it as that sum (within 1 currency unit)"),
    _le("last1Month", "last3Month", "a 1-month inquiry window lies inside the 3-month window"),
    _le("last3Month", "last6Month", "a 3-month window lies inside the 6-month window"),
    _le("last6Month", "last12Month", "a 6-month window lies inside the 12-month window"),
    _le("daysInDeliquency", "MaximumNumberDelinquencyDays", "the latest delinquency cannot be longer than the longest one"),
    _le("MaximumNumberDelinquencyDays", "TotalNumberDelinquencyDays", "the longest spell cannot exceed the total overdue days"),
    _le("TotalNumberDelinquencyDaysLastYear", "TotalNumberDelinquencyDays", "last-year days are part of the lifetime total"),
    _le("overdueInstalmentsCount", "TotalNumberOverdueInst", "overdue instalments are a subset of all instalments ever late"),
    _le("penaltyPaid", "penaltySum", "you cannot pay more penalty than was accrued"),
    _le("OutstandingPrincipalAm", "DISBURSED_AMOUNT", "principal still owed cannot exceed what was lent"),
    _le("OutstandingPrincipalAm", "outstandingAmount", "balance = principal + interest, so it is at least the principal"),
    _le("outstandingAmount", "TotalOutstandingAmount", "the borrower's total over all loans includes this loan"),
    _le("overdueAmount", "TotalDebtOverdue", "the borrower's total overdue includes this loan's overdue"),
    _le("standardInstalmentAmount", "MaximumAmountOfInstalmentBySchedule", "the standard instalment cannot exceed the largest one"),
    ("material overdue money but zero overdue instalments", ("overdueAmount", "overdueInstalmentsCount"), None,
     lambda d: (d["overdueAmount"] >= MATERIAL) & (d["overdueInstalmentsCount"] == 0), "material overdue money (>= 1 unit) must belong to at least one instalment"),
    ("monthly loans: ContractTermMonth = standardInstalmentCount (+-1)", ("ContractTermMonth", "standardInstalmentCount", "PRINCIPAL_PAYMENT_FREQUENCY"),
     lambda d: d["PRINCIPAL_PAYMENT_FREQUENCY"] == 2, lambda d: (d["ContractTermMonth"] - d["standardInstalmentCount"]).abs() > 1, "one instalment per month"),
    ("RemainingTerm (days) <= whole contract", ("RemainingTerm", "ContractTermMonth"), None,
     lambda d: d["RemainingTerm"] > 31 * (d["ContractTermMonth"] + 1), "days left cannot exceed the contract length"),
]  # fmt: skip

MORTGAGE_PURPOSES = (7, 10, 13)  # PRODUCT_PURPOSE codes whose dictionary name is a mortgage
CONSUMER_PURPOSES = (2, 3, 4, 5, 6)  # consumer loan, payday, instant instalment, overdraft, credit card
# Category agreements worth a look.  They are reported as COUNTS, never as errors: some can be legitimate (secured consumer loans,
# micro-business consumption).  (label, needed columns, flag, why it is worth a look)
CATEGORY_RULES = [
    ("disbursed amount is 0 but a balance of at least 1 unit is still owed", ("DISBURSED_AMOUNT", "OutstandingPrincipalAm"), lambda d: (d["DISBURSED_AMOUNT"] == 0) & (d["OutstandingPrincipalAm"] >= MATERIAL), "a booked loan has a positive disbursed amount"),
    ("disbursed amount is 0", ("DISBURSED_AMOUNT",), lambda d: d["DISBURSED_AMOUNT"] == 0, "zero-inflation hides it: normal for overdrafts and cards, odd for term loans"),
    ("loanType = ConsumerLoan but the product purpose is a mortgage", ("loanType", "PRODUCT_PURPOSE"), lambda d: (d["loanType"] == "ConsumerLoan") & d["PRODUCT_PURPOSE"].isin(MORTGAGE_PURPOSES), "two columns describe the same contract differently"),
    ("loanType = MortgageLoan but the product purpose is not a mortgage", ("loanType", "PRODUCT_PURPOSE"), lambda d: (d["loanType"] == "MortgageLoan") & ~d["PRODUCT_PURPOSE"].isin(MORTGAGE_PURPOSES), "two columns describe the same contract differently"),
    ("consumer-type product purpose in the corporate / SME / micro segment", ("PRODUCT_PURPOSE", "DEBT_SEGMENT"), lambda d: d["PRODUCT_PURPOSE"].isin(CONSUMER_PURPOSES) & d["DEBT_SEGMENT"].isin([1, 2, 3]), "can be legitimate (micro-business consumption)"),
    ("private individual (legal form 7) in the Business loan segment", ("CLIENT_LEGAL_FORM", "LoanSegment"), lambda d: (d["CLIENT_LEGAL_FORM"] == 7) & (d["LoanSegment"] == "Business"), "can be legitimate (self-employed)"),
    ("collateral amount above 0 but LTV_RATIO = 0", ("guaranteeAmount", "LTV_RATIO"), lambda d: (d["guaranteeAmount"] > 0) & (d["LTV_RATIO"] == 0), "so LTV = 0 does not mean 'unsecured'"),
]  # fmt: skip

# Pairs that should be strongly related if the columns mean what the dictionary says.
RELATIONS = [
    ("PTI_RATIO ~ AVERAGE_MONTHLY_PAYMENT / INCOME_AMOUNT_TOTAL", ("PTI_RATIO", "AVERAGE_MONTHLY_PAYMENT", "INCOME_AMOUNT_TOTAL"),
     lambda d: d["PTI_RATIO"], lambda d: d["AVERAGE_MONTHLY_PAYMENT"] / d["INCOME_AMOUNT_TOTAL"].replace(0, np.nan)),
    ("DISBURSED_AMOUNT ~ OutstandingPrincipalAm", ("DISBURSED_AMOUNT", "OutstandingPrincipalAm"), lambda d: d["DISBURSED_AMOUNT"], lambda d: d["OutstandingPrincipalAm"]),
    ("OutstandingPrincipalAm ~ outstandingAmount", ("OutstandingPrincipalAm", "outstandingAmount"), lambda d: d["OutstandingPrincipalAm"], lambda d: d["outstandingAmount"]),
    ("ContractTermMonth ~ standardInstalmentCount", ("ContractTermMonth", "standardInstalmentCount"), lambda d: d["ContractTermMonth"], lambda d: d["standardInstalmentCount"]),
    ("ContractTermMonth ~ RemainingTerm", ("ContractTermMonth", "RemainingTerm"), lambda d: d["ContractTermMonth"], lambda d: d["RemainingTerm"]),
    ("AVERAGE_MONTHLY_PAYMENT ~ standardInstalmentAmount", ("AVERAGE_MONTHLY_PAYMENT", "standardInstalmentAmount"), lambda d: d["AVERAGE_MONTHLY_PAYMENT"], lambda d: d["standardInstalmentAmount"]),
    ("LTV_RATIO ~ OutstandingPrincipalAm / guaranteeAmount", ("LTV_RATIO", "OutstandingPrincipalAm", "guaranteeAmount"),
     lambda d: d["LTV_RATIO"], lambda d: d["OutstandingPrincipalAm"] / d["guaranteeAmount"].replace(0, np.nan)),
    ("TotalNumberDelinquencyDays ~ MaximumNumberDelinquencyDays", ("TotalNumberDelinquencyDays", "MaximumNumberDelinquencyDays"), lambda d: d["TotalNumberDelinquencyDays"], lambda d: d["MaximumNumberDelinquencyDays"]),
]  # fmt: skip


def find_pileups(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    """Values that hold an abnormal share of a continuous-looking column: zero-inflation,
    default / sentinel values, and caps / floors (censoring)."""
    rows = []
    for c in cols:
        s = df[c]
        vc = s.value_counts()
        v, n = vc.index[0], int(vc.iloc[0])
        # integer columns have natural modes (48-month terms, 2 contracts) - not defects
        integral = bool((s % 1 == 0).all())
        if n / len(s) >= SPIKE_SHARE and (v == 0 or v in (s.max(), s.min()) or not integral):
            kind = (
                "zero-inflated"
                if v == 0
                else "capped at max"
                if v == s.max()
                else "floored at min"
                if v == s.min()
                else "default / sentinel value"
            )
            rows.append({"column": c, "value": float(v), "rows": n, "share": n / len(s), "pattern": kind})
        uniq = np.sort(s.unique())
        for edge, nb, tag in (
            (s.max(), uniq[-2] if len(uniq) > 1 else None, "capped at max"),
            (s.min(), uniq[1] if len(uniq) > 1 else None, "floored at min"),
        ):
            n_edge = int((s == edge).sum())
            n_nb = int((s == nb).sum()) if nb is not None else 0
            already = any(r["column"] == c and r["value"] == float(edge) for r in rows)
            if not already and n_edge >= max(20, 0.001 * len(s)) and n_edge >= 5 * max(n_nb, 1):
                tag = "zero-inflated" if edge == 0 else tag  # a floor at exactly 0 is a structural zero, not censoring
                rows.append(
                    {"column": c, "value": float(edge), "rows": n_edge, "share": n_edge / len(s), "pattern": tag}
                )
    return (
        pd.DataFrame(rows, columns=["column", "value", "rows", "share", "pattern"])
        .sort_values("share", ascending=False)
        .reset_index(drop=True)
    )


@register(2, "Data-quality scorecard", "Can I trust these numbers - and where are the traps?")
def section_quality(ctx: Ctx) -> None:
    """Can the numbers be trusted?  2a-2b look for rule breaks, caps, spikes and rare levels; 2c-2e print the scorecard, category agreements
    and dictionary relationships; 2f-2h test whether the broken rules look generated, find 'dust' and ratio scales; 2i lists spikes and caps."""
    rep, df, feats = ctx.rep, ctx.df, ctx.features
    num_cols = [c for c in feats if is_num(df[c])]
    cont_cols = [c for c in num_cols if df[c].nunique() > MAX_LEVELS_AS_CATEGORY]

    # ── 2a. the dictionary promises relationships between columns - do they hold? ────────────────────────────────────
    rules = []
    for label, need, applies, bad, why in IDENTITY_RULES:
        if all(c in df.columns for c in need):
            mask = applies(df) if applies else pd.Series(True, index=df.index)
            n_app = int(mask.sum())
            n_bad = int((bad(df) & mask).sum())
            rules.append(
                {
                    "relationship that must hold": label,
                    "rows it applies to": n_app,
                    "rows violating": n_bad,
                    "share violating": n_bad / max(n_app, 1),
                    "why it must hold": why,
                }
            )
    rule_df = (
        pd.DataFrame(rules).sort_values("share violating", ascending=False).set_index("relationship that must hold")
        if rules
        else pd.DataFrame()
    )
    n_bad_rules = int((rule_df["share violating"] > RULE_TOL).sum()) if len(rule_df) else 0

    rels = []
    for label, need, fa, fb in RELATIONS:
        if all(c in df.columns for c in need):
            a, b = fa(df), fb(df)
            ok = a.notna() & b.notna() & np.isfinite(a) & np.isfinite(b)
            rho = float(stats.spearmanr(a[ok], b[ok])[0])
            rels.append(
                {
                    "pair that should move together": label,
                    "Spearman rho": rho,
                    "verdict": "strong"
                    if abs(rho) >= 0.8
                    else "moderate"
                    if abs(rho) >= 0.5
                    else "weak - dictionary logic not visible",
                }
            )
    rel_df = pd.DataFrame(rels).set_index("pair that should move together") if rels else pd.DataFrame()

    # ── 2b. pile-ups, caps, ranges, rare levels ─────────────────────────────────────────────────────────────────────
    piles = find_pileups(df, [c for c in cont_cols if ctx.kinds[c] not in ("binary", "nominal")])
    neg = [c for c in num_cols if ctx.kinds[c] in ("amount", "count", "days") and (df[c] < 0).any()]
    odd = []
    if "probabilityOfDefault" in df.columns and (df["probabilityOfDefault"] > 100).any():
        odd.append("probabilityOfDefault > 100")
    if "Age" in df.columns and (df["Age"] < 18).any():
        odd.append("Age < 18")
    rare_rows = []
    for c in feats:
        if ctx.plot_as[c] == "categorical":
            vc = df[c].value_counts()
            for lvl, n in vc[vc < 20].items():
                rare_rows.append(
                    {
                        "column": c,
                        "level": label_of(c, lvl),
                        "rows": int(n),
                        "event rate": float(ctx.y[(df[c] == lvl).to_numpy()].mean()),
                    }
                )
    rare = pd.DataFrame(rare_rows)
    censored = piles[piles["pattern"].isin(["capped at max", "floored at min"])]
    sentinels = piles[piles["pattern"] == "default / sentinel value"]
    zeros = piles[piles["pattern"] == "zero-inflated"]
    consts = [c for c in feats if df[c].nunique() <= 1]
    near_const = [c for c in feats if df[c].nunique() > 1 and df[c].value_counts(normalize=True).iloc[0] >= 0.99]

    # ── 2c. category agreements (counts, never verdicts) ───────────────────────────────────────────────────────────────────
    cat_rows = []
    for label, need, flag, why in CATEGORY_RULES:
        if all(c in df.columns for c in need):
            n_flag = int(flag(df).sum())
            cat_rows.append(
                {
                    "check": label,
                    "rows flagged": n_flag,
                    "share of rows": n_flag / ctx.n,
                    "event rate there": float(ctx.y[flag(df).to_numpy()].mean()) if n_flag >= 20 else np.nan,
                    "why it is worth a look": why,
                }
            )
    n_cat_flags = sum(r["share of rows"] >= CAT_NOTE_SHARE for r in cat_rows)

    # ── 2d. scorecard ───────────────────────────────────────────────────────────────────────────────────────────────────
    n_dup = int(df.duplicated(subset=feats).sum())
    n_na = int(df[feats].isna().sum().sum())
    ctx.store["quality"] = {
        "n_bad_rules": n_bad_rules,
        "n_rules": len(rule_df),
        "worst_rule": rule_df.index[0] if len(rule_df) else "",
        "worst_share": float(rule_df["share violating"].iloc[0]) if len(rule_df) else 0.0,
        "censored": censored,
        "sentinels": sentinels,
        "zeros": zeros,
        "n_dup": n_dup,
        "n_na": n_na,
        "rare": rare,
    }
    card = pd.DataFrame(
        [
            ("Missing values", f"{n_na:,} cells", "pass" if n_na == 0 else "fail"),
            ("Duplicate rows", f"{n_dup:,} rows", "pass" if n_dup == 0 else "fail"),
            ("Constant columns", ", ".join(consts) or "none", "pass" if not consts else "warn"),
            (
                "Near-constant columns (>= 99% one value)",
                ", ".join(near_const) or "none",
                "pass" if not near_const else "warn",
            ),
            (
                "Negative amounts or counts, PD > 100, Age < 18",
                ", ".join([*neg, *odd]) or "none",
                "pass" if not (neg or odd) else "fail",
            ),
            (
                "Category agreement (see the table below)",
                f"{n_cat_flags} of {len(cat_rows)} checks flag at least {pct(CAT_NOTE_SHARE, 0)} of the rows"
                if cat_rows
                else "not checked",
                "note" if cat_rows else "pass",
            ),
            (
                "Caps / floors (censored values)",
                ", ".join(f"{r.column} @ {num(r.value)}" for r in censored.itertuples()) or "none",
                "pass" if censored.empty else "warn",
            ),
            (
                "Default / sentinel values (non-zero)",
                ", ".join(f"{r.column} = {num(r.value)}" for r in sentinels.itertuples()) or "none",
                "pass" if sentinels.empty else "warn",
            ),
            (
                "Zero-inflated continuous columns",
                f"{len(zeros)} of {len(cont_cols)} (normal for counts / days / amounts)",
                "note" if len(zeros) else "pass",
            ),
            (
                "Categorical levels with < 20 rows",
                (
                    f"{len(rare):,} levels in "
                    + ", ".join(f"{c} ({n:,})" for c, n in rare["column"].value_counts().head(3).items())
                )
                if len(rare)
                else "none",
                "pass" if rare.empty else "warn",
            ),
            (
                f"Dictionary relationships violated in > {pct(RULE_TOL, 0)} of rows",
                f"{n_bad_rules} of {len(rule_df)}",
                "pass" if n_bad_rules == 0 else "fail",
            ),
        ],
        columns=["check", "result", "status"],
    ).set_index("check")
    badge = {"pass": "✔ pass", "note": "ℹ note", "warn": "▲ warn", "fail": "✖ fail"}
    rep.table(
        card,
        "quality_scorecard",
        "Data-quality scorecard",
        "pass = nothing to do · note = normal, just be aware · warn = handle it in preprocessing · fail = changes how you can use the column",
        fmt={"status": lambda v: badge[v]},
        console=10,
    )

    if cat_rows:
        rep.table(
            pd.DataFrame(cat_rows).set_index("check"),
            "category_agreement",
            "Do the categories agree with each other?  (counts, not verdicts)",
            "Some of these can be legitimate (secured consumer loans, micro-business consumption); they are listed so that you decide how to treat them.",
            fmt={
                "rows flagged": "{:,}",
                "share of rows": lambda v: pct(v, 1),
                "event rate there": lambda v: "–" if pd.isna(v) else pct(v),
            },
            bars={"share of rows": (0, 0.25)},
            console=0,
        )

    # ── 2e-2f. is the data synthetic?  (advanced: dictionary relationships and the generator test) ──────────────────
    with rep.group("Is the data synthetic?  (advanced: the dictionary relationships and the generator test)"):
        if len(rule_df):
            rep.text(
                "### Do the columns obey the dictionary?\n\n"
                "A column's *definition* often implies arithmetic between columns (a sum, a ≤ ordering). If real accounting produced the data, those "
                "relationships hold in ~100% of rows. Here they do not:",
                console=False,
            )
            strict_id = (
                float(
                    (
                        (df["overdueAmount"] - df["OverduePrincipalAm"] - df["OverdueInterestCommission"]).abs() > 0.01
                    ).mean()
                )
                if {"overdueAmount", "OverduePrincipalAm", "OverdueInterestCommission"} <= set(df.columns)
                else None
            )
            strict_note = (
                f" At cent precision (|difference| > 0.01) the overdue-amount identity would fail in {pct(strict_id, 0)} of rows; that is mostly 'dust' (see below), which is why money rules get a tolerance."
                if strict_id is not None
                else ""
            )
            rep.table(
                rule_df,
                "dictionary_identities",
                "Relationships the dictionary promises",
                f"Money rules are judged with a tolerance of {MATERIAL:g} currency unit, 'overdue money' means at least {MATERIAL:g} unit, and an 'a ≤ b' rule between two money columns is broken only when a exceeds b by more than that tolerance.{strict_note}",
                fmt={"share violating": lambda v: pct(v), "rows it applies to": "{:,}", "rows violating": "{:,}"},
                bars={"share violating": (0, 1)},
                max_rows=20,
                console=0,
            )
            worst = rule_df.index[0]
            fig, ax = plt.subplots(figsize=(9.2, 0.36 * len(rule_df) + 1.4))
            shares = rule_df["share violating"].to_numpy()
            colors = [INK2 if s > RULE_TOL else NEUTRAL_LIGHT for s in shares]
            ax.barh(range(len(rule_df)), shares * 100, color=colors, height=0.62)
            ax.axvline(RULE_TOL * 100, color=INK, lw=1, ls="--")
            ax.set_yticks(range(len(rule_df)), [i if len(i) < 62 else i[:59] + "…" for i in rule_df.index], fontsize=8)
            ax.invert_yaxis()
            for i, s in enumerate(shares):
                # never on top of the dashed tolerance line
                ax.text(max(s * 100, RULE_TOL * 100) + 1.0, i, f"{s * 100:.1f}%", va="center", fontsize=8, color=INK2)
            ax.set_xlabel("share of rows that break the relationship (%)")
            ax.grid(axis="y", visible=False)
            legend_top(
                ax,
                ncol=3,
                handles=[
                    Patch(facecolor=INK2, label=f"breaks it in > {pct(RULE_TOL, 0)} of rows"),
                    Patch(facecolor=NEUTRAL_LIGHT, label="within tolerance"),
                    Line2D([], [], color=INK, ls="--", lw=1, label=f"{pct(RULE_TOL, 0)} tolerance"),
                ],
            )
            rep.figure(
                fig,
                "identity_violations",
                "Dictionary relationships that do not hold",
                f"One bar per relationship the dictionary implies; length = % of rows that violate it. Dark bars = more than {pct(RULE_TOL, 0)} of rows.",
                f"**{n_bad_rules} of {len(rule_df)}** relationships are violated in more than {pct(RULE_TOL, 0)} of rows; the worst is `{worst}` ({pct(shares[0])}).",
            )
            if n_bad_rules >= MIN_BAD_RULES:
                rep.finding(
                    "warning",
                    "Columns do not obey their own definitions",
                    f"{n_bad_rules} of {len(rule_df)} dictionary relationships fail in more than {pct(RULE_TOL, 0)} of rows (worst: {worst}, {pct(shares[0])}). Arithmetic between columns "
                    f"cannot be trusted (see the generator test below for what this pattern means).",
                    "Keep the supplied ratios (PTI, LTV) as they are and do not rebuild them from components; validate every engineered ratio on its own.",
                    priority=2,
                )
        if len(rel_df):
            rep.table(
                rel_df,
                "dictionary_relations",
                "Pairs that should move together",
                "Rank correlation (Spearman) between columns that the dictionary says are tightly linked. A weak link is not proof of a broken column: PTI, for instance, may use all of the borrower's payments rather than this loan's payment.",
                fmt={"Spearman rho": "{:+.2f}"},
                bars={"Spearman rho": (0, 1)},
            )
            weak = rel_df[rel_df["Spearman rho"].abs() < 0.5]
            if len(weak):
                rep.finding(
                    "info",
                    f"{len(weak)} of {len(rel_df)} 'should be linked' pairs are only weakly correlated",
                    "; ".join(f"{i} (rho {v:+.2f})" for i, v in weak["Spearman rho"].items()),
                    "do not assume the business logic behind these columns is intact",
                )

        # ── 2f. are the broken rules 'smooth'?  what would a simple statistical generator give? ─────────────────────────────────
        with rep.attempt("Generator test"):
            rows_g = []
            rng_g = np.random.default_rng(SEED)
            for lab in rule_df.index:
                if lab not in ORDER_PAIRS:
                    continue
                a_, b_, tol_ = ORDER_PAIRS[lab]
                a_v, b_v = df[a_].to_numpy(float), df[b_].to_numpy(float)
                rows_g.append(
                    {
                        "rule": lab,
                        "observed": float(np.mean(a_v > b_v + tol_)),
                        "if columns were independent": float(np.mean(a_v > rng_g.permutation(b_v) + tol_)),
                        "if only distributions + rank correlation were known": copula_violation(a_v, b_v, tol=tol_),
                    }
                )
            gdf = pd.DataFrame(rows_g).set_index("rule")
            c_col, i_col = "if only distributions + rank correlation were known", "if columns were independent"
            mae_c = float((gdf["observed"] - gdf[c_col]).abs().mean())
            mae_i = float((gdf["observed"] - gdf[i_col]).abs().mean())
            worst_r = (gdf["observed"] - gdf[c_col]).abs().idxmax()
            rep.table(
                gdf,
                "generator_test",
                "Generator test: observed vs predicted violation rates",
                "For each 'a <= b' rule: the observed violation rate vs. what you would see if (1) the two columns were independent, or (2) the data were generated knowing only each column's distribution and the rank correlation between them (a Gaussian copula).",
                fmt={c: (lambda v: pct(v)) for c in gdf.columns},
                bars={c: (0, 0.5) for c in gdf.columns},
                max_rows=15,
            )
            top = max(60.0, float(gdf.max().max()) * 100 + 3)
            fig, ax = plt.subplots(figsize=(6.2, 6.0))
            ax.plot([-1, top], [-1, top], color=MUTED, ls="--", lw=1)
            ax.scatter(
                gdf["observed"] * 100, gdf[i_col] * 100, facecolors="none", edgecolors=NEUTRAL, s=48, linewidth=1.5
            )
            ax.scatter(gdf["observed"] * 100, gdf[c_col] * 100, color=INK, s=34, edgecolor=SURFACE, linewidth=0.8)
            ax.set_xlim(-1, top)
            ax.set_ylim(-1, top)
            ax.set_xlabel("observed violation rate (%)")
            ax.set_ylabel("violation rate predicted by the simple model (%)")
            legend_top(
                ax,
                ncol=1,
                handles=[
                    Line2D([], [], color=MUTED, ls="--", lw=1, label="perfect prediction"),
                    Line2D(
                        [],
                        [],
                        marker="o",
                        ls="",
                        markerfacecolor="none",
                        markeredgecolor=NEUTRAL,
                        label="model: columns are independent",
                    ),
                    Line2D(
                        [],
                        [],
                        marker="o",
                        ls="",
                        color=INK,
                        markersize=5.5,
                        label="model: distributions + rank correlation only",
                    ),
                ],
            )
            ax.set_aspect("equal", adjustable="box")
            rep.figure(
                fig,
                "generator_test",
                "Are the broken rules statistically smooth?",
                "One dot per rule (all rules shown). A model that explains the data puts its dots on the dashed diagonal.",
                f"Over all {len(gdf)} rules, a model that knows only each column's distribution and the rank correlations is off by **{mae_c * 100:.1f} percentage points** on average (worst: `{worst_r}`); assuming independent columns is off by **{mae_i * 100:.1f}**.",
            )
            ctx.store["generator"] = {"mae_copula": mae_c, "mae_indep": mae_i, "n_rules": len(gdf)}
            if mae_c < 0.5 * mae_i and mae_c < 0.03:
                rep.finding(
                    "warning",
                    "The broken rules look like the output of a simple statistical generator",
                    f"Across {len(gdf)} rules a model that knows only column distributions and rank correlations is off by {mae_c * 100:.1f} pp (independence: {mae_i * 100:.1f} pp). A tabular generator (copula / GAN) or heavy independent noise on each column would do exactly this; the data cannot tell which, and real extracts also break some identities (timing, FX, rounding).",
                    "Model the columns as given; expect relationships between columns to be statistical, not accounting identities; ask the organisers how the data were produced.",
                    priority=2,
                )

    # ── 2g. structural zeros smeared into tiny positive values ('dust') ──────────────────────────────────────────────────
    money = [c for c in num_cols if ctx.kinds[c] == "amount" and df[c].min() >= 0]
    dust_rows = []
    for c in money:
        v = df[c].to_numpy(float)
        zone = {
            "exactly 0": v == 0,
            "dust (0, 1)": (v > 0) & (v < 1),
            "small [1, 10)": (v >= 1) & (v < 10),
            "material >= 10": v >= 10,
        }
        if zone["dust (0, 1)"].mean() >= 0.05:
            r = {"column": c}
            for name, m in zone.items():
                r[f"% rows: {name}"] = float(m.mean())
            for name in ("exactly 0", "dust (0, 1)", "material >= 10"):
                r[f"event rate: {name}"] = float(ctx.y[zone[name]].mean()) if zone[name].sum() > 20 else np.nan
            dust_rows.append(r)
    if dust_rows:
        ddf = pd.DataFrame(dust_rows).set_index("column").sort_values("% rows: dust (0, 1)", ascending=False)
        rep.table(
            ddf,
            "dust_values",
            "Structural zeros smeared into tiny positive values ('dust')",
            "A bank normally stores 0 for 'nothing overdue', not 0.37. These columns hold a large mass of values below 1 currency unit, so `column > 0` is true almost everywhere and tells you little; compare the event rates across the zones to find the cut that matters.",
            fmt={c: (lambda v: "–" if pd.isna(v) else pct(v)) for c in ddf.columns},
            bars={"% rows: dust (0, 1)": (0, 1)},
            console=0,
        )
        like_zero = unlike = 0
        for c in ddf.index:
            v = df[c].to_numpy(float)
            z_, d_ = v == 0, (v > 0) & (v < 1)
            if z_.sum() >= 20 and d_.sum() >= 20:
                p_ = float(
                    stats.chi2_contingency(
                        np.array([[ctx.y[z_].sum(), (1 - ctx.y[z_]).sum()], [ctx.y[d_].sum(), (1 - ctx.y[d_]).sum()]])
                        + 0.5
                    )[1]
                )
                like_zero += p_ >= 0.01
                unlike += p_ < 0.01
        rep.finding(
            "info",
            f"{len(ddf)} money columns hold many values below 1 currency unit ('dust')",
            ", ".join(f"{c} ({pct(v, 0)})" for c, v in ddf["% rows: dust (0, 1)"].head(4).items())
            + f". In {like_zero} of them the event rate of the dust matches that of exact zeros; in {unlike} it differs (chi-square p < 0.01).",
            "never use `> 0` as a flag on these; use a materiality cut (the table shows where the event rate jumps) or a rank / log transform",
        )

    # ── 2h. ratio columns: what scale are they on? ───────────────────────────────────────────────────────────────────────────
    ratio_rows, scales = [], {}
    for c in [c for c in num_cols if ctx.kinds[c] == "ratio"]:
        v_all = df[c].to_numpy(float)
        v = v_all[v_all > 0]
        if len(v) < 100:
            continue
        hi_share, trough = float((v > 5).mean()), float(((v > 2) & (v <= 10)).mean())
        two_regimes = hi_share >= 0.05 and trough < 0.02
        lo_med, hi_med = (
            float(np.median(v[v <= 5])) if (v <= 5).any() else float("nan"),
            float(np.median(v[v > 5])) if (v > 5).any() else float("nan"),
        )
        ev_lo = float(ctx.y[(v_all > 0) & (v_all <= 5)].mean()) if ((v_all > 0) & (v_all <= 5)).any() else float("nan")
        ev_hi = float(ctx.y[v_all > 5].mean()) if (v_all > 5).any() else float("nan")
        if two_regimes:
            agree = abs(hi_med / 100 / lo_med - 1) <= 0.15 if lo_med > 0 else False
            verdict = (
                f"two regimes ({pct(hi_share, 0)} of positive values above 5; ÷100 gives a median of {hi_med / 100:.2f} against {lo_med:.2f}): "
                + (
                    "looks like a unit mix - keep the supplied value and add an is-high-regime flag"
                    if agree
                    else f"the regimes also differ in event rate ({pct(ev_hi, 1)} vs {pct(ev_lo, 1)}), so it is more than a unit mix - keep the supplied value and add an is-high-regime flag"
                )
            )
        else:
            verdict = f"one scale: median {np.median(v):.2f}, 90th percentile {np.quantile(v, 0.9):.1f}" + (
                f"; {pct(float((v > 1).mean()), 0)} of positive values exceed 1, so it is not a plain fraction"
                if (v > 1).mean() > 0.2
                else ""
            )
        scales[c] = {"verdict": verdict, "two_regimes": two_regimes}
        ratio_rows.append(
            {
                "column": c,
                "% rows = 0": float((v_all == 0).mean()),
                "median (positive)": float(np.median(v)),
                "p90": float(np.quantile(v, 0.9)),
                "% above 1": float((v > 1).mean()),
                "% above 5": hi_share,
                "reading": verdict,
            }
        )
        if two_regimes:
            fig, ax = plt.subplots(figsize=(8.4, 3.2))
            ax.hist(np.log10(v), bins=70, color=NEUTRAL_LIGHT)
            ax.axvline(np.log10(5), color=INK, lw=1.4)
            ticks = [0.01, 0.1, 1, 10, 100]
            ax.set_xticks(np.log10(ticks), [str(t) for t in ticks])
            ax.set_xlabel(f"{c} (log scale; dark line = 5)")
            ax.set_ylabel("loans")
            ax.set_yticks([])
            rep.figure(
                fig,
                f"ratio_regimes_{c}",
                f"`{c}` has two regimes",
                "Histogram of the column on a log scale (positive values only). Two separate humps mean two scales (or two populations) were mixed.",
                f"{pct(hi_share, 0)} of the positive values are above 5 while only {pct(trough, 1)} sit between 2 and 10: a fraction scale (median {lo_med:.2f}) and a percent-like scale (median {hi_med:.1f}; ÷100 = {hi_med / 100:.2f}). Event rate: {pct(ev_hi, 1)} in the high regime vs {pct(ev_lo, 1)} in the low one.",
            )
            rep.finding(
                "warning",
                f"`{c}` has two regimes",
                verdict,
                f"keep `{c}` as supplied and add an is-high-regime flag; do not rescale it blindly",
                priority=3,
            )
    if ratio_rows:
        ctx.store["ratio_scales"] = scales
        rep.table(
            pd.DataFrame(ratio_rows).set_index("column"),
            "ratio_scales",
            "What scale are the ratio columns on?",
            "A 'ratio' can be a fraction, a percentage or something else; the dictionary does not say. Do not apply regulatory thresholds (such as LTV > 80%) before you know.",
            fmt={
                "% rows = 0": lambda v: pct(v, 0),
                "% above 1": lambda v: pct(v, 0),
                "% above 5": lambda v: pct(v, 0),
                "median (positive)": "{:.2f}",
                "p90": "{:.1f}",
            },
            console=0,
        )

    # ── 2i. spikes, caps, sentinels ──────────────────────────────────────────────────────────────────────────────────────
    if not piles.empty:
        rep.table(
            piles,
            "spikes_and_caps",
            "Spikes, caps and floors in continuous columns",
            f"A single value holding more than {pct(SPIKE_SHARE, 0)} of the rows (or a clear pile-up at the min / max).",
            fmt={"share": lambda v: pct(v, 2), "value": lambda v: num(v)},
            max_rows=25,
        )
        if len(zeros):
            z = zeros.sort_values("share", ascending=False)
            fig, ax = plt.subplots(figsize=(8.6, 0.3 * len(z) + 1.2))
            ax.barh(range(len(z)), z["share"] * 100, color=NEUTRAL, height=0.62)
            ax.set_yticks(range(len(z)), [f"{c}  [{ROLE_TAG[ctx.roles[c]]}]" for c in z["column"]], fontsize=8)
            ax.invert_yaxis()
            for i, v in enumerate(z["share"]):
                ax.text(v * 100 + 0.8, i, f"{v * 100:.0f}%", va="center", fontsize=8, color=INK2)
            ax.set_xlabel("share of rows that are exactly 0 (%)")
            ax.grid(axis="y", visible=False)
            n_half = int((z["share"] > 0.5).sum())
            n_del = sum(ctx.roles[c] == "tier0_target_defining" for c in z["column"])
            rep.figure(
                fig,
                "zero_inflation",
                "How zero-heavy are the continuous columns?",
                "One bar per column: the share of rows whose value is exactly 0; the tag shows the column's role.",
                f"**{len(z)}** columns have exact zeros in at least {pct(float(z['share'].min()), 1)} of rows; {n_half} are majority-zero and {n_del} of the {len(z)} are delinquency columns, where 'never late' is the majority. The most extreme is `{z.iloc[0]['column']}` ({pct(z.iloc[0]['share'], 0)} zeros).",
            )
        special = piles[~piles["pattern"].eq("zero-inflated")]
        if len(special):
            top_cols = list(dict.fromkeys(special["column"]))[:6]
            n_rows = int(np.ceil(len(top_cols) / 3))
            fig, axes = plt.subplots(n_rows, min(len(top_cols), 3), figsize=(10.2, 2.9 * n_rows + 0.3), squeeze=False)
            for ax, c in zip(axes.ravel(), top_cols, strict=False):
                s_ = df[c].astype(float)
                lo, hi = s_.quantile(0.0), s_.quantile(0.995)
                ax.hist(np.clip(s_, lo, hi), bins=60, color=NEUTRAL_LIGHT)
                sub = special[special["column"] == c]
                for _, p in sub.iterrows():
                    ax.axvline(p["value"], color=INK, lw=1.4)
                ax.set_title(c, fontsize=9.5)
                ax.text(
                    0.97,
                    0.93,
                    "\n".join(
                        f"{pct(p['share'])} of rows = {num(p['value'])} ({'cap' if p['pattern'] == 'capped at max' else 'floor' if p['pattern'] == 'floored at min' else p['pattern']})"
                        for _, p in sub.iterrows()
                    ),
                    transform=ax.transAxes,
                    ha="right",
                    va="top",
                    fontsize=8,
                    color=INK2,
                )
                ax.set_yticks([])
                ax.set_xlabel("value (top 0.5% not shown)", fontsize=8)
            for ax in axes.ravel()[len(top_cols) :]:
                ax.axis("off")
            fig.tight_layout()
            big = special.iloc[0]
            rep.figure(
                fig,
                "spikes_and_caps",
                "Caps, floors and default values",
                "Histogram of each column (the top 0.5% of values is clipped for readability); the dark line marks the over-represented value.",
                f"**{special['column'].nunique()}** columns have a cap, floor or default value; the biggest is `{big['column']}` = {num(big['value'])} in {pct(big['share'])} of rows.",
            )
        for _, p in censored.iterrows():
            rep.finding(
                "warning",
                f"`{p['column']}` is {'capped at its maximum' if p['pattern'] == 'capped at max' else 'floored at its minimum'}: {num(p['value'])}",
                f"{p['rows']:,} rows ({pct(p['share'], 2)}) sit exactly on this limit, so the column is censored there (clamp, cap or rating-grade floor).",
                "keep the column but add an is-at-limit flag; do not trust extremes",
            )
        if len(zeros):
            rep.finding(
                "info",
                f"{len(zeros)} continuous columns are zero-inflated",
                ", ".join(f"{r['column']} ({pct(r['share'], 0)})" for _, r in zeros.head(6).iterrows())
                + (" …" if len(zeros) > 6 else ""),
                "add an is-zero flag next to a log transform for linear models; trees do not care",
            )
        if len(sentinels):
            rep.finding(
                "info",
                f"{len(sentinels)} columns have a default / sentinel value",
                ", ".join(f"{r['column']} = {num(r['value'])} ({pct(r['share'])})" for _, r in sentinels.iterrows()),
                "check whether it means 'missing' or 'not applicable'; keep it as its own flag",
            )
    if len(rare):
        rep.table(
            rare.set_index("column"),
            "rare_levels",
            "Categorical levels with fewer than 20 rows",
            fmt={"event rate": lambda v: pct(v)},
        )
        rep.finding(
            "info",
            f"{len(rare)} very rare category levels",
            "; ".join(f"{r['column']}={r['level']} ({r['rows']} rows)" for _, r in rare.head(4).iterrows()),
            "merge into 'other' before one-hot encoding; never trust their event rate",
        )
    if consts or near_const:
        rep.finding("info", "(Near-)constant columns", ", ".join([*consts, *near_const]), "drop or keep only as a flag")
    clean_shape = n_dup == 0 and n_na == 0
    rep.callout(
        "warn" if (n_bad_rules >= MIN_BAD_RULES or not clean_shape) else "good",
        "**Bottom line for modeling.** "
        + (
            f"Structurally the table is clean ({n_na:,} missing cells, {n_dup:,} duplicate rows)"
            if clean_shape
            else f"The table has {n_na:,} missing cells and {n_dup:,} duplicate rows"
        )
        + (
            f", but the columns do not follow their own definitions ({n_bad_rules} of {len(rule_df)} relationships broken)"
            if n_bad_rules >= MIN_BAD_RULES
            else ""
        )
        + ". The traps are semantic: censored / spiked columns, values that behave like placeholders, and relationships that do not hold. Prefer models that tolerate this (trees) and be careful with hand-built ratios.",
    )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 3 · Can the files be pooled?
# ══════════════════════════════════════════════════════════════════════════════════
@register(
    3,
    "Can the files be pooled?",
    "Are the CSV files random chunks of ONE population - or different populations that need separate treatment?",
)
def section_pooling(ctx: Ctx) -> None:
    """Are the files interchangeable?  3a event rate per file, 3b feature drift against a shuffled-label noise ceiling, 3c adversarial validation,
    3d row-order trends, 3e batch balance, 3f the pooling verdict."""
    rep, df, y, feats = ctx.rep, ctx.df, ctx.y, ctx.features
    files = sorted(df[FILE_COL].unique(), key=lambda f: int(re.sub(r"\D", "", f) or 0))
    k = len(files)
    if k < 2:
        rep.callout("info", "Only one file was found, so there is nothing to compare: pooling cannot be tested.")
        return
    gid = df[FILE_COL].map({f: i for i, f in enumerate(files)}).to_numpy()
    rng = np.random.default_rng(SEED)

    # ── 3a. same event rate in every file? ─────────────────────────────────────────────────────────────────────────
    g = pd.DataFrame({"file": df[FILE_COL], "y": y}).groupby("file")["y"].agg(["size", "sum"]).reindex(files)
    lo, hi = wilson(g["sum"], g["size"])
    p_chi = float(stats.chi2_contingency(np.array([g["sum"], g["size"] - g["sum"]]))[1])
    rate = g["sum"] / g["size"]
    fig, ax = plt.subplots(figsize=(8.4, 3.4))
    ax.errorbar(
        range(k),
        rate * 100,
        yerr=[(rate - lo) * 100, (hi - rate) * 100],
        fmt="o",
        color=C1,
        ecolor=C1,
        capsize=3,
        ms=6,
        lw=1.5,
        label="event rate in the file (95% CI)",
    )
    ax.axhline(ctx.base_rate * 100, color=INK2, ls="--", lw=1, label=f"pooled rate {pct(ctx.base_rate, 2)}")
    ax.set_xticks(range(k), [f.replace("df_", "df ") for f in files])
    ax.set_ylabel("target = 1  (%)")
    ax.set_ylim(0, max(hi) * 100 * 1.25)
    legend_top(ax, ncol=2)
    rep.figure(
        fig,
        "event_rate_by_file",
        "Is the event rate the same in every file?",
        "One dot per file with its 95% Wilson confidence interval; the dashed line is the pooled rate.",
        f"Rates range {pct(rate.min(), 2)} - {pct(rate.max(), 2)}; a chi-square test for equal rates gives **p = {p_chi:.2f}** (small p would mean the files differ).",
    )

    # ── 3b. does any feature drift between files?  (noise-calibrated) ────────────────────────────────────────────────
    as_cat = {c: ctx.plot_as[c] == "categorical" for c in feats}
    obs = drift_matrix(df, feats, as_cat, gid, k)
    null = np.hstack([drift_matrix(df, feats, as_cat, rng.permutation(gid), k).to_numpy() for _ in range(3)])
    ceiling = np.maximum(null.max(axis=1), 1e-6)  # largest PSI seen when file labels are SHUFFLED = pure sampling noise
    ratio = obs.div(ceiling, axis=0)
    ratio.columns = [f.replace("df_", "") for f in files]
    n_cells, n_exceed = ratio.size, int((ratio > 1).to_numpy().sum())
    expected_exceed = n_cells / (null.shape[1] + 1)
    order = ratio.max(axis=1).sort_values(ascending=False).index[:30]
    show = ratio.loc[order]
    fig, ax = plt.subplots(figsize=(7.4, 0.2 * len(show) + 1.6))
    im = ax.imshow(show.to_numpy(), aspect="auto", cmap=CMAP_SEQ, vmin=0, vmax=2)
    ax.set_xticks(range(k), show.columns)
    ax.set_yticks(range(len(show)), show.index, fontsize=7.5)
    ax.set_xlabel("file number")
    ax.grid(False)
    for (i, j), v in np.ndenumerate(show.to_numpy()):
        if v > 1:
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, ec=INK, lw=1.6))
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cb.set_label("PSI ÷ noise ceiling   (1 = pure sampling noise)", fontsize=8.5)
    rep.figure(
        fig,
        "file_drift",
        "Does any feature look different in some file?",
        "Each cell compares one file with the other nine (PSI, population stability index) and divides by the largest PSI you get when file labels are shuffled (30 shuffled values per column), so about 1 cell in 31 crosses the ceiling by luck alone. Dark outline = above the ceiling.",
        f"**{n_exceed} of {n_cells}** cells exceed the noise ceiling (about {expected_exceed:.0f} are expected by chance alone) and the largest ratio is {ratio.to_numpy().max():.2f}. Showing the 30 most extreme features.",
    )

    # ── 3c. can a model tell the files apart?  (adversarial validation, with a shuffled-label control per file) ─────────
    adv: dict[str, float] = {}
    adv_null: dict[str, float] = {}
    if not ctx.args.no_models:
        X = to_numeric_frame(df, feats)
        for i in range(k if not ctx.args.fast else min(k, 4)):
            pos = np.flatnonzero(gid == i)
            others = np.flatnonzero(gid != i)
            # files of different sizes
            pos = pos if len(pos) <= len(others) else rng.choice(pos, size=len(others), replace=False)
            neg = rng.choice(others, size=len(pos), replace=False)
            idx = np.concatenate([pos, neg])
            lab = np.r_[np.ones(len(pos)), np.zeros(len(neg))].astype(int)
            adv[files[i]] = float(np.mean(cv_predict(X.iloc[idx], lab, folds=3, max_iter=60)[1]))
            # same job, labels shuffled
            adv_null[files[i]] = float(np.mean(cv_predict(X.iloc[idx], rng.permutation(lab), folds=3, max_iter=60)[1]))
        xs = np.arange(len(adv))
        fig, ax = plt.subplots(figsize=(8.4, 3.2))
        ax.vlines(xs, 0.5, list(adv.values()), color=NEUTRAL, lw=2.2, alpha=0.6)
        ax.scatter(xs, list(adv.values()), color=NEUTRAL, s=48, zorder=3, label="file vs the rest")
        ax.scatter(
            xs,
            list(adv_null.values()),
            facecolors="none",
            edgecolors=INK2,
            s=60,
            zorder=4,
            label="same, labels shuffled (what 'indistinguishable' looks like)",
        )
        ax.axhline(0.5, color=INK2, ls="--", lw=1)
        ax.set_ylim(0.45, 0.60)
        ax.set_xticks(xs, [f.replace("df_", "df ") for f in adv])
        ax.set_ylabel("AUC (0.5 = chance)")
        legend_top(ax, ncol=2)
        rep.figure(
            fig,
            "adversarial_validation",
            "Can a model recognise which file a row came from?",
            "A gradient-boosting model tries to tell one file from a same-sized random sample of the others (3-fold CV). 0.5 = impossible (files are interchangeable); 1.0 = trivial. Hollow dots repeat the job with shuffled labels, i.e. pure chance.",
            f"Best AUC is **{max(adv.values()):.3f}**; with shuffled labels the same procedure scores up to {max(adv_null.values()):.3f}, so the files cannot be told apart from their features.",
        )

    # ── 3d. is there a trend or sorting along the rows of a file? ─────────────────────────────────────────────────────
    num_cols = [c for c in feats if is_num(df[c])]
    codes = to_numeric_frame(df, feats)  # bool / text columns become 0/1 codes so EVERY column is tested
    worst = (0.0, "", "")
    z_t = 0.0
    n_tests = 0
    for f in files:
        rows_f = np.flatnonzero((df[FILE_COL] == f).to_numpy())
        z_t = max(z_t, abs(runs_z(y[rows_f])))
        for c in feats:
            n_tests += 1
            r = abs(float(stats.spearmanr(df[ROW_COL].to_numpy()[rows_f], codes[c].to_numpy()[rows_f])[0]))
            if np.isfinite(r) and r > worst[0]:
                worst = (r, c, f)
    n_file = int(len(df) / k)
    # 95% ceiling of the max |rho| over n_tests pure-noise tests
    rho_noise = float(stats.norm.isf(0.025 / n_tests) / np.sqrt(n_file))
    z_noise = float(stats.norm.isf(0.025 / k))
    no_trend = worst[0] <= rho_noise and z_t <= z_noise
    rep.text(
        f"**Row order inside the files - trends.** Across all {len(feats)} columns the strongest rank correlation between row position and a column is |rho| = {worst[0]:.3f} "
        f"(`{worst[1]}` in {worst[2]}; pure noise stays below ~{rho_noise:.3f} over {n_tests} tests of {n_file:,} rows) and the runs test on the target gives |z| = {z_t:.1f} (noise ceiling {z_noise:.1f}). "
        + (
            "No monotone sorting and no drift along the file."
            if no_trend
            else "There IS a trend or sorting along the file: shuffle before any positional split (head / tail / iloc)."
        )
    )

    # ── 3e. are the rows 'too evenly mixed' inside fixed-size batches?  (a generator / assembly signature) ──────────────
    batchy = False
    if ctx.args.fast:
        rep.text("*The batch-balance test needs the complete files, so it is skipped in `--fast` mode.*", console=False)
    else:
        with rep.attempt("Batch-balance test"):
            bb = batch_balance(df, num_cols, files, (100, 250, 500, 1000, 2500))
            best = bb["median variance ratio"].idxmin()
            vr, exp_vr = (
                float(bb.loc[best, "median variance ratio"]),
                float(bb.loc[best, "expected if order is random"]),
            )
            batchy = vr < 0.7 * exp_vr
            rep.table(
                bb,
                "batch_balance",
                "Are the rows too evenly mixed inside blocks?",
                "Rows are cut into consecutive blocks of the given size; a variance ratio of 1 means the block means vary as much as in a random shuffle, well below 1 means every block is more balanced than chance.",
                fmt={
                    "median variance ratio": "{:.2f}",
                    "expected if order is random": "{:.2f}",
                    "columns clearly too balanced (< 0.7)": lambda v: pct(v, 0),
                },
                bars={"median variance ratio": (0, 1.5)},
            )
            ctx.store["batch"] = {"block": int(best), "vr": vr, "expected": exp_vr, "batchy": batchy}
            if batchy:
                rep.finding(
                    "info",
                    f"Rows are mixed more evenly than chance inside {best}-row blocks",
                    f"Median variance ratio of block means is {vr:.2f} against {exp_vr:.2f} expected for a random order ({pct(float(bb.loc[best, 'columns clearly too balanced (< 0.7)']), 0)} of columns clearly too balanced). Real extracts are not assembled this way; batch-wise generation or stratified assembly is.",
                    "Pooling and CV are unaffected, but never treat row position as a time axis and always shuffle before splitting or sub-sampling.",
                )

    # ── 3f. verdict ─────────────────────────────────────────────────────────────────────────────────────────────────────
    max_ratio = float(ratio.to_numpy().max())
    worst_cell = ratio.stack().idxmax()  # (feature, file)
    # distinguishable only if clearly above the shuffled-label control
    adv_ok = (not adv) or max(adv.values()) < max(adv_null.values()) + 0.03
    interchangeable = p_chi > 0.01 and n_exceed <= max(5, 3 * expected_exceed) and max_ratio < 3.0 and adv_ok
    ctx.store["pooling"] = {
        "interchangeable": interchangeable,
        "n_files": k,
        "adv_max": max(adv.values()) if adv else None,
        "p_chi": p_chi,
        "last_file": files[-1],
        "n_last": int((gid == k - 1).sum()),
        "pos_last": int(y[gid == k - 1].sum()),
    }
    if interchangeable:
        rep.finding(
            "ok",
            f"The {k} files are interchangeable random chunks - pool them",
            f"event rates agree (p = {p_chi:.2f}); {n_exceed} of {n_cells} drift cells exceed noise (the largest is {max_ratio:.1f}x the ceiling)"
            + (f", and a model cannot tell files apart (AUC {max(adv.values()):.3f})." if adv else "."),
            "Use all rows as one dataset; keep one whole file (e.g. the last) as a sealed final test, and develop with stratified 5-fold CV on the rest.",
        )
    else:
        rep.finding(
            "warning",
            "The files differ from each other",
            f"chi-square p = {p_chi:.3g}; {n_exceed} drift cells above noise, the worst is `{worst_cell[0]}` in file {worst_cell[1]} ({max_ratio:.1f}x the ceiling)"
            + (f"; adversarial AUC up to {max(adv.values()):.3f}" if adv else ""),
            "Investigate before pooling: treat the file as a group in cross-validation (GroupKFold) and check for period / source effects.",
        )
    rep.finding(
        "info",
        "No time axis, so no out-of-time validation",
        "Random i.i.d. chunks mean every split strategy is equivalent here - but also that a future / production shift cannot be measured.",
        "State this limitation when you report model performance.",
    )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 4 · The target
# ══════════════════════════════════════════════════════════════════════════════════
# Simple 'delinquency style' flags to compare with the target: (definition, needed columns, flag)
ALT_FLAGS = [
    ("MaximumNumberDelinquencyDays >= 30", ("MaximumNumberDelinquencyDays",), lambda d: d["MaximumNumberDelinquencyDays"] >= 30),
    ("MaximumNumberDelinquencyDays >= 90", ("MaximumNumberDelinquencyDays",), lambda d: d["MaximumNumberDelinquencyDays"] >= 90),
    ("daysInDeliquency >= 30", ("daysInDeliquency",), lambda d: d["daysInDeliquency"] >= 30),
    ("daysInDeliquency >= 90", ("daysInDeliquency",), lambda d: d["daysInDeliquency"] >= 90),
    ("TotalNumberDelinquencyDaysLastYear >= 90", ("TotalNumberDelinquencyDaysLastYear",), lambda d: d["TotalNumberDelinquencyDaysLastYear"] >= 90),
    ("TotalNumberDelinquencyDays >= 365", ("TotalNumberDelinquencyDays",), lambda d: d["TotalNumberDelinquencyDays"] >= 365),
    ("overdueInstalmentsCount >= 1", ("overdueInstalmentsCount",), lambda d: d["overdueInstalmentsCount"] >= 1),
    ("overdueAmount >= 1 (material)", ("overdueAmount",), lambda d: d["overdueAmount"] >= MATERIAL),
    ("RestructuringStatusBool", ("RestructuringStatusBool",), lambda d: d["RestructuringStatusBool"].astype(bool)),
    ("score <= 150", ("score",), lambda d: d["score"] <= 150),
    ("probabilityOfDefault >= 85", ("probabilityOfDefault",), lambda d: d["probabilityOfDefault"] >= 85),
]  # fmt: skip


def auc_se(auc: float, n_pos: int, n_neg: int) -> float:
    """Hanley-McNeil standard error of an AUC estimate."""
    q1, q2 = auc / (2 - auc), 2 * auc**2 / (1 + auc)
    return float(
        np.sqrt((auc * (1 - auc) + (n_pos - 1) * (q1 - auc**2) + (n_neg - 1) * (q2 - auc**2)) / (n_pos * n_neg))
    )


@register(4, "The target", "What are we predicting, how rare is it, and what does that mean for modeling?")
def section_target(ctx: Ctx) -> None:
    """The target: 4a how it was chosen, 4b class balance, 4c what 'good' means on a rare event, 4d how well simple rules and one best cut reproduce it."""
    rep, df, y = ctx.rep, ctx.df, ctx.y
    n_pos, n_neg, rate = int(y.sum()), int((1 - y).sum()), ctx.base_rate

    # ── 4a. how the target was chosen ───────────────────────────────────────────────────────────────────────────────
    cand = ctx.store["candidates"]
    how = (
        "the documented default - the dictionary does not name a target, so confirm it with the organisers"
        if ctx.target_is_default
        else "chosen by you (--target)"
    )
    rep.text(
        f"**Target = `{ctx.target}`** ({how}). Dictionary meaning: *{describe(ctx.target) or 'n/a'}*. Positive class (1) = `{ctx.store.get('positive_label', 1)}` ({pct(rate, 1)} of the rows)."
    )
    if len(cand):
        shown = cand.head(8).set_index("column")[["positive_rate", "name_hint", "family"]]
        rep.table(
            shown,
            "target_candidates",
            "Binary columns that could be a target",
            "Ranked by 'looks like an outcome flag': a telling name and a rare-event rate. Everything else is a feature.",
            fmt={"positive_rate": lambda v: pct(v, 2)},
        )

    # ── 4b. class balance ────────────────────────────────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8.6, 1.7))
    ax.barh(
        [0],
        [n_neg],
        color=C0,
        height=0.5,
        edgecolor=SURFACE,
        linewidth=2,
        label=f"target = 0   {n_neg:,} rows ({pct(1 - rate, 2)})",
    )
    ax.barh(
        [0],
        [n_pos],
        left=[n_neg],
        color=C1,
        height=0.5,
        edgecolor=SURFACE,
        linewidth=2,
        label=f"target = 1   {n_pos:,} rows ({pct(rate, 2)})",
    )
    ax.set_yticks([])
    ax.set_xlabel("rows")
    ax.grid(axis="y", visible=False)
    legend_top(ax, ncol=2)
    rep.figure(
        fig,
        "class_balance",
        "How rare is the event?",
        "The full bar is every row; the orange sliver is the rows with target = 1.",
        f"About **1 in {1 / rate:.0f}** rows is positive ({pct(rate, 2)}; {n_neg / n_pos:.2g} non-events per event)."
        + (" It is a rare-event problem." if rate < RARE_EVENT else ""),
    )

    # ── 4c. what 'good' means on an imbalanced target ───────────────────────────────────────────────────────────────
    base = pd.DataFrame(
        {
            "a useless model scores": [pct(1 - rate, 2), "0.500", f"{rate:.4f}", f"{rate * (1 - rate):.4f}"],
            "so use it?": [
                "no - 'always predict 0' already wins",
                "yes - ranking quality, threshold-free",
                "yes - much more sensitive to the rare class",
                "yes - checks the probabilities, not only the ranking",
            ],
        },
        index=pd.Index(["Accuracy", "ROC-AUC", "PR-AUC (average precision)", "Brier score"], name="metric"),
    )
    rep.table(
        base,
        "metric_baselines",
        "What does 'good' look like on a rare target?",
        "Chance levels to compare every later number against.",
    )
    se75, se90 = auc_se(0.75, n_pos, n_neg), auc_se(0.90, n_pos, n_neg)
    fold_pos = n_pos * (N_FOLDS - 1) / N_FOLDS
    rep.text(
        f"- **Events per variable:** {n_pos:,} positives / {len(ctx.features)} candidate features = **{n_pos / len(ctx.features):.0f}** (rule of thumb: at least {EPV_COMFORT} is comfortable). In every cross-validation test fold there are about {n_pos / N_FOLDS:,.0f} positives.\n"
        f"- **Resolution of an AUC:** pooling all {n_pos:,} events, one out-of-fold AUC of 0.75 is known to about ±{1.96 * se75:.3f} (±{1.96 * se90:.3f} at 0.90); a single fold with only {n_pos // N_FOLDS:,} events is about ±{1.96 * auc_se(0.75, n_pos // N_FOLDS, n_neg // N_FOLDS):.3f}. To compare two models use the same folds and a paired difference (DeLong or bootstrap), not these intervals.\n"
        f"- Train folds keep about {fold_pos:,.0f} positives, enough for gradient boosting and regularised logistic regression; heavy deep learning is not justified."
    )
    if rate < RARE_EVENT:
        epv = n_pos / len(ctx.features)
        rep.finding(
            "warning" if rate < 0.01 else "info",
            f"Rare event: {pct(rate, 2)} positives (about 1 in {1 / rate:.0f})",
            f"{n_pos:,} positives = {epv:.0f} per feature, "
            + (
                "enough to model."
                if epv >= EPV_COMFORT
                else f"fewer than the {EPV_COMFORT} per feature that make a model comfortable: keep it simple and regularised."
            ),
            "Stratify every split; judge with ROC-AUC, PR-AUC, KS and lift - never accuracy; try class weights only if PR-AUC improves; calibrate probabilities afterwards.",
        )

    # ── 4d. how does the target relate to simple delinquency flags? ─────────────────────────────────────────────────
    rows = []
    for name, need, fn in ALT_FLAGS:
        if all(c in df.columns for c in need) and name.split()[0] != ctx.target:
            flag = fn(df).to_numpy().astype(bool)
            n_f = int(flag.sum())
            both = int((flag & (y == 1)).sum())
            rows.append(
                {
                    "flag": name,
                    "rows flagged": n_f,
                    "P(target | flag)": both / max(n_f, 1),
                    "P(flag | target)": both / max(n_pos, 1),
                    "Jaccard overlap": both / max(n_f + n_pos - both, 1),
                }
            )
    num_feats = [c for c in ctx.features if is_num(df[c])]
    cut = best_single_cut(df, num_feats, y)
    if cut.get("column"):
        ctx.store["best_cut"] = cut
        rows.append(
            {
                "flag": f"best single cut found by scanning: {cut['column']} {cut['sign']} {cut['threshold']:,.4g}",
                "rows flagged": cut["flagged"],
                "P(target | flag)": cut["precision"],
                "P(flag | target)": cut["recall"],
                "Jaccard overlap": cut["jaccard"],
            }
        )
    if rows:
        alt = pd.DataFrame(rows).sort_values("Jaccard overlap", ascending=False).set_index("flag")
        rep.table(
            alt,
            "target_vs_simple_flags",
            "Is the target just a familiar delinquency rule?",
            "precision = P(target | flag) · recall = P(flag | target) · Jaccard = overlap / union (1 = identical). The last kind of row is the best single threshold found by scanning every cut point of every numeric column.",
            fmt={
                "P(target | flag)": lambda v: pct(v),
                "P(flag | target)": lambda v: pct(v),
                "Jaccard overlap": "{:.2f}",
                "rows flagged": "{:,}",
            },
            bars={"P(target | flag)": (0, 1), "P(flag | target)": (0, 1), "Jaccard overlap": (0, 1)},
            console=5,
        )
        hand = alt[~alt.index.str.startswith("best single cut")]
        msg = (
            f"The closest hand-picked rule is **{hand.index[0]}** (precision {pct(hand.iloc[0]['P(target | flag)'])}, recall {pct(hand.iloc[0]['P(flag | target)'])}, Jaccard {hand.iloc[0]['Jaccard overlap']:.2f})."
            if len(hand)
            else ""
        )
        if cut.get("column"):
            msg += f" Scanning every cut point finds a better one: **`{cut['column']}` {cut['sign']} {cut['threshold']:,.4g}** (precision {pct(cut['precision'])}, recall {pct(cut['recall'])}, Jaccard {cut['jaccard']:.2f})."
            msg += (
                " So one delinquency-style threshold already rebuilds a good part of the label, but it still mislabels many loans: the label is a state of the loan, not a single cut-off (section 8 shows what a small tree adds)."
                if cut["jaccard"] >= 0.6
                else " No single threshold reproduces the label, so it is defined by something richer than one cut-off - see section 8 for how much delinquency information it still carries."
            )
        rep.text(msg, console=False)
    if cut.get("column"):
        with rep.attempt("Event rate around the best cut"):
            c_ = cut["column"]
            x = df[c_].to_numpy(float)
            pos_x = x[x > 0]
            q_edges = np.unique(np.quantile(pos_x, np.linspace(0, 1, 11)[1:-1])) if len(pos_x) else np.array([])
            edges = np.unique(np.concatenate([q_edges, [cut["threshold"]]]))
            codes = np.where(x == 0, 0, 1 + np.digitize(x, edges, right=True))
            labels = ["0", *[f"≤ {num(e)}" for e in edges], f"> {num(edges[-1])}"]
            tab = (
                pd.DataFrame({"k": codes, "y": y})
                .groupby("k")["y"]
                .agg(n="size", events="sum")
                .reindex(range(len(labels)), fill_value=0)
            )
            tab = tab[tab["n"] >= 30]
            lo_, hi_ = wilson(tab["events"], tab["n"])
            rate_ = (tab["events"] / tab["n"]).to_numpy()
            floor_ = 0.0005  # zero-event bins are drawn at the bottom as hollow dots
            fig, ax = plt.subplots(figsize=(9.2, 3.9))
            xs = np.arange(len(tab))
            ok = tab["events"].to_numpy() > 0
            ax.errorbar(
                xs[ok],
                rate_[ok] * 100,
                yerr=[(rate_[ok] - lo_[ok]) * 100, (hi_[ok] - rate_[ok]) * 100],
                fmt="o-",
                color=C1,
                ecolor=C1,
                capsize=3,
                ms=6,
                lw=1.6,
            )
            ax.scatter(xs[~ok], np.full((~ok).sum(), floor_ * 100), facecolors="none", edgecolors=C1, zorder=3)
            ax.axhline(ctx.base_rate * 100, color=INK2, ls="--", lw=1)
            ax.set_yscale("log")
            ax.set_yticks([0.1, 1, 10, 100], ["0.1%", "1%", "10%", "100%"])
            ax.set_ylim(floor_ * 100 * 0.7, 140)
            ax.set_xticks(xs, [labels[k] for k in tab.index], rotation=35, ha="right", fontsize=8)
            at_cut = [i for i, k in enumerate(tab.index) if k > 0 and labels[k] == f"≤ {num(cut['threshold'])}"]
            if at_cut and at_cut[0] + 1 < len(tab):
                ax.axvline(at_cut[0] + 0.5, color=INK, lw=1.4)
                ax.text(at_cut[0] + 0.55, floor_ * 100 * 1.2, "best cut", fontsize=8.5, color=INK, va="bottom")
            for i, (n_, r_) in enumerate(zip(tab["n"], rate_, strict=True)):
                ax.text(i, max(r_ * 100, floor_ * 100) * 1.6, f"n={int(n_):,}", ha="center", fontsize=7.2, color=INK2)
            ax.set_xlabel(f"{c_} (bins; 0 has its own bin)")
            ax.set_ylabel("event rate (log scale)")
            beyond = x > cut["threshold"] if cut["sign"] == ">" else x <= cut["threshold"]
            rep.figure(
                fig,
                "event_rate_by_best_cut",
                f"Where does the label switch on?  Event rate by `{c_}`",
                "Rows are binned by one column (0 gets its own bin; the dark line is the best single cut of the table above). The vertical axis is logarithmic, hollow dots = bins without events, dashed = overall event rate.",
                (
                    f"The event rate is {pct(float(y[x == 0].mean()), 2)} where `{c_}` is 0 and "
                    if (x == 0).any()
                    else "The event rate is "
                )
                + f"{pct(float(y[beyond].mean()), 0)} beyond the cut ({int(beyond.sum()):,} loans, of which {int((beyond & (y == 0)).sum()):,} are not flagged), against {pct(ctx.base_rate, 2)} overall.",
            )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 5 · Feature distributions
# ══════════════════════════════════════════════════════════════════════════════════
def recommend_transform(s: pd.Series, kind: str) -> tuple[str, float, float]:
    """(recommended transform, skew before, skew after) - the 'after' is measured, not assumed."""
    x = s.to_numpy(float)
    before = skew_of(s)
    if kind in ("binary", "nominal") or abs(before) <= 1.0:
        return "none", before, before
    if x.min() >= 0:
        zero_heavy = (x == 0).mean() >= 0.05 and (x > 0).sum() > 20
        # a zero spike makes log1p of the WHOLE column over-correct: judge a zero-heavy column on its positive part
        after = skew_of(pd.Series(np.log1p(x[x > 0] if zero_heavy else x)))
        if abs(after) < abs(before):
            return ("zero-flag + log1p of the positive part" if zero_heavy else "log1p"), before, after
    clipped = pd.Series(np.clip(x, *np.quantile(x, [0.005, 0.995])))
    return "clip at p0.5 / p99.5", before, skew_of(clipped)


UNITS = {
    "amount": "currency units",
    "days": "days",
    "count": "count",
    "ratio": "ratio",
    "rate": "rate",
    "score": "points",
}
COLUMN_UNITS = {"Age": "years"}  # where the kind's generic unit would be wrong
LOG_TICKS = [(0, "0"), (1, "1"), (10, "10"), (100, "100"), (1e3, "1k"), (1e4, "10k"), (1e5, "100k"), (1e6, "1M")]


def draw_class_hist(ax: plt.Axes, s: pd.Series, y: np.ndarray, title: str, unit: str = "") -> float:
    """Histogram of one numeric column for class 0 vs class 1 (density, so shapes compare).
    Heavy right tails are drawn on a log scale; zero-heavy columns show only the non-zero part and
    print the share of zeros per class.  Values beyond the 0.5% / 99.5% quantiles are not drawn (and
    counted in the note).  Returns the KS distance between the classes."""
    x = s.to_numpy(float)
    skew = skew_of(s)
    use_log = x.min() >= 0 and abs(skew) > 1.5
    zero = x == 0
    zero_heavy = zero.mean() >= 0.2
    keep = ~zero if zero_heavy else np.ones(len(x), bool)
    t = np.log10(1 + x[keep]) if use_log else x[keep]
    yy = y[keep]
    ax.set_title(title, fontsize=9, pad=14)
    ax.set_yticks([])
    note = f"skew {skew:+.1f}" + (
        f" · zeros {zero[y == 0].mean():.0%} vs {zero[y == 1].mean():.0%}" if zero_heavy else ""
    )
    if len(t) < 20 or np.ptp(t) == 0:
        ax.text(0.5, 0.5, "constant", transform=ax.transAxes, ha="center", color=MUTED)
        ax.text(0, 1.02, note, transform=ax.transAxes, ha="left", va="bottom", fontsize=7.8, color=INK2)
        return 0.0
    lo, hi = np.quantile(t, [0.005, 0.995])
    if lo == hi:
        lo, hi = t.min(), t.max()
    beyond = float(((t < lo) | (t > hi)).mean())
    bins = np.linspace(lo, hi, 36)
    for cls, color in ((0, C0), (1, C1)):
        vals = t[yy == cls]
        vals = vals[(vals >= lo) & (vals <= hi)]
        if len(vals) > 5:
            ax.hist(vals, bins=bins, density=True, histtype="stepfilled", alpha=0.30, color=color, lw=0)
            ax.hist(vals, bins=bins, density=True, histtype="step", color=color, lw=1.5)
    if beyond >= 0.005:
        note += f" · {beyond:.1%} off-plot"
    ax.text(0, 1.02, note, transform=ax.transAxes, ha="left", va="bottom", fontsize=7.8, color=INK2)
    what = f"{unit} " if unit else ""
    if use_log:
        pos = [(np.log10(1 + v), lab) for v, lab in LOG_TICKS if lo <= np.log10(1 + v) <= hi]
        if pos:
            ax.set_xticks([a for a, _ in pos], [b for _, b in pos], fontsize=7.8)
        ax.set_xlabel(f"{what}(log scale)" + (", non-zero values only" if zero_heavy else ""), fontsize=8)
    else:
        ax.tick_params(axis="x", labelsize=7.8)
        ax.set_xlabel(what.strip() + (" (non-zero values only)" if zero_heavy else ""), fontsize=8)
    return ks_stat(y, x)


def level_table(ctx: Ctx, col: str, max_levels: int = 12) -> pd.DataFrame:
    """Rows = levels of a categorical column: n, events, event rate with Wilson CI.  Rare levels
    beyond the `max_levels` biggest are merged into 'other'."""
    d = pd.DataFrame({"v": ctx.df[col].to_numpy(), "y": ctx.y})
    g = d.groupby("v")["y"].agg(n="size", events="sum")
    if len(g) > max_levels:
        top = g["n"].nlargest(max_levels).index
        rest = g.drop(top).sum()
        g = g.loc[top].sort_values("n", ascending=False)
        g.index = [label_of(col, v) for v in g.index]
        g.loc["other"] = rest
    else:
        g = g.sort_index()
        g.index = [label_of(col, v) for v in g.index]
    g["rate"] = g["events"] / g["n"]
    g["lo"], g["hi"] = wilson(g["events"], g["n"])
    return g


def draw_category_rows(ctx: Ctx, cols: Sequence[str]) -> tuple[plt.Figure, dict[str, pd.DataFrame]]:
    """One row per categorical column: left = share of rows per level, right = event rate per level
    (hollow dot = fewer than 30 rows, no interval drawn)."""
    tabs = {c: level_table(ctx, c) for c in cols}
    heights = [max(len(t), 3) + 1.2 for t in tabs.values()]
    fig = plt.figure(figsize=(10.2, 0.27 * sum(heights) + 0.4))
    gs = fig.add_gridspec(len(cols), 2, height_ratios=heights, width_ratios=[1, 1.15], hspace=0.6, wspace=0.55)
    for i, (c, t) in enumerate(tabs.items()):
        a1, a2 = fig.add_subplot(gs[i, 0]), fig.add_subplot(gs[i, 1])
        yy = np.arange(len(t))
        ok = (t["n"] >= 30).to_numpy()
        share = (t["n"] / t["n"].sum() * 100).to_numpy()
        a1.barh(yy, share, color=NEUTRAL, height=0.62)
        a1.set_yticks(yy, t.index, fontsize=8)
        a1.invert_yaxis()
        a1.set_title(f"{c}  -  share of rows (%), label = rows", fontsize=8.8)
        for j, (n, sh) in enumerate(zip(t["n"], share, strict=True)):
            a1.text(sh + 0.5, j, f"{int(n):,}", va="center", fontsize=7.8, color=INK2)
        a1.grid(axis="y", visible=False)
        rate = (t["rate"] * 100).to_numpy()
        a2.errorbar(
            rate[ok],
            yy[ok],
            xerr=[((t["rate"] - t["lo"]) * 100).to_numpy()[ok], ((t["hi"] - t["rate"]) * 100).to_numpy()[ok]],
            fmt="o",
            color=C1,
            ecolor=C1,
            capsize=2.5,
            ms=5,
            lw=1.3,
        )
        if (~ok).any():
            a2.scatter(rate[~ok], yy[~ok], facecolors="none", edgecolors=C1, zorder=3)
        a2.axvline(ctx.base_rate * 100, color=INK2, ls="--", lw=1)
        a2.set_yticks(yy, [""] * len(t))
        a2.set_ylim(a1.get_ylim())
        a2.set_title("event rate (%) with 95% CI (own scale; dashed = overall)", fontsize=8.4)
        top_x = max(
            float(((t["hi"] * 100).to_numpy()[ok]).max()) if ok.any() else 0.0,
            ctx.base_rate * 150,
            float(rate.max()) * 1.05,
        )
        a2.set_xlim(0, top_x * 1.05)
        a2.grid(axis="y", color=GRID, lw=0.6)
        a2.tick_params(axis="x", labelsize=8)
    return fig, tabs


@register(5, "Feature distributions", "What does each column look like, and how do events differ from non-events?")
def section_distributions(ctx: Ctx) -> None:
    """What does each column look like?  5a numeric profile with a measured transform advice, 5b class-overlay histograms, 5c categorical columns and flags."""
    rep, df, y, feats = ctx.rep, ctx.df, ctx.y, ctx.features
    num_cols = [c for c in feats if ctx.plot_as[c] == "numeric"]
    bin_cols = [c for c in feats if ctx.kinds[c] == "binary"]
    cat_cols = [c for c in feats if ctx.plot_as[c] == "categorical" and c not in bin_cols]

    # ── 5a. numeric profile + a transform recommendation that is MEASURED ──────────────────────────────────────────
    prof = []
    for c in [c for c in feats if is_num(df[c]) and ctx.kinds[c] != "nominal"]:
        s = df[c].astype(float)
        rec, sk0, sk1 = recommend_transform(s, ctx.kinds[c])
        q1, q3 = s.quantile([0.25, 0.75])
        iqr = q3 - q1
        outl = float(((s < q1 - 3 * iqr) | (s > q3 + 3 * iqr)).mean()) if iqr > 0 else np.nan
        prof.append(
            {
                "column": c,
                "kind": ctx.kinds[c],
                "unique": int(s.nunique()),
                "mean": s.mean(),
                "median": s.median(),
                "p99": s.quantile(0.99),
                "max": s.max(),
                "% zero": float((s == 0).mean()),
                "% extreme (3 IQR)": outl,
                "skew": sk0,
                "transform": rec,
                "skew after": sk1,
            }
        )
    prof_df = pd.DataFrame(prof).set_index("column")
    ctx.store["profile"] = prof_df
    rep.table(
        prof_df,
        "numeric_profile",
        "Numeric columns: shape and recommended transform",
        "Skew > 1 means a long right tail. The recommended transform is only suggested if it lowers |skew|; 'skew after' is measured. Trees do not need transforms; linear models / neural nets do.",
        fmt={
            "% zero": lambda v: pct(v, 0),
            "% extreme (3 IQR)": lambda v: "–" if pd.isna(v) else pct(v, 1),
            "skew": "{:+.1f}",
            "skew after": "{:+.1f}",
            "mean": lambda v: num(v),
            "median": lambda v: num(v),
            "p99": lambda v: num(v),
            "max": lambda v: num(v),
        },
        max_rows=60,
    )
    n_log = int(prof_df["transform"].str.contains("log1p").sum())
    n_heavy = int((prof_df["skew"].abs() > 5).sum())
    rep.finding(
        "info",
        f"{n_log} numeric columns need a log transform (linear models)",
        f"{n_heavy} have extreme skew (|skew| > 5); log1p brings the median |skew| from {prof_df['skew'].abs().median():.1f} to {prof_df['skew after'].abs().median():.1f}.",
        "Apply log1p to amounts / counts / days, plus a zero-flag where > 5% are zeros; skip it for tree models.",
    )

    # ── 5b. class-overlay histograms, 12 panels per figure ───────────────────────────────────────────────────────────
    for n_fig, start in enumerate(range(0, len(num_cols), 12), 1):
        chunk = num_cols[start : start + 12]
        rows = int(np.ceil(len(chunk) / 3))
        fig, axes = plt.subplots(rows, 3, figsize=(10.2, 2.6 * rows + 0.5))
        axes = np.atleast_1d(axes).ravel()
        ks_chunk = {
            c: draw_class_hist(ax, df[c], y, c, COLUMN_UNITS.get(c, UNITS.get(ctx.kinds[c], "")))
            for ax, c in zip(axes, chunk, strict=False)
        }
        for ax in axes[len(chunk) :]:
            ax.axis("off")
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        fig.legend(
            handles=[
                Patch(facecolor=C0, alpha=0.6, label="target = 0"),
                Patch(facecolor=C1, alpha=0.6, label="target = 1"),
            ],
            loc="upper right",
            ncol=2,
            frameon=False,
        )
        top = sorted(ks_chunk, key=ks_chunk.get, reverse=True)[:3]
        rep.figure(
            fig,
            f"numeric_distributions_{n_fig}",
            f"Numeric columns by class ({n_fig}/{int(np.ceil(len(num_cols) / 12))})",
            "Blue = rows with target 0, orange = rows with target 1; both are normalised so shapes compare. Heavy tails are drawn on a log scale. Above each panel: skew; for zero-heavy columns the share of exact zeros in target 0 vs target 1 (the plot then shows only the non-zero part); and the share of values left out as off-plot (the most extreme 1%).",
            "The columns where the two colours separate most (KS distance): "
            + ", ".join(f"`{c}` ({ks_chunk[c]:.2f})" for c in top)
            + ".",
        )

    # ── 5c. categorical columns ──────────────────────────────────────────────────────────────────────────────────────
    for n_fig, start in enumerate(range(0, len(cat_cols), 4), 1):
        chunk = cat_cols[start : start + 4]
        fig, tabs = draw_category_rows(ctx, chunk)
        spread = {
            c: (t["rate"][t["n"] >= 100].max() - t["rate"][t["n"] >= 100].min()) if (t["n"] >= 100).sum() > 1 else 0.0
            for c, t in tabs.items()
        }
        widest = max(spread, key=spread.get)
        hi_lvl = tabs[widest]["rate"][tabs[widest]["n"] >= 100].idxmax()
        rep.figure(
            fig,
            f"categorical_columns_{n_fig}",
            f"Categorical columns ({n_fig}/{int(np.ceil(len(cat_cols) / 4))})",
            "Left: how many rows fall in each level. Right: the event rate in that level with a 95% interval; the dashed line is the overall rate. A level far from the line AND with many rows is informative.",
            f"Biggest spread here: `{widest}` - event rate runs from {pct(tabs[widest]['rate'][tabs[widest]['n'] >= 100].min())} to {pct(spread[widest] + tabs[widest]['rate'][tabs[widest]['n'] >= 100].min())} (highest in level `{hi_lvl}`).",
        )
    if bin_cols:
        d_rows = []
        for c in bin_cols:
            t = level_table(ctx, c, max_levels=2)
            if len(t) == 2:
                d_rows.append((c, t))
        d_rows.sort(key=lambda r: -abs(r[1]["rate"].iloc[0] - r[1]["rate"].iloc[1]))
        fig, ax = plt.subplots(figsize=(9.4, 0.58 * len(d_rows) + 1.2))
        xmax = max(float(t["rate"].max()) for _, t in d_rows) * 100 * 1.12
        for i, (_, t) in enumerate(d_rows):
            ax.plot(t["rate"].to_numpy() * 100, [i, i], color=NEUTRAL_LIGHT, lw=2, zorder=1)
            for k_, (lvl, rate_, n_) in enumerate(zip(t.index, t["rate"], t["n"], strict=True)):
                lower = rate_ <= t["rate"].iloc[1 - k_]
                ax.scatter(
                    rate_ * 100, i, s=40 + 260 * n_ / len(df), color=NEUTRAL, zorder=3, edgecolor=SURFACE, linewidth=1.2
                )
                ax.text(
                    rate_ * 100 + (-0.015 if lower else 0.015) * xmax,
                    i - 0.3,
                    f"{lvl} ({int(n_):,})",
                    ha="right" if lower else "left",
                    va="bottom",
                    fontsize=7.8,
                    color=INK2,
                )
        ax.axvline(ctx.base_rate * 100, color=INK2, ls="--", lw=1)
        ax.set_yticks(range(len(d_rows)), [c for c, _ in d_rows], fontsize=8.5)
        ax.invert_yaxis()
        ax.set_xlabel(
            "event rate (%) when the flag takes each of its two values   (dot size = number of rows; dashed = overall)"
        )
        ax.grid(axis="y", visible=False)
        ax.set_xlim(-0.2 * xmax, xmax)
        ax.set_xticks([v for v in ax.get_xticks() if 0 <= v <= xmax])
        widest = d_rows[0]
        rep.figure(
            fig,
            "binary_flags",
            "Two-level columns: how much does the event rate move between the two values?",
            "Each row is a yes/no style column; the two dots are the event rates of its two values (label shows the value and its row count). A long line = a strong effect.",
            f"Largest gap: `{widest[0]}` ({pct(widest[1]['rate'].iloc[0])} vs {pct(widest[1]['rate'].iloc[1])}).",
        )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 6 · Features vs the target
# ══════════════════════════════════════════════════════════════════════════════════
def iv_strength(iv: float) -> str:
    return (
        "very strong (> 0.5)"
        if iv >= IV_SUSPICIOUS
        else "strong"
        if iv >= IV_STRONG
        else "medium"
        if iv >= 0.1
        else "weak"
        if iv >= 0.02
        else "none"
    )


AUC_COL = "AUC (category-aware, out-of-fold)"  # the one honest single-column AUC used everywhere
NAIVE_COL = "AUC if code read as a number"


def signal_table(ctx: Ctx) -> pd.DataFrame:
    """One row per feature: category-aware out-of-fold AUC, the naive 'code as a number' AUC,
    information value, KS - plus family / role.  Cached in ctx.store."""
    if "signal" in ctx.store:
        return ctx.store["signal"]
    df, y = ctx.df, ctx.y
    num = to_numeric_frame(df, ctx.features, fill_missing=True)
    rows = []
    for c in ctx.features:
        as_cat = ctx.kinds[c] in ("nominal", "binary")
        auc_oof = oof_signal_auc(df[c], y, as_cat=as_cat)
        naive = safe_auc(y, num[c].to_numpy())
        woe_tab, iv = woe_iv(df[c], y, as_cat=as_cat)
        if not as_cat:
            # rate_profile() reuses it instead of recomputing the same table
            ctx.store.setdefault("woe", {})[c] = woe_tab
        numeric_like = ctx.kinds[c] not in ("nominal", "binary")
        rows.append(
            {
                "column": c,
                "family": FAMILIES[family_of(c)][0],
                "role": ROLES[ctx.roles[c]][0],
                "role_key": ctx.roles[c],
                "kind": ctx.kinds[c],
                AUC_COL: auc_oof,
                NAIVE_COL: max(naive, 1 - naive),
                "Gini": 2 * auc_oof - 1,
                "IV": iv,
                "KS": ks_stat(y, num[c].to_numpy()) if numeric_like else np.nan,
                "strength (IV)": iv_strength(iv),
            }
        )
    sig = pd.DataFrame(rows).set_index("column").sort_values(AUC_COL, ascending=False)
    ctx.store["signal"] = sig
    return sig


def short_bin(label: str) -> str:
    m = re.search(r",\s*([-0-9.e+]+)\]", str(label))
    return num(float(m.group(1))) if m else str(label)[:10]


def rate_profile(ctx: Ctx, col: str) -> tuple[pd.DataFrame, bool]:
    """Event rate per bin (numeric: zero gets its own bin + quantile bins) or per level (categorical).
    Returns (table with n / rate / CI, is_ordered)."""
    if ctx.plot_as[col] == "categorical":
        t = level_table(ctx, col)
        return t, ctx.kinds[col] in ("count", "ordinal", "amount", "days", "ratio", "rate", "score")
    g = ctx.store.get("woe", {}).get(col)
    if g is None:
        g, _ = woe_iv(ctx.df[col], ctx.y)
    t = pd.DataFrame({"n": g["n"], "events": g["events"], "rate": g["event_rate"]})
    t["lo"], t["hi"] = wilson(t["events"], t["n"])
    t.index = [short_bin(i) for i in t.index]
    return t, True


def _fingerprint_takeaway(ctx: Ctx, cols: Sequence[str]) -> str:
    """One computed sentence about where the event rate peaks in each panel."""
    up = down = 0
    peaks = []
    for c in cols:
        t, ordered = rate_profile(ctx, c)
        t = t[t["n"] >= 30]
        if len(t) < 2:
            continue
        peaks.append(float(t["rate"].max()))
        if ordered:
            up += int(t["rate"].iloc[-1] > t["rate"].iloc[0])
            down += int(t["rate"].iloc[-1] < t["rate"].iloc[0])
    if not peaks:
        return "Not enough rows per bin to read the shape."
    return (
        f"The event rate is higher at the top end than at the bottom end in {up} of {len(cols)} panels and lower in {down} (a low score means high risk); the highest-rate bin of a panel reaches {pct(float(np.median(peaks)), 0)} "
        f"at the median and {pct(max(peaks), 0)} at most, against {pct(ctx.base_rate, 1)} overall: these columns describe the state the label records, they do not predict it."
    )


def wrap_name(name: str, width: int = 24) -> str:
    """Break a long CamelCase / snake_case column name onto two lines at the boundary nearest to the middle."""
    if len(name) <= width:
        return name
    cuts = [m.start() for m in re.finditer(r"(?<=[a-z0-9])(?=[A-Z])|(?<=_)", name) if 6 <= m.start() <= len(name) - 4]
    cut = min(cuts, key=lambda i: abs(i - len(name) / 2)) if cuts else len(name) // 2
    return name[:cut] + "\n" + name[cut:]


def draw_profile_grid(ctx: Ctx, cols: Sequence[str], aucs: pd.Series, ncols: int = 4) -> plt.Figure:
    """Small multiples: event rate (95% CI) across the bins / levels of each column."""
    nrows = int(np.ceil(len(cols) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(min(3.4 * ncols, 10.4), 2.75 * nrows))
    for ax, c in zip(np.atleast_1d(axes).ravel(), cols, strict=False):
        t, ordered = rate_profile(ctx, c)
        x = np.arange(len(t))
        ax.errorbar(
            x,
            t["rate"] * 100,
            yerr=[(t["rate"] - t["lo"]) * 100, (t["hi"] - t["rate"]) * 100],
            fmt="o-" if ordered else "o",
            color=C1,
            ecolor=C1,
            capsize=2,
            ms=4.2,
            lw=1.4,
            elinewidth=1,
        )
        ax.axhline(ctx.base_rate * 100, color=INK2, ls="--", lw=0.9)
        ax.set_title(f"{wrap_name(c)}\nAUC {aucs[c]:.2f} · {ROLE_TAG[ctx.roles[c]]}", fontsize=8.4, linespacing=1.3)
        ticks = list(x) if (len(t) <= 8 or not ordered) else [0, len(t) // 2, len(t) - 1]
        ax.set_xticks(
            ticks,
            [t.index[i] for i in ticks],
            fontsize=7 if ordered else 6,
            rotation=0 if ordered else 60,
            ha="center" if ordered else "right",
        )
        ax.set_ylim(bottom=0)
        ax.tick_params(axis="y", labelsize=7.5)
        ax.set_xlabel("bin (tick = upper edge)" if ordered else "", fontsize=7.5)
    for ax in np.atleast_1d(axes).ravel()[len(cols) :]:
        ax.axis("off")
    fig.supylabel("event rate (%)", fontsize=9)
    fig.tight_layout()
    return fig


@register(6, "Features vs the target", "Which columns separate events from non-events - and can I believe the ranking?")
def section_features_vs_target(ctx: Ctx) -> None:
    """Which columns separate events from non-events?  6a ranked single-column AUC, 6b the code-as-number trap, 6c event-rate profiles,
    6d non-monotone effects, 6e cohort concentration, 6f leaks / weak columns / sensitive attributes."""
    rep, df, y = ctx.rep, ctx.df, ctx.y
    sig = signal_table(ctx)
    a_col, naive_col = AUC_COL, NAIVE_COL

    show = sig.drop(columns=["role_key", "kind"])
    rep.table(
        show,
        "feature_signal",
        "Signal of every column against the target",
        "AUC: 0.5 = no signal, 1.0 = perfect (direction-free). Codes are scored per level on held-out folds, so a code is never mistaken for an ordered number. IV (information value): < 0.02 none · 0.02-0.1 weak · 0.1-0.3 medium · 0.3-0.5 strong · > 0.5 very strong. Scorecard IV bands were built for weaker signals: IV above 0.5 is common here simply because the label is easy to separate - use AUC ≥ 0.9 as the leak test.",
        fmt={a_col: "{:.3f}", naive_col: "{:.3f}", "Gini": "{:.3f}", "IV": "{:.3f}", "KS": "{:.2f}"},
        bars={a_col: (0.5, 1.0), naive_col: (0.5, 1.0), "IV": (0, 2), "KS": (0, 1)},
        max_rows=60,
        console=0,
    )

    # ── 6a. ranked chart ──────────────────────────────────────────────────────────────────────────────────────────────
    top = sig.head(30)
    cols_ = [ROLE_COLORS[r] for r in top["role_key"]]
    fig, ax = plt.subplots(figsize=(9.4, 0.27 * len(top) + 1.4))
    ax.hlines(range(len(top)), 0.5, top[a_col], color=cols_, lw=2.4, alpha=0.7)
    ax.scatter(top[a_col], range(len(top)), color=cols_, s=46, zorder=3, edgecolor=SURFACE, linewidth=0.8)
    ax.axvline(LEAK_AUC, color=INK2, ls="--", lw=1)
    ax.text(LEAK_AUC - 0.004, len(top) - 0.3, "too good to be true →", ha="right", va="bottom", fontsize=8, color=INK2)
    ax.set_yticks(
        range(len(top)), [f"{c}  [{ROLE_TAG[r]}]" for c, r in zip(top.index, top["role_key"], strict=True)], fontsize=8
    )
    ax.invert_yaxis()
    ax.set_xlim(0.5, 1.0)
    ax.set_xlabel("single-feature AUC (out-of-fold, category-aware; 0.5 = no signal)")
    ax.grid(axis="y", visible=False)
    legend_top(ax, ncol=3, handles=role_handles([r for r in ROLE_COLORS if r in set(top["role_key"])]))
    n_strong = int((sig[a_col] >= LEAK_AUC).sum())
    rep.figure(
        fig,
        "signal_ranking",
        "Which single columns predict the target best?",
        "One dot per column (top 30): how far right = the AUC if you used only that column, starting from 0.5 = no signal. Colour and the tag in brackets give the role we expect the column to play when modeling.",
        f"**{n_strong}** columns reach AUC ≥ {LEAK_AUC} on their own; the best is `{sig.index[0]}` ({sig[a_col].iloc[0]:.3f}). (Rule of thumb: the best single application-time variable usually has AUC {SINGLE_COLUMN_AUC[0]:.2f}-{SINGLE_COLUMN_AUC[1]:.2f}; above about {SINGLE_COLUMN_AUC[2]:.2f} look for leakage.)",
    )

    # ── 6b. the 'code read as number' trap ────────────────────────────────────────────────────────────────────────────────
    nom = sig[sig["kind"] == "nominal"][[a_col, naive_col]].copy()
    if len(nom):
        nom["difference"] = nom[naive_col] - nom[a_col]
        n_neg_diff = int((nom["difference"] < 0).sum())
        worst_loss = nom["difference"].idxmin()
        nom = nom.rename(columns={a_col: "AUC: each code its own category", naive_col: "AUC: code read as a number"})
        a_col_t, naive_col_t = "AUC: each code its own category", "AUC: code read as a number"
        rep.table(
            nom.sort_values("difference"),
            "code_as_number_trap",
            "Nominal codes: each code as a category vs read as a number",
            "A code such as a region or sector number is a label, not a quantity. Left: AUC when every code is its own category. Middle: AUC when the code is read as a number. A negative difference means the ORDER of the codes carries no meaning (use categories / target encoding); a positive one means the order happens to be informative here, which is a coincidence of how the codes were assigned.",
            fmt={a_col_t: "{:.3f}", naive_col_t: "{:.3f}", "difference": "{:+.3f}"},
        )
        rep.text(
            f"For {n_neg_diff} of the {len(nom)} nominal columns the code's ORDER carries no information; the biggest loss from reading a code as a number is `{worst_loss}` ({nom.loc[worst_loss, 'difference']:+.3f} AUC).",
            console=False,
        )

    # ── 6c. event-rate profiles of the best USABLE features ──────────────────────────────────────────────────────────────
    usable = sig[~sig["role_key"].isin(["tier0_target_defining", "incumbent_model_output"])].head(12)
    fig = draw_profile_grid(ctx, list(usable.index), usable[a_col], ncols=3)
    rep.figure(
        fig,
        "usable_feature_profiles",
        "How does the event rate move with the best columns that are not delinquency columns or the supplied score?",
        "Each panel: event rate (with 95% CI) across the bins / levels of one column that is NOT a delinquency column or the supplied score; the tag after the AUC is the column's role. Dashed = overall rate. Numeric columns run low → high (tick = upper edge of the first / middle / last bin; a separate first bin means value 0); categorical ones show each level.",
        "The best single columns outside the delinquency / score groups are "
        + ", ".join(f"`{c}` ({usable.loc[c, a_col]:.2f})" for c in usable.index[:3])
        + ". Look for steps (thresholds), U-shapes (trees find them, plain linear models do not) and flat stretches (no information).",
    )

    # ── 6d. non-monotone patterns: how much of the event-rate curve is NOT explained by the best monotone fit? ──────────────
    ctx.store["non_monotone"] = []
    nm = []
    for c in sig.index:
        if ctx.kinds[c] in ("nominal", "binary") or sig.loc[c, a_col] < 0.6:
            continue
        t, _ = rate_profile(ctx, c)
        t = t[t["events"] >= 10]  # a bin with ~1 expected event says nothing about its rate
        if len(t) >= 4:
            xs_, r_, w_ = np.arange(len(t)), t["rate"].to_numpy(), t["n"].to_numpy()
            total = float(np.average((r_ - np.average(r_, weights=w_)) ** 2, weights=w_))
            best = min(
                float(
                    np.average(
                        (r_ - IsotonicRegression(increasing=inc).fit(xs_, r_, sample_weight=w_).predict(xs_)) ** 2,
                        weights=w_,
                    )
                )
                for inc in (True, False)
            )
            if total > 0 and best / total > 0.15:
                nm.append(
                    {
                        "column": c,
                        "role": ROLE_TAG[ctx.roles[c]],
                        "AUC": sig.loc[c, a_col],
                        "share of the pattern a monotone curve misses": best / total,
                        "lowest event rate in the bin up to": t["rate"].idxmin(),
                        "highest event rate in the bin up to": t["rate"].idxmax(),
                    }
                )
    if nm:
        nmt = (
            pd.DataFrame(nm)
            .set_index("column")
            .sort_values("share of the pattern a monotone curve misses", ascending=False)
        )
        ctx.store["non_monotone"] = list(nmt.index)
        rep.table(
            nmt,
            "non_monotone",
            "Columns whose effect is NOT a simple 'more is riskier'",
            "Strong signal, but a rising-only or falling-only curve misses more than 15% of the pattern (isotonic fit): a linear / logistic model on the raw column will under-use it. Bin it, add flags, or use trees.",
            fmt={"AUC": "{:.3f}", "share of the pattern a monotone curve misses": lambda v: pct(v, 0)},
            bars={"share of the pattern a monotone curve misses": (0, 1)},
        )
        rep.finding(
            "info",
            f"{len(nmt)} columns have non-monotone effects",
            ", ".join(f"`{c}`" for c in nmt.index[:6]),
            "bin them or use tree-based models; do not rely on linear terms alone",
        )

    # ── 6e. where do the events live?  cohort concentration ─────────────────────────────────────────────────────────────────
    with rep.attempt("Cohort concentration"):
        pool_c = [
            c
            for c in sig.index
            if ctx.kinds[c] in ("binary", "nominal") and df[c].nunique() <= 7 and ctx.roles[c] == "origination_time"
        ]
        cohort_cols = pool_c[:5]  # few-level columns only, otherwise the cohorts become too small to mean anything
        lab = None
        if len(cohort_cols) >= 2:
            lab = df[cohort_cols[0]].map(lambda v: label_of(cohort_cols[0], v)).astype(str)
            for c_ in cohort_cols[1:]:
                lab = lab + " | " + df[c_].map(lambda v, c_=c_: label_of(c_, v)).astype(str)
        g = (
            pd.DataFrame({"cohort": lab, "y": y}).groupby("cohort")["y"].agg(rows="size", events="sum")
            if lab is not None
            else pd.DataFrame()
        )
        g = g[g["rows"] >= max(30, int(0.001 * ctx.n))] if len(g) else g
        if len(g):
            g["event rate"] = g["events"] / g["rows"]
            g["lift"] = g["event rate"] / ctx.base_rate
            g["share of all events"] = g["events"] / max(int(y.sum()), 1)
            g["share of rows"] = g["rows"] / ctx.n
            g = g.sort_values("event rate", ascending=False)
            cum_ev, cum_rows = g["share of all events"].cumsum(), g["share of rows"].cumsum()
            k50 = min(int(np.searchsorted(cum_ev.to_numpy(), 0.5) + 1), len(g))  # never past the last eligible cohort
            rep.table(
                g.head(12),
                "event_cohorts",
                "Where do the events live?  Cohorts over " + ", ".join(cohort_cols),
                f"Every combination of the {len(cohort_cols)} most informative low-cardinality columns known at origination (cohorts with enough rows), riskiest first. Each cohort name lists the values of `{' | '.join(cohort_cols)}` in this order.",
                fmt={
                    "event rate": lambda v: pct(v),
                    "lift": "{:.1f}x",
                    "share of all events": lambda v: pct(v),
                    "share of rows": lambda v: pct(v),
                    "rows": "{:,}",
                    "events": "{:,}",
                },
                bars={"share of all events": (0, 1), "event rate": (0, 1)},
                console=4,
            )
            conc = cum_rows.iloc[k50 - 1] <= 0.30
            rep.text(
                f"**Concentration.** The {k50} riskiest cohorts hold {pct(cum_rows.iloc[k50 - 1], 1)} of the loans and {pct(cum_ev.iloc[k50 - 1], 0)} of all events (out of {len(g)} cohorts). "
                + (
                    "Segment-level rules (or a segment-specific model) can therefore capture a lot with few loans reviewed."
                    if conc
                    else "Events are spread over many segments: segment rules alone will not do much."
                ),
                console=False,
            )
            ctx.store["cohorts"] = {
                "k50": k50,
                "rows_share": float(cum_rows.iloc[k50 - 1]),
                "events_share": float(cum_ev.iloc[k50 - 1]),
                "n_cohorts": len(g),
                "cols": cohort_cols,
            }

    # ── 6f. leaks, weak features, sensitive attributes ──────────────────────────────────────────────────────────────────────
    unexpected = sig[
        (sig[a_col] >= LEAK_AUC) & ~sig["role_key"].isin(["tier0_target_defining", "incumbent_model_output"])
    ]
    expected = sig[(sig[a_col] >= LEAK_AUC) & sig["role_key"].isin(["tier0_target_defining", "incumbent_model_output"])]
    if len(unexpected):
        rep.finding(
            "critical",
            "Columns assumed to be safe look like leaks",
            ", ".join(f"{c} ({v:.3f})" for c, v in unexpected[a_col].items()),
            "investigate these before trusting any model that uses them",
        )
    if len(expected):
        rep.text(
            f"**{len(expected)} columns reach AUC ≥ {LEAK_AUC} on their own** ({', '.join(f'`{c}` {v:.2f}' for c, v in expected[a_col].head(5).items())}{' …' if len(expected) > 5 else ''}): delinquency facts and the supplied score. "
            "Treat them as the label's fingerprints, not as predictors; section 8 shows how much signal remains without them."
        )
    weak = sig[(sig["IV"] < 0.02) & (sig[a_col] < 0.55)]
    if len(weak):
        rep.finding(
            "info",
            f"{len(weak)} columns carry (almost) no univariate signal",
            ", ".join(weak.index[:8]) + (" …" if len(weak) > 8 else ""),
            "candidates to drop; they may still help in interactions, so decide with permutation importance (section 9)",
        )
    sens_rows = []
    for c in [c for c in SENSITIVE if c in sig.index]:
        t = level_table(ctx, c) if ctx.plot_as[c] == "categorical" else rate_profile(ctx, c)[0]
        t = t[t["n"] >= 100]
        if len(t) >= 2 and t["rate"].min() > 0 and t["rate"].max() / t["rate"].min() >= 1.5:
            sens_rows.append(
                (
                    c,
                    float(t["rate"].max() / t["rate"].min()),
                    float(t["rate"].min()),
                    float(t["rate"].max()),
                    float(sig.loc[c, a_col]),
                    int(t.loc[t["rate"].idxmin(), "events"]),
                )
            )
    if sens_rows:
        bits = [
            f"`{c}`: {pct(lo)} vs {pct(hi)} ({r:.1f}x; AUC {a:.2f}; the lower group has {ne} events)"
            for c, r, lo, hi, a, ne in sens_rows
        ]
        rep.finding(
            "warning" if any(a >= 0.60 for *_, a, _ in sens_rows) else "info",
            "Sensitive attributes differ in event rate between groups",
            "; ".join(bits) + ". A group with few events (< 30) says little.",
            "Decide whether regulation / policy allows using them; the group audit in section 9 compares flag rates, with and without them.",
        )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 7 · Multivariate structure
# ══════════════════════════════════════════════════════════════════════════════════
@register(
    7,
    "Multivariate structure",
    "Which columns say the same thing, and how many independent pieces of information are there?",
)
def section_multivariate(ctx: Ctx) -> None:
    """Which columns say the same thing?  7a clustered correlation heatmap, 7b redundancy groups with a keeper, 7c VIF and PCA."""
    rep, df = ctx.rep, ctx.df
    cols = [c for c in ctx.features if ctx.kinds[c] != "nominal"]  # unordered codes have no meaningful correlation
    num = to_numeric_frame(df, cols, fill_missing=True)

    # ── 7a. clustered Spearman heatmap ────────────────────────────────────────────────────────────────────────────────
    corr = num.corr(method="spearman").fillna(0.0)
    dist = 1 - corr.abs().to_numpy()
    np.fill_diagonal(dist, 0.0)
    order = hierarchy.leaves_list(hierarchy.linkage(squareform((dist + dist.T) / 2, checks=False), method="average"))
    cs = corr.iloc[order, order]
    absr = corr.abs().to_numpy().copy()
    np.fill_diagonal(absr, 0)
    sig = signal_table(ctx)
    adj = csr_matrix(absr >= CORR_REDUNDANT)
    n_comp, lab = connected_components(adj, directed=False)
    groups = []
    auc_col = AUC_COL
    for g in range(n_comp):
        members = [cols[i] for i in np.flatnonzero(lab == g)]
        if len(members) < 2:
            continue
        sub = corr.loc[members, members].abs().to_numpy()
        usable = [m for m in members if ctx.roles[m] not in ("tier0_target_defining", "incumbent_model_output")]
        keep = max(usable, key=lambda m: sig.loc[m, auc_col]) if usable else "(none - all are leak-type columns)"
        groups.append(
            {
                "redundant group": ", ".join(sorted(members, key=lambda m: -sig.loc[m, auc_col])),
                "size": len(members),
                "weakest link |rho|": float(sub[np.triu_indices(len(members), 1)].min()),
                "suggested keeper": keep,
            }
        )
    biggest = max(groups, key=lambda g: g["size"]) if groups else None
    block = ""
    if biggest:
        fams = pd.Series([FAMILIES[family_of(m)][0] for m in biggest["redundant group"].split(", ")]).value_counts()
        block = f" The biggest block has {biggest['size']} columns, mostly from '{fams.index[0]}'."
    fig, ax = plt.subplots(figsize=(10.0, 9.0))
    im = ax.imshow(cs.to_numpy(), cmap=CMAP_DIV, vmin=-1, vmax=1)
    ax.set_xticks(range(len(cs)), cs.columns, rotation=90, fontsize=7.5)
    ax.set_yticks(range(len(cs)), cs.index, fontsize=7.5)
    ax.grid(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cb.set_label("Spearman correlation", fontsize=8.5)
    rep.figure(
        fig,
        "correlation_heatmap",
        "Which columns move together?",
        "Rank (Spearman) correlation between every pair of non-code columns, re-ordered so that similar columns sit next to each other: blocks of strong colour on the diagonal are groups of near-duplicates. Red = move together, blue = move opposite.",
        f"{int((absr >= CORR_REDUNDANT).sum() // 2)} pairs have |rho| ≥ {CORR_REDUNDANT}.{block} (Binary text columns are coded 0/1 in arbitrary direction, so only their strength is meaningful.)",
    )

    # ── 7b. redundancy groups + keeper recommendation ───────────────────────────────────────────────────────────────
    ctx.store["redundancy"] = {m.strip(): g["redundant group"] for g in groups for m in g["redundant group"].split(",")}
    if groups:
        gdf = pd.DataFrame(groups).sort_values("size", ascending=False).set_index("redundant group")
        rep.table(
            gdf,
            "redundancy_groups",
            f"Groups of (near-)duplicate columns (|rho| ≥ {CORR_REDUNDANT})",
            "Columns in the same group can replace each other. Linear models should keep one; trees do not care but their importance gets split among the twins.",
            fmt={"weakest link |rho|": "{:.2f}"},
            max_rows=15,
        )
        rep.finding(
            "info",
            f"{len(gdf)} groups of near-duplicate columns",
            f"largest: {gdf.index[0].split(', ')[0]} and {gdf['size'].iloc[0] - 1} others.",
            "keep one per group for linear / regularised models; for trees, expect importance to be shared among twins",
        )
    iu = np.triu_indices(len(cols), 1)
    pairs = pd.DataFrame({"a": [cols[i] for i in iu[0]], "b": [cols[j] for j in iu[1]], "rho": corr.to_numpy()[iu]})
    pairs = pairs.reindex(pairs["rho"].abs().sort_values(ascending=False).index).head(15).reset_index(drop=True)
    rep.table(
        pairs.set_index("a"),
        "top_correlated_pairs",
        "The 15 most correlated pairs",
        fmt={"rho": "{:+.3f}"},
        bars={"rho": (-1, 1)},
        max_rows=15,
    )

    # ── 7c. VIF + PCA on a log-transformed, standardised matrix ───────────────────────────────────────────────────────
    z = num.astype(float).copy()
    for c in z:
        if z[c].min() >= 0 and abs(skew_of(z[c])) > 1:
            z[c] = np.log1p(z[c])
    z = z.loc[:, z.std() > 0]
    z = (z - z.mean()) / z.std()
    r = np.corrcoef(z.to_numpy().T)
    vif = pd.Series(np.diag(np.linalg.pinv(r)), index=z.columns).sort_values(ascending=False)
    pca = PCA().fit(z.to_numpy())
    cum = np.cumsum(pca.explained_variance_ratio_)
    k90, k95 = int(np.searchsorted(cum, 0.90) + 1), int(np.searchsorted(cum, 0.95) + 1)
    n_vif10 = int((vif > 10).sum())

    top = vif.head(15)
    fig, a1 = plt.subplots(figsize=(8.8, 0.3 * len(top) + 1.5))
    hot = [v > 10 for v in top]
    a1.hlines(range(len(top)), 1, top.to_numpy(), color=[INK2 if h else NEUTRAL_LIGHT for h in hot], lw=2.4)
    a1.scatter(
        top.to_numpy(),
        range(len(top)),
        color=[INK2 if h else NEUTRAL for h in hot],
        s=46,
        zorder=3,
        edgecolor=SURFACE,
        linewidth=0.8,
    )
    a1.axvline(10, color=INK, ls="--", lw=1)
    a1.set_yticks(range(len(top)), top.index, fontsize=8)
    a1.invert_yaxis()
    a1.set_xscale("log")
    a1.set_xticks([1, 2, 5, 10, 20], ["1", "2", "5", "10", "20"])
    a1.set_xlim(0.9, max(float(top.max()) * 1.25, 12))
    a1.set_xlabel("variance inflation factor (log scale; dashed line = 10)")
    a1.grid(axis="y", visible=False)
    rep.figure(
        fig,
        "vif",
        "Which columns are almost copies of the others? (VIF)",
        "Variance inflation factor: how well each column is explained by all the others (1 = not at all). Above 10 (dashed line, dark dots) a column is nearly a linear combination of the rest.",
        f"**{n_vif10}** of {len(vif)} column{' has' if n_vif10 == 1 else 's have'} VIF > 10; the highest is `{vif.index[0]}` ({vif.iloc[0]:.1f}).",
    )

    fig, a2 = plt.subplots(figsize=(7.8, 4.2))
    a2.plot(range(1, len(cum) + 1), cum * 100, color=NEUTRAL, marker="o", ms=3.5)
    for k_, lvl in ((k90, 90), (k95, 95)):
        a2.axhline(lvl, color=MUTED, ls=":", lw=1)
        a2.plot([k_], [lvl], "o", color=INK, ms=6, zorder=4)
        a2.annotate(
            f"{k_} components → {lvl}%",
            (k_, lvl),
            xytext=(k_ + 1.5, lvl - 12),
            fontsize=8.5,
            color=INK2,
            arrowprops={"arrowstyle": "-", "color": MUTED},
        )
    a2.set_xlabel("number of principal components")
    a2.set_ylabel("cumulative variance explained (%)")
    a2.set_ylim(0, 102)
    rep.figure(
        fig,
        "pca",
        "How many independent pieces of information are there? (PCA)",
        "Principal components of the log-transformed, standardised columns: how many components are needed to capture most of the variance.",
        f"{k90} components capture 90% of the variance and {k95} capture 95% (of {len(cum)} columns)."
        + (
            " The data is lower-dimensional than its column count suggests."
            if k90 < 0.7 * len(cum)
            else " Most columns carry their own information: there is little to compress."
        ),
    )
    if n_vif10:
        rep.finding(
            "info",
            f"{n_vif10} column{'s' if n_vif10 != 1 else ''} with VIF > 10 (strongly collinear)",
            ", ".join(f"{c} ({v:.1f})" for c, v in vif[vif > 10].head(4).items()),
            "for logistic regression drop or combine them (or use ridge / elastic net); trees are fine",
        )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 8 · Leakage & feature roles
# ══════════════════════════════════════════════════════════════════════════════════


def state_tracking(ctx: Ctx, sig: pd.DataFrame, quiet: np.ndarray | None) -> dict | None:
    """Which columns follow the loan's delinquency STATE, not just the label?

    Both groups below are NON-events, so the label cannot explain any difference between them: 'look-alikes' carry as
    much arrears as the typical event (beyond the best single cut of section 4) but are not flagged; 'clean' loans have
    no arrears at all.  A column that separates the two follows the state of the loan - even if the dictionary says it
    is known at origination.  Returns None when the two groups are too small to say anything."""
    cut, y, df = ctx.store.get("best_cut"), ctx.y, ctx.df
    if not cut or not cut.get("column") or quiet is None or ctx.roles.get(cut["column"]) != "tier0_target_defining":
        return None
    flag = (df[cut["column"]] > cut["threshold"]) if cut["sign"] == ">" else (df[cut["column"]] <= cut["threshold"])
    look, clean = (y == 0) & flag.to_numpy(), (y == 0) & quiet
    if look.sum() < 60 or clean.sum() < 1000:
        return None
    idx = np.flatnonzero(look | clean)
    grp = look[idx].astype(int)
    t0, rows = set(ctx.cols(role="tier0_target_defining")), []
    for c in ctx.features:
        if c in t0:
            continue  # these define the groups
        x = df[c].iloc[idx].reset_index(drop=True)
        a = oof_signal_auc(x, grp, as_cat=ctx.kinds[c] in ("nominal", "binary"))
        r = {
            "column": c,
            "role": ROLES[ctx.roles[c]][0],
            "role_key": ctx.roles[c],
            "label AUC": float(sig.loc[c, AUC_COL]),
            "state AUC": a,
            "biggest level gap": np.nan,
            "level with the biggest gap": "",
        }
        # name the level that moves most, e.g. 'ECONOMIC_SECTORS = 0: 1% of clean loans, 39% of look-alikes'
        if df[c].nunique() <= 40:
            xs = df[c].iloc[idx]
            sh_look, sh_clean = xs[grp == 1].value_counts(normalize=True), xs[grp == 0].value_counts(normalize=True)
            gap = sh_look.sub(sh_clean, fill_value=0.0)
            lvl = gap.abs().idxmax()
            r["biggest level gap"] = float(gap.abs().max())
            r["level with the biggest gap"] = (
                f"{label_of(c, lvl)}: {pct(float(sh_clean.get(lvl, 0.0)), 0)} of clean vs {pct(float(sh_look.get(lvl, 0.0)), 0)} of look-alike loans"
            )
        rows.append(r)
    out = pd.DataFrame(rows).set_index("column").sort_values("state AUC", ascending=False)
    return {
        "table": out,
        "n_look": int(look.sum()),
        "n_clean": int(clean.sum()),
        "cut": cut,
        "trackers": [
            c
            for c in out.index
            if out.loc[c, "role_key"] == "origination_time"
            and (out.loc[c, "state AUC"] >= STATE_TRACKING_AUC or (out.loc[c, "biggest level gap"] >= STATE_LEVEL_GAP))
        ],
    }


@register(8, "Leakage & feature roles", "Which columns must NOT be used to predict - and how do I know?")
def section_leakage(ctx: Ctx) -> None:
    """Which columns must not be used - and how do we know?  8a role check, 8a2 state-tracking evidence map, 8b fingerprints / label recipe / loan age /
    exact values / score bucket, 8c the supplied score and PD as a benchmark."""
    rep, df, y = ctx.rep, ctx.df, ctx.y
    sig = signal_table(ctx)
    inc = ctx.cols(role="incumbent_model_output")
    dq_cols = [c for c in ctx.cols(role="tier0_target_defining") if ctx.kinds[c] in ("days", "count")]
    quiet = (df[dq_cols] == 0).all(axis=1).to_numpy() if dq_cols else None  # loans without any delinquency history
    if quiet is not None:
        ctx.store["quiet"] = quiet

    # ── 8a. do the dictionary-based roles survive contact with the data? ────────────────────────────────────────────
    rows = []
    for key, (name, meaning) in ROLES.items():
        members = [c for c in ctx.features if ctx.roles[c] == key]
        if not members:
            continue
        a = sig.loc[members, AUC_COL]
        med, mx, share = float(a.median()), float(a.max()), float((a >= LEAK_AUC).mean())
        if key == "tier0_target_defining":
            verdict = (
                "confirmed: strong fingerprints of the label"
                if med >= TIER0_CONFIRMED_AUC
                else "NOT confirmed: weaker than expected - re-check the role"
            )
        elif key == "incumbent_model_output":
            verdict = "confirmed: very predictive" if med >= TIER0_CONFIRMED_AUC else "less predictive than expected"
        elif key == "post_origination_state":
            verdict = "carries a lot of signal" if med >= 0.7 else "moderate signal"
        elif key == "origination_time":
            hot = [c for c in members if sig.loc[c, "IV"] > IV_SUSPICIOUS]
            if mx >= LEAK_AUC:
                verdict = "SUSPECT: " + ", ".join(a[a >= LEAK_AUC].index[:3])
            elif hot:
                verdict = f"NOT certified: no single column reaches AUC {LEAK_AUC}, but {len(hot)} have IV > {IV_SUSPICIOUS} (e.g. {', '.join(hot[:3])}) - their levels may encode the loan's state (see the state-tracking check below)"
            else:
                verdict = "no single column above the leak thresholds"
        elif key == "snapshot_unverified":
            verdict = "informative, but without dates it cannot be certified as pre-application"
        else:
            verdict = "informative - audit fairness"
        rows.append(
            {
                "role": name,
                "what it means": meaning,
                "columns": len(members),
                "median AUC": med,
                "max AUC": mx,
                "share above leak threshold": share,
                "verdict from the data": verdict,
            }
        )
    role_df = pd.DataFrame(rows).set_index("role")
    rep.table(
        role_df,
        "role_check",
        "Do the dictionary-based roles hold up against the data?",
        f"Roles come from the dictionary (what a column *means*); this table checks them against the data (how strongly each column predicts the target on its own; leak threshold AUC ≥ {LEAK_AUC}).",
        fmt={"median AUC": "{:.3f}", "max AUC": "{:.3f}", "share above leak threshold": lambda v: pct(v, 0)},
        bars={"median AUC": (0.5, 1), "max AUC": (0.5, 1)},
        console=8,
    )
    t0 = ctx.cols(role="tier0_target_defining")
    if t0:
        med = float(sig.loc[t0, AUC_COL].median())
        fp_cols = [c for c in t0 if sig.loc[c, AUC_COL] >= LEAK_AUC]
        weak = sig.loc[t0, AUC_COL].sort_values().head(2)
        weak_txt = (
            "; the weakest are " + ", ".join(f"`{c}` ({v:.2f})" for c, v in weak.items() if v < 0.8)
            if (weak < 0.8).any()
            else ""
        )
        ctx.store["tier0"] = {
            "n": len(t0),
            "n_fingerprints": len(fp_cols),
            "median_auc": med,
            "max_auc": float(sig.loc[t0, AUC_COL].max()),
        }
        rep.finding(
            "critical" if med >= TIER0_CONFIRMED_AUC else "info",
            f"{len(fp_cols)} of {len(t0)} delinquency / penalty columns are fingerprints of the label (single-column AUC ≥ {LEAK_AUC})"
            if med >= TIER0_CONFIRMED_AUC
            else "Delinquency columns are weaker than expected",
            f"median single-column AUC {med:.2f}, max {sig.loc[t0, AUC_COL].max():.2f}{weak_txt}. The dictionary does not define the status, but the data show it travels with heavy arrears recorded at the same snapshot (overdue amounts, days past due, penalties).",
            "In a prediction track, exclude them: a model that uses them detects the status instead of predicting it. Use them only to understand the label, or in a status-detection / triage track and only if the organisers confirm they exist when you must score.",
            priority=1,
        )

    # ── 8a2. do the 'known at origination' columns follow the loan's STATE?  (look-alike vs clean non-events) ──────────────
    with rep.attempt("State-tracking check"):
        stt = state_tracking(ctx, sig, quiet)
        if stt:
            tab = stt["table"]
            ctx.store["state_track"] = stt
            fig, ax = plt.subplots(figsize=(8.6, 6.2))
            for role, g in tab.groupby("role_key"):
                ax.scatter(
                    g["state AUC"],
                    g["label AUC"],
                    color=ROLE_COLORS[role],
                    s=46,
                    edgecolor=SURFACE,
                    linewidth=0.8,
                    zorder=3,
                )
            ax.axvline(STATE_TRACKING_AUC, color=INK2, ls="--", lw=1)
            ax.axhline(LEAK_AUC, color=INK2, ls=":", lw=1)
            ax.set_xlim(0.45, 1.0)
            ax.set_ylim(0.45, 1.0)
            for c in tab.index[:6].union(tab.sort_values("label AUC", ascending=False).index[:3]):
                ax.annotate(
                    c,
                    (tab.loc[c, "state AUC"], tab.loc[c, "label AUC"]),
                    xytext=(5, 4),
                    textcoords="offset points",
                    fontsize=7.8,
                    color=INK2,
                )
            ax.set_xlabel("follows the loan's state: AUC separating look-alike non-events from clean non-events")
            ax.set_ylabel("predicts the label: single-column AUC")
            ax.grid(True)
            legend_top(ax, ncol=3, handles=role_handles([r for r in ROLE_COLORS if r in set(tab["role_key"])]))
            trk = stt["trackers"]
            rep.figure(
                fig,
                "state_tracking",
                "Which columns follow the loan's state, not just the label?",
                f"Both groups are NON-events, so the label cannot explain the difference: {stt['n_look']:,} 'look-alikes' (`{stt['cut']['column']}` {stt['cut']['sign']} {stt['cut']['threshold']:,.4g}, as heavy as a typical event but not flagged) against {stt['n_clean']:,} clean loans (no arrears at all). "
                f"Right of the dashed line a column separates them, i.e. it follows the loan's arrears state; the dotted line marks AUC {LEAK_AUC}. Delinquency columns are left out (they define the groups).",
                (
                    f"**{len(trk)}** columns the dictionary calls 'known at origination' follow the state: "
                    + ", ".join(f"`{c}` ({tab.loc[c, 'state AUC']:.2f})" for c in trk[:5])
                    + "."
                    if trk
                    else "No origination column separates the two groups strongly."
                ),
            )
            rep.table(
                tab.head(15).drop(columns=["role_key"]),
                "state_tracking",
                "The columns that follow the loan's state most",
                f"State AUC = how well the column separates look-alike from clean non-events (0.5 = not at all). A single code can move a lot without a high AUC, so the table also gives the biggest gap in a level's share (a column counts as following the state at state AUC ≥ {STATE_TRACKING_AUC} or a gap ≥ {pct(STATE_LEVEL_GAP, 0)}).",
                fmt={
                    "label AUC": "{:.3f}",
                    "state AUC": "{:.3f}",
                    "biggest level gap": lambda v: "–" if pd.isna(v) else pct(v, 0),
                },
                bars={"state AUC": (0.5, 1)},
                console=0,
            )
            if trk:
                rep.finding(
                    "warning",
                    f"{len(trk)} columns marked 'known at origination' follow the loan's delinquency state",
                    ", ".join(
                        f"`{c}` (state AUC {tab.loc[c, 'state AUC']:.2f}; {tab.loc[c, 'level with the biggest gap']})"
                        if tab.loc[c, "level with the biggest gap"]
                        else f"`{c}` (state AUC {tab.loc[c, 'state AUC']:.2f})"
                        for c in trk[:4]
                    ),
                    "Treat them as state indicators (fields blanked or switched as a loan goes bad), not as application facts: the hand-off table lists a stricter application set (in_A_strict) without them.",
                    priority=2,
                )

    # ── 8b. what do the label's fingerprints look like? ────────────────────────────────────────────────────────────────
    leaky = sig[sig["role_key"].isin(["tier0_target_defining", "incumbent_model_output"])].head(8)
    if len(leaky):
        fig = draw_profile_grid(ctx, list(leaky.index), leaky[AUC_COL], ncols=4)
        rep.figure(
            fig,
            "label_fingerprints",
            "What the leaky columns look like against the event rate",
            "Same panels as section 6, for the strongest delinquency / incumbent-model columns. A near-vertical wall from ~0% to a high rate is the signature of a column that (almost) defines the label.",
            _fingerprint_takeaway(ctx, list(leaky.index)),
        )

    # ── 8b2. the label's recipe: how much of the target can a tiny rule set rebuild from the delinquency columns? ──────────
    dcols = [c for c in t0 if is_num(df[c])]
    if len(dcols) >= 2:
        with rep.attempt("Label recipe"):
            xd = df[dcols].astype(float)
            prec_min = 0.5
            tp = fp = fn = 0
            for tr, te in StratifiedKFold(N_FOLDS, shuffle=True, random_state=SEED).split(xd, y):
                tr_tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=50, random_state=SEED).fit(
                    xd.iloc[tr], y[tr]
                )
                lv_tr = tr_tree.apply(xd.iloc[tr])
                good = [n_ for n_ in np.unique(lv_tr) if y[tr][lv_tr == n_].mean() >= prec_min]
                flag = np.isin(tr_tree.apply(xd.iloc[te]), good)
                tp += int((flag & (y[te] == 1)).sum())
                fp += int((flag & (y[te] == 0)).sum())
                fn += int((~flag & (y[te] == 1)).sum())
            prec, rec = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
            full = DecisionTreeClassifier(max_depth=3, min_samples_leaf=50, random_state=SEED).fit(xd, y)
            leaves = tree_rules(full, xd, y)
            top_leaves = leaves[leaves["precision"] >= prec_min].sort_values("events", ascending=False).head(6)
            rep.table(
                top_leaves.set_index("rule"),
                "label_recipe",
                "The label's recipe: the leaves of a depth-3 tree on the delinquency columns",
                f"A tiny rule set built only from delinquency columns. Leaves with precision ≥ {pct(prec_min, 0)} are shown, biggest first.",
                fmt={
                    "precision": lambda v: pct(v),
                    "share of all events": lambda v: pct(v),
                    "rows": "{:,}",
                    "events": "{:,}",
                },
                bars={"precision": (0, 1), "share of all events": (0, 1)},
                console=4,
            )
            ctx.store["recipe"] = {
                "precision": prec,
                "recall": rec,
                "top_rule": top_leaves["rule"].iloc[0] if len(top_leaves) else "",
            }
            cut = ctx.store.get("best_cut")
            vs_cut = (
                f" For comparison the best single cut (`{cut['column']}` {cut['sign']} {cut['threshold']:,.4g}) has precision {pct(cut['precision'])} and recall {pct(cut['recall'])}: the tree is {'more precise but finds fewer events' if prec > cut['precision'] and rec < cut['recall'] else 'about as good'}."
                if cut and cut.get("column")
                else ""
            )
            rep.text(
                f"**How much of the label do a handful of delinquency rules rebuild?** Out-of-fold, flagging every loan that falls in a leaf with precision ≥ {pct(prec_min, 0)} gives precision **{pct(prec)}** and recall **{pct(rec)}**.{vs_cut} "
                + (
                    f"So most of the label can be rebuilt from the delinquency history, yet {pct(1 - rec, 0)} of the events fall outside every such rule: the label is a loan state, not a single threshold."
                    if prec >= 0.7 and rec >= 0.6
                    else "So a few simple rules reproduce only part of the label: it is something richer than one threshold."
                )
            )

    # ── 8b2b. how far through its contract is the loan? (a loan's age is not in the data, but its term and remaining term are) ─
    if {"RemainingTerm", "ContractTermMonth"} <= set(df.columns):
        with rep.attempt("Loan age"):
            term_days = df["ContractTermMonth"].to_numpy(float) * 30.4375
            rt = df["RemainingTerm"].to_numpy(float)
            with np.errstate(divide="ignore", invalid="ignore"):
                used = np.where(term_days > 0, 1 - rt / term_days, np.nan)
            order = [
                "matured",
                "used ≥ 90%",
                "used 70-90%",
                "used 50-70%",
                "used 25-50%",
                "used < 25%",
                "days left > term",
            ]
            grp = np.select(
                [rt <= 0, rt > term_days, used >= 0.9, used >= 0.7, used >= 0.5, used >= 0.25],
                [order[0], order[6], order[1], order[2], order[3], order[4]],
                default=order[5],
            )
            fig, t_age = count_profile(
                ctx, pd.Categorical(grp, categories=order, ordered=True), "share of the contract term already used"
            )
            old = (grp == order[0]) | (grp == order[1])
            ev_old, ne_old = float(old[y == 1].mean()), float(old[y == 0].mean())
            ctx.store["loan_age"] = {"events_old": ev_old, "non_events_old": ne_old}
            rep.figure(
                fig,
                "loan_age",
                "How far through its contract is the loan?",
                "Share of the contract term already used = 1 - RemainingTerm / (ContractTermMonth x 30.44 days); 'matured' = no days left, 'days left > term' = more days left than the contract length (a term mismatch, e.g. revolving products). The loan's own age is not in the data, but this gives a rough picture. Left: how many loans per group. Right: their event rate.",
                f"{pct(ev_old, 0)} of the events have used at least 90% of their term (or are matured), against {pct(ne_old, 0)} of the non-events: the label sits on loans near the end of their life, which is why balance and term columns carry so much signal.",
            )

    # ── 8b3. exact values that (almost) guarantee the label ──────────────────────────────────────────────────────────────
    fp_rows = []
    for c in [*t0, *inc]:
        v = df[c]
        if not is_num(v) or (v.dropna() % 1 != 0).any():
            continue
        g = pd.DataFrame({"v": v.to_numpy(), "y": y}).groupby("v")["y"].agg(n="size", events="sum")
        present = g.index.to_numpy(float)
        top = float(v.max())
        for val, r in g[(g["n"] >= 60) & (g["events"] / g["n"] >= 0.6)].iterrows():
            nxt = present[present > val]
            note = ""
            if val >= 0.98 * top > 0:
                note = "at the column's ceiling"  # chronic cases legitimately pile up at the cap of a bounded column
            elif 0 < val <= 1 and len(nxt) and nxt.min() >= 20:
                note = f"flag-like: next value is {nxt.min():,.0f}"
            fp_rows.append(
                {
                    "column": c,
                    "value": float(val),
                    "rows": int(r["n"]),
                    "event rate": float(r["events"] / r["n"]),
                    "share of all events": float(r["events"] / max(y.sum(), 1)),
                    "note": note,
                }
            )
    if fp_rows:
        fdf = pd.DataFrame(fp_rows).sort_values("share of all events", ascending=False).set_index("column")

        def covered_by(mask: pd.Series) -> np.ndarray:
            hit = np.zeros(ctx.n, bool)
            for c in set(fdf.index[mask.to_numpy()]):
                sub_ = fdf.loc[[c]]
                hit |= df[c].isin(sub_.loc[mask.to_numpy()[fdf.index == c], "value"].to_numpy()).to_numpy()
            return hit

        ceiling = fdf["note"].eq("at the column's ceiling")
        covered = covered_by(~ceiling) if (~ceiling).any() else np.zeros(ctx.n, bool)
        rep.table(
            fdf.head(15),
            "fingerprint_values",
            "Exact values with an unusually high event rate",
            "Values of integer delinquency / score columns that hold ≥ 60 rows with an event rate ≥ 60%. A pile-up at the cap of a bounded column (e.g. days in the last year) is natural; values elsewhere are not. The same loan can sit in several rows, so the shares of events do not add up.",
            fmt={
                "value": lambda v: num(v),
                "event rate": lambda v: pct(v),
                "share of all events": lambda v: pct(v),
                "rows": "{:,}",
            },
            bars={"share of all events": (0, 1)},
            console=5,
        )
        cov_ev, cov_prec = float(y[covered].sum() / max(y.sum(), 1)), float(y[covered].mean()) if covered.any() else 0.0
        ctx.store["fingerprints"] = {
            "n_values": int((~ceiling).sum()),
            "n_ceiling": int(ceiling.sum()),
            "events_covered": cov_ev,
            "precision": cov_prec,
        }
        if (~ceiling).any():
            rep.finding(
                "warning",
                "Delinquency histories look templated",
                f"{int((~ceiling).sum())} exact values away from any column ceiling appear far too often; flagging any row with one of them catches {pct(cov_ev, 0)} of all events at {pct(cov_prec, 0)} precision (the values were picked on these same rows, so read it as an upper bound)"
                + (
                    f". A further {int(ceiling.sum())} values sit at a column's ceiling, which is natural for chronic cases and is not counted here."
                    if ceiling.any()
                    else "."
                ),
                "A model will learn these fingerprints: great leaderboard scores here are not evidence of real predictive power",
                priority=2,
            )

    # ── 8b4. does the score / PD 'bucket' follow delinquency or the label? ───────────────────────────────────────────────
    sc_cols = [c for c in inc if ctx.kinds[c] == "score"]
    if sc_cols and t0:
        sc = sc_cols[0]
        lead = sig.loc[t0, AUC_COL].idxmax()
        # the interior band of values where events pile up (a sentinel, not a floor)
        bucket = high_event_band(df[sc], y)
        top_d = (df[lead] >= df[lead].quantile(0.90)).to_numpy()
        rows_f = []
        for name, mk in (f"top 10% of {lead}", top_d), ("everyone else", ~top_d):
            for cls in (0, 1):
                m = mk & (y == cls)
                if m.sum() >= 20:
                    rows_f.append(
                        {
                            "population": name,
                            "target": cls,
                            "rows": int(m.sum()),
                            f"median {sc}": float(df.loc[m, sc].median()),
                            f"share inside the {sc} high-event bucket": float(bucket[m].mean()),
                        }
                    )
        if rows_f and bucket.any():
            fl = pd.DataFrame(rows_f).set_index("population")
            rep.table(
                fl,
                "score_bucket_follows",
                f"Does the {sc} 'high-event bucket' follow the delinquency history or the label?",
                f"Among loans with the heaviest `{lead}`, compare events with non-events. If both sit in the bucket, the supplied score puts loans there because of their delinquency state, not because of the label.",
                fmt={
                    f"share inside the {sc} high-event bucket": lambda v: pct(v),
                    "rows": "{:,}",
                    f"median {sc}": lambda v: num(v),
                },
            )

    # ── 8c. the supplied score / PD ───────────────────────────────────────────────────────────────────────────────────
    if inc:
        band_rows = []
        for c in inc:
            vc = df[c].value_counts(normalize=True)
            for v, share in vc[vc >= 0.02].head(4).items():
                m = (df[c] == v).to_numpy()
                band_rows.append(
                    {
                        "column": c,
                        "value": float(v),
                        "share of rows": float(share),
                        "event rate there": float(y[m].mean()),
                        "share of ALL events": float(y[m].sum() / max(y.sum(), 1)),
                    }
                )
        if band_rows:
            with rep.attempt("Anatomy of the supplied score"):
                cols_ = [c for c in inc if df[c].nunique() > 20][:2]
                fig, axes = plt.subplots(1, len(cols_), figsize=(5.2 * len(cols_), 3.9), squeeze=False)
                notes = []
                for ax, c in zip(axes.ravel(), cols_, strict=False):
                    v = df[c].to_numpy(float)
                    bins = np.linspace(v.min(), v.max(), 70)
                    ax.hist(v, bins=bins, color=NEUTRAL_LIGHT, label="all loans")
                    ax.hist(v[y == 1], bins=bins, color=C1, alpha=0.85, label="events")
                    ax.set_yscale("log")
                    band = high_event_band(df[c], y)
                    if (band & (y == 1)).sum() >= 20:
                        # the core of the band: where 90% of its events sit
                        lo_b, hi_b = (float(q_) for q_ in np.quantile(v[band & (y == 1)], [0.05, 0.95]))
                        core = (v >= lo_b) & (v <= hi_b)
                        ax.axvspan(lo_b, hi_b, color=INK2, alpha=0.18, lw=0)
                        notes.append(
                            f"`{c}` {num(lo_b)}-{num(hi_b)} holds {pct(float(core.mean()), 0)} of the loans and {pct(float(y[core].sum() / max(y.sum(), 1)), 0)} of the events"
                        )
                    ax.set_xlabel(c)
                    ax.set_ylabel("loans (log scale)")
                legend_top(
                    axes.ravel()[0],
                    ncol=3,
                    handles=[
                        Patch(facecolor=NEUTRAL_LIGHT, label="all loans"),
                        Patch(facecolor=C1, label="events"),
                        Patch(facecolor=INK2, alpha=0.3, label="band where events pile up"),
                    ],
                )
                fig.tight_layout()
                rep.figure(
                    fig,
                    "score_anatomy",
                    "Anatomy of the supplied score and PD",
                    "Histogram of each supplied column on a log scale: grey = all loans, orange = events. The shaded band is the core of the value range where the event rate is at least 5 times the overall rate (where 90% of that range's events sit).",
                    ("; ".join(notes) + ". " if notes else "")
                    + "A smooth model output would not pile its events into one narrow band: this looks like a recoding of the delinquency state (section 8 checks it below).",
                )
            rep.table(
                pd.DataFrame(band_rows).set_index("column"),
                "incumbent_bands",
                "Piles of values in the supplied score / PD (values holding ≥ 2% of rows)",
                "A model output normally has a smooth distribution. A value that holds a large share of rows AND a large share of all events is a bucket (sentinel) the model assigns to troubled loans; it can sit in the middle of the range, not only at an end.",
                fmt={
                    "value": lambda v: num(v),
                    "share of rows": lambda v: pct(v),
                    "event rate there": lambda v: pct(v),
                    "share of ALL events": lambda v: pct(v),
                },
                console=6,
            )
        pops = [("everyone", np.ones(ctx.n, bool))]
        if quiet is not None and quiet.sum() > 1000 and y[quiet].sum() >= 30:
            pops.append(("loans with NO delinquency history", quiet))
        band = np.zeros(ctx.n, bool)
        for c in inc:
            band |= high_event_band(df[c], y)
        if 0 < band.sum() < ctx.n and (~band).sum() > 1000 and y[~band].sum() >= 30:
            pops.append((f"outside the high-event bands ({pct(band.mean(), 0)} of loans are inside)", ~band))
            if y[band].sum() >= 30 and (1 - y[band]).sum() >= 30:
                pops.append(("inside the high-event bands", band))
        bench = []
        for name, m in pops:
            r = {"population": name, "rows": int(m.sum()), "events": int(y[m].sum())}
            for c in inc:
                a = safe_auc(y[m], df.loc[m, c].to_numpy(float))
                r[f"AUC of {c}"] = max(a, 1 - a)
            bench.append(r)
        bdf = pd.DataFrame(bench).set_index("population")
        rep.table(
            bdf,
            "incumbent_benchmark",
            "How good is the supplied score as a benchmark - and why?",
            "If the supplied score is much weaker where delinquency information is absent, then most of its power IS arrears information, and beating it overall is meaningless.",
            fmt={"rows": "{:,}", **{c: "{:.3f}" for c in bdf.columns if c.startswith("AUC")}},
            console=3,
        )
        aucs = [c for c in bdf.columns if c.startswith("AUC")]
        direction = [
            f"`{c}`: {'lower' if safe_auc(y, df[c].to_numpy(float)) < 0.5 else 'higher'} value = more events"
            for c in inc
        ]
        sc_, pd_ = (next((c for c in inc if ctx.kinds[c] == k), None) for k in ("score", "rate"))
        both = (
            f" `{sc_}` and `{pd_}` rank loans almost identically (|Spearman| = {abs(float(stats.spearmanr(df[sc_], df[pd_])[0])):.2f}), so they are one model seen twice."
            if sc_ and pd_
            else ""
        )
        rep.text(
            "**Direction** (all AUCs in this report ignore direction, so check it before using a column): "
            + "; ".join(direction)
            + f".{both}",
            console=False,
        )
        if len(bdf) >= 2 and aucs:
            all_, q_ = float(bdf.iloc[0][aucs].max()), float(bdf.iloc[1][aucs].max())
            ctx.store["incumbent_auc"] = (all_, q_)
            if len(bdf) >= 3:
                ctx.store["incumbent_auc_outside_band"] = float(bdf.iloc[2][aucs].max())
            n_q, ev_q = int(bdf.iloc[1]["rows"]), int(bdf.iloc[1]["events"])
            half_q = 1.96 * auc_se(max(q_, 0.5), ev_q, max(n_q - ev_q, 1))
            ctx.store["incumbent_quiet"] = {"rows": n_q, "events": ev_q, "half_width": half_q}
            rep.finding(
                "warning" if q_ < all_ - 0.1 else "info",
                "The supplied score / PD is driven by arrears information"
                if q_ < all_ - 0.1
                else "The supplied score holds up without delinquency history",
                f"best AUC among the supplied columns is {all_:.3f} overall but {q_:.3f} (±{half_q:.2f}) among the {n_q:,} loans with no delinquency history, which contain only {ev_q} events.",
                "Use it as a benchmark only on the 'no delinquency history' population, never as a feature when predicting the status."
                if q_ < all_ - 0.1
                else "it may be usable as a feature; verify availability at prediction time",
                priority=2,
            )
        pdc = next((c for c in inc if ctx.kinds[c] == "rate" and df[c].max() <= 100 and df[c].max() > 1), None)
        if pdc:
            ctx.store["pd_mean"] = (pdc, float(df[pdc].mean()))
            dec = pd.qcut(df[pdc].rank(method="first"), 10, labels=False)
            g = pd.DataFrame({"pd": df[pdc], "y": y}).groupby(dec.to_numpy()).agg(pd=("pd", "mean"), y=("y", "mean"))
            fig, ax = plt.subplots(figsize=(5.6, 5.0))
            ax.plot([0, 100], [0, 100], color=MUTED, ls="--", lw=1, label="perfect calibration")
            ax.plot(g["pd"], g["y"] * 100, "o-", color=NEUTRAL, label="observed event rate by PD decile")
            # most deciles sit near 0, so a linear axis would pile them in a corner
            ax.set_xscale("symlog", linthresh=1)
            ax.set_yscale("symlog", linthresh=1)
            ax.set_xticks([0, 1, 10, 100], ["0", "1", "10", "100"])
            ax.set_yticks([0, 1, 10, 100], ["0", "1", "10", "100"])
            ax.set_xlabel(f"{pdc} (mean per decile, %, log-like scale)")
            ax.set_ylabel("observed event rate (%, log-like scale)")
            ax.set_xlim(0, 100)
            ax.set_ylim(0, 100)
            legend_top(ax, ncol=1)
            rep.figure(
                fig,
                "pd_calibration",
                f"Is `{pdc}` a calibrated probability of THIS target?",
                "If the points sat on the dashed diagonal, a PD of 20% would mean 20% of such loans have the event.",
                f"Mean {pdc} is {df[pdc].mean():.1f}% but the observed event rate is {pct(ctx.base_rate, 2)}. Used as a probability its Brier score is {brier_score_loss(y, df[pdc] / 100):.4f} versus {ctx.base_rate * (1 - ctx.base_rate):.4f} for simply guessing the event rate: a PD is only comparable with a label that has the same default definition and horizon, and none is stated for this target, so do not read it as this target's probability.",
            )
        sc = next((c for c in inc if ctx.kinds[c] == "score"), None)
        if sc and not ctx.args.no_models:
            with rep.attempt("Reverse-engineering the score"):
                rows_ = stratified_rows(ctx, 12_000 if ctx.args.fast else 30_000)
                tgt = df[sc].to_numpy(float)[rows_]
                res = {}
                for label, cols in (
                    (
                        "application-time + balance columns (no delinquency)",
                        [
                            c
                            for c in ctx.features
                            if ctx.roles[c] in ("origination_time", "sensitive_attribute", "post_origination_state")
                        ],
                    ),
                    ("delinquency columns only", t0),
                ):
                    x, cat = model_frame(ctx, cols)
                    x = x.iloc[rows_].reset_index(drop=True)
                    pred = np.zeros(len(x))
                    for tr, te in KFold(3, shuffle=True, random_state=SEED).split(x):
                        pred[te] = (
                            HistGradientBoostingRegressor(
                                max_iter=100, random_state=SEED, categorical_features=cat or None
                            )
                            .fit(x.iloc[tr], tgt[tr])
                            .predict(x.iloc[te])
                        )
                    res[label] = float(r2_score(tgt, pred))
                rep.text(
                    f"**Can the score be reproduced from the other columns?** Predicting `{sc}` (3-fold R²): from {list(res)[0]} → **{list(res.values())[0]:.2f}**; from {list(res)[1]} → **{list(res.values())[1]:.2f}**."
                )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 9 · Baseline signal check
# ══════════════════════════════════════════════════════════════════════════════════
def draw_dots(
    ax: plt.Axes,
    labels: Sequence[str],
    values: Sequence[float],
    *,
    base: float = 0.5,
    color=NEUTRAL,
    xerr=None,
    notes: Sequence[str] | None = None,
    fmt: str = "{:.3f}",
) -> None:
    """Lollipop anchored at a meaningful baseline (0.5 = chance for an AUC) instead of a truncated bar."""
    y_ = np.arange(len(values))
    ax.hlines(y_, base, values, color=color, lw=2.2, alpha=0.55)
    ax.scatter(values, y_, color=color, s=46, zorder=3, edgecolor=SURFACE, linewidth=0.8)
    ax.axvline(base, color=MUTED, ls="--", lw=1)
    ax.set_yticks(y_, labels, fontsize=8)
    if not ax.yaxis_inverted():  # idempotent: two panels that share a y axis must not flip it back
        ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    for i, v in enumerate(values):
        txt = notes[i] if notes is not None else fmt.format(v)
        ax.text(v + 0.008, i, txt, va="center", fontsize=8, color=INK2)


def draw_hbars(
    ax: plt.Axes, labels: Sequence[str], values: Sequence[float], *, color=NEUTRAL, fmt: str = "{:.3f}"
) -> None:
    ax.barh(range(len(values)), values, color=color, height=0.62)
    ax.set_yticks(range(len(values)), labels, fontsize=8)
    if not ax.yaxis_inverted():
        ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    span = max(max(values), 0) - min(min(values), 0)
    for i, v in enumerate(values):
        ax.text(
            max(v, 0) + 0.01 * span, i, "0.000" if abs(v) < 5e-4 else fmt.format(v), va="center", fontsize=8, color=INK2
        )


@register(
    9, "Baseline signal check", "If I model this today, how well can I do - and which information is doing the work?"
)
def section_baseline(ctx: Ctx) -> None:
    """If I model this today?  The AUC ladder (9a), event arms (9a2), family ablation (9b), dead ends, run-off signature and markers (9c), permutation importance
    and signal concentration (9d), linear vs trees (9e), calibration and gains (9f), learning curve (9g), group audit (9h).  Needs models: skipped with --no-models."""
    rep, df, y, feats = ctx.rep, ctx.df, ctx.y, ctx.features
    if ctx.args.no_models:
        rep.callout("info", "Skipped because of `--no-models`.")
        return
    fast = ctx.args.fast
    kw, folds = model_settings(ctx)
    fs = feature_sets(ctx)
    inc, t0, sens, orig, snap = fs["inc"], fs["t0"], fs["sens"], fs["A"], fs["snap"]
    spec = [  # (key, label, columns)
        ("all", "1  everything", feats),
        ("no_inc", "2  minus the supplied score / PD", [c for c in feats if c not in inc]),
        ("no_del", "3  minus delinquency & penalty columns", [c for c in feats if c not in inc and c not in t0]),
        ("no_state", "4  minus balances, schedule & loan events", orig + snap + sens),
        ("strict", "5  minus bureau snapshot columns (application-time + sensitive attributes)", orig + sens),
        ("strict_ns", "6  minus sensitive attributes (= application-time only)", orig),
    ]
    kept: list[tuple[str, str, list[str]]] = []
    aliases: dict[str, str] = {}
    # a rung with the same columns as the previous one adds nothing: it becomes an alias of it
    for key, label, cols in spec:
        if not cols:
            continue
        if kept and set(cols) == set(kept[-1][2]):
            aliases[key] = kept[-1][1]
        else:
            kept.append((key, label, cols))
    res: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for key, label, cols in kept:
        res[label] = by_key[key] = eval_set(ctx, cols, folds=folds, **kw)
        rep.say(f"   · {label}: AUC {res[label]['auc']:.3f} ± {res[label]['std']:.3f}  ({len(cols)} columns)")
    ctx.store["ladder_keys"] = {**{key: label for key, label, _ in kept}, **aliases}
    refs: dict[str, dict] = {}  # reference points that are NOT steps of the ladder
    ref_specs = [
        ("delinq_alone", "(for reference) delinquency columns ALONE", t0),
        (
            "profile_alone",
            "(for reference) borrower profile ALONE: customer + income columns",
            [c for c in feats if family_of(c) in ("customer", "income")],
        ),
        (
            "weak_alone",
            "(for reference) age, income, amount, rate, term ALONE",
            [
                c
                for c in (
                    "Age",
                    "INCOME_AMOUNT_TOTAL",
                    "INCOME_AMOUNT_SOI04",
                    "DISBURSED_AMOUNT",
                    "INTEREST_RATE_NOMINAL",
                    "ContractTermMonth",
                )
                if c in feats
            ],
        ),
    ]
    for key, label, cols in ref_specs:
        if len(cols) >= 3:
            refs[label] = by_key[key] = eval_set(ctx, cols, folds=folds, **kw)
            ctx.store["ladder_keys"][key] = label
    allres = {**res, **refs}
    ctx.store["ladder"] = allres

    fig, (a1, a2) = plt.subplots(
        1, 2, figsize=(10.4, 0.6 * len(allres) + 1.7), sharey=True, gridspec_kw={"width_ratios": [1.15, 1]}
    )
    labels = ["\n".join(textwrap.wrap(k, 34)) for k in allres]
    draw_dots(
        a1,
        labels,
        [v["auc"] for v in allres.values()],
        notes=[f"{v['auc']:.3f} ± {v['std']:.3f}" for v in allres.values()],
    )
    a1.set_xlim(0.48, 1.12)
    a1.set_xlabel("ROC-AUC (5-fold CV, ± std over folds)")
    a1.set_title("ranking quality (dashed = chance)", fontsize=9.5)
    draw_hbars(a2, labels, [v["ap"] for v in allres.values()])
    a2.axvline(ctx.base_rate, color=INK, ls="--", lw=1)
    a2.set_xlim(0, 1.12)
    a2.set_xlabel("PR-AUC (average precision)")
    a2.set_title("precision on the rare class (dashed = event rate)", fontsize=9.5)
    fig.tight_layout()
    seq = [v["auc"] for v in res.values()]
    drops = [(seq[i] - seq[i + 1], list(res)[i + 1]) for i in range(len(seq) - 1)]
    big = max(drops) if drops else (0.0, "")
    app = rung_of(ctx, "strict_ns", "strict", "no_state") or list(res.values())[-1]  # the application-time only model
    # same, with the sensitive attributes left in (needed for the group audit)
    app_s = rung_of(ctx, "strict", "strict_ns") or app
    ctx.store["app_auc"] = app["auc"]
    rep.figure(
        fig,
        "auc_ladder",
        "The AUC ladder: how much does the model rely on each kind of information?",
        f"Each row is a gradient-boosting model ({folds}-fold CV) trained on fewer and fewer kinds of columns, from everything down to application-time columns only. Left = ROC-AUC, right = precision-recall; the last row shows the delinquency columns on their own.",
        f"Everything scores **{seq[0]:.3f}**; the largest single step down (**{-big[0]:+.3f}**) comes at '{big[1].strip()}'. "
        + ("There is no cliff: the signal is spread across every kind of column. " if big[0] < 0.05 else "")
        + f"Application-time columns alone reach **{app['auc']:.3f}**.",
    )
    rows_ladder = pd.DataFrame(
        {
            k: {"columns": v["n_features"], "AUC": v["auc"], "AUC std": v["std"], "PR-AUC": v["ap"]}
            for k, v in allres.items()
        }
    ).T
    rep.table(
        rows_ladder,
        "auc_ladder",
        "Ladder numbers",
        fmt={"columns": "{:.0f}", "AUC": "{:.3f}", "AUC std": "{:.3f}", "PR-AUC": "{:.3f}"},
        bars={"AUC": (0.5, 1), "PR-AUC": (0, 1)},
        console=8,
    )
    se = 1.96 * auc_se(max(app["auc"], 0.5), int(y.sum()), int((1 - y).sum()))
    if app["auc"] >= UNREALISTIC_AUC:
        rep.finding(
            "warning",
            f"Application-time columns alone reach AUC {app['auc']:.2f} - unusually high for credit risk",
            f"Application scorecards typically sit between {REALISTIC_AUC[0]:.2f} and {REALISTIC_AUC[1]:.2f} (behavioural models that see arrears can reach 0.90). With balances, schedule, loan events and bureau snapshot columns removed the model still separates the classes almost perfectly (± {se:.3f}); something in the 'safe' columns still encodes the outcome.",
            f"Treat results above ~{UNREALISTIC_AUC:.2f} as suspect: look at which columns drive them (below) and ask the organisers how the label and the columns were produced.",
            priority=1,
        )
    else:
        rep.finding(
            "ok", f"Application-time columns give a plausible AUC of {app['auc']:.2f}", f"95% interval ± {se:.3f}.", ""
        )

    # ── 9a2. does the model work for every kind of event?  (the label is a union of populations) ─────────────────────────
    with rep.attempt("Event arms"):
        cut, r_nd = ctx.store.get("best_cut"), rung_of(ctx, "no_del")
        if cut and cut.get("column") and r_nd is not None and cut["sign"] == ">":
            xv = df[cut["column"]].to_numpy(float)[r_nd["rows"]]
            yy, oo = y[r_nd["rows"]], r_nd["oof"]
            thr = cut["threshold"]
            arms = [
                ("no arrears at all (0)", xv == 0),
                (f"some (up to {num(thr / 4)})", (xv > 0) & (xv <= thr / 4)),
                (f"a lot ({num(thr / 4)} to {num(thr)})", (xv > thr / 4) & (xv <= thr)),
                (f"beyond the best cut (> {num(thr)})", xv > thr),
            ]
            rows_a = []
            for name, m in arms:
                n_ev, n_ne = int(yy[m].sum()), int((1 - yy[m]).sum())
                rows_a.append(
                    {
                        "arm": name,
                        f"loans (by `{cut['column']}`)": int(m.sum()),
                        "events": n_ev,
                        "share of all events": n_ev / max(int(yy.sum()), 1),
                        "event rate": n_ev / max(int(m.sum()), 1),
                        "AUC of the delinquency-free model inside the arm": safe_auc(yy[m], oo[m])
                        if n_ev >= 20 and n_ne >= 20
                        else np.nan,
                    }
                )
            arm_df = pd.DataFrame(rows_a).set_index("arm")
            rep.table(
                arm_df,
                "event_arms",
                "Does the model work equally well for every kind of event?",
                "The events are split by how much arrears history they carry. Inside each arm the model (no delinquency columns, no supplied score; out-of-fold predictions) must separate events from non-events using the remaining columns only. A low AUC in an arm means that arm's events are not explained by those columns.",
                fmt={
                    f"loans (by `{cut['column']}`)": "{:,}",
                    "share of all events": lambda v: pct(v, 0),
                    "event rate": lambda v: pct(v, 1),
                    "AUC of the delinquency-free model inside the arm": "{:.3f}",
                },
                console=6,
            )
            col_auc = "AUC of the delinquency-free model inside the arm"
            ok_ = arm_df[col_auc].dropna()
            if len(ok_) >= 2:
                weakest, strongest = ok_.idxmin(), ok_.idxmax()
                share_light = float(arm_df.loc[arm_df.index[:-1], "share of all events"].sum())
                ctx.store["arms"] = {
                    "weakest": weakest,
                    "weakest_auc": float(ok_.min()),
                    "strongest": strongest,
                    "strongest_auc": float(ok_.max()),
                    "share_light": share_light,
                }
                rep.text(
                    f"**Events with very different arrears histories.** {pct(float(arm_df['share of all events'].iloc[-1]), 0)} of the events carry arrears beyond the best cut; the other {pct(share_light, 0)} carry less, or none at all. "
                    f"Without any delinquency column or the supplied score, the model still separates events from non-events inside every arm (AUC {ok_.min():.2f} in '{weakest}' up to {ok_.max():.2f} in '{strongest}'), so the other columns carry label information even where arrears do not. "
                    "Whether the lighter events are another kind of status or a generator artefact cannot be settled from this table - ask the organisers which statuses 'negative record status' includes.",
                    console=False,
                )

    # ── 9b. which family of columns carries the signal?  (alone / removed / greedy build-up) ──────────────────────────
    clean = [c for c in feats if c not in inc and c not in t0]
    fam_cols = {f: [c for c in clean if family_of(c) == f] for f in FAMILIES if any(family_of(c) == f for c in clean)}
    with rep.attempt("Family ablation"):
        rows_ = stratified_rows(ctx, 12_000 if fast else 30_000)
        kw2 = {"max_iter": 50, "folds": 3, "rows": rows_}
        base = eval_set(ctx, clean, **kw2)["auc"]
        alone_f = {f: eval_set(ctx, cols, **kw2)["auc"] for f, cols in fam_cols.items()}
        alone_cache = {frozenset(cols): alone_f[f] for f, cols in fam_cols.items()}
        drop_f = {
            f: base - eval_set(ctx, [c for c in clean if c not in cols], **kw2)["auc"]
            for f, cols in fam_cols.items()
            if len(fam_cols) > 1
        }
        chosen, left, curve = [], list(fam_cols), []
        for _ in range(min(6, len(fam_cols))):
            scores = {}
            for f in left:
                cols_ = [c for g in [*chosen, f] for c in fam_cols[g]]
                # the first step IS the 'alone' run
                scores[f] = alone_cache.get(frozenset(cols_)) or eval_set(ctx, cols_, **kw2)["auc"]
            best = max(scores, key=scores.get)
            chosen.append(best)
            left.remove(best)
            curve.append((FAMILIES[best][0], scores[best]))
        fig, axes = plt.subplots(
            1, 3, figsize=(10.4, 0.42 * len(fam_cols) + 3.0), gridspec_kw={"width_ratios": [1.1, 1, 0.9]}
        )
        order = sorted(alone_f, key=alone_f.get, reverse=True)
        wrap = lambda f: "\n".join(textwrap.wrap(FAMILIES[f][0], 18))  # noqa: E731
        draw_dots(axes[0], [wrap(f) for f in order], [alone_f[f] for f in order])
        axes[0].set_xlim(0.48, 1.14)
        axes[0].set_xlabel("AUC (dashed = chance)")
        axes[0].set_title("AUC using ONLY this family", fontsize=9.5)
        order2 = sorted(drop_f, key=drop_f.get, reverse=True)
        draw_hbars(axes[1], [wrap(f) for f in order2], [drop_f[f] for f in order2], fmt="{:+.3f}")
        axes[1].set_title("AUC LOST when it is removed", fontsize=9.5)
        lo_x, hi_x = min(0.0, min(drop_f.values()) * 1.4), max(max(drop_f.values()) * 1.5, 0.02)
        axes[1].set_xlim(lo_x, hi_x)
        axes[1].xaxis.set_major_locator(MaxNLocator(4))
        axes[1].axvline(0, color=INK2, lw=0.8)
        axes[1].set_xlabel("change in AUC (< 0.003 is fold noise)")
        axes[2].plot(range(1, len(curve) + 1), [a for _, a in curve], "o-", color=NEUTRAL)
        axes[2].set_xticks(
            range(1, len(curve) + 1), [f"+ {nm}" for nm, _ in curve], rotation=40, ha="right", fontsize=8
        )
        axes[2].set_ylim(0.5, 1.0)
        axes[2].axhline(0.5, color=MUTED, ls="--", lw=1)
        axes[2].set_ylabel("AUC (axis starts at chance)")
        axes[2].set_title("Greedy build-up", fontsize=9.5)
        fig.tight_layout()
        first95 = next((i for i, (_, a) in enumerate(curve, 1) if a >= 0.95), None)
        rep.figure(
            fig,
            "family_ablation",
            "Which family of columns does the work?  (the cut-point)",
            "Models without delinquency columns and without the supplied score (3-fold CV on a 30,000-row sample, so differences below about 0.003 are noise). Left: AUC with one family alone. Middle: AUC lost when a family is taken away from the rest. Right: add the most useful family first, then the next best, and so on.",
            f"Strongest single family: **{FAMILIES[order[0]][0]}** ({alone_f[order[0]]:.3f} alone); removing it costs {max(drop_f[order[0]], 0):.3f} AUC. The costliest removal is **{FAMILIES[order2[0]][0]}** ({max(drop_f[order2[0]], 0):.3f}). "
            + (
                f"{first95} {'family already reaches' if first95 == 1 else 'families already reach'} AUC ≥ 0.95."
                if first95
                else f"Greedy build-up reaches {curve[-1][1]:.3f} after {len(curve)} families."
            ),
        )
        ctx.store["families"] = {"alone": alone_f, "drop": drop_f, "curve": curve}

    # ── 9c. why is it still so high?  events sit at the 'dead end' of balance columns ──────────────────────────────────
    ends = []
    for c in [
        c
        for c in clean
        if ctx.roles[c] == "post_origination_state" and ctx.kinds[c] in ("amount", "days", "count") and df[c].min() >= 0
    ]:
        v = df[c].to_numpy(float)
        # ties at the minimum make this set larger than 10% for zero-heavy columns: its size is reported
        lowest = v <= np.quantile(v, 0.10)
        zero = v == 0
        r_set = float(y[lowest].mean())
        ends.append(
            {
                "column": c,
                "rows = 0": float(zero.mean()),
                "event rate at 0": float(y[zero].mean()) if zero.sum() > 20 else np.nan,
                "rows in the lowest set": float(lowest.mean()),
                "event rate, lowest set": r_set,
                "event rate, the rest": float(y[~lowest].mean()),
                "lift of the lowest set": r_set / ctx.base_rate,
            }
        )
    if ends:
        edf = pd.DataFrame(ends).set_index("column").sort_values("lift of the lowest set", ascending=False)
        rep.table(
            edf.head(12),
            "dead_end_columns",
            "Where do the events sit inside the balance / schedule columns?",
            "The lowest set = rows at or below the column's 10th percentile (bigger than 10% of the rows when many rows are 0; the column 'rows in the lowest set' says how big). A loan that is almost repaid, closed or written off has tiny balances, remaining term and instalments, so if events concentrate there (lift above 1), these columns describe the *stage of the loan's life*, which is tied to the outcome.",
            fmt={
                "rows = 0": lambda v: pct(v),
                "event rate at 0": lambda v: "–" if pd.isna(v) else pct(v),
                "rows in the lowest set": lambda v: pct(v, 0),
                "event rate, lowest set": lambda v: pct(v),
                "event rate, the rest": lambda v: pct(v),
                "lift of the lowest set": "{:.1f}x",
            },
            bars={"lift of the lowest set": (0, 20)},
            console=5,
        )

    # ── 9c2. how many balance / schedule columns are exactly zero?  (the 'run-off' signature) ─────────────────────────────
    with rep.attempt("Zero-count profile"):
        zc_cols = [
            c
            for c in clean
            if ctx.roles[c] == "post_origination_state"
            and ctx.kinds[c] in ("amount", "days", "count")
            and df[c].min() >= 0
            and (df[c] == 0).mean() >= 0.005
        ]
        if len(zc_cols) >= 3:
            zc = (df[zc_cols] == 0).sum(axis=1).to_numpy()
            fig, t = count_profile(
                ctx, zc, f"how many of the {len(zc_cols)} balance / schedule columns equal exactly 0"
            )
            lo_r, hi_r = float(t["rate"].iloc[0]), float(t["rate"].iloc[-1])
            auc_zc = safe_auc(y, zc)
            best_flag = max(
                max(a_, 1 - a_) for a_ in (safe_auc(y, (df[c] == 0).to_numpy().astype(int)) for c in zc_cols)
            )
            ctx.store["zero_count"] = {
                "cols": zc_cols,
                "low": lo_r,
                "high": hi_r,
                "k_high": int(t.index[-1]),
                "auc": auc_zc,
                "best_flag": best_flag,
            }
            rep.figure(
                fig,
                "zero_count",
                "The 'run-off' signature: how many balance columns are exactly zero?",
                "Each loan gets a score = number of its balance / schedule / remaining-term columns that are exactly 0 (a closed, written-off or fully-due loan has several). Left: how many loans per score. Right: their event rate.",
                f"The event rate goes from **{pct(lo_r)}** with no zero column to **{pct(hi_r)}** with {int(t.index[-1])} zero columns. This one count has an AUC of **{auc_zc:.3f}** on its own (the best single 'is zero' flag reaches {best_flag:.3f}).",
            )
            rep.finding(
                "info" if auc_zc < 0.8 else "warning",
                "A simple 'number of zero balance columns' feature is highly predictive"
                if auc_zc >= 0.8
                else "A simple 'number of zero balance columns' feature carries some signal",
                f"{pct(lo_r)} → {pct(hi_r)} event rate across the counts, AUC {auc_zc:.3f} ({', '.join(zc_cols[:4])} …).",
                "These columns describe the stage of the loan's life; treat any model that leans on them as a state detector, and check whether the same information is available when you would actually score a loan.",
            )

    # ── 9c3. a model-free 'marker count' - markers are chosen INSIDE each training fold, counted on the held-out fold ───────
    with rep.attempt("Marker count"):
        mark_cols = [c for c in clean if ctx.roles[c] != "sensitive_attribute"]
        oof_cnt = np.zeros(ctx.n, int)
        for tr, te in StratifiedKFold(N_FOLDS, shuffle=True, random_state=SEED).split(df, y):
            oof_cnt[te] = marker_counts(ctx, find_markers(ctx, mark_cols, tr), te)
        auc_cnt = safe_auc(y, oof_cnt)
        markers = find_markers(ctx, mark_cols, np.arange(ctx.n))
        if len(markers) >= 2 and auc_cnt > 0.6:
            fig, t = count_profile(
                ctx, oof_cnt, "number of markers a loan carries (markers picked on training folds only)"
            )
            m_rows = []
            for c, v in markers:
                m = (df[c] == v).to_numpy()
                m_rows.append(
                    {
                        "marker": f"{c} == {label_of(c, v)}",
                        "rows": int(m.sum()),
                        "event rate when set": float(y[m].mean()),
                        "share of all events": float(y[m].sum() / max(y.sum(), 1)),
                        "share of non-events": float((~(y == 1) & m).sum() / max((y == 0).sum(), 1)),
                    }
                )
            rep.table(
                pd.DataFrame(m_rows).set_index("marker"),
                "markers",
                "The markers (chosen on all rows, for display)",
                "Single yes/no conditions on non-delinquency columns that are rare but strongly linked to the event.",
                fmt={
                    "rows": "{:,}",
                    "event rate when set": lambda v: pct(v),
                    "share of all events": lambda v: pct(v),
                    "share of non-events": lambda v: pct(v),
                },
                bars={"share of all events": (0, 1)},
                console=6,
            )
            ev_any = float(y[oof_cnt >= 1].sum() / max(y.sum(), 1))
            ne_any = float(((oof_cnt >= 1) & (y == 0)).sum() / max((y == 0).sum(), 1))
            rep.figure(
                fig,
                "marker_count",
                "A model-free score: count how many markers a loan carries",
                "Each loan gets one point per marker (a rare yes/no condition strongly linked to the event). Markers were picked on the training folds and counted on the held-out fold, so the result is honest. Left: how many loans per count. Right: their event rate.",
                f"Counting up to {len(markers)} simple markers gives an AUC of **{auc_cnt:.3f}** with no model at all; {pct(ev_any, 0)} of events carry at least one marker versus {pct(ne_any, 0)} of non-events. The event rate goes from {pct(float(t['rate'].iloc[0]))} (none) to {pct(float(t['rate'].iloc[-1]), 0)} ({int(t.index[-1])} markers).",
            )
            ctx.store["markers"] = {
                "auc": auc_cnt,
                "n": len(markers),
                "events_with_marker": ev_any,
                "non_events_with_marker": ne_any,
                "low": float(t["rate"].iloc[0]),
                "high": float(t["rate"].iloc[-1]),
                "k_high": int(t.index[-1]),
                "names": [f"{c}={label_of(c, v)}" for c, v in markers],
            }

    # ── 9d. permutation importance on the delinquency-free model ───────────────────────────────────────────────────────
    with rep.attempt("Permutation importance"):
        x, cat = model_frame(ctx, clean)
        rows_ = stratified_rows(ctx, 20_000 if fast else 60_000)
        xtr, xte, ytr, yte = train_test_split(
            x.iloc[rows_], y[rows_], test_size=0.3, stratify=y[rows_], random_state=SEED
        )
        mdl = make_hgb(categorical_features=cat or None, **kw).fit(xtr, ytr)
        pi = permutation_importance(mdl, xte, yte, scoring="roc_auc", n_repeats=3, random_state=SEED, n_jobs=1)
        imp = (
            pd.DataFrame({"drop": pi.importances_mean, "std": pi.importances_std}, index=x.columns)
            .sort_values("drop", ascending=False)
            .head(15)
        )
        role_of = {c: ctx.roles.get(c.split("__")[0], "origination_time") for c in imp.index}
        fig, ax = plt.subplots(figsize=(8.6, 0.3 * len(imp) + 1.4))
        ax.barh(
            range(len(imp)),
            imp["drop"],
            xerr=imp["std"],
            color=[ROLE_COLORS[role_of[c]] for c in imp.index],
            height=0.64,
            error_kw={"ecolor": INK2, "capsize": 2.5, "lw": 1},
        )
        ax.set_yticks(range(len(imp)), [f"{c}  [{ROLE_TAG[role_of[c]]}]" for c in imp.index], fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("drop in AUC when the column is shuffled (held-out data)")
        ax.grid(axis="y", visible=False)
        legend_top(ax, ncol=3, handles=role_handles(list(dict.fromkeys(role_of.values()))))
        rep.figure(
            fig,
            "permutation_importance",
            "What does the delinquency-free model actually use?",
            "Shuffle one column at a time on held-out rows and measure how much the AUC falls: the bigger the fall, the more the model leans on that column. Colour and the tag in brackets give the column's role; names ending in __bucket / __log_freq are engineered versions of the region code.",
            "Top columns: "
            + ", ".join(f"`{c}`" for c in imp.index[:4])
            + f" (AUC drops {imp['drop'].iloc[0]:.3f} / {imp['drop'].iloc[1]:.3f} / {imp['drop'].iloc[2]:.3f} / {imp['drop'].iloc[3]:.3f}).",
        )

    # ── 9d2. is the application-time only signal carried by a few columns, or spread everywhere? ─────────────────────────
    with rep.attempt("Signal concentration"):
        strict = orig
        if len(strict) >= 6:
            xs_, cat_ = model_frame(ctx, strict)
            rows_ = stratified_rows(ctx, 20_000 if fast else 60_000)
            xtr, xte, ytr, yte = train_test_split(
                xs_.iloc[rows_], y[rows_], test_size=0.3, stratify=y[rows_], random_state=SEED
            )
            base_m = make_hgb(categorical_features=cat_ or None, **kw).fit(xtr, ytr)
            base_auc = safe_auc(yte, base_m.predict_proba(xte)[:, 1])
            pi_ = permutation_importance(base_m, xte, yte, scoring="roc_auc", n_repeats=3, random_state=SEED, n_jobs=1)
            rank = pd.Series(pi_.importances_mean, index=xs_.columns).sort_values(ascending=False)
            # derived columns count for their source column
            ordered = list(dict.fromkeys(c.split("__")[0] for c in rank.index))
            curve_k = [(0, base_auc)]
            for k_ in range(1, min(10, len(strict) - 3) + 1):
                keep = [c for c in strict if c not in ordered[:k_]]
                xk, ck = model_frame(ctx, keep)
                xa_, xb_, ya_, yb_ = train_test_split(
                    xk.iloc[rows_], y[rows_], test_size=0.3, stratify=y[rows_], random_state=SEED
                )
                curve_k.append(
                    (
                        k_,
                        safe_auc(
                            yb_, make_hgb(categorical_features=ck or None, **kw).fit(xa_, ya_).predict_proba(xb_)[:, 1]
                        ),
                    )
                )
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.4, 4.4), gridspec_kw={"width_ratios": [1, 1.1]})
            top_ = rank.head(12)
            a1.barh(range(len(top_)), top_.to_numpy(), color=ROLE_COLORS["origination_time"], height=0.64)
            a1.set_yticks(range(len(top_)), top_.index, fontsize=8)
            a1.invert_yaxis()
            a1.set_xlabel("drop in AUC when the column is shuffled")
            a1.set_title(f"which of the {len(strict)} application-time columns matter", fontsize=9.5)
            a1.xaxis.set_major_locator(MaxNLocator(4))
            a1.grid(axis="y", visible=False)
            a2.plot([k for k, _ in curve_k], [a for _, a in curve_k], "o-", color=NEUTRAL)
            a2.axhline(0.5, color=MUTED, ls="--", lw=1)
            a2.set_ylim(0.45, 1.0)
            a2.set_xlabel("strongest columns removed (k)")
            a2.set_ylabel("AUC of a model re-trained without them")
            a2.set_title("does the signal collapse when the best columns go?", fontsize=9.5)
            for k_, a in curve_k[::3]:
                a2.annotate(
                    f"{a:.3f}", (k_, a), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8, color=INK2
                )
            fig.tight_layout()
            at5 = next((a for k_, a in curve_k if k_ == min(5, curve_k[-1][0])), curve_k[-1][1])
            diffuse = at5 >= 0.85 * base_auc + 0.15 * 0.5
            ctx.store["concentration"] = {
                "base": base_auc,
                "k": min(5, curve_k[-1][0]),
                "after_k": at5,
                "diffuse": diffuse,
                "top": ordered[:3],
            }
            rep.figure(
                fig,
                "signal_concentration",
                "Is the application-time signal carried by a few columns?",
                "Left: permutation importance of the application-time only model (shuffle one column, see how much the AUC falls). Right: re-train after deleting the strongest columns one by one; a signal carried by a single leak collapses at once, a diffuse one erodes slowly.",
                f"AUC {base_auc:.3f} with all {len(strict)} columns, still **{at5:.3f}** after removing the {min(5, curve_k[-1][0])} strongest ({', '.join(ordered[:3])} …). "
                + (
                    "The signal is spread over many columns: there is no single leak to remove."
                    if diffuse
                    else "The signal depends on a handful of columns: look at them closely for leakage."
                ),
            )

    # ── 9e. linear vs tree on the same application-time columns ───────────────────────────────────────────────────────
    with rep.attempt("Linear baseline"):
        # the exact columns of the 'application-time + sensitive attributes' gradient-boosting rung
        a_cols = orig + sens
        xa = ctx.df[a_cols]
        nom = [c for c in a_cols if ctx.kinds[c] == "nominal" or not is_num(xa[c])]
        numc = [c for c in a_cols if c not in nom]
        pre = ColumnTransformer(
            [
                (
                    "num",
                    Pipeline(
                        [
                            ("log", FunctionTransformer(lambda v: np.sign(v) * np.log1p(np.abs(v)))),
                            ("sc", StandardScaler()),
                        ]
                    ),
                    numc,
                ),
                ("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=0.005), nom),
            ]
        )
        lr = Pipeline([("pre", pre), ("lr", LogisticRegression(max_iter=300, C=0.5))])
        xx = xa.copy()
        for c in nom:
            xx[c] = xx[c].astype(str)
        oof = np.zeros(ctx.n)
        lr_aucs = []
        # the same folds the boosted trees used
        for tr, te in StratifiedKFold(folds, shuffle=True, random_state=SEED).split(xx, y):
            lr.fit(xx.iloc[tr], y[tr])
            oof[te] = lr.predict_proba(xx.iloc[te])[:, 1]
            lr_aucs.append(safe_auc(y[te], oof[te]))
        lin, lin_ap = float(np.mean(lr_aucs)), float(average_precision_score(y, oof))
        tree_a, tree_ap = app_s["auc"], app_s["ap"]
        gap = tree_a - lin
        diffs = np.array(app_s["fold_aucs"]) - np.array(lr_aucs)
        noise = float(diffs.std(ddof=1)) if len(diffs) > 1 else float("nan")
        verdict = (
            "a big gap: the signal is non-linear / interaction-driven, so tree models (or a lot of feature engineering) are needed."
            if gap > 0.05
            else f"a clear gap ({gap:.3f} AUC, {gap / noise:.0f}x the fold-to-fold spread of the difference) and a larger one in PR-AUC ({tree_ap - lin_ap:+.3f}): boosted trees find structure that the logistic model misses, although the logistic model is a good, explainable baseline."
            if gap > CLEAR_GAP and noise > 0 and gap > 2 * noise
            else "a small gap: a regularised linear scorecard is competitive and far easier to explain."
        )
        rep.text(
            f"**Linear vs boosted trees on the same {len(a_cols)} application-time columns, same folds:** logistic regression (log-transform, scaling, one-hot) reaches AUC **{lin:.3f}** / PR-AUC {lin_ap:.3f}; gradient boosting **{tree_a:.3f}** / {tree_ap:.3f} - "
            + verdict
        )
        ctx.store["lr"] = {
            "auc": lin,
            "ap": lin_ap,
            "gbm_auc": tree_a,
            "gbm_ap": tree_ap,
            "gap": gap,
            "n_cols": len(a_cols),
        }
        ctx.store["lr_auc"] = lin

    # ── 9f. calibration + lift of the two interesting models ──────────────────────────────────────────────────────────
    with rep.attempt("Calibration and lift"):
        models = {
            "application-time + sensitive attributes": app_s,
            "no delinquency columns, no supplied score": by_key.get("no_del", app),
        }
        styles = [{"color": NEUTRAL, "ls": "-", "marker": "o"}, {"color": INK, "ls": "--", "marker": "s"}]
        fig1, a1 = plt.subplots(figsize=(6.2, 5.2))
        fig2, a2 = plt.subplots(figsize=(6.2, 5.2))
        a1.plot([0, 1], [0, 1], color=MUTED, ls=":", lw=1.2, label="perfect calibration")
        a2.plot([0, 100], [0, 100], color=MUTED, ls=":", lw=1.2, label="random review")
        lifts, gaps = {}, {}
        for (nm, m), st_ in zip(models.items(), styles, strict=False):
            p, yy = m["oof"], y[m["rows"]]
            dec = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False).to_numpy()
            g = pd.DataFrame({"p": p, "y": yy, "d": dec}).groupby("d").mean()
            a1.plot(g["p"], g["y"], ms=5, label=nm, **st_)
            order = np.argsort(-p)
            cum = np.cumsum(yy[order]) / max(yy.sum(), 1)
            xs = np.arange(1, len(p) + 1) / len(p)
            a2.plot(xs * 100, cum * 100, color=st_["color"], ls=st_["ls"], label=nm)
            k10 = int(0.1 * len(p))
            lifts[nm] = (
                float(cum[k10 - 1]),
                float(yy[order][:k10].mean() / yy.mean()),
                float(brier_score_loss(yy, p)),
                float(yy.mean() * (1 - yy.mean())),
            )
            gaps[nm] = float((g["p"] - g["y"]).abs().mean())  # mean |predicted - observed| over the ten risk groups
        for ax_ in (a1,):
            # a 3.6% event: a linear 0-1 axis would squeeze nine of ten points into a corner
            ax_.set_xscale("symlog", linthresh=0.01)
            ax_.set_yscale("symlog", linthresh=0.01)
            ax_.set_xlim(0, 1)
            ax_.set_ylim(0, 1)
            ax_.set_xticks([0, 0.01, 0.1, 1], ["0", "1%", "10%", "100%"])
            ax_.set_yticks([0, 0.01, 0.1, 1], ["0", "1%", "10%", "100%"])
        a1.set_xlabel("predicted probability (mean per decile, log-like scale)")
        a1.set_ylabel("observed event rate (log-like scale)")
        a2.set_xlabel("share of loans reviewed, riskiest first (%)")
        a2.set_ylabel("share of all events caught (%)")
        legend_top(a1, ncol=1)
        legend_top(a2, ncol=1)
        txt = "; ".join(
            f"{nm}: the riskiest 10% of loans catch **{c * 100:.0f}%** of events (lift {lf:.1f}x), Brier {b:.4f} vs {b0:.4f} for a constant guess"
            for nm, (c, lf, b, b0) in lifts.items()
        )
        rep.figure(
            fig1,
            "calibration",
            "Calibration: do the predicted probabilities mean what they say?",
            "Loans are sorted into ten groups by predicted risk; each point compares the mean prediction with the observed event rate. Points on the dotted diagonal = trustworthy probabilities. Both axes use a log-like scale because the event is rare.",
            "Mean gap between predicted and observed event rate over the ten risk groups: "
            + "; ".join(f"{nm} {gp:.4f}" for nm, gp in gaps.items())
            + f" (the overall event rate is {ctx.base_rate:.4f}); "
            + txt
            + ".",
        )
        rep.figure(
            fig2,
            "cumulative_gains",
            "Gains: how fast do we catch the events?",
            "Review loans from the riskiest down; the curve shows the share of all events caught. The dotted line is what random review gives. The higher the curve, the fewer loans you must review.",
            txt + ".",
        )
        ops = []
        for nm, m in models.items():
            p_, yy = m["oof"], y[m["rows"]]
            pr_, rc_, th_ = precision_recall_curve(yy, p_)
            for tgt in (0.5, 0.8, 0.9, 0.95):
                i = int(np.flatnonzero(rc_[:-1] >= tgt)[-1])
                ops.append(
                    {
                        "model": nm,
                        "event recall you ask for": tgt,
                        "precision you get": float(pr_[i]),
                        "share of all loans flagged": float((p_ >= th_[i]).mean()),
                        "score threshold": float(th_[i]),
                    }
                )
        odf = pd.DataFrame(ops).set_index("model")
        rep.table(
            odf,
            "operating_points",
            "What do the thresholds buy you?",
            "Pick the share of events you want to catch (recall); the table says how many loans you must flag and how many of those flags are real events (precision).",
            fmt={
                "event recall you ask for": lambda v: pct(v, 0),
                "precision you get": lambda v: pct(v),
                "share of all loans flagged": lambda v: pct(v, 1),
                "score threshold": "{:.3f}",
            },
            bars={"precision you get": (0, 1)},
            console=8,
        )

    # ── 9g. do we need all the data? ─────────────────────────────────────────────────────────────────────────────────────
    with rep.attempt("Learning curve"):
        tr_all, te_idx = train_test_split(np.arange(ctx.n), test_size=0.25, stratify=y, random_state=SEED)
        sizes = [s_ for s_ in (2_000, 5_000, 10_000, 20_000, 40_000, 75_000) if s_ <= len(tr_all)]
        if len(tr_all) not in sizes:
            sizes.append(len(tr_all))
        fig, ax = plt.subplots(figsize=(8.2, 4.0))
        gains = {}
        for (nm, cols), st_ in zip(
            (
                ("application-time + sensitive attributes", orig + sens),
                ("no delinquency columns, no supplied score", clean),
            ),
            ({"color": NEUTRAL, "ls": "-", "marker": "o"}, {"color": INK, "ls": "--", "marker": "s"}),
            strict=True,
        ):
            x, cat = model_frame(ctx, cols)
            curve_ = []
            for n_ in sizes:
                sub = tr_all[:n_] if n_ < len(tr_all) else tr_all
                mdl = make_hgb(categorical_features=cat or None, **kw).fit(x.iloc[sub], y[sub])
                curve_.append(safe_auc(y[te_idx], mdl.predict_proba(x.iloc[te_idx])[:, 1]))
            ax.plot(sizes, curve_, ms=5, label=nm, **st_)
            gains[nm] = curve_[-1] - curve_[-2] if len(curve_) > 1 else 0.0
        ax.set_xscale("log")
        ax.set_xticks(sizes, [f"{s_ / 1000:g}k" for s_ in sizes])
        ax.minorticks_off()
        ax.set_xlabel("training rows (log scale)")
        ax.set_ylabel("AUC on a fixed held-out 25%")
        legend_top(ax, ncol=2)
        rep.figure(
            fig,
            "learning_curve",
            "Do we need all the data?  (learning curve)",
            "AUC on the same held-out rows when the model is trained on more and more rows. A curve that is still rising means more data would help; a flat tail means features, not rows, are the bottleneck.",
            "Gain from the last step up in training size: "
            + "; ".join(f"{k} {v:+.3f}" for k, v in gains.items())
            + ".",
        )

    # ── 9g2. does the way we validate or weight change the answer?  (split strategy, seed, class weights) ─────────────────
    with rep.attempt("Validation checks"):
        x_v, cat_v = model_frame(ctx, clean)
        rows_v = stratified_rows(ctx, 20_000 if fast else ctx.n)
        xs_v, ys_v = x_v.iloc[rows_v].reset_index(drop=True), y[rows_v]
        files_v = df[FILE_COL].iloc[rows_v].to_numpy()

        def run_cv(name: str, splits, **hp) -> dict:
            oof_v, aucs_v = np.zeros(len(ys_v)), []
            for tr, te in splits:
                p_ = (
                    make_hgb(categorical_features=cat_v or None, **{**kw, **hp})
                    .fit(xs_v.iloc[tr], ys_v[tr])
                    .predict_proba(xs_v.iloc[te])[:, 1]
                )
                oof_v[te] = p_
                aucs_v.append(safe_auc(ys_v[te], p_))
            return {
                "validation / weighting": name,
                "AUC": float(np.mean(aucs_v)),
                "AUC std over folds": float(np.std(aucs_v)),
                "PR-AUC": float(average_precision_score(ys_v, oof_v)),
                "Brier score": float(brier_score_loss(ys_v, oof_v)),
            }

        def strat(seed: int):
            return StratifiedKFold(folds, shuffle=True, random_state=seed).split(xs_v, ys_v)

        n_files_v = len(set(files_v))
        checks = [
            run_cv("stratified CV (the default)", strat(SEED)),
            run_cv("stratified CV, another random seed", strat(SEED + 1)),
        ]
        if n_files_v >= 3:
            checks.append(
                run_cv(
                    "leave whole files out (GroupKFold by file)",
                    GroupKFold(min(folds, n_files_v)).split(xs_v, ys_v, files_v),
                )
            )
        checks.append(run_cv("stratified CV + class_weight='balanced'", strat(SEED), class_weight="balanced"))
        vdf = pd.DataFrame(checks).set_index("validation / weighting")
        rep.table(
            vdf,
            "validation_checks",
            "Does the way we validate or weight change the answer?",
            f"The same gradient-boosting model on the delinquency-free columns ({len(clean)}), {folds}-fold. Another seed shows the noise floor; leaving whole files out shows whether the files are interchangeable; class weights show what re-balancing does to ranking and to probabilities.",
            fmt={"AUC": "{:.4f}", "AUC std over folds": "{:.4f}", "PR-AUC": "{:.3f}", "Brier score": "{:.4f}"},
            console=6,
        )
        noise = abs(float(vdf["AUC"].iloc[0] - vdf["AUC"].iloc[1]))
        text = f"**Noise floor:** a different random seed moves the AUC by {noise:.4f}, so smaller differences between models are noise. "
        if n_files_v >= 3:
            text += f"Leaving whole files out gives {vdf['AUC'].iloc[2]:.4f} against {vdf['AUC'].iloc[0]:.4f}: " + (
                "the same within noise, so random stratified CV is as good as a file-based split here. "
                if abs(vdf["AUC"].iloc[2] - vdf["AUC"].iloc[0])
                <= max(3 * noise, 2 * float(vdf["AUC std over folds"].iloc[0]))
                else "different, so check section 3 before pooling. "
            )
        cw = vdf.iloc[-1]
        text += (
            f"With class_weight='balanced' the AUC is {cw['AUC']:.4f} and the Brier score {cw['Brier score']:.4f} (default: {vdf['Brier score'].iloc[0]:.4f}): "
            + (
                "re-weighting buys no ranking quality and distorts the probabilities - start without it."
                if cw["AUC"] - vdf["AUC"].iloc[0] < max(3 * noise, 0.002)
                else "re-weighting helps the ranking; recalibrate the probabilities afterwards."
            )
        )
        rep.text(text, console=False)
        ctx.store["validation_checks"] = {
            "noise": noise,
            "file_out_gap": float(vdf["AUC"].iloc[2] - vdf["AUC"].iloc[0]) if n_files_v >= 3 else None,
            "cw_auc_gain": float(cw["AUC"] - vdf["AUC"].iloc[0]),
            "brier": float(vdf["Brier score"].iloc[0]),
            "cw_brier": float(cw["Brier score"]),
        }

    # ── 9g3. a stress test without dates: train on young loans, test on old ones ───────────────────────────────────────────
    with rep.attempt("Stress test without dates"):
        if {"RemainingTerm", "ContractTermMonth"} <= set(df.columns):
            term_days = df["ContractTermMonth"].to_numpy(float) * 30.4375
            rt = df["RemainingTerm"].to_numpy(float)
            with np.errstate(divide="ignore", invalid="ignore"):
                used_share = np.where(term_days > 0, 1 - rt / term_days, np.nan)
            old = (rt <= 0) | (used_share >= 0.95)  # matured, or at least 95% of the contract term used
            young = ~old
            if old.sum() >= 300 and y[old].sum() >= 50 and y[young].sum() >= 50:
                rows_s = []
                for name, cols, rung in (
                    ("known at origination (set A)", fs["A"], rung_of(ctx, "strict_ns", "strict")),
                    ("no delinquency columns, no supplied score", clean, rung_of(ctx, "no_del")),
                ):
                    if rung is None or len(rung["rows"]) != ctx.n:
                        continue
                    x_s, cat_s = model_frame(ctx, cols)
                    model_s = make_hgb(categorical_features=cat_s or None, **kw).fit(x_s[young], y[young])
                    auc_shift = safe_auc(y[old], model_s.predict_proba(x_s[old])[:, 1])
                    # the same old loans, scored by out-of-fold models that saw old loans in training
                    auc_same = safe_auc(y[old], rung["oof"][old])
                    rows_s.append(
                        {
                            "column set": name,
                            "AUC on old loans, trained on all other loans (CV)": auc_same,
                            "AUC on old loans, trained on young loans only": auc_shift,
                            "drop": auc_same - auc_shift,
                        }
                    )
                if rows_s:
                    sdf = pd.DataFrame(rows_s).set_index("column set")
                    rep.table(
                        sdf,
                        "age_stress_test",
                        "A stress test without dates: train on young loans, test on old ones",
                        f"There is no date column, so loan age is approximated by the share of the contract term already used. 'Old' = matured or at least 95% used ({int(old.sum()):,} loans, {int(y[old].sum()):,} events); 'young' = the rest ({int(young.sum()):,} loans, {int(y[young].sum()):,} events). A model that works only because of the portfolio's age structure loses AUC when it never saw old loans.",
                        fmt={c: "{:.3f}" for c in sdf.columns[:2]} | {"drop": "{:+.3f}"},
                        console=4,
                    )
                    worst_s = sdf["drop"].idxmax()
                    rep.text(
                        f"**What the stress test says.** Trained on young loans only, the largest loss on old loans is {sdf['drop'].max():.3f} AUC ({worst_s}); "
                        + (
                            "small: the signal does not depend on having seen old loans."
                            if sdf["drop"].max() < 0.02
                            else "a visible loss: part of the signal is the age structure of this portfolio, so expect weaker results on a later vintage."
                        ),
                        console=False,
                    )
                    ctx.store["age_stress"] = {
                        "max_drop": float(sdf["drop"].max()),
                        "drops": {k: float(v) for k, v in sdf["drop"].items()},
                        "n_old": int(old.sum()),
                    }

    # ── 9h. group audit of the application-time model (with and without the sensitive columns) ────────────────────────────
    with rep.attempt("Group audit"):
        m, m_ns = app_s, by_key.get("strict_ns")
        p, idx = m["oof"], m["rows"]
        yy = y[idx]
        thr = np.quantile(p, 0.90)
        p_ns = m_ns["oof"] if m_ns is not None and len(m_ns["oof"]) == len(p) else None
        thr_ns = np.quantile(p_ns, 0.90) if p_ns is not None else None
        groups, too_small = {}, []
        for c in [c for c in SENSITIVE if c in df.columns]:
            col = df[c].iloc[idx].reset_index(drop=True)
            if c == "Age":
                col = pd.cut(col, [0, 30, 45, 60, 200], labels=["<=30", "31-45", "46-60", ">60"]).astype(str)
            for lvl in sorted(col.unique(), key=str):
                mk = (col == lvl).to_numpy()
                name = f"{c} = {lvl}"
                ev = int(yy[mk].sum())
                if mk.sum() < 200 or ev < 30:
                    if mk.sum() >= 30:
                        too_small.append(f"{name} ({int(mk.sum()):,} loans, {ev} events)")
                    continue
                flagged = p[mk] >= thr
                # sampling range of the flag rate
                f_lo, f_hi = wilson(np.array([int(flagged.sum())]), np.array([int(mk.sum())]))
                row = {
                    "rows": int(mk.sum()),
                    "events": ev,
                    "ratio_lo": float(f_lo[0]) / 0.10,
                    "ratio_hi": float(f_hi[0]) / 0.10,
                    "event rate": float(yy[mk].mean()),
                    "mean predicted risk": float(p[mk].mean()),
                    "flagged in riskiest 10%": float(flagged.mean()),
                    "share of events flagged": float(flagged[yy[mk] == 1].mean()),
                    "share of non-events flagged": float(flagged[yy[mk] == 0].mean()),
                    "AUC inside group": safe_auc(yy[mk], p[mk]),
                }
                if p_ns is not None:
                    row["flagged WITHOUT the sensitive columns"] = float((p_ns[mk] >= thr_ns).mean())
                groups[name] = row
        if groups:
            gdf = pd.DataFrame(groups).T
            gdf["flag-rate ratio vs overall"] = gdf["flagged in riskiest 10%"] / 0.10
            gdf["flag-rate ratio (95% range)"] = [
                f"{r:.2f}x ({lo:.2f}-{hi:.2f})"
                for r, lo, hi in zip(gdf["flag-rate ratio vs overall"], gdf["ratio_lo"], gdf["ratio_hi"], strict=True)
            ]
            gdf["event-rate ratio vs overall"] = gdf["event rate"] / ctx.base_rate
            if p_ns is not None:
                gdf["ratio WITHOUT the sensitive columns"] = gdf["flagged WITHOUT the sensitive columns"] / 0.10
            fmt_g = {
                "rows": "{:,.0f}",
                "events": "{:,.0f}",
                "event rate": lambda v: pct(v),
                "mean predicted risk": lambda v: pct(v),
                "flagged in riskiest 10%": lambda v: pct(v),
                "share of events flagged": lambda v: pct(v, 0),
                "share of non-events flagged": lambda v: pct(v),
                "AUC inside group": "{:.3f}",
                "flag-rate ratio vs overall": "{:.2f}x",
                "event-rate ratio vs overall": "{:.2f}x",
                "flagged WITHOUT the sensitive columns": lambda v: pct(v),
                "ratio WITHOUT the sensitive columns": "{:.2f}x",
            }
            skipped = (
                f" Groups with fewer than 30 events or 200 loans are not rated ({'; '.join(too_small)})."
                if too_small
                else ""
            )
            rep.table(
                gdf.drop(columns=["ratio_lo", "ratio_hi"]),
                "group_audit",
                "Does the application-time model treat the sensitive groups differently?",
                "Out-of-fold predictions. 'Flagged' = in the riskiest 10% of loans; a flag-rate ratio below 1 means the group is flagged LESS often than average, above 1 more often. Compare it with the event-rate ratio (what the base rates alone would justify) and with the ratio of a model that never saw the sensitive columns."
                + skipped,
                fmt=fmt_g,
                bars={"flag-rate ratio vs overall": (0, 3)},
                console=8,
            )
            gdf["distance"] = np.abs(np.log(gdf["flag-rate ratio vs overall"].clip(lower=1e-3)))
            worst = gdf["distance"].idxmax()
            r_ = float(gdf.loc[worst, "flag-rate ratio vs overall"])
            # outside the band AND outside sampling noise
            uneven = (r_ > 1.25 or r_ < 0.8) and not (gdf.loc[worst, "ratio_lo"] <= 1.0 <= gdf.loc[worst, "ratio_hi"])
            favourable = {}  # four-fifths style check per attribute: the lowest not-flagged rate over the highest
            for attr in {n_.split(" = ")[0] for n_ in gdf.index}:
                sel = gdf[[n_.startswith(attr + " = ") for n_ in gdf.index]]
                if len(sel) >= 2:
                    nf = 1 - sel["flagged in riskiest 10%"]
                    favourable[attr] = float(nf.min() / nf.max())
            # can the sensitive attributes be recovered from the allowed columns?  (proxy test)
            proxy = {}
            if len(orig) >= 3:
                xo, cato = model_frame(ctx, orig)
                rows_p = stratified_rows(ctx, 12_000 if fast else 30_000)
                for attr in [c for c in SENSITIVE if c in df.columns]:
                    col = df[attr]
                    # the smaller group (or age over 60)
                    flag = (col > 60) if attr == "Age" else (col == col.value_counts().index[-1])
                    if int(flag.iloc[rows_p].sum()) >= 100:
                        _, a_ = cv_predict(
                            xo.iloc[rows_p].reset_index(drop=True),
                            flag.iloc[rows_p].astype(int).to_numpy(),
                            folds=3,
                            categorical_features=cato or None,
                            max_iter=60,
                        )
                        proxy[attr] = float(np.mean(a_))
            delta = (m["auc"] - m_ns["auc"]) if m_ns is not None else float("nan")
            ctx.store["fairness"] = {
                "worst": worst,
                "ratio": r_,
                "events": int(gdf.loc[worst, "events"]),
                "event_ratio": float(gdf.loc[worst, "event-rate ratio vs overall"]),
                "ratio_ns": float(gdf.loc[worst, "ratio WITHOUT the sensitive columns"]) if p_ns is not None else None,
                "delta_auc": delta,
                "proxy": proxy,
                "favourable": favourable,
                "uneven": bool(uneven),
            }
            dirn = "less" if r_ < 1 else "more"
            detail = (
                f"`{worst}` ({int(gdf.loc[worst, 'rows']):,} loans, {int(gdf.loc[worst, 'events'])} events) is flagged {r_:.2f}x as often as the average loan, i.e. {dirn} often; its event rate is {gdf.loc[worst, 'event-rate ratio vs overall']:.2f}x the average, so the flags follow the base rate."
                + (
                    f" A model that never saw the sensitive columns flags it {gdf.loc[worst, 'ratio WITHOUT the sensitive columns']:.2f}x as often, and leaving them out costs {delta:.3f} AUC."
                    if p_ns is not None
                    else ""
                )
                + (
                    " The sensitive columns can still be recovered from the allowed ones (AUC "
                    + ", ".join(f"{k} {v:.2f}" for k, v in proxy.items())
                    + "), so dropping them does not remove their influence."
                    if proxy
                    else ""
                )
            )
            action = "Using sex or age as a rating factor is restricted or prohibited in many jurisdictions whatever the base rates say: check the law and the competition rules before using them. If you drop them, test for proxies as above."
            rep.finding(
                "warning" if uneven else "ok",
                "Check group differences in the application-time model"
                if uneven
                else "Flag rates are balanced across sensitive groups",
                detail,
                action if uneven else "",
            )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 10 · Modeling blueprint
# ══════════════════════════════════════════════════════════════════════════════════
def rung_of(ctx: Ctx, *keys: str) -> dict | None:
    """A ladder result by its short key ('all', 'no_inc', 'no_del', 'no_state', 'strict', 'strict_ns'); with several
    keys the first one that exists wins.  A rung that had the same columns as the one before it is an alias of it."""
    for key in keys:
        label = ctx.store.get("ladder_keys", {}).get(key)
        if label and ctx.store.get("ladder", {}).get(label):
            return ctx.store["ladder"][label]
    return None


def feature_sets(ctx: Ctx) -> dict[str, list[str]]:
    """The column sets the whole report talks about (ladder, framings, feature_roles.csv) - defined once."""

    def by(*roles: str) -> list[str]:
        return [c for c in ctx.features if ctx.roles[c] in roles]

    a = by("origination_time")
    b = a + by("snapshot_unverified", "post_origination_state")
    return {
        "A": a,
        "B": b,
        "C": b + by("tier0_target_defining"),
        "t0": by("tier0_target_defining"),
        "inc": by("incumbent_model_output"),
        "sens": by("sensitive_attribute"),
        "snap": by("snapshot_unverified"),
        "state": by("post_origination_state"),
    }


def model_settings(ctx: Ctx) -> tuple[dict, int]:
    """(gradient-boosting keyword arguments, number of CV folds) used by every model-based table: lighter with --fast."""
    return ({"max_iter": 60} if ctx.args.fast else {"max_iter": 100}), (3 if ctx.args.fast else N_FOLDS)


def fmt_auc(r: dict | None) -> str:
    return "n/a (needs models)" if r is None else f"{r['auc']:.3f}"


def pop_auc(res: dict | None, y: np.ndarray, mask: np.ndarray | None) -> dict | None:
    """AUC of a cross-validated result on the sub-population `mask` (rows, events, AUC and its 95% half-width)."""
    if res is None or mask is None:
        return None
    m = mask[res["rows"]]
    yy = y[res["rows"]][m]
    n_ev = int(yy.sum())
    if n_ev < 10 or n_ev == len(yy):
        return None
    a = safe_auc(yy, res["oof"][m])
    return {"auc": a, "rows": int(m.sum()), "events": n_ev, "half": 1.96 * auc_se(max(a, 0.5), n_ev, len(yy) - n_ev)}


def fmt_pop(r: dict | None, what: str) -> str:
    return (
        "n/a (needs models)"
        if r is None
        else f"{r['auc']:.3f} ±{r['half']:.2f} ({r['events']:,} events among {r['rows']:,} {what})"
    )


@register(10, "Modeling blueprint", "Given all of this: what should I build, with which columns, validated how?")
def section_blueprint(ctx: Ctx) -> None:
    """What to build: 10a the five framings with honest AUCs, 10b preprocessing recipe, 10c validation plan, 10d questions for the organisers,
    10e the feature_roles.csv hand-off and starter code."""
    rep, df, y, feats, st = ctx.rep, ctx.df, ctx.y, ctx.features, ctx.store
    sig = signal_table(ctx)
    fs = feature_sets(ctx)
    set_a, set_b, set_c, sens, inc, t0 = fs["A"], fs["B"], fs["C"], fs["sens"], fs["inc"], fs["t0"]
    kw, folds = model_settings(ctx)
    quiet = st.get("quiet")  # loans without any delinquency history (computed in section 8)
    r_a, r_t0 = rung_of(ctx, "strict_ns", "strict"), rung_of(ctx, "delinq_alone")
    inc_auc = st.get("incumbent_auc")
    a_auc = r_a["auc"] if r_a else None
    unrealistic = a_auc is not None and a_auc >= UNREALISTIC_AUC

    # ── 10a. what can you actually build?  (every AUC is computed on the exact columns and loans the row describes) ───────
    r_b = r_c = None
    if not ctx.args.no_models and set_b:
        with rep.attempt("Evaluating column sets B and C"):
            r_b = eval_set(ctx, set_b, folds=folds, **kw)
            r_c = eval_set(ctx, set_c, folds=folds, **kw) if set_c != set_b else r_b
    stt = st.get("state_track")
    trackers = stt["trackers"] if stt else []
    set_a_strict = [c for c in set_a if c not in trackers]  # A without the columns that follow the loan's arrears state
    r_as = None
    if trackers and set_a_strict and not ctx.args.no_models:
        with rep.attempt("Evaluating the conservative application set"):
            r_as = eval_set(ctx, set_a_strict, folds=folds, **kw)
    st["framing_auc"] = {
        "A": r_a["auc"] if r_a else None,
        "B": r_b["auc"] if r_b else None,
        "C": r_c["auc"] if r_c else None,
        "A_strict": r_as["auc"] if r_as else None,
    }
    st["A_strict"] = set_a_strict
    nq = None if quiet is None else ~quiet
    frames = pd.DataFrame(
        [
            {
                "framing": "A · Application scorecard",
                "decision it supports": "approve / price a loan before it exists",
                "columns allowed": f"{len(set_a)} columns fixed at origination (sensitive attributes and bureau-snapshot counts excluded)",
                "CV AUC on all loans": fmt_auc(r_a),
                "AUC where the decision is made": fmt_pop(pop_auc(r_a, y, quiet), "loans with no delinquency history")
                if r_a
                else "n/a (needs models)",
                "main risk": (
                    "AUC is unrealistically high - look for artefacts before believing it. " if unrealistic else ""
                )
                + "booked loans only (no rejected applicants), no origination date / vintage / outcome window, the label has no stated window",
            },
            *(
                [
                    {
                        "framing": "A′ · Conservative application scorecard",
                        "decision it supports": "the same, but without the codes that follow the loan's arrears state",
                        "columns allowed": f"{len(set_a_strict)} columns: A minus " + ", ".join(trackers),
                        "CV AUC on all loans": fmt_auc(r_as),
                        "AUC where the decision is made": fmt_pop(
                            pop_auc(r_as, y, quiet), "loans with no delinquency history"
                        )
                        if r_as
                        else "n/a (needs models)",
                        "main risk": "dropping a genuine risk driver (an interest rate, a sector) together with a state indicator; compare it with A to see how much of the signal survives",
                    }
                ]
                if trackers
                else []
            ),
            {
                "framing": "B · Portfolio monitoring",
                "decision it supports": "watch live loans that are not (yet) delinquent",
                "columns allowed": f"A + {len(set_b) - len(set_a)} bureau-snapshot / balance / schedule / loan-event columns (no delinquency columns, no sensitive attributes)",
                "CV AUC on all loans": fmt_auc(r_b),
                "AUC where the decision is made": fmt_pop(pop_auc(r_b, y, quiet), "loans with no delinquency history")
                if r_b
                else "n/a (needs models)",
                "main risk": "balance / term-to-go columns describe the loan's stage of life, which is tied to the outcome; few events in the population that matters",
            },
            {
                "framing": "C · Status detection / triage",
                "decision it supports": "rank loans that are already late",
                "columns allowed": f"B + {len(set_c) - len(set_b)} delinquency columns (supplied score and sensitive attributes excluded)",
                "CV AUC on all loans": fmt_auc(r_c),
                "AUC where the decision is made": fmt_pop(pop_auc(r_c, y, nq), "loans with a delinquency history")
                if r_c
                else "n/a (needs models)",
                "main risk": "the label is nearly built from these columns, so this reconstructs the status rather than predicting it; without dates, 'escalation' cannot be validated",
            },
            {
                "framing": "D · Beat the supplied score",
                "decision it supports": "challenge the supplied score",
                "columns allowed": "A or B, compared with " + (", ".join(inc) or "n/a"),
                "CV AUC on all loans": (f"supplied score: {inc_auc[0]:.3f}" if inc_auc else "n/a"),
                "AUC where the decision is made": (
                    f"supplied score: {inc_auc[1]:.3f} on loans with no delinquency history" if inc_auc else "n/a"
                ),
                "main risk": "the benchmark itself is driven by arrears information, so 'beating' it overall means little; its producer, horizon and default definition are unknown",
            },
            {
                "framing": "E · Reconstruct the label",
                "decision it supports": "understand what 'negative status' means",
                "columns allowed": f"{len(t0)} delinquency columns",
                "CV AUC on all loans": fmt_auc(r_t0),
                "AUC where the decision is made": "-",
                "main risk": "a reconstruction is not a prediction: "
                + (
                    f"the best small rule set recovers only {pct(st['recipe']['recall'], 0)} of the events"
                    if "recipe" in st
                    else "check what it recovers"
                ),
            },
        ]
    ).set_index("framing")
    rep.table(
        frames,
        "modeling_framings",
        "Five ways to use this data - and what each one can honestly achieve",
        f"Every AUC is a {folds}-fold cross-validated gradient-boosting result on exactly the columns listed, shown for all loans and for the loans the decision is about (± = 95% interval). Pick the framing first; it decides which columns are allowed.",
        max_rows=10,
        console=0,
    )

    # ── 10b. preprocessing recipe (every item is computed from the data) ───────────────────────────────────────────────────
    prof = st.get("profile")
    n_log = int(prof["transform"].str.contains("log1p").sum()) if prof is not None else 0
    q = st.get("quality")
    cens = q.get("censored", pd.DataFrame()) if q else pd.DataFrame()
    nominal = [c for c in feats if ctx.kinds[c] == "nominal"]
    big_nom = [c for c in nominal if df[c].nunique() > MAX_NOMINAL_LEVELS]
    groups = st.get("redundancy")
    nm = st.get("non_monotone")
    regimes = st.get("ratio_scales", {})
    ratio_txt = ""
    if regimes:
        ratio_txt = "; ".join(f"`{c}`: {r['verdict']}" for c, r in regimes.items())
    recipe = [
        f"**Types.** Cast the {len(nominal)} unordered code columns ({', '.join(nominal[:5])}{' …' if len(nominal) > 5 else ''}) to `category`; never feed them to a model as ordered numbers."
        + (
            f" `{big_nom[0]}` has {df[big_nom[0]].nunique():,} levels: use log-frequency + a coarse bucket (the thousands' digit of the code, ≈{int(round(df[big_nom[0]].max() / 1000))} buckets), not one-hot."
            if big_nom
            else ""
        ),
        f"**Skew.** {n_log} numeric columns are heavily right-skewed: `log1p` them for linear / neural models (trees do not need it); add an is-zero flag where more than 5% are zeros."
        if prof is not None
        else "**Skew.** (not computed: section 5 did not finish)",
        (
            (
                "**Censoring.** Add 'at limit' flags for "
                + ", ".join(f"`{r.column}` ({r.pattern} at {num(r.value)})" for r in cens.itertuples())
                + "."
            )
            if len(cens)
            else "**Censoring.** No capped / floored columns found."
        )
        if q
        else "**Censoring.** (not computed: section 2 did not finish)",
        f"**Redundancy.** {len(set(groups.values()))} groups of near-duplicate columns: keep one per group for linear models (see section 7)."
        if groups is not None
        else "**Redundancy.** (not computed: section 7 did not finish)",
        (
            (
                "**Non-linear effects.** "
                + ", ".join(f"`{c}`" for c in nm[:6])
                + " do not move monotonically with risk: bin them or use trees."
            )
            if nm
            else "**Non-linear effects.** No strong column has a clearly non-monotone effect."
        )
        if nm is not None or "non_monotone" in st
        else "**Non-linear effects.** (not computed: section 6 did not finish)",
        (
            f"**Ratios.** {ratio_txt}. Do not rebuild them from components: the dictionary's arithmetic does not hold here."
            if q and q.get("n_bad_rules", 0) >= MIN_BAD_RULES
            else (
                "**Ratios.** "
                + (ratio_txt + ". " if ratio_txt else "")
                + "The dictionary's arithmetic holds, so rebuilding ratios from components is possible."
            )
        )
        if q
        else "",
        f"**Sensitive attributes** ({', '.join(sens)}): decide the policy first; if you exclude them, test for proxies (section 9 group audit does this)."
        if sens
        else "",
    ]
    rep.text("### Preprocessing recipe\n\n" + "\n".join(f"- {r}" for r in recipe if r), console=False)

    # ── 10c. validation plan ─────────────────────────────────────────────────────────────────────────────────────────────
    n_pos, rate = int(y.sum()), ctx.base_rate
    pool = st.get("pooling")
    half_pool = 1.96 * auc_se(0.8, n_pos, len(y) - n_pos)
    half_fold = 1.96 * auc_se(0.8, n_pos // N_FOLDS, (len(y) - n_pos) // N_FOLDS)
    if pool is None:
        sealed = "**Sealed test.** (not computed: section 3 did not finish) - check whether the files are interchangeable before choosing a split."
    elif pool["interchangeable"]:
        half_sealed = 1.96 * auc_se(0.8, pool["pos_last"], pool["n_last"] - pool["pos_last"])
        sealed = (
            f"**Sealed test.** Keep one whole file ({pool['last_file']}: {pool['n_last']:,} rows, {pool['pos_last']} events) untouched until the very end; the files are interchangeable, so it is as good as a random split. "
            f"Its AUC will only be known to about ±{half_sealed:.3f}, so quote the interval."
        )
    else:
        sealed = "**Sealed test.** The files are NOT interchangeable: validate with GroupKFold by file and report per-file results."
    plan = [
        f"**Split.** Pool all {ctx.n:,} rows. Develop with stratified {N_FOLDS}-fold CV (repeat it with different seeds): the pooled out-of-fold AUC ({n_pos:,} events) is known to about ±{half_pool:.3f}, a single fold ({n_pos // N_FOLDS:,} events) only to ±{half_fold:.3f}. Compare models on the same folds.",
        sealed,
        "**Leakage guards.** Fit every preprocessing step (imputation, encoding, scaling, target encoding, feature selection) inside each training fold; choose features by role, not by test-set AUC.",
        f"**Metrics.** ROC-AUC (Gini = 2 x AUC - 1) and PR-AUC (a useless model scores {rate:.4f}), KS, lift in the riskiest 10%, Brier score and a calibration curve. Never accuracy ({pct(1 - rate, 1)} for 'always 0'). Use whatever metric the organisers announce for the final ranking.",
        "**Imbalance.** Start without resampling; compare `class_weight` only on PR-AUC and re-calibrate probabilities afterwards.",
        "**Time.** There is no date column, so out-of-time stability cannot be tested - say so when you report results.",
    ]
    rep.text("### Validation plan\n\n" + "\n".join(f"- {r}" for r in plan), console=False)

    # ── 10d. questions that change the modeling ─────────────────────────────────────────────────────────────────────────────
    has_id = any(ID_LIKE.search(c) for c in feats)
    qs = [
        "Which metric and submission format will be used for the final ranking (AUC / PR-AUC / F1; probabilities or labels)?",
        f"What exactly does `{ctx.target}` mean (the dictionary says only 'negative record statuses'): which statuses and delinquency definition build it, and over what time window?",
        "Are the supplied score / PD and the delinquency columns allowed as model inputs - or are they outcomes of the process we are supposed to model? Who produced the score and PD, and with which horizon?",
        "What is the snapshot date (the same for every loan?), and what are the loan ages / origination dates?",
        "Does the file contain closed, written-off or transferred loans? (Balances of 0 and a 0 remaining term are common among the events.)",
        "What currency and period do the amounts refer to (monthly GEL? nominal terms?), and what do PRINCIPAL_PAYMENT_FREQUENCY = 1 and ECONOMIC_SECTORS = 0 mean?",
        ""
        if has_id
        else "Was a loan / borrower ID or a reporting date removed? Can one borrower appear in several rows (it would break cross-validation)?",
        f"May {', '.join(sens)} be used as inputs?" if sens else "",
        f"Were the columns masked, perturbed or synthesised? {q['n_bad_rules']} of {q['n_rules']} dictionary relationships fail in more than {pct(RULE_TOL, 0)} of rows."
        if q and q.get("n_bad_rules", 0) >= MIN_BAD_RULES
        else "",
        f"Why do application-time columns alone reach AUC {a_auc:.2f}? Is the label derived from, or generated together with, those columns?"
        if unrealistic
        else "",
        f"Will the final test set look like these {len(ctx.file_info)} files, or come from a later period?",
    ]
    qs = [x for x in qs if x]
    st["n_questions"] = len(qs)
    rep.text(
        "### Questions to ask the organisers (the answers change the model)\n\n"
        + "\n".join(f"{i}. {x}" for i, x in enumerate(qs, 1)),
        console=False,
    )

    # ── 10e. machine-readable hand-off ────────────────────────────────────────────────────────────────────────────────────
    a_note = f" (the set jointly reaches AUC {a_auc:.2f}: see section 8)" if unrealistic else ""
    use = {
        "target": "target",
        "tier0_target_defining": "EXCLUDE when predicting the status; only for status detection / triage if the organisers allow",
        "incumbent_model_output": "benchmark only - not a feature",
        "post_origination_state": "state-aware models only (B, C) - verify availability",
        "snapshot_unverified": "B, C; verify the timing before using it in an application model",
        "origination_time": f"use (A, B, C) - the dictionary says known at origination, not proven{a_note}",
        "sensitive_attribute": "audit fairness; exclude unless policy allows",
    }
    recs = []
    for c in feats:
        notes = []
        if nm and c in nm:
            notes.append("non-monotone effect")
        if len(cens) and c in set(cens["column"]):
            notes.append("capped / floored")
        if groups and c in groups:
            notes.append("near-duplicate group: " + groups[c][:60])
        for mk_name in st.get("markers", {}).get("names", []):
            if mk_name.split("=")[0] == c:
                notes.append(f"event marker: {mk_name} (add a yes/no flag)")
        if c in sig.index and sig.loc[c, AUC_COL] >= LEAK_AUC:
            notes.append(f"single-column AUC {sig.loc[c, AUC_COL]:.2f}")
        if c in trackers:
            notes.append(
                "follows the loan's arrears state: "
                + str(
                    stt["table"].loc[c, "level with the biggest gap"]
                    or f"state AUC {stt['table'].loc[c, 'state AUC']:.2f}"
                )
            )
        stab = stt["table"] if stt else None
        recs.append(
            {
                "feature": c,
                "family": FAMILIES[family_of(c)][0],
                "kind": ctx.kinds[c],
                "role": ROLES[ctx.roles[c]][0],
                "role_key": ctx.roles[c],
                "recommended_use": use[ctx.roles[c]],
                "in_A_application": c in set_a,
                "in_A_strict": c in set_a_strict,
                "in_B_monitoring": c in set_b,
                "in_C_status_detection": c in set_c,
                "single_column_AUC": float(sig.loc[c, AUC_COL]) if c in sig.index else np.nan,
                "state_tracking_AUC": float(stab.loc[c, "state AUC"])
                if stab is not None and c in stab.index
                else np.nan,
                "tracks_loan_state": c in trackers,
                "IV": float(sig.loc[c, "IV"]) if c in sig.index else np.nan,
                "transform_for_linear_models": prof.loc[c, "transform"] if prof is not None and c in prof.index else "",
                "notes": "; ".join(notes),
            }
        )
    roles_df = pd.DataFrame(recs).set_index("feature")
    roles_df.to_csv(rep.out_dir / "feature_roles.csv")
    rep.table(
        roles_df,
        "feature_roles",
        "Feature roles - the hand-off table",
        "Also saved as reports/feature_roles.csv so a modeling script can load the feature sets (A / B / C) directly.",
        fmt={"single_column_AUC": "{:.3f}", "IV": "{:.3f}"},
        bars={"single_column_AUC": (0.5, 1)},
        max_rows=60,
    )
    starter = f'''import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

# 1) data + the column set chosen in section 10:  A = application (A_strict = without state-tracking codes), B = monitoring, C = status detection
df = pd.concat([pd.read_csv(f"Data/df_{{i}}.csv") for i in range(1, 11)], ignore_index=True)
roles = pd.read_csv("reports/feature_roles.csv", index_col="feature")
features = list(roles.index[roles["in_A_application"]])   # or "in_A_strict" / "in_B_monitoring" / "in_C_status_detection"
y = df["{ctx.target}"]

# 2) types: text and unordered codes -> category (never let a model read code 12 as 'twelve')
X = df[features].copy()
if "DEBT_DISBURSED_REGION" in X:                            # thousands of distinct codes -> a coarse bucket (thousands digit)
    X["DEBT_DISBURSED_REGION"] = (X["DEBT_DISBURSED_REGION"] / 1000).round()
as_category = [c for c in features if roles.loc[c, "kind"] == "nominal" or not pd.api.types.is_numeric_dtype(X[c])]
X[as_category] = X[as_category].astype("category")

# 3) honest estimate: stratified 5-fold, out-of-fold probabilities
model = HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=200, learning_rate=0.08, random_state=42)
cv = StratifiedKFold(5, shuffle=True, random_state=42)
oof = cross_val_predict(model, X, y, cv=cv, method="predict_proba")[:, 1]
print(f"ROC-AUC {{roc_auc_score(y, oof):.3f}}   PR-AUC {{average_precision_score(y, oof):.3f}}   (chance PR-AUC = {{y.mean():.3f}})")
'''
    rep.code("Starter code: load a feature set and get an honest baseline (copy & paste)", starter)
    st["starter"] = starter

    rep.finding(
        "info",
        "Pick the framing before the algorithm",
        "A, B and C use different columns and answer different business questions; their AUCs are not comparable with each other or with the supplied score.",
        "Start from the framing table above; load the matching column set from reports/feature_roles.csv.",
    )


# ══════════════════════════════════════════════════════════════════════════════════
# PART II · MOCK & SYNTHETIC BANK DATA (sections 11-14)
#   openbanking_mock/ simulates the 12 Georgian banks' open-banking APIs for 50,000 customers who are rows of Data/.
#   Everything here is READ-ONLY and aggregated: customer names, ids, phone numbers and IBANs are never read (the
#   counterparty columns of the transactions are only counted, never kept or shown), and nothing is
#   written inside the mock folder.  The one pass over the shards (scan_mock) runs once and feeds sections 11-14.
# ══════════════════════════════════════════════════════════════════════════════════
DEFAULT_MOCK_DIR = ROOT / "openbanking_mock" / "output" / "dataset"
MOCK_SECTIONS = frozenset({11, 12, 13, 14})
MOCK_FAST_SHARDS = 20  # --fast reads this many shards (250 customers each)
MOCK_TABLES = {  # layer -> tables the sections use; a shard of one table lines up with the same shard of the others
    "api": ("accounts", "balances", "transactions"),
    "truth": ("customers", "loans", "fx_rates"),
}
MOCK_LAYER_NOTE = {
    "api": "what a lender could retrieve through the 12 banks' APIs",
    "truth": "the simulator's hidden state - for evaluation only, never a feature",
}
# columns of Data/ that identify a loan row exactly (the mock stores the loan columns of its customers as df_*)
LINK_COLS = (
    "DISBURSED_AMOUNT", "Age", "INCOME_AMOUNT_TOTAL", "score", "TotalNumberDelinquencyDays",
    "OutstandingPrincipalAm", "AVERAGE_MONTHLY_PAYMENT", "addNegStatus",
)  # fmt: skip
# truth.customers columns that are read: no names, no person ids, no phone numbers, no birth dates
TRUTH_CUSTOMER_COLS = (
    "cid", "customer_index", "segment", "income_month_minor", "remittance_month_minor", "sector", "salary_day",
    "banks", "stress", "pd_pct", "score", "sim_tags",
)  # fmt: skip
SIM_TAGS = ("late_rate", "save_rate", "spend_scale", "overdue_planned", "loan_bank", "loan_visible")
MOCK_WEIGHT_TOL = 0.02  # a fitted weight within this of a round number counts as 'exactly that weight'
STRESS_PLANTED_R2 = 0.95  # latent stress explained at least this well by (PD rank, delinquency days, label) = a formula
MOCK_RHO_NOTABLE = 0.30  # |Spearman| with the latent stress above this is a behaviour 'wired to stress'

# ── what the scan needs to know about the bank APIs (the mock's own README documents all of it) ──
MOCK_TX_COLS = (  # the transaction columns that become pandas (the others are only counted, in Arrow)
    "bank", "psu_id", "source_endpoint", "status", "booking_date", "amount", "currency", "remittance_information",
    "bank_transaction_code", "merchant_category_code", "balance_after_amount", "is_card_mirror",
)  # fmt: skip
MOCK_FILL_FIELDS = (  # the transaction fields whose fill rate per bank is drawn in section 12
    "remittance_information", "bank_transaction_code", "merchant_category_code", "balance_after_amount",
    "creditor_name", "debtor_name", "end_to_end_id", "purpose_code", "masked_pan", "fx_rate", "mandate_id",
)  # fmt: skip
MOCK_ACC_COLS = (
    "bank", "psu_id", "source_endpoint", "resource_id", "currency", "product", "cash_account_type", "status",
    "credit_limit", "contract_start_date", "contract_end_date", "interest_rate_pct",
)  # fmt: skip
MOCK_BAL_COLS = ("psu_id", "source_endpoint", "resource_id", "balance_type", "amount", "currency", "balance_sub_type")

# The Georgian remittance texts the banks write; first match wins.  ISO 20022 codes are the fallback.
_TEXT_KINDS = [
    ("penalty", r"ჯარიმა|[Pp]enalt"),
    ("overdraft_fee", r"ოვერდრაფტის"),
    ("salary", r"^ხელფასი"),
    ("quick_repay", r"სწრაფი სესხის დაფარვა"),
    ("loan_repay", r"^სესხის დაფარვა|^Loan repayment"),
    ("quick_loan", r"^სწრაფი სესხი"),
    ("loan_in", r"^სესხის გაცემა"),
    ("cc_repay", r"საკრედიტო ბარათის დაფარვა|[Cc]redit card repayment"),
    ("fee", r"საკომისიო|\b[Ff]ee\b"),
    ("atm", r"ბანკომატიდან|ATM cash"),
    ("cash_in", r"ნაღდი ფულის შეტანა"),
    ("card", r"^ბარათით გადახდა|Purchase via POS"),
    ("remit", r"ფულადი გზავნილი|Money transfer"),
    ("own", r"საკუთარ ანგარიშზე|ანაბრ|ვალუტის კონვერტაცია"),
    ("p2p", r"პირადი გადარიცხვა"),
    ("rent", r"ბინის ქირა"),
    ("bill", r"ელექტროენერგია|მობილური|წყალი|ინტერნეტი|დასუფთავება|ბუნებრივი აირი|გადახდა მომსახურებაზე|საგადასახადო|გადახდა ხელშეკრულებით"),
]  # fmt: skip
TEXT_KINDS = [(k, re.compile(p)) for k, p in _TEXT_KINDS]
CODE_KINDS = {
    "PMNT-CCRD-POSD": "card", "PMNT-CCRD-CWDL": "atm", "PMNT-CCRD-CDPT": "cash_in", "PMNT-RCDT-SALA": "salary",
    "PMNT-RCDT-XBCT": "remit", "ACMT-MDOP-CHRG": "fee", "PMNT-RDDT-PMDD": "bill", "PMNT-ICDT-BOOK": "own",
    "PMNT-RCDT-BOOK": "own", "LDAS-FTDP-DPST": "own", "ACMT-MCOP-INTR": "interest",
}  # fmt: skip
NEUTRAL_KINDS = ("own", "cc_repay", "loan_in", "quick_loan")  # money moving between the customer's own products
DEBT_KINDS = ("loan_repay", "quick_repay")
FEE_KINDS = ("fee", "penalty", "overdraft_fee")
DEBIT_CODES = (
    "PMNT-CCRD-POSD",
    "PMNT-CCRD-CWDL",
    "PMNT-RDDT-PMDD",
    "ACMT-MDOP-CHRG",
    "PMNT-ICDT-DMCT",
    "PMNT-ICDT-BOOK",
)
CREDIT_CODES = (
    "PMNT-RCDT-SALA",
    "PMNT-RCDT-DMCT",
    "PMNT-RCDT-XBCT",
    "PMNT-RCDT-BOOK",
    "PMNT-CCRD-CDPT",
    "ACMT-MCOP-INTR",
)
GAMBLING = re.compile(r"BET|CASINO|POKER|VULKAN|SLOT", re.I)
MERCHANT = re.compile(r"^(?:ბარათით გადახდა - |ყიდვა/Purchase via POS terminal, )([^,]+)")
DAYS_PER_MONTH = 30.4375

# Features built from api/ only (name, family, definition).  The scan fills exactly these columns.
MOCK_FEATURES: list[tuple[str, str, str]] = [
    ("n_banks", "footprint", "banks where the customer has any product visible through the APIs"),
    ("n_current_accounts", "footprint", "accounts listed by the accounts endpoint (all currencies, without the XXX parent aggregate)"),
    ("n_fx_accounts", "footprint", "current accounts in a foreign currency"),
    ("n_cards", "footprint", "card accounts listed by the card-accounts endpoint (0 at banks whose API has none)"),
    ("has_credit_card", "footprint", "1 if any product is a credit card"),
    ("credit_limit_ratio", "footprint", "sum of the credit limits of the card accounts / monthly inflow"),
    ("has_savings", "footprint", "1 if a savings / deposit resource is visible"),
    ("inflow_month", "cash flow", "average monthly inflow in GEL (salary, remittances, transfers in, cash in)"),
    ("spend_month", "cash flow", "average monthly spending in GEL (cards, ATM, transfers out, bills, rent)"),
    ("net_flow_ratio", "cash flow", "(inflow - spending - loan and micro-loan repayments - fees) / inflow over the 12 months; credit-card repayments are not subtracted again (their purchases are in spending when the card account is visible); loans paid out as a lump sum are not spending"),
    ("spend_to_inflow", "cash flow", "spending / inflow"),
    ("inflow_cv", "cash flow", "coefficient of variation of the 12 monthly inflows"),
    ("spend_cv", "cash flow", "coefficient of variation of the 12 monthly spending totals"),
    ("neg_months", "cash flow", "months (of 12) in which outflows exceeded inflows"),
    ("tx_per_month", "cash flow", "booked entries per month (own-account moves included)"),
    ("inflow_trend", "cash flow", "inflow of the last 3 months / inflow of the first 3 months"),
    ("salary_month", "income", "average salary credit (entries typed salary) in GEL; missing if none"),
    ("salary_months", "income", "months (of 12) with a salary credit"),
    ("salary_cv", "income", "coefficient of variation of the salary credits"),
    ("salary_day_std", "income", "standard deviation of the booking day-of-month of the salary credits"),
    ("remit_month", "income", "average monthly cross-border / remittance inflow in GEL"),
    ("p2p_in_share", "income", "personal transfers in / inflow"),
    ("card_spend_month", "spending", "average monthly card purchases in GEL"),
    ("card_ticket", "spending", "average card purchase in GEL"),
    ("atm_share", "spending", "ATM cash / spending"),
    ("bills_share", "spending", "utility bills, taxes, service payments and rent / spending"),
    ("rent_month", "spending", "average monthly rent payment in GEL (0 if none)"),
    ("p2p_out_share", "spending", "personal transfers out / spending"),
    ("n_merchants", "spending", "distinct merchants named in card purchases (only banks that print them)"),
    ("gambling_n", "spending", "card purchases at betting / casino merchants (name or MCC 7995)"),
    ("debt_service_ratio", "debt signals", "loan, micro-loan and credit-card repayments paid from the account / inflow"),
    ("loan_repay_month", "debt signals", "average monthly loan repayment seen as a debit text, in GEL"),
    ("quick_loan_n", "debt signals", "micro-loan ('quick loan') disbursements received"),
    ("penalty_n", "debt signals", "late-payment penalty entries ('fine for overdue')"),
    ("overdraft_fee_n", "debt signals", "overdraft-fee entries"),
    ("fee_share", "debt signals", "bank fees and penalties / inflow"),
    ("bal_min_ratio", "balance", "lowest running balance after any entry / monthly inflow (current accounts, GEL)"),
    ("bal_mean_ratio", "balance", "average running balance after an entry (entry-weighted, not time-weighted) / monthly inflow"),
    ("bal_neg_share", "balance", "share of entries after which the running balance is negative"),
    ("end_balance_ratio", "balance", "closing balance of the current accounts today / monthly inflow"),
    ("savings_ratio", "balance", "balance of the savings resources / monthly inflow"),
    ("loan_visible", "loan seen via API", "1 if a loan resource is visible at a connected bank"),
    ("loan_rate_pct", "loan seen via API", "interest rate of the visible loan (%)"),
    ("loan_age_months", "loan seen via API", "months since the visible loan's contract start"),
    ("loan_remaining_months", "loan seen via API", "months until the visible loan's contract end"),
    ("loan_repaid_share", "loan seen via API", "1 - closing balance / opening balance of the visible loan"),
    ("loan_outstanding_ratio", "loan seen via API", "outstanding loan balance / annual inflow"),
    ("loan_overdue_visible", "loan seen via API", "1 if the API shows an OVERDUE_AMOUNT balance"),
    ("loan_acct_repay_month", "loan seen via API", "average monthly repayments recorded on the loan resource, in GEL"),
]  # fmt: skip
MOCK_FEATURE_NAMES = [f[0] for f in MOCK_FEATURES]
MOCK_FAMILY = {name: fam for name, fam, _ in MOCK_FEATURES}
MOCK_FAMILIES = list(dict.fromkeys(f[1] for f in MOCK_FEATURES))
# the families that exist only because a loan or its arrears are visible: not 'behaviour' in the plain sense
MOCK_LOAN_FAMILIES = ("debt signals", "loan seen via API")


@dataclass
class MockWorld:
    """A mock dataset folder that passed the checks in open_mock (nothing has been read from it yet, except MANIFEST)."""

    root: Path
    manifest: dict
    shards: list[int]  # shard indexes that are read (all of them, or the first MOCK_FAST_SHARDS with --fast)
    n_shards: int  # shards in the folder
    explicit: bool  # the user passed --mock-dir

    def path(self, layer: str, table: str, shard: int) -> Path:
        return self.root / layer / table / f"part-{shard:05d}.parquet"

    @property
    def name(self) -> str:
        """The folder as the report writes it: relative to the project when possible (reports get shared: no home paths)."""
        try:
            return self.root.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return self.root.name


def open_mock(args: argparse.Namespace) -> tuple[MockWorld | None, str]:
    """Find the mock dataset.  Returns (world, '') or (None, why not).  Nothing is read except MANIFEST.json."""
    if args.no_mock:
        return None, "switched off with --no-mock"
    explicit = args.mock_dir is not None
    if explicit:
        root = Path(args.mock_dir)
    elif Path(args.data_dir).resolve() == DEFAULT_DATA_DIR.resolve():
        root = DEFAULT_MOCK_DIR
    else:
        return None, "not looked for: --data-dir is not the default (pass --mock-dir to analyse a mock dataset)"
    try:
        import pyarrow.parquet  # noqa: F401  (only the mock sections need it)
    except ImportError:
        return None, "pyarrow is not installed (run: uv sync)"
    if not (root / "MANIFEST.json").is_file():
        return None, f"no mock dataset at {root.name}/ (it has no MANIFEST.json)"
    try:
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"MANIFEST.json could not be read ({exc!r}); the folder may be mid-generation"
    found = {
        f"{layer}/{t}": {
            int(m.group(1))
            for p in (root / layer / t).glob("part-*.parquet")
            if (m := re.fullmatch(r"part-(\d+)", p.stem))
        }
        for layer, tables in MOCK_TABLES.items()
        for t in tables
        if t != "fx_rates"
    }
    common = sorted(set.intersection(*found.values())) if found else []  # a shard counts only if every table has it
    if not common:
        empty = ", ".join(k for k, v in found.items() if not v) or "the tables share no shard number"
        return None, f"no usable parquet shards ({empty})"
    shards = common[:MOCK_FAST_SHARDS] if args.fast else common
    return MockWorld(root, manifest, shards, len(common), explicit), ""


def read_frame(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    """One parquet shard -> pandas with exactly `columns` (missing ones become NaN); decimals -> float, dates -> datetime."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    have = set(pf.schema_arrow.names)
    table = pf.read(columns=[c for c in columns if c in have])
    for i, f in enumerate(table.schema):
        if pa.types.is_decimal(f.type):
            table = table.set_column(i, f.name, table.column(i).cast(pa.float64()))
    return table.to_pandas(date_as_object=False).reindex(columns=list(columns))


def read_transactions(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """One transactions shard -> (the columns the features need, as pandas; how many entries of each bank have each of
    MOCK_FILL_FIELDS; duplicate entries within a (bank, endpoint)).  The fill counts and the duplicate check run in Arrow,
    so the string columns that only feed them never become Python objects (that halves the peak memory of the scan)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    names = set(pf.schema_arrow.names)
    if missing := {"bank", "psu_id", "status", "booking_date", "amount", "currency"} - names:
        raise ValueError(f"{path.name} lacks the transaction columns {sorted(missing)}")
    table = pf.read(columns=[c for c in dict.fromkeys([*MOCK_TX_COLS, *MOCK_FILL_FIELDS, "entry_key"]) if c in names])
    fields = [c for c in MOCK_FILL_FIELDS if c in names]
    if "bank" in names and fields:
        agg = table.group_by("bank").aggregate([(c, "count") for c in fields]).to_pandas().set_index("bank")
        agg.columns = [c.removesuffix("_count") for c in agg.columns]
    else:
        agg = pd.DataFrame(columns=list(MOCK_FILL_FIELDS))
    keys = ["bank", "source_endpoint", "entry_key"]
    dups = table.num_rows - table.group_by(keys).aggregate([]).num_rows if set(keys) <= names else 0
    keep = table.select([c for c in MOCK_TX_COLS if c in names])
    for i, f in enumerate(keep.schema):
        if pa.types.is_decimal(f.type):
            keep = keep.set_column(i, f.name, keep.column(i).cast(pa.float64()))
    tx = keep.to_pandas(date_as_object=False).reindex(columns=list(MOCK_TX_COLS))
    return tx, agg.reindex(columns=list(MOCK_FILL_FIELDS), fill_value=0), int(dups)


def text_kinds(texts: pd.Series) -> np.ndarray:
    """Kind of every remittance text.  Each distinct text is classified once (thousands, not hundreds of thousands)."""
    codes, uniq = pd.factorize(texts)
    labels = [next((k for k, rx in TEXT_KINDS if rx.search(u)), "other") for u in uniq]
    return np.array([*labels, "none"], dtype=object)[codes]  # code -1 (no text) -> "none"


def merchants_of(texts: pd.Series) -> pd.Series:
    """Merchant from a card-purchase text ('ბარათით გადახდა - CARREFOUR' -> 'CARREFOUR'); missing if it is not one."""
    codes, uniq = pd.factorize(texts)
    names = [m.group(1).strip() if (m := MERCHANT.match(u)) else None for u in uniq]
    return pd.Series(np.array([*names, None], dtype=object)[codes], index=texts.index)


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """num / den, missing where the denominator is not positive."""
    return num / den.where(den > 0)


def scan_shard(job: tuple) -> tuple[pd.DataFrame, dict]:
    """One shard (250 customers) -> (one row of features per customer, counters for sections 11-13).

    Only what a TPP could retrieve is used: api/accounts, api/balances, api/transactions.  Card mirrors are dropped,
    pending entries are neither income nor spending, foreign currencies are converted at the 12-month mean NBG rate."""
    tx_path, acc_path, bal_path, cids, p = job
    tx, fill, n_dup = read_transactions(tx_path)
    acc = read_frame(acc_path, MOCK_ACC_COLS)
    bal = read_frame(bal_path, MOCK_BAL_COLS)
    months = p["months"]
    start, asof = pd.Timestamp(p["start"]), pd.Timestamp(p["as_of"])
    tx["is_card_mirror"] = tx["is_card_mirror"].fillna(False).astype(bool)
    acct: dict = {}

    # ── counters on every row of the shard ──
    acct["rows_bank"] = tx.groupby("bank").size()
    acct["endpoint_rows"] = tx.groupby(["bank", "source_endpoint"]).size()
    acct["fill"] = fill
    booked_all, pending = tx["status"] == "booked", tx["status"] == "pending"
    code = tx["bank_transaction_code"]
    acct["hygiene"] = {
        "rows": len(tx),
        "pending": int(pending.sum()),
        "mirrors": int(tx["is_card_mirror"].sum()),
        "duplicate_entries": n_dup,
        "booked_without_date": int((booked_all & tx["booking_date"].isna()).sum()),
        "pending_with_date": int((pending & tx["booking_date"].notna()).sum()),
        "outside_window": int((booked_all & ((tx["booking_date"] < start) | (tx["booking_date"] > asof))).sum()),
        "zero_amount": int((tx["amount"] == 0).sum()),
        "debit_code_credited": int((code.isin(DEBIT_CODES) & (tx["amount"] > 0) & ~tx["is_card_mirror"]).sum()),
        "credit_code_debited": int((code.isin(CREDIT_CODES) & (tx["amount"] < 0) & ~tx["is_card_mirror"]).sum()),
        "coded_rows": int(code.notna().sum()),
    }  # fmt: skip

    # ── the working frame: booked entries that are not card mirrors ──
    t = tx[~tx["is_card_mirror"] & booked_all & tx["booking_date"].notna()]
    acct["hygiene"]["no_fx_rate"] = int((~t["currency"].isin(list(p["fx"]))).sum())  # counted 1:1: say how many
    rate = t["currency"].map(p["fx"]).fillna(1.0).to_numpy(float)
    a = t["amount"].to_numpy(float) * rate
    ep = t["source_endpoint"].to_numpy(object)
    kind = text_kinds(t["remittance_information"])
    by_code = t["bank_transaction_code"].map(CODE_KINDS).to_numpy(object)
    fallback = np.where(pd.notna(by_code), by_code, np.where(t["merchant_category_code"].notna(), "card", "other"))
    kind = np.where(np.isin(kind, ("none", "other")), fallback, kind)
    kind = np.where(ep == "loans", "loan_acct", np.where(ep == "savings", "own", kind))
    # a debit on a credit-card account is a card purchase even when the bank prints neither text nor MCC (TBC)
    kind = np.where((ep == "card-accounts") & (kind == "other") & (a < 0), "card", kind)
    merchant = merchants_of(t["remittance_information"])
    is_bet = (
        merchant.fillna("").str.contains(GAMBLING).to_numpy() | (t["merchant_category_code"] == "7995").to_numpy()
    ) & (kind == "card")  # fmt: skip
    # window month k = the calendar month `start month + k`; the window starts and ends mid-month, so the partial first
    # and last calendar months are pooled into month 0 and every fixed pay day (also one that sits next to the window's
    # start day, and one that is moved by a weekend) lands exactly once in each of the 12 months
    cal = (
        (t["booking_date"].dt.year.to_numpy().astype(np.int64) - start.year) * 12
        + t["booking_date"].dt.month.to_numpy()
        - start.month
    )
    mi = np.clip(np.where(cal >= months, cal - months, cal), 0, months - 1)
    pos, neg = a > 0, a < 0
    # a loan paid out as a lump sum arrives as a credit ('loan disbursed') and leaves again as one debit of the same amount
    # (a contract payment, a property purchase): that is money passing through, not spending
    psu_code = pd.factorize(t["psu_id"])[0].astype(np.int64)
    key = psu_code * 10**11 + np.rint(np.abs(a) * 100).astype(np.int64)
    passes_through = neg & np.isin(kind, ("bill", "other")) & np.isin(key, key[(kind == "loan_in") & pos])
    kind = np.where(passes_through, "own", kind)
    neutral = np.isin(kind, NEUTRAL_KINDS)
    debt = np.isin(kind, DEBT_KINDS) | ((kind == "cc_repay") & neg)
    fees = np.isin(kind, FEE_KINDS)
    loan_acct = kind == "loan_acct"
    spend_mask = neg & ~neutral & ~debt & ~fees & ~loan_acct
    bal_after = np.where(
        (ep == "accounts") & (t["currency"] == "GEL").to_numpy(), t["balance_after_amount"].to_numpy(float), np.nan
    )
    dom = t["booking_date"].dt.day.to_numpy()
    wday = t["booking_date"].dt.weekday.to_numpy()
    is_card = (kind == "card") & neg

    def when(mask: np.ndarray, sign: int = 1) -> np.ndarray:
        return np.where(mask, sign * a, 0.0)

    w = pd.DataFrame(
        {
            "psu": t["psu_id"].to_numpy(),
            "mi": mi,
            "inflow": when(pos & ~neutral & ~loan_acct & ~fees),
            "spend": when(spend_mask, -1),
            "debt": when(debt & neg, -1),
            # credit-card repayments are left out here: the card purchases they pay for are already counted as spending
            "debt_loans": when(np.isin(kind, DEBT_KINDS) & neg, -1),
            "fees": when(fees & neg, -1),
            "salary": when((kind == "salary") & pos),
            "remit": when((kind == "remit") & pos),
            "p2p_in": when((kind == "p2p") & pos),
            "card": when(is_card, -1),
            "atm": when((kind == "atm") & neg, -1),
            "p2p_out": when((kind == "p2p") & neg, -1),
            "bills": when(np.isin(kind, ("bill", "rent")) & neg, -1),
            "rent": when((kind == "rent") & neg, -1),
            "loan_repay": when((kind == "loan_repay") & neg, -1),
            "loan_acct": when(loan_acct & pos),
            "n": 1.0,
            "n_card": is_card.astype(float),
            "n_bet": (is_bet & neg).astype(float),
            "n_penalty": (kind == "penalty").astype(float),
            "n_od_fee": (kind == "overdraft_fee").astype(float),
            "n_quick": ((kind == "quick_loan") & pos).astype(float),
            "dom_sal": np.where((kind == "salary") & pos, dom, np.nan),
            "bal": bal_after,
            "bal_neg": np.where(np.isnan(bal_after), np.nan, (bal_after < 0).astype(float)),
        }
    )  # fmt: skip
    del tx, t  # the arrays above hold everything still needed; free the big frames before the group-bys
    sum_cols = [c for c in w.columns if c not in ("psu", "mi", "dom_sal", "bal", "bal_neg")]
    g = w.groupby("psu", sort=False)
    s = g[sum_cols].sum()
    s["bal_min"], s["bal_mean"], s["bal_neg_share"] = g["bal"].min(), g["bal"].mean(), g["bal_neg"].mean()
    mm = w.groupby(["psu", "mi"], sort=False)[["inflow", "spend", "debt_loans", "fees"]].sum()
    mon = {c: mm[c].unstack(fill_value=0.0).reindex(columns=range(months), fill_value=0.0) for c in mm.columns}
    net_m = mon["inflow"] - mon["spend"] - mon["debt_loans"] - mon["fees"]
    sal = w[w["salary"] > 0].groupby("psu", sort=False)
    sal_stats = pd.DataFrame(
        {
            "salary_month": sal["salary"].mean(),
            "salary_months": sal["mi"].nunique(),
            "salary_cv": sal["salary"].std(ddof=0) / sal["salary"].mean(),
            "salary_day_std": sal["dom_sal"].std(ddof=0),
        }
    )

    # ── accounts and balances ──
    ep_a = acc["source_endpoint"]
    # the account type field is filled differently by every bank (TBC types its current account CARD, the card-accounts
    # rows have no type at all), so accounts and cards are told apart by the endpoint that lists them
    cur = acc[(ep_a == "accounts") & (acc["currency"] != "XXX")]
    cards = acc[ep_a == "card-accounts"].drop_duplicates(["psu_id", "resource_id"])
    credit = cards
    loans = acc[ep_a == "loans"]
    foot = pd.DataFrame(
        {
            "n_banks": acc.groupby("psu_id")["bank"].nunique(),
            "n_current_accounts": cur.groupby("psu_id").size(),
            "n_fx_accounts": cur[cur["currency"] != "GEL"].groupby("psu_id").size(),
            "n_cards": cards.groupby("psu_id").size(),
            "has_credit_card": credit[credit["product"].fillna("").str.contains("credit", case=False)].groupby("psu_id").size().clip(upper=1),
            "credit_limit_total": credit.groupby("psu_id")["credit_limit"].sum(),
            "has_savings": acc[ep_a == "savings"].groupby("psu_id").size().clip(upper=1),
            "loan_visible": loans.groupby("psu_id").size().clip(upper=1),
            "loan_rate_pct": loans.groupby("psu_id")["interest_rate_pct"].mean(),
            "loan_start": loans.groupby("psu_id")["contract_start_date"].min(),
            "loan_end": loans.groupby("psu_id")["contract_end_date"].max(),
        }
    )  # fmt: skip
    bal = bal.drop_duplicates(["psu_id", "resource_id", "balance_type", "balance_sub_type"])
    bt, bep = bal["balance_type"].fillna("").str.lower(), bal["source_endpoint"]
    bal_f = pd.DataFrame(
        {
            "bal_now": bal[(bep == "accounts") & (bt == "closingbooked") & (bal["currency"] == "GEL")].groupby("psu_id")["amount"].sum(),
            "sav_bal": bal[(bep == "savings") & (bt == "closingbooked") & (bal["currency"] == "GEL")].groupby("psu_id")["amount"].sum(),
            # some banks list a loan balance as a negative number (a liability), others as a positive one: use the size
            "loan_close": bal[(bep == "loans") & (bt == "closingbooked") & bal["balance_sub_type"].isna()].assign(amount=lambda d: d["amount"].abs()).groupby("psu_id")["amount"].sum(),
            "loan_open": bal[(bep == "loans") & (bt == "openingbooked")].assign(amount=lambda d: d["amount"].abs()).groupby("psu_id")["amount"].sum(),
            "loan_overdue_visible": bal[bal["balance_sub_type"].fillna("") == "OVERDUE_AMOUNT"].groupby("psu_id").size().clip(upper=1),
        }
    )  # fmt: skip

    # ── one row per customer of the shard ──
    idx = pd.Index(cids, name="cid")
    s, foot, bal_f, sal_stats = (x.reindex(idx) for x in (s, foot, bal_f, sal_stats))
    inflow, spend = s["inflow"].fillna(0), s["spend"].fillna(0)
    inflow_m = inflow / months
    inflow_mm, spend_mm = mon["inflow"].reindex(idx, fill_value=0.0), mon["spend"].reindex(idx, fill_value=0.0)
    visible = foot["loan_visible"].fillna(0) > 0
    f = pd.DataFrame(index=idx)
    f["n_banks"] = foot["n_banks"].fillna(0)
    f["n_current_accounts"] = foot["n_current_accounts"].fillna(0)
    f["n_fx_accounts"] = foot["n_fx_accounts"].fillna(0)
    f["n_cards"] = foot["n_cards"].fillna(0)
    f["has_credit_card"] = foot["has_credit_card"].fillna(0)
    f["credit_limit_ratio"] = _ratio(foot["credit_limit_total"].fillna(0), inflow_m)
    f["has_savings"] = foot["has_savings"].fillna(0)
    f["inflow_month"] = inflow_m
    f["spend_month"] = spend / months
    f["net_flow_ratio"] = _ratio(inflow - spend - s["debt_loans"].fillna(0) - s["fees"].fillna(0), inflow).clip(-5, 1)
    f["spend_to_inflow"] = _ratio(spend, inflow).clip(upper=10)
    f["inflow_cv"] = _ratio(inflow_mm.std(axis=1, ddof=0), inflow_mm.mean(axis=1))
    f["spend_cv"] = _ratio(spend_mm.std(axis=1, ddof=0), spend_mm.mean(axis=1))
    f["neg_months"] = (net_m.reindex(idx, fill_value=0.0) < 0).sum(axis=1)
    f["tx_per_month"] = s["n"].fillna(0) / months
    f["inflow_trend"] = _ratio(inflow_mm.iloc[:, -3:].sum(axis=1), inflow_mm.iloc[:, :3].sum(axis=1))
    f["salary_month"] = sal_stats["salary_month"]
    f["salary_months"] = sal_stats["salary_months"].fillna(0)
    f["salary_cv"] = sal_stats["salary_cv"]
    f["salary_day_std"] = sal_stats["salary_day_std"]
    f["remit_month"] = s["remit"].fillna(0) / months
    f["p2p_in_share"] = _ratio(s["p2p_in"].fillna(0), inflow)
    f["card_spend_month"] = s["card"].fillna(0) / months
    f["card_ticket"] = _ratio(s["card"].fillna(0), s["n_card"].fillna(0))
    f["atm_share"] = _ratio(s["atm"].fillna(0), spend)
    f["bills_share"] = _ratio(s["bills"].fillna(0), spend)
    f["rent_month"] = s["rent"].fillna(0) / months
    f["p2p_out_share"] = _ratio(s["p2p_out"].fillna(0), spend)
    named = merchant.notna().to_numpy() & is_card
    f["n_merchants"] = (
        pd.Series(merchant[named].to_numpy(), index=w["psu"][named]).groupby(level=0).nunique().reindex(idx).fillna(0)
    )
    f["gambling_n"] = s["n_bet"].fillna(0)
    f["debt_service_ratio"] = _ratio(s["debt"].fillna(0), inflow).clip(upper=10)
    f["loan_repay_month"] = s["loan_repay"].fillna(0) / months
    f["quick_loan_n"] = s["n_quick"].fillna(0)
    f["penalty_n"] = s["n_penalty"].fillna(0)
    f["overdraft_fee_n"] = s["n_od_fee"].fillna(0)
    f["fee_share"] = _ratio(s["fees"].fillna(0), inflow).clip(upper=10)
    f["bal_min_ratio"] = _ratio(s["bal_min"], inflow_m)
    f["bal_mean_ratio"] = _ratio(s["bal_mean"], inflow_m)
    f["bal_neg_share"] = s["bal_neg_share"]
    f["end_balance_ratio"] = _ratio(bal_f["bal_now"], inflow_m)
    f["savings_ratio"] = _ratio(bal_f["sav_bal"].fillna(0), inflow_m)
    f["loan_visible"] = visible.astype(float)
    f["loan_rate_pct"] = foot["loan_rate_pct"]
    f["loan_age_months"] = ((asof - pd.to_datetime(foot["loan_start"])).dt.days / DAYS_PER_MONTH).where(visible)
    f["loan_remaining_months"] = ((pd.to_datetime(foot["loan_end"]) - asof).dt.days / DAYS_PER_MONTH).where(visible)
    f["loan_repaid_share"] = (1 - _ratio(bal_f["loan_close"], bal_f["loan_open"])).where(visible)
    f["loan_outstanding_ratio"] = _ratio(bal_f["loan_close"], inflow).where(visible)
    f["loan_overdue_visible"] = bal_f["loan_overdue_visible"].fillna(0).where(visible, 0)
    f["loan_acct_repay_month"] = (s["loan_acct"].fillna(0) / months).where(visible)
    f = f[MOCK_FEATURE_NAMES].astype("float32")

    # ── counters for the realism checks (card purchases, salaries, months) ──
    cents = np.rint(np.abs(a[is_card]) * 100).astype(np.int64)
    lead = (
        np.floor(cents[cents > 0] / 10 ** np.floor(np.log10(cents[cents > 0]))).astype(int)
        if (cents > 0).any()
        else np.array([], int)
    )
    atm_a = np.abs(a[(kind == "atm") & neg])
    acct["weekday_card"] = np.bincount(wday[is_card], minlength=7)
    acct["salary_dom"] = np.bincount(dom[(kind == "salary") & pos], minlength=32)
    acct["month_rows"] = np.bincount(mi, minlength=months)
    acct["month_spend"] = np.bincount(mi, weights=np.where(spend_mask, -a, 0.0), minlength=months)
    acct["cents_last"] = np.bincount(cents % 10, minlength=10)
    acct["cents_round"] = np.array([int((cents % 100 == 0).sum()), len(cents)])
    acct["lead_digit"] = np.bincount(lead, minlength=10)[1:10]
    acct["atm_round"] = np.array([int((atm_a % 10 == 0).sum()), len(atm_a)])
    acct["merchants"] = Counter(merchant[named])
    acct["loan_pairs"] = loans.groupby(["psu_id", "bank"]).size().rename("n_loan_resources").reset_index()
    return f, acct


def merge_counters(parts: Sequence[dict]) -> dict:
    """Add the per-shard counters of scan_shard together."""
    out: dict = {}
    for part in parts:
        for k, v in part.items():
            if k not in out:
                out[k] = v.copy() if hasattr(v, "copy") else v
            elif k == "hygiene":
                out[k] = {kk: out[k][kk] + v[kk] for kk in v}
            elif k == "loan_pairs":
                out[k] = pd.concat([out[k], v], ignore_index=True)
            elif isinstance(v, pd.DataFrame | pd.Series):
                out[k] = out[k].add(v, fill_value=0)
            else:  # numpy arrays and Counters add up
                out[k] = out[k] + v
    return out


def scan_mock(ctx: Ctx) -> dict:
    """The one pass over the mock shards: truth.customers + the API features of every customer + counters.

    Streams shard by shard (one shard in memory at a time) and keeps only per-customer rows.  A shard that cannot be
    read is skipped and listed; if most of them fail the whole scan fails (the folder may be mid-generation)."""
    import pyarrow.parquet as pq

    world, rep = ctx.store["mock_world"], ctx.rep
    t0 = time.perf_counter()
    cfg = world.manifest.get("config", {})
    if "as_of" not in cfg:
        raise ValueError("MANIFEST.json has no config.as_of: cannot place the 12-month window")
    as_of, months = pd.Timestamp(cfg["as_of"]), int(cfg.get("months", 12))
    window = {"as_of": as_of, "months": months, "start": as_of - pd.DateOffset(months=months)}
    fx_files = sorted((world.root / "truth" / "fx_rates").glob("*.parquet"))
    fx_mean = {"GEL": 1.0}
    if fx_files:  # the public NBG rate table (not customer data): mean rate of each currency over the 12-month window
        fx = read_frame(fx_files[0], ["date", "currency", "gel_per_unit"])
        in_window = fx[(fx["date"] >= window["start"]) & (fx["date"] <= as_of)]
        fx_mean.update((in_window if len(in_window) else fx).groupby("currency")["gel_per_unit"].mean().to_dict())
    else:
        rep.say("   (no truth/fx_rates table: foreign-currency amounts are counted 1:1)")
    window["fx"] = fx_mean

    truth_parts, feat_parts, count_parts, loan_parts, failed = [], [], [], [], []
    loan_cols = ("loan_id", "bank", "cid", "product", "principal_minor", "rate", "term_months", "start", "maturity",
                 "instalment_minor", "outstanding_principal_minor", "overdue_principal_minor", "overdue_count", "days_in_delinquency")  # fmt: skip
    for k, i in enumerate(world.shards, 1):
        try:
            names = pq.ParquetFile(world.path("truth", "customers", i)).schema_arrow.names
            cols = [c for c in TRUTH_CUSTOMER_COLS if c in names] + [c for c in names if c.startswith("df_")]
            cust = read_frame(world.path("truth", "customers", i), cols)
            cust["shard"] = i
            feats, counts = scan_shard(
                (
                    world.path("api", "transactions", i),
                    world.path("api", "accounts", i),
                    world.path("api", "balances", i),
                    cust["cid"].tolist(),
                    window,
                )
            )
            loans = read_frame(world.path("truth", "loans", i), loan_cols)
        except Exception as exc:  # a half-written or corrupt shard must not lose the other 199
            failed.append((i, repr(exc)))
            continue
        truth_parts.append(cust)
        feat_parts.append(feats)
        count_parts.append(counts)
        loan_parts.append(loans)
        if k % max(len(world.shards) // 5, 1) == 0 or k == len(world.shards):
            rep.say(f"   scanning the bank-API shards: {k}/{len(world.shards)}  ({time.perf_counter() - t0:.0f}s)")
    if not truth_parts or len(failed) > 0.25 * len(world.shards):
        raise RuntimeError(f"{len(failed)} of {len(world.shards)} shards could not be read (first: {failed[:1]})")
    truth = pd.concat(truth_parts, ignore_index=True)
    tags = pd.DataFrame([json.loads(s) if isinstance(s, str) else {} for s in truth["sim_tags"]], index=truth.index)
    for key in SIM_TAGS:
        truth[f"sim_{key}"] = tags.get(key, np.nan)
    truth = truth.drop(columns=["sim_tags"]).set_index("cid")
    feats = pd.concat(feat_parts)
    quality = {}
    with suppress(OSError, ValueError):  # the generator's own report is a bonus
        quality = json.loads((world.root / "quality.json").read_text(encoding="utf-8"))
    return {
        "cust": truth.join(feats),
        "counts": merge_counters(count_parts),
        "loans": pd.concat(loan_parts, ignore_index=True),
        "quality": quality,
        "window": window,
        "failed": failed,
        "seconds": time.perf_counter() - t0,
    }


def mock_scan_of(ctx: Ctx) -> dict:
    """The scan result, computed by the first mock section that needs it and shared with the others."""
    if "mock_scan" not in ctx.store:
        ctx.store["mock_scan"] = scan_mock(ctx)
    return ctx.store["mock_scan"]


def binarize_flag(s: pd.Series, spec: str) -> pd.Series:
    """The 0/1 flag --binarize builds, for another copy of the same column (spec was validated in build_context)."""
    m = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*", spec)
    op, thr = m.group(1), float(m.group(2))
    return {">=": s >= thr, "<=": s <= thr, "==": s == thr, "!=": s != thr, ">": s > thr, "<": s < thr}[op]


def mock_ctx(ctx: Ctx) -> Ctx | None:
    """A Ctx over the mock customers - their loan columns (the df_* copies of Data/) plus the API features - so
    every model helper of sections 1-10 works on them unchanged.  None if the target is not one of the loan columns."""
    st = ctx.store
    if "mock_ctx" in st:
        return st["mock_ctx"]
    cust = mock_scan_of(ctx)["cust"]
    base = st.get("derived_from") or ctx.target
    if f"df_{base}" not in cust:
        st["mock_ctx"] = None
        return None
    raw = cust[f"df_{base}"]
    flag = binarize_flag(raw, ctx.args.binarize) if ctx.args.binarize else raw == st["positive_label"]
    loan_cols = [c for c in ctx.features if f"df_{c}" in cust]
    df = pd.DataFrame({c: cust[f"df_{c}"].to_numpy() for c in loan_cols}, index=cust.index)
    for c in MOCK_FEATURE_NAMES:
        df[c] = cust[c]
    df[FILE_COL] = cust["shard"].to_numpy()  # the shard (250 customers): the natural group for leave-shard-out checks
    df[ROW_COL] = np.arange(len(df))
    mctx = Ctx(
        ctx.args, ctx.rep, df, ctx.target, flag.astype(int).to_numpy(), loan_cols, ctx.defs_ka, ctx.file_info,
        ctx.target_is_default, dict(ctx.kinds), dict(ctx.plot_as), dict(ctx.roles), {},
    )  # fmt: skip
    for c in MOCK_FEATURE_NAMES:
        mctx.kinds[c], mctx.plot_as[c], mctx.roles[c] = "amount", "numeric", "open_banking"
    dq = [c for c in mctx.cols(role="tier0_target_defining") if mctx.kinds[c] in ("days", "count")]
    mctx.store["quiet"] = (df[dq] == 0).all(axis=1).to_numpy() if dq else None
    st["mock_ctx"] = mctx
    return mctx


MOCK_TABLE_NOTE = {
    ("api", "accounts"): "accounts, cards, loans and savings resources a TPP can list",
    ("api", "balances"): "balances of those resources (Berlin Group types)",
    ("api", "transactions"): "booked and pending entries, flattened from the API responses",
    ("truth", "customers"): "the simulated people: the loan columns of Data/ (df_*), income, hidden drivers",
    ("truth", "accounts"): "every simulated account, including those no API shows",
    ("truth", "ledger"): "the full internal ledger the API views are cut from",
    ("truth", "loans"): "loan terms and arrears as simulated",
    ("truth", "loan_schedule"): "instalment schedule with payment dates and penalties",
    ("truth", "deposits"): "term deposits",
    ("truth", "fx_rates"): "daily NBG exchange rates",
}
FILL_LABEL = {
    "remittance_information": "remittance text", "bank_transaction_code": "ISO 20022 code",
    "merchant_category_code": "merchant category", "balance_after_amount": "running balance",
    "creditor_name": "creditor name", "debtor_name": "debtor name", "end_to_end_id": "end-to-end id",
    "purpose_code": "purpose code", "masked_pan": "masked card no.", "fx_rate": "FX rate", "mandate_id": "mandate id",
}  # fmt: skip


def link_keys(df: pd.DataFrame) -> np.ndarray:
    """One 64-bit hash per row of the (rounded) columns: equal loan rows get equal keys."""
    return pd.util.hash_pandas_object(df.astype(float).round(4), index=False).to_numpy()


def banks_of(cust: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(primary bank per customer, customers per bank, banks per customer) from truth.customers.banks ('tbc|bog|credo')."""
    parts = cust["banks"].fillna("").str.split("|")
    return parts.str[0], parts.explode().value_counts(), parts.str.len()


def pct_cell(v: float) -> str:
    return "–" if pd.isna(v) else f"{100 * v:.0f}%"


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 11 · Mock & synthetic bank data: what it is
# ══════════════════════════════════════════════════════════════════════════════════
@register(
    11, "Mock & synthetic bank data", "What is the generated bank dataset, and how does it connect to the loan table?"
)
def section_mock_overview(ctx: Ctx) -> None:
    """11a what it is, 11b inventory, 11c link to the supplied loans (same people? representative?), 11d bank footprint."""
    import pyarrow.parquet as pq

    rep, st = ctx.rep, ctx.store
    world: MockWorld = st["mock_world"]
    scan = mock_scan_of(ctx)
    cust, man = scan["cust"], world.manifest
    cfg, win = man.get("config", {}), scan["window"]
    partial = len(world.shards) < world.n_shards
    primary, members, n_banks_c = banks_of(cust)
    n_bank_names = len(man.get("customers_by_primary_bank", {})) or members.size

    # ── 11a. what it is ────────────────────────────────────────────────────────────────────────────────────────────
    built = f", built in {man['seconds'] / 60:.0f} min" if man.get("seconds") else ""
    rep.callout(
        "info",
        f"**What this is.** `{world.name}/` is a *generated* banking world: the sibling `obmock` tool took the loans in `Data/` as its "
        f"customers and simulated what {n_bank_names} Georgian banks' open-banking APIs would return for them "
        f"({'`synthetic: true` in its manifest' if man.get('synthetic') else 'no `synthetic` flag in its manifest'}). "
        f"{int(cfg.get('customers', len(cust))):,} customers, {win['months']} months of history up to {win['as_of']:%d %b %Y}, "
        f"seed {cfg.get('seed', '?')}, profile `{man.get('profile', '?')}`{built}. Two layers: "
        f"**api/** = {MOCK_LAYER_NOTE['api']}; **truth/** = {MOCK_LAYER_NOTE['truth']}. "
        "This report reads the folder read-only, builds features from `api/` only, uses `truth/` for the label and as an oracle for "
        "checks, and shows aggregates only (the tables hold names, ids and IBANs: customer identifiers are never read, and the counterparty fields of transactions are only counted).",
    )
    if partial:
        rep.callout(
            "info",
            f"`--fast` reads the first {len(world.shards)} of {world.n_shards} shards = {len(cust):,} of the "
            f"{int(cfg.get('customers', len(cust))):,} customers. Run without `--fast` for the full population.",
        )

    # ── 11b. inventory ─────────────────────────────────────────────────────────────────────────────────────────────
    rows = []
    for layer in ("api", "truth"):
        for tdir in sorted(p for p in (world.root / layer).iterdir() if p.is_dir()):
            files = sorted(tdir.glob("*.parquet"))
            if not files:
                continue
            rows.append(
                {
                    "layer": layer,
                    "table": tdir.name,
                    "rows": sum(pq.ParquetFile(f).metadata.num_rows for f in files),
                    "columns": len(pq.ParquetFile(files[0]).schema_arrow),
                    "files": len(files),
                    "size (MB)": sum(f.stat().st_size for f in files) / 1e6,
                    "what it is": MOCK_TABLE_NOTE.get((layer, tdir.name), ""),
                }
            )
    inv = pd.DataFrame(rows)
    rep.table(
        inv,
        "mock_inventory",
        "What is in the folder",
        "Row counts come from the Parquet footers (the whole folder, also with `--fast`). `api/` is what a lender would get; `truth/` is the simulator's own bookkeeping.",
        fmt={"size (MB)": "{:,.0f}", "rows": "{:,.0f}"},
        index=False,
    )

    extras = [
        label
        for label, path in (
            ("`output/dataset_v1_legacy` (an earlier, smaller generation)", world.root.parent / "dataset_v1_legacy"),
            ("`captures/` (raw API conversations for a sample of customers)", world.root / "captures"),
            (
                "`fixtures/` (example responses copied from the banks' documentation)",
                world.root.parent.parent / "fixtures",
            ),
        )
        if path.is_dir()
    ]
    if extras:
        rep.callout(
            "info",
            f"**Also next to this dataset, not analysed here:** {'; '.join(extras)}.",
        )

    # ── 11c. does it connect to the supplied loans, and is it a fair sample of them? ────────────────────────────────
    link = None
    with rep.attempt("Linking the mock customers to the supplied loans"):
        cols = [c for c in LINK_COLS if c in ctx.df and f"df_{c}" in cust]
        mctx = mock_ctx(ctx)
        if len(cols) < 6 or mctx is None:
            rep.callout(
                "info",
                "The mock customers cannot be matched to loan rows here: the key columns or the target are missing from this dataset.",
            )
        else:
            mock_keys = link_keys(pd.DataFrame({c: cust[f"df_{c}"].to_numpy() for c in cols}))
            data_keys = link_keys(ctx.df[cols])
            found, is_mock = np.isin(mock_keys, data_keys), np.isin(data_keys, mock_keys)
            n_dup = int(pd.Series(data_keys).duplicated().sum())
            y_m, y_rest = mctx.y, ctx.y[~is_mock]
            lo, hi = wilson(y_m.sum(), len(y_m))
            p_rate = float(
                stats.chi2_contingency(
                    np.array([[y_m.sum(), len(y_m) - y_m.sum()], [y_rest.sum(), len(y_rest) - y_rest.sum()]])
                )[1]
            )
            # is the mock sample a random half?  PSI of every loan column against the loans that are NOT mock customers,
            # next to the largest PSI under shuffled labels (pure sampling noise); a model that tries to tell them apart
            psi_cols = [c for c in ctx.features if f"df_{c}" in cust]
            comb = pd.concat(
                [
                    ctx.df.loc[~is_mock, psi_cols].reset_index(drop=True),
                    pd.DataFrame({c: cust[f"df_{c}"].to_numpy() for c in psi_cols}),
                ],
                ignore_index=True,
            )
            grp = np.r_[np.zeros(int((~is_mock).sum()), int), np.ones(len(cust), int)]
            as_cat = {c: ctx.plot_as[c] == "categorical" for c in psi_cols}
            psi = drift_matrix(comb, psi_cols, as_cat, grp, 2)[0]
            rng = np.random.default_rng(SEED)
            ceiling = max(
                float(drift_matrix(comb, psi_cols, as_cat, rng.permutation(grp), 2)[0].max()) for _ in range(3)
            )
            adv = None
            if not ctx.args.no_models and min(grp.sum(), (1 - grp).sum()) >= 200:
                idx = np.sort(rng.choice(len(grp), size=min(len(grp), 30_000), replace=False))
                adv = float(
                    np.mean(cv_predict(to_numeric_frame(comb, psi_cols).iloc[idx], grp[idx], folds=3, max_iter=60)[1])
                )
            link = {
                "n_mock": len(cust), "found": float(found.mean()), "is_mock_share": float(is_mock.mean()), "n_dup_keys": n_dup,
                "rate_mock": float(y_m.mean()), "rate_lo": float(lo), "rate_hi": float(hi), "rate_rest": float(y_rest.mean()),
                "p_rate": p_rate, "psi_max": float(psi.max()), "psi_ceiling": ceiling, "psi_n_over": int((psi > max(ceiling, 0.01)).sum()),
                "n_cols": len(psi_cols), "adv_auc": adv, "fast": ctx.args.fast,
            }  # fmt: skip
            st["mock_link"] = link
            tab = pd.DataFrame(
                [
                    ("mock customers", f"{link['n_mock']:,}", "rows of truth.customers read"),
                    ("... found among the loaded loans (exact match on 8 columns)", pct(link["found"], 1), "100% means every mock customer IS a supplied loan" + (" (--fast loads only a sample of the loans, so this is lower)" if ctx.args.fast else "")),
                    ("loaded loans that are mock customers", pct(link["is_mock_share"], 1), f"a random subset of {len(cust):,} out of {int(ctx.file_info['rows'].sum()):,} loans would give {pct(len(cust) / ctx.file_info['rows'].sum(), 1)}"),
                    ("duplicate keys among the loaded loans", f"{n_dup:,}", "0 = the key identifies a loan row uniquely"),
                    ("target rate: mock customers", f"{pct(link['rate_mock'], 2)}  (95% CI {pct(lo, 2)} - {pct(hi, 2)})", "label taken from the loan row"),
                    ("target rate: loans that are not mock customers", pct(link["rate_rest"], 2), f"difference test p = {p_rate:.2f}"),
                    (f"largest PSI of the {len(psi_cols)} loan columns, mock vs the rest", f"{link['psi_max']:.4f}", f"pure-noise ceiling {ceiling:.4f}"),
                ]
                + ([("a model's AUC for telling mock customers from the rest", f"{adv:.3f}", "0.5 = impossible, i.e. a random subset")] if adv is not None else []),
                columns=["check", "result", "reading"],
            )  # fmt: skip
            rep.table(tab, "mock_link", "How the mock customers relate to the supplied loans", "", index=False)
            if link["found"] >= 0.999 and not ctx.args.fast:
                rep.finding(
                    "ok",
                    f"All {link['n_mock']:,} mock customers are exact rows of the supplied loan table",
                    f"They are a {pct(link['is_mock_share'], 0)} subset of the loans and the label is the supplied label ({pct(link['rate_mock'], 2)} positive against {pct(link['rate_rest'], 2)} in the other loans, p = {p_rate:.2f}). "
                    + (
                        f"A model cannot tell them from the other loans (AUC {adv:.2f}), so the subset is representative."
                        if adv is not None and adv < 0.55
                        else ""
                    ),
                    priority=4,
                )
            elif link["found"] < 0.5 and not ctx.args.fast:
                rep.finding(
                    "warning",
                    "Most mock customers are not rows of the supplied loan table",
                    f"Only {pct(link['found'], 0)} were found by an exact match: this mock dataset was probably generated from a different loan file.",
                    "regenerate the mock from this Data/, or treat its labels as unrelated to the supplied ones",
                    priority=2,
                )

    # ── 11d. who is in it: banks per customer ──────────────────────────────────────────────────────────────────────
    with rep.attempt("The bank footprint of the mock customers"):
        order = members.index.tolist()
        prim = primary.value_counts().reindex(order).fillna(0)
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.4, 3.9), gridspec_kw={"width_ratios": [1.5, 1]})
        yy = np.arange(len(order))
        a1.barh(yy, members.reindex(order), color=NEUTRAL_LIGHT, height=0.7, label="customers who use the bank")
        a1.barh(yy, prim, color=NEUTRAL, height=0.45, label="customers whose main bank it is")
        a1.set_yticks(yy, order, fontsize=8.5)
        a1.invert_yaxis()
        a1.grid(axis="y", visible=False)
        a1.set_xlabel("customers")
        legend_top(a1, ncol=1)
        nb = n_banks_c.value_counts().sort_index()
        a2.bar(nb.index.astype(str), nb / nb.sum() * 100, color=NEUTRAL, width=0.6)
        for x_, v_ in zip(nb.index.astype(str), nb / nb.sum() * 100, strict=True):
            a2.text(x_, v_ + 0.8, f"{v_:.0f}%", ha="center", fontsize=8.5, color=INK2)
        a2.set_xlabel("banks per customer")
        a2.set_ylabel("share of customers (%)")
        top2 = primary.value_counts(normalize=True).head(2)
        multi = float((n_banks_c >= 2).mean())
        st["mock_footprint"] = {"top2": top2.to_dict(), "multi": multi}
        rep.figure(
            fig,
            "mock_footprint",
            "Which banks do the mock customers use?",
            "Left: customers per bank (light = uses the bank at all, dark = it is the customer's main bank). Right: how many banks a customer uses.",
            f"{' and '.join(top2.index)} are the main bank of {pct(float(top2.sum()), 0)} of the customers (the generator allocates main banks by sourced market shares); "
            f"{pct(multi, 0)} use two or more banks, so a lender needs consent at several banks to see a customer whole.",
        )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 12 · Mock bank APIs: what a lender would actually see
# ══════════════════════════════════════════════════════════════════════════════════
@register(
    12,
    "Mock bank APIs: what a lender sees",
    "What would a lender actually receive through the bank APIs - and what is missing by design?",
)
def section_mock_api(ctx: Ctx) -> None:
    """12a field coverage per bank, 12b the loan itself: visible or not, 12c volumes and calendar, 12d hygiene and the generator's own checks."""
    rep, st = ctx.rep, ctx.store
    world: MockWorld = st["mock_world"]
    scan = mock_scan_of(ctx)
    cust, counts, win = scan["cust"], scan["counts"], scan["window"]
    months = win["months"]
    fill, rows_bank = counts["fill"], counts["rows_bank"]
    primary, members, n_banks_c = banks_of(cust)
    banks = rows_bank.sort_values(ascending=False).index.tolist()
    rep.callout(
        "info",
        "**Each bank's API exposes a different subset.** A missing value in `api/` usually means *this bank's API has no such field*, not "
        "*unknown*: it is structural, so a model should treat it as a property of the bank rather than impute it. Rows marked as card "
        "mirrors (the same card entry listed under `accounts` and under `card-accounts`) are dropped before anything is summed.",
    )
    if scan["failed"]:
        rep.finding(
            "warning",
            f"{len(scan['failed'])} of {len(world.shards)} shards could not be read and are missing from every number below",
            f"First error: {scan['failed'][0][1][:160]}. The mock folder may have been regenerating while this ran.",
            "run again once the mock generation has finished",
            priority=2,
        )

    # ── 12a. which fields does each bank fill? ─────────────────────────────────────────────────────────────────────
    with rep.attempt("Field coverage per bank"):
        frac = fill.div(rows_bank, axis=0).reindex(banks)[list(MOCK_FILL_FIELDS)]
        frac.columns = [FILL_LABEL[c] for c in frac.columns]
        fig, ax = plt.subplots(figsize=(9.2, 0.36 * len(banks) + 1.9))
        im = ax.imshow(frac.to_numpy(), aspect="auto", cmap=CMAP_SEQ, vmin=0, vmax=1)
        ax.set_xticks(range(frac.shape[1]), frac.columns, rotation=35, ha="right", fontsize=8.5)
        ax.set_yticks(range(len(banks)), banks, fontsize=8.5)
        ax.grid(False)
        for (i, j), v in np.ndenumerate(frac.to_numpy()):
            ax.text(j, i, f"{100 * v:.0f}", ha="center", va="center", fontsize=7.5, color="white" if v > 0.55 else INK2)
        cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
        cb.set_label("share of the bank's entries that have the field", fontsize=8.5)
        absent = {c: [b for b in banks if frac.loc[b, c] < 0.01] for c in frac.columns}
        always = [c for c in frac.columns if frac[c].min() >= 0.95]
        worst = max(absent, key=lambda c: len(absent[c]))
        st["mock_fill"] = {"absent": absent, "always": always}
        rep.figure(
            fig,
            "mock_field_coverage",
            "Which fields does each bank's API fill?",
            "Rows = banks (most entries first), columns = transaction fields. The number is the share of that bank's entries where the field is present (0 = the API never returns it).",
            (
                f"Only {len(always)} of {frac.shape[1]} fields are present in nearly every entry of every bank ({', '.join(always)})"
                if always
                else f"No field of the {frac.shape[1]} is present in nearly every entry of every bank"
            )
            + f"; **{worst}** is absent at {len(absent[worst])} banks ({', '.join(absent[worst][:5])}{' ...' if len(absent[worst]) > 5 else ''}). Features built on a field only exist for the banks that publish it.",
        )

    # ── 12b. per bank, and the loan itself ─────────────────────────────────────────────────────────────────────────
    with rep.attempt("What each bank shows, and whether the loan itself is visible"):
        loans, pairs = scan["loans"], counts["loan_pairs"]
        seen = loans.merge(pairs, left_on=["cid", "bank"], right_on=["psu_id", "bank"], how="left")
        seen["visible"] = seen["n_loan_resources"].notna()
        by_bank = seen.groupby("bank")["visible"].agg(["size", "sum"]).reindex(banks).fillna(0)
        tab = pd.DataFrame(index=banks)
        tab.index.name = "bank"
        tab["customers using it"] = members.reindex(banks)
        tab["API entries"] = rows_bank.reindex(banks)
        tab["entries / customer / month"] = tab["API entries"] / tab["customers using it"] / months
        tab["ISO 20022 code"] = (fill["bank_transaction_code"] / rows_bank).reindex(banks)
        tab["merchant category"] = (fill["merchant_category_code"] / rows_bank).reindex(banks)
        tab["running balance"] = (fill["balance_after_amount"] / rows_bank).reindex(banks)
        tab["simulated loans"] = by_bank["size"]
        tab["... visible via the API"] = by_bank["sum"]
        tab["share visible"] = by_bank["sum"] / by_bank["size"].where(by_bank["size"] > 0)
        rep.table(
            tab,
            "mock_banks",
            "What each bank's API gives a lender",
            "Entries are API transaction rows (including card mirrors). 'Simulated loans' are the loans the generator placed at the bank (truth); 'visible' = a loan resource of that customer is listed by that bank's API.",
            fmt={c: pct_cell for c in ("ISO 20022 code", "merchant category", "running balance", "share visible")}
            | {"entries / customer / month": "{:.1f}"},
            bars={"share visible": (0, 1)},
        )
        n_ext = int((cust["sim_loan_bank"] == "EXT").sum())
        n_loans_all = int(len(seen)) + n_ext
        n_vis = int(seen["visible"].sum())
        no_api = [b for b in banks if by_bank.loc[b, "size"] >= 20 and by_bank.loc[b, "sum"] == 0]
        hidden_conn = int(len(seen) - n_vis - sum(by_bank.loc[b, "size"] for b in no_api))
        st["mock_visibility"] = {
            "share_visible": n_vis / max(n_loans_all, 1),
            "n_loans": n_loans_all,
            "n_ext": n_ext,
            "no_api": no_api,
        }
        order = sorted(banks, key=lambda b: -by_bank.loc[b, "size"])
        labels = [*order, "outside the 12 banks"]
        vis = [by_bank.loc[b, "sum"] for b in order] + [0]
        hid = [by_bank.loc[b, "size"] - by_bank.loc[b, "sum"] for b in order] + [n_ext]
        fig, ax = plt.subplots(figsize=(8.6, 0.33 * len(labels) + 1.5))
        ax.barh(range(len(labels)), vis, color=NEUTRAL, height=0.65, label="loan is listed by the bank's API")
        ax.barh(
            range(len(labels)),
            hid,
            left=vis,
            color=NEUTRAL_LIGHT,
            height=0.65,
            label="loan exists but the API does not show it",
        )
        ax.set_yticks(range(len(labels)), labels, fontsize=8.5)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("customers' loans")
        legend_top(ax, ncol=1)
        rep.figure(
            fig,
            "mock_loan_visibility",
            "Can a lender see the loan itself through the APIs?",
            "One bar per bank that holds simulated loans: dark = the bank's API lists the loan, light = the loan exists but is not exposed. The last bar is loans held at banks outside the twelve (never visible).",
            f"Only **{pct(n_vis / max(n_loans_all, 1), 0)}** of the {n_loans_all:,} loans are visible. {pct(n_ext / max(n_loans_all, 1), 0)} sit at a bank outside the twelve"
            + (f"; banks whose API lists no loans at all: {', '.join(no_api)}" if no_api else "")
            + (f"; {hidden_conn:,} more are held at a connected bank but not listed." if hidden_conn > 0 else "."),
        )
        share_vis = n_vis / max(n_loans_all, 1)
        minority = share_vis < 0.5
        rep.finding(
            "info",
            f"The loan itself is visible through the APIs for {'only ' if minority else ''}{pct(share_vis, 0)} of the loans",
            f"Every customer is a borrower in the supplied data, but a lender looking at their bank accounts would see the loan (balance, rate, term) for {'a minority' if minority else 'part'} of them. "
            + (
                "Loan-based API features are therefore missing for most customers; "
                if minority
                else "Loan-based API features are missing for the rest; "
            )
            + "the transaction history (instalment debits, penalties) is the other way to see it.",
            priority=3,
        )

    # ── 12c. volumes and the calendar ──────────────────────────────────────────────────────────────────────────────
    with rep.attempt("Volumes and calendar patterns"):
        fig, ax = plt.subplots(2, 2, figsize=(10.4, 6.2))
        tpm = cust["tx_per_month"].dropna()
        ax[0, 0].hist(tpm, bins=40, color=NEUTRAL, edgecolor=SURFACE)
        ax[0, 0].axvline(tpm.median(), color=INK2, ls="--", lw=1)
        ax[0, 0].set_xlabel("booked entries per customer per month")
        ax[0, 0].set_ylabel("customers")
        ax[0, 0].set_title(f"median {tpm.median():.0f}", fontsize=9.5)
        mr = counts["month_rows"]
        start = win["start"]
        labels_m = [(start + pd.DateOffset(months=k)).strftime("%b") for k in range(months)]
        ax[0, 1].bar(range(months), mr / 1e6, color=NEUTRAL, width=0.7)
        ax[0, 1].set_xticks(range(months), labels_m, rotation=45, ha="right", fontsize=8)
        ax[0, 1].set_ylabel("entries (millions)")
        ax[0, 1].set_title(f"entries per calendar month ({labels_m[0]}: first and last part pooled)", fontsize=9.5)
        sd = counts["salary_dom"][1:32]
        ax[1, 0].bar(range(1, 32), sd / max(sd.sum(), 1) * 100, color=NEUTRAL, width=0.8)
        ax[1, 0].set_xlabel("day of the month a salary is booked")
        ax[1, 0].set_ylabel("share of salary credits (%)")
        wd = counts["weekday_card"]
        ax[1, 1].bar(
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], wd / max(wd.sum(), 1) * 100, color=NEUTRAL, width=0.7
        )
        ax[1, 1].set_ylabel("share of card entries (%)")
        ax[1, 1].set_xlabel("weekday of the BOOKING date")
        fig.tight_layout()
        top3 = np.sort(sd)[::-1][:3].sum() / max(sd.sum(), 1)
        weekend = float(wd[5:].sum() / max(wd.sum(), 1))
        peak_m = int(np.argmax(mr))
        st["mock_calendar"] = {"weekend": weekend, "payday_top3": float(top3)}
        rep.figure(
            fig,
            "mock_volumes_calendar",
            "How much data is there, and when does it happen?",
            "Top left: entries per customer-month. Top right: all entries per calendar month of the window (the window starts and ends mid-month, so those two partial months are pooled). Bottom left: day of the month on which salaries are booked. Bottom right: weekday on which card entries are booked.",
            f"A typical customer has {tpm.median():.0f} entries a month. Three calendar days carry {pct(float(top3), 0)} of all salary credits (paydays are concentrated), "
            f"and **{pct(weekend, 1)}** of card entries are booked on a weekend: card bookings follow the business-day rule, so booking weekday says nothing about when the purchase happened. Busiest month: {labels_m[peak_m]}.",
        )

    # ── 12d. hygiene, and the generator's own checks ───────────────────────────────────────────────────────────────
    with rep.attempt("Data hygiene of the API tables"):
        h = counts["hygiene"]
        n = max(h["rows"], 1)
        rows = [
            ("API transaction rows scanned", f"{h['rows']:,}", "", ""),
            ("card-mirror rows (copies of a card entry)", f"{h['mirrors']:,} ({pct(h['mirrors'] / n, 1)})", "drop before summing", "info"),
            ("pending entries", f"{h['pending']:,} ({pct(h['pending'] / n, 2)})", "not yet booked: excluded from flows", "info"),
            ("rows with an ISO 20022 code", f"{h['coded_rows']:,} ({pct(h['coded_rows'] / n, 0)})", "banks without codes need the remittance text", "info"),
            ("booked entries in a currency without an NBG rate", f"{h['no_fx_rate']:,}", "counted 1:1 in GEL; expected 0", "ok" if h["no_fx_rate"] == 0 else "check"),
            ("duplicate entries within a (bank, endpoint)", f"{h['duplicate_entries']:,}", "expected 0", "ok" if h["duplicate_entries"] == 0 else "check"),
            ("booked entries without a booking date", f"{h['booked_without_date']:,}", "expected 0", "ok" if h["booked_without_date"] == 0 else "check"),
            ("pending entries with a booking date", f"{h['pending_with_date']:,}", "expected 0", "ok" if h["pending_with_date"] == 0 else "check"),
            ("booked entries outside the 12-month window", f"{h['outside_window']:,}", "expected 0", "ok" if h["outside_window"] == 0 else "check"),
            ("entries with amount 0", f"{h['zero_amount']:,}", "expected 0; a handful is harmless", "ok" if h["zero_amount"] / n < 1e-4 else "check"),
            ("debit-type code on a credit", f"{h['debit_code_credited']:,}", "expected 0", "ok" if h["debit_code_credited"] == 0 else "check"),
            ("credit-type code on a debit", f"{h['credit_code_debited']:,}", "expected 0", "ok" if h["credit_code_debited"] == 0 else "check"),
        ]  # fmt: skip
        checks = scan["quality"].get("checks", {})
        if checks:
            full = sum(1 for p_, t_ in checks.values() if p_ == t_)
            rows.append(
                (
                    "the generator's own quality checks (quality.json)",
                    f"{full} of {len(checks)} pass on every row",
                    "ISO 13616 / 20022 / 9362 / 4217, balances, booking rules",
                    "ok" if full == len(checks) else "check",
                )
            )
        rep.table(
            pd.DataFrame(rows, columns=["check", "result", "expected / meaning", "status"]),
            "mock_hygiene",
            "Hygiene of the API tables",
            "Computed over every scanned row (counters are added up shard by shard).",
            index=False,
        )
        bad = [r[0] for r in rows if r[3] == "check"]
        if bad:
            rep.finding(
                "warning",
                "The API tables break some of their own rules",
                "; ".join(bad),
                "do not trust the affected rows",
                priority=2,
            )
        else:
            rep.finding(
                "ok",
                "The API tables are internally consistent",
                f"No duplicates, no booked entry without a date, no entry outside the window, no sign/code contradiction in {h['rows']:,} rows; the mock's own {len(checks) or 'quality'} checks agree. Clean here does not mean realistic (section 13).",
                priority=4,
            )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 13 · Is the synthetic world realistic - or planted?
# ══════════════════════════════════════════════════════════════════════════════════
def stress_fit(cust: pd.DataFrame) -> dict | None:
    """Least-squares fit of the generator's latent stress on (rank of the PD, delinquency days / 90 capped at 1, the label
    addNegStatus).  None if the mock does not carry the stress or one of its ingredients."""
    need = ("stress", "df_probabilityOfDefault", "df_MaximumNumberDelinquencyDays", "df_addNegStatus")
    if not all(c in cust for c in need) or cust["stress"].isna().any():
        return None
    rank_pd = cust["df_probabilityOfDefault"].rank(pct=True).to_numpy()
    days = np.minimum(1.0, cust["df_MaximumNumberDelinquencyDays"].to_numpy(float) / 90)
    lab = cust["df_addNegStatus"].to_numpy(float)
    s = cust["stress"].to_numpy(float)
    X = np.column_stack([rank_pd, days, lab, np.ones(len(cust))])
    beta, *_ = np.linalg.lstsq(X, s, rcond=None)
    r2 = 1 - float(((s - X @ beta) ** 2).sum() / max(float(((s - s.mean()) ** 2).sum()), 1e-12))
    return {"beta": beta, "r2": r2, "s": s, "lab": lab, "rank_pd": rank_pd}


def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman correlation on the rows where both exist (NaN if fewer than 30 rows or no spread)."""
    ok = a.notna() & b.notna()
    if ok.sum() < 30 or a[ok].nunique() < 2 or b[ok].nunique() < 2:
        return float("nan")
    return float(stats.spearmanr(a[ok], b[ok])[0])


@register(
    13,
    "Mock data: realistic or planted?",
    "Where does the synthetic bank data give itself away - and what did its generator build into it?",
)
def section_mock_realism(ctx: Ctx) -> None:
    """13a the latent-stress formula (is the behaviour generated from the label?), 13b what is wired to it, 13c realism fingerprints,
    13d what synthetic data with answers is good for: recovering the truth."""
    rep, st = ctx.rep, ctx.store
    scan = mock_scan_of(ctx)
    cust, counts = scan["cust"], scan["counts"]
    summary = st.setdefault("mock_summary", {})

    # ── 13a. the generator's latent 'stress': a formula that contains the label ───────────────────────────────────
    with rep.attempt("The latent stress formula"):
        fit = stress_fit(cust)
        if fit is None:
            rep.callout(
                "info",
                "The latent stress score or the columns it is built from are not in this mock dataset, so the formula check is skipped.",
            )
        else:
            beta, r2, s, lab, rank_pd = fit["beta"], fit["r2"], fit["s"], fit["lab"], fit["rank_pd"]
            y_lab = lab.astype(int)
            auc_stress = safe_auc(y_lab, s)
            auc_nolab = safe_auc(y_lab, s - beta[2] * lab)
            auc_pd = safe_auc(y_lab, rank_pd)
            terms = [
                "rank of the supplied PD",
                "delinquency days / 90 (capped at 1)",
                "the label `addNegStatus` itself",
            ]
            tab = pd.DataFrame({"term in the stress formula": terms, "fitted weight": beta[:3]})
            tab["rounds to"] = [
                f"{round(b_ * 20) / 20:.2f}" if abs(b_ - round(b_ * 20) / 20) <= MOCK_WEIGHT_TOL else "-"
                for b_ in beta[:3]
            ]
            rep.table(
                tab,
                "mock_stress_formula",
                "The hidden driver of the simulated behaviour",
                f"Least-squares fit of `truth.customers.stress` on the three columns (intercept {beta[3]:+.3f}). R² = {r2:.5f}; the generator's own notes (docs/SIMULATION_MODELS.md) describe the same recipe.",
                fmt={"fitted weight": "{:.3f}"},
                index=False,
            )
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.4, 3.5), gridspec_kw={"width_ratios": [1.5, 1]})
            bins = np.linspace(0, 1, 41)
            a1.hist(s[y_lab == 0], bins=bins, color=C0, alpha=0.85, density=True, label="target 0")
            a1.hist(s[y_lab == 1], bins=bins, color=C1, alpha=0.85, density=True, label="target 1")
            a1.set_xlabel("latent stress (0 = calm, 1 = distressed)")
            a1.set_ylabel("density")
            legend_top(a1, ncol=2)
            a2.barh(range(3), beta[:3], color=NEUTRAL, height=0.6)
            a2.set_yticks(range(3), ["PD rank", "delinquency days", "label itself"], fontsize=8.5)
            a2.invert_yaxis()
            a2.grid(axis="y", visible=False)
            for i_, b_ in enumerate(beta[:3]):
                a2.text(b_ + 0.01, i_, f"{b_:.2f}", va="center", fontsize=8.5, color=INK2)
            a2.set_xlabel(f"weight in the stress formula (R² = {r2:.4f})")
            fig.tight_layout()
            planted = r2 >= STRESS_PLANTED_R2 and beta[2] > 0.05
            summary.update(
                stress_r2=r2,
                stress_w=[float(b_) for b_ in beta[:3]],
                auc_stress=auc_stress,
                auc_nolab=auc_nolab,
                auc_pd=auc_pd,
                planted=planted,
            )
            rep.figure(
                fig,
                "mock_stress",
                "What drives the simulated behaviour?",
                "Left: the distribution of the generator's latent stress for loans with target 0 and 1. Right: the weight of each ingredient in a least-squares fit of that stress.",
                f"Stress is **{'exactly' if r2 > 0.999 else 'closely'} a formula** of three loan columns (R² {r2:.4f}): {beta[0]:.2f} x PD rank + {beta[1]:.2f} x delinquency days + **{beta[2]:.2f} x the label**. "
                f"As a score it separates the classes with AUC {auc_stress:.3f}; without the label term {auc_nolab:.3f} (PD rank alone {auc_pd:.3f}).",
            )
            if planted:
                rep.finding(
                    "critical",
                    "The simulated bank behaviour is generated from a score that contains the label",
                    f"Spending, balances, lateness, micro-loans and gambling in the mock are driven by a latent stress that is a formula of the supplied PD, the delinquency days and `addNegStatus` itself (R² {r2:.4f}). "
                    "Any model that predicts the label from these transactions is partly reading the label back out of a function of it"
                    + (
                        ", and the PD and delinquency columns are exactly the ones section 8 says must not be inputs. "
                        if (t0 := st.get("tier0")) and t0["median_auc"] >= TIER0_CONFIRMED_AUC
                        else ". "
                    )
                    + "The AUCs in section 14 therefore measure how much planted signal survives the bank views, never whether open-banking data predicts default.",
                    "use the mock to build and test pipelines and feature code; do not report its AUC as evidence about real data",
                    priority=1,
                )
            else:
                rep.finding(
                    "info",
                    "The simulated behaviour is not a simple formula of the label columns",
                    f"The generator's latent stress is explained by the PD rank, the delinquency days and the label with R² {r2:.3f} (label weight {beta[2]:.2f}), "
                    "which is below the level of a formula; the mock's own notes may describe a different recipe. Check how its behaviour is generated before reading its AUCs.",
                    priority=3,
                )

    # ── 13b. what the generator wired to stress ────────────────────────────────────────────────────────────────────
    with rep.attempt("Which behaviours are wired to stress"):
        inc = cust["income_month_minor"] / 100
        lab_s = cust["df_addNegStatus"] if "df_addNegStatus" in cust else pd.Series(np.nan, index=cust.index)
        rows = []
        for f in MOCK_FEATURE_NAMES:
            if cust[f].notna().mean() < 0.2 or cust[f].nunique() < 3:
                continue
            rows.append(
                (
                    f,
                    MOCK_FAMILY[f],
                    cust[f].notna().mean(),
                    spearman(cust[f], cust["stress"]),
                    spearman(cust[f], inc),
                    spearman(cust[f], lab_s),
                )
            )
        sp = pd.DataFrame(
            rows, columns=["feature", "family", "coverage", "rho with stress", "rho with income", "rho with the label"]
        ).set_index("feature")
        sp = sp.reindex(sp["rho with stress"].abs().sort_values(ascending=False).index)
        wired = sp[(sp["rho with stress"].abs() >= MOCK_RHO_NOTABLE) & (sp["rho with income"].abs() < MOCK_RHO_NOTABLE)]
        n_wired = len(wired)  # follow the latent stress closely and do not simply scale with income
        drivers = []
        for key, what in (
            ("sim_late_rate", "chance an instalment is paid late"),
            ("sim_save_rate", "share of income saved"),
            ("sim_spend_scale", "overall spending level"),
        ):
            if key in cust and cust[key].notna().any():
                r = sp.index.to_series().map(lambda f, k=key: spearman(cust[f], cust[k])).abs().dropna()
                if len(r):
                    best = r.idxmax()
                    drivers.append((key.replace("sim_", ""), what, best, spearman(cust[best], cust[key])))
        summary.update(n_wired=n_wired, n_feat=len(sp), wired=list(wired.index[:4]))
        rep.table(
            sp.head(15),
            "mock_wired_to_stress",
            "Behaviours ranked by how closely they follow the latent stress",
            f"Spearman correlations over all scanned customers. Amount-like features follow income because they are measured in money; the ones that follow stress while ignoring income (|ρ| < {MOCK_RHO_NOTABLE:.1f}) are the behaviours the generator tied to it.",
            fmt={"coverage": pct_cell}
            | {c: "{:+.2f}" for c in ("rho with stress", "rho with income", "rho with the label")},
        )
        if drivers:
            rep.table(
                pd.DataFrame(drivers, columns=["hidden driver", "what it is", "best API feature", "Spearman"]),
                "mock_driver_recovery",
                "Can the hidden drivers be read back from the API features?",
                "Each hidden driver of the simulator (`sim_tags` in truth.customers) against the API feature that follows it best.",
                fmt={"Spearman": "{:+.2f}"},
                index=False,
            )
        top = sp.head(12)
        fig, ax = plt.subplots(figsize=(8.6, 0.34 * len(top) + 1.6))
        yy = np.arange(len(top))
        ax.barh(yy, top["rho with stress"], color=NEUTRAL, height=0.6, label="Spearman with the latent stress")
        ax.scatter(
            top["rho with income"],
            yy,
            marker="D",
            s=34,
            facecolors="none",
            edgecolors=INK,
            zorder=3,
            label="Spearman with income",
        )
        ax.axvline(0, color=INK2, lw=0.8)
        ax.set_yticks(yy, top.index, fontsize=8.5)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Spearman correlation")
        legend_top(ax, ncol=2)
        rep.figure(
            fig,
            "mock_wired_to_stress",
            "Which behaviours move with the hidden stress?",
            "Bars: correlation of each API feature with the generator's latent stress. Diamonds: correlation of the same feature with the customer's income.",
            f"{n_wired} of {len(sp)} features follow the stress at |ρ| ≥ {MOCK_RHO_NOTABLE:.1f} without following income"
            + (f" ({', '.join(f'`{f}`' for f in wired.index[:4])})" if n_wired else "")
            + f"; the strongest overall are {', '.join(f'`{f}`' for f in sp.index[:3])}.",
        )

    # ── 13c. realism fingerprints ──────────────────────────────────────────────────────────────────────────────────
    with rep.attempt("Realism fingerprints"):
        rows, merch = [], counts["merchants"]
        n_purchases = sum(merch.values())
        top10 = sum(v for _, v in merch.most_common(10)) / max(n_purchases, 1)
        rows.append(
            (
                "distinct merchants named in card purchases",
                f"{len(merch):,}; the top 10 take {pct(top10, 0)} of purchases",
                "a national card portfolio shows thousands of merchants with a long tail",
                "consistent with a generated catalogue" if len(merch) < 500 else "plausible",
            )
        )
        cl = counts["cents_last"]
        p_uniform = float(stats.chisquare(cl[1:])[1]) if cl[1:].sum() > 0 else float("nan")
        rows.append(
            (
                "last cent digit of card purchases (1-9)",
                f"equally likely (chi-square p = {p_uniform:.2f}); digit 9 = {pct(cl[9] / max(cl[1:].sum(), 1), 1)} of non-zero digits",
                "retail prices cluster on .90 / .99 / .50, so 9 and 5 stand out",
                "consistent with generated amounts" if p_uniform > 0.01 else "has some structure",
            )
        )
        lin = None
        ok = (cust["card_spend_month"] > 0) & (cust["salary_month"] > 0)
        if ok.sum() > 200:
            lin = stats.linregress(np.log(cust.loc[ok, "salary_month"]), np.log(cust.loc[ok, "card_spend_month"]))
            rows.append(
                (
                    "card spending vs salary (log-log slope)",
                    f"{lin.slope:.2f} ± {1.96 * lin.stderr:.2f} (r = {lin.rvalue:.2f})",
                    "household surveys usually find spending rising less than 1:1 with income (richer households save more)",
                    "consistent with a generator that scales spending with income" if lin.slope > 0.9 else "plausible",
                )
            )
        inc = cust["income_month_minor"] / 100
        okk = cust["salary_month"].notna() & (inc > 0)
        err = ((cust.loc[okk, "salary_month"] - inc[okk]) / inc[okk]).abs()
        if okk.sum() > 200:
            rows.append(
                (
                    "observed salary vs declared income",
                    f"median error {pct(float(err.median()), 1)}; {pct(float((err <= 0.1).mean()), 0)} within ±10%",
                    "declared income at application and later salary credits rarely agree this closely (gross vs net, job changes)",
                    "consistent with salaries drawn from the declared income" if err.median() < 0.02 else "plausible",
                )
            )
        ar = counts["atm_round"]
        rows.append(
            (
                "ATM withdrawals that are multiples of 10 GEL",
                pct(float(ar[0] / max(ar[1], 1)), 0),
                "ATMs dispense fixed notes, so nearly all are round",
                "plausible",
            )
        )
        wd = counts["weekday_card"]
        rows.append(
            (
                "card entries booked on Saturday / Sunday",
                pct(float(wd[5:].sum() / max(wd.sum(), 1)), 1),
                "banks that book on business days only show the same; the purchase date is in the card timestamp",
                "a documented booking rule, not a flaw",
            )
        )
        rep.table(
            pd.DataFrame(
                rows,
                columns=[
                    "check",
                    "observed here",
                    "typical in real data (rule of thumb, not measured here)",
                    "reading",
                ],
            ),
            "mock_fingerprints",
            "Realism fingerprints of the synthetic transactions",
            "None of these proves the data is synthetic (the manifest already says so); they show where it differs from what real card and payroll data usually look like, i.e. what a model trained on it would not have to learn.",
            index=False,
        )
        fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(11.4, 3.5))
        a1.bar(range(10), cl / max(cl.sum(), 1) * 100, color=NEUTRAL, width=0.7)
        a1.axhline(10, color=INK2, ls="--", lw=1)
        a1.set_xticks(range(10))
        a1.set_xlabel("last cent digit of a card purchase")
        a1.set_ylabel("share of purchases (%)")
        a1.set_title("cents of card purchases", fontsize=9)
        if lin is not None:
            sub = cust.loc[ok].sample(min(int(ok.sum()), 8000), random_state=SEED)
            a2.hexbin(
                np.log10(sub["salary_month"]), np.log10(sub["card_spend_month"]), gridsize=35, cmap=CMAP_SEQ, mincnt=1
            )
            xs = np.array([np.log10(sub["salary_month"].min()), np.log10(sub["salary_month"].max())])
            a2.plot(
                xs,
                (lin.intercept + lin.slope * np.log(10) * xs) / np.log(10),
                color=C1,
                lw=2,
                label=f"fitted slope {lin.slope:.2f}",
            )
            a2.plot(
                xs,
                xs + (np.log10(sub["card_spend_month"].median()) - np.log10(sub["salary_month"].median())),
                color=INK2,
                ls="--",
                lw=1,
                label="slope 1 (proportional)",
            )
            a2.set_xlabel("monthly salary, GEL (log10)")
            a2.set_ylabel("monthly card spending, GEL (log10)")
            a2.legend(loc="upper left", fontsize=8)
            a2.set_title(f"spending vs pay (slope {lin.slope:.2f})", fontsize=9)
        freq = np.array(sorted(merch.values(), reverse=True), float)
        a3.loglog(np.arange(1, len(freq) + 1), freq, "o-", color=NEUTRAL, ms=3.5, lw=1)
        a3.set_xlabel("merchant rank")
        a3.set_ylabel("purchases")
        a3.set_title(f"{len(freq)} distinct merchants", fontsize=9)
        fig.tight_layout()
        flagged = sum(r[3].startswith("consistent") for r in rows)
        bits = [  # each clause only when its own check came out that way
            *([f"a small fixed merchant catalogue ({len(merch)} names)"] if len(merch) < 500 else []),
            *(["no price-point clustering in the cents"] if p_uniform > 0.01 else []),
            *([f"spending that scales one-for-one with pay (slope {lin.slope:.2f})"] if lin is not None and lin.slope > 0.9 else []),
            *(["salaries that equal the declared income almost exactly"] if okk.sum() > 200 and err.median() < 0.02 else []),
        ]  # fmt: skip
        summary.update(
            n_merchants=len(merch),
            spend_slope=None if lin is None else float(lin.slope),
            salary_err=float(err.median()) if okk.sum() > 200 else None,
            n_flagged=flagged,
            n_checks=len(rows),
        )
        rep.figure(
            fig,
            "mock_fingerprints",
            "Where the synthetic transactions differ from real ones",
            "Left: last cent digit of card purchases. Middle: monthly card spending against monthly salary (each cell groups customers). Right: how often each merchant appears (log-log).",
            f"{flagged} of {len(rows)} checks read as generated rather than observed"
            + (": " + ", ".join(bits) if bits else "")
            + ".",
        )

    # ── 13d. what synthetic data with answers is good for: recover the truth ───────────────────────────────────────
    with rep.attempt("Recovering the hidden truth from the API features"):
        inc = cust["income_month_minor"] / 100
        rec = []
        okk = cust["salary_month"].notna() & (inc > 0)
        if okk.sum() > 100:
            e = (cust.loc[okk, "salary_month"] - inc[okk]) / inc[okk]
            rec.append(
                (
                    "monthly income",
                    "average salary credit",
                    int(okk.sum()),
                    spearman(cust.loc[okk, "salary_month"], inc[okk]),
                    float(e.abs().median()),
                    float((e.abs() <= 0.1).mean()),
                )
            )
        okf = cust["inflow_month"] > 0
        if okf.sum() > 100:
            e = (cust.loc[okf, "inflow_month"] - inc[okf]) / inc[okf]
            rec.append(
                (
                    "monthly income",
                    "all inflows / 12 (works for sole proprietors too)",
                    int(okf.sum()),
                    spearman(cust.loc[okf, "inflow_month"], inc[okf]),
                    float(e.abs().median()),
                    float((e.abs() <= 0.1).mean()),
                )
            )
        loans = scan["loans"]
        inst = loans.groupby("cid")["instalment_minor"].sum() / 100
        both = pd.DataFrame({"inst": inst, "seen": cust["loan_repay_month"]}).dropna()
        both = both[(both["inst"] > 0) & (both["seen"] > 0)]
        if len(both) > 100:
            e = (both["seen"] - both["inst"]) / both["inst"]
            rec.append(
                (
                    "monthly loan instalment",
                    "instalment debits found by their text",
                    len(both),
                    spearman(both["seen"], both["inst"]),
                    float(e.abs().median()),
                    float((e.abs() <= 0.1).mean()),
                )
            )
        if rec:
            tab = pd.DataFrame(
                rec,
                columns=[
                    "hidden quantity",
                    "recovered from",
                    "customers",
                    "Spearman",
                    "median abs. error",
                    "within ±10%",
                ],
            )
            rep.table(
                tab,
                "mock_recovery",
                "How well do the API features recover what the simulator knows?",
                "Truth = `truth.customers.income_month_minor` and `truth.loans.instalment_minor`; the API-side estimate uses only api/ tables. Only customers for whom the estimate exists are counted.",
                fmt={"Spearman": "{:.3f}", "median abs. error": pct_cell, "within ±10%": pct_cell},
                index=False,
            )
            summary["recovery"] = {
                "income_within10": float(rec[0][5]),
                "income_n": int(rec[0][2]),
                "income_err": float(rec[0][4]),
            }
            rep.callout(
                "good",
                "**This is what the mock is genuinely good for.** Because the answers are known, you can unit-test feature code: does your salary detector find the income, does your instalment detector find the loan? "
                f"Here {pct(float(rec[0][5]), 0)} of salaried customers get an income estimate within ±10% of the truth"
                + (
                    "; declared incomes and later salary credits usually differ more in real data (gross vs net, job changes), so treat this as a test of your code, not of how well real incomes can be inferred."
                    if rec[0][5] >= 0.9
                    else "."
                ),
            )


# ══════════════════════════════════════════════════════════════════════════════════
# SECTION 14 · Features from the bank APIs, and what they can (not) tell us
# ══════════════════════════════════════════════════════════════════════════════════
@register(
    14,
    "Bank-data features & how to use them",
    "What can be built from the bank APIs, what do the features show, and what may - and may not - be concluded from them?",
)
def section_mock_features(ctx: Ctx) -> None:
    """14a the feature dictionary, 14b timing, 14c single-feature signal, 14d do they add anything (models), 14e how to use the mock."""
    rep, st = ctx.rep, ctx.store
    scan = mock_scan_of(ctx)
    cust = scan["cust"]
    mctx = mock_ctx(ctx)
    summary = st.setdefault("mock_summary", {})
    # blue / orange mean "target 0 / target 1" everywhere in this report, so the families use the other hues
    fam_color = {f: [*CAT[2:], NEUTRAL][i % 7] for i, f in enumerate(MOCK_FAMILIES)}

    # ── 14a. what can be built ─────────────────────────────────────────────────────────────────────────────────────
    dic = pd.DataFrame(MOCK_FEATURES, columns=["feature", "family", "definition"]).set_index("feature")
    dic["coverage"] = [float(cust[f].notna().mean()) for f in dic.index]
    dic["median"] = [float(cust[f].median()) for f in dic.index]
    rep.text(
        f"**{len(dic)} features in {len(MOCK_FAMILIES)} families** are built from the `api/` tables only (accounts, balances, transactions), over the {scan['window']['months']} months before {scan['window']['as_of']:%d %b %Y}, "
        "per customer across all the customer's banks, in GEL (foreign currencies at the window's mean NBG rate, the public rate table), with card mirrors, own-account moves and loans paid straight out removed. "
        "'Months' are calendar months; the window starts and ends mid-month, so those two partial months are pooled. 'Coverage' is the share of customers for whom the feature exists "
        "(a missing value means the bank's API does not expose what the feature needs, or the customer has none of it)."
    )
    rep.table(
        dic,
        "mock_feature_dictionary",
        "The API feature dictionary",
        "",
        fmt={"coverage": pct_cell, "median": "{:,.2f}"},
        max_rows=60,
    )
    if mctx is None:
        rep.callout(
            "info",
            "The target is not one of the loan columns copied into the mock, so the target-based parts of this section are skipped.",
        )
        return
    y, df = mctx.y, mctx.df

    # ── 14b. when were these behaviours observed, relative to the loan? ───────────────────────────────────────────
    with rep.attempt("Timing of the transactions relative to the loans"):
        loans = scan["loans"]
        started = pd.to_datetime(loans["start"])
        before = float((started < scan["window"]["start"]).mean())
        summary["pre_window"] = before
        rep.finding(
            "warning",
            f"For {pct(before, 0)} of the loans the whole transaction window comes AFTER the loan started",
            f"Only {pct(1 - before, 0)} of the simulated loans were granted inside the {scan['window']['months']}-month window. For the rest, every bank-data feature describes behaviour while the loan was already running (instalments, arrears, penalties), "
            "so these are monitoring / behavioural-scoring features, comparable to feature set B, and not application-time features. A genuine application scorecard would need the history BEFORE each loan's start date, which this mock does not contain.",
            "never mix them with set A in an application-scorecard framing",
            priority=2,
        )

    # ── 14c. single-feature signal ─────────────────────────────────────────────────────────────────────────────────
    with rep.attempt("Single-feature signal"):
        rows = []
        for f in MOCK_FEATURE_NAMES:
            x = df[f].astype(float)
            if x.notna().sum() < 100 or x.nunique() < 2:
                continue
            _, iv = woe_iv(x, y)
            rows.append((f, MOCK_FAMILY[f], float(x.notna().mean()), oof_signal_auc(x, y), iv))
        sig = pd.DataFrame(rows, columns=["feature", "family", "coverage", "AUC (out-of-fold)", "IV"]).set_index(
            "feature"
        )
        sig = sig.sort_values("AUC (out-of-fold)", ascending=False)
        rep.table(
            sig,
            "mock_feature_signal",
            "How well does each API feature separate the target on its own?",
            "Direction-free, out-of-fold AUC (non-monotone patterns count; missing values form their own level) and information value.",
            fmt={"coverage": pct_cell, "AUC (out-of-fold)": "{:.3f}", "IV": "{:.3f}"},
            bars={"AUC (out-of-fold)": (0.5, 1.0)},
            max_rows=20,
        )
        top = sig.head(15)
        fig, ax = plt.subplots(figsize=(8.6, 0.31 * len(top) + 1.7))
        draw_dots(ax, list(top.index), list(top["AUC (out-of-fold)"]), color=[fam_color[f] for f in top["family"]])
        ax.set_xlim(0.5, min(1.0, float(top["AUC (out-of-fold)"].max()) + 0.07))
        ax.set_xlabel("AUC of the single feature (0.5 = chance)")
        legend_top(ax, ncol=3, handles=[Patch(facecolor=fam_color[f], label=f) for f in dict.fromkeys(top["family"])])
        n_strong = int((sig["AUC (out-of-fold)"] >= 0.80).sum())
        summary.update(top_feature=sig.index[0], top_feature_auc=float(sig.iloc[0, 2]), n_strong=n_strong)
        rep.figure(
            fig,
            "mock_feature_auc",
            "Which API features carry signal on their own?",
            "One dot per feature (top 15), coloured by family; the dashed line is chance.",
            f"{n_strong} of {len(sig)} features reach AUC ≥ 0.80 alone; the best is **{sig.index[0]}** ({sig.iloc[0, 2]:.3f})."
            + (
                f" For orientation, a whole application scorecard usually lands between {REALISTIC_AUC[0]:.2f} and {REALISTIC_AUC[1]:.2f} (the rule of thumb of section 9), so a single feature above that is more likely planted or leaked signal than behaviour."
                if sig.iloc[0, 2] > REALISTIC_AUC[1]
                else ""
            ),
        )

    # ── 14d. do the bank-data features add anything beyond the loan columns? ───────────────────────────────────────
    if ctx.args.no_models:
        rep.callout("info", "Model-based comparison skipped (`--no-models`).")
    else:
        with rep.attempt("Do the bank-data features add anything?"):
            kw, folds = model_settings(ctx)
            fs = feature_sets(mctx)
            beh = [f for f in MOCK_FEATURE_NAMES if MOCK_FAMILY[f] not in MOCK_LOAN_FAMILIES]
            sets = {
                "loan columns known at origination (set A)": fs["A"],
                "bank data: behaviour only": beh,
                "bank data: all features": list(MOCK_FEATURE_NAMES),
                "A + bank behaviour": fs["A"] + beh,
                "A + all bank data": fs["A"] + list(MOCK_FEATURE_NAMES),
            }
            sets = {k: v for k, v in sets.items() if v}
            res = {k: eval_set(mctx, v, folds=folds, **kw) for k, v in sets.items()}
            quiet = mctx.store.get("quiet")
            base = res.get("loan columns known at origination (set A)")
            rows = []
            for k, r in res.items():
                diff = None
                if base is not None and r is not base:
                    d = np.array(r["fold_aucs"]) - np.array(base["fold_aucs"])
                    diff = (float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float("nan"))
                q = pop_auc(r, y, quiet)
                rows.append({"columns": k, "n columns": r["n_features"], "CV AUC": r["auc"], "± fold std": r["std"],
                             "change vs set A": "" if diff is None else f"{diff[0]:+.3f} (± {diff[1]:.3f})",
                             "AUC on loans with no delinquency history": "" if q is None else f"{q['auc']:.3f} ± {q['half']:.2f} ({q['events']} events)"})  # fmt: skip
            tab = pd.DataFrame(rows).set_index("columns")
            fit = stress_fit(cust)  # reference lines: the generator's own stress as a score for THIS target
            ref = (
                (None, None)
                if fit is None
                else (safe_auc(y, fit["s"]), safe_auc(y, fit["s"] - fit["beta"][2] * fit["lab"]))
            )
            rep.table(
                tab,
                "mock_model_comparison",
                "Do the bank-data features add anything to the loan columns?",
                f"Gradient boosting, {folds}-fold stratified CV on the {len(y):,} mock customers ({int(y.sum())} events), same folds for every row. 'Change vs set A' is the mean paired difference over the folds with its standard error.",
                fmt={"CV AUC": "{:.3f}", "± fold std": "{:.3f}"},
            )
            names = list(res)
            vals = [res[k]["auc"] for k in names]
            fig, ax = plt.subplots(figsize=(8.6, 0.42 * len(names) + 1.7))
            draw_dots(ax, names, vals, color=NEUTRAL, xerr=None)
            for t_ in ax.texts:  # a halo keeps the value labels readable where a reference line crosses them
                t_.set_bbox({"facecolor": SURFACE, "edgecolor": "none", "pad": 1.5})
            for k_, (v_, lab_) in enumerate(
                ((ref[1], "stress without its addNegStatus term"), (ref[0], "the latent stress itself"))
            ):
                if v_ is not None:  # two stacked labels, both ending at their line, so they cannot overprint each other
                    ax.axvline(v_, color=INK2, ls=":" if k_ else "--", lw=1.3)
                    ax.annotate(
                        lab_,
                        xy=(v_, 1.0),
                        xycoords=("data", "axes fraction"),
                        xytext=(-3, 3 + 12 * k_),
                        textcoords="offset points",
                        ha="right",
                        va="bottom",
                        fontsize=7.5,
                        color=INK2,
                    )
            low = min([*vals, *(v_ for v_ in ref if v_ is not None)])
            ax.set_xlim(max(0.5, np.floor((low - 0.04) * 20) / 20), 1.0)  # zoom: every AUC here is high
            ax.set_xlabel("cross-validated AUC")
            a_beh, a_all, a_a = (
                res.get(k)
                for k in (
                    "bank data: behaviour only",
                    "bank data: all features",
                    "loan columns known at origination (set A)",
                )
            )
            gain = (
                res["A + all bank data"]["auc"] - base["auc"]
                if base is not None and "A + all bank data" in res
                else None
            )
            summary.update(
                auc_A=None if base is None else base["auc"],
                auc_beh=None if a_beh is None else a_beh["auc"],
                auc_api=None if a_all is None else a_all["auc"],
                auc_a_api=res.get("A + all bank data", {}).get("auc"),
                gain=gain,
                n_events=int(y.sum()),
                n_customers=len(y),
            )
            rep.figure(
                fig,
                "mock_model_comparison",
                "How much do the bank-data features add?",
                "Cross-validated AUC of gradient-boosting models on different column sets (same folds). Dashed and dotted lines: how well the generator's own latent stress ranks this target, with and without the `addNegStatus` term it contains.",
                (
                    f"Bank behaviour alone reaches **{a_beh['auc']:.3f}** and all bank data {a_all['auc']:.3f}; added to the loan columns they move the AUC by {gain:+.3f}. "
                    if a_beh and a_all and gain is not None
                    else ""
                )
                + (
                    "That is the planted signal of section 13 surviving the bank views - it is not evidence that open-banking data predicts default in real data."
                    if summary.get("planted") and ctx.target == DEFAULT_TARGET
                    else "The generator ties its behaviour to the PD, the delinquency days and `addNegStatus` (section 13a), so signal for any target that these columns relate to is inherited from them; these AUCs show what the simulation contains, not what real open-banking data would show."
                    if summary.get("planted")
                    else "These are AUCs on simulated behaviour: they say how much signal the simulation contains, not what real open-banking data would show."
                ),
            )

    # ── 14e. how to use all this ───────────────────────────────────────────────────────────────────────────────────
    rows_total = st["mock_world"].manifest.get("rows", {}).get("api.transactions")
    n_api_rows = f"{rows_total / 1e6:.0f} million API rows" if rows_total else "the whole API table"
    rep.text(
        "### How to use the mock and synthetic data\n\n"
        "- **Good for:** developing and testing the pipeline end to end (parsing the 7 bank dialects, joining customers across banks, handling fields that are missing by design); unit-testing feature code against known answers (section 13d); "
        f"load and run-time tests on {n_api_rows}; practising consent-based data collection.\n\n"
        "- **Not good for:** estimating how much open-banking data helps to predict default, choosing between feature ideas by their AUC, or tuning thresholds. "
        + (
            "The behaviour was generated from the label (section 13a)"
            if summary.get("planted")
            else "The behaviour is simulated (section 13a)"
        )
        + (
            f"; the loan itself is visible for only {pct(vis['share_visible'], 0)} of the customers"
            if (vis := st.get("mock_visibility"))
            else ""
        )
        + (
            f"; for {pct(summary['pre_window'], 0)} of the loans the transaction window starts after origination (14b)."
            if summary.get("pre_window") is not None
            else "."
        )
        + "\n\n"
        "- **If you model on it anyway:** split by customer (never by transaction), keep every API feature out of an application-time (set A) framing, treat missing values as 'this bank does not expose it', drop the `truth/` tables as inputs, and report results next to the stress benchmark above."
    )
    rep.code(
        "Load the API features next to the loan columns (starter code)",
        "import pandas as pd\n\n"
        "# 1. run:  uv run python EDA.py --export-mock-features   -> reports/mock_customer_features.csv\n"
        "feats = pd.read_csv('reports/mock_customer_features.csv', index_col='cid')\n"
        "# 2. columns: the target, the API features (see the dictionary above) and 'shard' (250 customers per shard)\n"
        "X, y = feats.drop(columns=['target', 'shard']), feats['target']\n"
        "# 3. validate by customer groups so no customer's entries are split across folds:\n"
        "from sklearn.model_selection import GroupKFold\n"
        "folds = GroupKFold(5).split(X, y, groups=feats['shard'])\n",
    )
    if ctx.args.export_mock_features:
        out = mctx.df[MOCK_FEATURE_NAMES].copy()
        out.insert(0, "target", y)
        out.insert(1, "shard", mctx.df[FILE_COL].to_numpy())
        out.index.name = "cid"
        out.to_csv(rep.out_dir / "mock_customer_features.csv")
        rep.say(f"   wrote mock_customer_features.csv ({len(out):,} rows x {out.shape[1]} columns)")
        rep.callout(
            "info",
            f"`reports/mock_customer_features.csv` was written: {len(out):,} rows (one per mock customer) with the target, the shard and the {len(MOCK_FEATURE_NAMES)} API features. It is row-level (synthetic) data: do not share the folder blindly.",
        )


def build_story(ctx: Ctx) -> tuple[list[str], list[str]]:
    """The executive summary: five things to know + the rest in plain language.  Every number comes from the sections
    above, and a sentence is only written when the number behind it exists and supports it."""
    st = ctx.store
    rate, n_pos = ctx.base_rate, int(ctx.y.sum())
    fs = feature_sets(ctx)
    q, pool, t0, gen = st.get("quality"), st.get("pooling"), st.get("tier0"), st.get("generator")
    r3, r_t0 = rung_of(ctx, "no_del"), rung_of(ctx, "delinq_alone")
    a_auc, framing = st.get("app_auc"), st.get("framing_auc", {})
    n_files = len(ctx.file_info)

    # ── the five things ──────────────────────────────────────────────────────────────────────────────────────────────
    top = []
    pooled = (
        f" The {n_files} files behave like random chunks of one population, so pool them."
        if pool and pool["interchangeable"]
        else f" The {n_files} files may differ from each other: check section 3 before pooling."
        if pool
        else ""
    )
    top.append(
        f"**The task.** Predict `{ctx.target}`: {n_pos:,} of {ctx.n:,} loans are positive ({pct(rate, 2)}, about 1 in {1 / rate:.0f})."
        + (
            " It is a rare-event problem: judge models with ROC-AUC and PR-AUC, never accuracy."
            if rate < RARE_EVENT
            else " Judge models with ROC-AUC and PR-AUC rather than accuracy."
        )
        + (" What the label exactly means is not documented - ask the organisers." if ctx.target_is_default else "")
        + pooled
    )

    if t0 and t0["median_auc"] >= TIER0_CONFIRMED_AUC:
        rc, iq, ia = st.get("recipe"), st.get("incumbent_quiet"), st.get("incumbent_auc")
        top.append(
            f"**Do not use as inputs: the delinquency columns and the supplied score.** {t0['n_fingerprints']} of the {t0['n']} delinquency / penalty columns reach AUC ≥ {LEAK_AUC} on their own"
            + (f" (together {r_t0['auc']:.3f})" if r_t0 else "")
            + ". They record the arrears state the label itself reflects"
            + (
                f": a small rule over them rebuilds {pct(rc['recall'], 0)} of the events at {pct(rc['precision'], 0)} precision"
                if rc
                else ""
            )
            + "."
            + (
                f" The supplied score / PD is driven by the same information: AUC {ia[0]:.2f} overall, but {ia[1]:.2f} (±{iq['half_width']:.2f}) on the {iq['rows']:,} loans with no delinquency history ({iq['events']} events)."
                if ia and iq
                else ""
            )
        )
    elif t0:
        top.append(
            f"**Delinquency columns are only moderately related to this target** (median single-column AUC {t0['median_auc']:.2f}, best {t0['max_auc']:.2f}), so the 'delinquency columns reveal the label' idea does not hold for `{ctx.target}`."
        )

    trust = []
    if a_auc is not None and a_auc >= UNREALISTIC_AUC:
        trust.append(
            f"Columns known when the loan is granted already reach AUC {a_auc:.3f}; a good application scorecard usually lands between {REALISTIC_AUC[0]:.2f} and {REALISTIC_AUC[1]:.2f}."
            + (
                f" Even without the {len(st['state_track']['trackers'])} of them that follow the loan's arrears state ({', '.join(st['state_track']['trackers'][:5])}) the AUC is {framing['A_strict']:.3f}."
                if framing.get("A_strict") is not None and st.get("state_track")
                else ""
            )
        )
    elif a_auc is not None:
        trust.append(
            f"Columns known when the loan is granted reach AUC {a_auc:.3f}, which is plausible for credit risk."
        )
    if q and q["n_rules"] and q["n_bad_rules"]:
        trust.append(
            f"{q['n_bad_rules']} of {q['n_rules']} relationships the dictionary promises break in more than {pct(RULE_TOL, 0)} of rows"
            + (
                " in a way a statistical generator reproduces, so the data may be synthetic or heavily perturbed."
                if gen and gen["mae_copula"] < 0.5 * gen["mae_indep"]
                else "."
            )
        )
    if trust:
        top.append(
            "**Treat high scores with suspicion.** "
            + " ".join(trust)
            + " A high score here does not prove a good model."
        )

    if fs["A"]:
        fr = [
            f"{lab} ({len(cols)} columns{', AUC ' + f'{framing[key]:.3f}' if framing.get(key) is not None else ''})"
            for key, lab, cols in (
                ("A", "A = known at origination", fs["A"]),
                ("B", "B = + balances and schedule", fs["B"]),
                ("C", "C = + delinquency columns", fs["C"]),
            )
        ]
        top.append(
            f"**Start here.** Load a column set from `reports/feature_roles.csv`: {'; '.join(fr)}. Validate with stratified {N_FOLDS}-fold CV and keep "
            + (
                f"`{pool['last_file']}` sealed for the final test."
                if pool and pool["interchangeable"]
                else "one file sealed for the final test."
            )
            + " Pick the framing (section 10) before the algorithm."
        )
    top.append(
        f"**Before you invest time, ask the organisers** the {st.get('n_questions', 'few')} questions at the end of section 10 - first the metric and submission format, then whether the delinquency columns and the score may be used as inputs."
    )

    # ── the rest ─────────────────────────────────────────────────────────────────────────────────────────────────────
    more = []
    if q:
        clean_shape = q["n_na"] == 0 and q["n_dup"] == 0
        text = f"**Can the data be trusted?** Structurally {'yes' if clean_shape else 'not fully'} ({q['n_na']:,} missing cells, {q['n_dup']:,} duplicate rows)"
        if q["n_rules"] and q["n_bad_rules"]:
            text += f". But {q['n_bad_rules']} of {q['n_rules']} dictionary relationships fail in more than {pct(RULE_TOL, 0)} of rows (worst: `{q['worst_rule']}`, {pct(q['worst_share'], 0)})"
        if gen and gen["mae_copula"] < 0.5 * gen["mae_indep"]:
            text += (
                f". A model that knows only each column's distribution and the rank correlations predicts the violation rates within {gen['mae_copula'] * 100:.1f} percentage points (independent columns: {gen['mae_indep'] * 100:.1f}), "
                "which is what a tabular generator - or heavy per-column noise - would produce; real extracts also break some identities for mundane reasons"
            )
        more.append(text + ".")
    if st.get("best_cut") and st["best_cut"].get("column"):
        bc = st["best_cut"]
        more.append(
            f"**One cut-off already tells a lot.** `{bc['column']}` {bc['sign']} {bc['threshold']:,.4g} flags {bc['flagged']:,} loans with precision {pct(bc['precision'], 0)} and recall {pct(bc['recall'], 0)} (Jaccard {bc['jaccard']:.2f}). The label is a loan state, not a single threshold."
            if bc["jaccard"] >= 0.6
            else f"**No single cut-off reproduces the label** (best: `{bc['column']}` {bc['sign']} {bc['threshold']:,.4g}, Jaccard {bc['jaccard']:.2f})."
        )
    fpv = st.get("fingerprints")
    if fpv and fpv["n_values"]:
        more.append(
            f"**Delinquency histories repeat the same few values.** {fpv['n_values']} exact values (away from any column ceiling) catch {pct(fpv['events_covered'], 0)} of all events at {pct(fpv['precision'], 0)} precision (picked on these same rows)."
        )
    if r3:
        lr = st.get("lr")
        more.append(
            f"**A result to double-check.** Without delinquency columns and without the supplied score a boosted-tree model still reaches AUC {r3['auc']:.3f}"
            + (f"; with columns known at origination only, {a_auc:.3f}" if a_auc is not None else "")
            + (
                f". On the same {lr['n_cols']} columns and folds, logistic regression gets {lr['auc']:.3f} against {lr['gbm_auc']:.3f} for boosted trees"
                if lr
                else ""
            )
            + "."
        )
    vc, ag = st.get("validation_checks"), st.get("age_stress")
    if vc:
        more.append(
            f"**How to validate.** A different random seed moves the AUC by {vc['noise']:.4f}, so smaller differences between models are noise. "
            + (
                f"Leaving whole files out changes it by {vc['file_out_gap']:+.4f}, so ordinary stratified CV is fine. "
                if vc["file_out_gap"] is not None
                else ""
            )
            + f"Class weights change the AUC by {vc['cw_auc_gain']:+.4f} and the Brier score from {vc['brier']:.4f} to {vc['cw_brier']:.4f}: start without re-weighting."
        )
    if ag:
        more.append(
            "**A stress test without dates.** Training only on young loans and testing on old ones ("
            + f"{ag['n_old']:,} loans)"
            + " costs "
            + "; ".join(f"{v:.3f} AUC for {k}" for k, v in ag["drops"].items())
            + (
                ": the signal survives a change of vintage."
                if ag["max_drop"] < 0.02
                else ": part of the signal is this portfolio's age structure, so expect less on a later vintage."
            )
        )
    r_prof, r_weak = rung_of(ctx, "profile_alone"), rung_of(ctx, "weak_alone")
    if r_prof and r_weak:
        more.append(
            f"**Sanity checks that fail.** The borrower profile alone (customer + income columns) reaches AUC {r_prof['auc']:.3f}, and five columns with little direct link to default (age, income, loan amount, interest rate, term) alone reach {r_weak['auc']:.3f}; such columns normally stay near {SINGLE_COLUMN_AUC[0]:.1f}-{SINGLE_COLUMN_AUC[1]:.1f}. Income and rate carry some genuine risk signal, so this is supporting evidence, not proof, that the label and the columns were generated together."
        )
    cc = st.get("concentration")
    if cc and cc["diffuse"]:
        more.append(
            f"**No single leak to remove.** Even after deleting the {cc['k']} strongest application-time columns ({', '.join(cc['top'])} …) a re-trained model keeps AUC {cc['after_k']:.3f}: the signal is shared by many columns."
        )
    mk = st.get("markers")
    if mk:
        more.append(
            f"**A handful of yes/no markers explain most of it.** Counting simple markers ({', '.join(mk['names'][:4])}{' …' if len(mk['names']) > 4 else ''}) - no model at all - gives AUC {mk['auc']:.3f} out-of-fold: {pct(mk['events_with_marker'], 0)} of events carry at least one marker versus {pct(mk['non_events_with_marker'], 0)} of non-events, and the event rate goes from {pct(mk['low'], 1)} (no marker) to {pct(mk['high'], 0)} ({mk['k_high']} markers)."
        )
    zc = st.get("zero_count")
    if zc:
        more.append(
            f"**The 'run-off' signature.** Count how many of the {len(zc['cols'])} balance / schedule columns are exactly 0 (a closed, written-off or fully-due loan has several): the event rate goes from {pct(zc['low'], 1)} (none) to {pct(zc['high'], 0)} ({zc['k_high']} of them), and this one count has AUC {zc['auc']:.3f}."
        )
    coh = st.get("cohorts")
    if coh and coh["rows_share"] <= 0.30 and coh.get("events_share", 0) >= 0.5:
        more.append(
            f"**Events are concentrated.** The {coh['k50']} riskiest cohorts over {', '.join(coh['cols'][:3])}{' …' if len(coh['cols']) > 3 else ''} hold {pct(coh['rows_share'], 1)} of the loans but {pct(coh['events_share'], 0)} of all events."
        )
    fam = st.get("families")
    if fam:
        topf = max(fam["alone"], key=fam["alone"].get)
        more.append(
            f"**Where the signal lives.** With delinquency and score removed, the strongest single family is '{FAMILIES[topf][0]}' (AUC {fam['alone'][topf]:.2f} alone); the build-up curve reaches {fam['curve'][-1][1]:.3f} after {len(fam['curve'])} families."
        )
    fr_ = st.get("fairness")
    if fr_ and fr_.get("uneven"):
        more.append(
            f"**Groups.** Sensitive attributes are in the data and predictive. The application-time model flags `{fr_['worst']}` at {fr_['ratio']:.2f}x the average rate, {'less' if fr_['ratio'] < 1 else 'more'} often than average; that group's event rate is {fr_['event_ratio']:.2f}x the average ({fr_['events']:,} events"
            + (", few enough to make this uncertain" if fr_["events"] < 100 else "")
            + "). See the group audit in section 9."
        )
    ms, lk, vis = st.get("mock_summary"), st.get("mock_link"), st.get("mock_visibility")
    if ms or lk or vis:  # the mock / synthetic bank data (sections 11-14); its AUCs never go into the five things
        text = "**Mock & synthetic bank data (sections 11-14).**"
        if lk:
            text += f" {lk['n_mock']:,} simulated customers are {'exact rows of' if lk['found'] >= 0.999 and not lk['fast'] else 'linked to'} the supplied loans, with the supplied label ({pct(lk['rate_mock'], 2)} positive)."
        if vis:
            text += f" Through the bank APIs a lender would see the loan itself for only {pct(vis['share_visible'], 0)} of them."
        if ms and ms.get("planted"):
            text += (
                f" The simulated behaviour is generated from a latent stress score that is a formula of the PD rank, the delinquency days and the label itself (R² {ms['stress_r2']:.4f}), "
                "so AUCs computed from it"
                + (
                    f" (bank behaviour alone {ms['auc_beh']:.2f}, with the loan columns {ms['auc_a_api']:.2f})"
                    if ms.get("auc_beh") is not None and ms.get("auc_a_api") is not None
                    else ""
                )
                + " measure planted signal, not whether open-banking data predicts default."
            )
        if ms and ms.get("recovery"):
            rc = ms["recovery"]
            text += f" Use it to build and test pipelines and feature code (a salary-based income estimate lands within ±10% for {pct(rc['income_within10'], 0)} of salaried customers), not to estimate predictive value."
        more.append(text)
    return top, more


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def thread_limit(requested: int) -> int:
    """Threads for BLAS / OpenMP: the --n-jobs value, but never more than a cap the user already set in the environment."""
    caps = [
        int(v)
        for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
        if (v := os.environ.get(k, "")).isdigit() and int(v) > 0
    ]
    return min([requested, *caps])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pre-modeling EDA of the loan-portfolio dataset -> one HTML report + figures + tables.",
        epilog="examples:\n  uv run python EDA.py\n  uv run python EDA.py --fast\n  uv run python EDA.py --target RestructuringStatusBool\n"
        '  uv run python EDA.py --target score --binarize "<=150"\n  uv run python EDA.py --sections 1,2,8 --no-models',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help=f"folder with df_*.csv and the dictionary xlsx [default: {DEFAULT_DATA_DIR}]",
    )
    p.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"folder that receives eda_report.html, figures/ and tables/ [default: {DEFAULT_OUT_DIR}]",
    )
    p.add_argument(
        "--target",
        default=None,
        help=f"binary column to analyse as the target [default: {DEFAULT_TARGET}, the dictionary's outcome flag]",
    )
    p.add_argument(
        "--binarize", default=None, metavar="OP_NUM", help="turn the numeric --target into 0/1, e.g. '<=150' or '>=85'"
    )
    p.add_argument(
        "--fast",
        action="store_true",
        help=f"use a stratified {FAST_ROWS:,}-row sample and lighter models (about a minute)",
    )
    p.add_argument(
        "--no-models",
        action="store_true",
        help="skip the model-based checks (adversarial validation in section 3, the score reverse-engineering in section 8, all of section 9)",
    )
    p.add_argument(
        "--sections",
        default=None,
        metavar="N,N,...",
        help="run only these section numbers, e.g. 1,2,8 [default: all ten]",
    )
    p.add_argument(
        "--n-jobs",
        type=positive_int,
        default=4,
        help="upper bound of CPU threads for models / BLAS; a lower OMP_NUM_THREADS wins [default: 4]",
    )
    p.add_argument(
        "--mock-dir",
        default=None,
        metavar="DIR",
        help=f"folder of the generated mock/synthetic bank dataset (MANIFEST.json, api/, truth/) for sections 11-14 [default: {DEFAULT_MOCK_DIR.relative_to(ROOT)} when --data-dir is the default and the folder exists]",
    )
    p.add_argument(
        "--no-mock",
        action="store_true",
        help="skip sections 11-14 (the mock/synthetic bank data) even if the folder exists",
    )
    p.add_argument(
        "--export-mock-features",
        action="store_true",
        help="also write reports/mock_customer_features.csv: one row per mock customer with the API features and the target (row-level, synthetic)",
    )
    p.add_argument("--open", action="store_true", help="open the HTML report in the browser when done")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    # the console output uses ✔ ✖ ▲ symbols: never crash on a narrow terminal encoding
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    setup_matplotlib()
    t0 = time.perf_counter()
    out_dir = Path(args.out_dir).resolve()
    rep = Report(out_dir)
    rep.say(f"EDA.py · reading {args.data_dir}  ·  writing {out_dir}")
    ctx = build_context(args, rep)  # validates the arguments and the data: nothing on disk has been touched yet
    wanted = None
    if args.sections:
        try:
            wanted = {int(x) for x in args.sections.replace(" ", "").split(",") if x}
        except ValueError:
            sys.exit(f"--sections expects section numbers like 1,2,8 (got {args.sections!r})")
    world, why_not = open_mock(args)
    if world is None and (args.mock_dir is not None and not args.no_mock or wanted and wanted & MOCK_SECTIONS):
        sys.exit(f"the mock/synthetic bank data is not available: {why_not}")
    ctx.store["mock_world"] = world
    rep.clear_outputs()
    rep.say(
        f"loaded {ctx.n:,} rows x {len(ctx.features)} features · target = {ctx.target} ({pct(ctx.base_rate, 2)} positive)"
    )
    rep.say(
        f"mock/synthetic bank data: {world.name} ({len(world.shards)} of {world.n_shards} shards)"
        if world
        else f"mock/synthetic bank data: not included - {why_not}"
    )
    # keep BLAS / OpenMP polite on a shared machine; restored afterwards
    with threadpool_limits(limits=thread_limit(args.n_jobs)):
        for num, title, question, fn in sorted(SECTIONS, key=lambda s: s[0]):
            if wanted is not None and num not in wanted:
                continue
            if num in MOCK_SECTIONS and world is None:
                continue
            rep.section(num, title, question)
            try:
                with rep.timed():
                    fn(ctx)
            except Exception as exc:  # one broken section must not kill the whole report
                rep.error(exc)
    secs = time.perf_counter() - t0
    try:
        top, more = build_story(ctx)
    except Exception as exc:  # the summary is a bonus; never lose the report over it
        top, more = [], []
        rep.say(f"(could not build the executive story: {exc!r})")
        rep.failures.append("executive summary")
    header = {
        "title": "Loan portfolio · pre-modeling EDA",
        "subtitle": f"Target analysed: {ctx.target}"
        + (" (default - change it with --target)" if ctx.target_is_default else ""),
        "chips": {
            "rows": f"{ctx.n:,}",
            "features": len(ctx.features),
            "positives": f"{int(ctx.y.sum()):,} ({pct(ctx.base_rate, 2)})",
            "mode": "fast sample" if args.fast else "full data",
            **({"mock data": f"{len(ctx.store['mock_scan']['cust']):,} customers"} if "mock_scan" in ctx.store else {}),
        },
        "story": top,
        "story_more": more,
        "footer": f"Generated {datetime.now():%Y-%m-%d %H:%M} in {secs:.0f}s · python {platform.python_version()} · pandas {pd.__version__} · numpy {np.__version__} · scipy {scipy.__version__} · scikit-learn {sklearn.__version__} · command: EDA.py {' '.join(argv if argv is not None else sys.argv[1:])}",
    }
    out = out_dir / "eda_report.html"
    try:
        out.write_text(rep.render_html(header), encoding="utf-8")
    except Exception as exc:  # the figures and tables are already on disk: say what is left instead of a bare traceback
        rep.say(
            f"✖ the HTML report could not be rendered: {exc!r}\n{''.join(traceback.format_exception(exc)).strip()}\n  (figures/ and tables/ are complete)"
        )
        rep.write_manifest()
        return 1
    rep.write_manifest()
    summary = [f"# {header['title']}", f"*{header['subtitle']}*", "", "## Five things to know", ""]
    summary += [f"{i}. {t}" for i, t in enumerate(top, 1)] or ["(no summary available)"]
    summary += ["", "## More findings", ""] + [f"- {t}" for t in more]
    summary += ["", "## Findings that need attention", ""]
    for f in sorted(rep.findings, key=lambda f: (f.priority, SEVERITIES[f.severity][0])):
        if f.severity in ("critical", "warning") and f.priority <= 2:
            summary.append(
                f"- **{SEVERITIES[f.severity][1]}** - {f.title}. {f.detail}" + (f" -> {f.action}" if f.action else "")
            )
    (out_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    if top:
        rep.say(f"\n{'═' * 84}\n FIVE THINGS TO KNOW\n{'═' * 84}")
        for i, t in enumerate(top, 1):
            rep.say(f"  {i}. " + re.sub(r"[*`]", "", t))
    rep.say(f"\n✔ report written: {out}  ({out.stat().st_size / 1e6:.1f} MB) in {secs:.0f}s")
    if rep.failures:
        rep.say(
            f"▲ {len(rep.failures)} part(s) of the report could not be computed (see the warnings above): "
            + "; ".join(rep.failures)
        )
    if args.open:
        webbrowser.open(out.as_uri())
    return 1 if rep.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
