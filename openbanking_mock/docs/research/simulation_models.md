# Simulating realistic Georgian retail-banking data for Open-Banking mocks: models, tools, recipes, inputs

Research date: 2026-10-20. Scope: mock Berlin-Group-style (NextGenPSD2) APIs for 12 Georgian banks, data to be reused for credit-scoring / PD / income-estimation ML.

**Verification legend** (used on every factual claim that could be wrong):

| Flag | Meaning |
|---|---|
| **[P]** | Primary source opened in this session (URL given) |
| **[L]** | Verified locally by running code or inspecting a downloaded file (PyPI/GitHub API metadata, pip-installed libs, the Georgian XS2A PDF, the Berlin Group YAML) |
| **[S]** | Secondary only: news article, vendor docs, travel blog, or a search-result summary. Treat as a pointer, not a fact |
| **[U]** | Unverified / my own prior / illustrative assumption. Must be calibrated or confirmed before use |

"Last activity" for GitHub repos = `pushed_at`; for PyPI = upload date of latest release, both fetched 2026-10-20.

---

## (a) Executive summary and ranked recommendation

**Bottom line.** No existing open-source simulator fits this job. PaySim, BankSim, Sparkov, AMLSim and AMLworld model mobile money, US credit cards or money laundering; none emits Berlin-Group objects, GEL, Georgian merchants, remittances, loans with instalment schedules, or delinquency. Generic synthetic-data libraries (SDV, CTGAN, etc.) can sample the *static* registry columns but cannot, on their own, keep balance reconciliation, salary-to-income consistency, or instalment-to-debit consistency. The team already owns the scarcest ingredient (a real loan-level registry with ~59 columns), so the right design is: **calibrate static attributes from the registry, then derive everything else mechanistically**, with output shaped by the official Georgian Open Banking spec.

**Ranked recommendation (what to do, in order):**

1. **Use the official spec as the output contract, not a library.** The Georgian standard is public: *XS2A Standard Implementation Guide 0.8.RC3* (Georgian-language PDF, released 2022-09-12, "based on" Berlin Group NextGenPSD2) at `openfinance.ge/en/standards` [P], and I downloaded and read it [L]. Take JSON schemas from the Berlin Group OpenAPI YAML v1.3.11 (licence **CC BY 4.0**, header of the file) [L], mirrored in the adorsys repo. Validate every mock response with `openapi-core` / `schemathesis`. Key mandatory behaviours are listed in section (d.1). **Gap:** the 0.8.RC3 guide contains no loan or deposit data model (grep for loan/deposit/სესხ/დეპოზიტ returned zero hits [L]); NBG says "loan information sharing" and "deposit information sharing" APIs were added in Feb 2025 [P], and openfinance.ge points to a SharePoint folder for the updated standard that returned HTTP 403 (login required) when I tried it [L]. Ask NBG (`ob@nbg.gov.ge`, per the NBG page [P]) or the team's bank contacts for it before designing the loan/deposit endpoints.
2. **Build the population layer yourselves (50 lines of numpy/scipy), do not use a black-box GAN.** Smoothed bootstrap plus a Gaussian or vine copula over *primitive* columns only (income, remittance income, age, sex, region, legal form, disbursed amount, rate, term, frequency, score). **Derive** instalment, PTI, LTV, outstanding principal, overdue amounts deterministically, then compare to the real columns. Libraries: `numpy`, `scipy`, `statsmodels` (BSD-3, has `GaussianCopula`/`StudentTCopula`) [L], or `pyvinecopulib` (MIT; supports discrete variable types, `var_types` attribute present) [L].
3. **Build the transaction and delinquency engine from scratch (it is the part nothing reusable covers).** Scenario-first design: decide each loan's delinquency path (roll-rate / mover-stayer Markov chain, Vasicek common factor), *then* plan the cash flows so the missed instalment debit actually fails for lack of funds. Recipes in section (c).
4. **Reuse small, permissively licensed plumbing:** `faker` (ka_GE locale for names, cities, jobs; MIT) with custom providers for everything it gets wrong (IBAN, ID, phone, postcode, merchants), `holidays` (has `GE`, MIT) [L], `python-stdnum` + `schwifty` for IBAN checks [L], `sdmetrics` (MIT) and `pandera` (MIT) for validation, `lifelines` (MIT) for hazard fitting.
5. **Treat public datasets as structure/sanity references, never as Georgian data.** Berka/PKDD'99 (real, anonymized Czech bank: accounts, transactions, loans with A/B/C/D status) is the closest public template for loan-to-transaction linkage [P]. AMLworld (data only, CDLA-Sharing-1.0; **generator code not released**) and TabFormer (24M synthetic US card transactions) show plausible flow structure.
6. **SDV is optional and has a licence catch.** SDV, CTGAN, Copulas, RDT, SDGym are **BUSL-1.1** (not MIT; the `copulas` library changed licence) [L]. Additional Use Grant allows use except as a "Synthetic Data Service" (a commercial offering letting third parties use SDV's features) [P, raw LICENSE]. Generating data offline and shipping static files is fine; exposing "generate N synthetic customers" via SDV on a public endpoint is the risky pattern. Not legal advice. **SDV 2.0.0 was released 2026-10-15** with removed deprecated APIs and a new Metadata V2 format [L, HISTORY.md]; if used, pin `sdv<2`.

### a.1 Do-not-use list and risks

**Do not use** (reasons verified): PaySim (GPL-3.0, Java, mobile money, last push 2021-05-30); Sparkov (US cards, last push 2022-06-22); `ydata-synthetic` 2.0.1 (PyPI says Python `<3.12,>=3.9`; incompatible with 3.12+); `gretel-synthetics` (GitHub repo archived, last push 2025-06-24); TabDDPM and TabSyn (research code, no push since 2024-07); CTAB-GAN+ (no licence on GitHub); PyPI `hawkes` (no licence, 2021); `scikit-survival` (GPL-3.0-or-later); Mimesis (no Georgian locale: 48 locales, none `ka`/`ge` [L]).

**Top risks and what to do first (first 48 hours):**
- *Diagnose the registry before modelling.* Are the 10 files independent samples or snapshots sharing a loan key? (Decides whether roll rates can be estimated empirically or only moment-matched.) Is "total income" monthly, gross or net? Is PTI = instalment / income exactly? What horizon is `probabilityOfDefault`? What is the snapshot date?
- *Privacy.* Bootstrapping real credit-registry rows can leak. Use smoothing/jitter plus a distance-to-closest-record check (section e).
- *Spec drift.* The team's mocks target Berlin Group; Georgia's profile differs from the base standard (e.g. `valueDate` mandatory, Georgian default language, mandatory balance types) and vendors (Paysera) publish their own "unsupported" lists that are *vendor deviations, not the standard* [P].
- *Calibration inputs that I could not verify* are listed at the end of section (d).

**Architecture (layers):**

```
L0 CONTRACT   Berlin Group YAML 1.3.11 (CC BY 4.0) + Georgian XS2A 0.8.RC3  -> response models, validators
L1 POPULATION registry bootstrap + copula on primitives -> persona + loan terms; derived PTI/LTV/instalment
L2 INCOME     salary (gross->net), pension, remittance process, other credits, per customer
L3 SPEND      inhomogeneous Poisson/Hawkes per category x payday x weekday x holiday; ATM cash wallet; FX
L4 CREDIT     amortisation schedule, back-cast loan age, delinquency scenario (roll-rate/mover-stayer + Vasicek Y_t)
L5 LEDGER     daily balance engine: booked/pending, balance types, declines on insufficient funds, instalment debits
L6 VIEWS      accounts, balances, transactions, card accounts/cards, loans, deposits -> API JSON
L7 VALIDATION identities, distributions, consistency, privacy (section e)
```

**Build vs reuse, with packages (all pip-installable; versions as found on PyPI 2026-10-20):**

| Layer | Build or reuse | Packages (licence) |
|---|---|---|
| L0 Contract | **Reuse** Berlin Group YAML 1.3.11 (CC BY 4.0) + Georgian guide as the written checklist; generate pydantic/typed models or validate directly against the YAML | `openapi-core` (BSD-3), `schemathesis` (MIT) |
| L1 Population | **Build** (about 50 lines): smoothed bootstrap + Gaussian/t/vine copula on primitives, derive ratios | `numpy`, `scipy`, `pandas`, `statsmodels` (BSD-3), optionally `pyvinecopulib` (MIT); SDV GaussianCopula only as an optional comparison (BUSL-1.1, pin `sdv<2`) |
| L2 Income | **Build** | `numpy`, `holidays` (MIT, has `GE`) |
| L3 Spend | **Build**; optional Hawkes via 20-line thinning | `numpy`; `tick` (BSD-3) only if you want its estimators |
| L4 Credit | **Build** (annuity formula by hand; `numpy-financial` 1.1.0 needs Python >=3.13); hazard fitting | `numpy`, `scipy`, `lifelines` (MIT), `optbinning` (Apache-2.0) for score-band checks |
| L5 Ledger | **Build** (integer tetri, daily loop); per-customer RNG streams | `numpy` (`default_rng`), stdlib `decimal` |
| L6 Views | **Build** thin renderers per bank; give each of the 12 banks a **capability profile** (which balance types, optional fields such as `debtorName`/remittance fields, card endpoints, pending support), because real banks expose different subsets, as Paysera's published "unsupported" list shows [P: https://developers.paysera.com/guides/open-banking-georgia] | `faker` (MIT, `ka_GE` for names/jobs; custom providers for IBAN/phone/postcode/merchants), `python-stdnum` (LGPL), `schwifty` (MIT) |
| L7 Validation | **Reuse** libraries, **build** the project-specific tests in section (e) | `pandera` (MIT), `sdmetrics` (MIT), `openapi-core`, `schemathesis`, pytest |

```
pip install numpy scipy pandas statsmodels pyvinecopulib faker holidays python-stdnum schwifty lifelines optbinning pandera sdmetrics openapi-core schemathesis pytest
```
(All names above exist on PyPI per the metadata queries; I installed and exercised `faker`, `mimesis`, `schwifty`, `python-stdnum`, `holidays`, `numpy`, `scipy`, `statsmodels`, `pyvinecopulib` locally; the rest are verified only via PyPI metadata.)

---

## (b) Comparison table of tools, datasets and libraries

Fit scale: **High** = reuse directly; **Med** = reuse a component or borrow design; **Low** = not suited, read for ideas only.

### b.1 Existing simulators, synthetic datasets and open-banking sandboxes

| Name | What | Licence / last activity | Install | Fit for us | URL |
|---|---|---|---|---|---|
| **PaySim** | Java multi-agent simulator of mobile-money transfers (cash-in/out, transfer, payment), calibrated on one month of logs from an African mobile-money service; built for fraud research | GPL-3.0; last push 2021-05-30 [L]; 56 commits [P] | Maven/Java; no pip | **Low.** Wrong product, GPL, stale | https://github.com/EdgarLopezPhD/PaySim ; EMSS 2016 paper https://msc-les.org/proceedings/emss/emss2016/emss2016_249.html |
| **BankSim** | Agent-based bank-payments simulator calibrated on aggregated data from a Spanish bank (2014 paper); dataset reportedly 594,643 records / 180 steps [S] | Repo location not found (the URL I guessed returned 404 [L]); dataset licence unverified [U] | Kaggle download (existence per secondary sources [S]) | **Low.** Payments between customers and merchant categories only | Paper: https://ddd.uab.cat/pub/poncom/2014/128517/ssc14_a2014a86iENG.pdf ; https://msc-les.org/proceedings/emss/emss2014/emss2014_144.html |
| **AMLSim (IBM)** | Java (MASON) + Python multi-agent simulator generating bank transaction graphs with injected money-laundering patterns | Apache-2.0; last push 2025-09-17 [L] | Clone; Java 8 + Python 3.7 scripts per README [P] | **Low-Med.** Graph/account structure ideas; no salaries/loans/GEL | https://github.com/IBM/AMLSim |
| **IBM AMLworld / AML-Data** | Six public synthetic datasets (HI/LI x Small/Medium/Large; large = 175-180M transactions) from a circular-flow agent world of persons, companies, banks; salary/pension amounts drawn from **US IRS** 2018 data; multi-currency | Repo Apache-2.0; **data CDLA-Sharing-1.0**; last push 2025-09-17 [P][L]. Paper: "what we have open-sourced is a set of data - not the code to generate the data" [P, arXiv Sect. 7] | Kaggle download | **Low** as data (US/AML), **Med** as conceptual model (salaries weekly/biweekly/monthly, interest monthly, per-entity multiple accounts) | https://github.com/IBM/AML-Data ; https://arxiv.org/abs/2306.16424 |
| **IBM TabFormer card data** | 24M synthetic US credit-card transactions, 12 fields; paper uses it for GPT/BERT-style tabular sequence models | Code Apache-2.0; last push 2025-09-17 [L]; data licence not stated on README [P] | Git LFS or box.com link [P] | **Low-Med.** US merchants; MCC-sequence structure only. Dataset columns not enumerated on README (I recall an MCC column: [U]) | https://github.com/IBM/TabFormer ; https://arxiv.org/abs/2011.01843 |
| **Sparkov** | Python card-transaction generator: customer profiles (JSON), per-profile spending windows by category, merchants, travel radius, fraud | MIT; last push 2022-06-22 [L] | Clone; `datagen.py` [P] | **Med** as a code *template* for profile-driven category spending; US demographics, no accounts/loans | https://github.com/namebrandon/Sparkov_Data_Generation |
| **Berka / PKDD'99 Financial** | *Real* anonymized Czech bank data: 8 tables, 1,090,086 rows, 682 loans (606 good / 76 bad; A,C good; B,D bad; A,B finished; C,D running) | Licence/citation terms not stated on page [P] | Public MariaDB (host `relational.fel.cvut.cz`, user `guest`) [P] | **Med-High as a reference** for how loan status relates to account transactions and for validating our loan-to-cashflow consistency tests; Czech, 1990s | https://relational.fel.cvut.cz/dataset/Financial |
| **Santander Product Recommendation (Kaggle)** | Real bank customer-product holdings, ~13.6M monthly rows, 24 products [S] | Kaggle competition terms [U] | Kaggle | **Low.** Product ownership, no transactions | (via search) https://github.com/rkcosmos/Santander-Product-Recommendation |
| **Open Bank Project (OBP-API)** | Scala open-banking API platform (Berlin Group, UK, etc.) with sandbox data import; NBG ran its 2023 technical sandbox on TESOBE's OBP platform [S via search of NBG news] | **AGPL-3.0** (network-use clause); last push 2026-10-20 [L] | Docker/sbt | **Med** as reference for how a bank sandbox is seeded; AGPL if code reused | https://github.com/OpenBankProject/OBP-API ; https://nbg.gov.ge/en/media/news/nbg-partnered-with-ifc-invites-interested-parties-to-test-new-open-finance-services |
| **adorsys XS2A + XS2A-Sandbox** | Java NextGenPSD2 implementation and a "PSD2-compatible banking system emulator" | **AGPL-3.0**; last push 2026-07-20 / 2026-07-17 [L] | Docker/Maven | **Med** as a response-shape cross-check; also hosts the Berlin Group YAML file at `xs2a-impl/src/main/resources/static/psd2-api_v1.3.11-2021-10-01v1.yaml` [L] | https://github.com/adorsys/xs2a ; https://github.com/adorsys/XS2A-Sandbox |
| **Berlin Group NextGenPSD2 OpenAPI v1.3.11** | The JSON schema source: `balanceType` enum, `transactionDetails`, `cardTransaction` (incl. `merchantCategoryCode`, `cardAcceptorId`, `terminalId`), `cardAccountDetails` (incl. `creditLimit`) | **CC BY 4.0** (in the YAML header) [L] | Download YAML | **High** (schema source) | https://raw.githubusercontent.com/adorsys/xs2a/develop/xs2a-impl/src/main/resources/static/psd2-api_v1.3.11-2021-10-01v1.yaml |
| **Georgian XS2A Implementation Guide 0.8.RC3** | Georgia's national profile of NextGenPSD2 (Georgian text, 100 pages): mandatory balance types, `entryReference`, `referenceDate`/`lastChangeDateTime`, card-account and card services, 140-char remittance rules | Licence unstated [U]; published by Georgian Banks Association site | PDF | **High** (normative for our mocks) | https://openfinance.ge/en/standards -> https://cdn.association.ge/static/openbanking/standards/pdf/NextGenPSD2_XS2A_Implementation_Guide_0.8.RC3_hL6sJdA.pdf |
| **Berlin Group openFinance framework** | Planned extensions for savings, loans, securities accounts; OpenAPI on GitLab per search [S]; downloads under CC BY-ND 4.0 [S] | The `/openfinance-downloads` URL returned 404 when I opened it [L]; data model unverified [U] | n/a | **Unknown.** Check for a loans/savings data model | https://www.berlin-group.org/ (search lead: https://berlin-group.org/single-post/press-release-berlin-group-starts-new-openfinance-api-framework) |
| **UK OBIE Read/Write API specs** | UK Open Banking schemas | No licence reported by GitHub API [L]; last push 2026-10-20 | Clone | **Low** (UK shapes); field reference only | https://github.com/OpenBankingUK/read-write-api-specs |
| **Plaid Sandbox custom users** | SaaS sandbox where a JSON "configuration object" seeds accounts and transactions for a `user_custom` test user | Proprietary SaaS [S] | API key | **Low** (US schema; shows the seeding pattern only) | https://plaid.com/docs/sandbox/user-custom/ |
| **TrueLayer Mock Bank** | SaaS mock bank: current, savings, card transactions, pending, standing orders, direct debits | Proprietary SaaS [S] | API key | **Low** (same) | https://docs.truelayer.com/docs/quickstart-retrieve-bank-data |
| **Tink sandbox/test data** | not verified: searches returned third-party wrappers only [U] | n/a | n/a | n/a | n/a |
| **Mockaroo** | Web/API row generator with typed fields (IBAN listed as a type [S]); free tier ~1,000 rows/file, 200 API requests/day [S] | Proprietary SaaS | HTTP API | **Low.** No temporal coherence, no loans | https://mockaroo.com/docs |

### b.2 Statistical / ML generators (tabular and sequence)

| Name | What | Licence / last activity | Install | Fit for us | URL |
|---|---|---|---|---|---|
| **SDV** (v2.0.0, 2026-10-15) | Single/multi-table/sequence synthesizers; metadata, constraints; community synthesizers: GaussianCopula, CTGAN, TVAE, CopulaGAN (experimental); XGC/Bootstrap/Segment/DayZ/DP variants are paid/enterprise [P docs] | **BUSL-1.1** (Additional Use Grant bars "Synthetic Data Service"; Change License MIT four years after each release) [P LICENSE]; repo pushed 2026-10-19 [L] | `pip install sdv` (Python `<3.15,>=3.9` [L]) | **Med.** Good for static registry columns with GaussianCopula; weak on PTI-type identities unless you derive them; pin `sdv<2` | https://github.com/sdv-dev/SDV ; https://docs.sdv.dev/sdv/single-table-data/modeling/synthesizers |
| **SDV PARSynthesizer** | Probabilistic Auto-Regressive model for multi-sequence data with per-sequence context columns; default epochs 128 [P docs]; paper "Sequential Models in the Synthetic Data Vault" arXiv 2207.14406 (2022) [L] | BUSL-1.1 (as SDV) | via `sdv` | **Low-Med.** Can learn amount/category sequences; cannot enforce balance reconciliation, calendar structure or instalment debits | https://docs.sdv.dev/sdv/sequential-data/modeling/parsynthesizer ; https://arxiv.org/abs/2207.14406 |
| **CTGAN / TVAE** | Conditional tabular GAN (mode-specific normalisation + conditional vector for imbalanced categoricals), paper arXiv 1907.00503 (Xu et al., 2019) [L]; ctgan 0.12.1 (2026-02-13) | BUSL-1.1 [L] | `pip install ctgan` | **Low-Med.** Slow on CPU, can miss rare categories and tail correlations; a copula usually wins at this scale | https://github.com/sdv-dev/CTGAN ; https://arxiv.org/abs/1907.00503 |
| **Copulas (library)** | Multivariate copula modelling (Gaussian, vine, etc.); copulas 0.14.1 (2026-02-05) | **BUSL-1.1** on PyPI [L] (do not assume MIT) | `pip install copulas` | **Med** but prefer MIT alternatives below | https://github.com/sdv-dev/Copulas |
| **statsmodels copulas** | `GaussianCopula`, `StudentTCopula` etc. in `statsmodels.distributions.copula.api` (statsmodels 0.15.0) | BSD-3-Clause [L]; pushed 2026-10-20 | `pip install statsmodels` | **High** for a hand-built Gaussian/t copula over registry primitives | https://github.com/statsmodels/statsmodels |
| **pyvinecopulib** | Vine copulas, pair-copula selection; `var_types` supports discrete variables (attribute present [L]; accuracy for mixed data not tested [U]) | MIT; v1.0.1 (2026-10-06) [L] | `pip install pyvinecopulib` | **High** if tail dependence or mixed discrete/continuous matters | https://pypi.org/project/pyvinecopulib/ |
| **SDMetrics** | Quality/diagnostic reports and metrics for synthetic data (0.32.0) | MIT [L]; pushed 2026-10-12 | `pip install sdmetrics` | **High** for evaluation. Privacy-metric availability not checked [U] | https://github.com/sdv-dev/SDMetrics |
| **synthpop (R)** | CART/parametric sequential-regression synthesis (Nowok, Raab, Dibben, JSS 2016) [L Crossref]; CRAN synthpop 1.9-3, GPL-2 or GPL-3, published 2026-09-10 [L crandb] | GPL-2/3 | `install.packages("synthpop")` | **Med** as a method (sequential CART preserves conditional structure); R only | https://doi.org/10.18637/jss.v074.i11 |
| **DoppelGANger** | GAN for networked time series with metadata, arXiv 1909.13403 (Lin et al.) [L]; reference-implementation repo not opened (unverified) | n/a | n/a | **Low.** Daily-aggregate series could be learned but no ledger constraints | https://arxiv.org/abs/1909.13403 |
| **ydata-synthetic** 2.0.1 | GAN-based tabular and time-series synthesizers (incl. DoppelGANger-style) | MIT; pushed 2026-09-03; **PyPI `requires_python <3.12,>=3.9`** [L] | `pip install ydata-synthetic` | **Low** (Python-version blocker) | https://github.com/ydataai/ydata-synthetic |
| **gretel-synthetics** | LSTM/DGAN synthesizers | **Archived** repo, last push 2025-06-24; licence NOASSERTION [L] | n/a | **Avoid** | https://github.com/gretelai/gretel-synthetics |
| **MOSTLY AI SDK** (6.2.0) | Synthetic data SDK; `mostlyai-mock` for LLM-based mock data | Apache-2.0 [L]; pushed 2026-10-12 | `pip install mostlyai` (Python `<3.14,>=3.11`) | **Low-Med** (not evaluated for ledger constraints) | https://github.com/mostly-ai/mostlyai |
| **synthcity** (0.2.12) | Tabular synthetic data benchmark/library with privacy/fairness metrics | Apache-2.0; PyPI upload 2025-05-08; repo pushed 2026-04-21 [L] | `pip install synthcity` | **Low-Med** (useful as a baseline zoo) | https://github.com/vanderschaarlab/synthcity |
| **REaLTabFormer** (0.2.4) | Transformer for tabular and relational (parent-child) data | MIT; PyPI 2026-01-04 [L] | `pip install realtabformer` | **Low-Med** (parent/child customer-transaction, no hard constraints) | https://github.com/worldbank/REaLTabFormer |
| **TabDDPM** | Tabular diffusion, ICML 2023, arXiv 2209.15421 [L] | MIT; last push 2024-07-13 [L] | Clone | **Low** (research code, stale) | https://github.com/yandex-research/tab-ddpm |
| **TabSyn** | Latent score-based diffusion for mixed-type tables, arXiv 2310.09656 [L] | Apache-2.0; last push 2024-07-15 [L] | Clone | **Low** (stale) | https://github.com/amazon-science/tabsyn |
| **CTAB-GAN+** | GAN for tabular data | **No licence** on GitHub [L]; push 2024-05-14 | Clone | **Avoid** (licence) | https://github.com/Team-TUD/CTAB-GAN-Plus |
| **Faker** (40.43.0, 2026-10-09) | Fake-data providers; `ka_GE` locale exists (see d.3 for exact coverage) | MIT [L] | `pip install faker` (Python >=3.10) | **Med-High** for names/cities/jobs only | https://github.com/joke2k/faker |
| **Mimesis** (22.2.0) | Fake-data providers, 48 locales, **no Georgian** [L] | MIT [L] | `pip install mimesis` | **Low** | https://github.com/lk-geimfari/mimesis |

### b.3 Libraries for the mechanistic layer and validation

| Name | What | Licence / last activity | Install | Fit | URL |
|---|---|---|---|---|---|
| **tick** (0.8.0.2) | Hawkes simulation/estimation among other things | BSD-3-Clause; pushed 2026-06-15 [L]; needs Python >=3.11 | `pip install tick` (C++ build) | **Med.** Hawkes thinning is ~20 lines of numpy anyway (I tested one, branching ratio 0.5 gave 1,245 events vs 1,200 expected [L]) | https://github.com/X-DataInitiative/tick |
| **stmorse/hawkes** | Multivariate Hawkes simulate/fit class | MIT; pushed 2025-08-27 [L] | Clone | **Low-Med** (reference) | https://github.com/stmorse/hawkes |
| **holidays** (0.107) | Public holidays; **`GE` supported** (2026 list printed locally, below) [L] | MIT; PyPI 2026-10-19 | `pip install holidays` | **High**, but community-maintained: confirm against Georgian law | https://github.com/vacanza/holidays |
| **lifelines** (0.30.3) | Survival/hazard models (Cox, discrete-time via logistic) | MIT [L] | `pip install lifelines` | **Med-High** for delinquency hazard fitting | https://github.com/CamDavidsonPilon/lifelines |
| **scikit-survival** (0.28.0) | Survival ML | **GPL-3.0-or-later** [L] | pip | **Avoid** unless GPL is acceptable | https://pypi.org/project/scikit-survival/ |
| **optbinning** (1.0.0) | Optimal binning, scorecards | Apache-2.0 [L] | `pip install optbinning` | **Med** (score->PD binning checks) | https://github.com/guillermo-navas-palencia/optbinning |
| **scorecardpy** (0.1.9.7, 2023-07-29) | Scorecard helper | No licence metadata on PyPI, stale [L] | pip | **Avoid** | https://pypi.org/project/scorecardpy/ |
| **numpy-financial** | `pmt`, `ipmt`, `ppmt` | BSD-3; **v1.1.0 (2026-09-15) requires Python >=3.13**; v1.0.0 is from 2019 [L] | pip | **Low.** Hand-write the 3-line annuity formula instead | https://pypi.org/project/numpy-financial/ |
| **Mesa** (3.5.1) | Agent-based modelling framework | Apache-2.0; needs Python >=3.12 [L] | pip | **Low** (a plain loop over customers is simpler and faster for this) | https://github.com/mesa/mesa |
| **python-stdnum** (2.2), **schwifty** (2026.10.0) | IBAN validation/generation | LGPL; MIT [L] | pip | **High** (IBAN checks; see d.2) | https://arthurdejong.org/python-stdnum/ ; https://github.com/mdomke/schwifty |
| **pandera** (0.34.1), **openapi-core** (0.23.1), **schemathesis** (4.32.2) | DataFrame schema checks; OpenAPI response validation; property-based API tests | MIT; BSD-3; MIT [L] | pip | **High** for validation harness | https://github.com/pandera-dev/pandera ; https://github.com/python-openapi/openapi-core ; https://github.com/schemathesis/schemathesis |

---

## (c) Mechanistic-model recipes

Notation: customer `i`, day `t`, month `m`. All numeric parameters below marked [U] are **illustrative priors, not Georgian facts**; fit them to the registry or to any real transaction sample the team can obtain. Parameters that come from a source carry its flag.

### c.0 Overall generation order (scenario first, ledger second)

```
1. sample_persona_and_loan(registry)            # c.1
2. decide_delinquency_scenario(loan, macro)     # c.8, c.9  (path of dpd per month, cure/default)
3. build_income_streams(persona)                # c.2
4. plan_obligations(loan schedule, rent, bills) # c.7
5. enforce_scenario(): shave discretionary cash/shift income in "miss" months so the instalment debit really fails   # c.8
6. simulate_discretionary_spend(t)              # c.3, c.4  (rejects events when funds are short)
7. run_ledger_daily(); derive balances/pending  # c.5
8. render API views; run validators             # c.6, (e)
```

Why scenario-first: a fully endogenous model (shortfall causes delinquency) needs expensive calibration to hit registry roll rates; a planned scenario hits them by construction and still leaves a consistent cash-flow footprint (the debit fails, dpd increments, arrears get paid when income arrives).

### c.1 Population layer: bootstrap + copula on primitives, derive the rest

```python
# primitives P = [age, sex, region, legal_form, total_income, remit_income(SOI04),
#                 disbursed_amount, nominal_rate, term, instalment_freq, score, n_bureau_inquiries, ...]
# derived     D = [instalment, PTI, LTV, outstanding_principal, overdue_*, dpd_*]  # NEVER sampled directly
u = rank(P_continuous) / (n + 1)                 # pseudo-observations
z = norm.ppf(u);  R = corrcoef(z.T)              # Gaussian copula (or Student-t / vine if tails matter)
z_new = mvn(0, R).rvs(N)
P_new = quantile(P_real, norm.cdf(z_new))        # empirical marginals (smoothed: add kernel jitter, eps ~ 0.02-0.05 sd)
# discrete columns: sample conditionally or use pyvinecopulib var_types=['d', ...]
instalment = pmt(P_new.disbursed, P_new.rate/f, n_instalments)          # f = instalments per year
PTI_new    = instalment_per_month / net_monthly_income                   # compare with real PTI
```

Checks: if real `PTI` != `instalment / income` exactly, learn the residual `r = PTI_real / PTI_formula` (e.g. different income definition, income at origination vs now) and sample `r`, do not ignore it. A pure copula on all 59 columns does not reproduce exact functional identities such as `PTI = instalment/income`, because each marginal is transformed independently and the copula only matches rank dependence. My toy test (synthetic income and an instalment-like amount with strong dependence) showed rank correlation and the ratio's mean and spread preserved (Spearman 0.883 real vs 0.879 synthetic; ratio mean 0.313 vs 0.313, sd 0.095 vs 0.097) [L, my test]; that is a strongly dependent case. How badly ratios degrade under weaker dependence is untested here [U]. The reason to derive PTI/instalment/outstanding stays the same: exact identities (and hard bounds such as `overdue <= outstanding`) hold by construction only if derived.

Privacy: smoothed bootstrap, then run a distance-to-closest-record test (section e); coarsen rare cells (region x age x income band).

### c.2 Income streams

**Employment income.** Registry income `G` is probably gross. Salary credit net of Georgian payroll deductions: income tax 20% and employee pension 2% (PwC/Rivermate/Teamed describe this; whether the 2% is deductible from the 20% base is disputed across sources) [S]. So `S_net ~ 0.78-0.784 * G_salary`. Paid on a payday `d_i` per employer type (public sector, private, self-employed with irregular transfers). Payday-of-month distribution is unknown [U]: start with a mixture (about one third around the 1st-10th, one third mid-month, one third 25th-month end) and shift to the previous working day when it falls on a weekend/holiday. Multiplicative monthly noise `exp(N(0, 0.03-0.08))` plus rare bonus months [U]. Longer-horizon income dynamics (persistent plus transitory components, rare large drops) follow the standard income-process literature, e.g. Guvenen, Karahan, Ozkan, Song, Econometrica 2021 [L, Crossref record: https://doi.org/10.3982/ecta14603]; saving buffers that make discretionary spend tighten as balances fall follow buffer-stock saving, Carroll, QJE 1997 [L, Crossref record: https://doi.org/10.1162/003355397555109]. PTI rules use *net* income [P: NBG regulation tables are on net monthly income].

**Pension and social transfers.** State old-age pension 2026: 370 GEL (<70) and 495 GEL (70+) [S: madloba.info; Parliament of Georgia budget briefing]; 905,000 recipients as of Aug 2026 [S: JAM News]. Payment date is not verified [U].

**Remittances (SOI04).** Registry column gives monthly mean `Rm`. Arrival process: Poisson with rate `lambda_r` (1-4/month [U]), amounts lognormal with `E[sum over month] = Rm`; corridors weighted by NBG shares. Inflows in July 2026 were USD 350.05M (+3.9% y/y) per NBG via 1TV [P]; EU about 53.7%, US 19.4%, Russia 12.9% (search summary, not on the page I opened) [S]; August 2026 Russian flows reportedly collapsed after a transfer system suspended service [S]. Channel narratives: bank SWIFT credit, money-transfer operators (names unverified [U]). USD/EUR-denominated credits convert at that day's NBG rate (field `currencyExchange` in Berlin Group).

**Income-to-registry consistency (hard constraint).** `mean_m(sum of salary credits)` must reproduce `0.78 x G_salary` within noise and `mean_m(sum of remittance credits)` must reproduce `SOI04`; unit-test this per customer.

### c.3 Transaction timing: inhomogeneous Poisson with payday effect, optional Hawkes bursts

For category `c`:

```
lambda_{i,c}(t) = lambda0_{i,c} * g_dow[c, dow(t)] * h_pay[c, tau_i(t)] * s_season[c, month(t)] * u_hol[c, t]
h_pay[c, tau]   = 1 + kappa_c * exp(-tau / theta_c)         # tau = days since last salary credit
```

- Payday effect: spending jumps at receipt of pay and decays; stronger for liquidity-constrained households. Canonical evidence: Stephens (2003) [L, Crossref record: https://doi.org/10.1257/000282803321455386]; Gelman, Kariv, Shapiro, Silverman, Tadelis, NBER WP 21025 [L, Crossref record: https://doi.org/10.3386/w21025]; Baker, JPE 2018 [L, Crossref record: https://doi.org/10.1086/698106]; Ganong and Noel, AER 2019 [L, Crossref record: https://doi.org/10.1257/aer.20170537]. Priors: `kappa` 0.3-1.5, `theta` 5-12 days [U].
- Weekday/holiday: use `holidays.country_holidays('GE', years=Y)`. 2026 output [L]: Jan 1, 2, 7, 19; Mar 3, 8; Apr 9; Apr 10-13 (Good Friday to Easter Monday); May 9, 12, 17, 26; Aug 28; Oct 14; Nov 23. This is a community list; confirm against Georgian legislation [U]. Bookings and interbank credits shift to the next working day; same-day instant payments are a separate case [U].
- Counts per day `N ~ Poisson(integral of lambda over the day)`; intraday time from a mixture of Gaussians over the hour (lunch/evening peaks) [U].
- **Hawkes bursts** (shopping trips, repeated top-ups, ATM retries): `lambda(t) = mu(t) + sum_{t_k<t} alpha * exp(-beta (t - t_k))`, stable iff branching ratio `alpha/beta < 1`; mean events over `T` is about `mu T / (1 - alpha/beta)`. Simulate by Ogata thinning [L, Crossref record: https://doi.org/10.1109/tit.1981.1056305]: draw `dt ~ Exp(lambda_bar)` where `lambda_bar >= lambda(t)` over the interval, accept with prob `lambda(t)/lambda_bar`. Original process: Hawkes (1971), Biometrika 58(1) [L, Crossref record: https://doi.org/10.1093/biomet/58.1.83 ; PDF https://www.dcscience.net/Hawkes-Biometrika-1971.pdf]. Finance review: Bacry, Mastromatteo, Muzy (arXiv 1502.04592) [L]. Use only where clustering is observable; otherwise Poisson is adequate.

### c.4 Amounts, categories, merchants, ATM cash

- **Amounts:** per-category lognormal mixture `log A ~ sum_k pi_k N(mu_k + eta * ln(income_i / ref), sigma_k)`, income elasticity `eta` about 0.3-0.6 [U]. Heavy tails: check with a power-law/lognormal comparison (Clauset, Shalizi, Newman, SIAM Review 2009 [L, Crossref record: https://doi.org/10.1137/070710111]). Price points: utilities and telecom top-ups at round amounts (5/10/20 GEL [U]); ATM withdrawals in multiples of 10 or 20 GEL [U]; non-round card amounts with 2 decimals.
- **Category shares:** per-household Dirichlet around the national consumption basket (Engel's law: food share falls as income rises). Source for Georgian shares: Geostat household survey, **not yet fetched [U]**.
- **MCC:** Berlin Group `cardTransaction.merchantCategoryCode` is a 4-character string [L]. Standard is ISO 18245 (list examples: 5411 grocery, 5814 fast food, 5541 service stations, 4121 taxis, 4814 telecom [S: bnm.md/NPCI/checkout.com lists]). Use at most ~25 MCC groups; map Georgian chains to MCCs yourself.
- **Markov overlay for within-day chaining** (optional): `P(next = c' | current = c)` from TabFormer-style counts as a weak prior; Georgian structure should dominate through category intensities.
- **ATM cash (Baumol-Tobin style):** cash wallet `w`; cash purchases drain `w`; when `w < w_min`, withdraw `W` around `sqrt(2 b C / r)` (Alvarez and Lippi; NBER WP 13416 [L, Crossref record: https://doi.org/10.3386/w13416]; published in Econometrica 2009, details unverified [U]). Cash share higher in regions and for low income [U].
- **Merchants/narratives (card):** merchant name to `cardAcceptorId` (max 35 chars) and `transactionDetails`; see d.5 for the merchant universe.

### c.5 Ledger and balance types (Berlin Group semantics)

Definitions from the Berlin Group YAML v1.3.11 [L]: `closingBooked` = booked balance at end of reporting period; `openingBooked` = previous closing; `interimBooked` and `interimAvailable` = intraday balances calculated "on the basis of booked credit and debit items" during the business day (the YAML does not say whether pending items or credit lines enter `interimAvailable`, so that is a bank-specific design choice: document yours); `expected` = booked entries plus pending items; `forwardAvailable`; `nonInvoiced` is listed for card accounts only (YAML text: "to be checked yet").

```
openingBooked(d)    = closingBooked(d-1)
closingBooked(d)    = openingBooked(d) + sum(booked entries with bookingDate = d)
interimBooked(t)    = openingBooked(d) + sum(booked entries up to t)
expected(t)         = interimBooked(t) + sum(pending entries)         # pending debits negative
interimAvailable(t) = interimBooked(t) + sum(pending debits) + overdraft_limit (if any)   # one defensible choice, not mandated
```

Georgian mandatory behaviour [L, Georgian guide]: account details must contain `interimAvailable` and `interimBooked`; the balances service must return at least `closingBooked` and `interimAvailable`; every `*Booked` balance carries `referenceDate`, every `Interim*` balance carries `lastChangeDateTime`; transaction lists are paged ("portions") with the balance types of Table 9 (p.69); booked entries sorted by `bookingDate`; `entryReference` mandatory and unique within consent; debits negative; `valueDate` mandatory; `bookingDate` required for booked; cards must return at least `interimAvailable` from the card system; multi-currency accounts use `XXX` aggregation level.

Event handling per day: credits at value date; scheduled debits at due dates; discretionary events by timestamp, **declined if `available + overdraft < amount`** (no ledger entry; optionally a decline log). Card authorisations: pending 1-3 days then booked with `bookingDate > transactionDate` [U]. Ledger identity must hold exactly in integer tetri (use `Decimal` or integers, never floats).

### c.6 Card accounts and card transactions

Berlin Group `cardAccountDetails` has `maskedPan`, `creditLimit`, `balances`; `cardTransaction` has `cardTransactionId`, `terminalId`, `transactionDate`, `bookingDate`, `transactionAmount`, `originalAmount`, `currencyExchange`, `markupFee`, `markupFeePercentage`, `cardAcceptorId`, `cardAcceptorAddress`, `merchantCategoryCode`, `maskedPAN`, `invoiced`, `proprietaryBankTransactionCode` [L]. Georgian rules: the resource id must never reveal the PAN; card-account and card endpoints extend 9.3 with `cardAccount` hyperlinks; for cards, `interimAvailable` must come from the card system itself [L, Georgian guide 9.4.x].
- Debit cards: card transactions debit the current account; the card-account view shows the same flows with MCC.
- Credit cards: statement cycle, `invoiced` true/false split (YAML: for card accounts `closingBooked` is composed of invoiced-but-unpaid entries; `expected` adds not-yet-invoiced booked entries and pending items; `nonInvoiced` is undefined beyond "to be checked yet", so read it as booked-not-invoiced [U]), minimum payment, interest on revolving balance; a missed minimum payment feeds the same delinquency engine.
- FX: foreign merchant in USD/EUR: `originalAmount` + `currencyExchange` (NBG rate for the day plus bank spread [U]) + `markupFee`.

### c.7 Loans: schedules and back-casting from registry columns

**Annuity.** With period rate `i = r / f` and `n` instalments: `PMT = P i / (1 - (1+i)^-n)`, `B_k = P (1+i)^k - PMT ((1+i)^k - 1)/i`. **Equal principal:** `principal_k = P/n`, `payment_k = P/n + B_{k-1} i`. Total interest for P=10,000, r=18%, n=24: annuity 1,981.78 vs equal-principal 1,875.00 [L, my calculation]. Day-count (actual/365 vs 30/360), fees and APR conventions of Georgian banks: [U], infer from registry columns `nominal interest rate` and any effective-rate column.

**Back-casting loan age from the snapshot.** Given `P, r, n, f`, observed outstanding principal `B_obs` and overdue principal `OP`:
```
B_sched = B_obs - OP                                   # principal that would remain if on schedule
k*      = ln((PMT - B_sched * i) / (PMT - P * i)) / ln(1 + i)     # instalments already due/paid (verified numerically: k=5,13,23 recovered exactly [L])
```
Round `k*`; residual `B_sched - B(round(k*))` is prepayment (negative) or a modelling error. Origination date = snapshot date minus `k* / f` years. If `k*` is incompatible with `term` (negative, `>n`), flag the row and resample age.

**Delinquency history that matches registry columns.** For `total_dpd = T`, `max_dpd = M`, overdue principal/interest `OP, OI`: sample `J >= 1` late episodes with `max length = M`, `sum of lengths = T`; if `OP > 0` one episode is ongoing at snapshot. Number of missed instalments about `ceil(OP / principal_part)`. Penalty interest: take the registry's overdue-interest column; regulatory caps unverified [U].

**Instalment schedule view** (suggested loan-API fields; the official loan data model is unverified): `instalmentNo, dueDate, principal, interest, fees, totalDue, status {paid, due, overdue}, paidDate, daysPastDue`, plus loan header (`contractNumber, currency, disbursedAmount, nominalRate, effectiveRate, term, frequency, outstandingPrincipal, overduePrincipal, overdueInterest, maxDaysPastDue`).

### c.8 Delinquency: roll rates, mover-stayer, cure, hazards

States (months): `0` current; `1` 1-29 dpd; `2` 30-59; `3` 60-89; `4` 90+; `D` default/write-off (absorbing); `P` paid off (absorbing). Default at 90+ dpd is the Basel/IFRS 9 convention [U unless confirmed for NBG classification; I could not find NBG's day-count categories].

- **Markov roll-rate model:** `x_{m+1} = x_m P(s)` with segment `s` (product, score band, age on book). Canonical references: Cyert, Davidson, Thompson (1962), *Estimation of the Allowance for Doubtful Accounts by Markov Chains*, Management Science [L, Crossref record: https://doi.org/10.1287/mnsc.8.3.287]; Jarrow, Lando, Turnbull (1997), Rev. Financial Studies [L, Crossref record: https://doi.org/10.1093/rfs/10.2.481]; Malik and Thomas (2010), JORS, consumer-loan Markov [L, Crossref record: https://doi.org/10.1057/jors.2009.123].
- **Mover-stayer correction (important):** homogeneous Markov chains misstate credit behaviour because a fraction of borrowers never move. Frydman, Kallberg, Kao (1985), *Testing the Adequacy of Markov Chain and Mover-Stayer Models as Representations of Credit Behavior*, Operations Research 33(6) [L, Crossref record: https://doi.org/10.1287/opre.33.6.1203]. Implementation: latent type `stayer ~ Bernoulli(p_stayer(score, age))`; stayers are always current; movers follow `P`.
- **Estimation:** (a) if the 10 registry files share loan keys across snapshots: counts `n_ij / n_i` by segment with Dirichlet smoothing; (b) otherwise moment-match: choose `theta` to minimise distance between simulated and observed `P(max_dpd >= 30)`, `P(>=60)`, `P(>=90)`, mean `total_dpd` given ever-late, share currently overdue, and implied cure rate. To show sensitivity only: a made-up matrix with rows `0 -> [0.965, 0.035, 0, 0, 0]`, `1 -> [0.45, 0.25, 0.30, 0, 0]`, `2 -> [0.20, 0, 0.25, 0.55, 0]`, `3 -> [0.05, 0, 0, 0.35, 0.60 to D]`, `D -> D` gives a 12-month cumulative default of 6.9% from "current" [L, my test]; such numbers are not Georgian facts and must not be shipped uncalibrated.
- **Discrete-time hazard alternative:** `logit h_m = a(age_m) + b' z_i + c * macro_m + g * Y_m`; see Bellotti and Crook (2009), JORS, survival analysis with macro variables [L, Crossref record: https://doi.org/10.1057/jors.2008.130]. Fit with `lifelines` or a logistic regression on a person-month table.
- **Cure:** arrears are paid when income arrives (salary credit or remittance): debit `overdue principal + overdue interest + penalty` before the next instalment; transition `k -> 0` at that date.
- **Scenario enforcement:** for months scheduled to miss, apply one or more of: salary credit delayed or reduced (job loss), remittance gap, large unavoidable outflow (medical, repair), P2P outflow to family, so that `available < PMT` on the due date; the failed debit is *not* booked (or is booked as a declined direct-debit attempt if you model it); `dpd` increments daily from the due date.

### c.9 Default correlation (Vasicek) and the score-to-PD link

- **Single-factor model:** asset/credit-quality variable `X_i = sqrt(rho) Y + sqrt(1-rho) Z_i`; default if `X_i < N^-1(PD_i)`. Conditional PD: `PD_i(Y) = N((N^-1(PD_i) - sqrt(rho) Y)/sqrt(1 - rho))`. Large-portfolio loss CDF: `F(x; p, rho) = N((sqrt(1-rho) N^-1(x) - N^-1(p))/sqrt(rho))` [P: Vasicek, "The Distribution of Loan Portfolio Value", Risk, Dec 2002, eqs. (3)-(5); PDF I read: https://www.maths.univ-evry.fr/pages_perso/crepey/Credit/Vasicek.pdf ; Risk.net page https://www.risk.net/risk-management/credit-risk/1530287/loan-portfolio-value]. I verified numerically that simulated conditional PDs match eq. (4) (F(0.10) = 0.9976 closed form vs 0.9974 simulated, p=0.04, rho=0.03) [L]. Rigorous foundation of the ASRF model: Gordy (2003), J. Financial Intermediation [L, Crossref record: https://doi.org/10.1016/s1042-9573(03)00040-8].
- **Asset correlations for retail** (BCBS, *An Explanatory Note on the Basel II IRB Risk Weight Functions*, 5 July 2005, Sect. 5.3) [L, I extracted the PDF]: residential mortgages `R = 0.15`; qualifying revolving retail `R = 0.04`; other retail `R = 0.03 * w + 0.16 * (1 - w)` with `w = (1 - exp(-35 PD)) / (1 - exp(-35))`. Values: PD 0.5% -> 0.139; 2% -> 0.095; 5% -> 0.053; 10% -> 0.034; 30% -> 0.030 [L, computed]. URL: https://www.bis.org/publications/explanatory-note-basel-ii-irb-risk-weight-functions.pdf (the `bcbs/irbriskweight.htm` landing page [P] lists it).
- **Implied default correlation** between two loans: `(Phi2(a, a; rho) - p^2) / (p (1 - p))` with `a = N^-1(p)`: for p=4%, rho=3% it is 0.0061; p=4%, rho=10% it is 0.0224 [L, computed]. Use as a sanity check against registry data if vintage/region clusters allow.
- **Macro factor `Y_t`:** monthly AR(1) (persistence about 0.8 [U]) driven by scenarios (GEL depreciation, unemployment, remittance shock). Draw a borrower-month default with `U < PD_i(Y_t) converted to monthly hazard 1 - (1 - PD_12m)^(1/12)`.
- **Score to PD:** fit a monotone map on registry rows: `logit(PD) = a + b * score` (or isotonic). Check the registry's `probabilityOfDefault` horizon (12 months assumed [U]). Standard scorecard scaling (points-to-double-odds) is described in Siddiqi, *Credit Risk Scorecards* (Wiley 2006) and Thomas, Edelman, Crook (SIAM 2002) [U: URLs not opened].
- Where the registry's own PD and score disagree with the observed dpd outcomes, keep the registry's *labels* as ground truth and re-derive the simulated PD from the labels (otherwise the ML task learns a simulator artefact).

### c.10 Macro stress

- **FX:** a large share of loans is FX-denominated: larization of loans 58.7% at 7M 2026 (so about 41% FX) [S: GBC]. For FX borrowers `PMT_GEL(t) = PMT_FX * s_t` where `s_t` = GEL per USD (about 2.62 in early August 2026 [S: Cbonds/GBC]); with GEL income, `PTI_t = PMT_FX * s_t / income_GEL`. Natural hedge: share of income in FX (remittances). Stress: `ln s_{t+1} = ln s_t + sigma * eps + J_t` with jump scenarios of +10/+20/+30%.
- **Rates:** NBG policy rate 8.25%, unchanged on 17 June 2026 (annual inflation 5.7% in May; next meeting was 29 July 2026) [P: https://1tv.ge/lang/en/news/nbg-decides-to-keep-monetary-policy-rate-unchanged-at-8-25-per-cent/]. Floating-rate loans reprice `r_t = r_0 + passthrough * (policy_t - policy_0)`, pass-through 0.5-1 [U].
- **Income shocks:** job-loss hazard, wage cut, remittance disruption (Russia corridor, [S]).
- **Regulatory caps to respect at origination (checks in e):** see d.6 for PTI/LTV rules.

### c.11 Savings and deposits

Term deposit: principal, rate, term, start/maturity, interest paid monthly or at maturity, capitalisation flag. Savings account: stock follows sweeps and interest: daily accrual `B * r / 365` credited monthly. Berlin Group lists a bankTransactionCode `PMNT-MCOP-OTHR` for dynamic-amount standing orders that move leftover funds, e.g. month-end sweeps to savings [L, YAML]. Savings rate of income `s_i ~ Beta` by income quintile [U]. Interest withholding tax: unverified [U]. Deposit API shape: unknown (see gap in section a).

### c.12 Pitfalls of ML generators for this task

- **Identities break.** `PTI = instalment / income`, `outstanding <= disbursed`, `overdue <= outstanding`, `total_dpd >= max_dpd` are not learned by GANs/copulas; derive or reject-sample.
- **Mode collapse / rare categories.** CTGAN addresses imbalanced categoricals with a conditional vector [L: arXiv 1907.00503], but small categories (rare regions, rare legal forms) are still lost; check category frequencies.
- **Tails and dependence.** A Gaussian copula has no tail dependence; delinquency clusters in the tails. Use Student-t or vines for the score/income/PTI block.
- **Privacy.** GAN/bootstrapped outputs can memorise rows; run DCR and exact-match tests; coarsen rare cells.
- **Sequences.** PAR/DoppelGANger/TabFormer-style models learn marginal sequence statistics but not calendar structure (paydays, holidays) or hard ledger constraints.
- **Labels.** Keep registry labels as truth; generate features conditionally on labels, not the other way round.

---

## (d) Georgian realism inputs, with verification flags

### d.1 Open-banking standard and what it mandates

| Item | Value | Source | Flag |
|---|---|---|---|
| Georgian standard documents | XS2A Implementation Guide 0.8.RC3 (2022-09-12); IAA manual 0.8.RC1 (2021-09-30); JWS guide 0.5.RC1 (2021-09-06); identity-assurance 0.3.RC3 (draft, 2022-09-12) | https://openfinance.ge/en/standards | **[P]** |
| Base framework | "Based on" Berlin Group API Framework; vendor says "Georgia v0.8 on NextGenPSD2 v1.3.9" | https://developers.paysera.com/guides/open-banking-georgia | **[S]** (vendor). I used YAML 1.3.11 for schemas; check differences with 1.3.9 |
| Loans/deposits | Not in 0.8.RC3 (zero hits). NBG added "loan information sharing" and "deposit information sharing" APIs (news dated 27 Feb 2025); no spec link | https://nbg.gov.ge/en/media/news/nbg-launched-new-services-in-order-to-develop-open-finance-ecosystem | **[P]**; actual schema **[U]** |
| NBG Open Banking page | Links to rule on inclusion, standard (openfinance.ge), registry, statistics; contact `ob@nbg.gov.ge`, +995 322 406 406 | https://nbg.gov.ge/en/page/open-banking | **[P]** |
| Sandbox | NBG + IFC technical sandbox on TESOBE's OBP platform (2023) | https://nbg.gov.ge/en/media/news/nbg-and-ifc-sign-open-banking-technical-sandbox-project | **[S]** (search summary) |
| Mandatory balances (accounts) | `interimAvailable`, `interimBooked` in account details when consent covers balances | Georgian guide, paragraphs 1566-1567 | **[L]** |
| Mandatory balances (balances endpoint) | at least `closingBooked` and `interimAvailable` | paragraphs 1586-1587 | **[L]** |
| Balance date attributes | `*Booked` -> `referenceDate`; `Interim*` -> `lastChangeDateTime`; intermediate-day `closingBooked` in paged lists (Table 9, p.69) | paragraphs 1716-1729 | **[L]** |
| Transactions | `entryReference` mandatory/unique; debit negative; `valueDate` mandatory; `bookingDate` required for booked; sorted by `bookingDate`; `remittanceInformationUnstructured` (Max140Text) or `...Array`, passed unchanged; `additionalInformation` Max500Text; `debtorName`/`creditorName` (Max70Text) conditional on OwnerName-type consent; `debtorAccount` for credits, `creditorAccount` for debits | pp.71-76, paragraphs 1702, 1793 | **[L]** |
| Cards | `GET /v1/cards/{card-id}/balances` mandatory with at least `interimAvailable`; card accounts return all balances a normal account does; no PAN leakage in `resourceId` | paragraphs 1858-1882, 1967-1977 | **[L]** |
| Vendor "unsupported" lists (e.g. Paysera lacks `closingBooked`, card accounts, `debtorName`) | A particular PSP's deviations, **not the standard** | https://developers.paysera.com/guides/open-banking-georgia | **[P]** (as vendor statement) |
| Language | Georgian is the default language/region when unspecified | Guide paragraphs 462, 465 | **[L]** |

### d.2 IBAN and bank codes (the 12 banks)

IBAN structure: 22 characters = `GE` + 2 check digits + 2-letter bank code (assigned by NBG) + 16-digit account identifier; example `GE29NB0000000101904917` [P: https://nbg.gov.ge/en/page/iban-international-bank-account-number]. Check digits are mod-97 (ISO 13616); the NBG example and `GE48TB0000000000000012` (generated by `schwifty.IBAN.generate('GE', bank_code='TB', account_code='0000000000000012')`) both pass `python-stdnum` `iban.validate` [L]. The internal composition of the 16 digits (branch/product/currency suffix) is bank-specific and **unverified [U]**; infer from real sample IBANs.

NBG table of IBAN bank codes (labelled "RTGS Code" and "IBAN Code") [P: https://nbg.gov.ge/en/payment-system/iban; no date on page; the download name hints 2025-06-19 [U]]. I cross-checked each 8-character code in the `schwifty` BIC registry [L]:

| Bank (as in your list) | NBG legal name on page | IBAN code | NBG "RTGS" code (BIC format) | schwifty cross-check |
|---|---|---|---|---|
| TBC | JSC "TBC Bank" | **TB** | TBCBGE22 | OK, GE, domestic code TB |
| Bank of Georgia | JSC "Bank of Georgia" | **BG** | BAGAGE22 | OK, BG |
| TeraBank | JSC "Terabank" | **KS** | TEBAGE22 | OK, KS (note: not "TE"/"TB") |
| ProCredit | JSC "ProCredit Bank" | **PC** | MIBGGE22 | OK, PC |
| Credo | JSC "Credo Bank" | **CD** | JSCRGE22 | OK, CD |
| Halyk | JSC "Halyk Bank Georgia" | **HB** | HABGGE22 | OK, HB |
| Liberty | JSC "Liberty Bank" | **LB** | LBRTGE22 | OK, LB |
| Basisbank | JSC "BasisBank" | **BS** | CBASGE22 | OK, BS |
| Silk | JSC "Silk Bank" (registry: "Silk Road Bank") | **BT** | DISNGE22 | OK, BT (note: not "SK") |
| Cartu | JSC "Cartu Bank" | **CR** | CRTUGE22 | OK, CR |
| PASHA | JSC "Pasha Bank Georgia" | **PB** | PAHAGE22 | OK, PB |
| Paysera | JSC "Paysera Bank Georgia" | **PS** | PSRAGE22 | BIC parses but **no registry record**: NBG only |

All 12 IBAN codes **[P]** (NBG); 11 of 12 BICs additionally confirmed by an independent registry bundled with `schwifty` **[L]**; Paysera's BIC **[P]** from NBG only. Other codes on the NBG page useful for counterparties: NB (National Bank), TR (State Treasury), VT (VTB Bank Georgia), ZB (Ziraat), IS (Isbank), HS (HASH Bank), PV (Pave Bank), MB (Microbank MBC), BC (Microbank Crystal) **[P]**. Whether NBG's 8-character "RTGS code" always equals the bank's SWIFT BIC is implied but not stated by NBG; for 11 banks the registry agrees **[L]**. Bank market-share weights for counterparty selection: not found **[U]**.

### d.3 Names, addresses, phones, IDs (Faker `ka_GE` audit, run locally [L])

| Field | Faker `ka_GE` behaviour (Faker 40.43.0) | Verdict |
|---|---|---|
| Locale exists | `ka_GE` is the only `ka*` locale; providers localized: person, address, phone_number, job, date_time, color | OK |
| Names | Georgian script; 112 male first names, 157 female, 479 surnames, sampled **uniformly** (about 70% end in -dze/-shvili but each surname equally likely; includes Azerbaijani-pattern -ev/-eva surnames such as Aliyev/Aliyeva) | Usable; reweight by real frequency; add transliteration to Latin yourself |
| City | 62 city names (includes towns such as Gagra, Gali, Akhali Atoni in occupied territories) | Replace with a population-weighted list and region mapping (Geostat) |
| Postcode | `#####` (5 digits) | **Wrong:** Georgia uses 4 digits (0100 Tbilisi) [S: UPU guide via https://www.upu.int/UPU/media/upu/PostalEntitiesFiles/addressingUnit/geoEn.pdf , geopostcodes.com] |
| Phone | Formats include `+995 ### ### ###`, `+995 32 ### ## ##`, `0 34# ...` | Random digits do not guarantee valid mobiles. Mobile numbers are 9 digits starting with 5 (e.g. 551, 555, 577, 591, 599) [S: https://en.wikipedia.org/wiki/Telephone_numbers_in_Georgia_(country)]; write a custom provider |
| `iban()`, `bban()`, `bank_country()`, `swift()` | Return **GB** IBAN/BIC (default bank provider); no `ka_GE` bank provider | **Not usable**; custom provider using d.2 |
| `currency_code()` | Random world currency (observed SLL) | Not usable; fix GEL/USD/EUR |
| `ssn()` | US-format | Not usable |
| `company()` | Surname-surname pattern | Replace with a merchant/employer list |
| Mimesis | 48 locales, no `ka`/`ge` | Not usable for Georgian |

Personal ID number: 11 digits. Structure per a 2003 Canadian IRB response citing an embassy official: 2-digit district code, 3-digit Interior-Ministry office code, 6-digit personal number [S: https://www.ecoi.net/en/document/1207477.html]; no checksum published per one weak source [S]. **[U]** for current rules. Recommendation: generate IDs from a clearly synthetic pattern and never reproduce real-looking valid IDs; document the scheme.

### d.4 Income, remittances, rates, regulation

| Item | Value | Source | Flag |
|---|---|---|---|
| Avg monthly nominal earnings of employees, Q1 2026 | **2,363.8 GEL** national (+8.9% y/y); women 1,901.8; men 2,836.0; business sector 2,335.9; non-business+financial 2,413.7; published 16.06.2026. (Mean of employees' "nominal" earnings, not median; whether Geostat's figure is before tax is not stated in the release I read, but is conventionally gross [U]) | https://geostat.ge/media/80106/Average-Monthly-Nominal-Earnings-of-Employees---%28I-Quarter%2C-2026%29.pdf | **[P][L]** (PDF has no text layer; read as images) |
| By region, Q1 2026 (GEL) | Tbilisi 2,779.6; Mtskheta-Mtianeti 2,188.8; Kvemo Kartli 2,011.4; Adjara 1,889.4; Samegrelo-Zemo Svaneti 1,587.9; Shida Kartli 1,538.9; Samtskhe-Javakheti 1,520.8; Imereti 1,471.4; Guria 1,458.7; Kakheti 1,418.5; Racha-Lechkhumi and Kvemo Svaneti 1,223.3 (some firms attributed to head-office region) | same Geostat PDF, chart no. 6 | **[P][L]** |
| By sector, Q1 2026 (GEL) | Finance and insurance 5,952.5; ICT 4,290.7; Mining 3,230.4; Professional/scientific/technical 3,186.0; lowest in chart: other services about 1,317; education about 1,423; agriculture about 1,509 | same PDF, chart no. 2 | **[P][L]** (low-end values read from chart) |
| Earnings, Q2 2026 | 2,389.3 GEL (+8%) | https://georgiatoday.ge/tag/salaries/ | **[S]** |
| Payroll deductions | Income tax 20%, employee pension 2% (+2% employer, state co-funding); deductibility of the 2% disputed across guides | https://taxsummaries.pwc.com/georgia/individual/other-taxes ; https://www.rivermate.com/guides/georgia/taxes | **[S]**; confirm on rs.ge / matsne.gov.ge |
| Old-age pension | 370 GEL (<70), 495 GEL (70+) in 2026; 905,000 recipients Aug 2026 | https://madloba.info/en/faq/work-business-taxes/salaries-and-pensions-in-georgia/ ; https://jam-news.net/?p=236620 | **[S]** |
| Remittance inflows, July 2026 | USD 350.05M (GEL 922.2M), +3.9% y/y; outflows USD 39.0M; 29 partner countries each > USD 1M, 97.6% of total | https://1tv.ge/lang/en/news/nbg-in-july-2026-remittances-entering-georgia-increased-by-3-9-per-cent-year-on-year-amounting-to-usd-350-million/ (reporting NBG data) | **[P]** (news page reporting NBG) |
| Remittance shares by country | EU 53.7%, US 19.4%, Russia 12.9% (July 2026); Aug 2026 Russia about USD 5M (-88% y/y) after Zolotaya Korona suspended transfers; US 64.14M, Italy 55.22M, Germany 31.31M, Greece 28.24M, Israel 27.92M | search summaries of 1tv/civil.ge/jam-news | **[S]** (not opened) |
| NBG policy (refinancing) rate | 8.25%, held 17 June 2026; inflation 5.7% (May); core 3.5% | https://1tv.ge/lang/en/news/nbg-decides-to-keep-monetary-policy-rate-unchanged-at-8-25-per-cent/ | **[P]** (as of June; later decisions not checked) |
| GEL loan rates | Mortgage 11.9% (Mar 2026, from 13.4% in Jan 2025); business 12.8% (from 14.4%), per NBG president Turnava | https://georgiatoday.ge/?p=103680 (search lead) | **[S]**. **Consumer-loan rate series from NBG: not found [U]** -> take rates from the registry |
| Loan dollarization | Larization of loans 58.7% (7M 2026) | https://gbc.ge/en/news/finanse/loan-larization-increased-by-1-in-1-year | **[S]** |
| GEL/USD | about 2.62 (NBG official, 6 Aug 2026) | https://gbc.ge/en/news/finanse/lari-strengthens-in-july-too (search lead), Cbonds | **[S]**; October 2026 value not found |
| NBG responsible-lending regulation (2018) | PTI by monthly *net* income tier and hedged/non-hedged status (table garbled on the page); LTV 85% GEL / 70% FX; PTI computed with annuity payments; effective 1 Jan 2019 | https://nbg.gov.ge/en/media/news/the-national-bank-of-georgia-has-approved-the-regulation-on-responsible-lending-to-natural | **[P]** (2018; later amended) |
| PTI cap update | 25% PTI limit applies to net monthly income below **1,500 GEL** now; **2,000 GEL from 1 Feb 2027**; **2,500 GEL from 1 Sep 2027**; published 30 Sep 2026 | https://georgiatoday.ge/georgia-to-tighten-lending-rules-from-february-2027/ | **[P]** (news page); above the threshold a 50% cap was reported by another outlet [S] |
| GEL mortgage LTV | 90% since Feb 2025 | https://sovanews.tv/en/2026/10/08/who-can-afford-real-estate-in-tbilisi-mortgages-installment-plans-and-new-regulations/ | **[S]** |
| NBG loan classification (days past due categories), NBG retail NPL ratio | not found | n/a | **[U]** |
| Households with at least one loan | about 1.9M (roughly half the adults), Mar 2025 | search summary (NBG data via press) | **[S]** |

### d.5 Merchants, utilities, platforms (names seen in sources; mapping to MCC is yours)

| Group | Names with evidence | Evidence | Flag |
|---|---|---|---|
| Supermarket chains | Daily Group (33% of modern retail), Ori Nabiji (20.5%), Nikora (18.7%) per TBC Capital 9M-2025 review; chains 38% of FMCG nationally (54% Tbilisi, 28% regions); traditional trade 59% | https://georgiatoday.ge/georgia-fmcg-9-8-in-2025-slows-to-5-9-in-2026/ | **[P]** |
| | Carrefour (Majid Al Futtaim franchise; first Georgian hypermarket 2012); SPAR Georgia (operated by Foodmart, 207th store Oct 2021); Agrohub (premium grocery chain, mainly Tbilisi); Libre (discount chain, listed on Wolt/Glovo; reportedly managed by Nikora Trade) | https://www.esmmagazine.com/carrefour-georgia-expansion/45633 ; https://spar-international.com/news/active-expansion-spar-georgia-opens-its-207th-store/ ; https://east-fruit.com/en/news/agrohub-is-developing-a-farm-to-table-retail-concept/ ; https://wolt.com/en/geo/tbilisi/brand/libre | **[S]** |
| Telecom | Magti 41%, Silknet 35%, Cellfie 23% (mobile subscriber shares, 2024); Beeline became Cellfie | https://comcom.ge/en/yvela-siaxle/number-of-mobile-internet-subscribers-reaches-46-million-in-2024.page | **[S]** (regulator site via search) |
| Electricity / gas / water | Telasi and Energo-Pro (distribution; retail arm Telmiko in Telasi area); gas: Tbilisi Energy, SOCAR Georgia Gas, Saqorggaz; water: Georgian Water and Power (GWP), about 507k customers | https://civil.ge/?p=124868 ; https://www.devex.com/organizations/georgian-water-and-power-155692 | **[S]** |
| Ride/delivery | Bolt, Yandex Go, Maxim; Glovo, Wolt, Bolt Food | travel guides, Eurasianet | **[S]** (weak; no share data) |
| Not researched | Goodwill, Fresco, Europroduct, fuel chains (Wissol, Gulf, SOCAR retail, Lukoil, Rompetrol), pharmacies (GPC, PSP, Aversi), electronics (Zoommer), Metromoney/Tbilisi transport, payment aggregators (TBC Pay, Pay.ge, etc.) | n/a | **[U]** (do not hard-code until checked) |

MCC reference lists (not Georgia-specific) for mapping: https://docs.checkout.com/resources/codes/merchant-category-codes ; https://bnm.md/files/List%20of%20merchant%20category%20codes%20(MCC)_2.pdf **[S]**; the authoritative lists are ISO 18245 and the card-scheme manuals [U].

### d.6 Narrative / remittance-information conventions

Verified: Berlin Group field names and limits (`remittanceInformationUnstructured` Max140Text or `...Array` of Max140Text; for MT conversion the first 140 characters go to the MT message and the rest stays in the array; `additionalInformation` Max500Text; names Max70Text; Georgian is the default language) [L]. The Georgian text of narratives is stored "as is"; whether banks transliterate for SWIFT/RTGS channels is implied by the MT truncation rules but not stated [U].
**Unverified, my proposed templates (check against real statements before use):**

| Type | Georgian | Latin/translit | English |
|---|---|---|---|
| Salary | `ხელფასი 09/2026` | `xelfasi 09/2026` | `Salary for September 2026` |
| Pension | `პენსია` | `pensia` | `Pension` |
| Rent | `ბინის ქირა, ოქტომბერი` | `binis qira oqtomberi` | `Rent October` |
| Utilities | `კომუნალური გადასახადი` | `komunaluri gadasaxadi` | `Utility payment - Telasi` |
| Loan instalment | `სესხის დაფარვა, ხელშ. N...` | `sesxis dafarva` | `Loan repayment contract N...` |
| P2P | `გადარიცხვა` + free text | `gadarickxva` | `Transfer to family` |
| Card/ATM | merchant name + city; `ნაღდი ფულის გამოტანა` for ATM | n/a | `ATM withdrawal` |
| Remittance in | `Western Union money transfer` / `SWIFT transfer from <country>` | | |

### d.7 Things I could not verify (explicit list)

- Official loan and deposit data models in Georgia's Open Banking standard (the updated-standard SharePoint folder linked from openfinance.ge returned HTTP 403, so it needs a login; the Berlin Group openFinance downloads URL returned 404).
- Georgian-language/English parity of the XS2A guide, and diffs between the Georgian profile and Berlin YAML 1.3.11 (Paysera says 1.3.9).
- Any NBG consumer-loan rate series, NBG loan-classification day thresholds, sector NPL ratios, and the internal structure of the 16 IBAN digits.
- Payday and pension payment dates; Georgian consumption shares by income; typical Georgian bank narratives; bank market shares for interbank counterparty weights.
- Personal ID structure (only a 2003 secondhand account).
- Tink sandbox tooling; DoppelGANger reference repository; Kaggle licences for PaySim/BankSim; TabFormer data licence; SDMetrics privacy metrics.
- Merchant universe beyond the names listed in d.5; Bank-by-bank MCC acquiring conventions.

---

## (e) Validation checklist

Run all as automated tests (`pandera` for tables, `openapi-core`/`schemathesis` for responses, plain pytest for identities). Thresholds are starting suggestions [U].

**E1. Schema and spec conformance**
1. Every response validates against Berlin Group YAML 1.3.11 (and the Georgian mandatory-field list in d.1): `entryReference` unique, `valueDate` present, debit amounts negative, `bookingDate` on booked, names <= 70 chars, remittance <= 140 chars (else `...Array`).
2. Balance types: accounts return `interimAvailable` + `interimBooked`; balances endpoint returns `closingBooked` + `interimAvailable`; every `*Booked` has `referenceDate`, every `Interim*` has `lastChangeDateTime`; cards return `interimAvailable`.
3. IBAN: `stdnum.iban.validate`, country `GE`, 22 characters, bank code in the d.2 table; BIC matches the bank; PAN masked, never in `resourceId`.
4. Pagination: `entryReferenceFrom`/portion logic returns no duplicates or gaps; intermediate `closingBooked` present per day-boundary.

**E2. Accounting identities (exact, in integer tetri)**
5. `openingBooked(d) + sum(booked on d) = closingBooked(d)` for every account-day; `openingBooked(d) = closingBooked(d-1)`.
6. `expected = interimBooked + sum(pending)`; pending items later appear booked with the same amount (or documented adjustment).
7. No negative `interimAvailable` unless an overdraft/credit limit exists; declined events leave no ledger entry.
8. Card-account balances are consistent with the `invoiced` flags on card transactions (closingBooked = invoiced-unpaid; expected adds not-yet-invoiced booked and pending).
9. Currency conversion: `originalAmount x rate (+markup) = transactionAmount` within rounding.

**E3. Cross-product consistency**
10. **Income to salary credits:** per customer, `mean monthly salary credit = (0.78 to 0.784) x registry gross salary` within +-10% (noise-aware); remittance credits mean = `SOI04` within tolerance; income stability metrics (CV) in a plausible range.
11. **Instalment to debits:** for every due date, a debit equal to `PMT` exists (or a documented partial/miss); sum of principal parts = reduction of `outstandingPrincipal`; interest parts match `rate`.
12. **Delinquency to missed debits:** every dpd increment corresponds to a missed/failed instalment debit with insufficient funds at that date; every cure corresponds to an arrears payment; `total_dpd >= max_dpd`; `overdue <= outstanding`; if `overdue principal > 0` then `current dpd > 0`.
13. Registry-derived columns reproduced: `PTI = instalment / net income` (or the registry's own definition), `LTV`, `outstanding` within 1% of the registry value for rows built from it. *Soft diagnostic, not a pass/fail test:* expect bunching at or below the regulatory PTI cap for recent GEL loans (NBG's 2018 regulation, effective 2019, used a tiered table, lowest tier <1,000 GEL at 20-25% for non-hedged borrowers [P]; a later 25%-below-1,500-GEL rule exists whose start date I do not know [U]; NBG computes PTI on a maximum-term annuity, which may differ from the registry's PTI column [P]).
14. Loan age back-cast `k*` is within `[0, n]` for > 99% of rows; residual prepayment share plausible.

**E4. Distributional fidelity vs the registry (population layer)**
15. Marginals: two-sample KS / Wasserstein per continuous column, chi-square or total-variation for categoricals (target: KS < 0.03 at N=10,000 per file [U]).
16. Dependence: Frobenius norm of Spearman-correlation difference < 0.05 [U]; conditional means of PD, PTI, delinquency by region and score band; tail checks (99th percentile of income, PTI).
17. ML utility: **TSTR vs TRTR** (train on synthetic, test on real, versus train on real, test on real) for the PD model: AUC gap < 0.02-0.03 [U]; calibration slope/intercept; feature-importance rank correlation.
18. `sdmetrics` quality/diagnostic reports as a regression dashboard (not a pass/fail on their own).

**E5. Behavioural realism (transaction layer)**
19. Payday effect visible: share of discretionary spend in the 7 days after salary > 7/30 x baseline by the intended `kappa`; day-of-week and holiday patterns (no card activity at impossible times; weekend booking lag).
20. Category mix and ticket sizes by income quintile follow Engel-type gradients (food share decreases with income); amount distribution tails reasonable (lognormal vs power-law diagnostic); round-amount frequencies (ATM multiples of 10/20).
21. Transaction counts per active customer-month within an expected band; Hawkes branching ratio < 1; inter-arrival distribution consistent with Poisson+payday.
22. Cash-wallet: ATM withdrawal frequency decreases with card share; amounts multiples of the note denomination.

**E6. Credit-risk process**
23. Portfolio default rate by score band within +-20% relative of the registry (calibration); AUC of simulated outcomes vs registry score within 0.02.
24. Roll rates: simulated monthly transition matrix vs registry-estimated (or moment-matched) one; cure rate; `P(ever >= 30/60/90 dpd)`; mover-stayer share.
25. **Vasicek check:** time-series of monthly default rate dispersion consistent with the chosen `rho` (retail 0.03-0.16 per Basel, d.c.9); implied default correlation (formula in c.9) matches the registry's vintage/region clustering if measurable.
26. Stress tests: +20% GEL depreciation raises PTI of unhedged FX borrowers by 20% and increases defaults monotonically; rate-up scenario affects only floating loans.

**E7. Privacy / leakage**
27. Distance-to-closest-record (DCR): fraction of synthetic rows with zero or near-zero distance to a real row on standardised primitives should be ~ DCR of a holdout, not lower; zero exact matches on the full vector.
28. Rare-cell check: no synthetic combination of (region, age band, income band, legal form) with real count < k (k = 5-10 [U]).
29. No real names, IDs, or phone numbers; synthetic ID scheme documented.

**E8. Regression and reproducibility**
30. Seeded generation (`numpy.random.Generator`, one stream per customer) is byte-reproducible; golden-file tests for 20 hand-checked customers (salary customer, remittance-dependent, pensioner, delinquent-then-cured, defaulted, FX borrower, credit-card revolver).

---

## Source index (URLs actually opened or seen in results; flags as above)

- Georgian standards: https://openfinance.ge/en/standards [P]; guide PDF https://cdn.association.ge/static/openbanking/standards/pdf/NextGenPSD2_XS2A_Implementation_Guide_0.8.RC3_hL6sJdA.pdf [L]; https://nbg.gov.ge/en/page/open-banking [P]; https://nbg.gov.ge/en/media/news/nbg-launched-new-services-in-order-to-develop-open-finance-ecosystem [P]; https://developers.paysera.com/guides/open-banking-georgia [P].
- Berlin Group YAML: https://raw.githubusercontent.com/adorsys/xs2a/develop/xs2a-impl/src/main/resources/static/psd2-api_v1.3.11-2021-10-01v1.yaml [L].
- NBG IBAN pages: https://nbg.gov.ge/en/payment-system/iban [P]; https://nbg.gov.ge/en/page/iban-international-bank-account-number [P].
- Geostat Q1 2026 earnings PDF [P][L] (URL in d.4).
- Papers: arXiv 2306.16424 (AMLworld) [P]; 2011.01843 (TabFormer) [P]; 1907.00503 (CTGAN) [L]; 2207.14406 (PAR) [L]; 1909.13403 (DoppelGANger) [L]; 2209.15421 (TabDDPM) [L]; 2310.09656 (TabSyn) [L]; 1502.04592 (Hawkes in finance) [L]; Crossref-verified DOIs for Hawkes 1971, Gordy 2003, Jarrow-Lando-Turnbull 1997, Cyert et al. 1962, Frydman et al. 1985, Malik and Thomas 2010, Bellotti and Crook 2009, Stephens 2003, Baker 2018, Ganong and Noel 2019, Carroll 1997 (https://doi.org/10.1162/003355397555109), Guvenen et al. 2021 (https://doi.org/10.3982/ecta14603), Clauset et al. 2009, Ogata 1981, Patki et al. 2016 SDV paper (https://doi.org/10.1109/dsaa.2016.49), Nowok et al. 2016 [L]; Vasicek 2002 PDF [L]; BCBS explanatory note PDF [L].
- Repos/PyPI metadata (licence, last push, versions) fetched via GitHub API and PyPI JSON on 2026-10-20 [L]; GitHub API rate limit was hit at the end, so `fjxmlzn/DoppelGANger`, `plaid/sandbox-custom-users` metadata were not retrieved.
