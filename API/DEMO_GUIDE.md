# Income-assessment demo

This is an **AI in Finance** prototype for a Georgian loan officer assessing recurring income, including service payments when payroll credits are absent. It assists income review; it does not approve loans. All customers, bank activity, registry responses and outcome labels are synthetic.

## Run from a fresh checkout

Use Python 3.12+ and Node.js 22.12+. Install the existing locked dependencies:

```powershell
uv sync
cd frontend
npm ci
cd ..
uv run python tools/run_demo.py --check
uv run python tools/run_demo.py --evaluate
```

Open http://127.0.0.1:5173. The launcher starts the 12-bank mock on port 8000, registry mock on 8001, income API on 9000, and Vite on 5173. Ctrl+C stops the processes it created. Existing services on those ports must be stopped first. Logs and local case records live under git-ignored `.runtime/`.

The committed XGBoost joblib is loaded normally when supported. For an incompatible native snapshot, the loader reads its embedded portable UBJ model in memory. It does not rewrite the artifact or retrain. No file inside `.venv/` is needed as a model artifact.

Local demo accounts:

| Account | Default password | Access |
| --- | --- | --- |
| officer | `demo-review-only` | Create assessments, run evaluations and record review decisions |
| auditor | `demo-audit-only` | Read saved cases, evidence and exports |

Override passwords with `DEMO_OFFICER_PASSWORD` / `DEMO_AUDITOR_PASSWORD`. These shared demo accounts are not production bank SSO. The service binds to loopback by default. Sessions use HttpOnly, SameSite=Strict cookies; set `DEMO_HTTPS=1` only behind HTTPS. Cross-origin writes are restricted to the documented local origins.

## Recording the existing sole proprietor

1. Sign in as officer. Select **Sole proprietor**, C50811, personal ID `91038618469`, then **Connect all banks**. Consent is automatically authorized in the mocks; source APIs subsequently validate tokens and consent. Liberty holds the account; eleven banks return no relationship.
2. Leave the recording scenario at **Normal collection**, then click **Estimate income**. The trace records actual parallel source requests, normalization, evidence generation, feature generation, inference and TreeSHAP durations. There are no fake processing delays.
3. Inspect **Underlying transaction evidence**. Its rules run on retrieved API records, not customer labels. This case has 76 service credits totaling 21,694.69 GEL over a 12-month window. Monthly gross service receipts are 1,807.89 GEL; these are not a statement of net earnings. Cash deposits, P2P receipts, loans and own-account movements are not counted as service receipts.
4. The normal model result is P10 1,746 / P50 1,878 / P90 2,104 GEL, low confidence, 81/100 interval-precision score. These values should be verified in the running app; partial-source scenarios may produce different results. The score is not a correctness probability.
5. Show **RS remittance income (SOI04)** in SHAP. RS exposes remittance income, not a salary declaration. The input is zero for this customer; its positive SHAP contribution describes a model effect, not received or declared salary.
6. Record an officer decision with a reason. Accepting a low-confidence or partial-source estimate requires acknowledgment. An abstained assessment cannot be accepted. No decision approves a loan.
7. Reopen the case from **Saved cases**, inspect the audit events and download the JSON record. Auditor sign-in can view the record but cannot create assessments or submit decisions.

For a visible fallback, select **Simulate RS HTTP 503** and run another assessment. The mock returns an actual 503 to the source request; the service warns and continues with missing RS features. Matched calibration is withheld. **Simulate bank HTTP 503** demonstrates abstention: no estimate is produced without adequate bank evidence. Neither option changes customer data or stops services.

## Benchmarks and model risk

The salary-only baseline reports a lack of identified payroll credits as **abstention**, not zero actual income. The evidence panel offers a server-timed manual aggregation task on retained normalized transactions. Classifications and model results are hidden while the operator counts and sums service credits. A correct answer is required to finish. The recorded comparison is one self-timed operator run, not an independent study or a measure of the complete manual underwriting process. Until a human actually performs it, no manual time saving is reported. Prepare a genuine operator run before recording if you want to show measured impact.

`uv run python income/demo_evaluation.py` evaluates the existing model on the fixed 20% held-out split and writes `.runtime/model-evaluation.json`. The UI can also run and display it. Metrics include MAE, interval coverage, salary-baseline abstention and matched-case comparisons, income-profile, sex-code, age-band and region diagnostics, sample sizes, and observed group gaps. Age, sex, citizenship and location are currently model inputs; feature justification and sensitivity review remain necessary. These synthetic diagnostics do not certify fairness or predict real-world performance because bank behaviour and target labels share a simulator.

## Architecture, records and limits

Browser → Vite `/api` proxy → authenticated income API → parallel mock banks / RS / Creditinfo → bank-dialect normalization → unchanged model feature pipeline → XGBoost quantiles and TreeSHAP → officer review → SQLite case record and audit export.

Cases store masked statement identifiers, source statuses and masked consent references, observation window, evidence hash, model artifact hash, processing trace, predictions, warnings and review history. Source access tokens and full personal IDs are not stored in case exports. The audit has no update/delete endpoint; hash-linked events detect edits. A local database administrator can still rewrite a database, so this is not an externally immutable archive. Review reasons are free text: operators must not enter credentials or personal data. Local sessions expire after eight hours and reset on server restart; saved case records remain.

Before adoption: bank SSO and tenant isolation, durable consent lifecycle/revocation, a retention/deletion policy, an independently secured audit archive, live API onboarding, representative real-data validation, model-risk and demographic-feature review, and regulatory assessment are still required. No production readiness or NBG compliance claim is made.

## Verification

```powershell
uv run pytest tests/test_demo_workspace.py tests/test_income_predictor.py tests/test_mock_registries.py
cd frontend
npm run build
```

`test_demo_workspace.py` uses the actual on-demand mock stack and committed model; it does not require the omitted transaction shards. It verifies the existing customer, roles, review gates, faults, abstention, restart persistence, export privacy, audit tampering detection and benchmark answer checking.

Primary third-party components: React / Vite / FastAPI (MIT), HTTPX / NumPy / pandas / scikit-learn / joblib (BSD-3-Clause), XGBoost / Apache Arrow (Apache-2.0), and Matplotlib (Matplotlib license based on PSF). Exact dependency versions are recorded in `uv.lock` and `frontend/package-lock.json`; distribution notices must accompany a packaged deployment.
