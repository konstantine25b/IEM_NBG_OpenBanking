"""Domain model of the simulated world: customers, accounts, ledgers, loans, deposits, cards.

All money is integer minor units (tetri/cents). A ledger is the single source of truth: every
API response, every dataset dump and every consistency check is derived from these objects,
so what one bank dialect shows can never contradict another dialect's view of the same data.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from obmock.config import WorldConfig
from obmock.util import add_months

# Entry numbers: customer n owns [n * REF_STRIDE, (n + 1) * REF_STRIDE); each of its accounts gets a
# block of REF_BLOCK numbers (an executed payment appends at max+1 inside the block). 11 digits
# hold 250,000 customers.
REF_BLOCK = 20_000
REF_STRIDE = 400_000
SEQ_STRIDE = 100_000  # simulator sequence numbers per customer (Txn.seq, E2E ids)


@dataclass
class Customer:
    cid: str
    person_id: str  # 11 digit Georgian personal number
    first_ka: str
    last_ka: str
    first_en: str
    last_en: str
    sex: int  # df SEX code (ASSUMED 1=male, 2=female)
    birth_date: date
    msisdn: str
    region_code: int
    region_name: str
    city: str
    segment: str  # PRIV | ORGA
    income_month_cents: int
    remit_month_cents: int
    employer: str
    sector: int
    salary_day: int
    stress: float  # latent 0..1; drives spend, buffers, lateness
    banks: list[str] = field(default_factory=list)  # first = primary
    truth: dict[str, Any] = field(default_factory=dict)  # hidden labels / source df row
    index: int = 0  # global 0-based ordinal (not the df row number); numbers entries and ids

    @property
    def name_en(self) -> str:
        return f"{self.first_en} {self.last_en}"

    @property
    def name_ka(self) -> str:
        return f"{self.first_ka} {self.last_ka}"


@dataclass(slots=True)
class Txn:
    seq: int
    account_id: str
    booking_date: date
    value_date: date
    ts: datetime  # intra-day ordering and bookingDateTime
    amount: int  # signed minor units in account currency
    currency: str
    kind: str  # salary, grocery, loan_payment, own_transfer_out ...
    status: str = "booked"  # booked | pending
    narrative_ka: str = ""
    narrative_en: str = ""
    cp_name: str | None = None  # Latin
    cp_name_ka: str | None = None  # Georgian script (persons only)
    cp_iban: str | None = None
    cp_bic: str | None = None
    cp_country: str | None = None
    cp_person_id: str | None = None
    merchant: str | None = None
    mcc: str | None = None
    city: str | None = None
    channel: str = "TRANSFER"  # POS | ECOM | ATM | TRANSFER | FEE | INTEREST | INTERNAL
    pan_masked: str | None = None
    e2e: str | None = None
    fx_src_ccy: str | None = None
    fx_rate: float | None = None
    fx_src_amount: int | None = None
    balance_after: int | None = None
    ref: str = ""  # entryReference / bank reference


@dataclass
class Account:
    id: str
    bank: str
    cid: str
    kind: str  # current | fx | savings_term | savings_flex | card_credit | loan | overdraft
    currency: str
    iban: str | None
    product: str
    name: str
    opened: date
    status: str = "enabled"
    usage: str = "PRIV"
    credit_limit: int = 0  # overdraft or credit-card limit, minor units
    opening_balance: int = 0  # balance at the start of the simulated window
    pan_masked: str | None = None
    scheme: str | None = None
    card_expiry: str | None = None
    txns: list[Txn] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def finalize(self, first_ref: int = 1) -> None:
        """Sort the ledger, number the entries and compute running balances for booked entries.

        `first_ref` is the entry number of the first entry; the caller gives every account of a
        customer its own block so that numbers (and the transactionIds built from them) are unique
        bank-wide and monotonic in report order (entryReferenceFrom paging).
        """
        self.txns.sort(key=lambda t: (t.booking_date, t.ts, t.seq))
        if len(self.txns) >= REF_BLOCK:
            raise ValueError(f"account {self.id} has {len(self.txns)} entries; raise REF_BLOCK")
        bal = self.opening_balance
        for i, t in enumerate(self.txns):
            t.ref = f"{first_ref + i:011d}"
            if t.status == "booked":
                bal += t.amount
                t.balance_after = bal


@dataclass
class Instalment:
    n: int
    due: date
    principal: int
    interest: int
    paid_on: date | None = None
    paid_amount: int = 0
    penalty: int = 0

    @property
    def total(self) -> int:
        return self.principal + self.interest


@dataclass
class Loan:
    id: str
    bank: str
    cid: str
    account_id: str  # the loan "account" resource id
    funding_account_id: str  # current account that is debited
    product_key: str  # consumer | instalment | mortgage | mortgage_construction | mortgage_renovation
    purpose_code: int  # df PRODUCT_PURPOSE
    currency: str
    principal: int
    rate: float  # nominal annual
    rate_type: str  # Fixed | Variable
    term_months: int
    start: date
    maturity: date
    pay_day: int
    instalment: int
    schedule: list[Instalment]
    as_of: date
    collateral: bool = False
    guarantee: bool = False
    restructured: bool = False
    refinanced: bool = False

    # ---- derived state at as_of (computed by refresh)
    outstanding_principal: int = 0
    overdue_principal: int = 0
    overdue_interest: int = 0
    overdue_count: int = 0
    days_in_delinquency: int = 0
    next_due: date | None = None
    next_amount: int = 0

    def refresh(self) -> None:
        paid_principal = 0
        od_p = od_i = od_n = 0
        oldest: date | None = None
        nxt: Instalment | None = None
        for i in self.schedule:
            if i.due <= self.as_of:
                if i.paid_on is not None and i.paid_on <= self.as_of:
                    paid_principal += i.principal
                else:
                    od_p += i.principal
                    od_i += i.interest
                    od_n += 1
                    oldest = oldest or i.due
            elif nxt is None:
                nxt = i
        self.overdue_principal, self.overdue_interest, self.overdue_count = od_p, od_i, od_n
        self.outstanding_principal = self.principal - paid_principal
        self.days_in_delinquency = (self.as_of - oldest).days if oldest else 0
        self.next_due = nxt.due if nxt else None
        self.next_amount = nxt.total if nxt else 0


@dataclass
class Deposit:
    id: str
    bank: str
    cid: str
    account_id: str
    kind: str  # term | flex
    currency: str
    principal: int
    rate: float
    term_months: int
    start: date
    maturity: date | None
    interest_paid: int = 0
    closed: bool = False


@dataclass
class Fx:
    """Daily GEL rates per currency (mid), shared by every bank."""

    rates: dict[str, dict[date, float]]

    def mid(self, ccy: str, d: date) -> float:
        if ccy == "GEL":
            return 1.0
        series = self.rates[ccy]
        first = min(series)
        while d not in series and d > first:
            d -= timedelta(days=1)
        return series.get(d, series[first])


class World:
    def __init__(self, cfg: WorldConfig):
        self.cfg = cfg
        self.customers: dict[str, Customer] = {}
        self.accounts: dict[str, Account] = {}
        self.loans: dict[str, Loan] = {}
        self.deposits: dict[str, Deposit] = {}
        self.fx: Fx | None = None
        self._by_bank_cust: dict[tuple[str, str], list[Account]] = defaultdict(list)
        self._by_bank: dict[str, list[str]] = defaultdict(list)  # bank -> cids
        self._ibans: set[str] = set()  # uniqueness guard for generated IBANs
        self._seq = 0

    # ------------------------------------------------------------------ construction
    def begin_customer(self, index: int) -> None:
        """Sequence numbers restart per customer (index * SEQ_STRIDE + k), so a customer's entries
        get the same numbers whichever shard simulates it."""
        self._seq = index * SEQ_STRIDE

    def next_seq(self) -> int:
        self._seq += 1
        if self._seq % SEQ_STRIDE == 0:
            raise ValueError("customer exceeded SEQ_STRIDE entries; raise SEQ_STRIDE")
        return self._seq

    def add_customer(self, c: Customer) -> None:
        self.customers[c.cid] = c

    def add_account(self, a: Account) -> Account:
        self.accounts[a.id] = a
        self._by_bank_cust[(a.bank, a.cid)].append(a)
        if a.cid not in self._by_bank[a.bank]:
            self._by_bank[a.bank].append(a.cid)
        return a

    # ------------------------------------------------------------------ queries
    @property
    def as_of(self) -> date:
        return self.cfg.as_of

    @property
    def window_start(self) -> date:
        return add_months(self.cfg.as_of, -self.cfg.months)

    def customers_of(self, bank: str) -> list[str]:
        return list(self._by_bank.get(bank, []))

    def accounts_of(
        self, bank: str, cid: str, kinds: tuple[str, ...] | None = None
    ) -> list[Account]:
        out = self._by_bank_cust.get((bank, cid), [])
        return [a for a in out if kinds is None or a.kind in kinds]

    def loans_of(self, bank: str, cid: str) -> list[Loan]:
        return [self.loans[a.meta["loan_id"]] for a in self.accounts_of(bank, cid, ("loan",))]

    def deposit_of(self, account_id: str) -> Deposit | None:
        return next((d for d in self.deposits.values() if d.account_id == account_id), None)

    # ------------------------------------------------------------------ balances
    def booked_balance(self, a: Account, on: date | None = None) -> int:
        """Booked balance after all entries with booking_date <= on (default: as_of)."""
        on = on or self.as_of
        bal = a.opening_balance
        for t in a.txns:
            if t.status == "booked" and t.booking_date <= on:
                bal += t.amount
        return bal

    def pending_sum(self, a: Account) -> int:
        return sum(t.amount for t in a.txns if t.status == "pending")

    def balances(self, a: Account) -> dict[str, int]:
        """Berlin-Group balance types in minor units for the *current* reporting month.

        openingBooked  = booked balance at close of the day before the month started
        closingBooked  = booked balance at close of the previous day (as_of - 1)
        interimBooked  = booked balance now (incl. today)
        expected       = interimBooked + pending entries
        interimAvailable = expected + credit/overdraft limit
        Invariant: openingBooked + sum(booked in [month_start, as_of-1]) == closingBooked.
        """
        as_of = self.as_of
        month_start = as_of.replace(day=1)
        prev = as_of - timedelta(days=1)
        opening = self.booked_balance(a, month_start - timedelta(days=1))
        closing = self.booked_balance(a, prev)
        interim = self.booked_balance(a, as_of)
        expected = interim + self.pending_sum(a)
        return {
            "openingBooked": opening,
            "closingBooked": closing,
            "interimBooked": interim,
            "expected": expected,
            "interimAvailable": expected + a.credit_limit,
            "forwardAvailable": expected + a.credit_limit,
        }

    def entries(
        self, a: Account, date_from: date | None = None, date_to: date | None = None,
        status: str = "both",
    ) -> list[Txn]:
        out = []
        for t in a.txns:
            if status != "both" and t.status != status:
                continue
            if date_from and t.booking_date < date_from:
                continue
            if date_to and t.booking_date > date_to:
                continue
            out.append(t)
        return out

    def stats(self) -> dict[str, Any]:
        kinds: dict[str, int] = defaultdict(int)
        for a in self.accounts.values():
            kinds[a.kind] += 1
        return {
            "customers": len(self.customers),
            "accounts": len(self.accounts),
            "accounts_by_kind": dict(kinds),
            "transactions": sum(len(a.txns) for a in self.accounts.values()),
            "loans": len(self.loans),
            "deposits": len(self.deposits),
            "customers_by_bank": {b: len(c) for b, c in self._by_bank.items()},
        }
