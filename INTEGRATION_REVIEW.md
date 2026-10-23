# Integration review

Branch: `integration/baka-ui-main`.

Sources: remote main `ae44a7d7406e41324446d886c9e5618ce7ed3dfb` and baka_ui
`263f93b04aa2b1286cf2fdd5a64222dad9e806ae`. The original checkout remains on
baka_ui; the integration is isolated in a separate worktree. Neither source
branch is moved, and this integration is not pushed.

## Preserved behavior

- Both catalogs: all 20 existing customers, with C50811 first and customer IDs
  visible to distinguish duplicate profile names. Regenerating picks retains
  existing entries. No model weights or project datasets changed.
- Main's identity profile, date-of-birth age calculation, provenance and bank
  owner-name checks; mobile client-list folding, scrolling and step states.
- Main's expandable SHAP factors, feature values and percentage shares, with
  the accurate RS remittance income (SOI04) label.
- Baka_ui's all-records default, transaction filters/pagination, bank/RS/
  Creditinfo source selection, cleaning audit, /100 confidence score, unified
  processing/audit panel and exact UTC timestamps with milliseconds.
- Consistent warning labels/panels without bullet markers; consent and result
  animations. Source-discovery failure does not block consent/estimation.
- Consent resilience, configurable as-of date and a consistent evidence/model
  observation window. Missing registries yield warnings; insufficient bank
  evidence withholds the estimate and its precision score.
- Persistent hash-linked audit events, in-memory assessments and model-risk
  evaluation. No accounts, saved-case history or download route.
- Both startup paths: Makefile/Bash and the Python launcher. Health retains
  both model-window field names and the launcher's demo-version marker.

The removed human-review, manual-comparison, recording-scenario and calendar-
month receipts controls remain absent. Legacy API review/benchmark behavior
continues to be covered separately; it is not presented as a frontend feature.

## Conflict resolutions and regressions caught

Nine conflicted files were resolved together: API/service.py, API/explain.py,
README.md, frontend/src/App.jsx, frontend/src/api.js, frontend/src/app.css,
frontend/src/components/ConsentTable.jsx, frontend/src/components/Result.jsx,
and tests/test_income_service.py.

The tests caught a moved timing variable and a stale loan_request lookup that
prevented completed assessments. The merged service returns both branches'
required response fields and excludes private runtime objects. The old
no-bank-data test now verifies abstention and no fabricated confidence score.
Registry fixtures have module scope so temporary truth data cannot leak into
the existing-customer regression suite.

## Verification

```powershell
uv run python -X utf8 -m pytest --tb=short -ra --junitxml=.runtime/regression-results.xml
uv run python tools/run_demo.py --check --evaluate
cd frontend
npm run lint
npm run build
cd ..
uv run --with playwright python -X utf8 tools/check_integration_browser.py
```

- Full non-slow suite: **134 passed, 8 skipped, 2 deselected**. The eight saved-
  dataset pipeline checks require the omitted transaction dump; all 20 existing
  customer flows are tested against the actual on-demand mock APIs and model.
  The two slow tests are excluded by the existing default pytest configuration.
- Python lint, frontend lint and production build pass.
- Python launcher/model setup check passes. Actual held-out evaluation covers
  20,000 synthetic customers: MAE 54.8 GEL, interval coverage 80.51%.
- C50811 still returns P10 **1,746**, P50 **1,878**, P90 **2,104 GEL**; confidence
  **81/100**, Low; **76** service receipts totaling **21,694.69 GEL**.
- The browser regression runs real isolated services on ports 18000, 18001,
  19000 and 15173. It covers desktop/mobile layouts, profile and expanded SHAP,
  source selection/pagination, exact timestamps matched to backend records,
  evaluation display, missing-source fallback, bank-failure abstention,
  source-discovery fallback, audit verification and intentionally removed UI.
  It stops only the child services it creates; the normal demo remains running.

Browser checks require Playwright plus Edge on Windows or installed Playwright
Chromium elsewhere. In a restricted Windows runner, starting/stopping those
private-port child services needs permission. UTF-8 mode avoids an existing
Windows default-encoding failure in the synthetic-report CLI test.

JUnit results and browser service logs are under git-ignored `.runtime/`.
These checks validate the synthetic prototype; they do not establish real-world
model accuracy or production readiness.
