# Synthetic loan population: fidelity report

100,000 synthetic loans, seed 20261021, fitted on Data.
Every row is new (no row of the real table is copied); the planted link between the label and the delinquency / score columns of the supplied data is reproduced, not removed.

## Gates

| check | measured | limit | result | detail |
|---|---|---|---|---|
| schema: header, dtypes, support, no missing | ok | none | PASS |  |
| event rate | 0.0356 | 0.0356 ± 0.00001 | PASS |  |
| marginals: max KS | 0.0030 | ≤ 0.02 | PASS | worst overdueAmount |
| marginals: max TV | 0.0060 | ≤ 0.02 | PASS | worst DEBT_DISBURSED_REGION |
| spikes: share of the dominant value | 0.0029 | ≤ 0.01 | PASS | worst ShortDaysInDeliquency |
| rank correlation: mean \|Δρ\| | 0.0060 | ≤ 0.02 | PASS |  |
| rank correlation: max \|Δρ\| | 0.0557 | ≤ 0.1 | PASS | PTI_RATIO ~ standardInstalmentCount |
| codes & flags: mean \|Δ Cramér's V\| | 0.0046 | ≤ 0.02 | PASS |  |
| codes & flags: max \|Δ Cramér's V\| | 0.0289 | ≤ 0.08 | PASS | ECONOMIC_SECTORS ~ LoanSegment |
| codes x quantities: mean \|Δ eta\| | 0.0167 | ≤ 0.02 | PASS | ECONOMIC_SECTORS ~ INCOME_AMOUNT_SOI04 |
| dictionary rules: max \|Δ violation share\| | 0.0148 | ≤ 0.03 | PASS | overdueAmount = OverduePrincipalAm + OverdueInterestCommission |
| label signal: max \|Δ single-column AUC\| | 0.0160 | ≤ 0.03 | PASS | DISBURSED_AMOUNT |
| run-off signature: max \|Δ event rate\| by number of zero balance columns | 0.0171 | ≤ 0.08 | PASS | at 4 zeros |
| 500-row blocks: median variance ratio | 0.4846 | 0.458 ± 0.12 | PASS |  |
| privacy: median distance to closest real row / real-to-real | 1.0790 | ≥ 0.9 | PASS |  |
| privacy: exact copies of real rows / duplicates | 0 / 0 | 0 / 0 | PASS |  |
| utility: AUC train-synthetic / test-real | 0.9700 | ≥ 0.9640 (real-on-real 0.9940) | PASS |  |

**Overall: PASS**

## Dictionary rules the supplied data breaks (share of rows violating)

|  | real | synthetic | gap |
|---|---|---|---|
| last1Month <= last3Month | 0.0467 | 0.0467 | 0.0000 |
| last3Month <= last6Month | 0.1303 | 0.1379 | 0.0076 |
| last6Month <= last12Month | 0.1422 | 0.1457 | 0.0035 |
| daysInDeliquency <= MaximumNumberDelinquencyDays | 0.0567 | 0.0613 | 0.0046 |
| MaximumNumberDelinquencyDays <= TotalNumberDelinquencyDays | 0.0614 | 0.0704 | 0.0090 |
| TotalNumberDelinquencyDaysLastYear <= TotalNumberDelinquencyDays | 0.0544 | 0.0670 | 0.0127 |
| overdueInstalmentsCount <= TotalNumberOverdueInst | 0.0063 | 0.0078 | 0.0015 |
| penaltyPaid <= penaltySum | 0.1091 | 0.1199 | 0.0108 |
| OutstandingPrincipalAm <= DISBURSED_AMOUNT | 0.3093 | 0.3222 | 0.0130 |
| OutstandingPrincipalAm <= outstandingAmount | 0.4235 | 0.4246 | 0.0012 |
| outstandingAmount <= TotalOutstandingAmount | 0.1313 | 0.1460 | 0.0147 |
| overdueAmount <= TotalDebtOverdue | 0.0389 | 0.0442 | 0.0053 |
| standardInstalmentAmount <= MaximumAmountOfInstalmentBySchedule | 0.5067 | 0.5050 | 0.0018 |
| overdueAmount = OverduePrincipalAm + OverdueInterestCommission | 0.1007 | 0.1155 | 0.0148 |
| material overdue money but zero overdue instalments | 0.0634 | 0.0689 | 0.0055 |
| monthly loans: ContractTermMonth = standardInstalmentCount (+-1) | 0.5682 | 0.5769 | 0.0087 |
| RemainingTerm (days) <= whole contract | 0.0883 | 0.0886 | 0.0003 |
| loanType = ConsumerLoan but the purpose is a mortgage | 0.0140 | 0.0144 | 0.0004 |
| loanType = MortgageLoan but the purpose is not a mortgage | 0.0203 | 0.0198 | 0.0004 |
| consumer purpose in the corporate / SME / micro segment | 0.1150 | 0.1147 | 0.0003 |
| private individual in the Business segment | 0.0206 | 0.0218 | 0.0012 |
| collateral above 0 but LTV_RATIO = 0 | 0.3071 | 0.3023 | 0.0047 |
| disbursed amount 0 but a balance is owed | 0.0196 | 0.0155 | 0.0041 |
| sector of purpose equals sector of income (where a purpose is given) | 0.5285 | 0.5199 | 0.0087 |

## Largest marginal gaps

| column | metric | gap |
|---|---|---|
| DEBT_DISBURSED_REGION | TV | 0.0060 |
| ECONOMIC_SECTORS | TV | 0.0049 |
| CLIENT_GEOGRAPHICAL_REGION | TV | 0.0043 |
| ECONOMIC_SECTORS_PURPOSE | TV | 0.0040 |
| PRINCIPAL_PAYMENT_FREQUENCY | TV | 0.0039 |
| overdueAmount | KS | 0.0030 |
| INTEREST_RATE_PAYMENT_FREQUENCY | TV | 0.0030 |
| OverduePrincipalAm | KS | 0.0030 |

## Run-off signature (zeros among the 8 balance / schedule columns)

| zeros | rows real | rows synthetic | event rate real | event rate synthetic | event gap |
|---|---|---|---|---|---|
| 0 | 0.7795 | 0.7815 | 0.0117 | 0.0118 | 0.0001 |
| 1 | 0.1486 | 0.1485 | 0.0464 | 0.0461 | 0.0002 |
| 2 | 0.0425 | 0.0412 | 0.1634 | 0.1682 | 0.0048 |
| 3 | 0.0193 | 0.0190 | 0.3489 | 0.3541 | 0.0052 |
| 4 | 0.0078 | 0.0077 | 0.5597 | 0.5768 | 0.0171 |
| 5 | 0.0023 | 0.0022 | 0.6754 | 0.6744 | 0.0010 |

## Largest single-column AUC gaps

| column | AUC real | AUC synthetic | gap |
|---|---|---|---|
| DISBURSED_AMOUNT | 0.4135 | 0.4295 | 0.0160 |
| last3Month | 0.2370 | 0.2487 | 0.0117 |
| last12Month | 0.2045 | 0.2162 | 0.0117 |
| OutstandingPrincipalAm | 0.1468 | 0.1581 | 0.0114 |
| PTI_RATIO | 0.6158 | 0.6264 | 0.0106 |
| overdueAmount | 0.9093 | 0.9184 | 0.0091 |
| TotalDebtOverdue | 0.9515 | 0.9427 | 0.0088 |
| last6Month | 0.1867 | 0.1955 | 0.0088 |
