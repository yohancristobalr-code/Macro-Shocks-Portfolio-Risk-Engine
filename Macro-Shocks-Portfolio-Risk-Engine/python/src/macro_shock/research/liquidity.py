"""
research/liquidity.py

Fed liquidity and rate expectations, from FRED.

Net liquidity = Fed balance sheet (WALCL) - Treasury General Account at the
Fed (WTREGEN) - overnight reverse repos (RRPONTSYD): the dollars the Fed has
created that are actually circulating in markets. It tends to move with
risk assets that depend on liquidity (Nasdaq, bitcoin); the report measures
how closely over the last years instead of assuming it.

Rate expectations: 2Y Treasury minus the effective fed funds rate (DFF).
Positive = the market expects hikes over the next two years; negative = cuts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

SERIES = {"walcl": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD", "y2": "DGS2", "ffr": "DFF"}
YEARS = 6


def fetch(fred, end: date, years: int = YEARS) -> Dict[str, pd.Series]:
    out = {}
    for key, sid in SERIES.items():
        try:
            s = pd.Series(fred.get_series(sid, observation_start=str(end - timedelta(days=365 * years)),
                                          observation_end=str(end)), dtype=float).dropna().sort_index()
            s.index = pd.DatetimeIndex(s.index)
        except Exception as e:
            logger.warning("liquidity_input_unavailable", series=sid, error=str(e))
            s = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        out[key] = s
    return out


def net_liquidity(s: Dict[str, pd.Series]) -> pd.Series:
    """Weekly net liquidity in billions of dollars, on the balance sheet's (Wednesday) dates."""
    walcl = s["walcl"] / 1000                                   # millions -> billions
    if walcl.empty:
        return walcl
    parts = [walcl]
    for key, scale in (("tga", 1000), ("rrp", 1)):              # FRED units: WTREGEN millions, RRPONTSYD billions
        x = s[key] / scale
        parts.append(x.reindex(walcl.index.union(x.index)).ffill().reindex(walcl.index) if len(x) else 0 * walcl)
    return (parts[0] - parts[1] - parts[2]).dropna()


def expectations(s: Dict[str, pd.Series]) -> pd.Series:
    """2Y yield minus effective fed funds, bps."""
    if s["y2"].empty or s["ffr"].empty:
        return pd.Series(dtype=float)
    j = pd.concat([s["y2"], s["ffr"]], axis=1, keys=["y2", "ffr"]).ffill().dropna()
    return (j["y2"] - j["ffr"]) * 100


@dataclass
class Liquidity:
    net: pd.Series
    expect: pd.Series
    change_4w: Optional[float]
    change_13w: Optional[float]
    links: Dict[str, float]          # correlation of 13-week changes with each asset


def _chg(s: pd.Series, weeks: int) -> Optional[float]:
    past = s[s.index <= s.index[-1] - pd.Timedelta(weeks=weeks)]
    return float(s.iloc[-1] - past.iloc[-1]) if len(past) else None


def link(net: pd.Series, asset: pd.Series, weeks: int = 13) -> Optional[float]:
    """Correlation of non-overlapping 13-week changes: net liquidity (bn) vs the asset (%), last years."""
    if net.empty or asset is None or len(asset) < 100:
        return None
    a = asset.reindex(net.index.union(asset.index)).ffill().reindex(net.index)
    step = net.iloc[::weeks]
    x = step.diff()
    y = np.log(a.iloc[::weeks]).diff() * 100
    j = pd.concat([x, y], axis=1).dropna()
    return float(j.corr().iloc[0, 1]) if len(j) >= 8 else None


def assess(s: Dict[str, pd.Series], assets: Optional[Dict[str, pd.Series]] = None) -> Liquidity:
    net = net_liquidity(s)
    if net.empty:
        raise ValueError("no Fed balance sheet data (WALCL)")
    links = {}
    for name, series in (assets or {}).items():
        c = link(net, series)
        if c is not None:
            links[name] = c
    return Liquidity(net, expectations(s), _chg(net, 4), _chg(net, 13), links)


def lines(liq: Liquidity) -> List[str]:
    f = lambda v: "n/a" if v is None else f"{v:+,.0f}bn"
    out = [f"  Fed net liquidity {liq.net.iloc[-1] / 1000:,.2f}tn ({liq.net.index[-1].date()}): "
           f"{f(liq.change_4w)} in 4 weeks, {f(liq.change_13w)} in 13 weeks"]
    if len(liq.expect):
        e = liq.expect.iloc[-1]
        out.append(f"  Rate expectations (2Y - fed funds): {e:+.0f} bps -> market prices "
                   + ("hikes" if e > 15 else "cuts" if e < -15 else "rates on hold"))
    if liq.links:
        out.append("  Link to liquidity (corr. of 13-week changes, last 6 years): "
                   + ", ".join(f"{k} {v:+.2f}" for k, v in liq.links.items()))
    return out
