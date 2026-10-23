"""
research/data_health.py

Is every series the report used fresh? For each one: last date, age, the
frequency inferred from its own spacing, and a status:

  ok       updated within its normal publication delay
  stale    older than that (the source may have stopped publishing it)
  failed   the fetch returned nothing (wrong id, source down or blocked)

No extra downloads: it inspects the series already fetched.
"""

from __future__ import annotations

from datetime import date
from typing import Dict

import pandas as pd

MAX_AGE = {"diaria": 7, "semanal": 16, "mensual": 75, "trimestral": 130}


def frequency(s: pd.Series) -> str:
    if len(s) < 3:
        return "trimestral"          # too few points to tell: the most lenient, no false alarms
    gap = pd.Series(s.index[-12:]).diff().dt.days.median()
    return "diaria" if gap <= 4 else "semanal" if gap <= 10 else "mensual" if gap <= 40 else "trimestral"


def check(sources: Dict[str, Dict[str, pd.Series]], today: date) -> pd.DataFrame:
    """One row per (source, series): last date, age in days, frequency and status."""
    rows = []
    for source, series in sources.items():
        for name, s in series.items():
            s = s.dropna() if s is not None else pd.Series(dtype=float)
            if s.empty:
                rows.append({"source": source, "series": name, "last": None, "age": None, "freq": "", "status": "failed"})
                continue
            last = pd.Timestamp(s.index[-1]).date()
            age = (today - last).days
            freq = frequency(s)
            rows.append({"source": source, "series": name, "last": last, "age": age, "freq": freq,
                         "status": "ok" if age <= MAX_AGE[freq] else "stale"})
    order = {"failed": 0, "stale": 1, "ok": 2}
    return pd.DataFrame(rows).sort_values(by=["status", "source"], key=lambda c: c.map(order) if c.name == "status" else c)
