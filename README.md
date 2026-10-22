# Loan-portfolio EDA (pre-modeling)

One command turns the ten CSV files in `Data/` (+ the Georgian data dictionary) into a **self-contained HTML
report** that answers the questions a data scientist asks *before* modeling: what is in here, can I trust it,
what is the target, what leaks, how should I split / encode / validate, and what score is realistic.

When the generated bank dataset of `openbanking_mock/` (mock open-banking APIs of 12 Georgian banks + 50,000 synthetic
customers who are rows of `Data/`) is present, the same report also covers it, in sections 11-14: what it is, what a
lender would really see through the bank APIs, how realistic it is, and what can (and cannot) be learned from it.

```
uv sync                         # once: creates .venv from uv.lock (Python 3.12)
uv run python EDA.py            # full run  -> reports/eda_report.html   (about 5 minutes; 3.5 without the mock part)
uv run python EDA.py --fast     # 20k loans + 5,000 mock customers, about 2 minutes
uv run python EDA.py --no-mock  # sections 1-10 only (the loan data), about 3.5 minutes
```

Open `reports/eda_report.html` in a browser: it starts with **five things to know**, then the fourteen sections.
The terminal prints the same five points at the end, and `reports/summary.md` has them as plain text.

> No `uv` yet? Install it with `curl -LsSf https://astral.sh/uv/install.sh | sh`. Without uv, any Python 3.12+
> virtualenv works: `python -m venv .venv && .venv/bin/pip install pandas numpy scipy scikit-learn matplotlib openpyxl threadpoolctl`
> and then `.venv/bin/python EDA.py`.

## What you get

```
reports/
├── eda_report.html     one page: executive summary + 14 sections (charts are embedded)
├── summary.md          the executive summary as plain text
├── feature_roles.csv   hand-off table: every column's role, advice, feature sets A / A_strict / B / C
├── figures/*.png       every chart as a stand-alone image
├── tables/*.csv        every table in the report
└── mock_customer_features.csv   only with --export-mock-features (see below)
```

`reports/` is regenerated on every run (and git-ignored). `Data/` is only ever read. A re-run replaces only the
files the previous run wrote (listed in `reports/.eda_manifest.json`), never your own files in the same folder.

## The ten sections (each one answers a single question)

| # | Section | Question it answers |
|---|---------|---------------------|
| 1 | Overview & inventory | What exactly did we receive - and does it match the documentation? |
| 2 | Data-quality scorecard | Can I trust these numbers - and where are the traps? |
| 3 | Can the files be pooled? | Are the files random chunks of one population? |
| 4 | The target | What are we predicting, how rare is it, and does one cut-off explain it? |
| 5 | Feature distributions | What does each column look like, and how do events differ? |
| 6 | Features vs the target | Which columns separate events from non-events? |
| 7 | Multivariate structure | Which columns say the same thing? |
| 8 | Leakage & feature roles | Which columns must NOT be used to predict - and how do I know? |
| 9 | Baseline signal check | If I model this today, how well can I do, and what does the work? |
| 10 | Modeling blueprint | What should I build, with which columns, validated how? |
| 11 | Mock & synthetic bank data | What is the generated bank dataset, and how does it connect to the loan table? |
| 12 | Mock bank APIs: what a lender sees | What do the 12 banks' APIs return - and what is missing by design? |
| 13 | Mock data: realistic or planted? | Where does the synthetic data give itself away, and what did its generator build in? |
| 14 | Bank-data features & how to use them | What can be built from the APIs, and what may (not) be concluded from it? |

Sections 11-14 are skipped (with a one-line reason on the console) when the mock folder is absent, with `--no-mock`, or
when `--data-dir` is not the default one. Ask for them explicitly with `--sections 11,12` and a missing mock is an error.

Every number in the text is computed when the script runs (the rules of thumb it compares them with are named
constants at the top of `EDA.py`). Only the column glossary (English translation of the Georgian dictionary),
the column families and the *hypothesised* roles are hand-written, and section 8 re-tests the roles against the
data.

## What the supplied data looks like (headline findings of the last full run)

- 100,000 loans x 57 columns, 3.56% positive (`addNegStatus`), no missing values; the ten files are random
  chunks of one population (pool them), but rows inside a file are assembled in balanced 500-row batches.
- **The label tracks the arrears state.** 12 of the 14 delinquency / penalty columns reach AUC >= 0.9 alone; one
  threshold (`TotalNumberDelinquencyDays` > 1,253) already reproduces most of the label (precision 85%, recall 78%).
  The supplied score / PD is a recoding of the same state: AUC 0.93 overall, 0.59 on loans without arrears.
- **No single leak:** without delinquency columns and score a boosted-tree model still reaches AUC 0.99, and the
  22 columns "known at origination" alone reach 0.97 - far above the 0.65-0.80 of a real application scorecard.
  Four of those columns follow the loan's arrears state (`ECONOMIC_SECTORS`, `PRINCIPAL_PAYMENT_FREQUENCY`, ...).
- **The columns do not obey the dictionary** (14 of 17 relationships fail) in a way a statistical generator
  reproduces, so the data is probably synthetic or heavily perturbed: a high score here is not proof of a good model.
- Questions for the organisers (metric, what the label means, whether score / delinquency columns are allowed
  inputs, snapshot date ...) are listed at the end of section 10.

## What the mock & synthetic bank data looks like (sections 11-14)

- All 50,000 mock customers are **exact rows of `Data/`** (a random 50% subset, same label, 3.64% positive). The folder
  has two layers: `api/` (what a lender could retrieve: 38.6M transactions, accounts, balances) and `truth/` (the
  simulator's hidden state, for evaluation only). Names, ids, phone numbers and IBANs in `truth/` are never read.
- A lender would see the **loan itself for only about 30%** of the customers (15% of loans sit at a bank outside the
  twelve, and most banks' APIs list no loans); every bank exposes a different subset of fields, so missing = "not
  exposed", not "unknown". 56% of the loans started before the 12-month window, so these are monitoring features, not
  application-time features.
- **The simulated behaviour is generated from the label.** The generator's latent stress is, to R² = 1.0000,
  0.65 x rank(PD) + 0.15 x delinquency days + 0.20 x `addNegStatus`; spending, balances, lateness, micro-loans and
  gambling follow it. Bank-data AUCs (behaviour alone ~0.96) therefore measure planted signal, never whether
  open-banking data predicts default; they are kept out of the five headline points on purpose.
- Where it **is** useful: ground truth is known, so feature code can be unit-tested (a salary-based income estimate lands
  within ±10% of the truth for ~94% of salaried customers), and the 7 bank dialects, the cross-bank join and the missing-
  by-design fields can be developed against 38M rows.

## Options

| flag | meaning |
|------|---------|
| `--fast` | stratified 20,000-row sample and lighter models |
| `--target COL` | analyse another binary column (default `addNegStatus`, the dictionary's outcome flag) |
| `--binarize "<=150"` | turn the numeric `--target` into 0/1, e.g. `--target score --binarize "<=150"` |
| `--sections 1,2,8` | run only these sections (the report then contains only them; later sections may need earlier ones for context) |
| `--no-models` | skip the model-based checks (adversarial validation, score reverse-engineering, section 9) |
| `--n-jobs N` | upper bound of CPU threads for models / BLAS (default 4; a lower `OMP_NUM_THREADS` wins) |
| `--data-dir`, `--out-dir` | non-default locations |
| `--mock-dir DIR` | the generated mock dataset (`MANIFEST.json`, `api/`, `truth/`); default `openbanking_mock/output/dataset` when `--data-dir` is the default and the folder exists |
| `--no-mock` | skip sections 11-14 even if the mock folder exists |
| `--export-mock-features` | also write `reports/mock_customer_features.csv`: one row per mock customer (target, shard, 49 API features); row-level synthetic data, so do not share the folder blindly |
| `--open` | open the report in the browser when done |

Bad arguments (unknown target, `--binarize` on a text column, a threshold that leaves one class ...) stop the
script with a readable message *before* anything in the output folder is touched. If a part of the analysis
crashes, the report is still written, the failure is listed under "needs your attention", and the exit code is 1.

Shortcuts: `make setup`, `make eda`, `make eda-fast`, `make notebook`, `make test`, `make test-all`, `make lint`,
`make format`, `make clean` (`make help` lists them).

## Notebook edition

`EDA.ipynb` is the same analysis as a Jupyter notebook: the code of `EDA.py` cut into cells, one heading + code cell +
`run(n)` cell per section, and each section's text, tables and charts shown right under its cell. Everything stays in
memory afterwards (`ctx`, `rep`), so it is the easy way to explore further or to re-run a single section.

- It is **generated** from `EDA.py` by `tools/make_notebook.py` (standard library only), so `EDA.py` stays the source of
  truth (tests, `make eda`). After changing `EDA.py` run `make notebook`; `make notebook-check` fails when the notebook is
  out of date. The generator refuses to overwrite a notebook whose cells you edited (`--force` overrides, `--out copy.ipynb`
  writes a copy), and it fails loudly if `EDA.py`'s layout (banners, `main()`, `@register`) stops matching what it expects.
- Open it from the project folder with the project's `.venv` as the kernel (`ipykernel` is in the `dev` group; VS Code:
  *Select Kernel -> Python Environments -> .venv*; JupyterLab: `uv run --with jupyterlab jupyter lab`).
- The first code cell holds the parameters (`FAST`, `NO_MOCK`, `ONLY_SECTIONS`, `TARGET`, `OUT_DIR` ...), the equivalents of
  the flags above. The notebook writes to `reports/notebook/` by default, never over the `reports/` files of `python EDA.py`.
- The notebook is saved without outputs; a full run takes about 5 minutes, `FAST = True` about 2.

## Using the results for modeling

```python
import pandas as pd

roles = pd.read_csv("reports/feature_roles.csv", index_col="feature")
set_a = roles.index[roles["in_A_application"]]        # known at origination (according to the dictionary)
set_a2 = roles.index[roles["in_A_strict"]]            # A without the columns that follow the loan's arrears state
set_b = roles.index[roles["in_B_monitoring"]]         # + balances / schedule / loan events (no delinquency columns)
set_c = roles.index[roles["in_C_status_detection"]]   # + delinquency columns (reconstructs the label!)
print(roles[["role", "recommended_use", "single_column_AUC", "tracks_loan_state", "notes"]])
```

Section 10 explains which framing (application scorecard, monitoring, status detection, beating the supplied score,
reconstructing the label) each set supports, with an honest AUC for the exact columns and loans of each one, a
preprocessing recipe, a validation plan and starter code.

## Synthetic loan generator (`synthgen.py`)

`openbanking_mock/` can only simulate bank data for customers that are *rows of `Data/`* (a bootstrap: at most 100,000
customers, every one a copy). `synthgen.py` writes **new** loans with the exact schema of `Data/df_*.csv` (58 columns, same
dtypes, same event rate), so they can replace `Data/` anywhere - the EDA, a model, or `openbanking_mock --data-dir`.

```
uv run python synthgen.py                         # 100,000 new loans -> synthetic/loans/df_1..10.csv + FIDELITY_REPORT.md (~45 s)
uv run python synthgen.py generate --rows 1000000 --seed 7 --no-check   # 1M loans in ~1 min, then `check` them
uv run python synthgen.py check --full            # gates (+ privacy, train-synthetic/test-real) on an existing folder
uv run python synthgen.py bank --customers 2000   # openbanking_mock validate -> generate -> report on those loans (~1 min)
```

**How the previous generation's rules are kept.** The Georgian-market rules (BoG 46% + TBC 32% = 78% of customers, only
what the 12 banks' APIs can return, ISO 13616 / 9362 / 4217 / 20022 and Berlin Group standards, NBG FX rates, business-day
booking) live in `openbanking_mock`, which is **not touched**: `bank` just points its `--data-dir` at the new loans. Last run
(2,000 customers): its ledger validation passes, 52 of 52 quality checks pass, BoG + TBC = 78.00%. The loan table's own
rules are reproduced from the real data and *checked*, not assumed:

| what | how | last full run (100,000 rows vs `Data/`) |
|---|---|---|
| schema, dtypes, supports, exact event rate | cells of (delinquency regime x class x mortgage) with a Gaussian copula and empirical marginals | identical header / dtypes, 3.56% events, KS <= 0.003, code shares TV <= 0.006 |
| spikes, caps, floors (zero inflation, income 300-10,000, PD floor 0.24) | empirical marginals, constants inside a cell | dominant-value share off by <= 0.003 |
| rank correlation, code-to-code and code-to-quantity association | moment-matched copula; codes borrowed jointly from nearby real rows | mean abs gap 0.006 (max 0.056); Cramer's V 0.005 (max 0.029); eta 0.017 |
| "run-off" loans: balance / schedule columns that are 0 *together* (EDA 9c) | their zero pattern is borrowed jointly with the codes; non-zero values keep the copula's order | event rate by number of zeros within 1.7 pp (1.2% -> 67% at five zeros; AUC of the count 0.813 vs 0.812) |
| the 16 dictionary rules the data breaks (14 of 17 fail) | **not enforced**; three relations a copula cannot express are re-paired to their real rate | every violation share within 1.5 pp of `Data/` |
| the label's signal | per-class cells | single-column AUC within 0.016; train-on-synthetic / test-on-real AUC 0.970 (real/real 0.994) |
| "too evenly mixed" 500-row blocks (EDA section 3) | stratified draws on the leading factors per batch | block variance ratio 0.48 (real 0.46; it moves by +-0.05 with the seed) |
| new, not copied rows | no row is copied | 0 exact copies; closest-real-row distance 1.08 x the real-to-real one (measured on the quantity columns: the codes of a row are borrowed from a real row by design) |

Things to know:
- The label-driven behaviour of the supplied data is reproduced on purpose (that *is* its rule), so the bank data built from
  these loans keeps `openbanking_mock`'s label-driven "stress"; AUCs computed on it are still not evidence about real open
  banking. Decoupling them would change the mock's rules and is deliberately not done here.
- `bank` makes one customer per loan row (`--customers` <= rows), writes to `synthetic/bank_dataset` (never inside
  `openbanking_mock/`) and refuses a non-empty folder it did not write.
- Same data + seed => identical bytes. Output folders are replaced only if `synthgen` wrote them; `Data/` is read-only.
- Scale: fitting takes about 25 s; 100,000 loans take about 30 s, 1,000,000 about 1 minute and 3 GB of memory
  (418 MB of CSV in 100 files; use `--no-check` or `check` on the result, the gates read everything into memory).
  `bank --customers 50000` mirrors the previous generation's scale (`openbanking_mock`'s own estimate: about 10 minutes on
  16 cores and 2 GB; not run here - the largest bank run was 2,000 customers).
- Checked with the project's own EDA (`EDA.py --data-dir synthetic/loans --no-mock`, full run vs the full run on `Data/`): 3,558
  positives, 12 of 14 arrears columns at AUC >= 0.9, the one-cut-off rule (1,246 vs 1,253 days, 86% vs 85% precision, 78%
  recall), 14 of 17 dictionary relations failing, the run-off curve (1.2% -> 67% vs 68%) and the "templated" delinquency
  values all reappear.
- **Known residual gaps.** The origination-time codes follow the arrears state about 70% as strongly as in `Data/` (sector 0
  = "no business" in 26% of the extreme-arrears non-events vs 37%; the EDA's state-tracking check flags 2 columns where it
  flags 4 on `Data/`), the no-model marker count scores AUC 0.89 vs 0.93, the application-time AUC is 0.957 vs 0.969, and a
  few rank correlations differ by up to 0.056 (instalment count with other columns, after its atom was restored). A model
  trained on the synthetic loans therefore reaches slightly lower AUCs than one trained on `Data/` (0.970 vs 0.994).
- Not modelled: anything outside the 58 columns (no dates, no ids), and relationships the EDA could not see.

## Project layout

```
synthgen.py       synthetic loan generator: new loans with the schema of Data/, its gates, hand-over to openbanking_mock
synthetic/        generated by synthgen.py (git-ignored): loans/ (df_*.csv + FIDELITY_REPORT.md), bank_dataset/
EDA.py            the whole analysis (one file; search for the banners listed in its docstring)
EDA.ipynb         the same analysis as a notebook, generated from EDA.py (make notebook)
income/           one row per person for income estimation (built from Data/ + the open-banking APIs)
                  build_income_dataset.py, income_dataset.csv, income_eda.ipynb
tools/            make_notebook.py: EDA.py -> EDA.ipynb
tests/            unit tests for the statistics + end-to-end runs on a synthetic dataset (uv run pytest)
Data/             raw input - never modified
openbanking_mock/ the mock bank APIs + generator (a separate tool); only its generated dataset is read, read-only
reports/          generated output
pyproject.toml    dependencies + ruff / pytest settings (managed with uv)
uv.lock           exact versions, so everyone gets the same environment
.python-version   3.12
Makefile          shortcuts
```

Dependencies used by the EDA: pandas, numpy, scipy, scikit-learn, matplotlib, openpyxl, threadpoolctl, and pyarrow
(only to read the mock dataset's Parquet files; it is also needed by `openbanking_mock/`).
Add one with `uv add <package>`; run tooling with `uv run ruff check .` and `uv run pytest`.

## Notes

- The repository is https://github.com/konstantine25b/IEM_NBG_OpenBanking (public). `Data/`, `synthetic/`, `reports/` and
  `openbanking_mock/output/` are committed on purpose; `.gitignore` only keeps `.venv/` and tool caches out.
- Resources: one process. Sections 1-10 alone (`--no-mock`) peak at about 0.7 GB and take about 3.5 minutes on 4 threads
  (`--fast`: 0.4 GB, 1.5 minutes). With the mock part the peak is about 1.2 GB (one shard of the 2 GB Parquet dataset is in
  memory at a time, only per-customer rows are kept) and the full run takes about 5 minutes (`--fast`: about 1 GB, 2 minutes);
  up to 6 when the machine is busy.
- Chart colours follow one code: **blue = target 0, orange = target 1** (the event rate is drawn in orange),
  every column role has its own colour and is also written next to the label, and grey is everything else.
- Sections 11-14 only **read** `openbanking_mock/output/dataset` (Parquet shards, `MANIFEST.json`, `quality.json`): nothing
  is written there and the mock's code is not imported. From `truth/customers` only a whitelist of columns is read (no
  names, person ids, phone numbers, birth dates), the counterparty fields of the transactions are only counted (never kept
  or shown), and the report shows aggregates only; `tbc_api_examples.py` is not used.
  The older `output/dataset_v1_legacy`, the raw `captures/` and `fixtures/` are not analysed.
