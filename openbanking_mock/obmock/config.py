"""Paths and tunable defaults for the Georgian Open Banking mock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent  # openbanking_mock/
PROJECT_ROOT = ROOT.parent  # hakathon/
SPEC_DIR = ROOT / "specs"
FIXTURE_DIR = ROOT / "fixtures"
OUTPUT_DIR = ROOT / "output"
DEFAULT_DATA_DIR = PROJECT_ROOT / "nbg_synthetic_data"

# Fixed so that two runs produce the same dataset; override with --as-of.
DEFAULT_AS_OF = date(2026, 10, 19)
DEFAULT_SEED = 20261020


@dataclass(frozen=True)
class WorldConfig:
    """Everything that determines the simulated world (same config => identical world)."""

    seed: int = DEFAULT_SEED
    n_customers: int = 240
    months: int = 12
    as_of: date = DEFAULT_AS_OF
    data_dir: Path = DEFAULT_DATA_DIR
    # Memberships guaranteed per bank so tiny worlds still give every mock some data. Primary-bank
    # shares follow BankProfile.weight exactly (quota), so this floor must stay small.
    min_customers_per_bank: int = 3
    page_size: int = 50
    # spec-complete: fill the standard-coded fields a bank's schema defines (ISO 20022
    # bankTransactionCode/purposeCode, mandateId, balanceAfterTransaction). evidence-only: leave exactly
    # those four empty, because no TBC/BoG example shows them; everything else is unchanged.
    profile: str = "spec-complete"
