import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obmock.config import WorldConfig  # noqa: E402
from obmock.engine.gateway import Gateway  # noqa: E402
from obmock.world import build_world  # noqa: E402


@pytest.fixture(scope="session")
def world():
    """Read-only world shared by all tests that do not mutate the ledger."""
    return build_world(WorldConfig(n_customers=70))


@pytest.fixture(scope="session")
def gw_off(world):
    return Gateway(world, auth_mode="off")


@pytest.fixture()
def fresh_world():
    """A small private world for tests that execute payments (they append ledger entries)."""
    return build_world(WorldConfig(n_customers=30, min_customers_per_bank=3))
