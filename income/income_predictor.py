#!/usr/bin/env python3
"""Train, save and use the income model (same recipe as income_model.ipynb).

    uv run python income/income_predictor.py train                 # -> income/models/income_model.joblib
    uv run python income/income_predictor.py predict row.json      # one raw row (JSON object) or a list of them
    uv run python income/income_predictor.py predict people.csv    # raw rows, same columns as income_dataset.csv

    from income_predictor import IncomeModel
    model = IncomeModel.load()
    model.predict({"Age": 35, "SEX": 1, "region_name": "Tbilisi", "banks": "tbc|bog", ...})

Input is a raw row of nbg_synthetic_data/income_dataset.csv, before any feature engineering. Columns the model
does not use (loan terms, arrears, cid, the label) are ignored; absent model columns count as "not reported".
Output per row: p10 / p50 / p90 monthly income in GEL, band_share = (p90 - p10) / p50, and a confidence level.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parent
DATA = REPO / "nbg_synthetic_data" / "income_dataset.csv"
MODEL_PATH = HERE / "models" / "income_model.joblib"

SEED = 20261020
LABEL = "INCOME_AMOUNT_TOTAL"
FILL_ZERO = ["ob_salary_month", "ob_card_ticket"]
ONE_HOT = ["segment", "region_name", "city", "CLIENT_LEGAL_FORM", "SEX", "GeoCitizen"]
PERSON_NUM = ["Age", "INCOME_AMOUNT_SOI04"]
BUREAU = [
    "last1Month",
    "last3Month",
    "last6Month",
    "last12Month",
    "ExistingOperationsDebtor",
    "TerminatedOperationsDebtor",
]
CONFIDENCE_LEVELS = ["very high", "high", "medium", "low", "very low"]  # narrowest band -> widest


def _clean_names(cols: pd.Index) -> pd.Index:
    return cols.str.replace(r"[^0-9A-Za-z_]+", "_", regex=True).str.strip("_")


class IncomeModel:
    """Preprocessing state + XGBoost quantile model (P10 / P50 / P90)."""

    def __init__(self, booster: xgb.XGBRegressor, state: dict):
        self.booster = booster
        self.state = state

    # ---- feature engineering: raw row -> model matrix ---------------------------------------------------------
    @staticmethod
    def _bank_flags(banks: pd.Series, names: list[str]) -> pd.DataFrame:
        found = banks.fillna("").astype(str).str.get_dummies(sep="|").add_prefix("bank_")
        return found.reindex(columns=names, fill_value=0)

    def transform(self, raw: pd.DataFrame) -> pd.DataFrame:
        s = self.state
        df = raw.copy()
        for col in s["null_cols"]:
            if col not in df:
                df[col] = np.nan
            df[f"{col}_missing"] = df[col].isna().astype(np.uint8)
        for col in FILL_ZERO:
            df[col] = df[col].fillna(0.0)
        if "banks" not in df:
            df["banks"] = ""
        bank = self._bank_flags(df["banks"], s["bank_cols"])
        df = pd.concat([df.reset_index(drop=True), bank.reset_index(drop=True)], axis=1)
        for col in s["numeric_cols"] + ONE_HOT:
            if col not in df:
                df[col] = np.nan

        parts = [df[s["numeric_cols"]].reset_index(drop=True)]
        for col, levels in s["levels"].items():
            names = [f"{col}_{lv}" for lv in levels]
            dummies = pd.get_dummies(df[col].astype(str), prefix=col).reindex(columns=names, fill_value=0)
            parts.append(dummies)
        X = pd.concat(parts, axis=1)
        X.columns = _clean_names(X.columns)
        return X.reindex(columns=s["feature_names"]).astype(np.float32)

    # ---- prediction -------------------------------------------------------------------------------------------
    def prepare(self, data: dict | list[dict] | pd.DataFrame) -> pd.DataFrame:
        """Raw rows as a frame, ready for transform()."""
        if isinstance(data, dict):
            data = [data]
        raw = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        # JSON nulls arrive as None; make them real NaN so "not reported" is handled like in training.
        raw = raw.astype(object).where(raw.notna(), np.nan)
        for col in self.state["numeric_cols"]:
            if col in raw:
                raw[col] = pd.to_numeric(raw[col], errors="coerce")
        return raw

    def predict(self, data: dict | list[dict] | pd.DataFrame) -> pd.DataFrame:
        raw = self.prepare(data)
        X = self.transform(raw)
        q = np.sort(self.booster.predict(X), axis=1)
        p10, p50, p90 = (np.clip(q[:, i], 0, None) for i in range(3))
        band_share = (p90 - p10) / np.clip(p50, 1, None)
        edges = self.state["confidence_edges"]
        level = np.searchsorted(edges, band_share)  # 0 = narrowest
        out = pd.DataFrame(
            {
                "p10": p10.round(0),
                "p50": p50.round(0),
                "p90": p90.round(0),
                "band_share": band_share.round(3),
                "confidence": [CONFIDENCE_LEVELS[i] for i in level],
            }
        )
        if "cid" in raw:
            out.insert(0, "cid", raw["cid"].to_numpy())
        return out

    # ---- persistence ------------------------------------------------------------------------------------------
    def save(self, path: Path = MODEL_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"booster": self.booster, "state": self.state}, path)
        return path

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> IncomeModel:
        blob = joblib.load(path)
        return cls(blob["booster"], blob["state"])


# ---- training -------------------------------------------------------------------------------------------------
def _make(depth: int, n_estimators: int, early: int | None) -> xgb.XGBRegressor:
    return xgb.XGBRegressor(
        objective="reg:quantileerror",
        quantile_alpha=[0.1, 0.5, 0.9],
        tree_method="hist",
        max_depth=depth,
        learning_rate=0.08,
        n_estimators=n_estimators,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        reg_lambda=1.0,
        early_stopping_rounds=early,
        random_state=SEED,
        n_jobs=4,
        verbosity=0,
    )


def split_indices(y: np.ndarray) -> tuple[np.ndarray, ...]:
    """(train, test, fit, val) row positions: 80/20 by person, stratified on income deciles; val is 10% of train."""
    decile = pd.qcut(y, 10, labels=False, duplicates="drop")
    train_idx, test_idx = train_test_split(np.arange(len(y)), test_size=0.2, random_state=SEED, stratify=decile)
    fit_idx, val_idx = train_test_split(train_idx, test_size=0.1, random_state=SEED, stratify=decile[train_idx])
    return train_idx, test_idx, fit_idx, val_idx


def _ob_window() -> dict:
    """Window and FX rates the open-banking aggregates in income_dataset.csv were computed over."""
    from build_income_dataset import ROOT, window_of

    w = window_of(ROOT)
    return {"as_of": w["as_of"].isoformat(), "months": w["months"], "fx": {k: float(v) for k, v in w["fx"].items()}}


def train(data_path: Path = DATA) -> IncomeModel:
    df = pd.read_csv(data_path)
    y = df[LABEL].to_numpy(float)
    ob = [c for c in df.columns if c.startswith("ob_")]
    null_cols = [c for c in df.columns if df[c].isna().any()]
    bank_cols = list(df["banks"].str.get_dummies(sep="|").add_prefix("bank_").columns)
    numeric_cols = (
        PERSON_NUM + BUREAU + ob + [f"{c}_missing" for c in null_cols] + bank_cols
    )
    state = {"null_cols": null_cols, "bank_cols": bank_cols, "numeric_cols": numeric_cols}
    # Used by income_pipeline.py to rebuild the columns a registry row lacks, and to aggregate raw bank data
    # over the same window and FX rates as income_dataset.csv.
    state["region_by_code"] = {
        float(k): v for k, v in df.groupby("CLIENT_GEOGRAPHICAL_REGION")["region_name"].agg(lambda s: s.mode()[0]).items()
    }
    state["segment_by_legal_form"] = {
        float(k): v for k, v in df.groupby("CLIENT_LEGAL_FORM")["segment"].agg(lambda s: s.mode()[0]).items()
    }
    state["ob_window"] = _ob_window()

    # Build a placeholder model only to reuse transform(); levels and feature names are fixed on the train rows.
    shell = IncomeModel(None, {**state, "levels": {}, "feature_names": []})
    train_idx, test_idx, fit_idx, val_idx = split_indices(y)

    shell.state["levels"] = {c: sorted(df.iloc[train_idx][c].astype(str).unique()) for c in ONE_HOT}
    shell.state["feature_names"] = list(
        _clean_names(
            pd.Index(numeric_cols + [f"{c}_{lv}" for c, lvs in shell.state["levels"].items() for lv in lvs])
        )
    )
    X = shell.transform(df)

    best_depth, best_trees, best_mae = 0, 0, np.inf
    for depth in (4, 6, 8):
        cand = _make(depth, 400, 40)
        cand.fit(X.iloc[fit_idx], y[fit_idx], eval_set=[(X.iloc[val_idx], y[val_idx])], verbose=False)
        pred = np.sort(cand.predict(X.iloc[val_idx]), axis=1)
        mae = float(np.mean(np.abs(y[val_idx] - pred[:, 1])))
        print(f"depth {depth}: {int(cand.best_iteration) + 1} trees, validation MAE {mae:.1f} GEL")
        if mae < best_mae:
            best_depth, best_trees, best_mae = depth, int(cand.best_iteration) + 1, mae

    model = _make(best_depth, best_trees, None)
    model.fit(X.iloc[train_idx], y[train_idx], verbose=False)

    q = np.sort(model.predict(X.iloc[test_idx]), axis=1)
    p10, p50, p90 = q[:, 0], q[:, 1], q[:, 2]
    yt = y[test_idx]
    band = (p90 - p10) / np.clip(p50, 1, None)
    shell.state["confidence_edges"] = np.quantile(band, [0.2, 0.4, 0.6, 0.8]).tolist()
    print(
        f"test: MAE {np.mean(np.abs(yt - p50)):.1f} GEL, "
        f"inside P10-P90 {100 * np.mean((yt >= p10) & (yt <= p90)):.1f}%"
    )
    shell.booster = model
    return shell


def _read_input(arg: str) -> pd.DataFrame | dict | list:
    path = Path(arg)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return json.loads(path.read_text() if path.exists() else arg)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"train", "predict"}:
        print(__doc__)
        return 1
    if argv[0] == "train":
        print(f"saved {train().save()}")
        return 0
    if len(argv) < 2:
        print("predict needs a .csv file, a .json file, or a JSON string")
        return 1
    print(IncomeModel.load().predict(_read_input(argv[1])).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
