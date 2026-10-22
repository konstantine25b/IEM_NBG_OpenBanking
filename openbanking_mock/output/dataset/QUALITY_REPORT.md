# Quality report: dataset

100,000 customers, 400 shards.

## 1. API tables: fidelity and standards (must be 100%)

| check | passed | total | result |
|---|---:|---:|---|
| BIC matches ISO 9362 | 155,462 | 155,462 | PASS |
| IBAN is GE + 2 check + 2 letters + 16 digits | 156,475 | 156,475 | PASS |
| IBAN passes ISO 13616 mod-97 | 156,475 | 156,475 | PASS |
| IBAN unique per (bank, iban, currency, endpoint) across the whole dataset | 156,475 | 156,475 | PASS |
| TBC/BoG transactionId = c_/d_ + entryReference | 42,261,978 | 42,261,978 | PASS |
| accounts: currency is ISO 4217 | 284,502 | 284,502 | PASS |
| balance type is a Berlin Group / bank-published type | 1,148,214 | 1,148,214 | PASS |
| balanceAfterTransaction moves by exactly the booked amount | 53,025,530 | 53,025,530 | PASS |
| balances: currency is ISO 4217 | 1,148,214 | 1,148,214 | PASS |
| bankTransactionCode is an official ISO 20022 triple | 53,291,275 | 53,291,275 | PASS |
| booked entry has a bookingDate | 76,924,090 | 76,924,090 | PASS |
| card purchases are booked on business days | 40,500,680 | 40,500,680 | PASS |
| credit/debit prefix matches the sign | 42,261,978 | 42,261,978 | PASS |
| currencyExchange.unitCurrency is the foreign currency | 1,601,640 | 1,601,640 | PASS |
| embedded interimBooked equals the balance after the last booked entry | 135,637 | 135,637 | PASS |
| exchange rate is a plausible GEL price (1.5-6) | 1,601,640 | 1,601,640 | PASS |
| interbank credits and salaries are booked on business days | 2,034,830 | 2,034,830 | PASS |
| mandateId only on direct debits | 822,566 | 822,566 | PASS |
| no duplicate entry within a (bank, endpoint) | 77,120,704 | 77,120,704 | PASS |
| pending entry has no bookingDate | 196,614 | 196,614 | PASS |
| purposeCode is an official ISO 20022 code | 8,491,172 | 8,491,172 | PASS |
| purposeCode only on credit transfers / direct debits | 8,491,172 | 8,491,172 | PASS |
| transactions: currency is ISO 4217 | 77,120,704 | 77,120,704 | PASS |

## 2. Simulated world vs sourced Georgian targets (truth layer)

| target | measured | target | tolerance | status of the source | result |
|---|---:|---:|---|---|---|
| TBC Bank: share of customers by primary bank | 32.00 % | 32 | ±0.1 % | DERIVED | PASS |
| Bank of Georgia: share of customers by primary bank | 46.00 % | 46 | ±0.1 % | DERIVED | PASS |
| TeraBank: share of customers by primary bank | 2.00 % | 2 | ±0.1 % | DERIVED | PASS |
| Liberty Bank: share of customers by primary bank | 8.00 % | 8 | ±0.1 % | DERIVED | PASS |
| Silk Bank: share of customers by primary bank | 0.50 % | 0.5 | ±0.1 % | DERIVED | PASS |
| Cartu Bank: share of customers by primary bank | 1.00 % | 1 | ±0.1 % | DERIVED | PASS |
| PASHA Bank Georgia: share of customers by primary bank | 0.90 % | 0.9 | ±0.1 % | DERIVED | PASS |
| Credo Bank: share of customers by primary bank | 4.00 % | 4 | ±0.1 % | DERIVED | PASS |
| Halyk Bank Georgia: share of customers by primary bank | 1.00 % | 1 | ±0.1 % | DERIVED | PASS |
| Basisbank: share of customers by primary bank | 2.50 % | 2.5 | ±0.1 % | DERIVED | PASS |
| ProCredit Bank Georgia: share of customers by primary bank | 1.50 % | 1.5 | ±0.1 % | DERIVED | PASS |
| Paysera Bank Georgia: share of customers by primary bank | 0.60 % | 0.6 | ±0.1 % | DERIVED | PASS |
| BoG + TBC: share of customers by primary bank | 78.00 % | 78 | ±0.2 % | DERIVED | PASS |
| BoG + TBC: share of API transaction rows (card mirrors removed) | 77.10 % | 78 | ±5 % | DERIVED | PASS |
| Visa share of debit cards (rest Mastercard) | 33.94 % | 34 | ±4 % | UNVERIFIED | PASS |
| Visa share of Visa+Mastercard credit cards | 74.41 % | 75 | ±6 % | VERIFIED | PASS |
| customers holding a credit card | 22.66 % | 23 | ±5 % | DERIVED | PASS |
| ATM withdrawals as share of card transactions (count) | 6.45 % | 7 | ±2 % | UNVERIFIED | PASS |
| foreign-currency share of term deposits (by count) | 50.01 % | 50 | ±10 % | DERIVED | PASS |
| USD share of foreign-currency deposits (rest EUR) | 78.90 % | 79 | ±8 % | VERIFIED | PASS |
| average GEL term-deposit rate | 9.08 % p.a. | 8.9 | ±0.9 % p.a. | VERIFIED | PASS |
| average foreign-currency term-deposit rate | 2.73 % p.a. | 2.5 | ±0.7 % p.a. | VERIFIED | PASS |
| TBC/BoG primary customers paying the GEL 1/month SMS fee | 60.08 % | 60 | ±6 % | ASSUMED | PASS |
| food: share of modelled spend | 47.13 % | 46.5 | ±3 % | VERIFIED | PASS |
| transport: share of modelled spend | 13.37 % | 13.7 | ±3 % | VERIFIED | PASS |
| housing utilities: share of modelled spend | 13.51 % | 13.6 | ±3 % | VERIFIED | PASS |
| health: share of modelled spend | 12.99 % | 13.2 | ±3 % | VERIFIED | PASS |
| clothing: share of modelled spend | 7.05 % | 7 | ±3 % | VERIFIED | PASS |
| durables: share of modelled spend | 5.96 % | 6.1 | ±3 % | VERIFIED | PASS |
| mean monthly income in the supplied loan dataset vs Geostat mean wage (Q2-2026 GEL 2,389) | 3339.79 GEL | 2389 | info | VERIFIED | info |
| mean GEL consumer-loan rate vs NBG Mar-2026 (16.7%) | 18.26 % p.a. | 16.7 | info | VERIFIED | info |
| mean GEL mortgage rate vs NBG Mar-2026 (12.0%) | 12.40 % p.a. | 12 | info | VERIFIED | info |
| loans whose instalment exceeds the NBG PTI limit | 12.02 % | 0 | info | VERIFIED | info |
| customers with an overdue loan (df-driven; real retail NPL is 1.3-1.5%) | 7.32 % | 1.4 | info | VERIFIED | info |

## 3. Where the data comes from, by bank

| bank | customers (primary) | API rows (no card mirrors) | share of API rows | share of ledger value |
|---|---:|---:|---:|---:|
| tbc | 32,000 | 18,415,827 | 32.8% | 32.2% |
| bog | 46,000 | 24,814,278 | 44.3% | 45.6% |
| terabank | 2,000 | 1,177,364 | 2.1% | 2.0% |
| liberty | 8,000 | 4,678,158 | 8.3% | 8.0% |
| silk | 500 | 300,509 | 0.5% | 0.5% |
| cartu | 1,000 | 602,558 | 1.1% | 1.0% |
| pasha | 900 | 534,616 | 1.0% | 0.9% |
| credo | 4,000 | 2,340,677 | 4.2% | 4.1% |
| halyk | 1,000 | 585,275 | 1.0% | 1.0% |
| basisbank | 2,500 | 1,462,805 | 2.6% | 2.5% |
| procredit | 1,500 | 824,579 | 1.5% | 1.5% |
| paysera | 600 | 334,574 | 0.6% | 0.6% |

**Overall: PASS**
