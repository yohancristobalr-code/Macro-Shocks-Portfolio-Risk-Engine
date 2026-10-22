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

from typing import Dict, Optional, Tuple

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


def window_returns(
    asset_daily: pd.DataFrame,
    regimes: pd.Series,
    market_daily: Optional[pd.Series] = None,
    horizon: int = 21,
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Forward return (%) of each asset over every non-overlapping window, and the
    regime on each window's first day. With ``market_daily`` returns are in
    excess of the market; without it they are raw (long-short style factors).
    Windows without a regime label or complete data are dropped.
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
    return fwd[keep], labels[keep]


def summarize(fwd: pd.DataFrame, labels: pd.Series, min_windows: int = 8) -> pd.DataFrame:
    """Mean, hit rate and 95% bootstrap interval per (regime, asset)."""
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
    return pd.DataFrame(rows, columns=["regime", "asset", "n", "mean_pct", "hit_rate", "ci_low", "ci_high"])


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
    fwd, labels = window_returns(asset_daily, regimes, market_daily, horizon)
    return summarize(fwd, labels, min_windows)


def _excludes_zero(lo: float, hi: float) -> bool:
    return bool((lo > 0) or (hi < 0))


def split_robustness(
    fwd: pd.DataFrame, labels: pd.Series, split: pd.Timestamp, min_windows: int = 8,
) -> pd.DataFrame:
    """
    Full-sample summary plus each half's mean and an ``evidence`` grade:

    - "robust":      interval excludes zero in the full sample and in each half,
                     with the same sign throughout
    - "consistent":  excludes zero in the full sample; both halves share its sign
    - "unstable":    excludes zero in the full sample, but a half has the other sign
    - "":            no clear full-sample result, or too few windows in a half
    """
    full = summarize(fwd, labels, min_windows)
    early = fwd.index <= split
    halves = [summarize(fwd[mask], labels[mask], min_windows) for mask in (early, ~early)]
    for i, half in enumerate(halves, start=1):
        half = half.rename(columns={c: f"{c}_h{i}" for c in ("n", "mean_pct", "ci_low", "ci_high")})
        full = full.merge(half[["regime", "asset", f"n_h{i}", f"mean_pct_h{i}", f"ci_low_h{i}", f"ci_high_h{i}"]],
                          on=["regime", "asset"], how="left")

    def grade(r) -> str:
        if not _excludes_zero(r["ci_low"], r["ci_high"]):
            return ""
        if any(not (r.get(f"n_h{i}", 0) >= min_windows) for i in (1, 2)):
            return ""
        sign = np.sign(r["mean_pct"])
        same = all(np.sign(r[f"mean_pct_h{i}"]) == sign for i in (1, 2))
        if not same:
            return "unstable"
        both_clear = all(
            _excludes_zero(r[f"ci_low_h{i}"], r[f"ci_high_h{i}"]) for i in (1, 2)
        )
        return "robust" if both_clear else "consistent"

    full["evidence"] = full.apply(grade, axis=1) if len(full) else pd.Series(dtype=object)
    return full


def suggestions(table: pd.DataFrame, regime: str, top: int = 3) -> Dict[str, pd.DataFrame]:
    """
    Best and worst assets for ``regime``, each flagged ``significant`` when its
    95% interval excludes zero.
    """
    t = table[table["regime"] == regime].copy()
    t["significant"] = (t["ci_low"] > 0) | (t["ci_high"] < 0)
    t = t.sort_values("mean_pct", ascending=False)
    return {"best": t.head(top), "worst": t.tail(top).iloc[::-1]}
