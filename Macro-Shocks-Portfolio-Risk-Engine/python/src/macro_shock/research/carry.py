"""
research/carry.py

Yen carry trade monitor.

Investors borrow cheaply in yen and invest in higher-yielding assets. When
the yen strengthens fast, they must buy yen back to repay, selling what they
bought: in August 2024 that unwind hit the Nasdaq and bitcoin within days.

  Carry: 3-month rate differential US - Japan (monthly, OECD via FRED)
  Risk:  USDJPY realized volatility (20 days) and its percentile in 3 years
  Carry-to-risk: differential / volatility (high = crowded, attractive trade)
  Unwind signal: the yen gains 3% or more against the dollar in 10 trading days

The report also measures what the Nasdaq did in the 30 days after every past
unwind signal since 2000, against all 30-day periods, instead of assuming it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

SERIES = {"usdjpy": "DEXJPUS", "us3m": "IR3TIB01USM156N|TB3MS", "jp3m": "IR3TIB01JPM156N|IRSTCI01JPM156N"}
UNWIND_MOVE = -3.0        # % change of USDJPY in 10 trading days (negative = stronger yen)
UNWIND_DAYS = 10
VOL_DAYS = 20
AFTER_DAYS = 30           # calendar days
START = date(2000, 1, 1)


def fetch(fred, end: date) -> Dict[str, pd.Series]:
    out = {}
    for key, ids in SERIES.items():
        s = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        for sid in ids.split("|"):
            try:
                s = pd.Series(fred.get_series(sid, observation_start=str(START), observation_end=str(end)),
                              dtype=float).dropna().sort_index()
                s.index = pd.DatetimeIndex(s.index)
                break
            except Exception as e:
                logger.warning("carry_input_unavailable", series=sid, error=str(e))
        out[key] = s
    if out["usdjpy"].empty:
        raise ValueError("no USDJPY (DEXJPUS) data")
    return out


def unwind_dates(usdjpy: pd.Series, gap_days: int = AFTER_DAYS) -> List[pd.Timestamp]:
    """First day of each yen surge (USDJPY -3% in 10 trading days), at least ``gap_days`` apart."""
    move = (usdjpy / usdjpy.shift(UNWIND_DAYS) - 1) * 100
    hits, last = [], None
    for d in move[move <= UNWIND_MOVE].index:
        if last is None or (d - last).days > gap_days:
            hits.append(d)
            last = d
    return hits


def after(asset: pd.Series, dates: List[pd.Timestamp], days: int = AFTER_DAYS) -> pd.Series:
    """Asset return (%) over the ``days`` after each date."""
    out = {}
    for d in dates:
        start = asset[asset.index <= d]
        end = asset[asset.index <= d + pd.Timedelta(days=days)]
        if len(start) and len(end) and end.index[-1] > d + pd.Timedelta(days=days - 5):
            out[d] = float((end.iloc[-1] / start.iloc[-1] - 1) * 100)
    return pd.Series(out, dtype=float)


@dataclass
class Carry:
    usdjpy: pd.Series
    change_10d: float
    vol: float
    vol_pct: float
    differential: Optional[float]
    carry_to_risk: Optional[float]
    unwind_now: bool
    history: Dict[str, Dict[str, float]]    # asset -> {n, mean_after, median_after, hit_down, mean_all}
    last_unwinds: List[pd.Timestamp]


def assess(s: Dict[str, pd.Series], assets: Optional[Dict[str, pd.Series]] = None) -> Carry:
    fx = s["usdjpy"]
    move = (fx / fx.shift(UNWIND_DAYS) - 1) * 100
    vol = np.log(fx).diff().rolling(VOL_DAYS).std() * np.sqrt(252) * 100
    recent = vol[vol.index > vol.index[-1] - pd.DateOffset(years=3)].dropna()
    diff = None
    if len(s["us3m"]) and len(s["jp3m"]):
        diff = float(s["us3m"].iloc[-1] - s["jp3m"].iloc[-1])
    dates = unwind_dates(fx)
    history = {}
    for name, a in (assets or {}).items():
        if a is None or len(a) < 500:
            continue
        a = a.dropna()
        post = after(a, [d for d in dates if d >= a.index[0]])
        sample = a.iloc[:: 5]
        base = after(a, list(sample.index[:-6]))
        if len(post):
            history[name] = {"n": len(post), "mean_after": float(post.mean()), "median_after": float(post.median()),
                             "hit_down": float((post < 0).mean()), "mean_all": float(base.mean())}
    return Carry(fx, float(move.iloc[-1]), float(vol.iloc[-1]), float((recent <= vol.iloc[-1]).mean()),
                 diff, diff / float(vol.iloc[-1]) if diff is not None and vol.iloc[-1] > 0 else None,
                 bool(move.iloc[-1] <= UNWIND_MOVE), history, dates[-5:])


def alerts(c: Carry) -> List[str]:
    out = []
    if c.unwind_now:
        out.append(f"YEN CARRY UNWIND: the yen gained {abs(c.change_10d):.1f}% vs the dollar in 10 days "
                   "(August 2024 pattern): risk for Nasdaq and crypto")
    elif c.vol_pct >= 0.9:
        out.append(f"USDJPY volatility at the {c.vol_pct:.0%} percentile of 3 years: carry trade under stress")
    return out


def lines(c: Carry) -> List[str]:
    out = [f"  USDJPY {c.usdjpy.iloc[-1]:.2f} ({c.usdjpy.index[-1].date()}): {c.change_10d:+.1f}% in 10 days, "
           f"volatility {c.vol:.1f}% (percentile {c.vol_pct:.0%} of 3 years)"]
    if c.differential is not None:
        out.append(f"  Carry: 3-month rate gap US - Japan {c.differential:+.2f} pp; carry-to-risk {c.carry_to_risk:.2f}")
    for name, h in c.history.items():
        out.append(f"  After {h['n']} past yen surges: {name} {h['mean_after']:+.1f}% on average in 30 days "
                   f"(fell {h['hit_down']:.0%} of the time) vs {h['mean_all']:+.1f}% in any 30 days")
    return out
