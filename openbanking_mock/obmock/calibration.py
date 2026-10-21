"""Calibration of the simulated population to the supplied loan-level dataset.

The hackathon data (Data/df_*.csv, 100k rows x 58 columns) is a loan-level credit table. We use
its rows as seeds: each simulated customer is a bootstrap draw (without replacement) of one row,
so marginals and joint structure (age/income/region/product/rate/term/delinquency/PD) follow the
data. The row itself is kept as hidden ground truth and never appears in API payloads.
"""
from __future__ import annotations

import glob
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class LoanFrame:
    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self._pd_rank = self.df["probabilityOfDefault"].rank(pct=True).to_numpy()
        self._cols = list(self.df.columns)

    def __len__(self) -> int:
        return len(self.df)

    @classmethod
    def load(cls, data_dir: Path) -> LoanFrame:
        return _load_cached(str(Path(data_dir).resolve()))

    def sample_indices(self, rng: np.random.Generator, n: int) -> np.ndarray:
        if n > len(self):
            raise ValueError(f"requested {n} customers but the dataset has {len(self)} rows")
        return rng.choice(len(self), size=n, replace=False)

    def row(self, i: int) -> dict[str, Any]:
        """One row as plain Python scalars (JSON/CSV friendly)."""
        r = self.df.iloc[i]
        out: dict[str, Any] = {}
        for c in self._cols:
            v = r[c]
            out[c] = v.item() if hasattr(v, "item") else v
        return out

    def rows(self, idx: np.ndarray) -> list[dict[str, Any]]:
        """Many rows as plain Python scalars (vectorised; used by the planning pass)."""
        return self.df.iloc[idx].to_dict("records")

    def stresses(self, idx: np.ndarray) -> np.ndarray:
        """`stress` for many rows at once."""
        d = self.df.iloc[idx]
        s = (0.65 * self._pd_rank[idx]
             + 0.15 * np.minimum(1.0, d["MaximumNumberDelinquencyDays"].to_numpy(float) / 90.0)
             + 0.20 * d["addNegStatus"].to_numpy(float))
        return np.clip(s, 0.0, 1.0)

    def stress(self, i: int) -> float:
        """Latent financial stress in [0, 1]: PD rank + worst delinquency + negative status."""
        r = self.df.iloc[i]
        s = (0.65 * self._pd_rank[i]
             + 0.15 * min(1.0, float(r["MaximumNumberDelinquencyDays"]) / 90.0)
             + 0.20 * float(r["addNegStatus"]))
        return float(min(1.0, max(0.0, s)))


@lru_cache(maxsize=2)
def _load_cached(data_dir: str) -> LoanFrame:
    files = sorted(glob.glob(str(Path(data_dir) / "df_*.csv")))
    if not files:
        raise FileNotFoundError(f"no df_*.csv found under {data_dir}")
    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    return LoanFrame(df)
