#!/usr/bin/env python3
"""One person in, an income estimate out: registry row + raw open-banking data -> features -> P10 / P50 / P90.

The input is NOT a row of income_dataset.csv. It is what a bank would have for a new client:
  registry   one row with the columns of nbg_synthetic_data/df_*.csv (age, sex, bureau counts, RS income ...)
  open_banking   that client's raw API tables: transactions, accounts, balances (the columns of
                 openbanking_mock/output/dataset/api/*), possibly from several banks
The pipeline rebuilds exactly the row build_income_dataset.py makes (same aggregation code, window and FX rates),
then hands it to income_predictor.IncomeModel.

    from income_pipeline import predict_person, load_open_banking
    result = predict_person(registry_row, load_open_banking("C00001"))

    uv run python income/income_pipeline.py demo C00001      # registry from the dataset's truth, bank data from api/
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "eda"))

from EDA import MOCK_FEATURE_NAMES, scan_shard  # noqa: E402
from income_predictor import IncomeModel  # noqa: E402

API = HERE.parent / "openbanking_mock" / "output" / "dataset" / "api"
SHARD_SIZE = 250
TABLES = ("transactions", "accounts", "balances")
DATE_COLUMNS = ("booking_date", "value_date", "contract_start_date", "contract_end_date", "reference_date")
# Registry columns that are not inputs: the label, and the loan sized with income already known.
_PSU = "NEW"


def load_open_banking(cid: str, api_dir: Path = API) -> dict[str, pd.DataFrame]:
    """A person's raw API rows (all banks) out of the generated dataset: what the TPP would have received."""
    shard = (int(cid.lstrip("C")) - 1) // SHARD_SIZE
    name = f"part-{shard:05d}.parquet"
    out = {}
    for table in TABLES:
        frame = pd.read_parquet(api_dir / table / name)
        out[table] = frame[frame["psu_id"] == cid].reset_index(drop=True)
    return out


def _as_frame(obj) -> pd.DataFrame:
    return obj.copy() if isinstance(obj, pd.DataFrame) else pd.DataFrame(obj)


def open_banking_features(open_banking: dict | None, model: IncomeModel, as_of: str | None = None) -> dict:
    """ob_* aggregates of one person, computed by the same scan_shard that built income_dataset.csv."""
    window = model.state["ob_window"]
    end = pd.Timestamp(as_of or window["as_of"])
    cfg = {"as_of": end, "months": window["months"], "start": end - pd.DateOffset(months=window["months"]), "fx": window["fx"]}
    empty = {f"ob_{c}": np.nan for c in MOCK_FEATURE_NAMES}
    if not open_banking:
        return empty
    frames = {t: _as_frame(open_banking.get(t, [])) for t in TABLES}
    if frames["transactions"].empty and frames["accounts"].empty:
        return empty
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for table in TABLES:
            frame = frames[table]
            frame["psu_id"] = _PSU
            for col in DATE_COLUMNS:
                if col in frame:
                    frame[col] = pd.to_datetime(frame[col], errors="coerce")
            path = Path(tmp) / f"{table}.parquet"
            frame.to_parquet(path, index=False)
            paths.append(path)
        feats, _ = scan_shard((paths[0], paths[1], paths[2], [_PSU], cfg))
    if feats.empty:
        return empty
    row = feats.iloc[0]
    return {f"ob_{c}": row.get(c, np.nan) for c in MOCK_FEATURE_NAMES}


def banks_of(open_banking: dict | None) -> str:
    if not open_banking:
        return ""
    names: set[str] = set()
    for table in TABLES:
        frame = _as_frame(open_banking.get(table, []))
        if "bank" in frame:
            names |= set(frame["bank"].dropna().astype(str))
    return "|".join(sorted(names))


def build_row(registry: dict, open_banking: dict | None, model: IncomeModel, as_of: str | None = None) -> dict:
    """The raw row of income_dataset.csv for this person (everything before feature engineering)."""
    state = model.state
    row = {k: (np.nan if v is None else v) for k, v in dict(registry).items()}
    code, form = row.get("CLIENT_GEOGRAPHICAL_REGION"), row.get("CLIENT_LEGAL_FORM")
    if "region_name" not in row:
        row["region_name"] = state["region_by_code"].get(float(code), "unknown") if pd.notna(code) else "unknown"
    if "segment" not in row:
        row["segment"] = state["segment_by_legal_form"].get(float(form), "unknown") if pd.notna(form) else "unknown"
    row.setdefault("city", "unknown")  # the registry has no city; unseen levels become all-zero dummies
    row["banks"] = banks_of(open_banking) or row.get("banks", "")
    row.update(open_banking_features(open_banking, model, as_of))
    return row


def predict_person(
    registry: dict,
    open_banking: dict | None = None,
    model: IncomeModel | None = None,
    as_of: str | None = None,
) -> dict:
    """{'p10', 'p50', 'p90', 'band_share', 'confidence'} for one client."""
    model = model or IncomeModel.load()
    out = model.predict(build_row(registry, open_banking, model, as_of)).iloc[0]
    return {
        "p10": float(out["p10"]),
        "p50": float(out["p50"]),
        "p90": float(out["p90"]),
        "band_share": float(out["band_share"]),
        "confidence": str(out["confidence"]),
    }


def registry_of(cid: str) -> dict:
    """The df_* registry fields of a generated customer (as in nbg_synthetic_data/df_*.csv)."""
    shard = (int(cid.lstrip("C")) - 1) // SHARD_SIZE
    truth = HERE.parent / "openbanking_mock" / "output" / "dataset" / "truth" / "customers" / f"part-{shard:05d}.parquet"
    person = pd.read_parquet(truth).set_index("cid").loc[cid]
    return {c.removeprefix("df_"): person[c] for c in person.index if c.startswith("df_")}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "demo":
        print(__doc__)
        return 1
    cid = argv[1]
    registry = registry_of(cid)
    result = predict_person(registry, load_open_banking(cid))
    print(f"{cid}  actual (registry label) {registry['INCOME_AMOUNT_TOTAL']:.0f} GEL")
    print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in result.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
