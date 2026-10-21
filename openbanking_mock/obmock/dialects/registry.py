"""Maps each bank's `dialect` key to its implementation class."""
from __future__ import annotations

from obmock.banks import BANKS
from obmock.dialects.base import Dialect
from obmock.dialects.families import (
    AltaSoftV08Dialect,
    AltaSoftV2Dialect,
    BogDialect,
    ObHomeDialect,
    PayseraDialect,
    ProCreditDialect,
    TbcDialect,
)
from obmock.world.model import World

DIALECTS: dict[str, type[Dialect]] = {
    "tbc": TbcDialect,
    "bog": BogDialect,
    "altasoft_v08": AltaSoftV08Dialect,
    "altasoft_v2": AltaSoftV2Dialect,
    "obhome": ObHomeDialect,
    "procredit": ProCreditDialect,
    "paysera": PayseraDialect,
}


def build_dialects(world: World) -> dict[str, Dialect]:
    return {key: DIALECTS[b.dialect](b, world) for key, b in BANKS.items()}
