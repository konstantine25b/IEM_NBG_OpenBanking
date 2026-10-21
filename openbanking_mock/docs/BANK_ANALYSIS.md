# Georgian Open Banking APIs — analysis of the 12 NBG-listed banks

Source list: <https://nbg.gov.ge/en/page/open-banking-detailed-data-and-documentation> (12 banks, each with an
"API Documentation" and a "KPIs" link). Research date: 2026-10-20. Every claim below is traceable to a file under
`specs/` (hash-listed in `specs/MANIFEST.tsv`) or to a note under `docs/research/`. Where something could not be
verified it says **UNVERIFIED**.

## 1. The twelve banks collapse into seven wire dialects

There is one national profile and a handful of implementations. The profile is the **Georgian XS2A Implementation
Guide 0.8.RC3** (Georgian Banking Association / NBG, based on Berlin Group NextGenPSD2 1.3.x; <https://openfinance.ge/en/standards>).
"0.8" is the *profile* version, not a Berlin Group release. Loans/savings ("v2") were added by NBG in Feb 2025.

| Dialect (mock key) | Banks | How the contract was obtained | Evidence level |
|---|---|---|---|
| `tbc` | **TBC** | per-operation OpenAPI fragments scraped from the ReadMe portal (4 assembled files; TBC disables OAS download) + 46 published response examples | spec (reconstructed) + examples |
| `bog` | **Bank of Georgia** | OpenAPI 3.0.1 (NextGenPSD2 1.3.9, base `/0.8`), 40 ops | spec |
| `altasoft_v08` | **TeraBank**, **Liberty** | AltaSoft "PSD2 Developer Portal" `/swagger/swagger.yaml`, v0.8, 59 ops | spec |
| `altasoft_v2` | **Silk**, **Cartu**, **PASHA** | same portal product, v2.0, 169 ops (adds loans, savings, cards, `/openfinance/v2` payments). PASHA's file is byte-identical to Cartu's | spec |
| `obhome` | **Credo**, **Halyk**, **Basisbank** | one shared Berlin Group 1.3.9 YAML (23 ops) served by the "OB Home" portal; hash-identical for all three | spec |
| `procredit` | **ProCredit** | OpenAPI inlined in a Redocly export (12 ops, **examples only, no schemas**) | examples |
| `paysera` | **Paysera** | no OpenAPI is published; 25 hand-written endpoint pages for "Open Banking for Georgia v0.8" | doc-only |

Two groups are *the same contract*: Silk ≡ Cartu ≡ PASHA (diff = whitespace and example text) and TeraBank ≈ Liberty
(exactly one field differs: `AccountDetails.owner` vs `ownerName`). The mock reproduces both facts.

## 2. Bank-by-bank matrix

| Bank | Vendor / stack | Base URL(s) | Auth & signing | Accounts | Card accts | Loans | Savings | Payments | Published KPIs (AIS ms / uptime / error) |
|---|---|---|---|---|---|---|---|---|---|
| TBC | in-house, ReadMe docs | test `https://test-openbanking.tbcbank.ge/0.8`, prod `https://openbanking.tbcbank.ge/0.8` (loans/savings servers have **no** `/0.8`) | mTLS (`tls_client_auth`), OAuth2 code+PKCE S256, `X-JWS-Signature`+`Digest`; `PSU-IP-Address` mandatory | yes (multi-currency `XXX`) | yes | **yes, thin** (v2) | yes (v2) | v1 + 21 v2 (deferred/recurring/secured) | 597–969 / 97.9–99.6 % / 0.1–0.6 (unit unstated) |
| Bank of Georgia | in-house, Redocusaurus | `https://xs2a-sandbox.bog.ge/0.8` | OAuth bearer | yes (+tx detail) | no | no | no | v1 generic | 297 / 99.95 % / 0.049 % |
| TeraBank | AltaSoft | `psd2-sandbox{1,2}.altasoft*.ge` (spec); portal `portal-ob.terabank.ge` | Bearer apiKey | yes | yes | no | no | v1 + periodic | no feed |
| Liberty | AltaSoft | same sandbox hosts; portal `openbanking-sandbox.lb.ge` | Bearer | yes | yes | no | no | v1 + periodic | no feed |
| Silk | AltaSoft v2 | same | Bearer | yes | yes (+`/v2/cards`) | yes (+tx) | yes (+tx) | v2 + `/openfinance/v2` | portal `/Metrics` |
| Cartu | AltaSoft v2 | same | Bearer | yes | yes | yes | yes | v2 + openfinance | portal `/Metrics` |
| PASHA | AltaSoft v2 | prod base unpublished | Bearer | yes | yes | yes | yes | v2 + openfinance | `/Metrics` empty |
| Credo | "OB Home" (Banking Association package) | prod `https://openbankgw.credo.ge/0.8/v1`, sandbox `.../sandbox/0.8/v1`, OIDC under `/ob-oidc` | mTLS (QWAC) + OAuth2/OIDC + JWS (`x-jws-signature`) | yes | yes | KPI feed has Loans/Savings columns, **API unverified** | idem | generic (9 ops) | 189 / 99.99 % / 0.017 % |
| Halyk | OB Home | `openbankgw.hbg.ge` | idem | yes | yes | idem | idem | idem | **2993** / 99.997 % / 0.126 % |
| Basisbank | OB Home | `openbankgw.bb.ge` | idem | yes | yes | idem | idem | idem | 1718 (PIS 3640) / 99.994 % / 0.146 % |
| ProCredit | Azure APIM + Redocly | test `https://online-test.procreditbank.ge/0.8/v1`, prod `https://api.procreditbank.ge/0.8/v1` | APIM `Ocp-Apim-Subscription-Key` + **mandatory** JWS/`Digest`; OAuth via `tkn.24-7.ge` | yes | no | no | no | 4 ops | 903 (PIS 5394) / 99.93 % / 0.25 % (2024 xlsx) |
| Paysera | in-house, Docusaurus | prod only `https://open-banking-api.paysera.com/xs2a/georgia/0.8/v1` | mTLS (QWAC) + OAuth2 PKCE, **no signing**; no sandbox | yes | no | no | no | single domestic/foreign; cancellation unsupported | 99.85 % / 90 days |

Operation counts: TBC 71 catalogued (12 data reads, 14 consent, 4 OAuth, 13 v1 + 21 v2 payments, others), BoG 40, Tera/Liberty 59,
Silk/Cartu/PASHA 169, OB Home 23, ProCredit 12, Paysera 25 pages / 16 functional.

## 3. What differs between dialects (what the mocks reproduce)

* **Routes** are copied verbatim: version prefixes (`/0.8/v1/…`, `/v2/…`, `/openfinance/v2/…`), TBC's
  `/v2/loans{resourceId}/balances` (missing slash, *served exactly as published* plus an unpublished slashed alias),
  AltaSoft v2's inconsistent `/v2/consents/account-access` vs `/v2/consents/{id}/authorisations`.
* **Consent header**: `Consent-ID` everywhere except TBC loans/savings (`consentId`) — both accepted.
* **Errors**: TBC returns RFC-7807-style `ApiProblemDetails` (`code,traceId,type,title,status,detail,instance`); BoG, AltaSoft, OB Home,
  Paysera use Berlin Group `tppMessages`; ProCredit's APIM layer answers `{statusCode,message}` for a missing subscription key.
* **Field style**: flat Berlin-Group (`creditorName`, `debtorAgent:"BIC"`) for TBC/BoG/AltaSoft v0.8/OB Home/ProCredit/Paysera; nested
  (`creditor{name}`, `creditorAgent{financialInstitutionId}`, `remittanceInformationUnstructured[]`, `cardTransaction`) for AltaSoft v2.
* **Amounts**: strings (`"-579.07"`) except ProCredit (JSON numbers) and TBC loan balances (numbers).
* **Names**: TBC example data is Georgian script; the mock uses Georgian script for TBC and Latin elsewhere (**ASSUMED**).
* **TBC identifiers**: `ownerIdentification.privateId.others[].identification = "PNOGE-<11 digits>"`, multi-currency accounts as
  repeated IBAN + `currency:"XXX"` aggregate, `cashAccountType:"CARD"`, proprietary codes `GMI`/`GMB`, tx ids `c_`/`d_` + 11 digits.
* **Paging**: Georgian guide = pages of ≤ 50 with `_links.first/next`, `entryReferenceFrom` strictly-after. AltaSoft pages with an opaque
  base64 `state=` token (PascalCase JSON); Paysera with `offset` and root-level `_links`.
* **Balances**: guide requires `closingBooked`+`interimAvailable` on `/balances`, `interimAvailable`+`interimBooked` embedded; Paysera
  supports only `interimAvailable`+`interimBooked`; ProCredit omits `referenceDate`.
* **Loans**: TBC's published loan schema has *no* instalment/overdue/arrears fields (only start/end, interest %, balances) — its mock
  therefore hides delinquency exactly as the real API would. AltaSoft v2 models a loan as an `AccountDetails` (`cashAccountType LOAN`,
  `interest[]`, `relatedDates[]`, `/transactions`); the mock adds `closingBooked`, `expected` (next instalment) and an `information` balance
  with `balanceSubTypeProprietary: OVERDUE_AMOUNT` (**the sub-type value is invented; the schema allows free text**).
* **Not enforced**: mTLS, QWAC/QSeal certificates and JWS signatures are *accepted, not verified* (no trust anchors). ProCredit's APIM
  key presence is enforced on data calls.
* **KPIs**: banks publish EBA/OBIE KPIs (uptime, downtime, PISP/AISP/CBPII latency, error rate). The mock's `--chaos kpi` samples
  latency/failures from each bank's real numbers.

## 4. Spec defects found (all handled in `obmock/validate.py`, none hidden)

1. AltaSoft models string wrappers (`Max70Text`, `StrAmount`, `CurrencyCode`, `IbanIdentifier`…) as **objects with a `length` property** and
   `ISODate`/`ISODateTime` as empty objects; the published examples show plain strings. Validation maps them to `string`.
2. AltaSoft `AccountDetails` marks `iban` **and** `other` required; the published example has neither. The v2 mock emits both.
3. TBC schemas declare `links` but every example uses `_links` (18 of 466 TBC examples fail TBC's own schemas); TBC `creditLimit` is an
   amount *string*; TBC `v2` consent responses use `validTo`, v1 `validUntil`.
4. OB Home YAML: payment-product enum is SEPA (`sepa-credit-transfers`…) but the Georgian guide requires `aspsp|domestic|foreign`; spec paths carry no `/v1`.
   The `/ob-documents/` copy is not even valid YAML (a comment inside a plain scalar) and lacks the card-account operations.
5. ProCredit's spec has no schemas; `instructedAmount` uses `value` in the request but `amount` in the response.
6. Paysera's NBG-listed URL (`/en/open-banking/v0.8/api-spec`) is 404; docs contradict themselves on cancellation and `combinedServiceIndicator`.
7. Basisbank's NBG link is malformed (`https:/openbank.bb.ge/ob-home/app/api-doc%20`); TBC's loans balances path is missing a slash.

## 5. Identifiers (verified against the NBG IBAN table, <https://nbg.gov.ge/en/payment-system/iban>)

| Bank | IBAN code | BIC | | Bank | IBAN code | BIC |
|---|---|---|---|---|---|---|
| TBC | TB | TBCBGE22 | | Silk | **BT** | **DISNGE22** |
| Bank of Georgia | BG | BAGAGE22 | | Cartu | CR | CRTUGE22 |
| TeraBank | **KS** | **TEBAGE22** | | PASHA | PB | PAHAGE22 |
| Liberty | LB | LBRTGE22 | | Credo | CD | JSCRGE22 |
| Halyk | HB | HABGGE22 | | Basisbank | BS | CBASGE22 |
| ProCredit | PC | MIBGGE22 | | Paysera | PS | PSRAGE22 (NBG only) |

Georgian IBAN = `GE` + 2 check digits + 2-letter bank code + 16 digits (22 chars). The 16-digit body layout is bank-specific and **UNVERIFIED**;
the mock uses `<code><product digit><15 random digits>` with valid mod-97 check digits.

## 6a. Validation results (default world, `python -m obmock validate`; `pytest` runs the same checks)

| Bank | responses validated | violations (lenient) | strict-mode findings | note |
|---|---|---|---|---|
| TBC | 187 | 0 | **146** | all are TBC's own spec defects: schema says `links`/forbids extra properties but the published examples use `_links` and `ownerIdentification` (see §4.3) |
| Bank of Georgia | 68 | 0 | 0 | |
| TeraBank / Liberty | 103 / 109 | 0 | 0 | |
| Silk / Cartu / PASHA | 105 / 149 / 171 | 0 | 0 | required `iban`+`other` both emitted |
| Credo / Halyk / Basisbank | 107 / 114 / 117 | 0 | 0 | |
| ProCredit | 54 | 0 | 0 | schemas are *inferred from one example* (not bank-published) |
| Paysera | – | – | – | no machine-readable spec exists |

"Lenient" drops `required`/`additionalProperties` (spec defects make them unusable verbatim); "strict" keeps them. Strict is 0 for every bank except TBC, i.e. no mock
omits a field its bank's schema marks required. Validation also covers consent and payment flows, and checks TBC's loan balances against the *published* (typo) path.
Independent of schemas, `tests/test_inventory.py` asserts that **every** method/path a bank publishes — all 66 unique operations of the 71-record TBC catalogue you pasted,
every `paths` entry of each spec, Paysera's 25 pages — resolves in that bank's router, and `tests/test_routes.py` calls every registered route with resolved ids.
(Two gaps were found this way and fixed: AltaSoft v2's stray-segment extended-payment DELETE and Paysera's documented-but-unsupported cancellation endpoints, which answer 405.)

## 6b. Known mock limits
Mutating calls (payments, consents) change in-memory state only and reset on restart; executed payments are appended to the ledger with a fresh, stable `entryReference`.
Signatures/certificates are not verified. The same debit card appears under `/accounts` and `/card-accounts` (as at the real banks); see the README pitfall.

## 6. Assumptions and gaps (read before trusting a number)

* Merchants, narratives, multi-banking rates, pay-day mix and the region-code → name mapping are **ASSUMED** (marked in `obmock/reference.py` and
  `obmock/banks.py`); market shares, remittance corridors, deposit rates, FX rates and card statistics are sourced (see `docs/CALIBRATION.md`). Region code 3 ≈ Tbilisi is inferred from its 49 % share only.
* Real **sandbox test data** (accounts/consents) is not published by any bank except the OB Home demo login (`testclient`/`testclient`/OTP `111111`, not used).
* **Loans/savings** responses of banks other than TBC/AltaSoft v2 are not specified anywhere public; whether Credo/Halyk/Basis expose them is **UNVERIFIED**.
* Pagination page size for TBC and the AltaSoft banks is **UNVERIFIED** (Georgian guide says ≤ 50; Paysera documents exactly 50).
* The official loan/deposit data model of the updated Georgian standard sits behind a SharePoint login (HTTP 403) — ask `ob@nbg.gov.ge`.
* TBC's KPI table has no September-2026 row and unit-less error rates; ProCredit's KPI file dates from 2024.

## 7. What a TPP can actually retrieve from the two priority banks (field-level contract)

Basis of the API-only dataset (`api/` tables). Evidence tiers: **S** schema, **E** only in a published example, **G** Georgian guide (second-hand).

| | Bank of Georgia | TBC |
|---|---|---|
| Account data endpoints | `/v1/accounts`, `{id}`, `/balances`, `/transactions`, `/transactions/{id}` (S) | same minus `/transactions/{id}`; plus `/v1/card-accounts*`, `/v2/loans*`, `/v2/savings*` (S) |
| Cards, loans, savings | none (S) | card accounts with transactions; loans and savings with **balances only**, no transactions endpoint (S) |
| Transaction fields | creditor/debtor name, account, agent, ultimates, remittance (unstructured/structured), `purposeCode`, `bankTransactionCode`, `proprietaryBankTransactionCode`, `mandateId`, `balanceAfterTransaction`, `currencyExchange[]`, `additionalInformation` (S) | same set without `ultimateCreditor`, batch fields, `entryDetails`; `ownerIdentification` (PNOGE id) only in examples (E) |
| Not in any response | MCC, merchant city/address, geolocation, device, channel (POS/ECOM/ATM), card scheme, expiry | same; TBC loans also have no instalment, next payment, arrears, schedule or IBAN (S) |
| Codes published | none for `bankTransactionCode`/`purposeCode` | `proprietaryBankTransactionCode` `GMI` (credit) / `GMB` (debit) on two example transfers and card entries (E) |
| FX accounts | currency sub-accounts under one IBAN (spec examples) | one IBAN, `XXX` aggregate + GEL/USD/EUR children (E) |
| Booking rules | `bookingStatus` required; history limit unstated | `bookingStatus` optional; consent max 90 days (S); history limit unstated |

Choices the mock makes where the banks are silent (all listed in `docs/CALIBRATION.md` as ASSUMED): which ISO 20022 code each transaction kind
gets, the wording of statement lines, and that optional schema fields (`purposeCode`, `balanceAfterTransaction`...) are populated in the default
`spec-complete` profile; `--profile evidence-only` drops them.
