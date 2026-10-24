# Income Estimation ML model

**Track: AI in Finance – Direction 1, Credit Risk**

**Estimates how much a person earns each month, for lending decisions in Georgia, from their open-banking data.**
Built for the GITA Hackathon (theme: AI and Open Banking in finance). All data in this repository is synthetic.

A loan officer picks a client, one click asks all banks (and the Revenue Service and the credit bureau) for the client's
consent, and a few seconds later the officer sees the monthly income as a range, how precise that range is, which facts
drove it, and what is missing.

---

## 1. The problem

Georgian banks must verify income before lending: the National Bank's responsible-lending rules cap the payment-to-income
ratio, and loans without verified income are limited. But the Revenue Service (RS) only shows *declared* income, and about
37.1% of workers in Georgia are informal (2022; ILO Working Paper 159, based on the Geostat Labour Force Survey). Young people,
the self-employed, rural households and small businesses often have real income that is invisible or heavily discounted,
so they are rejected or under-lent.

## 2. The solution

With the client's consent, the lending bank combines:

| Source | What it gives | Mock in this repo |
|---|---|---|
| **Open banking** (the client's other banks) | accounts, balances, up to 12 months of transactions, over the Georgian Open Banking Standard (based on Berlin Group); the client re-authorises at least every 90 days | 12 bank APIs, one per bank, 7 wire dialects |
| **Revenue Service** | the client's taxpayer data and income items | `API/mock_registries.py` |
| **Creditinfo** (credit bureau) | existing loans and credit searches (no income) | `API/mock_registries.py` |
| **The lender's own KYC** | name, birth date, place | `/_mock/kyc` on the bank mock |

A machine-learning model turns this into **estimated monthly income as a range**: P10 (conservative),
P50 (most likely), P90 (optimistic). It works for clients with RS-verified income (refining it) and for clients without
(estimating from bank data alone). The range is returned with:
- a **confidence label** read from the width of the range (shown as ±% of the P50),
- the **measured hit rate** of that label on clients the model never saw,
- **SHAP explanations** of what pushed the estimate up or down,
- **warnings** when data is missing (for example no salary credits found, or a bank that refused its consent).

## 3. How it works

```
 Loan officer (React page, :5173)
        │  POST /v1/consents        one click: consent to every source, in parallel
        │  POST /v1/income-estimates → job id          GET /v1/income-estimates/{id}  (progress, then result)
        ▼
 Income API (FastAPI, :9000)  ──►  12 mock bank APIs (:8000)   accounts → balances → every transaction page
        │                     ──►  mock RS + Creditinfo (:8001)
        ▼
  clean the data ─► the same features the model was trained on ─► XGBoost quantile model (P10 / P50 / P90)
                                                                 ─► confidence + SHAP + warnings
```

1. **Consent.** The API asks the 12 banks, RS and Creditinfo for consent at once (the mock approves immediately).
   A bank where the client has no account is marked "no relationship" and left out. A source that fails is marked failed;
   the others carry on.
2. **Fetch.** For every bank with a valid consent the API replays what a TPP does: accounts, balances, every transaction
   page, plus cards, loans and savings where the bank has them, using each bank's own routes and headers.
3. **Clean.** Card mirror entries, transfers between the client's own accounts and loan disbursements are kept out of the
   income and spending features.
4. **Features and model.** The raw bank data is turned into about 120 model inputs by the same code that built the training set,
   and an XGBoost quantile regression returns P10, P50 and P90.
5. **Judge.** Confidence comes from the band width; thin data (no bank data, under 3 months of history, fewer than 30
   transactions, no inflow) forces "very low" with the reasons.

## 4. Quick start

**You need:** Python 3.12 with [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and
[Node.js](https://nodejs.org) (`brew install node` on a Mac). Nothing else: the demo runs from a fresh checkout.

```bash
uv sync                  # Python packages, once
make frontend-setup      # web page packages, once
make demo                # starts everything; Ctrl-C stops it
# no make? run ./tools/demo.sh instead (same thing)
```

Then open **http://localhost:5173**. Swagger for the income API is at http://localhost:9000/docs.

`make demo` runs four processes. You can also start them one by one (start the income API after the two mocks):

| Command | What | Port |
|---|---|---|
| `make api-banks` | the 12 mock bank APIs for all 100,000 customers, built on demand | 8000 |
| `make api-registries` | mock RS and Creditinfo | 8001 |
| `make api-service` | the income estimation API | 9000 |
| `make frontend` | the web page (proxies `/api` to 9000) | 5173 |

**Tests:** `make test` (or `uv run pytest`). The API tests build their own 240-customer dataset in a temporary folder
(about 10 s), so no other data is needed. `make lint` runs ruff.

## 5. Using the demo

1. Pick one of the **20 existing demo clients** on the left (or type an 11-digit personal ID, or a client code `C00001` to
   `C100000`). The list runs from the clearest estimate to the hardest, and covers different bank mixes (BoG + TBC + a
   third bank, no TBC, small banks only), deposits, young, rural, sole proprietors, no salary credits, and remittances.
2. **Connect all banks.** Every bank, RS and Creditinfo gets its own animated request. The client profile appears
   (name, birth date, place, with the source of each; the name is checked against each bank's account owner name).
3. **Estimate income.** Each source shows its progress, then the result: the P50 on a P10 to P90 bar, the confidence
   badge, "How sure is this?" (band width, measured hit rate), and "Why this estimate" (SHAP factors; **Show all factors** opens
   every group and the model features inside it, with each one's share of the impact in %).
4. **Officer decision.** Under the result, the officer accepts, asks for more documents, or rejects. With high confidence
   one click is enough; with lower confidence, a withheld estimate or a failed source, a reason (10+ characters) and the
   box "I reviewed the uncertainty and the evidence" are required. The decision is locked and appears in the audit trail.
5. **Demo controls** (left, collapsed): simulate "Revenue Service unavailable" or "Banks unavailable" in the next
   estimate to show the fallback live. When no bank can be reached the estimate is withheld and the trace ends with
   "Estimate withheld".

`http://localhost:5173/?client=<personal id>` runs the whole flow from a link.

## 6. API

### The income API (`API/service.py`, port 9000)

| Call | What it does |
|---|---|
| `POST /v1/consents` `{"personal_id": "…"}` | Consent requests to all 12 banks, RS and Creditinfo, in parallel. Returns the table of banks (`connected`, `no_relationship`, `error`), the RS and Creditinfo results and the client profile with its provenance. Accepts an 11-digit personal ID, `PNOGE-` + the ID, or a client code. `404` if no bank knows the client; `502` if the bank gateway is unreachable. |
| `POST /v1/income-estimates` `{"consent_set_id": "…"}` | Starts the job and returns `202` with a `job_id` at once. |
| `GET /v1/income-estimates/{job_id}` | `status` (`queued`, `running`, `done`, `failed`), `stage`, one progress line per source, and when done the `result`. |
| `GET /v1/sources` | The 12 banks plus RS and Creditinfo (what a consent request goes to). |
| `GET /v1/demo/clients` | The 20 existing demo clients. |
| `GET /health` | Model date and the end of the history window. |

```bash
curl -s -X POST localhost:9000/v1/consents -H 'content-type: application/json' -d '{"personal_id":"50334745589"}'
curl -s -X POST localhost:9000/v1/income-estimates -H 'content-type: application/json' -d '{"consent_set_id":"<id from above>"}'
curl -s localhost:9000/v1/income-estimates/<job_id>
```

The result, abridged:

```jsonc
{
  "income": {"p10": 4295, "p50": 4327, "p90": 4423, "currency": "GEL", "period": "monthly"},
  "confidence": {
    "level": "very high", "score": 97, "band_share": 0.03, "plus_minus_pct": 1.5,
    "downgrade_reasons": [],                       // filled when thin data forces "very low"
    "measured": {"inside_p10_p90_pct": 83.7, "median_error_pct": 0.45, "within_10pct_pct": 100.0, "basis": "…"}
  },
  "explanation": {"starting_point_gel": 2717, "drivers": [{"driver": "Total money coming in", "effect_gel": 1053,
                  "share_pct": 64.0, "pct_of_p50": 24.3}, …], "all_drivers": [ /* every group, with its model features */ ]},
  "warnings": [],                                  // e.g. "No salary credits found. …", a failed source
  "data": {"banks_used_names": ["Bank of Georgia", "Liberty Bank", "TBC Bank"], "months_covered": 12.0, "transactions_used": 585},
  "sources_used": {"rs": true, "creditinfo": true}
}
```

Behaviour worth knowing:
- A source without a granted consent (RS, Creditinfo or a bank) is skipped with a warning; the estimate still runs.
- The **history window** ends where the bank data ends: `--as-of YYYY-MM-DD` if given, else the mock banks' own clock
  (2026-10-19). Real banks have data up to today; the mock banks stop on the day they were generated.
- Options: `uv run python API/service.py --port 9000 --banks-url … --registries-url … --as-of …`.
- Consent sets and jobs live in memory and reset on restart.

### The mock services

| Service | Notes |
|---|---|
| **Bank mock** (`API/mock_banks.py`, :8000) | Serves all 100,000 generated customers through the unchanged bank dialects by replaying a customer's plan on demand (about 10 ms). `POST /_mock/session/{bank}?customer=…` gives an approved consent and token (skip mode); the bank routes (`/{bank}/…`) need `Authorization` and `Consent-ID`. Also `/_mock/banks`, `/_mock/kyc/{customer}`, `/_mock/clock`. |
| **RS and Creditinfo mock** (`API/mock_registries.py`, :8001) | Read from the generated customer records; nothing is simulated. Real consent flow (`POST …/v1/consents`, status, authorise) plus `/_mock/session/{service}`. RS serves legal form, citizenship and the `SOI04` income item (the data dictionary defines it as income from remittances); Creditinfo serves search counts and counts of open and closed contracts. The income label is never served. |

More in [`API/README.md`](API/README.md); the 12 bank dialects are documented in
[`openbanking_mock/README.md`](openbanking_mock/README.md) and `openbanking_mock/docs/`.

## 7. The model and the confidence score

- **Model:** XGBoost quantile regression (P10 / P50 / P90) on 100,000 synthetic clients, 80/20 split by person.
  Train with `uv run python income/income_predictor.py train`; the saved model is `income/models/income_model.joblib`.
- **Confidence** (`income/confidence.py`): `band_share = (P90 - P10) / P50`; **score = 100% minus band_share**;
  labels by fixed cut-offs: very high up to 3%, high up to 6%, medium up to 10%, low up to 20%, very low above.
  The score measures how *precise* the range is; it is **not the chance of being right**.
- **Measured hit rate** (`uv run python income/confidence_calibration.py` writes
  `income/models/confidence_calibration.json`): on the 20,000 clients the model never trained on, the real income fell
  inside P10 to P90 for 80.5% of clients overall (the band is built for 80%), with a median error of 0.78%. Per label:

  | Label | Share of clients | Inside P10–P90 | Median error |
  |---|---|---|---|
  | very high | 50% | 83.7% | 0.45% |
  | high | 19% | 81.5% | 0.96% |
  | medium | 20% | 73.9% | 2.47% |
  | low | 10% | 77.2% | 3.63% |
  | very low | 0.6% | 62.1% | 7.30% |

  The range does not always contain actual income. On the 20 demo clients, the true income falls inside the range for 17.
- **Explanations:** exact TreeSHAP values from XGBoost (`pred_contribs`) for the P50 head, grouped into readable drivers
  in `API/explain.py`. They add up: the average client's 2,717 GEL plus all factors equals the P50.
- **Demo clients:** `uv run python API/pick_demo_clients.py` selects typical clients from the held-out 20% and keeps existing catalog entries.

## 8. Repository map

| Folder | What is in it |
|---|---|
| `API/` | the three FastAPI services, the SHAP explainer, the demo-client picker, `demo_clients.json` |
| `frontend/` | the React + Vite page ([`frontend/README.md`](frontend/README.md)) |
| `income/` | the modelling: dataset builder, notebooks, `income_predictor.py` (train / predict), `income_pipeline.py` (raw bank data to estimate), `confidence.py`, calibration |
| `openbanking_mock/` | the generator and mock servers for 12 Georgian banks over 100,000 simulated customers (`obmock` package) |
| `nbg_synthetic_data/` | the organisers' loan and credit-registry data (`df_1..10.csv`, data dictionary) and `income_dataset.csv` (one row per person with the label) |
| `eda/` | pre-modelling EDA of the loan and bank data ([`eda/README.md`](eda/README.md)) |
| `synthetic/`, `tools/`, `tests/` | synthetic loan generator, helper scripts (`tools/demo.sh`), tests |

## 9. Rebuilding data and model

Only needed if you change the generator or the model; the demo needs none of this.

```bash
cd openbanking_mock && uv run python -m obmock generate        # 100,000 customers -> output/dataset (~10 min on 16 cores, ~2 GB)
uv run python income/build_income_dataset.py                    # api/ shards -> nbg_synthetic_data/income_dataset.csv
uv run python income/income_predictor.py train                  # -> income/models/income_model.joblib
uv run python income/confidence_calibration.py                  # -> income/models/confidence_calibration.json
uv run python API/pick_demo_clients.py                          # -> API/demo_clients.json
```

The large generated files (`api/transactions`, `truth/ledger`, `captures`) are not in git; the demo does not need them
because the bank mock simulates each customer on demand.

## 10. Honest limits

- **Everything is synthetic.** The income label and the bank data come from the same simulator, so the model finds income
  almost directly in the salary credits. The accuracy figures above compare confidence levels with each other; they are
  not real-world accuracy, and a real bank would see much wider errors.
- **RS data is thin.** The only RS-like field in the data is `SOI04`, defined as income from remittances and stored on a
  mixed scale; the mock serves it as it is and does not invent salary, dividends or rent.
- **Mocks, not the real thing.** mTLS, JWS signatures and real SCA / OAuth redirects are not enforced; the consent is
  approved in "skip mode". No bank publishes sandbox test data, so no mock claims to equal one.
- **Not built yet:** the affordability step (maximum loan payment from the P10 end and the National Bank's limits).
- Georgian bank API fields follow each bank's published spec; what is assumed is flagged in `openbanking_mock/docs/`.

## 11. Fairness

`uv run python income/fairness_check.py` measures the saved model on the 20,000 held-out clients (the same split as the
rest of the repository) and writes `income/models/fairness_report.json`; every number below comes from that file. The API
serves it at `GET /v1/model/fairness`; the result page does not show it. The model is not changed
by the check. Data is synthetic, so the figures compare groups with each other and are not real-world accuracy.

| Group | Clients | Inside P10–P90 (nominal 80%) | Median error |
|---|---|---|---|
| All | 20,000 | 80.5% | 0.78% |
| Male | 10,959 | 80.5% | 0.77% |
| Female | 9,041 | 80.5% | 0.79% |
| Georgian citizen | 19,513 | 80.5% | 0.78% |
| Not a citizen | 487 | 79.7% | 0.7% |
| 11 regions (groups of 100+ clients) | | 78.7% (Kakheti/Other) to 84.0% (Racha) | per region in the JSON |
| Other legal forms | 18,814 | 81.0% | 0.73% |
| **Sole proprietors** | 1,186 | **72.9%** | **4.67%** |

- **Gender is an input but has negligible influence.** Of 89 model inputs, SEX has 0.007% of the
  XGBoost total gain (rank 34) and a mean |SHAP| of 0.1 GEL on the P50 (rank 49). GeoCitizen:
  0.0% of the gain (rank 64), 0.02 GEL (rank 63).
- **Flip test.** Setting every client's SEX to the other value and predicting again changes the P50 by a median of
  0.0%, 95th percentile 0.082%, at most 1.226% (17.2% of clients see any change).
- **Could be removed.** An internal A/B retrain without SEX gave held-out coverage 80.0% vs 79.6% and median error
  0.64% vs 0.63% (this retrain is not part of this repository's scripts). A production version would drop the field to
  comply with fair-lending expectations.
- **Known limitation: sole proprietors.** Their income is estimated less accurately (72.9% coverage, 4.67% median error
  against 0.73%). Officers should check these cases with extra care.

## 12. Guardrails

| Control | What it does | Where |
|---|---|---|
| Consent handling | Nothing is read without a consent per bank and registry; every source call checks the consent status first; consent lives at most 90 days | `API/service.py` (`create_consents`, `_check_consent`) |
| ID and name masking | Personal IDs, consent IDs and account owner names are masked in the UI and the audit; tokens never reach the audit | `API/service.py` (`mask_name`), `API/demo_evidence.py` (`mask`) |
| Abstention when data is thin | Fewer than the minimum months or transactions, or no inflow: no estimate, confidence forced to "very low" | `API/service.py` (`judge_confidence`) |
| Per-source outage fallback | One failing bank or registry never sinks the estimate; the screen says which source is missing | `API/service.py` (`_pull_bank`, `_pull_registry`), demo control in `frontend/src/App.jsx` |
| Officer decision step | The model suggests, the officer decides: accept, request more documents or reject; a reason and an acknowledgement are required when confidence is low or evidence is missing; one decision per case | `API/service.py` (`/v1/cases/{id}/review`), `frontend/src/components/OfficerDecision.jsx` |
| Hash-chained audit trail | Every step and the officer decision is an append-only event, hash-linked; edits are detected; the stored result is bound to the audit | `API/case_store.py` |
| Confidence calibration | Labels come from the range width and are checked on held-out clients | `income/confidence.py`, `income/confidence_calibration.py` |
| Fairness check | Coverage by group, SEX and GeoCitizen importance, gender flip test | `income/fairness_check.py`, `GET /v1/model/fairness` |
| Synthetic data only | No real customer data; the UI carries a "synthetic data" banner | `nbg_synthetic_data/`, `openbanking_mock/`, `frontend/src/App.jsx` |

## 13. Third-party components and licences

Direct dependencies as declared in `pyproject.toml` and `frontend/package.json`; exact versions are in `uv.lock` and
`frontend/package-lock.json`. Licences are read from the installed packages.

| Component | Used for | Licence |
|---|---|---|
| FastAPI | the three API services | MIT |
| Uvicorn | serves the APIs | BSD-3-Clause |
| HTTPX | calls between the services | BSD-3-Clause |
| Pydantic (via FastAPI) | request models | MIT |
| XGBoost | quantile model, TreeSHAP | Apache-2.0 |
| scikit-learn | train/test split, preprocessing helpers | BSD-3-Clause |
| joblib (via scikit-learn) | model file | BSD-3-Clause |
| NumPy | numerics | BSD-3-Clause (with 0BSD, MIT, Zlib, CC0-1.0 parts) |
| pandas | tables | BSD-3-Clause |
| SciPy | statistics | BSD-3-Clause |
| threadpoolctl | thread limits for the numeric libraries | BSD-3-Clause |
| PyArrow | Parquet files of the mock banks | Apache-2.0 |
| jsonschema | checks mock responses | MIT |
| PyYAML | mock bank specifications | MIT |
| openpyxl | reading the organiser's Excel files | MIT |
| Matplotlib | EDA charts | Matplotlib licence (PSF-based) |
| pytest, ruff, ipykernel (dev) | tests, lint, notebooks | MIT, MIT, BSD-3-Clause |
| React, React DOM | the web page | MIT |
| Vite, @vitejs/plugin-react | build and dev server | MIT |
| oxlint, @types/react, @types/react-dom (dev) | lint and types | MIT |

## 14. More documentation

[`API/README.md`](API/README.md) · [`frontend/README.md`](frontend/README.md) · [`eda/README.md`](eda/README.md) ·
[`openbanking_mock/README.md`](openbanking_mock/README.md)

Integration demo details, audit limitations and the Python launcher: [API/DEMO_GUIDE.md](API/DEMO_GUIDE.md).
