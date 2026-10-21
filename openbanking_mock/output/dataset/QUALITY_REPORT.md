# Quality report: dataset

50,000 customers, 200 shards.

## 1. API tables: fidelity and standards (must be 100%)

| check | passed | total | result |
|---|---:|---:|---|
| BIC matches ISO 9362 | 77,581 | 77,581 | PASS |
| IBAN is GE + 2 check + 2 letters + 16 digits | 78,094 | 78,094 | PASS |
| IBAN passes ISO 13616 mod-97 | 78,094 | 78,094 | PASS |
| IBAN unique per (bank, iban, currency, endpoint) across the whole dataset | 78,094 | 78,094 | PASS |
| TBC/BoG transactionId = c_/d_ + entryReference | 21,116,650 | 21,116,650 | PASS |
| accounts: currency is ISO 4217 | 141,979 | 141,979 | PASS |
| balance type is a Berlin Group / bank-published type | 573,004 | 573,004 | PASS |
| balanceAfterTransaction moves by exactly the booked amount | 26,502,193 | 26,502,193 | PASS |
| balances: currency is ISO 4217 | 573,004 | 573,004 | PASS |
| bankTransactionCode is an official ISO 20022 triple | 26,634,466 | 26,634,466 | PASS |
| booked entry has a bookingDate | 38,455,232 | 38,455,232 | PASS |
| card purchases are booked on business days | 20,246,596 | 20,246,596 | PASS |
| credit/debit prefix matches the sign | 21,116,650 | 21,116,650 | PASS |
| currencyExchange.unitCurrency is the foreign currency | 799,609 | 799,609 | PASS |
| embedded interimBooked equals the balance after the last booked entry | 67,611 | 67,611 | PASS |
| exchange rate is a plausible GEL price (1.5-6) | 799,609 | 799,609 | PASS |
| interbank credits and salaries are booked on business days | 1,016,619 | 1,016,619 | PASS |
| mandateId only on direct debits | 411,912 | 411,912 | PASS |
| no duplicate entry within a (bank, endpoint) | 38,552,934 | 38,552,934 | PASS |
| pending entry has no bookingDate | 97,702 | 97,702 | PASS |
| purposeCode is an official ISO 20022 code | 4,243,114 | 4,243,114 | PASS |
| purposeCode only on credit transfers / direct debits | 4,243,114 | 4,243,114 | PASS |
| transactions: currency is ISO 4217 | 38,552,934 | 38,552,934 | PASS |

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
| BoG + TBC: share of API transaction rows (card mirrors removed) | 77.08 % | 78 | ±5 % | DERIVED | PASS |
| Visa share of debit cards (rest Mastercard) | 34.05 % | 34 | ±4 % | UNVERIFIED | PASS |
| Visa share of Visa+Mastercard credit cards | 75.59 % | 75 | ±6 % | VERIFIED | PASS |
| customers holding a credit card | 22.78 % | 23 | ±5 % | DERIVED | PASS |
| ATM withdrawals as share of card transactions (count) | 6.45 % | 7 | ±2 % | UNVERIFIED | PASS |
| foreign-currency share of term deposits (by count) | 49.56 % | 50 | ±10 % | DERIVED | PASS |
| USD share of foreign-currency deposits (rest EUR) | 78.73 % | 79 | ±8 % | VERIFIED | PASS |
| average GEL term-deposit rate | 9.07 % p.a. | 8.9 | ±0.9 % p.a. | VERIFIED | PASS |
| average foreign-currency term-deposit rate | 2.73 % p.a. | 2.5 | ±0.7 % p.a. | VERIFIED | PASS |
| TBC/BoG primary customers paying the GEL 1/month SMS fee | 59.87 % | 60 | ±6 % | ASSUMED | PASS |
| food: share of modelled spend | 46.99 % | 46.5 | ±3 % | VERIFIED | PASS |
| transport: share of modelled spend | 13.47 % | 13.7 | ±3 % | VERIFIED | PASS |
| housing utilities: share of modelled spend | 13.66 % | 13.6 | ±3 % | VERIFIED | PASS |
| health: share of modelled spend | 12.95 % | 13.2 | ±3 % | VERIFIED | PASS |
| clothing: share of modelled spend | 7.00 % | 7 | ±3 % | VERIFIED | PASS |
| durables: share of modelled spend | 5.93 % | 6.1 | ±3 % | VERIFIED | PASS |
| mean monthly income in the supplied loan dataset vs Geostat mean wage (Q2-2026 GEL 2,389) | 3334.49 GEL | 2389 | info | VERIFIED | info |
| mean GEL consumer-loan rate vs NBG Mar-2026 (16.7%) | 18.26 % p.a. | 16.7 | info | VERIFIED | info |
| mean GEL mortgage rate vs NBG Mar-2026 (12.0%) | 12.38 % p.a. | 12 | info | VERIFIED | info |
| loans whose instalment exceeds the NBG PTI limit | 12.07 % | 0 | info | VERIFIED | info |
| customers with an overdue loan (df-driven; real retail NPL is 1.3-1.5%) | 7.34 % | 1.4 | info | VERIFIED | info |

## 3. Where the data comes from, by bank

| bank | customers (primary) | API rows (no card mirrors) | share of API rows | share of ledger value |
|---|---:|---:|---:|---:|
| tbc | 16,000 | 9,206,071 | 32.8% | 32.0% |
| bog | 23,000 | 12,402,980 | 44.2% | 45.5% |
| terabank | 1,000 | 584,564 | 2.1% | 2.0% |
| liberty | 4,000 | 2,332,418 | 8.3% | 8.2% |
| silk | 250 | 146,868 | 0.5% | 0.5% |
| cartu | 500 | 300,099 | 1.1% | 1.0% |
| pasha | 450 | 273,286 | 1.0% | 1.0% |
| credo | 2,000 | 1,173,086 | 4.2% | 4.1% |
| halyk | 500 | 295,325 | 1.1% | 1.0% |
| basisbank | 1,250 | 733,710 | 2.6% | 2.5% |
| procredit | 750 | 415,637 | 1.5% | 1.5% |
| paysera | 300 | 169,536 | 0.6% | 0.6% |

**Overall: PASS**
