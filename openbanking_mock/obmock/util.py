"""Small deterministic helpers: money, IBAN, ids, dates, pagination state."""
from __future__ import annotations

import base64
import calendar
import csv
import json
import uuid
from datetime import date, datetime, timedelta
from functools import lru_cache

from obmock.config import SPEC_DIR

_NS = uuid.UUID("5d0c3a56-6f0f-4b3e-9d57-0b5a1b7a9e11")
HOLIDAY_FILE = SPEC_DIR / "reference" / "ge_public_holidays_2025_2026.csv"


# --------------------------------------------------------------------------- money
def to_cents(x: float) -> int:
    """Round half away from zero to integer minor units (tetri / cents)."""
    return int(round(x * 100.0))


def fmt_amount(cents: int) -> str:
    """Berlin-Group style amount string: -?[0-9]{1,14}(\\.[0-9]{1,3})? with two decimals."""
    sign = "-" if cents < 0 else ""
    c = abs(int(cents))
    return f"{sign}{c // 100}.{c % 100:02d}"


# --------------------------------------------------------------------------- iban
def _iban_numeric(s: str) -> int:
    return int("".join(str(int(ch, 36)) for ch in s))


def iban_check_digits(country: str, bban: str) -> str:
    rearranged = bban + country + "00"
    return f"{98 - _iban_numeric(rearranged) % 97:02d}"


def make_iban(bban: str, country: str = "GE") -> str:
    """Georgian IBAN = GE + 2 check digits + 2 letter bank code + 16 digits (22 chars)."""
    return f"{country}{iban_check_digits(country, bban)}{bban}"


def is_valid_iban(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if len(s) < 15 or not s.isalnum():
        return False
    return _iban_numeric(s[4:] + s[:4]) % 97 == 1


def luhn_check_digit(partial: str) -> str:
    total, flip = 0, True
    for ch in reversed(partial):
        d = int(ch)
        if flip:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        flip = not flip
    return str((10 - total % 10) % 10)


def mask_pan(pan: str) -> str:
    return f"{pan[:6]}{'*' * (len(pan) - 10)}{pan[-4:]}"


# --------------------------------------------------------------------------- ids
def stable_uuid(*parts: object) -> str:
    """Deterministic UUID from arbitrary parts (same world config => same ids)."""
    return str(uuid.uuid5(_NS, "|".join(str(p) for p in parts)))


# --------------------------------------------------------------------------- dates
def last_day_of_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return date(y, m + 1, min(d.day, last_day_of_month(y, m + 1)))


def day_in_month(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, last_day_of_month(y, m)))


@lru_cache(maxsize=1)
def georgian_holidays() -> frozenset[date]:
    """Official non-working days, vendored in specs/reference (never fetched at run time).

    Empty when the file is absent, in which case only weekends are skipped.
    """
    if not HOLIDAY_FILE.exists():
        return frozenset()
    with HOLIDAY_FILE.open(encoding="utf-8", newline="") as fh:
        return frozenset(date.fromisoformat(r["date"]) for r in csv.DictReader(fh))


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in georgian_holidays()


def prev_business_day(d: date) -> date:
    """`d` itself when it is a business day, else the closest earlier one."""
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def next_business_day(d: date, n: int = 1) -> date:
    """Move forward n business days (weekends and Georgian public holidays skipped)."""
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


def iso_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def iso_dt_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- pagination
def encode_state(obj: dict) -> str:
    """AltaSoft-style opaque paging token (URL-safe base64 of compact JSON, no padding)."""
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_state(token: str) -> dict:
    pad = "=" * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(token + pad))
