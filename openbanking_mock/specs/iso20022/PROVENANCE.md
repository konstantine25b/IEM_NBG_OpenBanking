# ISO 20022 external code lists: provenance

Vendored for the synthetic Georgian bank-account data generator. Used to validate the API fields
`bankTransactionCode` (Domain-Family-SubFamily, e.g. `PMNT-CCRD-POSD`) and `purposeCode` (ExternalPurpose1Code).
Nothing here is hand-edited: every CSV is generated from the official ISO 20022 Registration Authority files below.

Retrieval date (UTC): 2026-10-21 (files fetched 12:47Z to 12:50Z with curl, sequentially, 1-2 s apart).

## Source files (raw downloads, not vendored; the CSVs are derived from them)

| What | URL | File name | SHA-256 (raw download) | Server Last-Modified |
|---|---|---|---|---|
| External Code Sets, XLSX bundle | https://www.iso20022.org/sites/default/files/media/file/ExternalCodeSets_XLSX.zip | ExternalCodeSets_XLSX.zip | `bca78ecb6be196d1ed53bf492649c62c8acb996edca0812b5c6269669a4c4193` | Thu, 03 Sep 2026 09:22:49 GMT |
| inner file of the bundle | (member of the zip above) | 2Q2026_externalcodesets_v3.xlsx | `7986d6df74c070a03d10f2075737faa8ebefae4ebe02deccd5632463a69b968a` | zip member dated 2026-09-02 17:33 |
| External Code Sets, JSON bundle (used only to cross-check code counts) | https://www.iso20022.org/sites/default/files/media/file/ExternalCodeSets_JSON.zip | ExternalCodeSets_JSON.zip | `8af0919378e91f719a8b949b35e23742f93d4ad80039f85b116ff630c35c0406` | Thu, 03 Sep 2026 09:22:13 GMT |
| inner file of the JSON bundle | (member of the zip above) | 2Q2026_externalcodesets_v3.json | `c0c045e7f73434a30dcc064c41f5b34249f0e3e90f94d9a6931235b4aa915560` | zip member dated 2026-09-02 17:26 |
| Bank Transaction Code combinations ("all permitted combinations of the BTC code set") | https://www.iso20022.org/sites/default/files/media/file/BTC_Codification_30Nov2025.xlsx | BTC_Codification_30Nov2025.xlsx | `f73d6d72a5becb9f042f98b939f7c8301d434610e48afc60f1b1ef01cbb7ac6e` | Fri, 28 Nov 2025 12:06:59 GMT |
| Bank Transaction Code description (definitions, used for semantic checks only) | https://www.iso20022.org/sites/default/files/media/file/BTC_ExternalCodeListDescription_Nov2025.docx | BTC_ExternalCodeListDescription_Nov2025.docx | `fd760fd7ca91363b2fcf300efc501624597bec297a252e81a24106ae026b21d3` | Fri, 28 Nov 2025 12:39:59 GMT |

Landing page: https://www.iso20022.org/catalogue-messages/additional-content-messages/external-code-sets

## Versions

* External code sets: **2Q2026 v3**. The workbook's own `Intro&History` sheet says "Publication date: 28 Aug 2026 - 2Q2026".
  ISO publishes external code sets quarterly (end of Feb, May, Aug, Nov). Every code in the sets used here has status Registered.
* Bank Transaction Codes: combinations file dated **30 November 2025 (v1)**; its History Log tops out at **v8.1 (2025-05-31, CR1471, addition of 2 codes)**.
  The accompanying description document is "November 2025, Version 8.0 - Approved Version" (its revision record lists 8.0 as the last entry).
  BTC changes rarely; the November 2025 file was the one linked from the landing page in the April 2026 web-archive snapshot and
  in a search-engine view of the live page (which also showed code sets "August 2026 (v3)").

## Caveats about how the files were found

* The HTML pages on iso20022.org answered HTTP 403 ("SWIFT site off-line") to curl and to the WebFetch tool, so the landing page was not read directly.
  The download links were taken from a Wayback Machine capture of the landing page (https://web.archive.org/web/20260419211552/https://www.iso20022.org/catalogue-messages/additional-content-messages/external-code-sets;
  that capture showed code sets "February 2026 (v1)" and "Bank Transaction Code combinations and description Dated 30 November 2025 (v1)").
  The file URLs themselves (all under /sites/default/files/media/file/) are served by the live site and were downloaded from there, so the
  hashes above are of the live files as of the retrieval date. The "ExternalCodeSets_*.zip" URLs are un-versioned and are overwritten on each ISO release;
  re-download and compare hashes before refreshing the CSVs.
* We could not confirm from the live page that no newer BTC combinations file than 30 Nov 2025 exists. The next scheduled ISO release window is end of November 2026.

## Derived files in this directory

| File | Rows (excl. header) | Built from | SHA-256 |
|---|---|---|---|
| bank_transaction_codes.csv | 1564 | BTC_Codification_30Nov2025.xlsx, sheet `BTC_Codification` | `556c3222f7da9449a38823b0a9a87934325d6c699fd03e78597f76024e31fa98` |
| external_purpose_codes.csv | 366 | 2Q2026_externalcodesets_v3.xlsx, sheet `AllCodeSets`, Code Set = ExternalPurpose1Code | `c1f672153231ea6aa853955a569a91f4db71c821d1dcc2c1a67f5a4e7b740f55` |
| external_cash_account_type.csv | 28 | same workbook, Code Set = ExternalCashAccountType1Code | `645ead41455c2eec421f8b80866c80b7cf704d801cb53ad6889b15e1fee6cab2` |
| external_balance_type.csv | 10 | same workbook, Code Set = ExternalBalanceType1Code | `4383e59063dfd09e5de0aa71952c970703970deb235d5a402491e6391aa74da4` |

### Transformations (the only ones applied)

* UTF-8, LF line endings, comma separated, standard CSV quoting. Rows sorted by code (BTC: by domain, family, subfamily).
* Whitespace in names and codes is normalised: leading/trailing blanks removed, runs of whitespace (including non-breaking spaces) collapsed to one space.
  In the published sheet 40 data rows have stray whitespace inside a code cell (38 of them a non-breaking space U+00A0, for example `POSD` followed by U+00A0, on PMNT-CCRD-POSD, PMNT-CCRD-POSC, PMNT-IDDT-PMDD and others); the codes in the CSV are the 4-character values without them.
* Otherwise names are exactly as published, including the source typos: `Miscelleanous Debit Operations` (family PMNT-MDOP row IADD only) and `Branch Withdrawl` (PMNT-CNTR-BCWD).
  Consequence: `family_name` is not perfectly constant per family code (PMNT/MDOP appears under two spellings; ACMT/ACOP had a double space which the normalisation removed). Key on the codes, not the names.
* The BTC sheet has 1,567 data rows; three triples appear twice (SECU-CASH-CPEN with identical names; SECU-CORP-INTR and SECU-CUST-INTR with two different sub-family names,
  "Interest Payment" and "Interests (Generic)"). The first occurrence is kept, giving 1,564 unique triples.
* Codes are 4 characters of A-Z/0-9 (SECU-CASH-GEN1 and SECU-CASH-GEN2 contain a digit).
* Multi-line definitions in the purpose file are flattened to a single line.

### Cross-checks done

* BTC: the 11 domain codes and all 19+6+12+11+9+8+8+9+14+7+1 family codes (per domain) in the CSV equal the tables in the description document (chapter 4). Every sub-family code listed in
  that document is present in the CSV. The CSV additionally has 32 sub-family codes (used only in the Securities, Commodities and Precious Metal domains, for example BFWD, CCIR, SLEB, TBAS) that the Nov 2025 description document does not list
  (reason not established). The spreadsheet is the authoritative "permitted combinations" list; none of those 32 codes appears in a Payments, Account Management or Loans/Deposits/Syndications triple that this project maps.
* Purpose, cash-account-type and balance-type code counts equal the enum sizes in the JSON bundle of the same release (366, 28, 10) and the code sets are identical.

## Cross-check: `purposeCode` in specs/bog/plugin-redoc-0.yaml vs ExternalPurpose1Code 2Q2026 v3

The BoG schema `components/schemas/purposeCode` says "Values from ISO 20022 External Code List ExternalCodeSets_1Q2018 June 2018" and lists 286 values (286 unique).

* In the BoG enum but not in the official list: **0** (the BoG enum is a strict subset).
* In the official list but not in the BoG enum: **80** (official list has 366). All 80 were created after 2018-06-30, i.e. added to ISO after the BoG snapshot:
  ACTF (AccountFunding), ALDS (AutoLoanDisbursement), AMEX (Amex), AUCO (AuthenticatedCollections), BLDS (BusinessLoanDisbursement), BNPL (BuyNowPayLater), BOND (Bonds), BROK (BrokerageFunding), CABD (CorporateActions-Bonds), CAEQ (CorporateActions-Equities), CARL (CarLeaseRental), CBCR (CreditCard), CHCP (CharitableDonationCorporate), CHDF (CharitableDonationDAF), CHFN (CharitableDonationFoundation), CLDS (ConstructionLoanDisbursement), CPEN (CashPenalties), CRYP (CryptoAssets), DBCR (DebitCard), DEBT (ChargesBorneByDebtor), DEPD (DependentSupportPayment), DICL (Diners), EFTC (LowValueCredit), EFTD (LowValueDebit), EQTS (Equities), ERPM (EmergencyReliefPayment), ESCR (EscrowPayment), FGDL (FoodGroceryDelivery), FLCR (FleetCard), FUEL (Fuel), GAFA (GovernmentFamilyAllowance), GAHO (GovernmentHousingAllowance), GAMB (GamblingOrWageringPayment), GIFT (Gift), GOVG (GovernmentGrants), GYMM (GymMembership), HMMS (HomeMaintenanceServices), HRPS (HomeRepairServices), INBC (InterbankCharges), INTP (IntraPartyPayment), IPAY (InstantPayments), IPCA (InstantPaymentsCancellation), IPDO (InstantPaymentsForDonations), IPEA (InstantPaymentsInECommerceWithoutAddressData), IPEC (InstantPaymentsInECommerceWithAddressData), IPEW (InstantPaymentsInECommerce), IPPS (InstantPaymentsAtPOS), IPRT (InstantPaymentsReturn), IPU2 (InstantPaymentsUnattendedVendingMachineWith2FA), IPUW (InstantPaymentsUnattendedVendingMachineWithout2FA), LBXX (LongBoxTransfer), LOTT (LotteryPayment), MDEP (MicroDeposit), MFSR (MutualFundSubscriptionRedemption), MOMA (MoneyMarket), ODPY (OnDemandPayment), PCOM (PropertyCompletionPayment), PDEP (PropertyDeposit), PDLN (PayDayLoan), PLDS (PropertyLoanDisbursement), PLRF (PropertyLoanRefinancing), PSDS (PersonalLoanDisbursement), RAPI (RapidPaymentInstruction), RETL (RetailPayment), RHSV (RideHailingServices), RMCO (RegisteredMandate), RRCT (ReimbursementReceivedCreditTransfer), RRTP (RelatedRequestToPay), SASW (ATM), SCAC (SchoolActivities), SLDS (StudentLoanDisbursement), SPSP (SalaryPensionSumPayment), STRM (StreamingServices), TIPS (TipsGratuity), TNTP (TaxNeutralPensionTransfer), TOLL (Tolls), TRVL (TravelAndHospitality), WALM (WalletFundingMultiUse), WALS (WalletFundingSingle), WALT (WalletTransactions).

## Note on `balanceType` (Berlin Group) vs ExternalBalanceType1Code

`balanceType` in specs/bog/plugin-redoc-0.yaml and specs/obhome/psd2-api_1.3.9_2021-05-04v1.yaml (Berlin Group) has 7 values. Six map to an ISO code by name
(first letter upper-cased): closingBooked = CLBD, expected = XPCD, openingBooked = OPBD, interimAvailable = ITAV, interimBooked = ITBD, forwardAvailable = FWAV.
`nonInvoiced` has no ISO 20022 code. The ISO codes CLAV (ClosingAvailable), INFO (Information), OPAV (OpeningAvailable) and PRCD (PreviouslyClosedBooked) have no Berlin Group value.
