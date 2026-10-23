"""Shared fixtures. The API tests do not need the 100,000-customer dataset (it is not in a fresh checkout): they build a
240-customer one in a temporary folder (about 10 s), with the same generator, and point the mock services at it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "API"), str(ROOT / "income"), str(ROOT / "eda"), str(ROOT / "openbanking_mock")]


@pytest.fixture(scope="session")
def mini_dataset(tmp_path_factory) -> Path:
    from obmock.config import WorldConfig
    from obmock.pipeline import generate

    cfg = WorldConfig()  # 240 customers, the generator's default world
    if not (cfg.data_dir / "df_1.csv").exists():
        pytest.skip("nbg_synthetic_data/ is missing: the generator has nothing to start from")
    out = tmp_path_factory.mktemp("mini_dataset")
    generate(cfg, out, workers=1, api_sample=0, api_sample_other=0, verbose=False)
    return out


@pytest.fixture(scope="module")
def mock_registries_on_mini(mini_dataset):
    """mock_registries reading the 240-customer truth tables instead of the full dataset."""
    import mock_registries as mr

    mp = pytest.MonkeyPatch()
    mp.setattr(mr, "TRUTH", mini_dataset / "truth" / "customers")
    mr._person_index.cache_clear()
    yield mr
    mp.undo()
    mr._person_index.cache_clear()
