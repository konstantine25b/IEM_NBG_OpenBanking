# JSC Paysera Bank Georgia - Open Banking API (research notes)

Collected 2026-10-20 (UTC). All downloaded content treated as untrusted data. Only anonymous GETs of public pages (plus the two public JSON
endpoints that the docs/status pages themselves fetch in the browser). No authentication attempts, no API calls to
`open-banking-api.paysera.com/xs2a/...`, no forms. Sequential, >= 2.5 s apart (site's robots.txt: `Crawl-delay: 1`).

## Bottom line

* The NBG-listed URL `developers.paysera.com/en/open-banking/v0.8/api-spec` is a 404 because the whole developer site was rebuilt
  (Docusaurus v3.10.2, no `/en/` prefix). Current Georgia documentation:
  - overview `https://developers.paysera.com/use-open-banking-georgia`
  - API reference `https://developers.paysera.com/api/open-banking-georgia` (+ 25 endpoint pages)
  - guides `https://developers.paysera.com/guides/open-banking-georgia/...` (10 pages)
  - group-wide EU equivalent: `/use-open-banking`, `/api/open-banking` (+29 endpoint pages), `/guides/open-banking/...`.
* **No downloadable OpenAPI/Swagger file is published.** Checked: sitemap.xml (445 URLs, none json/yaml), `/openapi.json` (404), `/llms.txt`
  (200, but the listed `/llms-open-banking.txt` is 404; `/llms-full.txt` is 8.7 KB and has no open-banking content), the 'paysera' GitHub org
  (115 public repos via API, none for open banking/psd2/xs2a/berlin/openapi), `developer-api.paysera.com/` (404 JSON). The Security page does say
  "These three headers appear in the OpenAPI specification ... declared `required: false`", so an internal OpenAPI spec exists but it is not linked or hosted publicly.
  The reference pages are hand-written MDX with Field/Type/Required tables, examples and error lists.
  => Substitute I built: `specs/paysera/derived/paysera_georgia_endpoint_inventory.{json,tsv}` (25 endpoints) and
  `derived/paysera_pages_extracted.json` (every page: method/URL, tables, code samples, error codes).
* **Georgia v0.8 is a separate profile from the EU v1.3 profile**, on the same host and infrastructure. Standard: NBG's Georgian XS2A guide
  ("NextGenPSD2 XS2A Implementation Guide 0.8 RC3", the glossary cites section 8.1 for the `domestic`/`foreign` products; the guide index says
  "Framework: NextGenPSD2 v1.3.9"; external link `https://openfinance.ge/en/standards`). So "0.8" is the NBG profile version, not a Berlin Group release.
  The AltaSoft v0.8 YAMLs implement the same profile, but Paysera's wire format differs in several places (section 6), so an AltaSoft-based mock is a starting point, not a drop-in.
* Paysera is **mTLS (QWAC) + OAuth2 code/PKCE, with NO request signing** - the opposite of ProCredit (signature headers mandatory) and, as I read the Georgian
  text of NBG 0.8 RC3 section 5.4 (qualified-e-seal-equivalent signing of requests AND responses), also a deviation from the published profile. The AltaSoft YAMLs declare no signature/Digest header parameters at all, so they neither require nor describe signing.
* Caveat on entity: the docs say "Georgian residents" and never name "JSC Paysera Bank Georgia"; the link between this API and that legal entity rests on the NBG listing
  (given in the task) pointing at these docs. A web search snippet also suggested an older version of the Georgia page said "MAC authentication with QWAC and QSealC" and "Georgian certification authorities" -
  the live pages now say mTLS only / "QSealC not used" (looks like a recent doc correction; unverified, search-engine summary only).

## 1. Provenance (everything under `.../scratchpad/specs/paysera/`; `raw/MANIFEST.tsv` has URL, UTC time, http code, bytes, sha256 for 76 files, all re-verified)

| group | files |
|---|---|
| index/probes | `raw/developers_root.html`, `raw/developers_sitemap.xml`, `raw/developers_robots.txt`, `raw/llms.txt`, `raw/llms-full.txt`, `raw/github_org_paysera_repos_p{1,2}.json`, `raw/probe_404/*` (old spec URL, `/en/`, `/openapi.json`, `llms-open-banking.txt`, `developer-api` root, old status URL) |
| Georgia | `raw/pages/api_open-banking-georgia_*.html` (25 endpoints), `raw/pages/page_api_open-banking-georgia.html`, `raw/pages/page_use-open-banking-georgia.html`, `raw/pages/guides_open-banking-georgia*.html` (index, getting-started, authentication, security, examples, advanced-scenarios, error-handling, performance, faq, glossary) |
| EU/Berlin comparison | `raw/pages/berlin_*.html` (overview, use page, guides incl. reports, 9 endpoint pages: create-consent, transaction list, initiate/cancel/get payment, account list/details, balances, card account list) |
| OAuth | `raw/oauth-authorization-server_metadata.json` (public RFC 8414 document, 462 bytes) |
| KPI | `raw/status_root.html`, `raw/status_root_range90.html`, `raw/status_api_public_v1_summary.json`, `raw/open-banking-performance.json`, `raw/assets/*.js` (docs bundle used to find the data URL) |
| derived | `derived/paysera_pages_extracted.json`, `derived/paysera_georgia_endpoint_inventory.{json,tsv}` (scripts in `scratchpad/tools_pc_pay/`) |

Site facts: NBG's old `/en/open-banking/v0.8/api-spec` and `/en/` both 404 with the Docusaurus "Page Not Found".
Support contacts in docs: `tech_support@paysera.com` (Open Banking), `developer-support@paysera.com` (site-wide llms.txt).

## 2. Endpoint inventory - Georgia v0.8 (25 documented, 16 functional)

Base (production only): `https://open-banking-api.paysera.com/xs2a/georgia/0.8/v1`. **No sandbox URL** (FAQ: sandbox "Work in Progress ... Coming Soon", planned
`https://open-banking-sandbox.paysera.com/xs2a/georgia/0.8/`; "Test credentials after TPP registration"). Spec title shown on pages: "Open Banking for Georgia - API Reference, Version: Georgia v0.8".

Auth class A = QWAC mTLS only; class B = mTLS + `Authorization: Bearer` + `Consent-ID`.

| method path | auth | status |
|---|---|---|
| POST `/consents` | A | supported |
| GET `/consents/{consentId}` ; GET `/consents/{consentId}/status` ; DELETE `/consents/{consentId}` | A | supported |
| GET `/consents/{consentId}/authorisations` ; GET `.../authorisations/{authorisationId}` (SCA status) | A | supported |
| POST `/consents/{consentId}/authorisations` ; PUT `.../authorisations/{authorisationId}` | A | page says NOT supported ("with redirect over OAuth, the authorisation is created together with the consent") |
| GET `/accounts` ; GET `/accounts/{account-id}` ; GET `/accounts/{account-id}/balances` | B | supported |
| GET `/accounts/{account-id}/transactions` ; GET `.../transactions/{transactionId}` | B | supported (list: 50/page) |
| POST `/payments/{payment-product}` (domestic, foreign) | A | supported (single payments only) |
| GET `/payments/{p}/{paymentId}` ; GET `.../status` ; GET `.../authorisations` ; GET `.../authorisations/{authorisationId}` | A | supported |
| POST `.../{paymentId}/authorisations` ; PUT `.../authorisations/{authorisationId}` | A | NOT supported (same reason) |
| DELETE `/payments/{p}/{paymentId}` ; GET/POST `.../cancellation-authorisations` ; GET/PUT `.../cancellation-authorisations/{authorisationId}` | A | NOT supported; page: "rejected with 405 SERVICE_INVALID" |

Absent vs Berlin v1.3 profile: all 4 card-account endpoints (list, details, balances, transactions), plus (also absent in both) funds-confirmation, signing baskets, trusted beneficiaries, standing orders, bulk/periodic payments, loans/savings/products endpoints.
No `/funds-confirmations`. No `{payment-service}` path segment is documented (only `/payments/{product}`).

## 3. Auth and headers

* Every request: QWAC client certificate over TLS 1.2+ (1.3 recommended), registered with Paysera; `X-Request-ID` (UUID; non-UUID => 400 FORMAT_ERROR). 403 if QWAC revoked/invalid/not recognised.
* Account-data endpoints additionally: `Authorization: Bearer <token>` and `Consent-ID` (missing => 400 `FORMAT_INVALID`).
* Consent and payment endpoints (create, read, status, authorisations) need **only the certificate** - no token, no Consent-ID.
* No signing: `Digest`, `x-jws-signature`, `TPP-Signature-Certificate` are never read, never rejected, "declared `required: false`"; QSealC not used.
* OAuth2 (one authorisation server for all profiles) - metadata at the host root `https://open-banking-api.paysera.com/.well-known/oauth-authorization-server` (fetched):
  `issuer https://open-banking-api.paysera.com`, `authorization_endpoint /oauth/authorize`, `token_endpoint /oauth/token`, `jwks_uri /.well-known/jwks.json`,
  `scopes_supported [profile, AIS:<consentId>, PIS:<paymentId>, PSP_AI, PSP_PI]`, `response_types_supported [code, token]`, `response_modes_supported [query]`,
  `grant_types_supported [authorization_code, refresh_token]`, `code_challenge_methods_supported [S256]`. (Relative URLs in a metadata document - non-standard but real.)
* Flow: create consent/payment (201 returns `_links.scaOAuth.href` = `/.well-known/oauth-authorization-server`, header `ASPSP-SCA-Approach: REDIRECT`, no `scaRedirect`) -> read metadata ->
  redirect PSU to `authorization_endpoint` with `response_type=code&client_id&redirect_uri&scope=AIS:<consentId>|PIS:<paymentId>&state&code_challenge&code_challenge_method=S256` ->
  `POST /oauth/token` (form: authorization_code, code, redirect_uri, client_id, code_verifier) -> `{access_token, token_type:"Bearer", expires_in:3600, refresh_token, scope:"PSP_AI AIS:<id>"}`.
  Token 1 h; refresh token 1 month, rotated; PIS needs no token (certificate only). One scope per authorisation. Authorisation link valid 15 min from creation of the consent/payment; late confirm =>
  redirect with `error=access_denied` + `state`; consent becomes `rejected`, payment `RJCT`.
* Decoupled/embedded SCA: not supported. Multilevel SCA: "supported" in the matrix.
* Authorize-page language param `lng=` supports en, lt, lv, ee, ru, pl, de (**no `ka`**) - FAQ.

## 4. Schema highlights and Georgia-specific vs group-wide

Documented Georgia items for mocking (examples are from the pages; amounts are strings):
* Consent create body (table: all 5 marked required): `access` (object: `accounts`/`balances`/`transactions` arrays of `{iban,currency?}`; example also shows `maskedPan` although the guide says MaskedPan is not supported), `recurringIndicator` (table: boolean; **example sends string `"true"`**), `validUntil` (ISO date, "9999-12-31" allowed, may be adjusted), `frequencyPerDay` (integer, default max 4, higher by request; excess => 429 `ACCESS_EXCEEDED`), `combinedServiceIndicator` (table: required; guide matrix and error list: unsupported / `SESSIONS_NOT_SUPPORTED`; the Georgia example omits it).
  201: `{"consentStatus":"received","consentId":"<uuid>","_links":{"self","status","scaStatus","scaOAuth"}}` (all four present in the sample). Consent statuses: `received, valid, rejected, expired, revoked, terminatedByTpp`.
* Payment body (`POST /payments/domestic|foreign`, identical validation): `instructedAmount{currency (any ISO 4217), amount:"123.50"}`, `creditorAccount{iban}` (IBAN only), `creditorName` (required, <= 70 chars), optional `debtorAccount{iban}` (if omitted PSU picks at SCA), `remittanceInformationUnstructured`, `endToEndIdentification`, `creditorAgent` (BIC), `creditorAgentName`, `creditorAddress`. Standard (non-instant) transfers, no automatic currency conversion; bulk/periodic/instant unsupported. 201: `{"transactionStatus":"RCVD","paymentId":"<uuid>","_links":{"scaOAuth","self","status","scaStatus"}}`. GET payment omits keys that carry no value. `transactionStatus` codes listed: `RCVD, PDNG, ACTC, ACCP, ACSC, ACSP, ACWC, ACWP, PART, PATC, RJCT, CANC` (**no `ACCC`** although NBG 0.8 RC3 defines it and ProCredit returns it).
* Accounts: `{"accounts":[{"resourceId":"<uuid>","iban":"GE00BG...","currency":"GEL","product":"Current Account","cashAccountType":"CACC","name":"Main Account","_links":{balances,transactions}}]}`; details add `ownerName` (supported; `psuName` not).
* Balances: `balanceType` `interimAvailable` + `interimBooked` only (closingBooked/expected/openingBooked/forwardAvailable explicitly unsupported), each with `balanceAmount{currency,amount:"500.00"}`, `referenceDate`, `lastChangeDateTime` (`2026-06-17T14:05:32+03:00`, +03 offset).
* Transactions: query `bookingStatus` REQUIRED (`booked|pending|both`; `all`/`information` => 400 `PARAMETER_NOT_SUPPORTED`), `dateFrom`, `dateTo`, `offset` (integer); `entryReferenceFrom`, `deltaList`, `withBalance` => 400. Response: `{"account":{iban},"transactions":{"booked":[...],"pending":[...],"_links":{"account"}},"_links":{"first","next"}}` (requested collections always present, empty array if none). Items: `transactionId`, `creditorName|debtorName` + account, `transactionAmount{currency,amount:"-256.67"}` (signed string), `bookingDate`, `valueDate`, `remittanceInformationUnstructured`, `bankTransactionCode` (optional), `proprietaryBankTransactionCode` (e.g. `transfer:internal`, `transfer:ge_nbg_rtgs`; never null, omitted when unknown). **Pagination: fixed page size 50, follow `_links.next` (root-level, `offset=50,100...`), no page-size parameter; every other list returns everything.**
* Errors: `{"tppMessages":[{"category":"ERROR","code":"...","path":"...","text":"..."}]}` with codes FORMAT_ERROR, FORMAT_INVALID, PARAMETER_NOT_SUPPORTED, PERIOD_INVALID, SERVICE_INVALID, SESSIONS_NOT_SUPPORTED, CONSENT_UNKNOWN, CONSENT_EXPIRED, CONSENT_INVALID, CERTIFICATE_INVALID/MISSING/EXPIRED, ROLE_INVALID, TOKEN_INVALID/EXPIRED/UNKNOWN, RESOURCE_UNKNOWN, ACCESS_EXCEEDED (429), INTERNAL_SERVER_ERROR, INVALID_TPP_CONFIGURATION, SERVICE_UNAVAILABLE (503), REQUEST_TIMEOUT (408), RESOURCE_CONFLICT (409). Statuses on all 25 endpoint pages: exactly 400,401,403,404,405,429,500 (503/408/409 appear only in the error-handling guide). Machine-readable 429 `Retry-After` is mentioned in the guide only.
* Limits/timeouts matrix (Georgia use page / guide): max frequency 4/day; SCA validity current consent 90 days (EU: 180); one-off 1 hour; consent establishment timeout 1 day; payment initiation timeout 15 minutes; authorisation link 15 min; access token 1 h; refresh 1 month. FAQ-only (Berlin-copy, not trustworthy for Georgia): reset at midnight CET, PSU-active session ~5 min, consent default 90/max 180 days.
* Conformance matrix from the Georgia guide (supported / not): supported - dedicated + bank-offered consent, global `allAccounts` and `allPsd2`, multilevel SCA, multicurrency (aggregation and sub-account levels, though "aggregation & sub-account" combined not), ownerName, PIS without IBAN, IBAN identifiers. NOT supported - multiple consents, signing baskets, card accounts, psuName, trusted beneficiaries, `withBalance`, `combinedServiceIndicator`, MaskedPan/BBAN, global consent with balances or owner names, standing orders, `entryReferenceFrom`/`deltaList`, `TPP-Rejection-NoFunds-Preferred`, implicit funds check in status, status notifications, mandatory auth on cancellation.

What is Georgia-specific vs group-wide (evidence = text diff of 8 Georgia pages vs the Berlin pages, which differ only in the items below):

| aspect | Georgia (v0.8) | EU (Berlin v1.3) | evidence |
|---|---|---|---|
| base URL | `/xs2a/georgia/0.8/v1` | `/xs2a/berlin/1.3/v1` | both use host `open-banking-api.paysera.com`, same OAuth metadata, same auth model |
| payment products | `domestic`, `foreign` | `sepa-credit-transfers`, `instant-sepa-credit-transfers`, `target-2-payments`, `domestic-ron-transfers` | initiate/cancel/get-payment pages |
| card accounts | none | 4 endpoints | endpoint lists |
| SCA validity (current consent) | 90 days | 180 days | use pages |
| certificates wording | "recognised in Georgia" | "trusted CA" | use pages |
| currency/IBAN examples | GEL, `GE00BG...` | EUR, DE/FR IBANs | page diffs |
| proprietary tx code example | `transfer:ge_nbg_rtgs` | `transfer:lt_lb` | transaction-list diff |
| error model, headers, pagination (50, offset/next), frequencyPerDay 4, 15-min PIS timeout, 1-day consent timeout, mTLS/no signing, X-Request-ID | identical | identical | diffs show no change |

Copy-paste artefacts on the Georgia pages (so they are NOT Georgia-specific facts): endpoint pages require a QWAC from "a qualified trust service provider (QTSP)" and an "organization identifier matching your TPP registration in the **EBA register**"; Security page example `organizationIdentifier: PSDLT-BOL-123456`;
FAQ OAuth example with `client_credentials`, FAQ "authorize" URL `https://bank.paysera.com/authorize?consent_id=...` (conflicts with `/oauth/authorize`), `/v1/...` paths without the profile prefix, "GDPR compliant", SCA exemptions in EUR (EUR 30 / EUR 100).

## 5. Internal contradictions found (record, don't smooth)

1. Cancellation: overview + FAQ + page banners say "not supported" (DELETE => 405 `SERVICE_INVALID`), yet 5 cancellation pages are in the sidebar with full request/response descriptions.
2. `combinedServiceIndicator`: marked required in the consent table; guide says unsupported; error list has `SESSIONS_NOT_SUPPORTED`.
3. `recurringIndicator`: boolean in table, string `"true"` in all examples.
4. `withBalance`: account list/details pages document it as an optional query param ("may be ignored"); transaction-list page and the matrix say it is rejected with 400.
5. Transaction list prose: "Georgian rails currently map onto no ISO bankTransactionCode" but the example's booked items carry `PMNT-ICDT-BOOK` / `PMNT-RCDT-BOOK`.
6. `account-id` path description mentions "(card) account" / "Read Card Account list" though card accounts do not exist in Georgia.
7. Account/consent `_links` hrefs are `/v1/...` while transaction and payment `_links` carry the full `/xs2a/georgia/0.8/v1/...`.
8. Consent-information example shows German IBANs (`DE2310010010123456789`) and a card `pan`; consent-create example uses `maskedPan` although unsupported.
9. Error table in the error-handling guide puts `CONSENT_INVALID` under 403, endpoint pages under 401; `CERTIFICATE_EXPIRED` appears only in the guide.
10. Authentication guide: "dual authentication ... access token for API requests" and "QWAC required for all API requests" reads as if a token is needed everywhere, whereas every consent/payment endpoint page says certificate only (the Security page table is the clearer one and agrees with the endpoint pages).
11. The "advanced scenarios" guide still has a "Bulk Payment Processing" section (it carries a note that bulk is unsupported and queues single payments client-side).
12. IBAN examples use bank code `BG` (`GE00BG...`, Bank of Georgia's code), not a Paysera Georgia code.

## 6. Differences from ProCredit and from the AltaSoft-portal banks (Silk/TeraBank/Liberty/Cartu; counts from the parent's `specs/inventory.txt`)

* vs ProCredit (same NBG 0.8 profile): Paysera = mTLS-only + OAuth; ProCredit = APIM subscription key + required JWS signature, no mTLS mention. Paysera amounts strings, `interimBooked`, transaction items with `transactionId`, `pending` array, `next` pagination, `_links.scaOAuth`, 16 functional endpoints incl. authorisation sub-resources and transaction details; ProCredit 12 operations, numeric amounts, `closingBooked`, `entryReference`, bulk-payments allowed, `ACCC`.
* vs AltaSoft (read-only `yaml.safe_load` of `specs/tera/swagger.yaml` v0.8 and `specs/silk/swagger.yaml` v2.0; Liberty/Cartu share the structure per inventory counts):
  - Structure: Tera 41 paths/117 schemas/59 ops; Silk 125 paths/195 schemas; paths are `/0.8/v1/...` (Tera) and `/v2/...` (Silk), links are relative without leading slash (`0.8/v1/consents/<id>`). Paysera: `/xs2a/georgia/0.8/v1/...`, links with leading slash (some `/v1/...`), 25 documented endpoints, no downloadable schema.
  - Security: AltaSoft declares one scheme `Bearer` (an `apiKey` in header `Authorization`, no global `security`) and declares no `X-Request-ID`/`PSU-IP-Address`/`Digest`/signature header params on any operation (only `Consent-ID`, plus optional `API-ContractID` in Silk). Paysera: mTLS QWAC (no AltaSoft equivalent in the spec) + Bearer on AIS data only + mandatory `X-Request-ID`.
  - Payments: AltaSoft has `payments`, `bulk-payments`, `periodic-payments` families (9 paths each) and products `aspsp`, `domestic`, `foreign`; Paysera: `payments` only, products `domestic`, `foreign` (no `aspsp`).
  - Enums: AltaSoft `BalanceType` = full Berlin list (closingBooked, expected, openingBooked, interimAvailable, interimBooked, forwardAvailable, nonInvoiced[, information in Silk]) vs Paysera only interimAvailable + interimBooked; `TransactionStatus` includes `ACCC`, `ACFC` (Paysera lists neither); `BookingStatus` = booked, pending, both, information, all (Paysera: booked, pending, both); `ConsentStatus` = received, rejected, valid, revokedByPsu, expired, terminatedByTpp, partiallyAuthorised (+replacedByTpp in Silk) vs Paysera `revoked` (not `revokedByPsu`), no `partiallyAuthorised`.
  - Amount: AltaSoft string with pattern `-?[0-9]{1,14}(\.[0-9]{1,3})?` (same as Paysera's strings).
  - Pagination: AltaSoft transactions use an opaque `state` query parameter and a `_links.next` href (`...transactions?state=<base64>`), no `offset`; Paysera uses `offset` (+50) with `_links.next`. Both have `entryReferenceFrom`/`deltaList`/`withBalance` in AltaSoft's schema, rejected by Paysera.
  - Links/SCA: AltaSoft examples carry `startAuthorisation` (multilevel) and `scaOAuth`/`scaRedirect` link types; Paysera returns `scaOAuth` + `scaStatus`, never `scaRedirect`.
  - Both AltaSoft files: card-accounts paths exist (4), funds-confirmations and signing-baskets do not (same as Paysera on the latter two). Sandboxes: AltaSoft hosts `psd2-sandbox1.altasoft.ge` / `psd2-sandbox2.altasoft2.ge` exist in spec `servers`; Paysera has no sandbox.

## 7. KPI numbers

(a) Status page `https://status.paysera.com/` (new platform, sits on `/api/public/v1/summary`; the NBG-listed `/uptime-history/130797` returns 404 `{"message":"Route GET:/uptime-history/130797 not found"}`):
 * Component "Open banking" (id 15, created 2026-08-01): **99.8458 % uptime, last 90 days** (page shows 99.84 %); status Operational on 2026-10-20.
 * 30-day grid (21 Sep-20 Oct): 29 operational days, 1 major-outage day (**25 Sep 2026**, tooltip "the affected service is currently unavailable"). 90-day grid: 88 operational, 1 partial outage (1 Aug, tooltip text about VISA top-ups - looks unrelated, component was created that day), 1 major outage (25 Sep). No incident in the 3-item recent history involves Open banking. Implied downtime if the % covers a full 90 days = 0.1542 % x 90 d ~= 3.3 h (my arithmetic, unverified).
 * Context: other components in the same snapshot: Mobile app 100, Online banking 99.914, Website 99.989, Checkout 98.608, Tickets 99.918, POS 100, Card payments 100, Internal transfers 97.606, SEPA Instant 99.996, SEPA 99.971, Target2 99.996, other currencies 99.996, Visa card 98.872, Other 99.959.
(b) Per-endpoint daily performance report (the data behind `https://developers.paysera.com/guides/open-banking/reports`, JSON `https://developer-api.paysera.com/open-banking-performance`, 324 rows, `type:"open-banking"`, 6 endpoints x 54 days):
 Units confirmed: response times in seconds; `total_downtime` in seconds (e.g. 21806 s = 25.238 % of 86400 s).
 Coverage: 2026-07-01..2026-09-30 with a **38-day gap (2026-08-16..2026-09-22)**; data stops 20 days before today.

| endpoint | days | mean rt (s) | median | p95 | max | days w/ downtime | max downtime % | mean failed % | max failed % |
|---|---|---|---|---|---|---|---|---|---|
| create_payment | 54 | 0.233 | 0.343 | 0.391 | 0.472 | 1 | 12.752 | 0.093 | 5.0 |
| get_accounts | 54 | 0.758 | 0.709 | 1.085 | 1.306 | 1 | 13.457 | 0.065 | 3.5 |
| get_balances | 54 | 0.246 | 0.262 | 0.330 | 0.515 | 1 | 25.238 | 0.070 | 3.8 |
| get_payment | 54 | 0.145 | 0.189 | 0.224 | 0.295 | 0 | 0 | 0 | 0 |
| get_payment_status | 54 | 0.146 | 0.190 | 0.226 | 0.271 | 1 | 13.653 | 0.006 | 0.3 |
| get_transactions | 54 | 0.553 | 0.534 | 0.639 | 2.380 | 0 | 0 | 0 | 0 |

 Overall simple mean over endpoint-days: downtime 0.201 % (=> 99.80 % uptime), failed requests 0.039 %, response 0.347 s. By month: July 0.35 % mean downtime, Aug and Sep 0.00 %. The only >1 % days: 2026-07-01 (get_accounts) and 2026-07-23 (create_payment, get_balances, get_payment_status).
 Fields: `name,date,type,average_response_time,average_downtime,total_downtime,downtime_percentage,failed_requests_percentage` - same four dimensions as the NBG KPI sheet that ProCredit publishes (availability, downtime, response time, error rate).
 Inconsistency: the status page shows a major outage on 25 Sep but the performance report has 0 % downtime/0 % failures on that day, so the two series are not measuring the same thing (or the outage was elsewhere). The performance dataset has **no market/profile field**: it cannot be attributed to the Georgia profile versus the EU profile.

## 8. Could not obtain / unverified

* A downloadable OpenAPI/Swagger document (exists internally per the Security page; no public URL; no GitHub repo).
* Sandbox: none documented as live ("Coming Soon"); no test credentials, no sandbox base URL, no synthetic data set. FAQ also says test mode would include "SCA bypass".
* Georgia-language content: docs are English only; no Georgian locale; authorize page has no `ka`. The standard itself is in Georgian (NBG PDF). `openfinance.ge/en/standards` was not fetched by me (another agent has `dl_xs2a/xs2a_ge_0.8.RC3.pdf`).
* Whether this Georgia API is operated by/for JSC Paysera Bank Georgia (vs Paysera group for Georgian residents): docs never name the legal entity; GE IBAN bank code used by Paysera Georgia not shown.
* Whether the KPI data (status component and performance JSON) covers the Georgia endpoints; no Georgia-specific KPI source found. The NBG-listed KPI URL is dead.
* Request/response JSON Schemas (formats, enums, max lengths beyond `creditorName` <= 70) - only examples/tables exist; `required` flags are only given in the consent body and path/query tables, not for response bodies.
* Georgian-specific certificate authority list (which CAs NBG recognises), pricing/onboarding steps beyond the 4-step overview (get QWAC, contact Paysera, fund test accounts, integrate).
* Actual behaviour of the live API (I made no calls): 405 on cancellation, 429 limits, token lifetimes are as documented only.
