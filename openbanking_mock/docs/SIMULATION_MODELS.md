# Simulating the data behind the mock APIs

Goal: data that *looks like what the 12 banks' APIs would return* and is good enough to develop credit-scoring / PD /
income-estimation features against. Full literature and tool survey (with URLs and verification flags): `docs/research/simulation_models.md`.
This page is the decision record and the description of what was built.

## 1. Verdict on existing tools

No open-source simulator fits. PaySim (mobile money, GPL-3, Java), BankSim, Sparkov (US cards), AMLSim and AMLworld (AML; AMLworld releases data but
*not* its generator) emit neither Berlin-Group objects, nor GEL, nor Georgian merchants/remittances, nor loan schedules with delinquency.
Generic tabular synthesizers (SDV/CTGAN/TVAE/copulas) can sample the *static* columns but cannot keep a ledger consistent (balance reconciliation,
salary ↔ declared income, instalment ↔ debit). Licence note from the survey: SDV, CTGAN, `copulas`, RDT, SDGym are **BUSL-1.1**, not MIT; SDV 2.0.0
shipped 2026-10-15 with breaking changes (pin `sdv<2` if used). `Faker` has a `ka_GE` locale usable for names/jobs only (its IBAN/phone/ID output is wrong);
`mimesis` has no Georgian locale.

Reusable plumbing worth knowing: Berlin Group OpenAPI 1.3.11 (CC BY 4.0) + the Georgian XS2A guide as the *output contract*; `holidays` (has `GE`),
`schwifty` / `python-stdnum` (IBAN), `sdmetrics` / `pandera` (distribution and schema checks), `lifelines` (hazard fitting), Berka/PKDD'99 (real Czech bank:
loans linked to transactions) as a structural reference. None of these is required by the mock; the generator uses only `numpy` + `pandas`.

## 2. What was built: a layered, mechanistic simulator calibrated to your data

`obmock/world/simulate.py` (deterministic per `--seed`; about 0.1 CPU-seconds per customer × 12 months, ~600 ledger entries each). `obmock/pipeline.py` shards the population and runs the shards in parallel: 50,000 customers, 29 M ledger entries and 38.6 M API rows take ~10 minutes on 16 cores, and the result is identical for any worker count because a customer depends only on (config, its plan, the FX series).

```
L1 population   bootstrap rows of Data/df_*.csv (without replacement)  -> persona + loan terms
L2 latent       stress ∈ [0,1] = 0.65·rank(PD) + 0.15·min(1, maxDelinqDays/90) + 0.20·addNegStatus
L3 products     primary bank by quota allocation of the sourced market shares (BoG 46 / TBC 32 / ...), 30% a 2nd bank, 3% a 3rd;
                current/FX/credit-card/savings/loan accounts, cards (debit Mastercard 66 / Visa 34)
L4 income       salary on the customer's pay day, remittances, business turnover (sole proprietors)
L5 spend        17 categories: Poisson counts × lognormal amounts, weekday + payday seasonality, liquidity feedback
L6 credit       annuity schedule back-cast from RemainingTerm; per-instalment payment plan from the dataset's delinquency fields
L7 views        one ledger -> 7 bank dialects (obmock/dialects)
```

### 2.1 Population (from `Data/df_*.csv`, 100k loan rows, 58 columns)
Each customer is one sampled row. Mapping: `Age`→birth date, `SEX` (1=male, 2=female, **ASSUMED**), `CLIENT_GEOGRAPHICAL_REGION`→region/city (labels **ASSUMED**),
`CLIENT_LEGAL_FORM=1`→sole proprietor (`usage ORGA`, turnover instead of salary), `INCOME_AMOUNT_TOTAL`→monthly net income, `INCOME_AMOUNT_SOI04`→remittance income
(values < 5 are noise in the source, 49.5 % of rows, and are treated as 0), `ECONOMIC_SECTORS`→employer name, `PRODUCT_PURPOSE` {2,4,7,10,13}→consumer / instalment /
mortgage / construction / renovation loan, `DISBURSED_AMOUNT`/`INTEREST_RATE_NOMINAL`/`ContractTermMonth`/`RemainingTerm`→loan principal, rate, term, age.
The row itself is stored as hidden truth (`truth/customers_truth.csv`) and never appears in an API payload.
The source's internal ratios (PTI, LTV, `AVERAGE_MONTHLY_PAYMENT`) are **not** consistent with its own primitives (e.g. payment 4,912 on income 394), so the simulator derives
instalment / outstanding / overdue from the loan's amortisation instead of copying them, and caps the instalment at 60 % of income.

### 2.2 Cash-flow engine (daily loop per customer)
* **Salary**: day ∈ {5,10,15,20,25,28,30} (weights 12/22/14/8/20/10/14 %, ASSUMED), moved back to the previous business day; amount `net·N(1, σ)`, σ = 1 % (75 % of customers) or 12 % (variable pay), +30 % bonus in December for 25 %.
* **Remittances** (only where `SOI04 ≥ 5`): 1–2 inflows/month from a fixed corridor weighted by NBG's Aug-2026 inflows by source country (US, IT, DE, GR, IL, TR, ES, KZ, GB, FR, RU), provider names, USD/EUR credited to a foreign-currency account or converted with a spread at the NBG official daily rate (vendored in `specs/reference/`).
* **Fixed outflows**: rent (35 % of non-mortgage customers; 12 % with a mortgage), utilities with winter seasonality (gas ×3.4, electricity ×1.5), mobile/internet, loan instalment, credit-card repayment, savings transfers, monthly fees.
* **Discretionary spend**: for category *c*, daily count `~ Poisson(λ_c · (inc/2700)^0.12 / 30 · weekday_c · payday_phase)` and amount `median_c · (inc/2700)^ε_c · scale · LogNormal(0, σ_c)`. Merchants are Georgian chains (Carrefour, Agrohub, Nikora, Wolt, Glovo, Bolt, GPC, Wissol…); foreign merchants carry `currencyExchange`. Card entries settle T+1/T+2 (so the newest ones are `pending`).
* **Budget controller** (monthly): `scale ← scale · clip(1 + 0.25·(balance − buffer)/income, 0.5, 1.6)` with `buffer = income·(0.25 + 1.2·(1 − stress)²)`; customers also spend less when the account is low. This keeps balances stationary (median end balance ≈ 0.4 × monthly income; 16 % of primary accounts end negative, 4 % of time negative) and makes **stress observable in the transactions**: stressed customers run lean accounts, use gambling merchants (`λ = 0.05 + 3·stress²`), overdraft fees, micro-loan inflows/outflows (+18 %), and ATM cash.
* **Multi-bank flows**: 30 % of customers hold a second bank, 10 % a third. If the loan sits at a secondary bank the instalment is funded by an *own-account transfer* from the primary bank the day before (two legs, same `endToEndId`).

### 2.3 Credit and delinquency
Annuity `P·r/(1−(1+r)^−n)`, schedule back-cast so `start = as_of − (n − RemainingTerm/30.4)` months. The dataset's delinquency fields become a per-instalment plan:
`overdueInstalmentsCount` → the last *k* due instalments are unpaid; `TotalNumberOverdueInst`/elapsed instalments → probability that an earlier instalment was paid late; lag
`~ Geometric(1/avg_lag)` capped by `MaximumNumberDelinquencyDays`; penalty `min(15 %, 0.1 %/day)`. 15 % of loans sit at a bank outside the customer's AIS scope: their instalments appear as outgoing transfers to a named lender but no loan resource exists.
Result on the default run: loans that are overdue in the simulation belong to customers with mean source PD **79 %** vs **11 %** for the others — the
transaction history carries the signal the registry labels describe.

### 2.4 Determinism and scale
Per-customer RNG `default_rng([seed, 100+i])`; ids via UUIDv5; integer minor units. `--customers`, `--months`, `--seed`, `--as-of` change the world; same inputs ⇒ identical bytes.

## 3. Validation (`python -m obmock validate`, `pytest`)
Ledger: IBAN mod-97 + uniqueness; running balance = opening + Σ booked; opening + month's entries = `closingBooked`; no entry before account opening; booked ≤ as_of < pending; own-transfer legs net to zero; loan debits = paid instalments;
median salary credit within 10 % of declared net income; credit-card and deposit balances within limits. API: every response of every bank validated with `jsonschema` against that bank's own OpenAPI (spec defects normalised, see `BANK_ANALYSIS.md` §4); every documented route returns a success status.

## 4. Known limits (not modelled)
Public holidays and Georgian pay-date seasonality; pensions and benefits; refunds, chargebacks, declined authorisations; ATM fees; payroll-linked loans; transfers *between simulated customers* (counterparties are random third parties with valid IBANs);
realistic merchant geography; FX beyond USD/EUR/GBP AR(1); business accounts beyond turnover + 1 % tax; intraday balances. The default 12-month window is shorter than the loans' histories, so older instalments exist only in the schedule.

## 5. If you need more realism (ranked)
1. Replace assumed constants with registry-estimated ones: roll-rates from consecutive snapshots (check whether `df_1..10` are independent samples or snapshots of one book), PD horizon, and whether `INCOME_AMOUNT_TOTAL` is gross or net.
2. Swap the independent bootstrap for a Gaussian/vine copula over the primitive columns (`statsmodels`, `pyvinecopulib`) if you need more than 100k customers; always run a distance-to-closest-record privacy check.
3. Add a Vasicek common factor for default clustering and a mover–stayer chain for delinquency transitions (recipes in `docs/research/simulation_models.md` §c).
4. Calibrate narratives and pay dates against real statements from consenting colleagues.

## 7. Georgia calibration and scale (added with the 50,000-customer dataset)

* **Where the numbers come from.** `obmock/calibration_sources.py` lists every calibrated number with its status (VERIFIED / DERIVED /
  UNVERIFIED / ASSUMED) and source; `docs/CALIBRATION.md` renders it, and `python -m obmock report` measures the generated data against it.
* **Market structure.** BoG 46% + TBC 32% = 78% of customers by primary bank (mean of NBG's loan, asset and individual-deposit shares for the
  two banks); the other ten banks share 22% by loan share, adjusted by judgement. Quota allocation makes the shares exact.
* **Georgian calendar.** Interbank credits and salaries book only on business days (vendored 2025-26 public holidays); card purchases book 1-2
  business days after the purchase; salaries due on a weekend or holiday are paid the previous business day (direction ASSUMED).
* **Fees.** TBC and BoG charge GEL 1/month for SMS (60% of customers keep it: ASSUMED share); GEL transfers to another bank cost GEL 1 / 2 / 5 / 50 by amount.
* **Spend mix.** Category frequencies are tuned so the simulated spend shares match Geostat's 2025 household expenditure, renormalised over the
  six groups modelled (food, transport, housing/utilities, health, clothing, durables).
* **Money.** Deposit rate levels follow NBG's July-2026 averages; term deposits are half GEL, half foreign currency (USD 79% of FX).
* **By design not realistic.** Default behaviour is driven by the df labels (6-7% of customers have an overdue loan versus 1.3-1.5% real retail
  NPL); incomes run ~1.4x above Geostat's mean wage; loans are GEL-only; every customer is a borrower.
