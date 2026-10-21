# JSC ProCredit Bank Georgia - Open Banking API (research notes)

Collected 2026-10-20 (UTC). All downloaded content treated as untrusted data. No authentication attempts, no API calls to
`api.procreditbank.ge` / `online-test.procreditbank.ge`, no form submissions. Requests were sequential, >= 2.5 s apart.

## Bottom line

* There IS a machine-readable definition, but it is **not published as a file**. The "developer portal" is a static
  Redocly v2.4.0 export (`devportal.procreditbank.ge/?mid=1535`, served from CloudFront) with the whole OpenAPI 3.0.1 document
  inlined as `const __redoc_state = {...}`. The "Download OpenAPI specification" paragraph has **no href**. No swagger/openapi
  URL exists to curl. I recovered the spec by `json.loads` of the inlined state (script: `scratchpad/tools_pc_pay/extract_redoc.py`).
* The recovered spec is **thin and not valid-as-is for schema validation**: title `PCB-GEO-TEST-PSD2-API`, version `v1`,
  12 operations, **no `components.schemas`** (Redoc shows "Schema not provided"), request/response bodies are single
  hand-made **examples only**, parameter types are Redoc pseudo-types (`string($uuid)`, `string($ipv4)`, `string($date)`),
  every error response is an empty stub. Everything an exact mock needs beyond the examples has to come from the
  NBG 0.8 RC3 standard (`scratchpad/dl_xs2a/xs2a.txt`) or from the Berlin Group spec.
* The PDF is NOT API documentation: it is a one-page customer consent notice (see below).
* Standard: the Georgian NBG profile "NextGenPSD2 XS2A Implementation Guide 0.8" (Berlin Group NextGenPSD2 v1.3.x adapted by the
  National Bank of Georgia). Vendor: **not AltaSoft; most likely a ProCredit-group or bank-side build published through Azure API
  Management** (inference only, from the APIM subscription-key names and ObjectId-style operation ids; no vendor is named anywhere).
  Differences from the AltaSoft YAMLs (Tera/Liberty v0.8, Silk/Cartu v2.0): 10 paths/12 ops and no schemas vs 41-125 paths and 117-195 schemas;
  APIM key + required signature headers vs a single `Bearer` apiKey scheme and no header parameters except `Consent-ID`; numeric vs string amounts;
  sandbox hosts `psd2-sandbox{1,2}.altasoft*.ge` vs `online-test.procreditbank.ge`; AltaSoft versions the path as `/0.8/v1` or `/v2` like ProCredit's `/0.8/v1`.

## 1. Provenance (see `specs/procredit/raw/MANIFEST.tsv` for URL / UTC time / sha256 of every file)

| file (all under `/tmp/claude-1000/-home-bezhomatiashvili-Desktop-hakathon/d833bfdd-adf6-41b0-9485-b22b3bba4fc9/scratchpad/specs/procredit/`) | what |
|---|---|
| `raw/procredit_open_banking_page_en.html`, `raw/procredit_open_banking_page_ka.html` | bank page (EN `/en/open-banking`, KA `/ge/ღია-ბანკინგი`); source of prod URL, support mail, KPI link |
| `raw/devportal_live.html` (= `devportal.html`, byte-identical) , `raw/devportal_live_mid1536_ka.html` | portal; `?mid=1536` (Georgian-language link) is byte-identical to `?mid=1535`, i.e. no Georgian API docs |
| `raw/procredit_openapi_extracted_from_devportal.json` | DERIVED: spec JSON extracted from `__redoc_state` (sha256 `052c61b8...`, derived from devportal_live.html sha256 `9f40aed5...`) |
| `derived/procredit_openapi_normalized.{json,yaml}` | DERIVED by me: pseudo-types converted to type+format, schemas **inferred from the single example** (flagged `x-inferred-from-example`), prod server added. Not published by ProCredit. Hand-checked structurally only (no OpenAPI validator installed) |
| `raw/open_banking_eng.pdf` (= `open_banking_eng.pdf`), `raw/open_banking_geo.pdf` | consent notice EN / KA, 1 page each; created 2022-02-24 (Adobe Illustrator) |
| `raw/oauth_well_known_configuration.json` | public OAuth metadata referenced by the `scaOAuth` sample link (plain GET, 211 bytes) |
| `raw/ob_kpi_ii_2024_0.xlsx` (= `ob_kpi_ii_2024.xlsx`, identical) | quarterly KPI sheet |

Facts taken from the bank page (not from the spec):
* Production consent URL: `https://api.procreditbank.ge/0.8/v1/consents` ("send a standardized request to this address" to register consent and obtain the API endpoints).
* Support: `geo.obsupport@procredit-group.com`. Consumer complaints: `geo.info@procredit-group.com`, +995 32 2202222.
* Page links: test portal ("Ttest Portal" sic), T&C PDF, KPI xlsx (`/sites/default/files/AUTO/ob_kpi_ii_2024_0.xlsx`).

Consent notice (PDF) content, as it matters for a mock: consent may be one-time or recurrent; recurrent = provider may fetch
automatically within 24 h "in the amount known in advance (maximum 4 pieces)" (=> `frequencyPerDay` max 4); each request
may return all data the bank exposes and the user consented to; user can see/cancel consents in Internet Banking, page
"Consent Management"; bank disclaims liability after hand-over of data to the TPP.

## 2. Endpoint inventory (12 operations, base `/0.8/v1`)

Servers in the spec: ONLY `https://online-test.procreditbank.ge/0.8/v1` (test). Production base (from bank page): `https://api.procreditbank.ge/0.8/v1`.
No sandbox/production distinction beyond that; the spec does not say whether the test host serves synthetic data.

| # | method path | tag | opId | success | notes |
|---|---|---|---|---|---|
| 01 | POST `/consents` | Consents | 605a078404d6ce0d20a91b7d | 201 | body example only |
| 02 | GET `/consents/{consentId}` | Consents | 605a078504d6ce0d20a91b7e | 200 | |
| 03 | GET `/consents/{consentId}/status` | Consents | 605a078504d6ce0d20a91b80 | 200 | `{"consentStatus":"valid"}` |
| 04 | DELETE `/consents/{consentId}` | Consents | 605a078504d6ce0d20a91b7f | 204 | |
| 05 | GET `/accounts` | Accounts | ...91b81 | 200 | `withBalance` |
| 06 | GET `/accounts/{account-id}` | Accounts | ...91b83 | 200 | `withBalance` |
| 07 | GET `/accounts/{account-id}/balances` | Accounts | ...91b82 | 200 | |
| 08 | GET `/accounts/{account-id}/transactions` | Accounts | ...91b84 | 200 | `bookingStatus` REQUIRED; `dateFrom,dateTo,entryReferenceFrom,withBalance` |
| 09 | POST `/{payment-service}/{payment-product}` | Payments | `09-payment-initiation-request` | 201 | service enum `payments`,`bulk-payments`; product enum `domestic`,`foreign`; also 409 |
| 10 | GET `/{payment-service}/{payment-product}/{paymentId}` | Payments | `get-payment-information` | 200 | |
| 11 | GET `.../{paymentId}/status` | Payments | `payment-initiation-status-request` | 200 | `{"transactionStatus":"ACCC"}` |
| 12 | DELETE `.../{paymentId}` | Payments | `payment-cancellation-request` | 204 | |

NOT present (so a mock need not model them for ProCredit): funds confirmation, card accounts, transaction details, consent/payment
authorisation sub-resources, signing baskets, standing orders/`bookingStatus=information`, and any loans/savings/deposits/"products"
endpoints. (AltaSoft/BoG-style specs have 32-125 paths; ProCredit has 10 paths / 12 operations: 3 consents, 4 accounts, 3 payments paths.)

Opids: the 8 consent+account operations have 24-hex ObjectId-like ids (`605a0784...`/`605a0785...`; read as a MongoDB ObjectId timestamp 0x605a0784 = 2021-03-23T15:21:40Z - my inference, unverified), the 4 payment operations have readable slugs.

## 3. Auth and headers

* Spec-level `security`: `apiKeyHeader` = header `Ocp-Apim-Subscription-Key` OR `apiKeyQuery` = query `subscription-key`.
  Those are the **Azure API Management** subscription-key names (strong vendor signal; that also explains the Mongo-style opIds
  and the "Ttest/PCB-GEO-TEST" naming of an APIM export). TPP gets the key at portal onboarding (not publicly described; unverified).
* PSU authentication/SCA: OAuth 2.0 redirect. The spec declares **no OAuth scheme**; the only hint is `_links.scaOAuth.href` =
  `https://tkn.24-7.ge/oauth/.well-known/configuration`. That document (fetched, 211 bytes) contains exactly:
  `issuer https://tkn.24-7.ge`, `jwks_uri https://tkn.24-7.ge/.well-known/openid-configuration/jwks`,
  `authorization_endpoint https://openbanking.24-7.ge`, `token_endpoint https://tkn.24-7.ge/connect/token`.
  No grant types, scopes, PKCE or auth-method info; `/connect/token` + `/.well-known/openid-configuration/jwks` are IdentityServer-style paths.
  The path `/oauth/.well-known/configuration` is neither RFC 8414 nor OIDC discovery.
  Ownership of `24-7.ge`: only circumstantial - `tkn.24-7.ge`, `openbanking.24-7.ge` and `api/online-test/www.procreditbank.ge` all resolve to
  addresses in 185.118.85-87.x (tkn .87.149, openbanking .87.145, api .85.141, online-test .87.247, www .85.133 - DNS only), and ProCredit's own sample links point there. A web search found no public ownership statement. Treat as "bank-operated, unproven".
* Request signing is REQUIRED here: `x-jws-signature` and `Digest` are marked required on every operation; the spec says
  "A signature of the request by the TPP on application level" / "Digest is contained iff x-jws-signature is contained".
  This matches NBG 0.8 RC3 section 5.4 (my reading of the Georgian text: application-level qualified-e-seal-equivalent signing is mandatory on both TPP requests and ASPSP responses; it references the Georgian Banking Association "JWS implementation guide for open banking v0.5").
  The spec does NOT say which certificate (QSealC-like) or JWS algorithm/format; nothing about mTLS/QWAC appears in the portal.
* Headers by operation: `X-Request-ID` (uuid) required on all; `x-jws-signature`, `Digest` required on all;
  `PSU-IP-Address` (ipv4) REQUIRED on POST /consents and POST payment, optional on consent GET/DELETE, accounts*, payment DELETE, absent on payment GET/status;
  `Consent-ID` REQUIRED on all 4 `/accounts*` ops; `TPP-Rejection-NoFunds-Preferred` (boolean) optional on payment initiation.
  Content-Type/Authorization are not declared at all (no Bearer scheme in the spec).
* Rate/limits: recurrent AIS access max 4 per 24 h per consent (PDF + `frequencyPerDay: 4` example). No Retry-After / pagination parameters documented.
* Error statuses documented per op (all empty stubs, no schema, no `tppMessages` example): 400,401,403,404,405,406,408,415,429,500,503 (+409 on payment init);
  the three payment GET/status ops and DELETE payment list no error responses at all.

## 4. Schema highlights and deviations (examples as published)

Consent create request: `{"access":{"accounts":[],"balances":[],"transactions":[]},"recurringIndicator":true,"validUntil":"2022-02-02","frequencyPerDay":4,"combinedServiceIndicator":true}`
(empty arrays in the example, i.e. a bank-offered/global-style request; `recurringIndicator` is a real boolean here - Paysera sends the string "true").
Consent create 201: `{"consentStatus":"received","consentId":"<uuid>","_links":{"scaOAuth":{href abs},"self":{"/0.8/v1/consents/<id>"},"status":{...}},"message":"Created"}` (`message` is non-standard).
Get consent 200: `access.{accounts,balances,transactions}[]={iban,currency}`, `recurringIndicator`, `validUntil`, `frequencyPerDay`, **`lastActionDate`**, `consentStatus:"valid"`.

Accounts: `{"accounts":[{"resourceId":"16653013","iban":"GE38PC0133600100044650","currency":"GEL","name":"<iban>","product":"მიმდინარე ანგარიში","status":"enabled","bic":"MIBGGE22","balances":[],"_links":{balances,transactions}}]}`
 - resourceId is a numeric-looking string; `name` = IBAN; `product` is Georgian text ("current account"); `status:"enabled"`, `bic`, `balances: []` are extra; `_links` are relative `/0.8/v1/...`.
 - IBAN: `GE38PC0133600100044650` - bank code `PC` (IBAN chars 5-6), BIC `MIBGGE22`, which the spec itself uses for ProCredit accounts.
Balances: `{"account":{iban,currency},"balances":[{"balanceAmount":{"currency":"GEL","amount":101.5},"balanceType":"interimAvailable"},{...,"balanceType":"closingBooked"}]}` - **amount is a JSON number**, no `referenceDate`.
Transactions: `{"account":{...},"transactions":{"booked":[{"entryReference":"1104978161","bookingDate","valueDate","transactionAmount":{"currency":"GEL","amount":27.6},"creditorName","creditorAccount":{"iban"},"remittanceInformationUnstructured":"ყიდვა/Purchase via POS terminal, ..., 417435******6229, 04/04/2021 13:11:03.-."}],"_links":{"account","first"}}}` -
 booked entries carry `entryReference` (no `transactionId`), only creditor-side fields even for POS purchases, no `pending` array shown, **no `next` link / no paging parameter documented** (only `_links.first`, Redoc example; unknown whether the bank pages at all).
 Remittance text is bilingual Georgian/English, uses card PAN masked `417435******6229`.
Payment initiation request: `{"creditorAccount":{"iban":"GE07PC...","currency":""},"creditorName":"domestic","remittanceInformationUnstructured":"domestic","debtorAccount":{"iban":"GE38PC...","currency":""},"instructedAmount":{"currency":"GEL","value":9.99}}`
 - **`instructedAmount.value` (number)** in the request vs **`instructedAmount.amount`** in the GET response and in Berlin/NBG/AltaSoft - an internal inconsistency of the published examples; `currency` of accounts is `""` (empty string).
Payment initiation 201: `{"transactionStatus":"ACCC","paymentId":"<uuid>","_links":{"self":{"/0.8/v1/payments/domestic/<id>"},"status":{...}},"psuMessage":"Login to e-Banking"}` -
 status already `ACCC` (completed) and **no `scaOAuth`/`scaRedirect` link**; PSU is told to log in to e-Banking (consistent with NBG text that says intra-bank transfers should go straight to ACCC; whether SCA happens before is not documented).
GET payment 200: `{"debtorAccount":{"currency":"GEL"},"debtorIdentification":{"privateId":{"others":[{"identification":"PNOGE-01019016022"}]}},"instructedAmount":{"currency":"GEL","amount":100},"creditorAccount":{"iban":"GE08PC0003600400000813","currency":"GEL"},"creditorAgent":"MIBGGE22","creditorName":"...","creditorIdentification":{...},"chargeBearer":"DEBT","remittanceInformationUnstructured":"...","remittanceInformationUnstructuredArray":[],"instructionPriority":"HIGH","transactionStatus":"RCVD"}`
 - `PNOGE-<11 digits>` personal-number format, `privateId.others[]`, `chargeBearer`, `instructionPriority`, `debtorIdentification/creditorIdentification` all occur in the NBG 0.8 RC3 text (grep of `dl_xs2a/xs2a.txt`: `PNOGE-01234567890` example at line 727, chargeBearer x5, instructionPriority x3); they are NBG-profile fields, not ProCredit quirks.
 - `creditorAgent` is a bare BIC string (`"MIBGGE22"`); `debtorAccount` has only `currency` (no iban) in this GET sample although the POST sample sends one - presumably masked, unverified.
 - `transactionStatus` values seen: `ACCC`, `RCVD`. The NBG text additionally defines ACCP, ACSC, ACFC, etc.

Deviations / things to model deliberately:
1. amount typing: ProCredit numbers (`101.5`, `27.6`, `9.99`, `100`); Berlin Group, Paysera and AltaSoft (Tera v0.8 schema `Amount.amount` = string, pattern `-?[0-9]{1,14}(\.[0-9]{1,3})?`; Silk v2.0 `StrAmount`) use strings. ProCredit is the outlier.
2. `balanceType`: `interimAvailable` + `closingBooked` (Paysera: `interimAvailable` + `interimBooked`).
3. signing mandatory (Paysera: ignored; AltaSoft Tera/Silk YAMLs: no operation declares `X-Request-ID`, `PSU-IP-Address`, `Digest`, `x-jws-signature` or `TPP-Signature-Certificate` header parameters at all - only `Consent-ID`, plus optional `API-ContractID` in Silk - so signing is not machine-specified there). ProCredit is the only one of the three that makes the signature headers a required part of its spec.
4. Subscription-key (APIM) layer on top of OAuth/signing; Paysera uses mTLS QWAC only; AltaSoft banks declare one scheme `Bearer` (an `apiKey` in header `Authorization`, no global `security`).
5. Dates in examples are 2021-2022 => the portal sample set is a 2021 snapshot; `validUntil 2022-02-02` would be in the past for a validating mock.
6. Payment path keeps `/{payment-service}/{payment-product}` with `bulk-payments` allowed (Paysera: single `payments` only, no service segment in the path docs).
7. Only 10 distinct paths; no AltaSoft-style extras (authorisation sub-resources, bulk/periodic payment families, card accounts, transaction details; BoG additionally has KPI and banking-products-services tags).
8. Georgian-language content appears only inside example data (`product`, creditor names, remittance text); no localized docs (portal KA link is the same English page; PDF exists in KA).

## 5. KPI numbers (xlsx `ob_kpi_ii_2024_0.xlsx`; sheet 1, file core-props `modified 2024-05-16T12:06:56Z`)

| KPI | metric | value |
|---|---|---|
| 1 | Availability - uptime per day of all interfaces | 99.93 % |
| 2 | downtime per day of all interfaces | 0.07 % |
| 3 | Performance - PISP response time | 5394 (unit not stated; probably ms = 5.4 s) |
| 4 | Account information SP response time | 903 (probably ms) |
| 5 | Card-based payment instrument issuers confirmation response time | N/A (no funds-confirmation service) |
| 6 | Daily error response rate | 0.25 % |

Caveats: the label "ii_2024" presumably means Q2 2024 but the file was last modified 16 May 2024, before Q2 ended (so it may be Q1 data or a mid-quarter draft); units are not labelled; as of 2026-10-20 this is the only KPI file linked from the page (>2 years old, no newer file is linked; I did not guess newer file names).

## 6. Could not obtain / unverified

* A standalone OpenAPI/Swagger file or its URL: none exists publicly (download link has no href). The embedded spec has no schemas.
* Any description of the PSU OAuth flow beyond the 4-key metadata document (no scopes, no PKCE statement, no client registration process, no token lifetimes).
* Onboarding: how to obtain `Ocp-Apim-Subscription-Key`, which certificates/signature algorithm (`x-jws-signature` format, `Digest` hash), TPP register/eIDAS/NBG requirements, test credentials, sandbox data set. Not published.
* Whether `online-test.procreditbank.ge` is reachable/open (I deliberately did not call the API).
* Whether `api.procreditbank.ge` uses exactly the same schema as the test spec (the page says "0.8/v1", the spec says test only).
* Error body format, pagination behaviour, field-level required/optional, enum lists (consent statuses, balance types, booking statuses beyond the one sentence "booked, pending, both").
* KPI period; KPI units; whether other KPI quarters exist.
* Owner of `24-7.ge` (circumstantial only).
* The "0.8" in `/0.8/v1` is the NBG profile version (Georgian XS2A guide 0.8 RC3), not a Berlin Group release number: the same string is used by BoG (`/0.8`) and Paysera (`/xs2a/georgia/0.8/v1`); ProCredit's `domestic`/`foreign` + `payments`/`bulk-payments` match NBG section 8.1.
