# API

The three FastAPI services of the demo. Run each in its own terminal from the repo root (start `service.py` last):

```bash
uv run python API/mock_banks.py --port 8000        # 12 mock bank APIs, all 100,000 customers, built on demand
uv run python API/mock_registries.py --port 8001   # mock RS + Creditinfo
uv run python API/service.py --port 9000           # income estimation API (what the portal calls)
```

Swagger: http://localhost:9000/docs (also :8000/docs and :8001/docs). State is in memory and resets on restart.

Flow: `POST /v1/consents` (personal id -> all banks + RS + Creditinfo) -> `POST /v1/income-estimates` (-> job id)
-> `GET /v1/income-estimates/{job_id}` (progress, then P10 / P50 / P90 + confidence).
Tests: `uv run pytest tests/test_income_service.py tests/test_mock_registries.py`.
