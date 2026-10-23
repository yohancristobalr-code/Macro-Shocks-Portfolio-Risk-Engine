"""
research/valuation.py

US equity valuation from FRED alone (no paid earnings data):

  Market value of corporate equities  NCBEILQ027S (Fed Z.1, quarterly, millions $),
                                       carried to today with the S&P 500
  Corporate profits after tax          CP (quarterly, billions $, annual rate)
  GDP                                  GDP (quarterly, billions $, annual rate)

  P/E proxy            market value / profits
  Buffett indicator    market value / GDP (%)
  Risk premium proxy   profits / market value (earnings yield) - 10Y real yield (DFII10)

Each comes with its percentile since 1951 and with what the US market (French
Data Library) returned per year over the next 5 years when valuation was in
the same fifth of its history: a measured link, not a target.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional

import numpy as np
import pandas as pd

from macro_shock.research import french

SERIES = {"equities": "NCBEILQ027S", "profits": "CP", "gdp": "GDP", "real10": "DFII10"}
START = "1951-01-01"


def fetch(fred, end: date) -> Dict[str, pd.Series]:
    out = {}
    for key, sid in SERIES.items():
        s = pd.Series(fred.get_series(sid, observation_start=START, observation_end=str(end)), dtype=float).dropna()
        s.index = pd.DatetimeIndex(s.index)
        out[key] = s.sort_index()
    return out


def quarterly(s: Dict[str, pd.Series]) -> pd.DataFrame:
    q = pd.DataFrame({"mv": s["equities"] / 1000, "profits": s["profits"], "gdp": s["gdp"]}).dropna()
    q["pe"] = q["mv"] / q["profits"]
    q["buffett"] = q["mv"] / q["gdp"] * 100
    q["ey"] = q["profits"] / q["mv"] * 100
    return q


def today_values(q: pd.DataFrame, sp500: Optional[pd.Series], real10: Optional[pd.Series]) -> Dict[str, float]:
    """Latest quarter carried to today with the S&P 500's move since the quarter's end."""
    last = q.iloc[-1]
    end = q.index[-1] + pd.offsets.QuarterEnd(0)
    growth = 1.0
    if sp500 is not None and len(sp500):
        at_end = sp500[sp500.index <= end]
        if len(at_end):
            growth = float(sp500.iloc[-1] / at_end.iloc[-1])
    mv = last["mv"] * growth
    out = {"pe": mv / last["profits"], "buffett": mv / last["gdp"] * 100, "ey": last["profits"] / mv * 100,
           "growth_since_quarter": (growth - 1) * 100, "quarter": str(q.index[-1].date())}
    if real10 is not None and len(real10):
        out["erp"] = out["ey"] - float(real10.iloc[-1])
    return out


def forward_returns(years: int = 5) -> pd.Series:
    """US market annualized return (%) over the next ``years``, at each quarter start."""
    f = french.daily_returns(french.FACTORS_3)
    m = (1 + french.market_return(f)).groupby(f.index.to_period("Q")).prod()
    level = m.cumprod()
    fwd = (level.shift(-4 * years) / level) ** (1 / years) - 1
    fwd.index = fwd.index.to_timestamp()
    return (fwd * 100).dropna()


@dataclass
class Valuation:
    history: pd.DataFrame
    now: Dict[str, float]
    percentiles: Dict[str, float]
    next5y: Dict[str, Dict[str, float]]   # metric -> {same_fifth, all}


def assess(s: Dict[str, pd.Series], sp500: Optional[pd.Series] = None, with_returns: bool = True) -> Valuation:
    q = quarterly(s)
    real_q = s["real10"].resample("QS").mean() if len(s["real10"]) else pd.Series(dtype=float)
    q["erp"] = q["ey"] - real_q.reindex(q.index)
    now = today_values(q, sp500, s["real10"])
    pct = {k: float((q[k].dropna() <= now[k]).mean()) for k in ("pe", "buffett", "erp") if k in now}
    nxt = {}
    if with_returns:
        try:
            fwd = forward_returns()
            for k in ("pe", "buffett"):
                x = q[k].reindex(fwd.index).dropna()
                if len(x) < 40:
                    continue
                fifth = np.ceil(pct[k] * 5) / 5
                lo, hi = x.quantile(max(fifth - 0.2, 0)), x.quantile(min(fifth, 1))
                same = fwd.reindex(x[(x >= lo) & (x <= hi)].index)
                nxt[k] = {"same_fifth": float(same.mean()), "all": float(fwd.reindex(x.index).mean()), "n": len(same)}
        except Exception:
            pass                                     # French Data Library unavailable: no return link
    return Valuation(q, now, pct, nxt)
