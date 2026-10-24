# API

Three FastAPI services. `make demo` starts them all with the web page; or one by one (start `service.py` last):

```bash
make api-banks        # API/mock_banks.py      :8000  the 12 bank APIs, all 100,000 customers, built on demand
make api-registries   # API/mock_registries.py :8001  mock RS + Creditinfo
make api-service      # API/service.py         :9000  the income estimation API (what the portal calls)
```

Swagger: http://localhost:9000/docs (also :8000/docs, :8001/docs). All state is in memory and resets on restart.

## The income API

| Call | What it does |
|---|---|
| `POST /v1/consents {"personal_id"}` | Consent requests to all 12 banks, RS and Creditinfo in parallel (the mock approves at once). Returns the bank table (accounts, "no relationship" banks) and the client profile with the source of each field. A bank that fails is marked `error`; the rest carry on. |
| `POST /v1/income-estimates {"consent_set_id"}` | Starts the job, returns `job_id` at once. |
| `GET /v1/income-estimates/{job_id}` | Progress per source, then the result: P10/P50/P90, confidence (label, precision score, measured hit rate), warnings, SHAP drivers. |
| `GET /v1/sources`, `GET /v1/demo/clients`, `GET /health` | The consent targets, the 20 existing demo clients, and the window the history is taken from. |

A source without consent (RS, Creditinfo or a bank) is skipped with a warning; the estimate still runs.

## Things to know

- **History window.** It ends where the bank data ends: `--as-of YYYY-MM-DD` if given, else the mock banks' own clock
  (`GET :8000/_mock/clock`, 2026-10-19). Asking for later days only finds empty days and shifts the numbers.
- **Confidence** (`income/confidence.py`): score = 100% - band width (P90-P10 over P50); the label uses fixed cut-offs
  (3 / 6 / 10 / 20%). The score is a precision, not a probability. The measured share of clients whose real income fell
  inside P10-P90 (about 80%, never 100%) is in `income/models/confidence_calibration.json` and shown next to it.
- **Client profile.** Name, birth date and place come from the lender's KYC record. In this demo that record is the
  generated customer (`/_mock/kyc/{customer}` on the bank mock). The name is checked against each bank's account owner name.
- **Demo clients.** `API/pick_demo_clients.py` picks kinds of person from the clients the model never trained on, typical
  of their kind; on 17 of the 20 the real income is inside the range, close to the held-out rate.
- **Synthetic data only.** Scores here say nothing about real-world accuracy.

## Tests

`uv run pytest tests/test_income_service.py tests/test_mock_registries.py` builds a 240-customer dataset in a temporary
folder (about 10 s) and runs the services against it: no other data needed.
