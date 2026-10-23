"""
research/track_record.py

The engine's own track record: every report logs its forecasts to
reports/predicciones.csv, and later reports score the ones whose outcome is
known. After a few months this says how far to trust each piece.

  cpi       CPI nowcast (headline m/m and its 90% range) -> the published CPI
  analogs   median 6-month market return of the historical analogs -> S&P 500
  model     the predictive model's 3-month forecast -> S&P 500

S&P 500 outcomes are price returns from FRED; the forecasts are total or
excess returns, so a difference of about the dividend yield is expected.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

COLUMNS = ["run_date", "kind", "target", "due", "value", "low", "high"]


def read(path: Path) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path, dtype={"run_date": str, "target": str, "due": str})


def record(path: Path, rows: List[Dict[str, object]]) -> pd.DataFrame:
    """Add today's forecasts, replacing any from an earlier run the same day."""
    log = read(path)
    new = pd.DataFrame(rows, columns=COLUMNS)
    if len(new):
        keys = set(zip(new["run_date"], new["kind"], new["target"]))
        log = log[[k not in keys for k in zip(log["run_date"], log["kind"], log["target"])]]
        log = pd.concat([log, new], ignore_index=True)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    log[COLUMNS].to_csv(path, index=False)
    return log


def forecasts(run_date: date, nowcast=None, analogs=None, model=None) -> List[Dict[str, object]]:
    rows = []
    if nowcast is not None and nowcast.months:
        m = nowcast.months[0]
        t = nowcast.table[(nowcast.table["month"] == m) & (nowcast.table["item"] == "headline")]
        if len(t):
            r = t.iloc[0]
            rows.append({"run_date": run_date.isoformat(), "kind": "cpi", "target": m.strftime("%Y-%m"),
                         "due": (m + pd.DateOffset(months=1, days=20)).date().isoformat(),
                         "value": r["mm"], "low": r["low"], "high": r["high"]})
    if analogs is not None and len(analogs.table):
        rows.append({"run_date": run_date.isoformat(), "kind": "analogs", "target": "sp500_6m",
                     "due": (pd.Timestamp(run_date) + pd.DateOffset(months=6)).date().isoformat(),
                     "value": float(analogs.table["mkt_6m"].median()), "low": np.nan, "high": np.nan})
    if model is not None:
        rows.append({"run_date": run_date.isoformat(), "kind": "model", "target": "sp500_3m",
                     "due": (pd.Timestamp(run_date) + pd.DateOffset(months=3)).date().isoformat(),
                     "value": model.forecast, "low": np.nan, "high": np.nan})
    return rows


def _sp500_return(sp500: pd.Series, start: str, months: int) -> Optional[float]:
    a = sp500[sp500.index <= pd.Timestamp(start)]
    b = sp500[sp500.index <= pd.Timestamp(start) + pd.DateOffset(months=months)]
    if a.empty or b.empty or b.index[-1] < pd.Timestamp(start) + pd.DateOffset(months=months) - pd.Timedelta(days=5):
        return None
    return float((b.iloc[-1] / a.iloc[-1] - 1) * 100)


def score(log: pd.DataFrame, cpi_level: Optional[pd.Series], sp500: Optional[pd.Series]) -> pd.DataFrame:
    """Outcome, error and hit for every forecast whose outcome is known."""
    rows = []
    cpi_mm = None
    if cpi_level is not None and len(cpi_level):
        lv = cpi_level.copy()
        lv.index = lv.index.to_period("M")
        cpi_mm = (lv / lv.shift(1) - 1) * 100
    for _, r in log.iterrows():
        actual = None
        if r["kind"] == "cpi" and cpi_mm is not None:
            p = pd.Period(r["target"], "M")
            actual = float(cpi_mm[p]) if p in cpi_mm.index and pd.notna(cpi_mm[p]) else None
        elif r["kind"] in ("analogs", "model") and sp500 is not None:
            actual = _sp500_return(sp500, r["run_date"], 6 if r["kind"] == "analogs" else 3)
        if actual is None:
            continue
        inside = (r["low"] <= actual <= r["high"]) if pd.notna(r["low"]) else np.nan
        rows.append({**r.to_dict(), "actual": actual, "error": actual - r["value"],
                     "same_sign": float(np.sign(actual) == np.sign(r["value"])), "inside": inside})
    return pd.DataFrame(rows)


def summary(scored: pd.DataFrame, log: pd.DataFrame) -> List[str]:
    if log.empty:
        return ["Aún no hay previsiones registradas: cada informe guarda las suyas."]
    pending = len(log) - len(scored)
    out = [f"{len(log)} previsiones registradas, {len(scored)} ya evaluables, {pending} pendientes."]
    for kind, label in (("cpi", "Nowcast del IPC"), ("analogs", "Análogos (bolsa a 6 meses)"),
                        ("model", "Modelo (bolsa a 3 meses)")):
        g = scored[scored["kind"] == kind] if len(scored) else scored
        if len(g):
            extra = f"; dentro del rango 90%: {g['inside'].mean():.0%}" if kind == "cpi" else \
                f"; signo acertado: {g['same_sign'].mean():.0%}"
            out.append(f"{label}: {len(g)} evaluadas, error medio absoluto {g['error'].abs().mean():.2f} pts{extra}.")
    return out
