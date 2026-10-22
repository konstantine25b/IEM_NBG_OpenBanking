# Georgian Open Banking mock (12 banks)

Mock PSD2/XS2A servers for the 12 banks listed on the NBG page
<https://nbg.gov.ge/en/page/open-banking-detailed-data-and-documentation>, backed by **one simulated population** so that the
same customer looks consistent across banks and across API calls. Everything is synthetic.

* `docs/BANK_ANALYSIS.md` — what each bank's API really looks like (7 wire dialects), spec defects, KPIs, what is verified / assumed.
* `docs/SIMULATION_MODELS.md` — how the data is generated and why (plus the tool survey verdict).
* `docs/research/` — raw research notes per bank and the full simulation-tool survey (with URLs and verification flags).
* `specs/` — the machine-readable specs the mocks follow, with provenance in `specs/MANIFEST.tsv` (SHA-256, source URL, fetch date).

## Quick start

```bash
cd openbanking_mock
uv run python -m obmock banks                       # the 12 banks, dialects, evidence level
uv run python -m obmock generate                    # 50,000 customers -> output/dataset (Parquet, ~10 min on 16 cores, ~2 GB)
uv run python -m obmock report                      # quality report: standards on the API tables + calibration targets
uv run python -m obmock serve --port 8000           # live server for all 12 banks (in-memory: use <= ~3,000 customers)
uv run python -m obmock validate                    # ledger invariants + every response vs. the bank's own spec
uv run pytest                                       # every documented route of every bank answers, every published
                                                    # operation is routable, fidelity + pipeline + ISO 20022 tests
```

The server resolves `nbg_synthetic_data/df_*.csv` (the supplied loan dataset) to seed customers; override with `--data-dir`.
Same `--seed/--customers/--months/--as-of` gives an identical world. The world's "today" is fixed at 2026-10-19 so runs are reproducible
(card entries of the last two days are therefore `pending` in the past); pass `--as-of today` for a live-looking clock. Useful flags:
`--auth off` (pick the customer with `X-Mock-Customer` or `?psu=`), `--chaos errors|kpi` (inject failures / latency from each bank's
*published* KPIs), `--expose-truth`. The server serialises requests with a lock (ledger and sessions are plain in-memory Python).

### Calling a bank like a TPP

```bash
S=$(curl -s -X POST localhost:8000/_mock/session/tbc)            # already-authorised consent + token
TOKEN=$(echo $S | jq -r .authorization); CONSENT=$(echo $S | jq -r .consentId)
curl -s "localhost:8000/tbc/0.8/v1/accounts?withBalance=true" \
  -H "Authorization: $TOKEN" -H "Consent-ID: $CONSENT" -H "X-Request-ID: $(uuidgen)" -H "PSU-IP-Address: 203.0.113.7"
```

The real flow also works end-to-end (consent, then `startAuthorisation` / `scaOAuth`, then `/psd2/openbanking/oauth/authorize` with PKCE S256, then
`/openbanking/oauth/token`, then data calls): see `tests/test_mock.py::test_oauth_pkce_flow_end_to_end`. Redirect-style SCA pages live at
`/{bank}/_mock/sca/...`. Payments are stateful: once authorised they debit the ledger and show up in the next `/transactions` call.

| Bank | Mock base path | Dialect | Evidence | Loans / savings / cards |
|---|---|---|---|---|
| TBC | `/tbc/0.8/v1`, `/tbc/v2` | tbc | spec (reconstructed) + examples | thin loans, savings, cards |
| Bank of Georgia | `/bog/0.8/v1`, `/bog/0.8/banking/products` | bog | spec | none |
| TeraBank / Liberty | `/terabank/0.8/v1`, `/liberty/0.8/v1` | altasoft_v08 | spec | cards |
| Silk / Cartu / PASHA | `/silk/v2`, `/cartu/v2`, `/pasha/v2` | altasoft_v2 | spec | loans (+tx), savings (+tx), cards |
| Credo / Halyk / Basisbank | `/credo/0.8/v1`, `/halyk/0.8/v1`, `/basisbank/0.8/v1` | obhome | spec | cards |
| ProCredit | `/procredit/0.8/v1` | procredit | examples only | none |
| Paysera | `/paysera/xs2a/georgia/0.8/v1` | paysera | doc-only | none |

Control plane: `GET /`, `/_mock/banks`, `/_mock/banks/{bank}/routes`, `/_mock/customers/{bank}`, `POST /_mock/session/{bank}?customer=C00012`, `/_mock/stats`.

## The dataset (`generate`)

Shares follow the Georgian market: customers are allocated to a primary bank by quota (BoG 46%, TBC 32%, Liberty 8%,
Credo 4%, Basisbank 2.5%, TeraBank 2%, ProCredit 1.5%, Cartu 1%, Halyk 1%, PASHA 0.9%, Paysera 0.6%, Silk 0.5%), so
**BoG + TBC = 78% of customers** and ~77% of API rows. Sources and their status (verified / derived / assumed) are in
`docs/CALIBRATION.md`.

```
output/dataset/
  api/{accounts,balances,transactions}/part-*.parquet   ONLY what a TPP can retrieve through the 12 APIs
  truth/{customers,accounts,ledger,loans,loan_schedule,deposits,fx_rates}/   the simulator's full state: evaluation only
  captures/<bank>/<cid>.json     raw API conversation for a sample (200 customers each of TBC and BoG, 30 of each other bank)
  MANIFEST.json  lineage.json  QUALITY_REPORT.md
```

**`api/` is built by crawling the mock APIs like a TPP and flattening the JSON bodies** (`obmock/harvest.py`). Every column
is read from a named JSON path of a bank's own response (`lineage.json`), so a value an API does not return cannot be in
these tables: no MCC or merchant city, no loan arrears or instalment for TBC, no loans/cards/savings for BoG, no
demographics. All of that lives in `truth/` (join on `truth.customers.cid = api.*.psu_id`; the ledger joins to
transactions on `(bank, account_id, entry_reference)`).

| table | one row per | notes |
|---|---|---|
| `accounts` | account resource a TPP can list | payment accounts, card accounts, loans, savings (`source_endpoint`); IBAN, currency, product, owner, masked PAN, credit limit, loan/savings dates and rate |
| `balances` | balance of such a resource (long) | `origin` = embedded in the account list or the `/balances` endpoint; Berlin Group types |
| `transactions` | booked/pending entry | creditor/debtor, remittance text, ISO 20022 `bank_transaction_code` / `purpose_code`, `balance_after_amount`, FX block, card fields where the bank exposes them |

Things to know when using `api/`:
* **Different banks expose different things** (BoG: payment accounts only; TBC adds card accounts, loans, savings; AltaSoft
  banks add loan/savings transactions; ProCredit/Paysera are thinner). Nulls mean "this bank's API has no such field".
* **Card mirrors.** TBC's own examples list a debit card under both `/accounts` and `/card-accounts`; the mock follows that
  for every bank with card-accounts. Mirrored entries share `entry_key`; `is_card_mirror=true` marks the card-accounts copy.
  Drop those rows before summing spend.
* **`XXX` aggregates** (TBC multi-currency parent) are listed in `accounts`; their balances and entries are not repeated
  because they repeat the per-currency children. FX sub-accounts share the current account's IBAN, so an account is keyed
  by `(iban, currency)`.
* Amounts are exact decimals (`decimal(18,3)`), dates are ISO 8601, a pending entry has no booking date.

Standards enforced and checked by `obmock report` over the whole dataset: ISO 13616 IBAN (mod-97, GE + 16 digits), ISO 9362
BIC, ISO 4217 currencies, ISO 20022 `bankTransactionCode` (each Domain-Family-SubFamily triple validated against the
official table vendored in `specs/iso20022/`) and `purposeCode`, Berlin Group balance types and booking rules, running
`balanceAfterTransaction`, interbank credits and card bookings only on Georgian business days (vendored 2025-26 holiday
calendar), NBG official daily FX rates (vendored). `--profile evidence-only` leaves exactly four fields empty (`bank_transaction_code`, `purpose_code`, `mandate_id`,
`balance_after_amount`), because no bank example shows them; the default `spec-complete` fills them.

`truth/` amounts are integer minor units (`*_minor`). `truth.ledger` is the full internal ledger (kind, MCC, merchant, city,
channel...) and also holds entries no API exposes (e.g. BoG credit-card spend). Do not use `truth/` as model features.

## Code map

```
obmock/banks.py              12 bank profiles (verified IBAN/BIC, dialect, KPIs, URLs, evidence, market-share weight)
obmock/reference.py          Georgian names, merchants/MCC, billers, remittance corridors, narratives, rates (flagged where assumed)
obmock/calibration.py        nbg_synthetic_data/df_*.csv loader, stress score
obmock/calibration_sources.py  every calibrated real-world number with status (verified/derived/assumed) and source
obmock/iso20022.py           simulator transaction kind -> ISO 20022 bankTransactionCode / purposeCode
obmock/world/                model.py (ledger domain) + simulate.py (plan_population, build_shard, daily cash-flow engine)
obmock/engine/gateway.py     routing, auth/consent/OAuth/payment state, KPI chaos (HTTP-agnostic)
obmock/dialects/             base.py (Georgian XS2A profile) + families.py (TBC, BoG, AltaSoft v0.8/v2, OB Home, ProCredit, Paysera)
obmock/harvest.py            API response bodies -> typed tables with column -> JSON-path lineage
obmock/pipeline.py           sharded, parallel generation: simulate -> serve through the APIs -> harvest -> Parquet
obmock/report.py             quality report (standards on api/, calibration targets on truth/)
obmock/server.py             FastAPI adapter + control plane      obmock/export.py  raw API capture (TPP crawl)
obmock/validate.py           ledger invariants + jsonschema validation of responses against specs/
specs/iso20022, specs/reference   vendored official ISO 20022 code tables, Georgian holidays, NBG daily FX rates (SHA-256 in MANIFEST.tsv)
tests/                       route exerciser, spec-inventory, fidelity, ISO 20022, pipeline determinism and standards tests
```

## What is real and what is not
**Verified from sources:** routes, version prefixes, header names, auth models, error shapes, field names and enums of each bank's published spec/examples; IBAN codes and BICs (NBG table); published KPIs; ISO 20022 code tables; NBG daily FX rates; Georgian public holidays; market shares, remittance corridors, deposit rates, card counts and the household spend mix (each with its status in `docs/CALIBRATION.md`).
**Assumed (flagged in code and docs):** statement narratives, merchants and ticket sizes, multi-banking rates, pay-day mix, which ISO 20022 code each internal transaction kind gets, the internal digit layout of IBANs, which optional schema fields TBC/BoG fill in production, the loan `OVERDUE_AMOUNT` sub-type, Georgian-vs-Latin name script per bank.
**Not enforced:** mTLS / QWAC certificates and JWS signatures are accepted but not verified. No bank publishes sandbox test data, so no mock claims to equal a real sandbox dataset.
**By design not realistic:** the supplied `nbg_synthetic_data/` is already synthetic and every customer is a borrower with a df-driven default rate far above real retail NPL (1.3-1.5%); incomes run ~1.4x above Geostat's mean wage (reported, not corrected); loans are GEL-only.
