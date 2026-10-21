"""Population + ledger simulation.

Layers (see docs/SIMULATION_MODELS.md):
  1. Static   - customers bootstrap-drawn from the supplied loan dataset (age/income/region/...).
  2. Latent   - one stress score per customer (PD rank, worst delinquency, negative status).
  3. Products - accounts, cards, loans (annuity schedules), deposits, spread over 1-3 banks.
  4. Cash-flow- day-by-day ledger: salary/remittances, bills, rent, loan instalments (with a
                delinquency plan taken from the dataset's overdue fields), card spend (Poisson
                counts x lognormal amounts, weekday/payday seasonality, balance feedback),
                P2P, FX, savings, credit-card cycle, fees.
Every ledger entry is generated once and shared by all bank dialects.
"""
from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any

import numpy as np

from obmock.banks import BANKS, BankProfile
from obmock.calibration import LoanFrame
from obmock.config import SPEC_DIR, WorldConfig
from obmock.reference import (
    BILLERS,
    CATEGORIES,
    DEPOSIT_RATES,
    EMPLOYER_STEMS,
    FEMALE_FIRST,
    FX_BASE,
    FX_SPREAD,
    MALE_FIRST,
    MONTHS_EN,
    MONTHS_KA,
    NARR,
    REGION_CITIES,
    REMITTANCE_CORRIDORS,
    SECTOR_NOUN,
    SURNAMES,
    XFER_FEE_TIERS,
    Category,
)
from obmock.util import (
    add_months,
    day_in_month,
    is_business_day,
    last_day_of_month,
    luhn_check_digit,
    make_iban,
    mask_pan,
    next_business_day,
    prev_business_day,
    stable_uuid,
    to_cents,
)
from obmock.world.model import (
    REF_BLOCK,
    REF_STRIDE,
    Account,
    Customer,
    Deposit,
    Fx,
    Instalment,
    Loan,
    Txn,
    World,
)

SALARY_DAYS = np.array([5, 10, 15, 20, 25, 28, 30])
SALARY_P = np.array([0.12, 0.22, 0.14, 0.08, 0.2, 0.1, 0.14])
MOBILE_PREFIX = ["555", "557", "568", "571", "574", "577", "579", "591", "592", "593", "595",
                 "597", "598", "599"]
PRODUCT_KEYS = {2: "consumer", 4: "instalment", 7: "mortgage", 10: "mortgage_construction",
                13: "mortgage_renovation"}
FOREIGN_MERCHANT_CCY = {
    "TEMU": "USD", "ALIEXPRESS": "USD", "AMAZON": "USD", "NETFLIX": "USD", "SPOTIFY": "EUR",
    "APPLE.COM/BILL": "USD", "GOOGLE *YOUTUBE": "USD", "BOOKING.COM": "EUR", "AIRBNB": "USD",
    "WIZZ AIR": "EUR", "PEGASUS": "EUR",
}
CC_CATEGORIES = {"restaurant", "delivery", "ecommerce", "clothing", "electronics",
                 "entertainment", "travel", "health", "grocery", "fastfood"}
WEEKDAY = {
    "restaurant": [0.8, 0.8, 0.9, 1.0, 1.4, 1.5, 1.1], "fastfood": [0.9, 0.9, 1, 1, 1.2, 1.3, 1.1],
    "grocery": [0.9, 0.9, 0.9, 1.0, 1.2, 1.4, 1.1],
    "entertainment": [0.7, 0.7, 0.8, 1, 1.4, 1.6, 1.2],
    "transit": [1.3, 1.3, 1.3, 1.3, 1.2, 0.5, 0.4], "rideshare": [1, 1, 1, 1, 1.3, 1.4, 1.0],
    "delivery": [0.9, 0.9, 1, 1, 1.3, 1.3, 1.2], "clothing": [0.7, 0.7, 0.8, 0.9, 1.1, 1.8, 1.4],
}
NOW_CUTOFF = time(15, 0)
Event = tuple[datetime, int, str, dict]


# ============================================================================== helpers
def _annuity(principal: float, r_month: float, n: int) -> float:
    if r_month <= 1e-9:
        return principal / n
    return principal * r_month / (1.0 - (1.0 + r_month) ** -n)


def _make_fx(cfg: WorldConfig, rng: np.random.Generator) -> Fx:
    start = cfg.as_of - timedelta(days=int(cfg.months * 31 + 40))
    n = (cfg.as_of - start).days + 2
    out: dict[str, dict[date, float]] = {}
    for ccy, base in FX_BASE.items():
        x = np.zeros(n)
        eps = rng.normal(0.0, 0.0016, n)
        for i in range(1, n):
            x[i] = 0.995 * x[i - 1] + eps[i]
        out[ccy] = {start + timedelta(days=i): round(float(base * (1 + x[i])), 4) for i in range(n)}
    return Fx(out)


NBG_FX_FILE = SPEC_DIR / "reference" / "nbg_official_fx_2025_2026.csv"


@lru_cache(maxsize=1)
def load_nbg_rates() -> dict[str, dict[date, float]]:
    """NBG official daily rates (GEL per 1 unit), vendored in specs/reference; {} if absent."""
    if not NBG_FX_FILE.exists():
        return {}
    out: dict[str, dict[date, float]] = {}
    with NBG_FX_FILE.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(r["currency"], {})[date.fromisoformat(r["date"])] = float(r["rate_per_unit"])
    return out


def make_fx(cfg: WorldConfig) -> Fx:
    """Real NBG rates when the vendored file covers the window, else a synthetic random walk."""
    start = cfg.as_of - timedelta(days=int(cfg.months * 31 + 40))
    real = load_nbg_rates()
    usd = real.get("USD", {})
    if usd and min(usd) <= start + timedelta(days=20) and max(usd) >= cfg.as_of - timedelta(days=3):
        return Fx(real)
    return _make_fx(cfg, np.random.default_rng([cfg.seed, 0]))


def _plan_banks(cfg: WorldConfig, rng: np.random.Generator) -> list[list[str]]:
    keys = list(BANKS)
    w = np.array([BANKS[k].weight for k in keys])
    w = w / w.sum()
    # Primary bank by largest-remainder quota (sourced shares hold exactly, not just in expectation),
    # shuffled so the customer index carries no information about the bank.
    quota = w * cfg.n_customers
    seats = np.floor(quota).astype(int)
    left = cfg.n_customers - int(seats.sum())
    seats[np.argsort(-(quota - seats), kind="stable")[:left]] += 1
    primary = np.repeat(np.arange(len(keys)), seats)
    rng.shuffle(primary)
    plans: list[list[str]] = []
    for first in primary:
        banks = [keys[int(first)]]
        for p_extra in (0.30, 0.10):  # ASSUMED multi-banking rates
            if rng.random() < p_extra:
                ww = np.array([0.0 if k in banks else x for k, x in zip(keys, w, strict=True)])
                banks.append(keys[int(rng.choice(len(keys), p=ww / ww.sum()))])
            else:
                break
        plans.append(banks)
    counts = Counter(b for p in plans for b in p)
    for k in keys:
        guard = 0
        while counts[k] < cfg.min_customers_per_bank and guard < cfg.n_customers * 60:
            guard += 1
            j = int(rng.integers(cfg.n_customers))
            if k in plans[j] or len(plans[j]) >= 3:
                continue
            plans[j].append(k)
            counts[k] += 1
    return plans


def _plan_pids(n: int, rng: np.random.Generator) -> list[str]:
    """Unique 11-digit Georgian personal numbers, drawn once for the whole population."""
    out: dict[str, None] = {}
    while len(out) < n:
        out["".join(str(int(x)) for x in rng.integers(0, 10, 11))] = None
    return list(out)


def _make_customer(
    n: int, row: dict[str, Any], stress: float, banks: list[str], pid: str,
    rng: np.random.Generator, cfg: WorldConfig,
) -> Customer:
    sex = int(row["SEX"])
    firsts = MALE_FIRST if sex == 1 else FEMALE_FIRST
    f = firsts[int(rng.integers(len(firsts)))]
    last = SURNAMES[int(rng.integers(len(SURNAMES)))]
    age = int(row["Age"])
    birth = cfg.as_of - timedelta(days=int(age * 365.25 + rng.integers(0, 365)))
    region = int(row["CLIENT_GEOGRAPHICAL_REGION"])
    rname, cities = REGION_CITIES.get(region, ("Other", ["Tbilisi"]))
    city = cities[int(rng.integers(len(cities)))]
    income = float(row["INCOME_AMOUNT_TOTAL"])
    soi = float(row["INCOME_AMOUNT_SOI04"])
    remit = min(soi if soi >= 5.0 else 0.0, 0.7 * income)
    sector = int(row["ECONOMIC_SECTORS"])
    stem = EMPLOYER_STEMS[int(rng.integers(len(EMPLOYER_STEMS)))]
    employer = f"LLC {stem} {SECTOR_NOUN.get(sector, 'Service')}".upper()
    msisdn = int(rng.integers(0, 10 ** 6))
    truth: dict[str, Any] = {f"df_{k}": v for k, v in row.items()}
    truth.update(stress=round(stress, 4), pd_pct=row["probabilityOfDefault"], score=row["score"])
    return Customer(
        cid=f"C{n + 1:05d}", person_id=pid, first_ka=f[0], last_ka=last[0], first_en=f[1],
        last_en=last[1], sex=sex, birth_date=birth,
        msisdn=f"5{MOBILE_PREFIX[int(rng.integers(len(MOBILE_PREFIX)))][1:]}{msisdn:06d}",
        region_code=region, region_name=rname, city=city,
        segment="ORGA" if int(row["CLIENT_LEGAL_FORM"]) == 1 else "PRIV",
        income_month_cents=to_cents(income), remit_month_cents=to_cents(remit), employer=employer,
        sector=sector, salary_day=int(rng.choice(SALARY_DAYS, p=SALARY_P)), stress=stress,
        banks=list(banks), truth=truth, index=n,
    )


# ============================================================================== per-customer sim
class _CustomerSim:
    def __init__(self, world: World, cust: Customer, row: dict[str, Any],
                 rng: np.random.Generator):
        self.w, self.c, self.row, self.rng = world, cust, row, rng
        self.as_of = world.as_of
        self.ws = world.window_start
        self.now = datetime.combine(self.as_of, NOW_CUTOFF)
        self.income = cust.income_month_cents / 100.0
        self.stress = cust.stress
        self.avail: dict[str, int] = {}
        self.sched: dict[date, list[Event]] = {}
        self.stoch: dict[date, list[Event]] = {}
        self.cur: dict[str, Account] = {}
        self.fx_acct: Account | None = None
        self.credit: Account | None = None
        self.savings: list[Account] = []
        self.dep_by_acct: dict[str, Deposit] = {}
        self.loan: Loan | None = None
        self.loan_visible = False
        self.rent = 0.0
        self.order = 0
        self.spend_scale = 1.0
        self.reserve = 0
        self.save_rate = 0.0
        self.uses: dict[str, bool] = {}
        self.corridor = REMITTANCE_CORRIDORS[0]
        self.tags: dict[str, Any] = {}

    # ------------------------------------------------------------------ small utils
    def _ts(self, d: date, h0: float = 8, h1: float = 22) -> datetime:
        sec = int(self.rng.uniform(h0 * 3600, h1 * 3600))
        return datetime.combine(d, time(sec // 3600, (sec // 60) % 60, sec % 60))

    def _iban(self, prof: BankProfile, digit: str) -> str:
        while True:
            body = "".join(str(int(x)) for x in self.rng.integers(0, 10, 15))
            iban = make_iban(f"{prof.iban_code}{digit}{body}")
            if iban not in self.w._ibans:
                self.w._ibans.add(iban)
                return iban

    def _pan(self, scheme: str) -> str:
        bins = {"VISA": ["418057", "426397", "439020", "476157"],
                "MC": ["517805", "545424", "533286"]}
        b = bins[scheme][int(self.rng.integers(len(bins[scheme])))]
        body = b + "".join(str(int(x)) for x in self.rng.integers(0, 10, 9))
        return mask_pan(body + luhn_check_digit(body))

    def _owner(self, prof: BankProfile) -> str:
        return self.c.name_ka if prof.name_script == "ka" else self.c.name_en

    def _rand_bank(self) -> str:
        keys = list(BANKS)
        p = np.array([BANKS[k].weight for k in keys])
        return keys[int(self.rng.choice(len(keys), p=p / p.sum()))]

    def _ext_iban(self, bank: str) -> str:
        return self._iban(BANKS[bank], "1")

    def _person(self) -> tuple[str, str, str, str, str]:
        """(name_en, name_ka, iban, bic, personal_id) of a random third party."""
        sx = MALE_FIRST if self.rng.random() < 0.5 else FEMALE_FIRST
        f = sx[int(self.rng.integers(len(sx)))]
        s = SURNAMES[int(self.rng.integers(len(SURNAMES)))]
        bank = self._rand_bank()
        return (f"{f[1]} {s[1]}", f"{f[0]} {s[0]}", self._ext_iban(bank), BANKS[bank].bic,
                "".join(str(int(x)) for x in self.rng.integers(0, 10, 11)))

    def _add_event(self, d: date, ts: datetime, kind: str, store: str = "sched",
                   **p: Any) -> None:
        if d < self.ws or d > self.as_of:
            return
        self.order += 1
        target = self.sched if store == "sched" else self.stoch
        target.setdefault(d, []).append((ts, self.order, kind, p))

    def post(self, acct: Account, d_book: date, ts: datetime, amount: int, kind: str,
             d_value: date | None = None, **kw: Any) -> Txn:
        status = "pending" if d_book > self.as_of else "booked"
        seq = self.w.next_seq()
        t = Txn(seq=seq, account_id=acct.id, booking_date=d_book, value_date=d_value or d_book,
                ts=ts, amount=int(amount), currency=acct.currency, kind=kind, status=status,
                ref=f"{BANKS[acct.bank].iban_code}{seq:010d}", **kw)
        acct.txns.append(t)
        self.avail[acct.id] += int(amount)
        return t

    @staticmethod
    def _month_name(d: date, ka: bool) -> str:
        return (MONTHS_KA if ka else MONTHS_EN)[d.month - 1]

    def _narr(self, key: str, d: date | None = None) -> tuple[str, str]:
        ka, en = NARR[key]
        if d is not None:
            ka = ka.format(mon=self._month_name(d, True))
            en = en.format(mon=self._month_name(d, False))
        return ka, en

    # ------------------------------------------------------------------ setup
    def run(self) -> None:
        self._setup_accounts()
        self._setup_loan()
        self._setup_savings()
        self._calibrate_spend()
        self._build_schedule()
        self._draw_stochastic()
        d = self.ws
        while d <= self.as_of:
            self._run_day(d)
            d += timedelta(days=1)
        self.w.customers[self.c.cid].truth.update(self.tags)

    def _card_expiry(self) -> str:
        yr = (self.as_of.year + int(self.rng.integers(1, 5))) % 100
        return f"{int(self.rng.integers(1, 13)):02d}/{yr:02d}"

    def _setup_accounts(self) -> None:
        c, rng, ws = self.c, self.rng, self.ws
        months = self.w.cfg.months
        for j, bank in enumerate(c.banks):
            prof = BANKS[bank]
            if j == 0 or rng.random() < 0.7:
                opened = ws - timedelta(days=int(rng.integers(60, 2500)))
            else:
                opened = ws + timedelta(days=int(rng.integers(15, max(16, int(months * 21)))))
            od = 0
            if j == 0 and c.segment == "PRIV" and rng.random() < 0.28:
                od = to_cents(min(5000.0, max(200.0, round(self.income * rng.uniform(0.3, 0.8), -2))))
            # NBG Jul-2025: debit Mastercard ~5.6m vs Visa ~2.8m cards (partly UNVERIFIED)
            scheme = "VISA" if rng.random() < 0.34 else "MC"
            a = Account(
                id=stable_uuid("acct", c.cid, bank, "cur"), bank=bank, cid=c.cid, kind="current",
                currency="GEL", iban=self._iban(prof, "1"), product=prof.products["current"],
                name=self._owner(prof), opened=opened, usage=c.segment, credit_limit=od,
                pan_masked=self._pan(scheme), scheme=scheme, card_expiry=self._card_expiry(),
            )
            if j == 0:
                med = max(60.0, 0.55 * self.income * (1.0 - 0.7 * self.stress))
                a.opening_balance = to_cents(rng.lognormal(math.log(med), 0.7))
            elif opened < ws:
                a.opening_balance = to_cents(rng.uniform(0, 0.3) * self.income)
            self.w.add_account(a)
            self.cur[bank] = a
            self.avail[a.id] = a.opening_balance
        self.primary = self.cur[c.banks[0]]
        wts = np.array([x[2] for x in REMITTANCE_CORRIDORS])
        self.corridor = REMITTANCE_CORRIDORS[int(rng.choice(len(wts), p=wts / wts.sum()))]
        if (c.remit_month_cents > 0 and rng.random() < 0.6) or rng.random() < 0.12:
            prof = BANKS[c.banks[0]]
            ccy = self.corridor[1]
            fa = Account(
                id=stable_uuid("acct", c.cid, c.banks[0], "fx"), bank=c.banks[0], cid=c.cid,
                kind="fx", currency=ccy, iban=self._iban(prof, "2"),
                product=f"{prof.products['fx']} {ccy}", name=self._owner(prof),
                opened=ws - timedelta(days=int(rng.integers(30, 1500))), usage=c.segment,
                opening_balance=to_cents(rng.uniform(5, 900) / FX_BASE[ccy] * self.income / 800.0),
            )
            self.fx_acct = self.w.add_account(fa)
            self.avail[fa.id] = fa.opening_balance
        # ~21% of customers hold a credit card (761k credit cards vs ~3m adults, NBG Jul-2025)
        if rng.random() < 0.05 + 0.30 * min(1.0, self.income / 5000.0):
            bank = c.banks[0] if rng.random() < 0.7 else c.banks[-1]
            prof = BANKS[bank]
            limit = min(15000.0, max(500.0, round(self.income * rng.uniform(1.0, 3.0), -2)))
            scheme = "VISA" if rng.random() < 0.75 else "MC"  # Visa 299k vs Mastercard 100k credit cards
            ca = Account(
                id=stable_uuid("acct", c.cid, bank, "cc"), bank=bank, cid=c.cid,
                kind="card_credit", currency="GEL", iban=None, product=prof.products["credit_card"],
                name=self._owner(prof),
                opened=max(ws - timedelta(days=int(rng.integers(60, 1200))), self.cur[bank].opened),
                usage=c.segment, credit_limit=to_cents(limit), pan_masked=self._pan(scheme),
                scheme=scheme, card_expiry=self._card_expiry(),
            )
            ca.opening_balance = -to_cents(limit * rng.uniform(0, 0.25) * (0.3 + self.stress))
            self.credit = self.w.add_account(ca)
            self.avail[ca.id] = ca.opening_balance

    def _setup_loan(self) -> None:
        r, c, rng = self.row, self.c, self.rng
        purpose = int(r["PRODUCT_PURPOSE"])
        key = PRODUCT_KEYS.get(purpose, "consumer")
        mort = key.startswith("mortgage")
        principal = float(r["DISBURSED_AMOUNT"])
        if principal < 300:
            principal = max(float(r["OutstandingPrincipalAm"]), rng.uniform(500, 6000))
        principal = min(400_000.0, principal)
        rate = float(r["INTEREST_RATE_NOMINAL"])
        if rate < 0.02:
            rate = rng.uniform(0.095, 0.13) if mort else rng.uniform(0.14, 0.28)
        rate = min(0.45, rate)
        term = int(min(300, max(3, float(r["ContractTermMonth"]))))
        if mort and term < 60:
            term = int(rng.integers(120, 241))
        k = term - float(r["RemainingTerm"]) / 30.4
        if not (0 <= k < term):
            k = rng.uniform(1, max(2, term - 1))
        k = int(min(term - 1, max(1, k)))  # at least one month old: no same-day disbursement
        start = add_months(self.as_of, -k)
        pay_day = int(rng.choice([1, 3, 5, 8, 10, 12, 15, 18, 20, 22, 25, 27]))
        start = start.replace(day=min(start.day, 28))
        inst = _annuity(principal, rate / 12.0, term)
        cap = 0.6 * self.income
        if inst > cap:
            principal *= cap / inst
        p_cents = to_cents(principal)
        sched, inst_cents = self._schedule(p_cents, rate, term, start, pay_day)
        visible = bool(rng.random() < 0.85)
        loan_bank = c.banks[0] if (len(c.banks) == 1 or rng.random() < 0.65) else \
            c.banks[int(rng.integers(1, len(c.banks)))]
        funding = self.cur[loan_bank] if visible else self.primary
        if visible and funding.opened > start - timedelta(days=5):
            funding.opened = start - timedelta(days=5)
        lid = stable_uuid("loan", c.cid)
        loan = Loan(
            id=lid, bank=loan_bank if visible else "EXT", cid=c.cid,
            account_id=stable_uuid("acct", c.cid, loan_bank, "loan"),
            funding_account_id=funding.id, product_key=key, purpose_code=purpose, currency="GEL",
            principal=p_cents, rate=rate,
            rate_type="Fixed" if str(r["instalmentType"]) == "Fixed" else "Variable",
            term_months=term, start=start, maturity=add_months(start, term).replace(day=pay_day),
            pay_day=pay_day, instalment=inst_cents, schedule=sched, as_of=self.as_of,
            collateral=mort or float(r["guaranteeAmount"]) > 0,
            guarantee=float(r["guaranteeAmount"]) > 0,
            restructured=bool(r["RestructuringStatusBool"]), refinanced=bool(r["RefinancingStatusBool"]),
        )
        self._plan_payments(loan)
        loan.refresh()
        self.loan, self.loan_visible = loan, visible
        self.tags.update(
            loan_visible=visible, loan_bank=loan.bank, sim_outstanding=loan.outstanding_principal / 100,
            sim_overdue_count=loan.overdue_count, sim_days_in_delinquency=loan.days_in_delinquency,
            sim_instalment=loan.instalment / 100, sim_principal=loan.principal / 100,
        )
        if visible:
            prof = BANKS[loan_bank]
            acct = Account(
                id=loan.account_id, bank=loan_bank, cid=c.cid, kind="loan", currency="GEL",
                iban=None, product=prof.products.get(key, prof.products["consumer"]),
                name=self._owner(prof), opened=start, usage=c.segment,
                meta={"loan_id": lid,
                      "contract_no": f"LN-{start.year}-{int(rng.integers(100000, 999999))}"},
            )
            self.w.add_account(acct)
            self.w.loans[lid] = loan

    @staticmethod
    def _schedule(p: int, rate: float, n: int, start: date,
                  pay_day: int) -> tuple[list[Instalment], int]:
        r = rate / 12.0
        pay = int(round(_annuity(p, r, n)))
        bal, out = p, []
        for k in range(1, n + 1):
            due = add_months(start.replace(day=1), k)
            due = day_in_month(due.year, due.month, pay_day)
            interest = int(round(bal * r))
            principal = pay - interest
            if k == n or principal > bal:
                principal = bal
            principal = max(0, principal)
            out.append(Instalment(k, due, principal, interest))
            bal -= principal
        return out, pay

    def _plan_payments(self, loan: Loan) -> None:
        """Turn the dataset's overdue/delinquency fields into a per-instalment payment plan."""
        r, rng = self.row, self.rng
        due_past = [i for i in loan.schedule if i.due <= self.as_of]
        for i in due_past:
            if i.due < self.ws:
                i.paid_on, i.paid_amount = i.due, i.total
        win = [i for i in due_past if i.due >= self.ws]
        n_over = min(int(r["overdueInstalmentsCount"]), len(win), 8)
        hist_total = max(0, int(r["TotalNumberOverdueInst"]) - n_over)
        frac = min(0.6, hist_total / max(1, len(due_past)))
        avg_lag = min(45.0, max(2.0, float(r["TotalNumberDelinquencyDays"])
                                / max(1.0, float(r["TotalNumberOverdueInst"]))))
        max_lag = int(min(120, max(3, float(r["MaximumNumberDelinquencyDays"]))))
        cut = len(win) - n_over
        for i in win[:cut]:
            lag = 0
            if rng.random() < frac:
                lag = int(min(max_lag, max(1, rng.geometric(1.0 / avg_lag))))
                lag = min(lag, (self.as_of - i.due).days)
            i.paid_on = i.due + timedelta(days=lag)
            i.paid_amount = i.total
            if lag > 0:
                i.penalty = min(int(i.total * 0.15), int(i.total * 0.001 * lag))
        for i in win[cut:]:
            i.paid_on = None
        self.tags.update(overdue_planned=n_over, late_rate=round(frac, 3))

    def _setup_savings(self) -> None:
        c, rng, ws = self.c, self.rng, self.ws
        p_save = 0.12 + 0.5 * (1 - self.stress) * min(1.0, self.income / 5000.0)
        if rng.random() >= p_save:
            return
        bank = c.banks[0] if (len(c.banks) == 1 or rng.random() < 0.7) else c.banks[-1]
        prof = BANKS[bank]
        term_dep = rng.random() < 0.55
        # NBG Aug-2026: term deposits are half GEL, half foreign currency; foreign currency is USD
        # 77% / EUR 20% (1tv.ge, July 2026), i.e. ~79% USD among USD+EUR
        ccy = "GEL" if rng.random() < 0.50 else ("USD" if rng.random() < 0.79 else "EUR")
        amount_gel = min(40000.0, max(500.0, round(self.income * rng.uniform(0.8, 6.0), -2)))
        rate_tbl = DEPOSIT_RATES[ccy]
        term = int(rng.choice(list(rate_tbl)))
        rate = rate_tbl[term] + rng.uniform(-0.005, 0.01)
        mid = 1.0 if ccy == "GEL" else self.w.fx.mid(ccy, self.as_of)
        principal = to_cents(amount_gel / mid)
        if rng.random() < 0.55:
            # started before the window; for term deposits keep maturity inside/after the window
            max_back = max(15, term * 30 - 40) if term_dep else 300
            start = ws - timedelta(days=int(rng.integers(10, max_back + 1)))
        else:
            start = ws + timedelta(days=int(rng.integers(20, max(21, self.w.cfg.months * 25))))
        start = max(start, self.cur[bank].opened + timedelta(days=3))
        maturity = add_months(start, term) if term_dep else None
        acct = Account(
            id=stable_uuid("acct", c.cid, bank, "sav"), bank=bank, cid=c.cid,
            kind="savings_term" if term_dep else "savings_flex", currency=ccy,
            iban=self._iban(prof, "3"),
            product=prof.products["savings_term" if term_dep else "savings_flex"],
            name=self._owner(prof), opened=start, usage=c.segment,
            meta={"term_months": term if term_dep else None},
        )
        dep = Deposit(id=stable_uuid("dep", c.cid), bank=bank, cid=c.cid, account_id=acct.id,
                      kind="term" if term_dep else "flex", currency=ccy, principal=principal,
                      rate=rate if term_dep else float(rng.uniform(0.01, 0.035)),
                      term_months=term, start=start, maturity=maturity)
        acct.meta["rate"] = round(dep.rate, 4)
        if start < ws:
            months_el = max(0, (ws.year - start.year) * 12 + ws.month - start.month)
            acct.opening_balance = int(round(principal * (1 + dep.rate / 12) ** months_el)) \
                if term_dep else principal
        self.w.add_account(acct)
        self.w.deposits[dep.id] = dep
        self.dep_by_acct[acct.id] = dep
        self.avail[acct.id] = acct.opening_balance
        self.savings.append(acct)
        self.tags.update(saver=True)

    def _lam(self, cat: Category) -> float:
        lam = cat.lam + cat.stress_boost * self.stress ** 2
        if cat.key == "atm_cash":
            lam *= 1 + 0.8 * self.stress
        if cat.key in ("restaurant", "entertainment", "travel"):
            lam *= 1 - 0.3 * self.stress
        return lam

    def _calibrate_spend(self) -> None:
        rng = self.rng
        mortgage = bool(self.loan and self.loan.product_key.startswith("mortgage"))
        if self.c.segment == "PRIV" and rng.random() < (0.12 if mortgage else 0.35):
            tb = 1.0 if self.c.region_code == 3 else 0.6
            self.rent = min(2500.0, max(250.0, round(self.income * rng.uniform(0.14, 0.28) * tb, -1)))
        self.uses = {k: bool(rng.random() < cat.prob) for k, cat in CATEGORIES.items()}
        bills = sum(b[5] * 1.2 for b in BILLERS)
        loan_pay = self.loan.instalment / 100.0 if self.loan else 0.0
        self.save_rate = float(min(0.25, rng.beta(2, 9) * (1 - 0.9 * self.stress)))
        target = max(0.14 * self.income,
                     self.income - self.rent - bills - loan_pay - self.save_rate * self.income)
        ef = (self.income / 2700.0) ** 0.12
        exp_total = 0.0
        for k, cat in CATEGORIES.items():
            if self.uses[k]:
                exp_total += (self._lam(cat) * ef * cat.median
                              * (self.income / 2700.0) ** cat.income_elasticity
                              * math.exp(cat.sigma ** 2 / 2))
        self.spend_scale = float(min(3.0, max(0.2, 0.9 * target / max(1.0, exp_total))))
        self.tags.update(spend_scale=round(self.spend_scale, 3), save_rate=round(self.save_rate, 3),
                         rent=self.rent)

    # ------------------------------------------------------------------ scheduled events
    def _months(self) -> list[tuple[int, int]]:
        out, d = [], self.ws.replace(day=1)
        while d <= self.as_of:
            out.append((d.year, d.month))
            d = add_months(d, 1)
        return out

    def _build_schedule(self) -> None:
        c, rng = self.c, self.rng
        prim = self.primary
        sal_sigma = 0.12 if rng.random() < 0.25 else 0.01
        net = (c.income_month_cents - c.remit_month_cents) / 100.0
        has_p = {"electricity": 1.0, "water": 0.9, "gas": 0.6, "waste": 0.8, "internet": 0.8,
                 "mobile": 1.0}
        bill_day = {b[0]: int(rng.integers(8, 26)) for b in BILLERS}
        has_bill = {k: bool(rng.random() < p) for k, p in has_p.items()}
        hh = (self.income / 2700.0) ** 0.15
        remit_n = 1 + int(rng.random() < 0.45)
        fee_pick = float(rng.choice([0.0, 0.5, 1.0, 2.0]))
        paid_sms = rng.random() < 0.6  # share of customers keeping the paid SMS service: ASSUMED
        # TBC and BoG both charge GEL 1/month for SMS notifications (gbc.ge, "TBC also cancels free
        # SMS bank"); other banks' monthly fees are ASSUMED
        fee_amt = (1.0 if paid_sms else 0.0) if c.banks[0] in ("tbc", "bog") else fee_pick
        cc_day = 20 + int(c.cid[1:]) % 6
        prev_turnover = 0.0
        for (y, m) in self._months():
            first = date(y, m, 1)
            last_d = last_day_of_month(y, m)
            if c.segment == "ORGA":
                turnover = max(300.0, self.income * rng.normal(1.0, 0.18))
                n_in = 3 + int(rng.poisson(4))
                for sh in rng.dirichlet(np.full(n_in, 1.5)) * turnover:
                    dd = day_in_month(y, m, int(rng.integers(1, last_d + 1)))
                    self._add_event(dd, self._ts(dd, 9, 17), "biz_in", amount=to_cents(sh))
                if prev_turnover > 0:
                    dd = day_in_month(y, m, 15)
                    self._add_event(dd, self._ts(dd, 10, 15), "tax",
                                    amount=to_cents(prev_turnover * 0.01))
                prev_turnover = turnover
            else:
                # a salary due on a weekend/public holiday is paid on the previous business day
                # (direction ASSUMED)
                sd = prev_business_day(day_in_month(y, m, c.salary_day))
                amt = max(100.0, net * rng.normal(1.0, sal_sigma))
                if m == 12 and rng.random() < 0.25:
                    amt *= 1.3
                self._add_event(sd, self._ts(sd, 9, 11), "salary", amount=to_cents(amt), mon=first)
                sc = sd + timedelta(days=3)
                self._add_event(sc, self._ts(sc, 12, 14), "sav_check")
            if c.remit_month_cents > 0:
                per = c.remit_month_cents / 100.0 / remit_n
                for _ in range(remit_n):
                    dd = day_in_month(y, m, int(rng.integers(1, last_d + 1)))
                    self._add_event(dd, self._ts(dd, 9, 19), "remit",
                                    amount=to_cents(per * rng.lognormal(0.0, 0.25)))
            if self.rent > 0:
                dd = day_in_month(y, m, int(rng.integers(1, 11)))
                self._add_event(dd, self._ts(dd, 10, 18), "rent", amount=to_cents(self.rent),
                                mon=first, acct_id=prim.id)
            for key, name, mcc, nka, nen, med, winter in BILLERS:
                if not has_bill[key]:
                    continue
                if m in (12, 1, 2, 3):
                    season = winter
                elif m in (11, 4):
                    season = 1 + (winter - 1) * 0.4
                else:
                    season = 1.0
                amt = med * season * hh * rng.lognormal(0.0, 0.18)
                dd = day_in_month(y, m, bill_day[key])
                self._add_event(dd, self._ts(dd, 9, 21), "bill", amount=to_cents(amt),
                                biller=name, mcc=mcc, nka=nka, nen=nen, acct_id=prim.id)
            for _ in range(int(rng.poisson(1.1))):
                dd = day_in_month(y, m, int(rng.integers(1, last_d + 1)))
                amt = round(70 * (self.income / 2700.0) ** 0.4 * rng.lognormal(0.0, 0.9) / 5) * 5
                self._add_event(dd, self._ts(dd, 10, 22), "p2p_out", amount=to_cents(max(10.0, amt)))
            for _ in range(int(rng.poisson(0.7))):
                dd = day_in_month(y, m, int(rng.integers(1, last_d + 1)))
                amt = round(110 * (self.income / 2700.0) ** 0.3 * rng.lognormal(0.0, 0.9) / 5) * 5
                self._add_event(dd, self._ts(dd, 10, 22), "p2p_in", amount=to_cents(max(10.0, amt)))
            if (c.segment == "ORGA" and rng.random() < 0.4) or rng.random() < 0.06:
                dd = day_in_month(y, m, int(rng.integers(1, last_d + 1)))
                self._add_event(dd, self._ts(dd, 10, 16), "cash_dep",
                                amount=to_cents(max(50.0, round(self.income * rng.uniform(0.1, 0.5), -1))))
            if self.credit is not None:
                dd = day_in_month(y, m, cc_day)
                self._add_event(dd, self._ts(dd, 9, 12), "cc_repay")
            if fee_amt > 0:
                dd = prev_business_day(date(y, m, last_d))
                self._add_event(dd, self._ts(dd, 2, 4), "fee", amount=to_cents(fee_amt))
            ld = date(y, m, last_d)
            self._add_event(ld, self._ts(ld, 23, 23.9), "month_end")
            self._add_event(first, self._ts(first, 3, 4), "adjust")
            if self.stress > 0.65 and rng.random() < 0.4:
                d1 = day_in_month(y, m, int(rng.integers(18, 27)))
                amt = float(round(rng.uniform(100, 450), -1))
                self._add_event(d1, self._ts(d1, 10, 20), "micro_in", amount=to_cents(amt))
                d2 = add_months(d1, 1).replace(day=10)
                self._add_event(d2, self._ts(d2, 10, 20), "micro_out", amount=to_cents(amt * 1.18))
            if self.fx_acct is not None and rng.random() < 0.18:
                dd = day_in_month(y, m, int(rng.integers(2, last_d)))
                self._add_event(dd, self._ts(dd, 10, 17), "fx_buy",
                                amount=to_cents(self.income * rng.uniform(0.05, 0.2)))
            if self.fx_acct is not None and c.remit_month_cents > 0:
                dd = day_in_month(y, m, min(last_d, 26))
                self._add_event(dd, self._ts(dd, 10, 17), "fx_sell")
        for acct in self.savings:
            self._savings_events(acct, self.dep_by_acct[acct.id])
        if self.loan is not None:
            self._loan_events(self.loan, self.loan_visible)

    def _savings_events(self, acct: Account, dep: Deposit) -> None:
        src = self.cur[acct.bank]
        if self.ws <= dep.start <= self.as_of:
            self._add_event(dep.start, self._ts(dep.start, 10, 16), "dep_open", src_id=src.id,
                            dst_id=acct.id, amount=dep.principal)
        if dep.kind != "term":
            return  # flex savings earn interest via the month_end event
        n = 1
        while True:
            d = add_months(dep.start, n)
            n += 1
            if d > self.as_of or (dep.maturity and d > dep.maturity):
                break
            matured = bool(dep.maturity and d >= dep.maturity)
            if d >= self.ws:
                self._add_event(d, self._ts(d, 2, 4), "dep_close" if matured else "dep_interest",
                                src_id=src.id, dst_id=acct.id)
            if matured:
                break

    def _loan_events(self, loan: Loan, visible: bool) -> None:
        funding = self.w.accounts[loan.funding_account_id]
        ext_lender = self._rand_bank()
        ext_iban = self._ext_iban(ext_lender)
        lender = funding.bank if visible else ext_lender
        if loan.start >= self.ws:
            d0 = loan.start
            self._add_event(d0, self._ts(d0, 10, 12), "disburse", acct_id=funding.id,
                            amount=loan.principal, lender=lender)
            d1 = d0 + timedelta(days=1)
            self._add_event(d1, self._ts(d1, 10, 16), "disb_out", acct_id=funding.id,
                            amount=int(loan.principal * 0.92),
                            mortgage=loan.product_key.startswith("mortgage"))
        for i in loan.schedule:
            if i.paid_on is None or i.paid_on < self.ws or i.paid_on > self.as_of:
                continue
            if funding.id != self.primary.id and visible:
                dd = prev_business_day(i.paid_on - timedelta(days=1))
                self._add_event(dd, self._ts(dd, 9, 12), "loan_topup", src_id=self.primary.id,
                                dst_id=funding.id, amount=i.total + i.penalty)
            self._add_event(i.paid_on, self._ts(i.paid_on, 7, 9), "loan_pay", acct_id=funding.id,
                            amount=i.total, penalty=i.penalty, n=i.n, lender=lender,
                            lender_iban=ext_iban, visible=visible,
                            lag=(i.paid_on - i.due).days, loan_ref=loan.id[:8].upper())

    # ------------------------------------------------------------------ stochastic spend
    def _draw_stochastic(self) -> None:
        rng = self.rng
        ndays = (self.as_of - self.ws).days + 1
        days = [self.ws + timedelta(days=i) for i in range(ndays)]
        sal = self.c.salary_day
        phase = np.array([1.2 if 0 <= (d.day - sal) % 30 < 7
                          else (0.85 if (d.day - sal) % 30 > 20 else 1.0) for d in days])
        ef = (self.income / 2700.0) ** 0.12
        for key, cat in CATEGORIES.items():
            if not self.uses[key]:
                continue
            wd = WEEKDAY.get(key, [1, 1, 1, 1, 1.1, 1.1, 0.9])
            lam_d = np.array([wd[d.weekday()] for d in days]) * phase * (self._lam(cat) * ef / 30.0)
            counts = rng.poisson(lam_d)
            lo, hi = (12, 23.5) if key == "restaurant" else (7, 22)
            for i in np.nonzero(counts)[0]:
                d = days[int(i)]
                for _ in range(int(counts[i])):
                    self._add_event(d, self._ts(d, lo, hi), "spend", store="stoch", cat=key)

    # ------------------------------------------------------------------ daily loop
    def _run_day(self, d: date) -> None:
        res = 0  # liquidity committed to the next few days' debits on the primary account
        for k in range(6):
            for _, _, kind, p in self.sched.get(d + timedelta(days=k), []):
                if kind in ("rent", "bill") and p.get("acct_id") == self.primary.id or kind == "loan_topup" and p["src_id"] == self.primary.id:
                    res += p["amount"]
                elif kind == "loan_pay" and p["acct_id"] == self.primary.id:
                    res += p["amount"] + p["penalty"]
        self.reserve = res
        evs = self.sched.pop(d, []) + self.stoch.pop(d, [])
        evs.sort(key=lambda e: (e[0], e[1]))
        for ts, _, kind, p in evs:
            if d == self.as_of and ts > self.now:
                continue
            getattr(self, f"_ev_{kind}")(d, ts, p)

    # ---- credits
    def _ev_salary(self, d: date, ts: datetime, p: dict) -> None:
        ka, en = self._narr("salary", p["mon"])
        b = self._rand_bank()
        self.post(self.primary, d, ts, p["amount"], "salary", narrative_ka=ka, narrative_en=en,
                  cp_name=self.c.employer, cp_iban=self._ext_iban(b), cp_bic=BANKS[b].bic,
                  channel="TRANSFER", cp_country="GE")

    def _settle(self, d: date, ts: datetime, cp_iban: str | None) -> tuple[date, datetime]:
        """Booking date/time of an incoming transfer. Interbank GEL credits are settled on business
        days only (RTGS/clearing), so one sent on a weekend or public holiday lands on the next
        business day; credits from the same bank post at once."""
        if is_business_day(d) or (cp_iban and cp_iban[4:6] == BANKS[self.primary.bank].iban_code):
            return d, ts
        book = next_business_day(d, 1)
        return book, datetime.combine(book, ts.time())

    def _ev_biz_in(self, d: date, ts: datetime, p: dict) -> None:
        n_en, n_ka, iban, bic, pid = self._person()
        ka, en = self._narr("business_income")
        d, ts = self._settle(d, ts, iban)
        self.post(self.primary, d, ts, p["amount"], "business_income", narrative_ka=ka,
                  narrative_en=en, cp_name=n_en, cp_name_ka=n_ka, cp_iban=iban, cp_bic=bic,
                  cp_country="GE", cp_person_id=pid, channel="TRANSFER")

    def _ev_remit(self, d: date, ts: datetime, p: dict) -> None:
        country, ccy, _, providers = self.corridor
        provider = providers[int(self.rng.integers(len(providers)))]
        ka, en = self._narr("remittance")
        fx = self.fx_acct
        if fx is not None and fx.currency == ccy and d >= fx.opened:
            amt = to_cents(p["amount"] / 100.0 / self.w.fx.mid(ccy, d))
            self.post(fx, d, ts, amt, "remittance", narrative_ka=ka, narrative_en=en,
                      cp_name=provider, cp_country=country, channel="TRANSFER")
        else:
            rate = self.w.fx.mid(ccy, d) * (1 - FX_SPREAD)
            self.post(self.primary, d, ts, p["amount"], "remittance", narrative_ka=ka,
                      narrative_en=en, cp_name=provider, cp_country=country, channel="TRANSFER",
                      fx_src_ccy=ccy, fx_rate=round(rate, 4),
                      fx_src_amount=to_cents(p["amount"] / 100.0 / rate))

    def _ev_p2p_in(self, d: date, ts: datetime, p: dict) -> None:
        n_en, n_ka, iban, bic, pid = self._person()
        ka, en = self._narr("p2p_in")
        d, ts = self._settle(d, ts, iban)
        self.post(self.primary, d, ts, p["amount"], "p2p_in", narrative_ka=ka, narrative_en=en,
                  cp_name=n_en, cp_name_ka=n_ka, cp_iban=iban, cp_bic=bic, cp_country="GE",
                  cp_person_id=pid, channel="TRANSFER")

    def _ev_cash_dep(self, d: date, ts: datetime, p: dict) -> None:
        ka, en = self._narr("cash_dep")
        self.post(self.primary, d, ts, p["amount"], "cash_deposit", narrative_ka=ka,
                  narrative_en=en, channel="ATM", cp_name=BANKS[self.primary.bank].name_en)

    def _ev_micro_in(self, d: date, ts: datetime, p: dict) -> None:
        self.post(self.primary, d, ts, p["amount"], "microloan_in", narrative_ka="სწრაფი სესხი",
                  narrative_en="Quick loan disbursement", cp_name="ONLINE LENDER LLC",
                  cp_iban=self._ext_iban(self._rand_bank()), channel="TRANSFER", cp_country="GE")

    # ---- debits (non-discretionary)
    def _ev_rent(self, d: date, ts: datetime, p: dict) -> None:
        n_en, n_ka, iban, bic, pid = self._person()
        ka, en = self._narr("rent", p["mon"])
        self.post(self.primary, d, ts, -p["amount"], "rent", narrative_ka=ka, narrative_en=en,
                  cp_name=n_en, cp_name_ka=n_ka, cp_iban=iban, cp_bic=bic, cp_country="GE",
                  cp_person_id=pid, channel="TRANSFER")
        self._xfer_fee(self.primary, d, ts, p["amount"], iban)

    def _ev_bill(self, d: date, ts: datetime, p: dict) -> None:
        ka = f"{p['nka']} - {self._month_name(d, True)}"
        en = f"{p['nen']} - {self._month_name(d, False)}"
        self.post(self.primary, d, ts, -p["amount"], "utility", narrative_ka=ka, narrative_en=en,
                  cp_name=p["biller"], mcc=p["mcc"], merchant=p["biller"], channel="TRANSFER",
                  cp_country="GE")

    def _ev_tax(self, d: date, ts: datetime, p: dict) -> None:
        ka, en = self._narr("tax")
        tail = "".join(str(int(x)) for x in self.rng.integers(0, 10, 4))
        self.post(self.primary, d, ts, -p["amount"], "tax", narrative_ka=ka, narrative_en=en,
                  cp_name="TREASURY SERVICE", cp_iban=make_iban(f"NB0330100200{tail}0000"),
                  channel="TRANSFER", cp_country="GE")

    def _ev_p2p_out(self, d: date, ts: datetime, p: dict) -> None:
        a = self.primary
        if self.avail[a.id] - self.reserve + a.credit_limit < p["amount"]:
            return
        n_en, n_ka, iban, bic, pid = self._person()
        ka, en = self._narr("p2p_out")
        self.post(a, d, ts, -p["amount"], "p2p_out", narrative_ka=ka, narrative_en=en,
                  cp_name=n_en, cp_name_ka=n_ka, cp_iban=iban, cp_bic=bic, cp_country="GE",
                  cp_person_id=pid, channel="TRANSFER")
        self._xfer_fee(a, d, ts, p["amount"], iban)

    def _xfer_fee(self, acct: Account, d: date, ts: datetime, amount: int, cp_iban: str | None) -> None:
        """Fee for a GEL transfer to another bank; transfers inside the same bank are free."""
        if not cp_iban or cp_iban[4:6] == BANKS[acct.bank].iban_code:
            return
        fee = next(f for cap, f in XFER_FEE_TIERS if amount / 100.0 <= cap)
        ka, en = self._narr("xfer_fee")
        self.post(acct, d, ts + timedelta(seconds=1), -to_cents(fee), "fee", narrative_ka=ka,
                  narrative_en=en, channel="FEE", cp_name=BANKS[acct.bank].name_en)

    def _ev_micro_out(self, d: date, ts: datetime, p: dict) -> None:
        self.post(self.primary, d, ts, -p["amount"], "microloan_out",
                  narrative_ka="სწრაფი სესხის დაფარვა", narrative_en="Quick loan repayment",
                  cp_name="ONLINE LENDER LLC", cp_iban=self._ext_iban(self._rand_bank()),
                  channel="TRANSFER", cp_country="GE")

    def _ev_fee(self, d: date, ts: datetime, p: dict) -> None:
        ka, en = self._narr("fee")
        self.post(self.primary, d, ts, -p["amount"], "fee", narrative_ka=ka, narrative_en=en,
                  channel="FEE", cp_name=BANKS[self.primary.bank].name_en)

    def _ev_adjust(self, d: date, ts: datetime, p: dict) -> None:
        """Behavioural feedback: people spend more when flush and less when short.

        The target buffer shrinks with latent stress, so stressed customers run lean accounts
        (and low-stress customers keep roughly one to one-and-a-half months of income).
        """
        target = self.income * 100 * (0.25 + 1.2 * (1.0 - self.stress) ** 2)
        err = (self.avail[self.primary.id] - target) / (self.income * 100 + 1.0)
        self.spend_scale = float(min(4.0, max(0.15, self.spend_scale
                                              * min(1.6, max(0.5, 1.0 + 0.25 * err)))))

    def _ev_month_end(self, d: date, ts: datetime, p: dict) -> None:
        a = self.primary
        bal = self.avail[a.id]
        if bal < 0:
            fee = int(-bal * 0.02)
            if fee > 0:
                ka, en = self._narr("overdraft_fee")
                self.post(a, d, ts, -fee, "overdraft_fee", narrative_ka=ka, narrative_en=en,
                          channel="FEE", cp_name=BANKS[a.bank].name_en)
        for s in self.savings:
            if s.kind == "savings_flex" and self.avail[s.id] > 0 and d >= s.opened:
                interest = int(self.avail[s.id] * self.dep_by_acct[s.id].rate / 12)
                if interest > 0:
                    ka, en = self._narr("interest")
                    self.post(s, d, ts, interest, "interest", narrative_ka=ka, narrative_en=en,
                              channel="INTEREST", cp_name=BANKS[s.bank].name_en)

    # ---- transfers between own accounts / FX
    def _own_transfer(self, src: Account, dst: Account, d: date, ts: datetime, amount: int,
                      k_out: str, k_in: str, nkey: str = "own") -> None:
        ka, en = self._narr(nkey)
        e2e = self._e2e()
        self.post(src, d, ts, -amount, k_out, narrative_ka=ka, narrative_en=en,
                  cp_name=self._owner(BANKS[dst.bank]), cp_iban=dst.iban,
                  cp_bic=BANKS[dst.bank].bic, channel="TRANSFER", cp_country="GE", e2e=e2e)
        self.post(dst, d, ts, amount, k_in, narrative_ka=ka, narrative_en=en,
                  cp_name=self._owner(BANKS[src.bank]), cp_iban=src.iban,
                  cp_bic=BANKS[src.bank].bic, channel="TRANSFER", cp_country="GE", e2e=e2e)

    def _ev_loan_topup(self, d: date, ts: datetime, p: dict) -> None:
        src, dst = self.w.accounts[p["src_id"]], self.w.accounts[p["dst_id"]]
        if d >= dst.opened and d >= src.opened:
            self._own_transfer(src, dst, d, ts, p["amount"], "own_transfer_out", "own_transfer_in")

    def _ev_loan_pay(self, d: date, ts: datetime, p: dict) -> None:
        acct = self.w.accounts[p["acct_id"]]
        if d < acct.opened:
            return
        ka, en = self._narr("loan")
        prof = BANKS.get(p["lender"])
        lname = prof.name_en if prof else "LENDER"
        self.post(acct, d, ts, -p["amount"], "loan_payment",
                  narrative_ka=f"{ka} {p['loan_ref']}", narrative_en=f"{en} {p['loan_ref']}",
                  cp_name=lname, cp_iban=None if p["visible"] else p["lender_iban"],
                  cp_bic=prof.bic if prof else None, channel="TRANSFER", cp_country="GE")
        if p["penalty"] > 0:
            kap, enp = self._narr("loan_penalty")
            self.post(acct, d, ts + timedelta(seconds=30), -p["penalty"], "loan_penalty",
                      narrative_ka=kap, narrative_en=enp, cp_name=lname, channel="FEE",
                      cp_country="GE")

    def _ev_disburse(self, d: date, ts: datetime, p: dict) -> None:
        acct = self.w.accounts[p["acct_id"]]
        if d < acct.opened:
            return
        prof = BANKS.get(p["lender"])
        self.post(acct, d, ts, p["amount"], "loan_disbursement", narrative_ka="სესხის გაცემა",
                  narrative_en="Loan disbursement", cp_name=prof.name_en if prof else "LENDER",
                  channel="TRANSFER", cp_country="GE")

    def _ev_disb_out(self, d: date, ts: datetime, p: dict) -> None:
        acct = self.w.accounts[p["acct_id"]]
        if d < acct.opened:
            return
        n_en, n_ka, iban, bic, pid = self._person()
        en, ka = ("Property purchase", "უძრავი ქონების შეძენა") if p["mortgage"] else \
            ("Payment under contract", "გადახდა ხელშეკრულებით")
        self.post(acct, d, ts, -p["amount"], "disbursement_out", narrative_ka=ka, narrative_en=en,
                  cp_name=n_en, cp_name_ka=n_ka, cp_iban=iban, cp_bic=bic, cp_country="GE",
                  cp_person_id=pid, channel="TRANSFER")

    def _ev_cc_repay(self, d: date, ts: datetime, p: dict) -> None:
        cc = self.credit
        if cc is None or d < cc.opened:
            return
        owed = -self.avail[cc.id]
        if owed <= 0:
            return
        full = self.rng.random() < 0.6 * (1 - self.stress) + 0.1
        pay = owed if full else max(min(owed, 2000), int(owed * 0.08))
        src = self.primary
        pay = max(0, min(pay, self.avail[src.id] + src.credit_limit))
        if pay <= 0:
            return
        self._own_transfer(src, cc, d, ts, pay, "cc_repay_out", "cc_repay_in", "cc_repay")
        rest = owed - pay
        if rest > 0:
            self.post(cc, d, ts + timedelta(minutes=1), -int(rest * 0.025), "cc_interest",
                      narrative_ka="საპროცენტო განაკვეთი", narrative_en="Interest charge",
                      channel="INTEREST", cp_name=BANKS[cc.bank].name_en)

    def _convert(self, fx: Account, d: date, ts: datetime, amount_gel: int | None,
                 amount_fx: int | None, sell: bool) -> None:
        """sell=True: customer sells FX for GEL. sell=False: customer buys FX with GEL."""
        gel = self.cur[fx.bank]
        mid = self.w.fx.mid(fx.currency, d)
        rate = mid * (1 - FX_SPREAD) if sell else mid * (1 + FX_SPREAD)
        if sell:
            fx_amt = amount_fx or 0
            gel_amt = to_cents(fx_amt / 100.0 * rate)
        else:
            gel_amt = amount_gel or 0
            fx_amt = to_cents(gel_amt / 100.0 / rate)
        if fx_amt <= 0 or gel_amt <= 0:
            return
        ka, en = self._narr("fx_out")
        e2e = self._e2e()
        sgn = -1 if sell else 1
        self.post(fx, d, ts, sgn * fx_amt, "fx_sell" if sell else "fx_buy", narrative_ka=ka,
                  narrative_en=en, channel="INTERNAL", cp_name=self._owner(BANKS[fx.bank]),
                  fx_src_ccy="GEL", fx_rate=round(rate, 4), fx_src_amount=gel_amt, e2e=e2e)
        self.post(gel, d, ts, -sgn * gel_amt, "fx_buy_gel" if sell else "fx_sell_gel",
                  narrative_ka=ka, narrative_en=en, channel="INTERNAL",
                  cp_name=self._owner(BANKS[fx.bank]), fx_src_ccy=fx.currency,
                  fx_rate=round(rate, 4), fx_src_amount=fx_amt, e2e=e2e)

    def _e2e(self) -> str:
        """EndToEndId: opaque, so it does not reveal this customer's event counter across banks."""
        seq = self.w.next_seq()
        return "E2E" + hashlib.sha1(f"{self.c.cid}:{seq}".encode()).hexdigest()[:12].upper()

    def _ev_fx_sell(self, d: date, ts: datetime, p: dict) -> None:
        fx = self.fx_acct
        if fx is None or d < fx.opened:
            return
        bal = self.avail[fx.id]
        if bal > 5000:
            self._convert(fx, d, ts, None, int(bal * self.rng.uniform(0.6, 1.0)), sell=True)

    def _ev_fx_buy(self, d: date, ts: datetime, p: dict) -> None:
        fx = self.fx_acct
        if fx is None or d < fx.opened:
            return
        gel = self.cur[fx.bank]
        if self.avail[gel.id] - self.reserve > p["amount"] * 1.3:
            self._convert(fx, d, ts, p["amount"], None, sell=False)

    # ---- savings
    def _ev_dep_open(self, d: date, ts: datetime, p: dict) -> None:
        src, dst = self.w.accounts[p["src_id"]], self.w.accounts[p["dst_id"]]
        gel_amt = p["amount"] if dst.currency == "GEL" else \
            to_cents(p["amount"] / 100.0 * self.w.fx.mid(dst.currency, d) * (1 + FX_SPREAD))
        ka, en = self._narr("deposit_open")
        e2e = self._e2e()
        extra: dict[str, Any] = {}
        if dst.currency != "GEL":
            extra = dict(fx_src_ccy="GEL", fx_src_amount=gel_amt,
                         fx_rate=round(gel_amt / max(1, p["amount"]), 4))
        short = gel_amt - (self.avail[src.id] - self.reserve)
        if short > 0:  # customer tops up the account from savings elsewhere before depositing
            self.post(src, d, ts - timedelta(minutes=5), short + 1000, "p2p_in",
                      narrative_ka="ანაბრისთვის თანხის შემოტანა", narrative_en="Funds for deposit",
                      channel="TRANSFER", cp_name=self.c.name_en, cp_country="GE")
        self.post(src, d, ts, -gel_amt, "deposit_open", narrative_ka=ka, narrative_en=en,
                  channel="INTERNAL", cp_name=BANKS[dst.bank].name_en, cp_iban=dst.iban, e2e=e2e)
        self.post(dst, d, ts, p["amount"], "deposit_open_in", narrative_ka=ka, narrative_en=en,
                  channel="INTERNAL", cp_name=self._owner(BANKS[src.bank]), cp_iban=src.iban,
                  e2e=e2e, **extra)

    def _ev_dep_interest(self, d: date, ts: datetime, p: dict) -> None:
        dst = self.w.accounts[p["dst_id"]]
        dep = self.dep_by_acct[dst.id]
        interest = int(self.avail[dst.id] * dep.rate / 12)
        if interest > 0 and d >= dst.opened:
            ka, en = self._narr("interest")
            self.post(dst, d, ts, interest, "interest", narrative_ka=ka, narrative_en=en,
                      channel="INTEREST", cp_name=BANKS[dst.bank].name_en)
            dep.interest_paid += interest

    def _ev_dep_close(self, d: date, ts: datetime, p: dict) -> None:
        dst, src = self.w.accounts[p["dst_id"]], self.w.accounts[p["src_id"]]
        dep = self.dep_by_acct[dst.id]
        bal = self.avail[dst.id]
        interest = int(bal * dep.rate / 12)
        ka, en = self._narr("interest")
        if interest > 0:
            self.post(dst, d, ts, interest, "interest", narrative_ka=ka, narrative_en=en,
                      channel="INTEREST", cp_name=BANKS[dst.bank].name_en)
            bal += interest
        kc, ec = self._narr("deposit_close")
        e2e = self._e2e()
        gel = bal if dst.currency == "GEL" else \
            to_cents(bal / 100.0 * self.w.fx.mid(dst.currency, d) * (1 - FX_SPREAD))
        extra: dict[str, Any] = {}
        if dst.currency != "GEL":
            extra = dict(fx_src_ccy=dst.currency, fx_src_amount=bal,
                         fx_rate=round(gel / max(1, bal), 4))
        later = ts + timedelta(minutes=1)
        self.post(dst, d, later, -bal, "deposit_close", narrative_ka=kc, narrative_en=ec,
                  channel="INTERNAL", cp_name=self._owner(BANKS[src.bank]), cp_iban=src.iban,
                  e2e=e2e)
        self.post(src, d, later, gel, "deposit_close_in", narrative_ka=kc, narrative_en=ec,
                  channel="INTERNAL", cp_name=BANKS[dst.bank].name_en, cp_iban=dst.iban,
                  e2e=e2e, **extra)
        dst.status = "deleted"
        dep.closed = True

    def _ev_sav_check(self, d: date, ts: datetime, p: dict) -> None:
        inc = int(self.income * 100)
        for s in self.savings:
            if s.kind != "savings_flex" or d < s.opened or s.currency != "GEL":
                continue
            src = self.cur[s.bank]
            bal = self.avail[src.id]
            if bal > 1.8 * inc:
                move = int((bal - 1.5 * inc) * 0.5 / 5000) * 5000
                if move > 0:
                    self._own_transfer(src, s, d, ts, move, "own_transfer_out", "own_transfer_in")
            elif bal < 0.12 * inc and self.avail[s.id] > 0:
                move = min(self.avail[s.id], int(0.3 * inc))
                if move > 0:
                    self._own_transfer(s, src, d, ts, move, "own_transfer_out", "own_transfer_in")

    # ---- stochastic card spend
    def _ev_spend(self, d: date, ts: datetime, p: dict) -> None:
        rng = self.rng
        cat = CATEGORIES[p["cat"]]
        prim = self.primary
        liquid = self.avail[prim.id] - self.reserve + prim.credit_limit
        if rng.random() > min(1.0, max(0.15, liquid / (0.25 * self.income * 100 + 1))):
            return  # people spend less when the account runs low
        amt_gel = (cat.median * (self.income / 2700.0) ** cat.income_elasticity
                   * self.spend_scale * rng.lognormal(0.0, cat.sigma))
        if cat.key == "atm_cash":
            amt_gel = max(20.0, round(amt_gel / 10) * 10)
        elif cat.key == "fuel":
            amt_gel = round(amt_gel / 5) * 5 or 20
        amt = to_cents(max(1.0, amt_gel))
        idx = min(int(rng.geometric(0.45)) - 1, len(cat.merchants) - 1)
        merchant = cat.merchants[0 if cat.key == "atm_cash" else idx]
        acct = prim
        cc = self.credit
        if (cc and d >= cc.opened and cat.key in CC_CATEGORIES and rng.random() < 0.5
                and cc.credit_limit + self.avail[cc.id] >= amt):
            acct = cc
        else:
            sec = [a for a in self.cur.values() if a.id != prim.id and d >= a.opened]
            if sec and rng.random() < 0.25:
                acct = sec[0]
        if acct.kind == "current":
            floor = self.reserve if acct is prim else 0
            if self.avail[acct.id] - floor + acct.credit_limit < amt:
                return
        if d < acct.opened:
            return
        foreign = FOREIGN_MERCHANT_CCY.get(merchant)
        fx_fields: dict[str, Any] = {}
        if foreign:
            rate = self.w.fx.mid(foreign, d) * (1 + FX_SPREAD)
            fx_fields = dict(fx_src_ccy=foreign, fx_rate=round(rate, 4),
                             fx_src_amount=to_cents(amt / 100.0 / rate))
        if cat.channel == "ATM":
            book = d if acct.bank == prim.bank else next_business_day(d, 1)
            ka, en = self._narr("atm")
            label = f"ATM {BANKS[self._rand_bank()].name_en.upper()}"
            self.post(acct, book, ts, -amt, "atm_cash", d_value=d, narrative_ka=ka, narrative_en=en,
                      merchant=label, mcc=cat.mcc, city=self.c.city, channel="ATM",
                      pan_masked=acct.pan_masked, cp_name=label, cp_country="GE")
            return
        book = next_business_day(d, 1 if rng.random() < 0.7 else 2)
        city = None if cat.channel == "ECOM" else (self.c.city if rng.random() < 0.7 else "Tbilisi")
        ka, en = self._narr("card")
        self.post(acct, book, ts, -amt, p["cat"], d_value=d, narrative_ka=f"{ka} - {merchant}",
                  narrative_en=f"{en} - {merchant}", merchant=merchant, mcc=cat.mcc, city=city,
                  channel=cat.channel, pan_masked=acct.pan_masked, cp_name=merchant,
                  cp_country="GE" if not foreign else "US", **fx_fields)


# ============================================================================== entry point
@dataclass(frozen=True)
class CustPlan:
    """Everything that is decided about one customer before simulation starts."""

    n: int  # global 0-based ordinal (a seeded draw from the df, not the df row number)
    row: dict[str, Any]  # the df row the customer is bootstrapped from (hidden truth)
    stress: float
    banks: tuple[str, ...]  # first = primary
    pid: str


def plan_population(cfg: WorldConfig) -> list[CustPlan]:
    """Deterministic, serial and cheap: who the customers are and where they bank."""
    frame = LoanFrame.load(cfg.data_dir)
    idx = frame.sample_indices(np.random.default_rng(cfg.seed), cfg.n_customers)
    rows, stress = frame.rows(idx), frame.stresses(idx)
    banks = _plan_banks(cfg, np.random.default_rng([cfg.seed, 1]))
    pids = _plan_pids(cfg.n_customers, np.random.default_rng([cfg.seed, 2]))
    return [CustPlan(n, rows[n], float(stress[n]), tuple(banks[n]), pids[n])
            for n in range(cfg.n_customers)]


def build_shard(cfg: WorldConfig, plans: list[CustPlan], fx: Fx) -> World:
    """Simulate `plans` (any subset of the population) into a self-contained World.

    A customer's result depends only on (cfg, its plan, fx), never on which other customers share the
    shard, so shards can run in parallel processes and be written independently.
    """
    world = World(cfg)
    world.fx = fx
    for p in plans:
        crng = np.random.default_rng([cfg.seed, 100 + p.n])
        cust = _make_customer(p.n, p.row, p.stress, list(p.banks), p.pid, crng, cfg)
        world.add_customer(cust)
        world.begin_customer(p.n)
        _CustomerSim(world, cust, p.row, crng).run()
    # Blocks are numbered per (customer, bank): a block number must not reveal how many other banks
    # the customer uses (it is not visible in any single bank's API). Numbers stay unique per bank.
    slots: dict[tuple[str, str], int] = {}
    for a in world.accounts.values():
        j = slots.get((a.cid, a.bank), 0)
        slots[(a.cid, a.bank)] = j + 1
        assert j < REF_STRIDE // REF_BLOCK, f"{a.cid} has too many accounts"
        a.finalize(world.customers[a.cid].index * REF_STRIDE + j * REF_BLOCK + 1)
    for loan in world.loans.values():
        loan.refresh()
    return world


def build_world(cfg: WorldConfig | None = None) -> World:
    """The whole population in one in-memory World (tests, `serve`, small runs)."""
    cfg = cfg or WorldConfig()
    return build_shard(cfg, plan_population(cfg), make_fx(cfg))
