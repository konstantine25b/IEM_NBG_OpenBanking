# Provenance: specs/reference/

Retrieved 2026-10-21 (UTC) by an automated research pass. Raw downloads were kept outside the repo (see "Raw downloads") and were only parsed, never executed. `specs/MANIFEST.tsv` was not modified.

| File | Rows (excl. header) | SHA-256 |
|---|---|---|
| `ge_public_holidays_2025_2026.csv` | 36 | `0cdcb2a929ab46134cb595f9fe20af7d8ba8e3cc14110594e5372e0b388a5467` |
| `nbg_official_fx_2025_2026.csv` | 1206 (402 calendar days x 3 currencies) | `88867905c3fd178fc987029f6f137c66a2c2ed3dd3274a043b0068a73f0ceba5` |

---

## A. `ge_public_holidays_2025_2026.csv`

Columns: `date,name_en,name_ka,source_url,confidence`. One row per statutory public holiday date for 2025-01-01..2026-12-31 (14 fixed + 4 Easter days = 18 per year). Weekend dates are included (weekday is derivable from the date).

### Primary sources
- Labour Code of Georgia (Organic Law 4113-RS of 17/12/2010), consolidated text. English: https://matsne.gov.ge/en/document/view/1155567?publication=29 ; Georgian: https://matsne.gov.ge/ka/document/view/1155567?publication=29 (identical to the default view; `publication=0` is the 2010 initial text and must not be cited).
- **The list of holidays is Article 30 ("Holidays" / "უქმე დღეები"), not Article 29.** In the current numbering Article 29 is "Right to medical examinations". (The pre-2020 code had it as Article 20.) Article 30(1) has 14 items: Jan 1-2, Jan 7, Jan 19, Mar 3, Mar 8, Apr 9, Easter holidays (Good Friday, Holy Saturday, Easter Sunday, Easter Monday; "movable feast"), May 9, May 12, May 17, May 26, Aug 28, Oct 14, Nov 23. Amendment footers on Art. 30: Organic Laws 7177 (29/09/2020) and 4455 (17/09/2024). The latest consolidated version (16/09/2026, Organic Law 1863) touches only the Tripartite Commission and not Article 30. The consolidated text was read on 2026-10-21, so dates after that rest on the law as it stood then.
- Orthodox Easter was computed with the Meeus Julian algorithm plus the 13-day Julian-to-Gregorian offset: **2025-04-20** and **2026-04-12** (Western Easter 2026 is 2026-04-05, so the Orthodox and Western dates differ in 2026). Easter block dates: 2025-04-18/19/20/21 and 2026-04-10/11/12/13. officeholidays.com agrees.

### Independent cross-checks
- https://www.officeholidays.com/countries/georgia/2025 and .../2026: the same 18 statutory dates per year, plus the extra days listed under "Disagreements" below.
- https://pbservices.ge/blog/official-holidays-georgia/ : the same list, cites Article 30, states weekend holidays are not transferred, and says government-decree days apply to administrative bodies.
- timeanddate.com returned HTTP 403 on both requests (2025 and 2026 pages). I stopped and did not retry. publicholidays.ge does not resolve in DNS. Neither source was used.

### Disagreements and judgement calls
1. **Government "extra days off" are not in the CSV.** Four Government ordinances, found by title search on matsne.gov.ge, each declare days off only "for employees of administrative bodies" (Art. 1, under Labour Code 30(3)). Art. 3 of each says the ordinance "does not apply to financial obligations to persons under the supervision of the National Bank of Georgia". They are not bank closures. They are:

   | Ordinance | Date | Extra days off (public-sector only) | URL |
   |---|---|---|---|
   | N441 | 2024-12-24 | 2025-01-03, 2025-01-06, 2025-01-08..10 | https://matsne.gov.ge/ka/document/view/6350594 |
   | N381 | 2025-08-25 | 2025-08-29 | https://matsne.gov.ge/ka/document/view/6612161 |
   | N438 | 2025-10-02 | 2025-10-03 | https://matsne.gov.ge/ka/document/view/6639648 |
   | N563 | 2025-12-23 | 2026-01-05, 2026-01-06 | https://matsne.gov.ge/ka/document/view/6713428 |

   The title search ("უქმე დღეების / დღის განსაზღვრის შესახებ") found no ordinance in 2026 up to 2026-10-21. A differently titled ordinance could have been missed, so completeness is probable but not proven. N167 (15/05/2024) made 2024-05-17 a one-off day off; May 17 is now item i1 of Art. 30, and the last footer on Art. 30 is Law 4455 of 2024-09-17 (that Law 4455 added it is my inference). This is outside the window.
2. officeholidays.com 2025 lists Jan 3, 6, 8, 9, 10 and Aug 29 as holidays and marks only Oct 3 as "Govt. employees". Its 2026 page omits Jan 5-6. These match the ordinances above, which are public-sector only, so they were left out of the CSV.
3. pbservices says May 17 is "first celebrated in 2025"; matsne shows the 2024 one-off decree followed by the Code amendment. This does not affect 2025-2026.
4. `2026-04-11` (Holy Saturday) is a statutory holiday but is flagged `confidence=medium` because the National Bank overrode it: NBG order **N 89/04 of 2026-04-09** ("On declaring a banking day", signed by NBG President N. Turnava) declares "the non-working day under Labour Code Art. 30(1)(z), namely 11 April 2026" a banking day. The NBG news item of 2026-04-09 says RTGS and the NBG overnight deposit were fully available to commercial banks and microbanks that day. Order: https://nbg.gov.ge/fm/uploads/sagadaxdo/89.pdf ; news: https://nbg.gov.ge/en/media/news/payment-operations-of-the-national-bank-of-georgia-to-proceed-as-usual-on-the-day-before-e . No equivalent override was found for 2025-04-19 (Holy Saturday 2025), but absence from search results is not proof that none exists.

### National Bank (RTGS) notices
- RTGS "Working hours" page (https://nbg.gov.ge/en/page/working-hours-and-service-fees): "The system is closed on Saturdays, Sundays and all public holidays", with a pointer to NBG declarations of holidays as banking days (the page itself lists only one, dated 2024-05-03). Hours: 09:00 start of day, 09:30 open, 18:00 first cut-off, 18:45 second cut-off, 18:55 final cut-off, 19:00 end of day (Tbilisi time).
- NBG news of 2024-12-30 (https://nbg.gov.ge/en/media/news/national-bank-of-georgia-due-to-the-specifics-of-the-work-monetary-and-payment-operation) says that on the decree days 2025-01-03/06/08-10 RTGS, securities settlement and cash operations stay available to commercial banks and that the official exchange rate is still published. The 2023-12-29 notice (https://nbg.gov.ge/en/media/news/from-january-3-to-5-nbg-s-monetary-and-payment-transactions-will-proceed-without-disrupti) says the same for 2024-01-03..05.
- No NBG notice was found for the decree days 2025-08-29, 2025-10-03 or 2026-01-05/06, and no complete NBG "RTGS non-working days 2025/2026" list was found on the site. The RTGS page (https://nbg.gov.ge/en/page/rtgs-system) says the modernised system processes payments "on a continuous basis", which is a general description and not a holiday schedule.
- **Transferred working Saturdays: none found.** None of the four ordinances moves a working day to a Saturday. Art. 30(3) only lets an employer ask an employee to work on the next regular rest day in place of an ordinance-declared day off. The only Saturday-related measure in 2025-2026 is the NBG banking-day override of 2026-04-11 above.

### Weekend rule and what it means for banking days
- Art. 30 contains no transfer or "next working day" provision, so a holiday on a Saturday or Sunday is **not moved** to the Monday (pbservices states this too). In 2025-2026 no Monday substitute day exists in the law or in the ordinances.
- For banking: banking days are Mon-Fri minus statutory holidays, plus any NBG-declared exception (2026-04-11). A weekend holiday removes no weekday. Examples: Sun 2025-11-23 leaves Mon 2025-11-24 a normal banking day, and likewise Sun 2026-03-08 -> Mon 03-09, Sat 2026-05-09 -> Mon 05-11, Sun 2026-05-17 -> Mon 05-18. (NBG published an FX rate on each of those Mondays.)
- Ordinance days (table above) are excluded from the CSV because each ordinance is limited to administrative bodies (Art. 1) and carves out financial obligations owed to NBG-supervised persons (Art. 3). The evidence that NBG and RTGS kept working on those days varies by date: **2025-01-03/06/08-10** is confirmed by an NBG notice; **2026-01-05/06** and **2025-08-29** show an FX publication that evening (a proxy only; no NBG notice was found for either); **2025-10-03** is ambiguous, since no FX rate was published that evening, but FX was also skipped on two ordinary weekdays (2025-10-30 and 2026-09-25), so the skip proves nothing. Individual bank branches may still close; no source on that was checked.

### Holiday dates settled for 2025-10-19..2026-10-19 (18 dates; 12 fall on weekdays; 249 weekday banking days in that 366-day window)
2025-11-23 (Sun), 2026-01-01 (Thu), 2026-01-02 (Fri), 2026-01-07 (Wed), 2026-01-19 (Mon), 2026-03-03 (Tue), 2026-03-08 (Sun), 2026-04-09 (Thu), 2026-04-10 (Fri), 2026-04-11 (Sat; NBG banking day), 2026-04-12 (Sun), 2026-04-13 (Mon), 2026-05-09 (Sat), 2026-05-12 (Tue), 2026-05-17 (Sun), 2026-05-26 (Tue), 2026-08-28 (Fri), 2026-10-14 (Wed). The remaining 2026 date, 2026-11-23 (Mon), is in the CSV but outside this window.

### Caveats
- 2026 holidays after the retrieval date (notably 2026-11-23) follow the law as of 2026-10-21. A later ordinance could add public-sector days, but these would not be bank holidays under the ordinance pattern above.
- `name_ka` is taken from the Georgian Labour Code text. Short and compound forms (e.g. "ბედობა", "მარიამობა") were joined with the legal wording.
- All `source_url` values point to the Labour Code (Art. 30). The cross-checks and the NBG order are documented here rather than in the CSV.

---

## B. `nbg_official_fx_2025_2026.csv`

Columns: `date,currency,quantity,rate,rate_per_unit,change`. Currencies USD, EUR, GBP (in that order per date). All quantities are 1, so `rate_per_unit` equals `rate`. Unit is GEL per `quantity` units.

### Source and method
- Endpoint: `https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/?currencies=USD&currencies=EUR&currencies=GBP&date=YYYY-MM-DD` (the same JSON the page https://nbg.gov.ge/en/monetary-policy/currency links to).
- 402 sequential requests (one per calendar day 2025-09-15..2026-10-21), 1-2 s apart, descriptive User-Agent. All returned HTTP 200 on the first attempt: no 429/403, no retries, no failures. The combined SHA-256 of the 402 raw JSON files (sorted by name, concatenated) is `533b0ce72d8f88316798d6e30ce8e08b4eb6dbe82adf0b623e2d2b2d98f3a7c7`.
- The range parameters (`start/end`, `startDate/endDate`) are ignored; the endpoint returns the latest record. A call with no `date` returns the newest record. When I made it (2026-10-21) it was already the record valid from **2026-10-22**, which is outside the requested range and therefore not included.
- Consistency check against the NBG page: its server-rendered data shows EUR 2.9127, GBP 3.4660, USD 2.5978, valid from 2026-10-21. This equals the last rows of the CSV.

### Semantics (read this before using the file)
- The brief assumed NBG publishes every calendar day including weekends. It does not. NBG publishes one record per **working day, in the evening, valid from the next calendar day**. `?date=D` returns the record in force on D (latest record with valid-from <= D).
- **The file has one row per calendar day, each row being the endpoint's verbatim answer for that date.** So 128 of the 402 days (64 runs) are carry-forwards of an earlier record, and their `change` repeats that record's change, not a day-over-day change. There are 275 distinct NBG records in the file.
- `change` is NBG's signed `diff`: rate minus the previous NBG record's rate (verified for all 822 record-to-record steps). 8 of the 1206 rows have change 0.
- Carry-forward runs: 53 are the normal pattern, the Saturday record carried over Sunday and Monday. The other 11 are listed below (record valid-from date -> calendar days it is carried over). One is a partial run at the start of the range, and the rest follow a weekday statutory holiday or a holiday long weekend:
  - 2025-09-13 -> 09-15 (partial: the range starts on Monday 2025-09-15)
  - 2025-10-14 -> 10-15
  - 2026-01-01 -> 01-02..01-05
  - 2026-01-07 -> 01-08
  - 2026-01-17 -> 01-18..01-20
  - 2026-03-03 -> 03-04
  - 2026-04-09 -> 04-10..04-14 (the Easter block; no rate was published for the banking-day Saturday 2026-04-11)
  - 2026-05-12 -> 05-13
  - 2026-05-26 -> 05-27
  - 2026-08-28 -> 08-29..08-31
  - 2026-10-14 -> 10-15
- Cross-check against the holiday list: NBG made no FX publication on any weekend day or statutory holiday in the window, and published on the decree days 2026-01-05 and 2026-01-06 and on the Mondays after weekend holidays.
- Irregular publications (the values are returned as-is):
  - The record valid Saturday 2025-10-04 has a publication timestamp of Monday 2025-10-06 (nothing was published Friday 2025-10-03, a decree day).
  - The records valid 2025-10-31 and 2025-11-01 were stamped 2025-10-31 18:11Z/18:24Z (nothing on the evening of Thursday 2025-10-30), so the API's 2025-10-31 answer was only published late that day.
  - The record valid Saturday 2026-09-26 was published Monday 2026-09-28 (nothing on Friday 2026-09-25).
  - For these dates the API's answer is retroactive and not what was known in real time.
- No gaps: all 402 calendar days are present for all three currencies.

### Statistics (GEL per 1 unit, rates as published, 4 decimals)
| Currency | Min | Max | Mean, calendar-day rows (n=402) | Mean, distinct records (n=275) | First (2025-09-15) | Last (2026-10-21) |
|---|---|---|---|---|---|---|
| USD | 2.5978 (2026-10-21) | 2.7470 (2026-03-10) | 2.673951 | 2.673765 | 2.7002 | 2.5978 |
| EUR | 2.9066 (2026-10-13) | 3.2376 (2025-09-19) | 3.103465 | 3.102984 | 3.1679 | 2.9127 |
| GBP | 3.4342 (2026-10-09) | 3.7338 (2025-09-19) | 3.584138 | 3.584277 | 3.6599 | 3.4660 |

Min/max are the same under both counts. Dates are the first calendar day on which the value appears.

### Caveats
- The rates are NBG's official indicative rates, not bank buy/sell rates.
- `date` is the day the rate applies to, not the day it was published. Revisions: each date was pulled once in the main loop. Eight dates (2025-09-15..17 and 2026-10-17..21) had also been pulled a few minutes earlier as probes, and those responses are byte-identical, so no revision was seen in that short interval. A longer-term revision check was not done.
- Request pacing: about six page and PDF requests to nbg.gov.ge (notices, order PDF, working-hours page) ran while the API loop was still in progress, so requests were not strictly sequential. The overlap was a handful of requests and no throttling or error was seen.
- The CSV has exactly the six requested columns. The valid-from date of each row is not stored (the carry-forward rules above reconstruct it).

---

## Raw downloads (not in repo)
`/tmp/claude-1000/-home-bezhomatiashvili-Desktop-hakathon/921215cf-ae6f-4108-a78b-fc1d9dbb1845/scratchpad/ref_dl/` : `nbg_daily/` (402 JSON), `matsne/` (Labour Code and ordinance HTML), `nbg_notices/` (order PDF, news pages, working-hours page), `nbg_page/`, `xcheck/` (officeholidays and pbservices pages). This is scratch storage and may be deleted.
