# Quality report: bank_dataset

2,000 customers, 8 shards.

## 1. API tables: fidelity and standards (must be 100%)

| check | passed | total | result |
|---|---:|---:|---|
| BIC matches ISO 9362 | 3,082 | 3,082 | PASS |
| IBAN is GE + 2 check + 2 letters + 16 digits | 3,103 | 3,103 | PASS |
| IBAN passes ISO 13616 mod-97 | 3,103 | 3,103 | PASS |
| IBAN unique per (bank, iban, currency, endpoint) across the whole dataset | 3,103 | 3,103 | PASS |
| TBC/BoG transactionId = c_/d_ + entryReference | 840,332 | 840,332 | PASS |
| accounts: currency is ISO 4217 | 5,667 | 5,667 | PASS |
| balance type is a Berlin Group / bank-published type | 22,854 | 22,854 | PASS |
| balanceAfterTransaction moves by exactly the booked amount | 1,052,750 | 1,052,750 | PASS |
| balances: currency is ISO 4217 | 22,854 | 22,854 | PASS |
| bankTransactionCode is an official ISO 20022 triple | 1,057,911 | 1,057,911 | PASS |
| booked entry has a bookingDate | 1,524,941 | 1,524,941 | PASS |
| card purchases are booked on business days | 804,702 | 804,702 | PASS |
| credit/debit prefix matches the sign | 840,332 | 840,332 | PASS |
| currencyExchange.unitCurrency is the foreign currency | 31,412 | 31,412 | PASS |
| embedded interimBooked equals the balance after the last booked entry | 2,687 | 2,687 | PASS |
| exchange rate is a plausible GEL price (1.5-6) | 31,412 | 31,412 | PASS |
| interbank credits and salaries are booked on business days | 40,401 | 40,401 | PASS |
| mandateId only on direct debits | 16,309 | 16,309 | PASS |
| no duplicate entry within a (bank, endpoint) | 1,528,712 | 1,528,712 | PASS |
| pending entry has no bookingDate | 3,771 | 3,771 | PASS |
| purposeCode is an official ISO 20022 code | 169,300 | 169,300 | PASS |
| purposeCode only on credit transfers / direct debits | 169,300 | 169,300 | PASS |
| transactions: currency is ISO 4217 | 1,528,712 | 1,528,712 | PASS |

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
| BoG + TBC: share of API transaction rows (card mirrors removed) | 77.05 % | 78 | ±5 % | DERIVED | PASS |
| Visa share of debit cards (rest Mastercard) | 34.03 % | 34 | ±4 % | UNVERIFIED | PASS |
| Visa share of Visa+Mastercard credit cards | 73.95 % | 75 | ±6 % | VERIFIED | PASS |
| customers holding a credit card | 22.65 % | 23 | ±5 % | DERIVED | PASS |
| ATM withdrawals as share of card transactions (count) | 6.43 % | 7 | ±2 % | UNVERIFIED | PASS |
| foreign-currency share of term deposits (by count) | 54.95 % | 50 | ±10 % | DERIVED | PASS |
| USD share of foreign-currency deposits (rest EUR) | 75.43 % | 79 | ±8 % | VERIFIED | PASS |
| average GEL term-deposit rate | 9.03 % p.a. | 8.9 | ±0.9 % p.a. | VERIFIED | PASS |
| average foreign-currency term-deposit rate | 2.62 % p.a. | 2.5 | ±0.7 % p.a. | VERIFIED | PASS |
| TBC/BoG primary customers paying the GEL 1/month SMS fee | 60.19 % | 60 | ±6 % | ASSUMED | PASS |
| food: share of modelled spend | 46.76 % | 46.5 | ±3 % | VERIFIED | PASS |
| transport: share of modelled spend | 13.61 % | 13.7 | ±3 % | VERIFIED | PASS |
| housing utilities: share of modelled spend | 13.49 % | 13.6 | ±3 % | VERIFIED | PASS |
| health: share of modelled spend | 12.88 % | 13.2 | ±3 % | VERIFIED | PASS |
| clothing: share of modelled spend | 7.08 % | 7 | ±3 % | VERIFIED | PASS |
| durables: share of modelled spend | 6.18 % | 6.1 | ±3 % | VERIFIED | PASS |
| mean monthly income in the supplied loan dataset vs Geostat mean wage (Q2-2026 GEL 2,389) | 3329.66 GEL | 2389 | info | VERIFIED | info |
| mean GEL consumer-loan rate vs NBG Mar-2026 (16.7%) | 18.78 % p.a. | 16.7 | info | VERIFIED | info |
| mean GEL mortgage rate vs NBG Mar-2026 (12.0%) | 12.49 % p.a. | 12 | info | VERIFIED | info |
| loans whose instalment exceeds the NBG PTI limit | 16.61 % | 0 | info | VERIFIED | info |
| customers with an overdue loan (df-driven; real retail NPL is 1.3-1.5%) | 7.40 % | 1.4 | info | VERIFIED | info |

## 3. Where the data comes from, by bank

| bank | customers (primary) | API rows (no card mirrors) | share of API rows | share of ledger value |
|---|---:|---:|---:|---:|
| tbc | 640 | 362,921 | 32.6% | 32.0% |
| bog | 920 | 495,274 | 44.5% | 45.5% |
| terabank | 40 | 23,394 | 2.1% | 2.2% |
| liberty | 160 | 93,106 | 8.4% | 8.6% |
| silk | 10 | 6,045 | 0.5% | 0.7% |
| cartu | 20 | 12,405 | 1.1% | 0.9% |
| pasha | 18 | 10,097 | 0.9% | 0.8% |
| credo | 80 | 46,358 | 4.2% | 3.8% |
| halyk | 20 | 11,328 | 1.0% | 1.0% |
| basisbank | 50 | 29,166 | 2.6% | 2.4% |
| procredit | 30 | 17,085 | 1.5% | 1.6% |
| paysera | 12 | 6,638 | 0.6% | 0.5% |

**Overall: PASS**
