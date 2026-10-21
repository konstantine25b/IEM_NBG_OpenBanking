"""Coverage in the other direction: every operation a bank *publishes* must be routable in the mock.

test_routes.py walks the routes the mocks registered; this walks the banks' own inventories (spec
`paths`, TBC's 71-operation catalogue = the list the user pasted, Paysera's endpoint pages), so an
operation that was documented but never built cannot hide.
"""
from __future__ import annotations

import json
import re

import pytest
from obmock.banks import BANKS
from obmock.config import FIXTURE_DIR, SPEC_DIR
from obmock.dialects.registry import build_dialects
from obmock.engine.gateway import ApiError, Router
from obmock.validate import HTTP, _load_doc

# Documented by the bank but intentionally not simulated (none at the moment).
NOT_SIMULATED: dict[str, set[tuple[str, str]]] = {}


def published_ops(bank: str) -> set[tuple[str, str]]:
    if bank == "tbc":  # the exact 71 method/path records of the user's pasted TBC catalogue
        recs = json.loads((FIXTURE_DIR / "tbc" / "tbc_example_outputs.json").read_text())["records"]
        return {(r["method"], r["path"]) for r in recs}
    if bank == "paysera":
        rows = json.loads(
            (SPEC_DIR / "paysera" / "paysera_georgia_endpoint_inventory.json").read_text())
        return {(r["method"], r["path"]) for r in rows}
    ops: set[tuple[str, str]] = set()
    for f in BANKS[bank].spec_files:
        doc = _load_doc(str(SPEC_DIR / f), True)
        for path, item in doc["paths"].items():
            ops.update((m.upper(), path) for m in item if m in HTTP)
    return ops


@pytest.fixture(scope="module")
def dialects(world):
    return build_dialects(world)


@pytest.mark.parametrize("bank", list(BANKS))
def test_every_published_operation_is_routable(bank, dialects):
    d = dialects[bank]
    router = Router(d.routes())
    ops = published_ops(bank)
    assert len(ops) >= 12, f"{bank}: inventory unexpectedly small ({len(ops)})"
    missing = []
    for method, path in sorted(ops):
        probe = d.normalize_path(re.sub(r"\{[^}]*\}", "x1", path))
        try:
            router.match(method, probe)
        except ApiError as e:
            if (method, path) not in NOT_SIMULATED.get(bank, set()):
                missing.append(f"{method} {path} -> {e.status}")
    assert not missing, (f"{bank}: {len(missing)} documented operations are not routable:\n"
                         + "\n".join(missing))


def test_tbc_inventory_is_the_users_catalogue():
    # 71 records; the OAuth discovery/authorize/token pairs are repeated across the v1/v2 groups
    assert len(published_ops("tbc")) == 66
