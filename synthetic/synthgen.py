"""Synthetic loan-population generator.

Writes NEW customers / loans with exactly the schema of ``nbg_synthetic_data/df_*.csv`` (58 columns, same dtypes, same
event rate), so the result is a drop-in replacement for ``nbg_synthetic_data/``:

    uv run python synthetic/synthgen.py                     # 100,000 synthetic loans -> synthetic/loans/df_1..10.csv
    uv run python synthetic/synthgen.py generate --rows 1000000 --seed 7
    uv run python synthetic/synthgen.py check               # fidelity / privacy / utility gates on synthetic/loans
    uv run python synthetic/synthgen.py bank --customers 2000   # bank API data for them, through the unchanged obmock chain

Why this exists.  ``openbanking_mock/`` simulates the 12 Georgian banks' API data for customers that are
*rows of nbg_synthetic_data/* (bootstrap without replacement), so it can never exceed the 100,000 supplied rows and every
customer is a copy.  This module supplies fresh rows.  Nothing in ``openbanking_mock/`` is changed: the bank
data is produced by pointing its ``--data-dir`` at the folder written here, so every rule of the previous
generation (Georgian market shares with BoG + TBC at 78%, only fields the 12 APIs can return, ISO 13616 / 9362 /
4217 / 20022 and Berlin Group conventions, NBG FX rates, business-day booking) still applies by construction.

Method (the EDA found the supplied data behaves like "distributions + rank correlation", plus regimes):
  * the population is split into cells: delinquency regime (no overdue history / history but nothing current /
    currently overdue) x class of ``addNegStatus`` x mortgage-or-not, wherever a cell has enough real rows.
    Columns that are constant inside a cell (e.g. all arrears columns in the no-history regime) come out constant;
  * inside a cell the quantity columns follow a Gaussian copula with empirical marginals, so spikes (zero
    inflation), caps / floors (INCOME 300 / 10,000, PD floor 0.24), integer supports and decimal precision are
    preserved; its correlation is corrected by simulated moment matching because ties attenuate rank correlation;
  * code columns and flags (incl. the 2,393 branch codes) are borrowed jointly from a real row of the cell that is
    close in the copula's leading factors, with donor weights raked so that every code keeps its real shares;
    the same donor supplies the zero pattern of the eight balance / schedule columns (a closed or written-off loan
    has several of them exactly 0 *together*, which a Gaussian copula has no tail dependence for); their non-zero
    values keep the copula's order and their exact margin;
  * the dictionary identities the supplied data breaks (14 of 17) are NOT enforced.  Three relations a copula cannot
    express (instalment count = months, days left <= contract length, 1-month <= 3-month inquiries) are restored to
    their real rate by re-pairing existing values, so no marginal changes; the other rules are checked, not repaired;
  * the event rate is exact; rows are drawn in 500-row batches with stratified (Latin-hypercube) normals on the
    leading factors, which reproduces the "too evenly mixed blocks" signature the EDA found in the supplied files.

The planted link between the label and the delinquency / score columns of the supplied data is reproduced too
(that is what "follow the rules" means for a credit table); it is not removed, and the bank data that
``openbanking_mock`` builds from these loans therefore keeps its label-driven stress (see its README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial import cKDTree
from scipy.special import ndtr, ndtri

ROOT = Path(__file__).resolve().parent.parent  # repo root (this file lives in synthetic/)
DATA_DIR = ROOT / "nbg_synthetic_data"
OUT_DIR = ROOT / "synthetic" / "loans"
BANK_OUT_DIR = ROOT / "synthetic" / "bank_dataset"
OBMOCK_DIR = ROOT / "openbanking_mock"
MANIFEST = "synth_manifest.json"
REPORT = "FIDELITY_REPORT.md"

TARGET = "addNegStatus"
SEED = 20261021
BATCH = 500  # rows per batch inside a file (the supplied files are balanced in blocks of this size)
STEP_MAX_LEVELS = 400  # up to this many distinct values a column keeps its exact support; above: interpolated
MSM_ROUNDS = 4  # simulated-moment-matching corrections of the copula correlation
MSM_ROWS = 60_000
MIN_CELL = 1000  # rows a (regime, class) cell needs to get a copula of its own
DONOR_PCA = 6  # leading principal factors in which a synthetic row looks for the real rows it borrows codes from
DONOR_PLS = 10  # ... plus this many directions that covary most with the code indicators
DONOR_K = 8  # ... it borrows from one of this many closest rows, picked at random (weighted, see _rake)
DONOR_RAKING = 8  # passes of iterative proportional fitting that keep the code shares of a cell exact

# Code columns (dictionary: the number is a label, not a quantity).  Bool / text / two-level columns are codes too.
NOMINAL = (
    "CLIENT_GEOGRAPHICAL_REGION",
    "CLIENT_LEGAL_FORM",
    "DEBT_DISBURSED_REGION",
    "INTEREST_RATE_TYPE",
    "INTEREST_RATE_PAYMENT_FREQUENCY",
    "PRINCIPAL_PAYMENT_FREQUENCY",
    "PRODUCT_PURPOSE",
    "ECONOMIC_SECTORS",
    "ECONOMIC_SECTORS_PURPOSE",
    "DEBT_SEGMENT",
)
HIGH_CARDINALITY = ("DEBT_DISBURSED_REGION",)  # 2,393 codes: compared by their thousands digit (about 12 buckets)
# Balance / schedule columns of a closed, written-off or fully-due loan are exactly 0 *together* (the EDA's "run-off"
# signature: the event rate climbs from 1% with no zero to 68% with five).  Their zero pattern is borrowed jointly.
ZERO_TOGETHER = (
    "AVERAGE_MONTHLY_PAYMENT",
    "OutstandingPrincipalAm",
    "outstandingAmount",
    "standardInstalmentCount",
    "standardInstalmentAmount",
    "RemainingTerm",
    "MaximumAmountOfInstalmentBySchedule",
    "TotalOutstandingAmount",
)
MORTGAGE_PURPOSES = (7, 10, 13)  # PRODUCT_PURPOSE codes whose dictionary name is a mortgage


def code_columns(d: pd.DataFrame) -> list[str]:
    """Columns that are labels, not quantities: code columns, flags and text (borrowed jointly from real rows)."""
    return [
        c for c in d.columns if c != TARGET and (c in NOMINAL or d[c].dtype.kind not in "iuf" or d[c].nunique() == 2)
    ]


def regime_of(d: pd.DataFrame) -> np.ndarray:
    """Delinquency regime of every loan: 0 no overdue history, 1 history but nothing current, 2 currently overdue.
    In regime 0 the arrears columns are (almost) all exactly 0; a copula alone cannot make them zero together."""
    need = {"TotalNumberDelinquencyDays", "daysInDeliquency", "overdueInstalmentsCount"}
    if not need <= set(d.columns):
        return np.zeros(len(d), dtype=int)
    history = d["TotalNumberDelinquencyDays"].to_numpy() > 0
    current = (d["daysInDeliquency"].to_numpy() > 0) | (d["overdueInstalmentsCount"].to_numpy() > 0)
    return np.where(~history, 0, np.where(~current, 1, 2))


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# reading / writing the loan files
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def list_loan_files(folder: Path) -> list[Path]:
    files = [p for p in Path(folder).glob("df_*.csv") if p.stem.split("_", 1)[1].isdigit()]
    return sorted(files, key=lambda p: int(p.stem.split("_", 1)[1]))


def read_loans(folder: Path) -> tuple[pd.DataFrame, list[int]]:
    """All ``df_<n>.csv`` of a folder in file order, and the row count of each file."""
    files = list_loan_files(folder)
    if not files:
        raise SystemExit(f"no df_<n>.csv found in {folder}")
    frames = [pd.read_csv(f) for f in files]
    columns = list(frames[0].columns)
    for f, fr in zip(files, frames, strict=True):
        if list(fr.columns) != columns:
            raise SystemExit(f"{f.name} has a different header than {files[0].name}")
    df = pd.concat(frames, ignore_index=True)
    if df.isna().any().any():
        raise SystemExit("missing values are not supported (the supplied data has none)")
    if TARGET not in df.columns:
        raise SystemExit(f"column {TARGET} not found in {folder}")
    return df, [len(fr) for fr in frames]


def shown(path: Path) -> str:
    """A path as written in manifests and reports: relative to the project when it is inside it."""
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if ROOT in p.parents else str(p)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare_out_dir(out: Path) -> None:
    """Create ``out``; replace only the files a previous run wrote there (never anything else)."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / MANIFEST
    mine: set[str] = set()
    if manifest.exists():
        mine = {MANIFEST, REPORT, *json.loads(manifest.read_text())["files"]}
    foreign = [p.name for p in out.iterdir() if p.name not in mine]
    if foreign:
        raise SystemExit(f"{out} contains files this tool did not write ({foreign[:3]} ...): pick another --out")
    for name in mine:
        (out / name).unlink(missing_ok=True)


def write_loans(df: pd.DataFrame, out: Path, rows_per_file: int, meta: dict) -> list[Path]:
    prepare_out_dir(out)
    paths = []
    for i, start in enumerate(range(0, len(df), rows_per_file), start=1):
        p = out / f"df_{i}.csv"
        df.iloc[start : start + rows_per_file].to_csv(p, index=False)
        paths.append(p)
    manifest = {
        **meta,
        "rows": len(df),
        "rows_per_file": rows_per_file,
        "files": {p.name: sha256_of(p) for p in paths},
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return paths


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# one column: empirical distribution <-> uniform scale of the copula
# ──────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Margin:
    """Empirical distribution of a column on the copula's uniform scale.

    ``levels`` are the distinct values in copula order (ascending for quantities, ``order_key`` for codes),
    ``cum`` / ``mid`` the cumulative and mid-point probability of each level.  Ties therefore map to a
    single score (their mid-probability) and decode back to exactly that value, which is what keeps spikes and
    exact supports.  Columns with many distinct values are interpolated between order statistics.
    """

    uniq: np.ndarray
    pos: np.ndarray
    levels: np.ndarray
    cum: np.ndarray
    mid: np.ndarray
    sorted_values: np.ndarray | None
    integral: bool
    decimals: int | None

    @classmethod
    def fit(cls, x: np.ndarray, order_key: np.ndarray | None = None) -> Margin:
        uniq, counts = np.unique(x, return_counts=True)
        n = len(x)
        order = np.arange(len(uniq)) if order_key is None else np.argsort(order_key, kind="stable")
        pos = np.empty(len(uniq), dtype=np.intp)
        pos[order] = np.arange(len(uniq))
        c = counts[order]
        cum = np.cumsum(c) / n
        interpolate = order_key is None and x.dtype.kind in "iuf" and len(uniq) > STEP_MAX_LEVELS
        integral = x.dtype.kind in "iu" or (x.dtype.kind == "f" and bool(np.all(x == np.round(x))))
        decimals = None
        if x.dtype.kind == "f" and not integral:
            decimals = next((d for d in range(9) if np.array_equal(np.round(x, d), x)), None)
        return cls(
            uniq=uniq,
            pos=pos,
            levels=uniq[order],
            cum=cum,
            mid=cum - c / (2 * n),
            sorted_values=np.sort(x) if interpolate else None,
            integral=integral,
            decimals=decimals,
        )

    def codes(self, x: np.ndarray) -> np.ndarray:
        return self.pos[np.searchsorted(self.uniq, x)]

    def uniform(self, x: np.ndarray) -> np.ndarray:
        """Mid-rank uniform scores of observed values (ties share their mid-probability)."""
        return self.mid[self.codes(x)]

    def level_of(self, u: np.ndarray) -> np.ndarray:
        return np.minimum(np.searchsorted(self.cum, u, side="right"), len(self.cum) - 1)

    def snap(self, u: np.ndarray) -> np.ndarray:
        """Mid-probability of the level a uniform draw falls into (what ``uniform`` returns for that value)."""
        return self.mid[self.level_of(u)]

    def values(self, u: np.ndarray) -> np.ndarray:
        if self.sorted_values is None:
            return self.levels[self.level_of(u)]
        n = len(self.sorted_values)
        v = np.interp(u, (np.arange(n) + 0.5) / n, self.sorted_values)
        if self.integral:
            return np.round(v)
        return v if self.decimals is None else np.round(v, self.decimals)


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# Gaussian copula of one cell
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def nearest_correlation(a: np.ndarray, floor: float = 1e-6) -> np.ndarray:
    """Symmetric, positive-definite, unit-diagonal matrix close to ``a`` (eigenvalue clipping)."""
    w, v = np.linalg.eigh((a + a.T) / 2)
    m = (v * np.clip(w, floor, None)) @ v.T
    d = 1.0 / np.sqrt(np.diag(m))
    out = m * d[:, None] * d[None, :]
    np.fill_diagonal(out, 1.0)
    return out


class CellModel:
    """One cell of the population (a delinquency regime, possibly one class of the target).

    The quantity columns follow a Gaussian copula with empirical marginals; columns that are constant inside the
    cell are simply constant.  The code columns (codes, flags, text) are copied jointly from a real row of the
    cell that is close to the synthetic row in the copula's leading factors, so their exact multi-way structure
    (e.g. instalmentType = Variable with principal frequency 7, sector = purpose sector) and their link to the
    quantities survive; a Gaussian copula can only express one ordered axis per code.
    """

    def __init__(self, d: pd.DataFrame, codes: Sequence[str], regime: int | None = None):
        self.n = len(d)
        self.regime = regime
        self.codes = list(codes)
        feats = [c for c in d.columns if c != TARGET and c not in self.codes]
        self.const = {c: d[c].iloc[0] for c in feats if d[c].nunique() == 1}
        self.cols = [c for c in feats if c not in self.const]
        self.margins = {c: Margin.fit(d[c].to_numpy()) for c in self.cols}
        uni = np.column_stack([self.margins[c].uniform(d[c].to_numpy()) for c in self.cols])
        self.target_rho = np.corrcoef(uni, rowvar=False)  # Spearman correlation of the real rows
        self.corr, self.fit_error = self._calibrate()
        self._refactor()
        self.donor_codes = {c: d[c].to_numpy() for c in self.codes}
        # zero pattern of the balance / schedule family: borrowed with the codes; the magnitudes of the non-zero part
        # keep the copula's dependence (their margin is the positive part only)
        self.zero_cols = [c for c in ZERO_TOGETHER if c in self.cols and 0 < (d[c] == 0).mean() < 1]
        self.donor_zero = {c: d[c].to_numpy() == 0 for c in self.zero_cols}
        self.positive = {c: Margin.fit(d[c].to_numpy()[~self.donor_zero[c]]) for c in self.zero_cols}
        z = ndtri(uni)
        self.proj = self._matching_directions(z, d)
        self.tree = cKDTree(z @ self.proj)
        self.row_weight = self._rake(d)

    def _refactor(self) -> None:
        w, v = np.linalg.eigh(self.corr)
        top = np.argsort(w)[::-1]  # leading factors first
        self.std, self.vec = np.sqrt(w[top]), v[:, top]

    def nudge(self, a: str, b: str, delta: float) -> None:
        """Shift the copula correlation of two columns (used to compensate what a restoration adds)."""
        if a in self.cols and b in self.cols:
            i, j = self.cols.index(a), self.cols.index(b)
            self.corr[i, j] = self.corr[j, i] = np.clip(self.corr[i, j] + delta, -0.999, 0.999)
            self.corr = nearest_correlation(self.corr)
            self._refactor()

    def _repair_current(self, out: dict[str, np.ndarray], rng: np.random.Generator) -> None:
        """In the 'currently overdue' regime a day count or an instalment count must be positive.  A row with
        neither swaps its (zero) day count with that of a row where both are positive: both columns keep their
        exact marginal, and both rows end up consistent."""
        days, count = out.get("daysInDeliquency"), out.get("overdueInstalmentsCount")
        if days is None or count is None:
            return
        bad = np.flatnonzero((days == 0) & (count == 0))
        good = np.flatnonzero((days > 0) & (count > 0))
        m = min(len(bad), len(good))
        if m:
            bad, partner = bad[:m], rng.choice(good, size=m, replace=False)
            days[bad], days[partner] = days[partner].copy(), days[bad].copy()

    def _with_borrowed_zeros(self, col: str, u: np.ndarray, zero: np.ndarray) -> np.ndarray:
        """Values of a balance / schedule column: exactly 0 where the donor row has 0, else a draw from the positive
        part of its margin.  The positives are ranked by the copula's uniform and rank-mapped onto that margin, so
        every row keeps its place in the order the copula gave it (a row the copula would have zeroed but the donor
        did not gets one of the smallest values) and the margin of the non-zero values stays exact."""
        pos = np.flatnonzero(~zero)
        out = np.zeros(len(u))
        out[pos] = self.positive[col].values((stats.rankdata(u[pos]) - 0.5) / max(len(pos), 1))
        return out

    def _latent(self, sizes: Sequence[int], rng: np.random.Generator, strata: int) -> np.ndarray:
        """Uniform draws of the copula for consecutive batches; ``strata`` leading factors are drawn as stratified
        (Latin-hypercube) normals inside every batch."""
        p = len(self.cols)
        g = rng.standard_normal((int(sum(sizes)), p))
        at = 0
        for b in sizes:
            k = min(strata, p)
            if k and b >= 8:
                perm = np.argsort(rng.random((b, k)), axis=0)
                g[at : at + b, :k] = ndtri((perm + rng.random((b, k))) / b)
            at += b
        return ndtr((g * self.std) @ self.vec.T)

    def _neighbours(self, u: np.ndarray) -> np.ndarray:
        """Indices of the real rows closest to each synthetic row in the matching directions."""
        s = ndtri(np.column_stack([self.margins[c].snap(u[:, j]) for j, c in enumerate(self.cols)]))
        k = min(DONOR_K, self.n)
        return self.tree.query(s @ self.proj, k=k, workers=-1)[1].reshape(len(u), k)

    def _rake(self, d: pd.DataFrame) -> np.ndarray:
        """Donor weight of every real row such that each code column keeps its real shares among the synthetic rows
        of this cell (iterative proportional fitting on a trial draw): borrowing from close rows alone drifts
        towards the dense parts of the cell."""
        ids = {c: np.unique(d[c].to_numpy(), return_inverse=True)[1] for c in self.codes if c not in HIGH_CARDINALITY}
        ids.update({f"zero:{c}": z.astype(int) for c, z in self.donor_zero.items()})
        if self.zero_cols:  # ... and how many of them are zero together
            ids["zero:count"] = np.unique(sum(z.astype(int) for z in self.donor_zero.values()), return_inverse=True)[1]
        if not ids or not self.cols:
            return np.ones(self.n)
        share = {c: np.bincount(i) / self.n for c, i in ids.items()}
        log_w = {c: np.zeros(len(s)) for c, s in share.items()}
        trial = min(20_000, max(2_000, self.n))
        near = self._neighbours(self._latent([trial], np.random.default_rng(1), 0))
        weight = np.ones(self.n)
        for _ in range(DONOR_RAKING):
            for c, i in ids.items():
                p = weight[near]
                p /= p.sum(1, keepdims=True)
                got = np.bincount(i[near].ravel(), weights=p.ravel(), minlength=len(share[c])) / trial
                log_w[c] = np.clip(log_w[c] + np.log(np.where(got > 0, share[c] / np.maximum(got, 1e-12), 1.0)), -3, 3)
                weight = np.exp(sum(log_w[k][ids[k]] for k in ids))
        return weight

    def _matching_directions(self, z: np.ndarray, d: pd.DataFrame) -> np.ndarray:
        """Directions of the quantity space in which a synthetic row looks for real rows to borrow codes from:
        the leading principal factors (the bulk of the structure) next to the directions that covary most with
        the code indicators (what separates mortgage from consumer loans, fixed from variable ...)."""
        _, factors = np.linalg.eigh(np.corrcoef(z, rowvar=False))
        pca = factors[:, ::-1][:, : min(DONOR_PCA, z.shape[1])]
        dummies = [
            (d[c].to_numpy()[:, None] == np.unique(d[c].to_numpy())[None, :]).astype(float)
            for c in self.codes
            if c not in HIGH_CARDINALITY and d[c].nunique() > 1
        ] + [z[:, None].astype(float) for z in self.donor_zero.values()]
        if not dummies:
            return pca
        ind = np.hstack(dummies)
        cross = z.T @ (ind - ind.mean(0)) / len(z)
        u, s, _ = np.linalg.svd(cross, full_matrices=False)
        r = min(DONOR_PLS, len(s))
        pls = u[:, :r] * (s[:r] / s[0])  # supervised directions, weighted by how much they matter
        return np.hstack([pca, pls])

    def _snapped_rho(self, corr: np.ndarray, g: np.ndarray) -> np.ndarray:
        """Spearman correlation of the decoded (tied) values a copula with this correlation would produce."""
        w, v = np.linalg.eigh(corr)
        u = ndtr((g * np.sqrt(np.clip(w, 0, None))) @ v.T)
        s = np.column_stack([self.margins[c].snap(u[:, j]) for j, c in enumerate(self.cols)])
        with np.errstate(invalid="ignore"):
            rho = np.corrcoef(s, rowvar=False)
        return np.where(np.isfinite(rho), rho, self.target_rho)

    def _calibrate(self) -> tuple[np.ndarray, float]:
        """Simulated moment matching: ties (zero inflation, spikes) attenuate rank correlation, so the copula's
        correlation is moved until the decoded values reproduce the real rank correlation."""
        g = np.random.default_rng(0).standard_normal((MSM_ROWS, len(self.cols)))
        corr = nearest_correlation(2 * np.sin(np.pi / 6 * self.target_rho))  # exact for continuous margins
        best, best_err = corr, np.inf
        for _ in range(MSM_ROUNDS + 1):
            gap = self.target_rho - self._snapped_rho(corr, g)
            err = float(np.abs(gap).mean())
            if err < best_err:
                best, best_err = corr, err
            corr = nearest_correlation(corr + gap)
        return best, best_err

    def sample(self, sizes: Sequence[int], rng: np.random.Generator, strata: int) -> pd.DataFrame:
        """Rows for consecutive batches of the given sizes."""
        u = self._latent(sizes, rng, strata)
        out = {c: self.margins[c].values(u[:, j]) for j, c in enumerate(self.cols)}
        near = self._neighbours(u)
        w = self.row_weight[near]
        draw = rng.random((len(u), 1)) * w.sum(1, keepdims=True)
        donor = near[np.arange(len(u)), np.minimum((np.cumsum(w, 1) < draw).sum(1), near.shape[1] - 1)]
        for c in self.codes:
            out[c] = self.donor_codes[c][donor]
        for c in self.zero_cols:
            out[c] = self._with_borrowed_zeros(c, u[:, self.cols.index(c)], self.donor_zero[c][donor])
        for c, v in self.const.items():
            out[c] = np.full(len(u), v)
        if self.regime == 2:
            self._repair_current(out, rng)
        return pd.DataFrame(out)


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# the generator
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def rematch(values: np.ndarray, key: np.ndarray, rows: np.ndarray, noise: np.ndarray) -> None:
    """Re-assign the values of ``rows`` among those rows in the order of ``key`` (smallest value to the smallest
    key, ties by ``noise``): the multiset of values is unchanged, only who holds which value."""
    order = rows[np.lexsort((noise[rows], key[rows]))]
    values[order] = np.sort(values[rows])


def bisect_fraction(trial, target: float, increasing: bool, rounds: int = 12) -> np.ndarray:
    """Smallest subset fraction whose ``trial(f) -> (rate, values)`` reaches ``target`` (all rows if it never does)."""
    lo, hi = 0.0, 1.0
    best = trial(1.0)
    for _ in range(rounds):
        mid = (lo + hi) / 2
        rate, vals = trial(mid)
        if (rate >= target) == increasing:
            hi, best = mid, (rate, vals)
        else:
            lo = mid
    return best[1]


def restore_atom(
    term: np.ndarray, count: np.ndarray, monthly: np.ndarray, target: float, noise: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Instalment counts re-paired with contract terms until monthly loans have as many instalments as months
    (+-1) as often as ``target``."""
    near = np.abs(count - term) <= 1
    if not monthly.any() or near[monthly].mean() >= target:
        return count
    far = np.flatnonzero(monthly & ~near & (count > 0))  # a zero count belongs to a closed loan: it stays where it is
    u = rng.random(len(far))

    def trial(f: float) -> tuple[float, np.ndarray]:
        c = count.copy()
        rematch(c, term, far[u < f], noise)
        return float((np.abs(c - term) <= 1)[monthly].mean()), c

    return bisect_fraction(trial, target, increasing=True)


def restore_order(
    values: np.ndarray, bound: np.ndarray, target: float, noise: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Values re-paired with their bounds until they exceed them no more often than ``target``.  Rows that move are
    matched with as many rows that do not exceed it, so the multiset of values is unchanged (zeros stay put)."""
    over = values > bound
    if over.mean() <= target:
        return values
    bad, good = np.flatnonzero(over), rng.permutation(np.flatnonzero(~over & (values > 0)))
    u = rng.random(len(bad))

    def trial(f: float) -> tuple[float, np.ndarray]:
        chosen = bad[u < f]
        c = values.copy()
        rematch(c, bound, np.concatenate([chosen, good[: len(chosen)]]), noise)
        return float((c > bound).mean()), c

    return bisect_fraction(trial, target, increasing=False)


def split_events(sizes: np.ndarray, n_events: int, sd: float, rng: np.random.Generator) -> np.ndarray:
    """Events per batch: proportional share plus a limited jitter, summing to exactly ``n_events``."""
    share = n_events * sizes / sizes.sum()
    c = np.clip(np.rint(share + rng.normal(0, sd, len(sizes))), 0, sizes).astype(int)
    diff = n_events - int(c.sum())
    while diff != 0:
        j = int(rng.integers(len(sizes)))
        step = 1 if diff > 0 else -1
        if 0 <= c[j] + step <= sizes[j]:
            c[j] += step
            diff -= step
    return c


def product_group(d: pd.DataFrame) -> np.ndarray:
    """1 for mortgage products (their terms, amounts and rates form a cluster of their own), else 0."""
    if "PRODUCT_PURPOSE" not in d.columns:
        return np.zeros(len(d), dtype=int)
    return d["PRODUCT_PURPOSE"].isin(MORTGAGE_PURPOSES).to_numpy().astype(int)


# "a <= b" relations of the supplied data that are restored to their real violation rate: name -> (column that
# moves, its bound).  Days left stay within the contract (31 days a month, one month of slack); the 1-month inquiry
# window lies inside the 3-month one.
ORDER_RESTORATIONS = {
    "bound": ("RemainingTerm", lambda d: 31 * (d["ContractTermMonth"].to_numpy() + 1)),
    "window": ("last1Month", lambda d: d["last3Month"].to_numpy()),
}


def scope_id(key: tuple[int, int | None, int | None]) -> int:
    """Restorations act inside a (regime, class) scope; the scope of a cell key (regime, class or None, group)."""
    return key[0] * 3 + (2 if key[1] is None else key[1])


@dataclass(frozen=True)
class Combo:
    """Share of the population that is in one regime / class / product group, and the cell that generates it."""

    cls: int
    cell: tuple[int, int | None, int | None]
    share: float


class Synthesizer:
    """Fitted on the real loan table; ``sample`` returns any number of new rows with its schema.

    Cells are (delinquency regime, class of the target, product group).  A class gets its own cell only where it has
    at least ``MIN_CELL`` real rows (in the regimes without overdue history almost no loan is a positive, and there
    the label is indistinguishable from the non-events, so both classes share the regime's cell); the product group
    splits a cell only if every resulting cell has at least ``MIN_CELL`` rows.
    """

    def __init__(self, real: pd.DataFrame, block_counts: Sequence[int] | None = None):
        self.columns = list(real.columns)
        self.dtypes = real.dtypes.to_dict()
        y = real[TARGET].to_numpy()
        regime = regime_of(real)
        split_regimes = np.bincount(regime, minlength=3).min() >= MIN_CELL
        if not split_regimes:
            regime = np.zeros(len(real), dtype=int)  # too small to split: one regime
        group = product_group(real)
        codes = code_columns(real)
        self.cells: dict[tuple[int, int | None, int | None], CellModel] = {}
        self.combos: list[Combo] = []
        scope = regime * 3 + 2  # scope = (regime, class) where the class has its own cell, else (regime, both)
        for r in np.unique(regime):
            in_r = regime == r
            by_class = min((in_r & (y == c)).sum() for c in (0, 1)) >= MIN_CELL
            if by_class:
                scope[in_r] = r * 3 + y[in_r]
            for c_key in (0, 1) if by_class else (None,):
                in_c = in_r & (y == c_key) if by_class else in_r
                by_group = min((in_c & (group == g)).sum() for g in (0, 1)) >= MIN_CELL
                for g_key in (0, 1) if by_group else (None,):
                    sel = in_c & (group == g_key) if by_group else in_c
                    cell = CellModel(real[sel].reset_index(drop=True), codes, int(r) if split_regimes else None)
                    self.cells[(int(r), c_key, g_key)] = cell
                    for c in (0, 1) if c_key is None else (c_key,):
                        for g in (0, 1) if g_key is None else (g_key,):
                            n_cg = int((in_r & (y == c) & (group == g)).sum())
                            if n_cg:
                                self.combos.append(Combo(c, (int(r), c_key, g_key), n_cg / len(real)))
        self.event_rate = float(y.mean())
        self.event_sd = self._event_sd(y)
        self.restorations = {int(s): self._fit_restorations(real[scope == s]) for s in np.unique(scope)}
        self._tune_atom(real, scope)
        self.strata = 0
        if block_counts is not None:
            self.strata = calibrate_strata(self, real, block_counts)

    def _tune_atom(self, real: pd.DataFrame, scope: np.ndarray, rounds: int = 4, n: int = 60_000) -> None:
        """Restoring the equality atom lifts the rank correlation of its two columns above the real one, so the
        copula's correlation of that pair is lowered until the restored sample reproduces the real value."""
        a, b = "ContractTermMonth", "standardInstalmentCount"
        if not any("atom" in rates for rates in self.restorations.values()):
            return
        real_rho = {s: real[scope == s][[a, b]].corr(method="spearman").iloc[0, 1] for s in self.restorations}
        for _ in range(rounds):
            syn, got = self.sample(n, seed=7, strata=0, with_scope=True)
            worst = 0.0
            for s, target in real_rho.items():
                gap = target - syn[got == s][[a, b]].corr(method="spearman").iloc[0, 1]
                worst = max(worst, abs(gap))
                for key, cell in self.cells.items():
                    if scope_id(key) == s and np.isfinite(gap):
                        cell.nudge(a, b, gap)
            if worst < 0.01:
                break

    @staticmethod
    def _event_sd(y: np.ndarray) -> float:
        """Spread of events per 500-row block of the real files (a coin flip would give sqrt(500 p (1 - p)))."""
        k = len(y) // BATCH
        if k < 8:
            return float(np.sqrt(BATCH * y.mean() * (1 - y.mean())))
        return float(y[: k * BATCH].reshape(k, BATCH).sum(1).std(ddof=1))

    @staticmethod
    def _fit_restorations(real: pd.DataFrame) -> dict[str, float]:
        """Rates of relations of the supplied data that a copula cannot express: an exact-equality atom (monthly loans
        have as many instalments as months, +-1, in 43% of cases) and the ``ORDER_RESTORATIONS`` bounds."""
        out: dict[str, float] = {}
        have = set(real.columns)
        if {"ContractTermMonth", "standardInstalmentCount", "PRINCIPAL_PAYMENT_FREQUENCY"} <= have:
            monthly = real["PRINCIPAL_PAYMENT_FREQUENCY"] == 2
            near = (real["standardInstalmentCount"] - real["ContractTermMonth"]).abs() <= 1
            out["atom"] = float(near[monthly].mean())
        for name, (col, bound) in ORDER_RESTORATIONS.items():
            try:
                out[name] = float((real[col].to_numpy() > bound(real)).mean())
            except KeyError:
                continue
        return out

    def _restore(self, df: pd.DataFrame, scope: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
        """Make those relations hold as often as in the supplied data (the copula alone gets about half of it) by
        re-pairing existing values inside a random subset of rows of the same (regime, class) scope, so every
        column keeps its exact marginal there."""
        noise = rng.random(len(df))
        count = df["standardInstalmentCount"].to_numpy().copy() if "standardInstalmentCount" in df else None
        monthly = (df["PRINCIPAL_PAYMENT_FREQUENCY"] == 2).to_numpy() if "PRINCIPAL_PAYMENT_FREQUENCY" in df else None
        moved, bounds = {}, {}
        for name, (col, bound) in ORDER_RESTORATIONS.items():
            try:
                moved[name], bounds[name] = df[col].to_numpy().copy(), bound(df)
            except KeyError:
                continue
        for sid, rates in self.restorations.items():
            rows = np.flatnonzero(scope == sid)
            if not len(rows):
                continue
            if "atom" in rates and count is not None and monthly is not None:
                term = df["ContractTermMonth"].to_numpy()
                count[rows] = restore_atom(term[rows], count[rows], monthly[rows], rates["atom"], noise[rows], rng)
            for name, values in moved.items():
                if name in rates:
                    values[rows] = restore_order(values[rows], bounds[name][rows], rates[name], noise[rows], rng)
        if count is not None:
            df["standardInstalmentCount"] = count
        for name, values in moved.items():
            df[ORDER_RESTORATIONS[name][0]] = values
        return df

    def sample(
        self,
        n: int,
        seed: int = SEED,
        event_rate: float | None = None,
        strata: int | None = None,
        with_scope: bool = False,
    ) -> pd.DataFrame | tuple[pd.DataFrame, np.ndarray]:
        rng = np.random.default_rng(seed)
        k = self.strata if strata is None else strata
        sizes = np.array([min(BATCH, n - i) for i in range(0, n, BATCH)])
        n1 = int(round(n * (self.event_rate if event_rate is None else event_rate)))
        events = split_events(sizes, n1, self.event_sd, rng)
        # rows of every combo in every batch (class totals are fixed per batch, the regime mix is random)
        count = np.zeros((len(self.combos), len(sizes)), dtype=int)
        for cls, per_batch in ((0, sizes - events), (1, events)):
            idx = [i for i, c in enumerate(self.combos) if c.cls == cls]
            if not idx:
                continue
            p = np.array([self.combos[i].share for i in idx])
            for b, m in enumerate(per_batch):
                count[idx, b] = rng.multinomial(m, p / p.sum())
        parts = []
        for key, cell in self.cells.items():
            n_cls = [
                sum(count[i] for i, c in enumerate(self.combos) if c.cell == key and c.cls == cls) for cls in (0, 1)
            ]
            n_cls = [x if isinstance(x, np.ndarray) else np.zeros(len(sizes), dtype=int) for x in n_cls]
            per_batch = n_cls[0] + n_cls[1]
            d = cell.sample(per_batch, rng, k)
            d[TARGET] = np.repeat(np.tile([0, 1], len(sizes)), np.column_stack(n_cls).ravel())
            d["_batch"] = np.repeat(np.arange(len(sizes)), per_batch)
            d["_scope"] = scope_id(key)
            parts.append(d)
        df = pd.concat(parts, ignore_index=True)
        order = np.argsort(df["_batch"].to_numpy() + rng.random(len(df)), kind="stable")  # shuffle inside a batch
        df = df.iloc[order].drop(columns="_batch").reset_index(drop=True)
        scope = df.pop("_scope").to_numpy()
        df = pd.DataFrame({c: pd.Series(df[c].to_numpy()).astype(self.dtypes[c]) for c in self.columns})
        df = self._restore(df, scope, rng)
        return (df, scope) if with_scope else df


def block_variance_ratio(df: pd.DataFrame, cols: Sequence[str], sizes: Sequence[int], block: int = BATCH) -> float:
    """Median over columns and files of: variance of block means of normal scores / what a random order gives
    (the EDA's batch-balance statistic; ~1 for shuffled rows, well below 1 for evenly mixed blocks)."""
    ratios = []
    start = 0
    for size in sizes:
        part = df.iloc[start : start + size]
        start += size
        k = size // block
        if k < 4:
            continue
        per_col = []
        for c in cols:
            z = ndtri((stats.rankdata(part[c].to_numpy(float)) - 0.5) / size)
            if z.std() == 0:
                continue
            m = z[: k * block].reshape(k, block).mean(1)
            per_col.append(m.var(ddof=1) / (z.var(ddof=1) / block * (size - block) / (size - 1)))
        if per_col:
            ratios.append(float(np.median(per_col)))
    return float(np.median(ratios)) if ratios else float("nan")


def balance_columns(df: pd.DataFrame) -> list[str]:
    """The columns the EDA's batch-balance statistic looks at: every numeric column but the target."""
    return [c for c in df.columns if c != TARGET and df[c].dtype.kind in "iuf"]


def calibrate_strata(model: Synthesizer, real: pd.DataFrame, block_counts: Sequence[int]) -> int:
    """Number of stratified leading factors that reproduces the real files' block balance (0 = none needed).
    The statistic is noisy (about 20 blocks per file, and it moves by +-0.05 with the seed), so each candidate is
    measured on three seeds of two files' worth of rows, and the monotone grid is searched by bisection."""
    cols = balance_columns(real)
    target = block_variance_ratio(real, cols, block_counts)
    if not np.isfinite(target) or target > 0.9:
        return 0
    size = int(np.median(block_counts))
    files = max(1, min(2, len(real) // size))

    def ratio(k: int) -> float:
        runs = [model.sample(size * files, seed=s, strata=k) for s in (1, 2, 3)]
        return float(np.mean([block_variance_ratio(r, cols, [size] * files) for r in runs]))

    grid = (0, 2, 4, 8, 12, 16, 24, 32, 48, 64)
    lo, hi = 0, len(grid) - 1
    seen: dict[int, float] = {}
    while lo < hi:  # the smallest k whose ratio is at or below the real one (the ratio falls as k grows)
        mid = (lo + hi) // 2
        seen[mid] = ratio(grid[mid])
        if seen[mid] <= target:
            hi = mid
        else:
            lo = mid + 1
    if lo == 0:
        return grid[0]
    here, before = seen.get(lo) or ratio(grid[lo]), seen.get(lo - 1) or ratio(grid[lo - 1])
    return grid[lo] if abs(here - target) <= abs(before - target) else grid[lo - 1]


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# rules of the supplied data: their violation rates are reproduced (and checked), never repaired
# ──────────────────────────────────────────────────────────────────────────────────────────────────
MATERIAL = 1.0  # one currency unit: a smaller gap between two money columns is rounding dust
_MONEY_PAIR = 1.0
# (a, b, tolerance): the dictionary says a <= b
ORDER_RULES: list[tuple[str, str, float]] = [
    ("last1Month", "last3Month", 0.0),
    ("last3Month", "last6Month", 0.0),
    ("last6Month", "last12Month", 0.0),
    ("daysInDeliquency", "MaximumNumberDelinquencyDays", 0.0),
    ("MaximumNumberDelinquencyDays", "TotalNumberDelinquencyDays", 0.0),
    ("TotalNumberDelinquencyDaysLastYear", "TotalNumberDelinquencyDays", 0.0),
    ("overdueInstalmentsCount", "TotalNumberOverdueInst", 0.0),
    ("penaltyPaid", "penaltySum", _MONEY_PAIR),
    ("OutstandingPrincipalAm", "DISBURSED_AMOUNT", _MONEY_PAIR),
    ("OutstandingPrincipalAm", "outstandingAmount", _MONEY_PAIR),
    ("outstandingAmount", "TotalOutstandingAmount", _MONEY_PAIR),
    ("overdueAmount", "TotalDebtOverdue", _MONEY_PAIR),
    ("standardInstalmentAmount", "MaximumAmountOfInstalmentBySchedule", _MONEY_PAIR),
]
CONSUMER_PURPOSES = (2, 3, 4, 5, 6)  # consumer loan, payday, instant instalment, overdraft, credit card


def violation_rates(d: pd.DataFrame) -> dict[str, float]:
    """Share of rows breaking each dictionary rule / category agreement (rules whose columns exist)."""
    out: dict[str, float] = {}
    have = set(d.columns)
    for a, b, tol in ORDER_RULES:
        if {a, b} <= have:
            out[f"{a} <= {b}"] = float((d[a] > d[b] + tol).mean())
    if {"overdueAmount", "OverduePrincipalAm", "OverdueInterestCommission"} <= have:
        gap = (d["overdueAmount"] - d["OverduePrincipalAm"] - d["OverdueInterestCommission"]).abs()
        out["overdueAmount = OverduePrincipalAm + OverdueInterestCommission"] = float((gap > MATERIAL).mean())
    if {"overdueAmount", "overdueInstalmentsCount"} <= have:
        out["material overdue money but zero overdue instalments"] = float(
            ((d["overdueAmount"] >= MATERIAL) & (d["overdueInstalmentsCount"] == 0)).mean()
        )
    if {"ContractTermMonth", "standardInstalmentCount", "PRINCIPAL_PAYMENT_FREQUENCY"} <= have:
        monthly = d["PRINCIPAL_PAYMENT_FREQUENCY"] == 2
        gap = (d["ContractTermMonth"] - d["standardInstalmentCount"]).abs() > 1
        out["monthly loans: ContractTermMonth = standardInstalmentCount (+-1)"] = float(gap[monthly].mean())
    if {"RemainingTerm", "ContractTermMonth"} <= have:
        out["RemainingTerm (days) <= whole contract"] = float(
            (d["RemainingTerm"] > 31 * (d["ContractTermMonth"] + 1)).mean()
        )
    if {"loanType", "PRODUCT_PURPOSE"} <= have:
        mortgage = d["PRODUCT_PURPOSE"].isin(MORTGAGE_PURPOSES)
        out["loanType = ConsumerLoan but the purpose is a mortgage"] = float(
            ((d["loanType"] == "ConsumerLoan") & mortgage).mean()
        )
        out["loanType = MortgageLoan but the purpose is not a mortgage"] = float(
            ((d["loanType"] == "MortgageLoan") & ~mortgage).mean()
        )
    if {"PRODUCT_PURPOSE", "DEBT_SEGMENT"} <= have:
        out["consumer purpose in the corporate / SME / micro segment"] = float(
            (d["PRODUCT_PURPOSE"].isin(CONSUMER_PURPOSES) & d["DEBT_SEGMENT"].isin([1, 2, 3])).mean()
        )
    if {"CLIENT_LEGAL_FORM", "LoanSegment"} <= have:
        out["private individual in the Business segment"] = float(
            ((d["CLIENT_LEGAL_FORM"] == 7) & (d["LoanSegment"] == "Business")).mean()
        )
    if {"guaranteeAmount", "LTV_RATIO"} <= have:
        out["collateral above 0 but LTV_RATIO = 0"] = float(((d["guaranteeAmount"] > 0) & (d["LTV_RATIO"] == 0)).mean())
    if {"DISBURSED_AMOUNT", "OutstandingPrincipalAm"} <= have:
        out["disbursed amount 0 but a balance is owed"] = float(
            ((d["DISBURSED_AMOUNT"] == 0) & (d["OutstandingPrincipalAm"] >= MATERIAL)).mean()
        )
    if {"ECONOMIC_SECTORS", "ECONOMIC_SECTORS_PURPOSE"} <= have:
        used = d["ECONOMIC_SECTORS_PURPOSE"] > 0
        out["sector of purpose equals sector of income (where a purpose is given)"] = float(
            (d["ECONOMIC_SECTORS"] == d["ECONOMIC_SECTORS_PURPOSE"])[used].mean()
        )
    return out


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# fidelity / privacy / utility gates
# ──────────────────────────────────────────────────────────────────────────────────────────────────
LIMITS = {
    "ks": 0.02,  # largest Kolmogorov-Smirnov distance of a quantity column
    "tv": 0.02,  # largest total-variation distance of a code / flag column
    "spike": 0.01,  # share of the dominant value (zero inflation, caps)
    "rho_mean": 0.02,  # mean |Spearman real - synthetic| over quantity pairs
    "rho_max": 0.10,
    "assoc_mean": 0.02,  # mean |Cramer's V real - synthetic| over code / flag pairs (target included)
    "assoc_max": 0.08,
    "eta_mean": 0.02,  # mean |correlation ratio| gap over code x quantity pairs
    "rule": 0.03,  # |violation share real - synthetic| of a dictionary rule
    "auc": 0.03,  # |single-column AUC real - synthetic| against the target
    "batch": 0.12,  # |block variance ratio real - synthetic|
    "runoff": 0.08,  # |event rate real - synthetic| at each number of zero balance / schedule columns
    "dcr": 0.9,  # median distance synthetic -> real / holdout real -> real (>= : not closer than real data is)
    "tstr": 0.03,  # AUC lost when training on synthetic instead of real rows
}


@dataclass
class Gate:
    name: str
    value: str
    limit: str
    ok: bool
    detail: str = ""


def quantity_cols(df: pd.DataFrame) -> list[str]:
    codes = set(code_columns(df))
    return [c for c in df.columns if c != TARGET and c not in codes]


def code_cols(df: pd.DataFrame) -> list[str]:
    """Codes and flags compared pairwise (the target included, the 2,393-level branch code left out)."""
    return [TARGET] + [c for c in code_columns(df) if c not in HIGH_CARDINALITY]


def tv_distance(a: pd.Series, b: pd.Series) -> float:
    pa, pb = a.value_counts(normalize=True), b.value_counts(normalize=True)
    idx = pa.index.union(pb.index)
    return float(0.5 * (pa.reindex(idx, fill_value=0) - pb.reindex(idx, fill_value=0)).abs().sum())


def marginal_gaps(real: pd.DataFrame, syn: pd.DataFrame) -> pd.DataFrame:
    rows = []
    codes = {TARGET, *code_columns(real)}
    for c in real.columns:
        if (
            c in HIGH_CARDINALITY
        ):  # thousands digit = region bucket; the 2,393 codes themselves are mostly sampling noise
            rows.append((c, "TV", tv_distance(real[c] // 1000, syn[c] // 1000)))
        elif c in codes:
            rows.append((c, "TV", tv_distance(real[c], syn[c])))
        else:
            rows.append((c, "KS", float(stats.ks_2samp(real[c].to_numpy(float), syn[c].to_numpy(float)).statistic)))
    return pd.DataFrame(rows, columns=["column", "metric", "gap"]).set_index("column")


def spike_gaps(real: pd.DataFrame, syn: pd.DataFrame) -> pd.Series:
    """Share of each quantity column's dominant value (zero inflation, caps and floors) in both tables."""
    out = {}
    for c in quantity_cols(real):
        top = real[c].value_counts().index[0]
        share = float((real[c] == top).mean())
        if share >= 0.05:
            out[c] = abs(share - float((syn[c] == top).mean()))
    return pd.Series(out, dtype=float)


def cramers_v(a: np.ndarray, b: np.ndarray) -> float:
    _, ia = np.unique(a, return_inverse=True)
    _, ib = np.unique(b, return_inverse=True)
    ka, kb = ia.max() + 1, ib.max() + 1
    if min(ka, kb) < 2:
        return 0.0
    t = np.bincount(ia * kb + ib, minlength=ka * kb).reshape(ka, kb).astype(float)
    e = np.outer(t.sum(1), t.sum(0)) / t.sum()
    chi2 = float(((t - e) ** 2 / np.where(e > 0, e, 1)).sum())
    return float(np.sqrt(chi2 / (t.sum() * (min(ka, kb) - 1))))


def correlation_ratio(codes: np.ndarray, z: np.ndarray) -> float:
    """eta: how much of a quantity (normal scores) is explained by a code column."""
    _, inv = np.unique(codes, return_inverse=True)
    n_k = np.bincount(inv)
    between = (np.bincount(inv, z) ** 2 / n_k).sum()
    return float(np.sqrt(between / (z**2).sum()))


def normal_scores(x: np.ndarray) -> np.ndarray:
    z = ndtri((stats.rankdata(x) - 0.5) / len(x))
    return z - z.mean()


def association_gaps(real: pd.DataFrame, syn: pd.DataFrame) -> dict[str, np.ndarray | list]:
    codes = code_cols(real)
    v_gap, v_pairs = [], []
    for i, a in enumerate(codes):
        for b in codes[i + 1 :]:
            v_gap.append(
                abs(cramers_v(real[a].to_numpy(), real[b].to_numpy()) - cramers_v(syn[a].to_numpy(), syn[b].to_numpy()))
            )
            v_pairs.append(f"{a} ~ {b}")
    e_gap, e_pairs = [], []
    zr = {c: normal_scores(real[c].to_numpy(float)) for c in quantity_cols(real)}
    zs = {c: normal_scores(syn[c].to_numpy(float)) for c in quantity_cols(real)}
    for a in codes:
        for c in zr:
            e_gap.append(
                abs(correlation_ratio(real[a].to_numpy(), zr[c]) - correlation_ratio(syn[a].to_numpy(), zs[c]))
            )
            e_pairs.append(f"{a} ~ {c}")
    return {"v": np.array(v_gap), "v_pairs": v_pairs, "eta": np.array(e_gap), "eta_pairs": e_pairs}


def auc_of(x: np.ndarray, y: np.ndarray) -> float:
    r = stats.rankdata(x)
    n1 = int(y.sum())
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1)))


def auc_gaps(real: pd.DataFrame, syn: pd.DataFrame) -> pd.DataFrame:
    rows = []
    yr, ys = real[TARGET].to_numpy(), syn[TARGET].to_numpy()
    for c in quantity_cols(real):
        rows.append((c, auc_of(real[c].to_numpy(float), yr), auc_of(syn[c].to_numpy(float), ys)))
    out = pd.DataFrame(rows, columns=["column", "AUC real", "AUC synthetic"]).set_index("column")
    out["gap"] = (out["AUC real"] - out["AUC synthetic"]).abs()
    return out


def privacy_check(real: pd.DataFrame, syn: pd.DataFrame, seed: int = 0, probes: int = 2000) -> dict[str, float]:
    """Distance to closest record: are synthetic rows closer to the real table than real rows are to each other?"""
    from sklearn.neighbors import NearestNeighbors

    cols = quantity_cols(real)
    rng = np.random.default_rng(seed)
    n = len(real)
    hold = rng.choice(n, size=min(probes, n // 5), replace=False)
    keep = np.ones(n, dtype=bool)
    keep[hold] = False
    sorted_cols = {c: np.sort(real[c].to_numpy(float)) for c in cols}

    def features(df: pd.DataFrame) -> np.ndarray:
        out = []
        for c in cols:
            s, x = sorted_cols[c], df[c].to_numpy(float)
            mid = (np.searchsorted(s, x, "left") + np.searchsorted(s, x, "right")) / 2 / len(s)
            out.append(ndtri(np.clip(mid, 0.5 / len(s), 1 - 0.5 / len(s))))
        return np.column_stack(out)

    x_real = features(real)
    probe = syn.iloc[rng.choice(len(syn), size=min(probes, len(syn)), replace=False)]
    nn = NearestNeighbors(n_neighbors=1, algorithm="brute", n_jobs=-1).fit(x_real[keep])
    d_real = nn.kneighbors(x_real[hold])[0][:, 0]
    d_syn = nn.kneighbors(features(probe))[0][:, 0]
    real_hashes = set(pd.util.hash_pandas_object(real, index=False))
    copies = int(pd.util.hash_pandas_object(syn, index=False).isin(real_hashes).sum())
    return {
        "median_ratio": float(np.median(d_syn) / np.median(d_real)),
        "p05_ratio": float(np.quantile(d_syn, 0.05) / np.quantile(d_real, 0.05)),
        "exact_copies": copies,
        "duplicates": int(syn.duplicated().sum()),
    }


def utility_check(real: pd.DataFrame, syn: pd.DataFrame, seed: int = 0) -> dict[str, float]:
    """Train on synthetic / test on real (TSTR) against train on real / test on real, same real test half."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    def encode(df: pd.DataFrame) -> pd.DataFrame:
        x = df.drop(columns=TARGET).copy()
        for c in x.columns:
            if x[c].dtype.kind not in "iuf":
                x[c] = pd.Categorical(x[c], categories=sorted(real[c].unique())).codes
        return x

    order = np.random.default_rng(seed).permutation(len(real))
    a, b = order[: len(real) // 2], order[len(real) // 2 :]
    test_x, test_y = encode(real.iloc[b]), real.iloc[b][TARGET].to_numpy()

    def auc(train: pd.DataFrame) -> float:
        m = HistGradientBoostingClassifier(max_iter=120, random_state=seed).fit(encode(train), train[TARGET])
        return float(roc_auc_score(test_y, m.predict_proba(test_x)[:, 1]))

    return {"real_on_real": auc(real.iloc[a]), "synthetic_on_real": auc(syn.iloc[: len(a)])}


def schema_problems(real: pd.DataFrame, syn: pd.DataFrame) -> list[str]:
    bad = []
    if list(real.columns) != list(syn.columns):
        bad.append("header differs")
    bad += [
        f"dtype of {c}: {syn[c].dtype} instead of {real[c].dtype}"
        for c in real.columns
        if c in syn and real[c].dtype != syn[c].dtype
    ]
    if syn.isna().any().any():
        bad.append("missing values")
    codes = {TARGET, *code_columns(real)}
    for c in real.columns:
        if c not in syn:
            continue
        if c in codes and not set(syn[c].unique()) <= set(real[c].unique()):
            bad.append(f"{c}: levels outside the real support")
        elif c not in codes and (syn[c].min() < real[c].min() or syn[c].max() > real[c].max()):
            bad.append(f"{c}: values outside the real range")
    return bad


def runoff_profile(real: pd.DataFrame, syn: pd.DataFrame, min_rows: int = 200) -> pd.DataFrame | None:
    """The EDA's 'run-off' signature: share of loans and event rate by how many of the balance / schedule columns
    are exactly 0 (counts that have at least ``min_rows`` real loans)."""
    cols = [c for c in ZERO_TOGETHER if c in real.columns]
    if len(cols) < 3:
        return None
    kr, ks = (real[cols] == 0).sum(axis=1), (syn[cols] == 0).sum(axis=1)
    out = pd.DataFrame(
        {
            "rows real": kr.value_counts(normalize=True),
            "rows synthetic": ks.value_counts(normalize=True),
            "event rate real": real.groupby(kr)[TARGET].mean(),
            "event rate synthetic": syn.groupby(ks)[TARGET].mean(),
        }
    ).fillna(0.0)
    out = out[out.index.map(lambda k: (kr == k).sum() >= min_rows)].sort_index()
    out.index.name = "zeros"
    out["event gap"] = (out["event rate real"] - out["event rate synthetic"]).abs()
    return out


def run_checks(
    real: pd.DataFrame, syn: pd.DataFrame, real_sizes: Sequence[int], syn_sizes: Sequence[int], full: bool = False
) -> tuple[list[Gate], dict[str, pd.DataFrame]]:
    gates: list[Gate] = []
    tables: dict[str, pd.DataFrame] = {}

    def add(name: str, value: float | str, limit: str, ok: bool, detail: str = "") -> None:
        gates.append(Gate(name, f"{value:.4f}" if isinstance(value, float) else str(value), limit, bool(ok), detail))

    problems = schema_problems(real, syn)
    add(
        "schema: header, dtypes, support, no missing",
        "ok" if not problems else f"{len(problems)} problems",
        "none",
        not problems,
        "; ".join(problems[:4]),
    )
    if problems:
        return gates, tables
    gap = abs(syn[TARGET].mean() - real[TARGET].mean())
    add(
        "event rate",
        float(syn[TARGET].mean()),
        f"{real[TARGET].mean():.4f} ± {1 / len(syn):.5f}",
        gap <= 1 / len(syn) + 1e-12,
    )

    mg = marginal_gaps(real, syn)
    tables["marginals"] = mg.sort_values("gap", ascending=False)
    for metric, key in (("KS", "ks"), ("TV", "tv")):
        sub = mg[mg["metric"] == metric]["gap"]
        add(
            f"marginals: max {metric}",
            float(sub.max()),
            f"≤ {LIMITS[key]}",
            sub.max() <= LIMITS[key],
            f"worst {sub.idxmax()}",
        )
    sp = spike_gaps(real, syn)
    add(
        "spikes: share of the dominant value",
        float(sp.max()),
        f"≤ {LIMITS['spike']}",
        sp.max() <= LIMITS["spike"],
        f"worst {sp.idxmax()}",
    )

    q = quantity_cols(real)
    rho = np.abs(real[q].corr(method="spearman").to_numpy() - syn[q].corr(method="spearman").to_numpy())
    iu = np.triu_indices(len(q), 1)
    worst = int(np.argmax(rho[iu]))
    add(
        "rank correlation: mean |Δρ|",
        float(rho[iu].mean()),
        f"≤ {LIMITS['rho_mean']}",
        rho[iu].mean() <= LIMITS["rho_mean"],
    )
    add(
        "rank correlation: max |Δρ|",
        float(rho[iu].max()),
        f"≤ {LIMITS['rho_max']}",
        rho[iu].max() <= LIMITS["rho_max"],
        f"{q[iu[0][worst]]} ~ {q[iu[1][worst]]}",
    )
    ag = association_gaps(real, syn)
    add(
        "codes & flags: mean |Δ Cramér's V|",
        float(ag["v"].mean()),
        f"≤ {LIMITS['assoc_mean']}",
        ag["v"].mean() <= LIMITS["assoc_mean"],
    )
    add(
        "codes & flags: max |Δ Cramér's V|",
        float(ag["v"].max()),
        f"≤ {LIMITS['assoc_max']}",
        ag["v"].max() <= LIMITS["assoc_max"],
        ag["v_pairs"][int(np.argmax(ag["v"]))],
    )
    add(
        "codes x quantities: mean |Δ eta|",
        float(ag["eta"].mean()),
        f"≤ {LIMITS['eta_mean']}",
        ag["eta"].mean() <= LIMITS["eta_mean"],
        ag["eta_pairs"][int(np.argmax(ag["eta"]))],
    )

    vr, vs = violation_rates(real), violation_rates(syn)
    rules = pd.DataFrame({"real": vr, "synthetic": vs}).assign(gap=lambda t: (t["real"] - t["synthetic"]).abs())
    tables["rules"] = rules
    add(
        "dictionary rules: max |Δ violation share|",
        float(rules["gap"].max()),
        f"≤ {LIMITS['rule']}",
        rules["gap"].max() <= LIMITS["rule"],
        str(rules["gap"].idxmax()),
    )

    auc = auc_gaps(real, syn)
    tables["auc"] = auc.sort_values("gap", ascending=False)
    add(
        "label signal: max |Δ single-column AUC|",
        float(auc["gap"].max()),
        f"≤ {LIMITS['auc']}",
        auc["gap"].max() <= LIMITS["auc"],
        str(auc["gap"].idxmax()),
    )

    runoff = runoff_profile(real, syn)
    if runoff is not None:
        tables["run-off"] = runoff
        worst = runoff["event gap"].max()
        add(
            "run-off signature: max |Δ event rate| by number of zero balance columns",
            float(worst),
            f"≤ {LIMITS['runoff']}",
            worst <= LIMITS["runoff"],
            f"at {int(runoff['event gap'].idxmax())} zeros",
        )

    if min(real_sizes) >= 4 * BATCH and min(syn_sizes) >= 4 * BATCH:
        bc = balance_columns(real)
        br, bs = block_variance_ratio(real, bc, real_sizes), block_variance_ratio(syn, bc, syn_sizes)
        add(
            "500-row blocks: median variance ratio",
            bs,
            f"{br:.3f} ± {LIMITS['batch']}",
            abs(br - bs) <= LIMITS["batch"],
        )

    if full:
        pr = privacy_check(real, syn)
        add(
            "privacy: median distance to closest real row / real-to-real",
            pr["median_ratio"],
            f"≥ {LIMITS['dcr']}",
            pr["median_ratio"] >= LIMITS["dcr"],
        )
        add(
            "privacy: exact copies of real rows / duplicates",
            f"{pr['exact_copies']} / {pr['duplicates']}",
            "0 / 0",
            pr["exact_copies"] == 0 and pr["duplicates"] == 0,
        )
        ut = utility_check(real, syn)
        add(
            "utility: AUC train-synthetic / test-real",
            ut["synthetic_on_real"],
            f"≥ {ut['real_on_real'] - LIMITS['tstr']:.4f} (real-on-real {ut['real_on_real']:.4f})",
            ut["synthetic_on_real"] >= ut["real_on_real"] - LIMITS["tstr"],
        )
    return gates, tables


def render_report(gates: list[Gate], tables: dict[str, pd.DataFrame], meta: dict) -> str:
    def md(t: pd.DataFrame, n: int | None = None) -> str:
        t = t.head(n) if n else t
        cols = [t.index.name or ""] + [str(c) for c in t.columns]
        lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for idx, row in t.iterrows():
            lines.append(
                "| " + " | ".join([str(idx)] + [f"{v:.4f}" if isinstance(v, float) else str(v) for v in row]) + " |"
            )
        return "\n".join(lines)

    ok = all(g.ok for g in gates)
    out = [
        "# Synthetic loan population: fidelity report",
        "",
        f"{meta.get('rows', '?'):,} synthetic loans, seed {meta.get('seed', '?')}, fitted on {meta.get('source', '?')}.",
        "Every row is new (no row of the real table is copied); the planted link between the label and the delinquency / "
        "score columns of the supplied data is reproduced, not removed.",
        "",
        "## Gates",
        "",
        "| check | measured | limit | result | detail |",
        "|---|---|---|---|---|",
        *[
            "| "
            + " | ".join(
                cell.replace("|", "\\|") for cell in (g.name, g.value, g.limit, "PASS" if g.ok else "FAIL", g.detail)
            )
            + " |"
            for g in gates
        ],
        "",
        f"**Overall: {'PASS' if ok else 'FAIL'}**",
    ]
    if "rules" in tables:
        out += ["", "## Dictionary rules the supplied data breaks (share of rows violating)", "", md(tables["rules"])]
    if "marginals" in tables:
        out += ["", "## Largest marginal gaps", "", md(tables["marginals"], 8)]
    if "run-off" in tables:
        out += ["", "## Run-off signature (zeros among the 8 balance / schedule columns)", "", md(tables["run-off"])]
    if "auc" in tables:
        out += ["", "## Largest single-column AUC gaps", "", md(tables["auc"], 8)]
    return "\n".join(out) + "\n"


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# command line
# ──────────────────────────────────────────────────────────────────────────────────────────────────
def say(msg: str = "") -> None:
    print(msg, file=sys.stderr, flush=True)


def print_gates(gates: list[Gate]) -> bool:
    for g in gates:
        say(
            f"  {'PASS' if g.ok else 'FAIL'}  {g.name:62s} {g.value:>10s}  (limit {g.limit}){'  ' + g.detail if g.detail and not g.ok else ''}"
        )
    ok = all(g.ok for g in gates)
    say(f"  Overall: {'PASS' if ok else 'FAIL'}")
    return ok


def cmd_generate(a: argparse.Namespace) -> int:
    real, sizes = read_loans(a.data_dir)
    t0 = time.time()
    say(f"fitting on {len(real):,} real loans from {a.data_dir} ...")
    model = Synthesizer(real, block_counts=sizes)
    say(f"  fitted in {time.time() - t0:.1f}s: {len(model.cells)} cells, {model.strata} stratified factors per batch")
    syn = model.sample(a.rows, seed=a.seed, event_rate=a.event_rate)
    meta = {
        "generator": "synthgen",
        "synthetic": True,
        "seed": a.seed,
        "source": shown(a.data_dir),
        "source_rows": len(real),
        "source_files": {p.name: sha256_of(p) for p in list_loan_files(a.data_dir)},
        "event_rate": float(syn[TARGET].mean()),
        "cells": {str(k): v.n for k, v in model.cells.items()},
        "strata": model.strata,
    }
    paths = write_loans(syn, a.out, a.rows_per_file, meta)
    say(f"wrote {len(syn):,} synthetic loans in {len(paths)} files to {a.out.resolve()}  ({time.time() - t0:.1f}s)")
    if a.no_check:
        return 0
    say("checking the written files against the real table ...")
    written, written_sizes = read_loans(a.out)  # re-read: also proves the CSV round trip keeps schema and dtypes
    gates, tables = run_checks(real, written, sizes, written_sizes, full=a.full_check)
    (a.out / REPORT).write_text(render_report(gates, tables, {**meta, "rows": len(syn)}))
    ok = print_gates(gates)
    say(f"report: {a.out / REPORT}")
    return 0 if ok else 1


def cmd_check(a: argparse.Namespace) -> int:
    real, sizes = read_loans(a.data_dir)
    syn, syn_sizes = read_loans(a.synthetic)
    gates, tables = run_checks(real, syn, sizes, syn_sizes, full=a.full)
    manifest = a.synthetic / MANIFEST
    meta = json.loads(manifest.read_text()) if manifest.exists() else {"rows": len(syn)}
    (a.synthetic / REPORT).write_text(render_report(gates, tables, meta))
    return 0 if print_gates(gates) else 1


def run_obmock(*args: str) -> int:
    """Run the unchanged obmock command line (bytecode is not written, so openbanking_mock/ stays untouched)."""
    say("$ python -m obmock " + " ".join(args))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "obmock", *args], cwd=OBMOCK_DIR, env=env).returncode


def cmd_bank(a: argparse.Namespace) -> int:
    """Bank API data for the synthetic loans: obmock's validate -> generate -> report, pointed at their folder."""
    data, out = a.synthetic.resolve(), a.out.resolve()
    if OBMOCK_DIR.resolve() in (out, *out.parents):
        raise SystemExit("--out must be outside openbanking_mock/ (this tool never writes there)")
    rows = sum(len(pd.read_csv(f, usecols=[TARGET])) for f in list_loan_files(data))
    if not rows:
        raise SystemExit(f"no synthetic loans in {data}: run `python synthetic/synthgen.py` first")
    if a.customers > rows:
        raise SystemExit(f"--customers {a.customers} exceeds the {rows} synthetic loans (one customer per loan row)")
    if out.exists() and any(out.iterdir()):
        made_by_obmock = (out / "MANIFEST.json").exists() and json.loads((out / "MANIFEST.json").read_text()).get(
            "generator"
        ) == "obmock"
        if not (a.overwrite and made_by_obmock):
            raise SystemExit(f"{out} is not empty: pass an empty --out, or --overwrite for a folder obmock wrote")
        shutil.rmtree(out)
    seed = ["--seed", str(a.seed)] if a.seed is not None else []
    if not a.skip_validate:
        code = run_obmock("validate", "--data-dir", str(data), "--customers", "240", *seed)
        if code:
            return code
    code = run_obmock("generate", "--data-dir", str(data), "--customers", str(a.customers), "--out", str(out), *seed)
    return code or run_obmock("report", "--dataset", str(out))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="synthgen", description="Synthetic loan population with the schema of nbg_synthetic_data/df_*.csv"
    )
    sub = p.add_subparsers(dest="cmd")
    g = sub.add_parser("generate", help="write new synthetic loans and check them (the default command)")
    g.add_argument("--rows", type=int, default=100_000, help="loans to generate (default 100,000, like nbg_synthetic_data/)")
    g.add_argument("--seed", type=int, default=SEED, help="same seed and data => identical files")
    g.add_argument("--data-dir", type=Path, default=DATA_DIR, help="the real df_*.csv the generator is fitted on")
    g.add_argument("--out", type=Path, default=OUT_DIR, help=f"output folder (default {OUT_DIR.relative_to(ROOT)})")
    g.add_argument("--rows-per-file", type=int, default=10_000)
    g.add_argument(
        "--event-rate", type=float, default=None, help="share of addNegStatus = 1 (default: as in the real data)"
    )
    g.add_argument(
        "--full-check", action="store_true", help="also privacy (closest real row) and train-synthetic / test-real"
    )
    g.add_argument("--no-check", action="store_true", help="skip the fidelity gates")
    g.set_defaults(fn=cmd_generate)
    c = sub.add_parser("check", help="fidelity gates for an existing synthetic folder")
    c.add_argument("--synthetic", type=Path, default=OUT_DIR)
    c.add_argument("--data-dir", type=Path, default=DATA_DIR)
    c.add_argument("--full", action="store_true", help="also privacy and train-synthetic / test-real")
    c.set_defaults(fn=cmd_check)
    b = sub.add_parser("bank", help="bank API data (openbanking_mock) for the synthetic loans")
    b.add_argument("--synthetic", type=Path, default=OUT_DIR)
    b.add_argument("--customers", type=int, default=2000, help="customers (one per synthetic loan; default 2,000)")
    b.add_argument("--out", type=Path, default=BANK_OUT_DIR)
    b.add_argument("--seed", type=int, default=None, help="obmock's seed (default: obmock's own)")
    b.add_argument("--skip-validate", action="store_true")
    b.add_argument("--overwrite", action="store_true", help="replace a non-empty --out that obmock itself wrote")
    b.set_defaults(fn=cmd_bank)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"generate", "check", "bank", "-h", "--help"}:
        argv = ["generate", *argv]
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
