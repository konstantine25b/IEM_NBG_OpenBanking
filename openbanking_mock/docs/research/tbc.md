# TBC Bank (Georgia) Open Banking API - research notes

Prepared 2026-10-20 (UTC). Everything below is derived from public documentation pages only; no bank API endpoint was called. All downloaded content was treated as untrusted data and parsed with `/usr/bin/python3 -I` (jsonschema checks used the project venv's `python3 -I`).

## 1. Sources and provenance

### 1.1 How the machine-readable definitions were obtained

* A full OpenAPI download is NOT available. Evidence: the portal's project settings (inside every page's SSR state) have `showOpenAPIDownload: false` and `openapiVisibility: admin`; `https://developers.tbcbank.ge/openapi/5ugjg1jmacba1am` answers `302 -> /404`; `https://dash.readme.com/api/v1/api-registry/<uuid>` is 404 (ruled out earlier by the requester; the 7 saved 404 bodies are in `raw/_ruled_out_api_registry_404/`); `https://developers.tbcbank.ge/.well-known/api-catalog` is 404.
* What does work: every `https://developers.tbcbank.ge/reference/<slug>` page for an operation (`type: endpoint`) embeds, in `<script id="ssr-props" type="application/x-ssr-props">` -> `document.api.schema`, an OpenAPI document (3.0.1 for AIS and loans/savings, 3.0.4 for the two payments files) holding that ONE operation plus every component it references (schemas, parameters, headers, examples, securitySchemes), `servers`, `security` and `x-readme`. `document.api.uri` names the parent spec file (e.g. `/branches/1.0/apis/openbanking-service-for-loans-and-savings.json`) and ReadMe's own `document.api.validation.status` (not my check) was `valid` for all 48 fetched operations.
* I fetched 48 operation pages, merged the fragments per parent spec file (union of `paths` and `components`; same-name components with different bodies would be flagged; none were), and checked that every `$ref` resolves. Results:
  * `conflicts: []` and `unresolved_refs: []` for all four files.
  * One defect inherited from upstream: the AIS file's consent operations reference security scheme `BearerAuthOAuth` which is never defined in that file (only `BearerAuth` is). It is recorded in each file's `x-provenance.undefined_security_schemes`.
  * Not run through an OpenAPI meta-schema validator (none installed). Only $ref resolution and example-vs-schema validation (section 4.3) were done.
* The assembled files carry an `x-provenance` block (source page per operation, conflicts, unresolved refs). They are reconstructions, not official downloads. Paths are verbatim from the fragments' `paths` keys, never derived from slugs.

### 1.2 Files

All under `/tmp/claude-1000/-home-bezhomatiashvili-Desktop-hakathon/d833bfdd-adf6-41b0-9485-b22b3bba4fc9/scratchpad/specs/tbc/`:

| File | Content |
|---|---|
| `openbanking-services-account-information-service-ais.assembled.json` | AIS: 18 ops, 16 paths (accounts, card-accounts, consents v1, OAuth). Complete for the sidebar group. |
| `openbanking-service-for-loans-and-savings.assembled.json` | Loans/savings: 16 ops, 14 paths (consents v2, OAuth, loans, savings). Complete for the sidebar group. |
| `openbanking-services-payments.assembled.json` | Payments v1: 6 of 16 sidebar ops. |
| `openbanking-services-payments-v2.assembled.json` | Payments v2 (incl. deferred/recurring/secured): 8 of 21 sidebar ops. |
| `inventory_rows.json`, `example_validation.json`, `example_validation_strict.json`, `sidebar_endpoints.json` | Machine-readable inventory, example-vs-schema results, full sidebar operation list (71 OB operations; 23 not fetched). |
| `raw/MANIFEST.tsv` | Every download: file, exact URL, UTC fetch time, HTTP status, bytes, sha256 prefix, notes. |
| `raw/DERIVED.tsv` | Derived files (per-op fragments, assembled files) and their source pages. |
| `raw/pages/*.html` (+ `.hdr` response headers) | Raw HTML of every page fetched (operation pages named `ref_<slug>.html`, docs pages `docs_<slug>.html`, reference overview pages `reference_<slug>.html`). |
| `raw/ops/<slug>.oas.json`, `.meta.json`, `.body.md` | Per-operation OAS fragment; page metadata; page markdown body (empty for endpoint pages, descriptions live in the OAS). |
| `raw/github/*` | Public TBCBank GitHub material (JWS library README, samples, tree, org repo list). |
| `raw/_probe/*` | The failed download probes (`/openapi/...` 302, `api-catalog` 404). |

Tooling used (all mine, in `.../scratchpad/tools/`): `fetch_tbc.sh` (sequential, >=5 s gap, hard cap, auto-backoff on 429 or the "Hello there, human!" interstitial), `batch.sh`, `extract.py`, `assemble.py`, `inventory.py`, `validate_examples.py`, `make_tables.py`.

### 1.3 Request log

* 58 requests to `developers.tbcbank.ge` by me between 2026-10-20T16:55:53Z and 17:06:21Z; strictly sequential, >=5 s apart; zero 429s or interstitials. (The requester's earlier probes are additional.) Two pages (`docs/open-banking-overview`, `docs/open-banking-statistics`) were REUSED from the requester's earlier probe files rather than re-fetched; their fetch time in the manifest is the file mtime (approx.) and the URL is taken from the page's own `reqUrl`.
* The earlier `specs/tbc/pages/*.html|md` files (21 KB each) are all the ReadMe rate-limit interstitial ("Hello there, human!"), not content. They can be deleted.
* Portal rate-limit headers observed on doc pages: `x-ratelimit-limit: 100` (reset ~2-3 s later) on reference pages and `30` on a 404 path. That is the doc portal's CDN limit, unrelated to the bank API limits.
* GitHub (different hosts, 11 requests): `api.github.com/orgs/TBCBank/repos`, the `TBC.OpenBanking.Jws` tree, and raw files. Note: `MANIFEST.tsv` also contains two foreign rows (`github_org_paysera_repos_p1/p2.json`, HTTP 000) appended by another process that reused my `fetch.sh`; ignore them.
* Nothing else was contacted. `.well-known` OAuth/OpenID endpoints on `test-api.tbcbank.ge` were deliberately NOT called (they are bank endpoints).

### 1.4 GitHub org TBCBank (public)

14 repos; no OpenAPI/Swagger repo. Relevant: `TBC.OpenBanking.Jws` (C#, MIT, README in Georgian) - the request/response signing library and samples. Others are SDKs for non-PSD2 products (`TBC.OpenAPI.SDK.*`: Core, ExchangeRates, OnlineInstallments, OnlineMortgage, DBI, BusinessIntegrationServices), plus unrelated repos. There is no SDK for the PSD2 AIS/PIS APIs besides the JWS handler.

## 2. Base URLs, auth, headers, versions

### 2.1 Servers (verbatim from each spec's `servers`)

| Spec | Entries |
|---|---|
| AIS | `https://test-openbanking.tbcbank.ge/0.8` (Sandbox), `https://openbanking.tbcbank.ge/0.8` (Production), `https://test-api.tbcbank.ge` (Sandbox for .well-known and authorize endpoints), `https://api.tbcbank.ge` (Production for .well-known and authorize endpoints) |
| Loans/Savings | `https://test-api.tbcbank.ge/xs2a-sandbox`, `https://test-openbanking.tbcbank.ge`, `https://openbanking.tbcbank.ge` (no descriptions; NOTE no `/0.8`; the docs text says test base is `https://test-openbanking.tbcbank.ge`) |
| Payments v1 | `https://test-openbanking.tbcbank.ge/0.8`, `https://openbanking.tbcbank.ge/0.8` |
| Payments v2 | `https://test-openbanking.tbcbank.ge`, `https://openbanking.tbcbank.ge` (no `/0.8`) |

Examples in the AIS spec show links as `/0.8/v1/accounts/{id}/balances` (i.e. the `/0.8` prefix is part of returned hrefs), while loans/savings sample links are relative without it (`v2/savings/{id}/balances`). Whether v2 endpoints really live under `/0.8` is unverified.

Example OAuth discovery document (AIS): `issuer` `https://test-api.tbcbank.ge/.well-known`, `authorization_endpoint` `https://test-api.tbcbank.ge/psd2/openbanking/oauth/authorize`, `token_endpoint` `https://test-openbanking.tbcbank.ge/openbanking/oauth/token`, `registration_endpoint` `.../openbanking/oauth/register` (no published operation), `revocation_endpoint` same URL as token, `token_endpoint_auth_methods_supported: ["tls_client_auth"]`, `scopes_supported: ["openid","AIS:<consentId>","PIS:<paymentId>"]`, `response_types_supported: ["code"]`, `grant_types_supported: ["authorization_code","refresh_token"]`, `ui_locales_supported: ["en","ka"]`. The v1 consent response's `scaOAuth` href points to `https://test-api.tbcbank.ge/.well-known/oauth-authorization-server`; the v2 equivalent points to `https://test-openbanking.tbcbank.ge/openbanking/oauth/.well-known/oauth-authorization-server` (different path, different host). The consent page (v2 `Location` header example) is `https://customerconsent.tbcbank.ge/loginappsandbox/session?sessionid=<uuid>`.

### 2.2 Authentication and security

* Transport: mutual TLS with a PSD2 certificate. Docs page "Get a Certificate for API Products": a certificate is mandatory in both test and production for the PSD2 APIs (AIS, PIS). Test: bank-issued test certificate on request. Production: QWAC / QSeal from an NBG-authorised trust provider (for FinTech/microfinance organisations; banks: certificate from a trusted provider licensed by the National Bank). The token endpoint only supports `tls_client_auth`.
* OAuth 2.0 authorization-code flow ("OAuth SCA approach"; the overview also lists a Redirect SCA approach). Sequence documented on the API overview pages: (1) `POST` create consent -> `consentId` + `_links.scaOAuth`; (2) `GET` OAuth authorization server metadata; (3) `GET /psd2/openbanking/oauth/authorize?client_id&scope=AIS:<consentId>&response_type=code&redirect_uri&nonce&state[&code_challenge&code_challenge_method=S256&ui_locales]` -> HTTP 302 to the TBC login page; code returned on `redirectUri`; (4) `POST /openbanking/oauth/token` (form-encoded `client_id`, `grant_type` in {authorization_code, refresh_token, client_credentials}, `code`, `code_verifier`, `refresh_token`, `redirect_uri`) -> `access_token`, `token_type: Bearer`, `refresh_token`, `refresh_token_issued_at`, `refresh_token_expires_in`, `refresh_count`, `scope`, `issued_at`, `expires_in`; (5) call resource endpoints with `Authorization: Bearer`. PKCE: only `S256` is supported.
* Per-operation security: account/card-account/loan/savings data ops require the Bearer token; consent create / start-authorisation / OAuth ops have no security; the other consent ops declare `BearerAuthOAuth` (AIS: scheme undefined, see 1.1).
* JWS request signing (docs + `TBC.OpenBanking.Jws` README + source): header `X-JWS-Signature` carries a detached JWS `base64url(protectedHeader) + ".." + base64url(signature)`. Protected header JSON: `alg` (RS256/384/512, PS256/384/512, ES256/384/512; sample config uses RS256), `x5c` (signer cert then chain, no root), `crit: ["sigT","sigD","b64"]`, `b64: false`, `sigT` (UTC signing time), `sigD: {mId: "http://uri.etsi.org/19182/HttpHeaders", pars: [lower-cased header names]}`. Signed data = the listed headers (`host`, `x-request-id`, `digest`, optionally `content-type`/`content-length`, and every header starting with `psu-`) followed by the body. `Digest: SHA-256=<base64>` (SHA-384/512 prefixes also supported). Both requests and responses are signed in the library; verification checks signature, cert chain/CRL and a signing-time window (`CheckSignatureTimeConstraint`). Spec text: "Signed data must be passed in header x-jws-signature for all the requests". Georgian Banking Association profile "OpenFinance JSON Web Signature Profile 0.5.RC1" is the reference.
* API keys are for TBC's non-PSD2 "Open API" products, not these services. The token request schema also lists `grant_type=client_credentials`, but the discovery document advertises only `authorization_code` and `refresh_token`.

### 2.3 Headers (from component parameters)

AIS data calls (`AIS-READ-HDRS` in the table): `X-Request-ID*` (uuid), `Consent-ID*`, `Digest*`, `X-JWS-Signature*`, `PSU-IP-Address*`, `Accept-Language` (example `ka`). Consent management (`CONSENT-HDRS`): `X-Request-ID*`, `Digest*`, `X-JWS-Signature*`, `PSU-IP-Address*`, `Accept-Language`, `PSU-IP-Port`, `PSU-Geo-Location`. Consent creation / starting an authorisation additionally: `PSU-ID`, `PSU-Corporate-ID`, `TPP-Redirect-URI*` (uri), `TPP-Explicit-Authorisation-Preferred`, `TPP-Redirect-Preferred`, `TPP-Decoupled-Preferred` (booleans). Response headers for 201: `X-Request-ID`, `Location`, `ASPSP-SCA-Approach` (DECOUPLED|REDIRECT). Loans/savings data calls differ: `X-Request-ID*`, header named `consentId*` (camelCase, not `Consent-ID`), `PSU-IP-Address*`; no `Digest`/`X-JWS-Signature` listed even though the docs say every call needs JWS. Payments (`PAY-HDRS`): `X-Request-ID*`, `PSU-ID`, `PSU-ID-Type`, `PSU-IP-Address` (required only on `POST /v1/payments/{payment-product}`), `PSU-IP-Port`, `PSU-Geo-Location`, `Accept-Language`, `TPP-Redirect-URI`, `X-Apigee-UserId`, `TPP-Explicit-Authorisation-Preferred`, `TPPRejectionNoFundsPreferred`, `Consent-ID`; no Digest/JWS header parameters are declared there even though the payments overview requires JWS.

Berlin Group differences visible here: `X-JWS-Signature` + `Digest` replace `Signature`/`TPP-Signature-Certificate`; `PSU-IP-Address` is mandatory; `Consent-ID` is a header on all AIS reads; `TPP-Redirect-URI` is mandatory on consent creation and start-authorisation. The sample C# client sends `X-Request-ID` as a Guid in "N" format (no dashes) although the spec says `format: uuid`, and `PSU-Accept-Language` (spec: `Accept-Language`).

### 2.4 Versions (checked)

"NextGenPSD2 Framework Version 1.3.6" appears only in the prose of `docs/open-banking-overview` (it also lists the Redirect and OAuth SCA approaches). The machine-readable specs say `openapi: 3.0.1`, `info.version: "1.0"` (docs branch 1.0), base path `/0.8` on most servers, and `v1`/`v2` in paths. The JWS README cites the Banking Association "XS2A Implementation Guide v0.6 2020-09-16" and JWS profile 0.5.RC1. So "1.3.6" is the Berlin Group baseline claim; it cannot be confirmed from the spec files. Spec fragments were last updated on the portal 2026-03-25; the docs pages 2026-10-16/19.

### 2.5 Consent semantics, errors, rate limits

* Consent: `frequencyPerDay` integer >=1, "if not otherwise agreed ... less equal to 4"; `validUntil` / `validTo` max 90 days (longer values are silently decreased to 90 days); a new consent expires the previous AIS consent for the PSU once authorised. ConsentStatus enum: `received, rejected, valid, revokedByPsu, expired, terminatedByTpp`. ScaStatus enum: `received, psuIdentified, psuAuthenticated, scaMethodSelected, started, unconfirmed, finalised, failed, exempted`.
* Error body = `ApiProblemDetails` `{type, title, status, detail, instance, traceId, code}`; `type` looks like `https://developers.tbcbank.ge/docs/error-type-400` (only the 400 form was seen in examples). Codes seen in published examples: 400 `FORMAT_ERROR`, 400 `RESOURCE_BLOCKED`, 401 `CONSENT_EXPIRED`, 401 `CONSENT_INVALID`, 401 `TOKEN_EXPIRED`, 403/404 `CONSENT_UNKNOWN`, 403 `STATUS_INVALID`, 404 `RESOURCE_UNKNOWN`, 429 `ACCESS_EXCEEDED` ("Too many request"; appears as an example under payments v1 operations, no 429 response is declared on any operation), 500 with empty code ("Something wrong happened in the application"). Example: `{"code":"FORMAT_ERROR","traceId":"00-5336c4b7...-01","type":"https://developers.tbcbank.ge/docs/error-type-400","title":"Invalid Request","status":400,"detail":"ValidUntil parameter is invalid, please check the date.","instance":"/v1/accounts"}`.
* Rate limits: no numeric limit is published for the PSD2 APIs. The generic docs page `error-type-429` only says requests are throttled per "predefined time period" and to contact the API support team for different policies. Only the consent-level `frequencyPerDay` (<=4 unattended accesses per day) is documented as a quota.
* Pagination: none beyond `_links.first/next/previous` on transactions plus `entryReferenceFrom`/`dateFrom`/`dateTo`; there are no page-size/page-number parameters. `LinksDownloadResponse.download` exists for transaction downloads (no operation documented).

## 3. Operation inventory

Source of every row: the assembled spec named in the "Spec file" column (AIS = `openbanking-services-account-information-service-ais`, LS = `openbanking-service-for-loans-and-savings`, PAY1 = `openbanking-services-payments`, PAY2 = `openbanking-services-payments-v2`). Header shorthands: AIS-READ-HDRS = X-Request-ID*, Consent-ID*, Digest*, X-JWS-Signature*, PSU-IP-Address*, Accept-Language. CONSENT-HDRS = X-Request-ID*, Digest*, X-JWS-Signature*, PSU-IP-Address*, Accept-Language, PSU-IP-Port, PSU-Geo-Location. CONSENT-AUTH-START-HDRS = CONSENT-HDRS + PSU-ID, PSU-Corporate-ID, TPP-Redirect-URI*, TPP-Redirect-Preferred, TPP-Decoupled-Preferred. CONSENT-CREATE-HDRS = CONSENT-AUTH-START-HDRS + TPP-Explicit-Authorisation-Preferred. PAY-HDRS = X-Request-ID*, PSU-ID, PSU-ID-Type, PSU-IP-Address, PSU-IP-Port, PSU-Geo-Location, Accept-Language, TPP-Redirect-URI, X-Apigee-UserId, TPP-Explicit-Authorisation-Preferred, TPPRejectionNoFundsPreferred, Consent-ID. "Success example" = an example exists for the 2xx/3xx response body or for the request body (`y`), does not exist (`n`), or there is no success body at all (`-`: 204/302 or no 2xx documented). "Error examples" = at least one 4xx/5xx example exists. Notable: all three `/v2/loans*` operations have NO example of any kind (neither success nor error); five payments v2 operations have error examples only; v1 payment DELETE documents no 2xx response (its only examples are error bodies). Errors on every op use ApiProblemDetails.

48 operations obtained with full detail (all AIS, consents v1/v2, OAuth for AIS and loans/savings, loans, savings; a representative subset of payments):

| # | Method | Path (verbatim from spec) | Spec file | Params ([p]ath [q]uery [h]eader, * = required) | Request schema | Response schema (success) | Success example | Error examples |
|---|---|---|---|---|---|---|---|---|
| 1 | GET | `/v1/accounts` | AIS | withBalance[q], AIS-READ-HDRS | - | 200: GetPsd2AccountsResponse | y | y |
| 2 | GET | `/v1/accounts/{account-id}` | AIS | account-id[p*], withBalance[q], AIS-READ-HDRS | - | 200: GetPsd2AccountResponse | y | y |
| 3 | GET | `/v1/accounts/{account-id}/balances` | AIS | account-id[p*], AIS-READ-HDRS | - | 200: GetBalanceResponse | y | y |
| 4 | GET | `/v1/accounts/{account-id}/transactions` | AIS | account-id[p*], dateFrom[q], dateTo[q], bookingStatus[q], withBalance[q], entryReferenceFrom[q], AIS-READ-HDRS | - | 200: GetPsd2TransactionsResponse | y | y |
| 5 | GET | `/v1/card-accounts` | AIS | withBalance[q], AIS-READ-HDRS | - | 200: GetCardAccountsResponse | y | y |
| 6 | GET | `/v1/card-accounts/{account-id}` | AIS | account-id[p*], withBalance[q], AIS-READ-HDRS | - | 200: GetCardAccountResponse | y | y |
| 7 | GET | `/v1/card-accounts/{account-id}/balances` | AIS | account-id[p*], AIS-READ-HDRS | - | 200: GetCardBalanceResponse | y | y |
| 8 | GET | `/v1/card-accounts/{account-id}/transactions` | AIS | account-id[p*], dateFrom[q], dateTo[q], bookingStatus[q], withBalance[q], entryReferenceFrom[q], AIS-READ-HDRS | - | 200: GetCardTransactionsResponse | y | y |
| 9 | POST | `/v1/consents` | AIS | CONSENT-CREATE-HDRS | CreateConsentRequest | 201: CreateConsentResponse | y | y |
| 10 | DELETE | `/v1/consents/{consentId}` | AIS | consentId[p*], CONSENT-HDRS | - | 204: (no body) | - | y |
| 11 | GET | `/v1/consents/{consentId}` | AIS | consentId[p*], CONSENT-HDRS | - | 200: GetConsentResponse | y | y |
| 12 | GET | `/v1/consents/{consentId}/authorisations` | AIS | consentId[p*], CONSENT-HDRS | - | 200: AuthorisationIdsResponse | y | y |
| 13 | POST | `/v1/consents/{consentId}/authorisations` | AIS | consentId[p*], CONSENT-AUTH-START-HDRS | - | 201: StartAuthorisationResponse | y | y |
| 14 | GET | `/v1/consents/{consentId}/authorisations/{authorisationId}` | AIS | consentId[p*], authorisationId[p*], CONSENT-HDRS | - | 200: AuthorisationStatusResponse | y | y |
| 15 | GET | `/v1/consents/{consentId}/status` | AIS | consentId[p*], CONSENT-HDRS | - | 200: GetConsentStatusResponse | y | y |
| 16 | GET | `/.well-known/oauth-authorization-server` | AIS | - | - | 200: OAuthAuthorizationServerResponse | y | y |
| 17 | POST | `/openbanking/oauth/token` | AIS | - | OauthTokenRequest [application/x-www-form-urlencoded] | 200: OauthTokenResponse | y | y |
| 18 | GET | `/psd2/openbanking/oauth/authorize` | AIS | client_id[q*], scope[q*], response_type[q*], redirect_uri[q*], nonce[q*], code_challenge_method[q], code_challenge[q], ui_locales[q], state[q*] | - | 302: (no body) | - | n |
| 19 | POST | `/v2/consents/account-access` | LS | CONSENT-CREATE-HDRS | CreateConsentRequest | 201: CreateConsentResponse | y | y |
| 20 | DELETE | `/v2/consents/account-access/{consentId}` | LS | consentId[p*], CONSENT-HDRS | - | 204: (no body) | - | y |
| 21 | GET | `/v2/consents/account-access/{consentId}` | LS | consentId[p*], CONSENT-HDRS | - | 200: GetConsentResponse | y | y |
| 22 | GET | `/v2/consents/account-access/{consentId}/authorisations` | LS | consentId[p*], CONSENT-HDRS | - | 200: AuthorisationIdsResponse | y | y |
| 23 | POST | `/v2/consents/account-access/{consentId}/authorisations` | LS | consentId[p*], CONSENT-AUTH-START-HDRS | - | 201: StartAuthorisationResponse | y | y |
| 24 | GET | `/v2/consents/account-access/{consentId}/authorisations/{authorisationId}` | LS | consentId[p*], authorisationId[p*], CONSENT-HDRS | - | 200: AuthorisationStatusResponse | y | y |
| 25 | GET | `/v2/consents/account-access/{consentId}/status` | LS | consentId[p*], CONSENT-HDRS | - | 200: GetConsentStatusResponse | y | y |
| 26 | GET | `/v2/loans` | LS | PSU-IP-Address*[h], X-Request-ID*[h], consentId*[h] | - | 200: LoansResponse | n | n |
| 27 | GET | `/v2/loans/{resourceId}` | LS | resourceId[p*], withBalance[q], PSU-IP-Address*[h], X-Request-ID*[h], consentId*[h] | - | 200: LoanResponse | n | n |
| 28 | GET | `/v2/loans{resourceId}/balances` | LS | resourceId[p*], PSU-IP-Address*[h], X-Request-ID*[h], consentId*[h] | - | 200: BalancesResponse | n | n |
| 29 | GET | `/v2/savings` | LS | withBalance[q], PSU-IP-Address*[h], X-Request-ID*[h], consentId*[h] | - | 200: SavingsResponse | y | n |
| 30 | GET | `/v2/savings/{resourceId}` | LS | resourceId[p*], withBalance[q], PSU-IP-Address*[h], X-Request-ID*[h], consentId*[h] | - | 200: SavingAccountResponse | y | n |
| 31 | GET | `/v2/savings/{resourceId}/balances` | LS | resourceId[p*], PSU-IP-Address*[h], X-Request-ID*[h], consentId*[h] | - | 200: GetSavingBalanceResponse | y | n |
| 32 | GET | `/openbanking/oauth/.well-known/oauth-authorization-server` | LS | - | - | 200: OAuthAuthorizationServerResponse | y | y |
| 33 | POST | `/openbanking/oauth/token` | LS | - | OauthTokenRequest [application/x-www-form-urlencoded] | 200: OauthTokenResponse | y | y |
| 34 | GET | `/psd2/openbanking/oauth/authorize` | LS | client_id[q*], scope[q*], response_type[q*], redirect_uri[q*], nonce[q*], code_challenge_method[q], code_challenge[q], ui_locales[q], state[q*] | - | 302: (no body) | - | n |
| 35 | POST | `/v1/bulk-payments/{payment-product}` | PAY1 | payment-product[p*], PAY-HDRS | BulkPaymentInitiationRequest | 201: PaymentInitiationResponse | y | y |
| 36 | POST | `/v1/payments/{payment-product}` | PAY1 | payment-product[p*], Accept-Language[h], Consent-ID[h], PSU-Geo-Location[h], PSU-ID-Type[h], PSU-ID[h], PSU-IP-Address*[h], PSU-IP-Port[h], TPP-Explicit-Authorisation-Preferred[h], TPP-Redirect-URI[h], TPPRejectionNoFundsPreferred[h], X-Apigee-UserId[h], X-Request-ID*[h] | PaymentInitiationRequest | 201: PaymentInitiationResponse | y | y |
| 37 | GET | `/v1/payments/{payment-product}/{paymentId}` | PAY1 | payment-product[p*], paymentId[p*], PAY-HDRS | - | 200: GetPaymentResponse | y | y |
| 38 | GET | `/v1/payments/{payment-product}/{paymentId}/status` | PAY1 | payment-product[p*], paymentId[p*], PAY-HDRS | - | 200: GetPaymentStatusResponse | y | y |
| 39 | DELETE | `/v1/{payment-service}/{payment-product}/{paymentId}` | PAY1 | payment-service[p*], payment-product[p*], paymentId[p*], PAY-HDRS | - | (none documented) | - | y |
| 40 | POST | `/v1/{payment-service}/{payment-product}/{paymentId}/authorisations` | PAY1 | payment-service[p*], payment-product[p*], paymentId[p*], PAY-HDRS | - | 201: StartScaprocessResponse | y | n |
| 41 | POST | `/v2/multiple-recurring-payments/{payment-product}` | PAY2 | payment-product[p*], PAY-HDRS | MultipleRecurringConsentInitiationRequest | 201: ExtendedPaymentInitiationResponse | n | y |
| 42 | POST | `/v2/recurring-payments/{payment-product}` | PAY2 | payment-product[p*], PAY-HDRS | RecurringConsentInitiationRequest | 201: PaymentInitiationResponse | y | y |
| 43 | POST | `/v2/secured-payments/{payment-product}` | PAY2 | payment-product[p*], PAY-HDRS | ConsentInitiationRequest | 201: PaymentInitiationResponse | y | y |
| 44 | GET | `/v2/secured-payments/{payment-product}/{paymentId}` | PAY2 | payment-product[p*], paymentId[p*], PAY-HDRS | - | 200: GetExtendedPaymentsResponse | n | y |
| 45 | GET | `/v2/secured-payments/{payment-product}/{paymentId}/status` | PAY2 | payment-product[p*], paymentId[p*], PAY-HDRS | - | 200: GetExtendedPaymentsStatusResponse | n | y |
| 46 | POST | `/v2/{deferred-payment-service}/{payment-product}` | PAY2 | deferred-payment-service[p*], payment-product[p*], PAY-HDRS | DeferredConsentInitiationRequest | 201: ExtendedPaymentInitiationResponse | n | y |
| 47 | POST | `/v2/{deferred-payment-service}/{payment-product}/{paymentId}/initiations` | PAY2 | deferred-payment-service[p*], payment-product[p*], paymentId[p*], X-Request-ID*[h] | DeferredPaymentInitiationRequest | 201: PaymentInitiationResponse | y | y |
| 48 | GET | `/v2/{extended-payment-service}/{payment-product}/{paymentId}/status` | PAY2 | extended-payment-service[p*], payment-product[p*], paymentId[p*], X-Request-ID*[h] | - | 200: GetConsentStatusResponse | n | y |
### 3.1 Operations in the sidebar that were NOT fetched (23; request budget)

Method comes from the sidebar. The paths are not verified and the parameters/schemas are unknown. Likely, but NOT confirmed: the three payments OAuth ops duplicate the AIS OAuth ops, and the GET/DELETE variants reuse the same parameter sets as their fetched siblings.

| Group | Method | Sidebar slug (path NOT verified) |
|---|---|---|
| Payments | GET | `get_v1-bulk-payments-payment-product-paymentid` (Get bulk payment information) |
| Payments | GET | `get_v1-bulk-payments-payment-product-paymentid-status` (Payment initiation request) |
| Payments | GET | `get_v1-payment-service-payment-product-paymentid-authorisations` (Get Authorisations) |
| Payments | GET | `get_v1-payment-service-payment-product-paymentid-authorisations-authorisationid` (Get Authorisation Sca Status) |
| Payments | POST | `post_v1-payment-service-payment-product-paymentid-cancellation-authorisations` (Cancellation Authorisations) |
| Payments | GET | `get_v1-payment-service-payment-product-paymentid-cancellation-authorisations` (Get Cancellation Authorisations) |
| Payments | GET | `get_v1-payment-service-payment-product-paymentid-cancellation-authorisations-authorisationid` (Get Cancellation Authorisation Status) |
| Payments | GET | `get_openbanking-oauth-well-known-oauth-authorization-server` (Oauth Authorization Server) |
| Payments | GET | `get_psd2-openbanking-oauth-authorize` (Authorization) |
| Payments | POST | `post_openbanking-oauth-token` (Authorize or Refresh Token) |
| Payments V2 | GET | `get_v2-deferred-payments-payment-product-paymentid` (Get deferred payment request) |
| Payments V2 | GET | `get_v2-secured-deferred-payments-payment-product-paymentid` (Get secured deferred payment request) |
| Payments V2 | GET | `get_v2-multiple-deferred-payments-payment-product-paymentid` (Get multiple deferred payment request) |
| Payments V2 | DELETE | `delete_v2-extended-payment-service-payment-product-paymentid-1` (Extended payment cancellation request) |
| Payments V2 | POST | `post_v2-recurring-payments-payment-product-paymentid-initiations-1` (Initiation for recurring payment) |
| Payments V2 | POST | `post_v2-multiple-recurring-payments-payment-product-paymentid-initiations-1` (Initiation for multiple recurring payments) |
| Payments V2 | GET | `get_v2-extended-payment-service-payment-product-paymentid-initiations-initiationid-status-1` (Get initiation status request) |
| Payments V2 | GET | `get_v2-extended-payment-service-payment-product-paymentid-initiations-initiationid-1` (Retrieve initiation request) |
| Payments V2 | DELETE | `delete_v2-extended-payment-service-payment-product-paymentid-initiations-initiationid-1` (Cancellation for initiations) |
| Payments V2 | GET | `get_v2-extended-payment-service-payment-product-paymentid-initiations-1` (Get initiations request) |
| Payments V2 | DELETE | `delete_v2-secured-payments-payment-product-paymentid` (Secured payment cancellation request) |
| Payments V2 | GET | `get_v2-recurring-payments-payment-product-paymentid` (Get recurring payment request) |
| Payments V2 | GET | `get_v2-multiple-recurring-payments-payment-product-paymentid` (Get multiple recurring payment request) |
## 4. Schemas worth knowing for a mock

### 4.1 AIS (accounts / card accounts)

* `GetPsd2AccountsResponse {accounts: Psd2AccountsResponse[]}`; `Psd2AccountsResponse`: resourceId, iban, bban, msisdn, currency, ownerName, name, displayName, product, cashAccountType (CACC|CARD in consent refs; examples use `CARD`), status ("enabled"/"deleted"/"blocked" per description), bic, linkedAccounts, usage (PRIV|ORGA), details, balances[], links. Examples: multi-currency card account appears as several entries with the SAME iban but different `resourceId` (uuid) and `currency`; the parent account row has `currency: "XXX"`.
* `Psd2BalanceResponse`: balanceType (free string typed `string`; description lists closingBooked, expected, openingBooked, interimAvailable, interimBooked, forwardAvailable, nonInvoiced; examples use interimBooked, interimAvailable, closingBooked - camelCase, unlike loans), balanceAmount `{currency, amount}` with `amount` a STRING with 2 decimals (`"30.00"`, negative `"-30.00"`), creditLimitIncluded (bool), lastCommittedTransaction, referenceDate (date string `2025-03-26`), lastChangeDateTime (`2025-03-26T00:00:00`, no zone).
* `GetBalanceResponse {account: AccountReferenceResponse, balances[]}`. `GetPsd2TransactionsResponse {account, balances[], transactions: {booked[], pending[], _links{account, first, next, previous}}, links{download}}`.
* `Psd2TransactionDetailsResponse`: transactionId, entryReference, endToEndId, mandateId, creditorId, creditorName, creditorAccount, creditorAgent (BIC), creditorIdentification, debtorName, debtorAccount, debtorAgent, debtorIdentification, ultimateDebtor(+Identification), transactionAmount, bookingDate, valueDate (both `YYYY-MM-DD` strings), remittanceInformationUnstructured, remittanceInformationStructured, remittanceInformationUnstructuredArary (sic), additionalInformation, purposeCode, bankTransactionCode, currencyExchange[], proprietaryBankTransactionCode (examples `GMI`, `GMB`), balanceAfterTransaction. Party ids look like `{"privateId":{"others":[{"identification":"PNOGE-00112233445"}]}}`. Debits are negative strings. Text fields in examples are Georgian.
* Card side: `GetCardAccountsResponse {cardAccounts[]}` (`resourceId` e.g. `123456xxxxxx1234`, maskedPan, creditLimit, balances, links), `CardTransactionDetailsResponse` (cardTransactionId, terminalId, transactionDate, acceptorTransactionDateTime, bookingDate, transactionAmount, originalAmount, markupFee, markupFeePercentage, cardAcceptorId, maskedPan, transactionDetails, invoiced, proprietaryBankTransactionCode); wrapper keys are `cardAccount`, `cardTransactions`.
* Consents v1: request `{access:{accounts[],balances[],transactions[],availableAccounts?,availableAccountsWithBalance?,additionalInformation?{ownerName[]}}, recurringIndicator, validUntil (date), frequencyPerDay, combinedServiceIndicator}` - all five top-level fields required. Response `{consentStatus, consentId (uuid), _links{scaOAuth|scaRedirect, self, status, scaStatus,...}, psuMessage}`. `GetConsentResponse` requires access, recurringIndicator, validUntil, frequencyPerDay, lastActionDate (date), consentStatus; `_links` {account, cardAccount}.

### 4.2 Loans and savings (TBC-specific, `openbanking-service-for-loans-and-savings`)

* Consent v2 (`POST /v2/consents/account-access`): required `access`, `recurringIndicator`, `validTo` (note: `validTo`, not v1's `validUntil`), `frequencyPerDay`. The published example body is `{access:{loans:[{account:{iban}, rights:["accountDetails","balances","transactions","ownerName"]}], savings:[{account:{iban}, rights:[...]}]}, consentType:"detailed", recurringIndicator:true, validTo:"2025-01-01", frequencyPerDay:4}`. The declared `AccountAccess` only has `accounts[]` and `rights[]` (`Rights` = free string), so `loans`, `savings`, `consentType` and the rights vocabulary exist only in the example (the schema has no `additionalProperties:false`, so it does not reject them). `GetConsentResponse` example shows `consentType:"V2Consent"`, `validTo`, `consentStatus`, but the schema requires `validUntil` and `lastActionDate` (see 4.3).
* `GET /v2/loans` -> `{accounts: LoanDetails[]}`; `GET /v2/loans/{resourceId}` -> `{account: LoanDetails}`; `GET /v2/loans{resourceId}/balances` (missing slash, preserved) -> `{balances: LoanBalance[]}`.
  * `LoanDetails`: `resourceId` (string), `currency` (string), `relatedDates {contractStartDate, contractEndDate}` (both `date-time`), `balances[]`, `status` (enum `Enabled | Deleted`), `interest {rate {percentage: number(double)}}`, `product` (string), `_links {balances, transactions}` (each `{href}`).
  * `LoanBalance`: `balanceAmount {currency, amount: number(double)}` (a JSON number, unlike every other API here), `balanceType` enum `ClosingBooked | OpeningBooked | ForwardAvailable | InterimBooked | Information` (PascalCase), `balanceSubTypeProprietary` (free string, nullable, values not enumerated), `referenceDate` (`date-time`).
  * There are NO instalment schedule, next-payment, overdue/arrears, principal vs interest split, or penalty fields in the published schema. I searched case-insensitively for `instal|overdue|arrear|penalt|schedul|principal|delinq|nextPayment|maturity` in the six loans/savings operation fragments, the assembled loans/savings file and the loans/savings HTML pages: the only hit in the specs is the word "scheduled" in the `contractEndDate` description; page hits are just the portal sidebar's unrelated Online Installments product navigation. The only hint of such data is `balanceSubTypeProprietary` (undocumented values) and the `_links.transactions` href (no transactions operation for loans is published). No loan response example is published at all.
  * Query `withBalance` exists on `/v2/loans/{resourceId}` but not on `/v2/loans`.
* `GET /v2/savings` -> `{accounts: SavingAccountResponse[]}`; `GET /v2/savings/{resourceId}` -> `SavingAccountResponse`; `GET /v2/savings/{resourceId}/balances` -> `{resourceId, balances: SavingBalanceResponse[]}`.
  * `SavingAccountResponse`: `resourceId`, `product` (example `"Term"`), `currency`, `status` (free string, example `"Active"`), `period` (int32, months: 36, 24), `contractStartDate` (date string `2024-01-03`), `ownerName`, `cashAccountType` (ISO 20022, example `"SVGS"`), `balances[]`, `links {balances {href}}`.
  * `SavingBalanceResponse`: `balanceAmount {currency, amount: string "442.64"}`, `balanceType` (string; example `OpeningBooked`), `referenceDate` (string `2024-11-01`).
  * Published savings example has two term deposits (GEL 442.64, USD 1121.08). `withBalance` is available on list and by-id calls.
  * No interest-rate/maturity-date/deposit-amount fields exist for savings in the schema.
* Auth/headers for these reads: Bearer + `X-Request-ID`, `consentId` header, `PSU-IP-Address`.

### 4.3 Spec defects found by validating the published examples against their own schemas

466 response/request examples were checked (jsonschema Draft 7 after converting `nullable`). Structural check (no format checking): 450 pass, 16 fail (`example_validation.json`). With format checking added (date, date-time per RFC 3339 incl. zone, uuid, uri; stock jsonschema only ships date/ipv4/ipv6/email/regex, so I added the others myself): 448 pass, 18 fail (`example_validation_strict.json`); the two extra failures are the AIS and card-account balances examples whose `lastChangeDateTime` (`2025-03-26T00:00:00`) has no time zone. A strict schema-validated mock built from the schemas as published will reject TBC's own examples. Main causes:

1. AIS response schemas declare `links` but every example uses `_links` (accounts, account details, transactions, card accounts, card transactions) and the schemas are `additionalProperties: false`. `_links` is the key to emit. The AIS account examples also include `ownerIdentification {privateId {others[{identification}]}}` which is not declared. Card account details example nests fields differently from `GetCardAccountResponse {cardAccount}`.
2. Loans/savings: savings `links` example has `balances` while `SavingAccountResponse.links` is `HrefTypeResponse {href}`; `GetConsentResponse` example lacks required `validUntil` and `lastActionDate` (and uses `validTo`).
3. OAuth: `expires_in` is an integer in examples but a string in `OauthTokenResponse`; the discovery schema puts `enum` on array-valued properties so the example array "is not one of" the enum.
4. Typing noise: `PSU-IP-Address` has `format: "ipv4 or ipv6"` (not a real format) with example `123.345.567.78`; `ValidUntil`/`ValidTo` are `format: date` with a date-time example `2022-12-31T00:00:00.000Z`; `lastChangeDateTime` is declared `date-time` but examples have no zone; `ResourceId` path param is `format: uuid` but the savings example ids (`456f48dh-f22f-...`) are not valid UUIDs; the AIS dedicated-accounts consent example uses capitalised keys (`Accounts`, `Iban`, `Balances`) while the schema uses lowercase; `dateFrom`/`dateTo` are `date-time` in the schema, but sample hrefs use `dateFrom=2023-01-01` and `withbalance=false` (lowercase).
5. Almost no `required` arrays on response schemas (AIS, loans, savings, payments): every field is optional/nullable, so schema validation alone gives weak guarantees; the examples are the real contract.
6. Payments: v1 `DELETE` documents no 2xx response; v1 `POST .../authorisations` documents only 201; v2 extended-status op returns `GetConsentStatusResponse {transactionStatus, remainingAuthorised}`; `PaymentProductEnum = aspsp | domestic | foreign`; `PaymentServiceEnum = payments | bulk-payments | periodic-payments | secured-payments`; v2 `PaymentStatusEnum` = ACCC, ACCP, ACSC, ACSP, ACTC, ACWC, ACWP, RCVD, PDNG, RJCT, CANC, ACFC, PATC, PART; `ExtendedPaymentService` = deferred-payments, secured-deferred-payments, multiple-deferred-payments, secured-multiple-deferred-payments, recurring-payments, multiple-recurring-payments (`DeferredPaymentService` = first four); `ChargeBearer` = DEBT, CRED, SHAR, SLEV; `InstructedAmount {currency, amount: string pattern -?[0-9]{1,14}(\.[0-9]{1,3})?}`; v2 recurring `frequency` = Daily, Weekly, EveryTwoWeeks, Monthly, EveryTwoMonths, Quarterly, SemiAnnual, Annual (multiple-recurring: Daily, Weekly, Monthly).
7. The literal path `/v2/loans{resourceId}/balances` is confirmed in the spec's `paths` key (the savings equivalent is correctly `/v2/savings/{resourceId}/balances`).

### 4.4 Payments (lowest priority; partial)

Initiation bodies: v1 `PaymentInitiationRequest` requires `creditorAccount` + `instructedAmount` (plus optional creditorName/Address/Agent, debtorAccount/Name/Identification, remittanceInformationUnstructured max 140, chargeBearer, ... and many untyped optional placeholders); `BulkPaymentInitiationRequest {batchBookingPreferred, debtorAccount, paymentInformationId, payments[]}`. v2: `ConsentInitiationRequest` (secured payments), `RecurringConsentInitiationRequest` (+ startDate, endDate, dayOfExecution 1-31, frequency), `MultipleRecurringConsentInitiationRequest`, `DeferredConsentInitiationRequest` (+ requestedExpiryDateTime), `DeferredPaymentInitiationRequest {paymentIdentification, instructedAmount, remittanceInformation*, closingFlag}`. Responses: `PaymentInitiationResponse {paymentId, transactionStatus, transactionFees, ..., scaMethods[], challengeData, _links, psuMessage}`; `ExtendedPaymentInitiationResponse` adds `expiryDateTime`. Payment SCA uses `PIS:<paymentId>` OAuth scope. Details are in the assembled files.

## 5. TBC-specific notes beyond Berlin Group (summary)

* Loans and savings are exposed through a separate "v2" consent type (`/v2/consents/account-access`, body with `loans[]`/`savings[]`, `consentType`, `validTo`) and read-only resources keyed by `resourceId`; Berlin Group has no loan/deposit resources.
* Loan balances use PascalCase balance types, JSON-number amounts and date-time reference dates; savings use string amounts and date-only strings. Statuses: loans `Enabled|Deleted`; savings free text (`Active`). `cashAccountType: "SVGS"` for deposits, `CARD` for card-based current accounts (AIS examples use `CARD` on `/v1/accounts`).
* TBC extensions on accounts: `displayName` (online channel name), `ownerIdentification.privateId.others[].identification` (format `PNOGE-<personal no.>`), multi-currency accounts as repeated IBANs with distinct `resourceId`, `currency: XXX` on the multi-currency parent.
* Georgian-language texts in responses (`Accept-Language: ka` default in examples), `ui_locales` en/ka, personal numbers prefixed `PNOGE-`, corporate `NTRGE-`.
* JWS profile (Georgian Banking Association) instead of Berlin Group HTTP Signature; signatures on both requests and responses.
* Bank transaction codes are proprietary three-letter codes (`GMI`, `GMB`); booking date/value date are date-only.

## 6. KPI numbers (https://developers.tbcbank.ge/docs/open-banking-statistics)

Source: page `docs_open-banking-statistics.html` (reused from the requester's earlier probe; portal `updated_at` 2026-10-06T09:07:16Z). The page has no definitions or units: columns are Uptime (KPI1) per service (AIS, PIS, Identification-Verification), Downtime (KPI2) per service, PISP response time (KPI3), AISP response time (KPI4), PIISP response time (KPI5), Error rate (KPI6). Response times are presumably milliseconds and the error rate unit is not stated (values 0.00006-0.61; a step change from ~0.01 to 0.14 happens in Dec 2025, possibly a unit/definition change). PIS uptime starts Jan 2025, IV (identification-verification) and PIIS metrics start May 2025, PISP response time starts Oct 2024. `N/A` = not reported.

Rows as published (42 months, April 2023 - "October 2026"):

| Period | AIS uptime | PIS uptime | IV uptime | AIS downtime | PIS downtime | IV downtime | PISP RT (KPI3) | AISP RT (KPI4) | PIIS RT (KPI5) | Error rate (KPI6) |
|---|---|---|---|---|---|---|---|---|---|---|
| April 2023 | 99.7% | N/A | N/A | 0.3% | N/A | N/A | N/A | 534.89 | N/A | 0.001 |
| May 2023 | 99.9% | N/A | N/A | 0.002% | N/A | N/A | N/A | 541.02 | N/A | 0.0009 |
| June 2023 | 98.8% | N/A | N/A | 1.185% | N/A | N/A | N/A | 642.11 | N/A | 0.001 |
| July 2023 | 99.9% | N/A | N/A | 0.03% | N/A | N/A | N/A | 640.24 | N/A | 0.001 |
| August 2023 | 99.9% | N/A | N/A | 0.08% | N/A | N/A | N/A | 628.48 | N/A | 0.001 |
| September 2023 | 100% | N/A | N/A | 0% | N/A | N/A | N/A | 579.47 | N/A | 0.001 |
| October 2023 | 99.88% | N/A | N/A | 0.12% | N/A | N/A | N/A | 552.08 | N/A | 0.002 |
| November 2023 | 99.93% | N/A | N/A | 0.07% | N/A | N/A | N/A | 578.4 | N/A | 0.002 |
| December 2023 | 99.93% | N/A | N/A | 0.07% | N/A | N/A | N/A | 535.7 | N/A | 0.004 |
| January 2024 | 93.33% | N/A | N/A | 6.67% | N/A | N/A | N/A | 814.85 | N/A | 0.0177 |
| February 2024 | 99.8875% | N/A | N/A | 0.1125% | N/A | N/A | N/A | 392.24 | N/A | 0.002 |
| March 2024 | 99.85% | N/A | N/A | 0.15% | N/A | N/A | N/A | 354.42 | N/A | 0.000064 |
| April 2024 | 99.89% | N/A | N/A | 0.11% | N/A | N/A | N/A | 432.0 | N/A | 0.004 |
| May 2024 | 99.87% | N/A | N/A | 0.13% | N/A | N/A | N/A | 504.51 | N/A | 0.003 |
| June 2024 | 99.7% | N/A | N/A | 0.3% | N/A | N/A | N/A | 656.93 | N/A | 0.0046 |
| July 2024 | 97.83% | N/A | N/A | 2.17% | N/A | N/A | N/A | 714.38 | N/A | 0.00548 |
| August 2024 | 98.32% | N/A | N/A | 1.68% | N/A | N/A | N/A | 716.13 | N/A | 0.014 |
| September 2024 | 97.41% | N/A | N/A | 2.59% | N/A | N/A | N/A | 943.86 | N/A | 0.0093 |
| October 2024 | 95.09% | N/A | N/A | 4.91% | N/A | N/A | 676.4 | 744.95 | N/A | 0.0042 |
| November 2024 | 100.00% | N/A | N/A | 0.00% | N/A | N/A | 515.7 | 702.84 | N/A | 0.0010 |
| December 2024 | 99.33% | N/A | N/A | 0.67% | N/A | N/A | 574.47 | 841.799 | N/A | 0.0032 |
| January 2025 | 99.98% | 99.58% | N/A | 0.02% | 0.042% | N/A | 407.88 | 650.74 | N/A | 0.004 |
| February 2025 | 99.99% | 99.99% | N/A | 0.01% | 0.01% | N/A | 420.01 | 636.18 | N/A | 0.0046 |
| March 2025 | 99.99% | 99.99% | N/A | 0.01% | 0.01% | N/A | 400.03 | 646.81 | N/A | 0.0054 |
| April 2025 | 99.98% | 99.98% | N/A | 0.02% | 0.02% | N/A | 274.02 | 618.35 | N/A | 0.012 |
| May 2025 | 99.97% | 95.74% | 100% | 0.03% | 4.26% | 0% | 235.01 | 621.09 | 662 | 0.042 |
| June 2025 | 100% | 99.961% | 100% | 0% | 0.039% | 0% | 475.53 | 620.32 | 662 | 0.0086 |
| July 2025 | 99.99% | 99.99% | 100% | 0.01% | 0.01% | 0% | 422.17 | 631.67 | 561.56 | 0.002 |
| August 2025 | 100% | 99.98% | 100% | 0% | 0.02% | 0% | 615.96 | 625.56 | 551.65 | 0.0019 |
| September 2025 | 100% | 99.97% | 100% | 0% | 0.29% | 0% | 1444.07 | 548.06 | 632.21 | 0.018 |
| October 2025 | 99.997% | 99.973% | 99.998% | 0.002% | 0.027% | 0.003% | 1063.88 | 682.51 | 592.54 | 0.011 |
| November 2025 | 99.53% | 99.94% | 99.89% | 0.47% | 0.06% | 0.11% | 388.23 | 637.63 | 514.76 | 0.011 |
| December 2025 | 99.38% | 99.90% | 99.85% | 0.62% | 0.10% | 0.15% | 398.81 | 700.85 | 525.36 | 0.14 |
| January 2026 | 99.05% | 99.68% | 99.68% | 0.95% | 0.32% | 0.32% | 433.62 | 766.02 | 544.96 | 0.13 |
| February 2026 | 98.17% | 99.37% | 99.68% | 1.83% | 0.63% | 0.32% | 482.73 | 910.33 | 527.89 | 0.24 |
| March 2026 | 98.23% | 99.83% | 99.77% | 1.77% | 0.17% | 0.23% | 425.72 | 883.55 | 504.24 | 0.23 |
| April 2026 | 97.85% | 99.85% | 99.66% | 2.15% | 0.15% | 0.34% | 481.53 | 969.27 | 510.2 | 0.31 |
| May 2026 | 99.4% | 99.92% | 99.09% | 0.6% | 0.08% | 0.91% | 394.79 | 608.87 | 511.25 | 0.61 |
| June 2026 | 99.56% | 99.81% | 99.56% | 0.44% | 0.19% | 0.44% | 375.92 | 639.63 | 508.75 | 0.18 |
| July 2026 | 99.46% | 99.84% | 99.59% | 0.54% | 0.16% | 0.41% | 401.78 | 597.26 | 546.2 | 0.21 |
| August 2026 | 99.08% | 99.85% | 99.22% | 0.92% | 0.15% | 0.78% | 413.4 | 650.12 | 535.12 | 0.4 |
| October 2026 | 99.13% | 99.91% | 99.73% | 0.87% | 0.09% | 0.27% | 423.09% | 675.2% | 536.36% | 0.10 |
Observations / anomalies:

* AIS uptime ranges 93.33% (Jan 2024) to 100%; worst recent months: Jan 2024 93.33%, Oct 2024 95.09%, Jul-Sep 2024 ~97.4-98.3%, Feb-Apr 2026 97.85-98.23%. Latest rows (Aug 2026 / "Oct 2026"): AIS 99.08% / 99.13%, PIS 99.85% / 99.91%, IV 99.22% / 99.73%.
* AISP response time in 2026: 597-969 ms (mean of the 8 published months ~750); PISP 376-483 ms; PIISP 504-546 ms. Peak PISP outliers: Sep 2025 1444 ms, Oct 2025 1064 ms.
* Data-quality issues in the table: the September 2026 row is missing (August 2026 is followed by "October 2026"; the page was last updated on 6 Oct 2026, so that row is likely partial or mislabeled); the "October 2026" row has `%` signs on all three response times (423.09%, 675.2%, 536.36%); Sep 2025 PIS uptime 99.97% vs downtime 0.29% does not sum to 100; in 2023 downtime is not the complement of uptime (May 99.9% vs 0.002%, Jun 98.8% vs 1.185%, Jul 99.9% vs 0.03%, Aug 99.9% vs 0.08%); "N/A" vs blank vs 0 usage varies; month labels contain stray HTML line breaks.

## 7. Could not obtain / unverified

Could not obtain:

1. An official full OpenAPI JSON/YAML for any of the 7 named spec files (download disabled; see 1.1). Obtained instead: per-operation fragments assembled into 4 files. Nothing for `openbanking-services-consents.json` (9vq73bzlm4i59kq), `open-banking-cards-service.json` (1eso34ljgvb99j) or `openbanking-services-payments-extended.json` (e4f6o1cm7n6o3va): every operation page I fetched resolves to one of the other four parent files (AIS consents live in the AIS file), so these three look legacy/hidden or only used by the unfetched payment ops.
2. 23 payments operations (list in 3.1): v1 bulk get/status, authorisation GET/status, cancellation-authorisation trio, 3 payments OAuth ops; v2 deferred/secured-deferred/multiple-deferred GETs, extended cancel, initiation variants for recurring/multiple recurring, initiation status/retrieve/cancel/list, secured cancel, recurring GETs.
3. Any published example for loans (`/v2/loans*`): none exist in the spec.
4. Sandbox behaviour: test PSU credentials, test IBANs/accounts, sandbox data sets, error-injection rules, how a TPP registers and gets `client_id`/test certificate (docs pages `create-developer-account`, `register-app`, `get-apikey-and-secret` were not read; the certificate page says to e-mail the TBC team).
5. Numeric rate limits for PSD2 APIs (not published); token lifetimes beyond the examples (`expires_in` 3599 s, refresh ~7.78M s = ~90 days).
6. Docs prose/tutorials for payments v2 (overview pages for PIS v2 groups), TBC ID (CIBA/userinfo) pages, error code catalogue page `error-code-description`, and the Postman collection list (which contains no Open Banking collection; only Exchange Rates, E-Commerce, Online Installment, Mortgage).
7. `.md` variants of pages (not needed once the SSR state was found; the earlier saved `.md` files were interstitials).

Unverified / needs a decision before relying on it:

* Whether v2 endpoints (loans, savings, consents v2, payments v2) are served under `/0.8` (servers say no; AIS sample hrefs include `/0.8`); whether `https://test-api.tbcbank.ge/xs2a-sandbox` is a real alternative sandbox for loans/savings.
* Whether JWS (`Digest`, `X-JWS-Signature`) is actually enforced on loans/savings and payments data calls (docs say yes; parameter lists omit them).
* Meaning of `balanceSubTypeProprietary`, which `rights` strings are valid for v2 consents, the enum of savings `status`, and whether other `cashAccountType`/`proprietaryBankTransactionCode` values exist.
* Which of `links` / `_links` (and `validTo` / `validUntil` in v2 consent responses) the live API returns. The examples (`_links`, `validTo`) are probably closer to real traffic than the schemas, but that is inference.
* Units/definitions in the KPI table; whether "NextGenPSD2 1.3.6" is the real baseline.
* All 16 failing examples in `example_validation.json` should be reviewed by hand before deciding which side (schema or example) the mock follows.
* The assembled files were not checked with a full OpenAPI 3.0 validator, and the 23 unfetched payments operations are absent from them.
