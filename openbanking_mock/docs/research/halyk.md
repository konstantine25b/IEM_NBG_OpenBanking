# JSC Halyk Bank Georgia - Open Banking API research (halyk)
Collected 2026-10-20 UTC. Raw files: `/tmp/claude-1000/-home-bezhomatiashvili-Desktop-hakathon/d833bfdd-adf6-41b0-9485-b22b3bba4fc9/scratchpad/specs/halyk/raw/` (MANIFEST.tsv there). Pre-existing bundle copy (untrusted, not executed): `.../specs/halyk_js/index.js`.

**Verdict:** same portal, same OpenAPI file and same PDFs as Credo and Basisbank (proof below). Spec: `NextGenPSD2 XS2A Framework 1.3.9_2021-05-04`, OpenAPI 3.0.1. Only the hosts, branding, KPI numbers and the OIDC issuer URL differ. Best mock inputs: `raw/psd2-api_1.3.9_2021-05-04v1.yaml` (CRLF line endings - normalise with `tr -d '\r'`) + `raw/NextGenPSD2_XS2A_Implementation_Guide_0.8.RC3.pdf` (Georgian).

## 1. Provenance
- Opened `https://openbank.hbg.ge/ob-home/app/api-doc` in Chrome: Redoc loads chunk `assets/ApiDoc-DoSBBkWM.js`, which `fetch`es `https://openbank.hbg.ge/ob-home/app/psd2-api%201.3.9%202021-05-04v1.yaml` (452,199 B = CRLF version of Credo's 440,101 B LF file). Main bundle served live: `assets/index-C2aMnXoC.js`; logo `ha/logo_primary.png`; brand colours `#01C26E / #202B30 / #C4C7C8`; bank-name string "JSC Halyk Bank Georgia". `/integration` page lists the same documents and sandbox data (see `raw/page_integration.txt`).
- KPI: `POST https://openbank.hbg.ge/ob-home/api-analytics/get-analytics-kpi` (body `{}`), same call the front-end makes.

Files:

| File (specs/halyk/raw/) | Exact source URL | UTC time | HTTP | Bytes | sha256 |
|---|---|---|---|---|---|
| psd2-api_1.3.9_2021-05-04v1.yaml | https://openbank.hbg.ge/ob-home/app/psd2-api%201.3.9%202021-05-04v1.yaml | 2026-10-20T16:58:04Z | 200 | 452199 | 2d216f747b9a45e96ba5e572bc3cbcd85605a3655644ccd8795c01a8c2601466 |
| ob-documents_psd2-api_1.3.9_2021-05-04v1.yaml | https://openbank.hbg.ge/ob-documents/0.8/psd2-api%201.3.9%202021-05-04v1.yaml | 2026-10-20T16:58:07Z | 200 | 430343 | 842c4bc4e612b477b04f53f4cc597a47ea205e449be94c6408ae7529082add63 |
| ApiDoc-DoSBBkWM.js | https://openbank.hbg.ge/ob-home/app/assets/ApiDoc-DoSBBkWM.js | 2026-10-20T16:58:10Z | 200 | 1074974 | daa6a328b76a475e74d2dabdfd19d170e2b5f24ba4a919a9194a3beaa39635b5 |
| locales_ka_index.json | https://openbank.hbg.ge/ob-home/app/locales/ka/index.json | 2026-10-20T16:58:12Z | 200 | 626 | a435e4eb3c6f212ca3ed97db4274881ecaff2ee8f11210eeeb0363fa9adbdad2 |
| NextGenPSD2_XS2A_Implementation_Guide_0.8.RC3.pdf | https://openbank.hbg.ge/ob-documents/0.8/NextGenPSD2%20XS2A%20Implementation%20Guide%200.8.RC3.pdf | 2026-10-20T16:58:15Z | 200 | 1692165 | 7b637af5db83b5d0c35769fbae2057aec790c03373219feddad1a3738cf50547 |
| OpenFinance_IAA_Implementation_Guide_0.8.RC1.pdf | https://openbank.hbg.ge/ob-documents/0.8/Open%20Finance%20IAA%20Implementation%20Guide%200.8.RC1.pdf | 2026-10-20T16:58:18Z | 200 | 604114 | 223f0f1c85bfd6ba1151005e6c129db459813bf81839c5697577042f0c2bc7ae |
| OpenFinance_JWS_Profile_0.5.RC1.pdf | https://openbank.hbg.ge/ob-documents/0.8/OpenFinance%20Json%20Web%20Signature%20Profile%200.5.RC1.pdf | 2026-10-20T16:58:20Z | 200 | 602966 | c207ac0b29db2389601c0f1a423706056ca90aec7391025c19fbd4925dbf7e0d |
| oidc_openid-configuration.json | https://openbank.hbg.ge/ob-oidc/.well-known/openid-configuration | 2026-10-20T16:58:23Z | 200 | 1727 | dd800bb52d86182e2fa9fee8845cd820025bdfbad8f980aab8b8dc4a6af19876 |
| kpi_post_response.json | POST https://openbank.hbg.ge/ob-home/api-analytics/get-analytics-kpi | 2026-10-20T17:05:42Z | 200 | 4261 | 68379c8bffd4136a7d7e090c4aacb6cc8d2503a8e4f02e8889b946c0b1bc49a3 |
| page_home.txt | https://openbank.hbg.ge/ob-home/app/ | 2026-10-20T17:16:27Z (file write time; content = browser-rendered text, transcribed) | n/a-browser | 1465 | df715b3433dff3a8e6f3fa5984bbb4b246379e8054355c91d0aab1d65d321b0f |
| page_integration.txt | https://openbank.hbg.ge/ob-home/app/integration | 2026-10-20T17:16:27Z (file write time; content = browser-rendered text, transcribed) | n/a-browser | 1556 | 5e086adf39e74005816a5664f5ab0af3aa990df19926d93e468a04af38b75e74 |

### Proof that Credo / Halyk / Basisbank share ONE spec
| Artefact | Credo | Halyk | Basis |
|---|---|---|---|
| In-app YAML, as served (bytes / sha256) | 440,101 / 209ecd48...d815c | 452,199 / 2d216f74...01466 | 452,199 / 2d216f74...01466 |
| In-app YAML after `tr -d '\r'` (sha256) | 209ecd48f287ccaeb448f7f6be818cff1833b8f68c67e547bb32b1afb91d815c | same | same |
| /ob-documents YAML, as served | 418,736 / 861bdb20...27048 | 430,343 / 842c4bc4...add63 | 430,343 / 842c4bc4...add63 |
| /ob-documents YAML after CR strip | 861bdb20bf1a88246d174fb950f8c967445a85b4220f6430fc564f833e027048 | same | same |
| NextGenPSD2_XS2A_Implementation_Guide_0.8.RC3.pdf | 7b637af5...50547 | same | same |
| Open Finance IAA Guide 0.8.RC1 / OpenFinance JWS Profile 0.5.RC1 | 223f0f1c...7c2bc7ae / c207ac0b...25dbf7e0d | same | same |
| OIDC discovery JSON (host replaced by HOST, keys sorted) | sha 1ad7507587c2b9b2... | same | same |
`diff` of Credo vs Halyk in-app YAML after CR strip: 0 lines. The only difference between Credo and the other two is CRLF vs LF line endings. The JS bundles are separate per-bank builds (different chunk hashes, e.g. `Home-CsB-ftbh.js` / `Home-BfKAmE1O.js` / `Home-DJhwBN3U.js`; sizes 603,616 / 604,224 / 604,214 B) differing only in bank name, logo, colours and (Basis) the certificate-request path; same routes (`/`, `/integration`, `/api-doc`, `/analytics`) and same API client code.

## 2-4. Endpoint inventory, auth/headers, schema highlights & deviations (identical for Credo, Halyk, Basisbank - proven by hash, see section 1)

All three banks publish the SAME React/Vite portal ("OB Home", /ob-home/app/) from the Georgian Banking Association's open-banking package, and the SAME OpenAPI file, the SAME 3 PDF guides and the SAME OIDC discovery document (only host names differ). The `/ob-home/app/api-doc` page is Redoc ("API docs by Redocly") rendering `GET /ob-home/app/psd2-api 1.3.9 2021-05-04v1.yaml`; the "Download" link on that page serves the same file.

### Which file to use (important)
| File | Where | Ops | Parses as YAML? |
|---|---|---|---|
| **In-app copy** `https://<host>/ob-home/app/psd2-api%201.3.9%202021-05-04v1.yaml` | what Redoc renders + Download link | 23 ops / 20 paths | YES - use this |
| `/ob-documents/0.8/psd2-api 1.3.9 2021-05-04v1.yaml` ("Swagger YAML File" link on /integration) | older copy | 18 ops / 16 paths | NO - PyYAML ScannerError "while scanning an alias" at line ~9250: a `# * "periodic-payments"` comment line sits inside a plain multi-line description scalar, so the next `* "bulk-paments"` line parses as a YAML alias. Also lacks the 4 `/card-accounts*` ops and `POST .../authorisations` (startPaymentAuthorisation). Only schema difference: `paymentInitiationBulkElement_json`. |

(Halyk/Basis copies are CRLF; after `tr -d '\r'` they are byte-identical to Credo's. Use LF-normalised file.)

### Spec identity
- `openapi: 3.0.1`, `info.title: NextGenPSD2 XS2A Framework`, `info.version: 1.3.9_2021-05-04` (Berlin Group file; "Download OpenAPI specification" in Redoc). Contact = Berlin Group, license CC-BY-4.0.
- 20 paths, 23 operations, 284 component schemas, 47 parameters, 77 responses, ~92 example nodes, 1 security scheme (`BearerAuthOAuth`: http bearer; every op has `security: [{}, {BearerAuthOAuth: []}]` = auth OPTIONAL as written).
- `servers:` are Berlin Group PLACEHOLDERS (`https://api.testbank.com/psd2`, `https://test-api.testbank.com/psd2`). Real base URLs are NOT in the spec - only on the portal pages (below).
- Spec paths carry NO `/v1` prefix; the real base already ends with `/0.8/v1` (Georgian guide 7.1: `https://{provider}/{version}/v1/{service}`, `{version}` = guide version 0.8, RC suffix dropped; production and test MUST be separated by the `{provider}` part).

### Base URLs / environments (from portal home + /integration pages, verbatim)
| Bank | Production base | Sandbox base | OIDC discovery |
|---|---|---|---|
| Credo | https://openbankgw.credo.ge/0.8/v1 | https://openbankgw.credo.ge/sandbox/0.8/v1 | https://openbanking.credo.ge/ob-oidc/.well-known/openid-configuration |
| Halyk | https://openbankgw.hbg.ge/0.8/v1 | https://openbankgw.hbg.ge/sandbox/0.8/v1 | https://openbank.hbg.ge/ob-oidc/.well-known/openid-configuration |
| Basisbank | https://openbankgw.bb.ge/0.8/v1 | https://openbankgw.bb.ge/sandbox/0.8/v1 | https://openbank.bb.ge/ob-oidc/.well-known/openid-configuration |

Sandbox credentials shown publicly on every portal ("Sandbox Credentials"): user `testclient`, password `testclient`, OTP `111111` (PSU login in the OAuth redirect page of the sandbox). NOT used by me (no authentication attempts). There is also a "Sandbox Certificate Generation" section (not opened; the bundle shows it POSTs to `/ob-home/certificate/request-certificate`; Basisbank: `/ob-internal/certificate/request-certificate`) - NOT used (form submission). No sandbox test IBANs / consent ids / account data are published anywhere on the portals or in the PDFs I read.

### Endpoint inventory (in-app YAML; `{payment-service}`/`{payment-product}` are generic templated segments)
| Method | Path (relative to base, NO /v1 in spec) | operationId | Required non-path params | Query params | requestBody | 2xx |
|---|---|---|---|---|---|---|
| POST | /{payment-service}/{payment-product} | initiatePayment | X-Request-ID,x-jws-signature,PSU-IP-Address | - | paymentInitiation | 201 |
| GET | /{payment-service}/{payment-product}/{paymentId} | getPaymentInformation | X-Request-ID,x-jws-signature | - | - | 200/202 |
| DELETE | /{payment-service}/{payment-product}/{paymentId} | cancelPayment | X-Request-ID,x-jws-signature | - | - | 204/202 |
| GET | /{payment-service}/{payment-product}/{paymentId}/status | getPaymentInitiationStatus | X-Request-ID,x-jws-signature | - | - | 200/202 |
| GET | /{payment-service}/{payment-product}/{paymentId}/authorisations | getPaymentInitiationAuthorisation | X-Request-ID,x-jws-signature | - | - | 200 |
| POST | /{payment-service}/{payment-product}/{paymentId}/authorisations | startPaymentAuthorisation | X-Request-ID,x-jws-signature | - | - | 201 |
| GET | /{payment-service}/{payment-product}/{paymentId}/authorisations/{authorisationId} | getPaymentInitiationScaStatus | X-Request-ID,x-jws-signature | - | - | 200 |
| GET | /{payment-service}/{payment-product}/{paymentId}/cancellation-authorisations | getPaymentInitiationCancellationAuthorisationInformation | X-Request-ID,x-jws-signature | - | - | 200 |
| GET | /{payment-service}/{payment-product}/{paymentId}/cancellation-authorisations/{authorisationId} | getPaymentCancellationScaStatus | X-Request-ID,x-jws-signature | - | - | 200 |
| GET | /accounts | getAccountList | X-Request-ID,x-jws-signature,Consent-ID | withBalance | - | 200 |
| GET | /accounts/{account-id} | readAccountDetails | X-Request-ID,x-jws-signature,Consent-ID | withBalance | - | 200 |
| GET | /accounts/{account-id}/balances | getBalances | X-Request-ID,x-jws-signature,Consent-ID | - | - | 200 |
| GET | /accounts/{account-id}/transactions | getTransactionList | bookingStatus,X-Request-ID,x-jws-signature,Consent-ID | dateFrom,dateTo,entryReferenceFrom,bookingStatus,deltaList,withBalance | - | 200 |
| GET | /card-accounts | getCardAccount | X-Request-ID,x-jws-signature,Consent-ID | - | - | 200 |
| GET | /card-accounts/{account-id} | ReadCardAccount | X-Request-ID,x-jws-signature,Consent-ID | - | - | 200 |
| GET | /card-accounts/{account-id}/balances | getCardAccountBalances | X-Request-ID,x-jws-signature,Consent-ID | - | - | 200 |
| GET | /card-accounts/{account-id}/transactions | getCardAccountTransactionList | bookingStatus,X-Request-ID,x-jws-signature,Consent-ID | dateFrom,dateTo,entryReferenceFrom,bookingStatus,deltaList | - | 200 |
| POST | /consents | createConsent | X-Request-ID,x-jws-signature,PSU-IP-Address | - | consents | 201 |
| GET | /consents/{consentId} | getConsentInformation | X-Request-ID,x-jws-signature | - | - | 200 |
| DELETE | /consents/{consentId} | deleteConsent | X-Request-ID,x-jws-signature | - | - | 204 |
| GET | /consents/{consentId}/status | getConsentStatus | X-Request-ID,x-jws-signature | - | - | 200 |
| GET | /consents/{consentId}/authorisations | getConsentAuthorisation | X-Request-ID,x-jws-signature | - | - | 200 |
| GET | /consents/{consentId}/authorisations/{authorisationId} | getConsentScaStatus | X-Request-ID,x-jws-signature | - | - | 200 |
Counts: accounts 4 + card-accounts 4 + consents 6 + PIS 9 (generic `/{payment-service}/{payment-product}`) = 23 operations.
NOT in the file (present in stock Berlin Group 1.3.9 / Georgian guide): `PUT` authorisation-update endpoints (PSU auth / SCA method / OTP), `POST /consents/{id}/authorisations`, `POST .../cancellation-authorisations`, `GET /accounts/{id}/transactions/{transactionId}`, `/signing-baskets*`, `/funds-confirmations`, `/periodic-payments/*` (no `periodic-payments` in the `payment-service` enum). Orphaned signing-basket / confirmationOfFunds `requestBodies`/`responses`/`schemas` are still in `components` (leftover from the stock file).
The portal home page lists four services: Account Information, Payment Initiation, Client Identification (the OIDC service under /ob-oidc) and "Products - fetching bank product details via REST services". The YAML has NO products endpoints and no loans/savings endpoints, although the KPI JSON has Loans (LIS) and Savings (SIS) columns (unverified whether the gateways expose them). The sibling Georgian 0.8 spec of Bank of Georgia (`specs/bog/plugin-redoc-0.yaml`, 40 ops, base `https://xs2a-sandbox.bog.ge/0.8`) DOES publish a `/banking/products/*` service (17 GET ops: index, `{id}`, `{locale}`, and per-family `trans-and-savings-accounts`, `term-deposits`, `pers-loans`, `residential-mortgages`, `business-loans`, `cred-and-chrg-cards`, each with a `/{locale}/` variant) plus a KPI tag - a likely shape of the missing "Products" service, UNVERIFIED for Credo/Halyk/Basis. Operation-set diff OB Home vs BoG: BoG keeps `/v1` in its paths, has PUT authorisation updates, `POST /consents/{consentId}/authorisations`, `GET /accounts/{account-id}/transactions/{transactionId}` and no card-accounts; OB Home has card-accounts and none of those - so the OB Home YAML is not a copy of the BoG file.

### Auth / security model
- Transport: mutual TLS with QWAC-type certificates; app-level JWS (home page: "OAuth2, MTLS, JWS and FAPI"). Georgian JWS profile (OpenFinance JSON Web Signature Profile 0.5.RC1, Georgian only) = ETSI JAdES-style **detached** JWS in header `x-jws-signature`: protected header `{"b64":false,"x5t#S256"|"x5c":..., "crit":["sigT","sigD","b64"],"sigT":"<UTC ISO>","sigD":{"pars":["(request-target)","host","content-type","psu-ip-address","psu-geo-location","digest",...],"mId":"http://uri.etsi.org/19182/HttpHeaders"},"alg":"RS256"}`; algs RS256/RS512/ES256/ES512/PS256/PS512 (never `none`); body hash in `Digest: SHA-256=<base64>`; all `PSU-*` headers and `x-request-id` present must be listed in `pars`; `x5c` XOR `x5t#S256` (+`kid`); cert chain issuer "Banking Association OB Seal CA" (Georgian Banking Association root); UTC time sync, a message dated >2 s in the future must not be processed.
- User auth: OAuth2/OIDC redirect (Georgian guide allows ONLY OAuth2 for SCA; decoupled/CIBA from 2022-06-01 when `TPP-Decoupled-Preferred=true`). Discovery doc (identical for 3 banks after host substitution; sha of normalised JSON equal): `scopes_supported:["openid"]`, `response_types_supported:["code"]`, `grant_types_supported:["authorization_code","refresh_token","urn:openid:params:grant-type:ciba"]`, `token_endpoint_auth_methods_supported:["client_secret_basic"]`, `code_challenge_methods_supported:["S256"]` (PKCE), `acr_values_supported:["https://openfinance.ge/ns/assurance/basic","https://openfinance.ge/ns/assurance/sca","https://openfinance.ge/ns/assurance/tmp/otp-challenge"]`, `id_token_signing_alg_values_supported:["RS512"]`, `subject_types_supported:["pairwise"]`, `ui_locales_supported:["en","ka","en-GB","ka-GE"]`, `verified_claims_supported:true`, `trust_frameworks_supported:["ge_aml"]`, `id_documents_supported` incl. `idcard, passport, ge_idcard_temporary, ge_compatriot_id...`, `backchannel_token_delivery_modes_supported:["poll"]`; endpoints `{issuer}/authorize|bc-authorize|token|revoke|introspect|userinfo|.well-known/jwks.json`, issuer `https://<oidc-host>/ob-oidc`.
- Required headers per the YAML: every operation: `X-Request-ID` (uuid) + `x-jws-signature` (string); AIS data endpoints also `Consent-ID`; `POST /consents` and `POST /{payment-service}/{payment-product}` also `PSU-IP-Address` (ipv4). `Digest` is declared OPTIONAL in the YAML (but mandatory under the Georgian JWS profile). `Authorization: Bearer` is not a declared parameter, only the optional security scheme.
- Georgian guide additions (0.8.RC3): ALL AIS data calls must carry BOTH `Authorization` and `Consent-ID`; `PSU-IP-Address` only if the PSU is actively present (never on background TPP polling; mandatory on consent creation); `PSU-ID`/`PSU-Corporate-ID` format = 3-letter type + 2-letter country + `-` + id: `PNOGE-012345678901` (personal no.), `PASGE-...` (passport), `IDCGE-...` (ID card), corporate `NTRGE-0123456789`; TPP/ASPSP identifiers `PSDGE-NBG-<BIC>` e.g. `PSDGE-NBG-BAGAGE22`; `Accept-Language` (default Georgian/ka-GE when absent), `Content-Language` recommended; `X-Request-ID` UUID doubles as idempotency key; `TPP-Rejection-NoFunds-Preferred` must be honoured.

### Deviations of the YAML from stock Berlin Group 1.3.9 (inferred from file contents; stock file not diffed)
1. Path template `/{payment-service}/{payment-product}` instead of `/payments|bulk-payments|periodic-payments/{payment-product}`; no `/v1` prefix.
2. `x-jws-signature` (required) replaces BG `Signature` + `TPP-Signature-Certificate`; `Signature` param still defined in components but unused.
3. Signing baskets, funds-confirmation, all PUT/auth-update ops removed (orphans remain).
4. Local patches in schemas: `chargeBearer` enum has extra `OUR`; `priorityCode` NORM/HIGH/URGT; payment body has `debtorIdentification`/`creditorIdentification` (`partyIdentification`).

### TRAPS for a schema-validated mock built from this YAML (highest priority first)
1. **Payment enums are SEPA, not Georgian.** `components.parameters.paymentProduct.schema.enum` = `sepa-credit-transfers, instant-sepa-credit-transfers, target-2-payments, cross-border-credit-transfers, pain.001-*` (8 values) and `paymentService.schema.enum` = `[payments, bulk-payments]`. The Georgian guide (the real contract) mandates products **`aspsp` (recommended), `domestic` (mandatory), `foreign` (mandatory)** and services `payments`, `bulk-payments`, `periodic-payments`. A validator built on the YAML rejects every real Georgian payment URL. PATCH: replace the paymentProduct enum with `[aspsp, domestic, foreign]` and the paymentService enum with `[payments, bulk-payments, periodic-payments]`; add `periodic-payments` body (`periodicPaymentInitiation_json` already exists with `frequency, dayOfExecution, startDate, endDate, executionRule`).
2. Payment body requires `debtorAccount, instructedAmount, creditorAccount, creditorName` (`paymentInitiation_json.required`), but the guide's matrix (8.2.1, columns aspsp | domestic same-bank | domestic other-bank RTGS | domestic treasury | domestic other-bank FX/SWIFT | foreign) makes `creditorName` FORBIDDEN ("-") for aspsp, same-bank domestic and treasury payments and mandatory ("m") only for RTGS other-bank, FX and foreign; `debtorAccount` is conditional ("c"); `creditorAgent` is mandatory for treasury, FX and foreign; fields marked "never" in the matrix (`debtorId, currencyOfTransfer, exchangeRateInformation, creditorId, creditorNameAndAddress, purposeCode, serviceLevel, structured remittance...`) still exist in the schema - real banks answer `FORMAT_ERROR` if they are sent.
3. `servers` are placeholders; base URL must be injected (see table). Security is declared optional (`- {}`), real gateway requires mTLS + JWS + Bearer.
4. `Digest` optional in YAML, mandatory in practice.
5. Error `code` enums lack Georgian-guide codes `FORMAT_INVALID` (used for unsupported `trustedBeneficiaries`); `tppMessageGeneric.code` wrongly `$ref`s `tppMessageCategory`. `CONSENT_INVALID`/`CONSENT_EXPIRED` are listed under 401 in the YAML.
6. Amounts are strings (`amountValue` pattern `-?[0-9]{1,14}(\.[0-9]{1,3})?`), IBAN pattern `[A-Z]{2,10}[0-9]{2,2}[a-zA-Z0-9]{1,30}` (loose), all examples are FR/DE IBANs and EUR - no Georgian (GE..., GEL) examples anywhere.
7. `GET /accounts/{id}` and `/card-accounts/{id}` 200 body is untyped `object`.
8. Georgian-guide requirements NOT expressible in the YAML (mock logic needed): `frequencyPerDay` must be 1 when `recurringIndicator=false`; `allPsd2` consents refused; empty `access` arrays => bank-side account selection + SCA; resourceId = opaque UUID, unique per currency sub-account; multicurrency aggregate account has `currency:"XXX"`; `bban`/`linkedAccounts` never returned; `status` only `blocked`/`deleted`; `usage` PRIV/ORGA; `details` only when blocked; balances only if consent covers them (else `CONSENT_INVALID`, not a reduced response); `/balances` must contain `closingBooked` + `interimAvailable`; account details `interimAvailable` + `interimBooked`; transactions: bookingStatus `booked|pending|both` required (`information` optional), >50 items => pages of <=50 with `_links.first` (= same URL as the call) and `next` (no `previous/last`), `entryReferenceFrom` = strictly-after semantics, `PARAMETER_NOT_CONSISTENT` if the reference vanished, expired consent => `CONSENT_EXPIRED`, history window limited only by law; chunked reports must carry `closingBooked` per day and `referenceDate` on every `*Booked` balance.

### Georgian implementation guide v0.8.RC3 - what it says (Georgian-language PDF, 100 pp; `raw/NextGenPSD2_XS2A_Implementation_Guide_0.8.RC3.pdf`)
- Glossary: ამსმპ = ASPSP, სმმ = PSU, გიმპ = PISP, მმპ = TPP (third-party provider; inferred from "მესამე მხარის პროვაიდერი"). Deviations from XS2A are marked in the text "// გადახვევა XS2A ჩარჩოდან" (only 2 markers: e-seal 7.4, `ownerIdentification` field 9.3.4).
- Mandatory-by dates: v0.8.RC1 from 2021-10-01; domestic single payments by 2021-11-30; foreign/SWIFT and corporate payments by 2022-05-31; bulk payments from 2022-05-31; card-account sharing from 2022-03-28.
- Payment data model (8.2): endToEndIdentification Max35, debtorName Max70, remittanceInformationUnstructured Max140 XOR ...Array, additionalInformation Max500; per-product required/optional matrix; RTGS (MT103-like) and SWIFT field mapping; treasury payments use `creditorAccount.other.identification` (treasury code) and `creditorAgent=TRESGE22`; `chargeBearer` only `OUR` (or absent) for domestic GEL, default `OUR`, `SLEV` forbidden on foreign (`PAYMENT_FAILED`); `instructionPriority` NORM|HIGH (HIGH = individual RTGS); `aspsp` product may identify creditor by `maskedPan`/`pan`/`msisdn` (995577123456) or just `creditorIdentification`.
- Payment statuses (8.10): ACCC, ACCP, ACSC, ACSP, ACTC, ACWC, ACWP, RCVD (never returned), PDNG (discouraged), RJCT, CANC, ACFC, PATC, PART; `fundsAvailable` flag required while ACTC/ACWC/ACCP; DELETE returns 202/204/405.
- Consent (9.1): body fields `access, frequencyPerDay, recurringIndicator, validUntil, combinedServiceIndicator`; access keys `accounts, balances, transactions, additionalInformation{ownerName}, availableAccounts("allAccounts"|"allAccountsWithOwnerName"), availableAccountsWithBalance`; `restrictedTo` discouraged; AccountReference supports `iban` (mandatory), `pan`/`maskedPan` (cards), `msisdn`, `currency`, `cashAccountType` (>= `CACC`,`CARD`); `bban` never. Consent states per BG (`received, rejected, valid, revokedByPsu, expired, terminatedByTpp, partiallyAuthorised`); SCA validity/limits (90 days / 4 per day) are quoted for PASHA's "Georgian standard" column (see pasha.md).
- Card accounts (9.4): optional until 2022-03-28 then mandatory; PAN must be masked everywhere; `pan` accepted only in consent request and converted at once (PCI DSS).


## 5. KPI / Metrics numbers (https://<host>/ob-home/app/analytics -> `POST /ob-home/api-analytics/get-analytics-kpi`)
Response = `{"sandboxData":[13 monthly rows],"productionData":[13 monthly rows]}`; row = `{year, month(0-based JS month!), upTime, downTime, averageAISResponseTime, averagePISResponseTime, [averageCLResponseTime], [averageLISResponseTime], [averageSISResponseTime], errorPercent}`; UI columns "AISP avg." / "PISP/PIISP avg." / "CL avg." / "AISP avg. (Loans)" / "AISP avg. (Savings)" / Uptime / Downtime / Error rate; null => "N/A". A GET returns an HTTP 405 HTML page. Raw JSON: `raw/kpi_post_response.json` (data as of 2026-10-20; the October-2026 row is a partial month and changes between calls, e.g. Credo's October uptime was 99.618 in the earlier pre-fetch and 99.667 now).

**PRODUCTION** (monthly; JSON month is 0-based, so month 9 of 2026 = October 2026 = partial month, data as of 2026-10-20)

| Month | Uptime % | Downtime % | AIS avg ms | PIS avg ms | CL avg ms | Loans(LIS) avg ms | Savings(SIS) avg ms | Error % |
|---|---|---|---|---|---|---|---|---|
| 2026-Oct | 99.995 | 0.005 | 2297 | 246 | N/A | N/A | N/A | 1.923 |
| 2026-Sep | 99.978 | 0.022 | 3168 | N/A | N/A | N/A | N/A | 0.053 |
| 2026-Aug | 99.980 | 0.020 | 8419 | N/A | N/A | N/A | N/A | 0.062 |
| 2026-Jul | 99.995 | 0.005 | 5667 | 139 | N/A | N/A | N/A | 0.185 |
| 2026-Jun | 99.761 | 0.239 | 2158 | N/A | 234 | N/A | N/A | 0.314 |
| 2026-May | 100.000 | 0.000 | 2688 | N/A | N/A | N/A | N/A | 0.150 |
| 2026-Apr | 99.998 | 0.002 | 3786 | N/A | N/A | N/A | N/A | 0.193 |
| 2026-Mar | 99.617 | 0.383 | 2675 | N/A | N/A | N/A | N/A | 0.215 |
| 2026-Feb | 100.000 | 0.000 | 3071 | N/A | N/A | N/A | N/A | 0.150 |
| 2026-Jan | 99.981 | 0.019 | 2992 | N/A | N/A | N/A | N/A | 0.085 |
| 2025-Dec | 100.000 | 0.000 | 2995 | N/A | N/A | N/A | N/A | 0.036 |
| 2025-Nov | 100.000 | 0.000 | 1894 | N/A | N/A | N/A | N/A | 0.049 |
| 2025-Oct | 100.000 | 0.000 | 1164 | N/A | N/A | N/A | N/A | 0.102 |

**SANDBOX** (monthly; JSON month is 0-based, so month 9 of 2026 = October 2026 = partial month, data as of 2026-10-20)

| Month | Uptime % | Downtime % | AIS avg ms | PIS avg ms | CL avg ms | Loans(LIS) avg ms | Savings(SIS) avg ms | Error % |
|---|---|---|---|---|---|---|---|---|
| 2026-Oct | 99.995 | 0.005 | 48 | 43 | 48 | N/A | N/A | 0.000 |
| 2026-Sep | 99.978 | 0.022 | 72 | 39 | 87 | N/A | N/A | 0.000 |
| 2026-Aug | 99.980 | 0.020 | 44 | 41 | N/A | N/A | N/A | 0.000 |
| 2026-Jul | 99.995 | 0.005 | 43 | 42 | N/A | N/A | N/A | 0.000 |
| 2026-Jun | 99.761 | 0.239 | 59 | 54 | N/A | N/A | N/A | 0.000 |
| 2026-May | 100.000 | 0.000 | 50 | 35 | 38 | N/A | N/A | 0.000 |
| 2026-Apr | 99.998 | 0.002 | 39 | 37 | 71 | N/A | N/A | 0.000 |
| 2026-Mar | 99.617 | 0.383 | 188 | 92 | N/A | N/A | N/A | 0.000 |
| 2026-Feb | 100.000 | 0.000 | 331 | 170 | N/A | N/A | N/A | 0.000 |
| 2026-Jan | 99.981 | 0.019 | 362 | 169 | N/A | N/A | N/A | 0.000 |
| 2025-Dec | 100.000 | 0.000 | N/A | N/A | N/A | N/A | N/A | 0.000 |
| 2025-Nov | 100.000 | 0.000 | N/A | N/A | N/A | N/A | N/A | 0.000 |
| 2025-Oct | 100.000 | 0.000 | N/A | N/A | N/A | N/A | N/A | 0.000 |

Observation: sandbox and production upTime/downTime identical in every month: True.
Halyk notes: production AIS is SLOW and spiky: 1.16 s (2025-Oct) up to 8.42 s (2026-Aug), 2.3 s partial Oct-2026; production PIS is N/A in 11 of 13 months (only 2026-Jul 139 ms, 2026-Oct 246 ms) and CL only 2026-Jun (234 ms) => PIS in production is barely used. Production error rate: 0.04-0.31 % per month, 1.92 % in partial Oct-2026. Sandbox AIS 39-362 ms (Jan-Feb 2026 330-362 ms), PIS 35-170 ms, CL 38-87 ms in 4 months; sandbox AIS/PIS N/A for 2025-Oct..Dec.

## 6. Could not obtain / unverified (shared)
- No live gateway call was made (no auth attempts, per instructions) => real behaviour of `openbankgw.*` (status codes, whether paths are `/0.8/v1/accounts`, header enforcement, sandbox data, error bodies) is UNVERIFIED.
- No sandbox test IBANs/consent ids/account fixtures published. No English version of the Georgian guides on these portals (PASHA's links to an `EN` v0.6 on association.ge return HTTP 404).
- Only the IAA guide TOC and identifier chapters were read in detail (Georgian prose); SCA screens (guide ch.10) and card chapter (9.4) were only skimmed.
- Meaning of the KPI column "CL avg." is not documented anywhere I found (possibly Client Identification/OIDC - unverified). The advertised "Products" service has no published definition on these portals (only BoG's sibling spec shows a possible shape).

### Halyk-specific unverified
- `openbankgw.hbg.ge` not contacted. Home/Integration page text was read from the rendered DOM; the last three document hrefs on /integration were truncated in my capture (assumed identical to Credo's, confirmed by identical PDF hashes obtained via the Credo-style paths on the Halyk host).
- Why production AIS averages are 1-8 s (and PIS N/A) is not explained on the site.
