"""
research/sector_regimes.py

Which sectors and styles did best after each market regime?

For every non-overlapping ``horizon``-day window, the regime on the window's
first day is matched with each asset's return over the window. Sector returns
are measured relative to the market (excess return), so a sector "wins" in a
regime when it beat the market there; style factors are already long-short and
are used as they are.

The output is a historical association, not a forecast: a regime in which a
sector beat the market in the past says nothing certain about the next window.
Intervals are percentile bootstraps, so they make no normality assumption.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def forward_returns(daily: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Compounded return (%) over the next ``horizon`` days, starting the day after each date."""
    growth = (1 + daily.fillna(0)).cumprod()
    return (growth.shift(-horizon) / growth - 1) * 100


def window_starts(index: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    """Every ``horizon``-th date, so consecutive windows never overlap."""
    return index[::horizon]


def _bootstrap_mean_ci(values: np.ndarray, n_boot: int = 2000, seed: int = 0):
    idx = np.random.default_rng(seed).integers(0, len(values), size=(n_boot, len(values)))
    lo, hi = np.percentile(values[idx].mean(axis=1), [2.5, 97.5])
    return float(lo), float(hi)


def regime_performance(
    asset_daily: pd.DataFrame,
    regimes: pd.Series,
    market_daily: Optional[pd.Series] = None,
    horizon: int = 21,
    min_windows: int = 8,
) -> pd.DataFrame:
    """
    Mean forward return per (regime, asset), with hit rate and 95% interval.

    ``asset_daily`` holds decimal daily returns. With ``market_daily`` the
    returns are measured in excess of the market; without it they are raw
    (for long-short style factors). ``regimes`` labels each date; dates without
    a label are skipped. Pairs with fewer than ``min_windows`` windows get no
    interval.
    """
    dates = asset_daily.index
    if market_daily is not None:
        dates = dates.intersection(market_daily.index)
    asset_daily = asset_daily.loc[dates]
    fwd = forward_returns(asset_daily, horizon)
    if market_daily is not None:
        mkt = forward_returns(market_daily.loc[dates].to_frame(), horizon).iloc[:, 0]
        fwd = fwd.sub(mkt, axis=0)

    starts = window_starts(dates, horizon)
    labels = regimes.reindex(starts)
    fwd = fwd.loc[starts]
    keep = labels.notna() & fwd.notna().all(axis=1)
    fwd, labels = fwd[keep], labels[keep]

    rows = []
    for regime in labels.unique():
        block = fwd[labels == regime]
        for asset in fwd.columns:
            vals = block[asset].to_numpy(dtype=float)
            lo, hi = _bootstrap_mean_ci(vals) if len(vals) >= min_windows else (np.nan, np.nan)
            rows.append({
                "regime": regime,
                "asset": asset,
                "n": len(vals),
                "mean_pct": float(vals.mean()),
                "hit_rate": float((vals > 0).mean()),
                "ci_low": lo,
                "ci_high": hi,
            })
    return pd.DataFrame(rows)


def suggestions(table: pd.DataFrame, regime: str, top: int = 3) -> Dict[str, pd.DataFrame]:
    """
    Best and worst assets for ``regime``, each flagged ``significant`` when its
    95% interval excludes zero.
    """
    t = table[table["regime"] == regime].copy()
    t["significant"] = (t["ci_low"] > 0) | (t["ci_high"] < 0)
    t = t.sort_values("mean_pct", ascending=False)
    return {"best": t.head(top), "worst": t.tail(top).iloc[::-1]}
