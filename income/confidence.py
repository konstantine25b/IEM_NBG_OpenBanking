"""Confidence of an estimate, read straight off the width of its P10-P90 band.

    band_share = (p90 - p10) / p50          how wide the band is, as a share of the estimate
    score      = 100% - band_share          100% = a point estimate, 0% = a band as wide as the estimate itself
    level      = fixed cut-offs on band_share, so a label means the same at every income level

The score is a measure of precision, not a probability: how often the real income falls inside the band is measured
separately (confidence_calibration.py) and shown beside it.
"""

from __future__ import annotations

# (label, largest band_share that still gets it); anything wider is LOWEST
CUTS = (("very high", 0.03), ("high", 0.06), ("medium", 0.10), ("low", 0.20))
LOWEST = "very low"
LEVELS = [name for name, _ in CUTS] + [LOWEST]


def level_of(band_share: float) -> str:
    return next((name for name, cut in CUTS if band_share <= cut), LOWEST)


def score_of(band_share: float) -> int:
    return max(0, min(100, round(100 * (1 - band_share))))


def scale() -> list[dict]:
    """The labels with their limits, for display."""
    return [{"level": name, "max_band_pct": round(100 * cut)} for name, cut in CUTS] + [
        {"level": LOWEST, "max_band_pct": None}
    ]
