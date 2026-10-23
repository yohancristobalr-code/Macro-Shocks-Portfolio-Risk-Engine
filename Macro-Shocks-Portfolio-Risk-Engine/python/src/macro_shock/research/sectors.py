"""
research/sectors.py

S&P 500 sectors through the 11 SPDR sector ETFs, against SPY (Stooq daily
prices). Unlike the French Data Library industries, these are the tradable
funds and are current to yesterday.

  Relative strength   return vs SPY over 1, 3, 6 and 12 months; trend (above
                      the 200-day average); 12-1 month relative momentum rank
  Macro sensitivities betas of each sector's return over SPY, per 20-day
                      period over 5 years, to the 10Y yield (bps), WTI (%),
                      the broad dollar (%) and the HY spread (bps)
  Scenario map        expected return vs SPY if each macro scenario plays out
                      as a typical (one standard deviation) move of its
                      variables, and for the moves of the last 20 days
  Breadth, rotation   share of sectors in an uptrend; cyclicals vs defensives
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from macro_shock.research import macro_scenario as ms
from macro_shock.research import stooq

logger = structlog.get_logger(__name__)

SECTORS = {"XLK": "Tecnología", "XLC": "Comunicaciones", "XLY": "Consumo discrecional", "XLF": "Financiero",
           "XLI": "Industria", "XLB": "Materiales", "XLE": "Energía", "XLV": "Salud", "XLP": "Consumo básico",
           "XLU": "Servicios públicos", "XLRE": "Inmobiliario"}
BENCH = "SPY"
CYCLICAL = ("XLK", "XLY", "XLF", "XLI", "XLB", "XLE")
DEFENSIVE = ("XLV", "XLP", "XLU")
FACTORS = ("y10", "oil", "dollar", "credit")       # keys of macro_scenario.MARKET
STEP = 20                                          # trading days per period for the betas
BETA_YEARS = 5


def fetch(get: Optional[Callable] = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
    prices, errors = {}, {}
    for sym in (BENCH, *SECTORS):
        try:
            prices[sym] = stooq.daily_close(f"{sym.lower()}.us", get)
        except Exception as e:
            errors[sym] = str(e)
    if BENCH not in prices:
        raise ValueError(f"no SPY prices from Stooq ({errors.get(BENCH)})")
    return pd.DataFrame(prices).sort_index(), errors


def _ret(s: pd.Series, days: int) -> float:
    s = s.dropna()
    return float((s.iloc[-1] / s.iloc[-1 - days] - 1) * 100) if len(s) > days else np.nan


def betas(prices: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Sector return over SPY per 20-day period regressed on the factors' changes (multiple regression)."""
    p = prices[prices.index > prices.index[-1] - pd.DateOffset(years=BETA_YEARS)]
    idx = p.index[::-1][::STEP][::-1]                       # every 20th day, ending today
    rel = np.log(p.loc[idx]).diff().sub(np.log(p.loc[idx, BENCH]).diff(), axis=0) * 100
    f = factors.reindex(factors.index.union(idx)).ffill().reindex(idx)
    moves = pd.DataFrame({k: (f[k].diff() * 100 if ms.unit(k) == "bps" else np.log(f[k]).diff() * 100) for k in FACTORS
                          if k in f})
    out = {}
    for sym in SECTORS:
        if sym not in rel:
            continue
        j = pd.concat([rel[sym], moves], axis=1).dropna()
        if len(j) < 20:
            continue
        X = np.column_stack([np.ones(len(j)), j[list(moves.columns)].to_numpy()])
        coef, *_ = np.linalg.lstsq(X, j[sym].to_numpy(), rcond=None)
        out[sym] = dict(zip(moves.columns, coef[1:]))
    return pd.DataFrame(out).T


def typical_moves(factors: pd.DataFrame) -> pd.Series:
    """Standard deviation of each factor's 20-day change over the last 3 years (the 'one typical move')."""
    ch = ms.changes(factors[[k for k in FACTORS if k in factors]].dropna(how="all"), STEP)
    return ch[ch.index > ch.index[-1] - pd.DateOffset(years=ms.HISTORY_YEARS)].std()


def scenario_map(b: pd.DataFrame, size: pd.Series, current: Optional[pd.Series] = None) -> pd.DataFrame:
    """Expected return vs SPY (%) per sector under each scenario's typical move, and for the latest moves."""
    cols = {}
    for key, (_, signs) in ms.SCENARIOS.items():
        move = pd.Series({k: signs.get(k, 0) * size.get(k, 0.0) for k in b.columns})
        cols[key] = b.mul(move, axis=1).sum(axis=1)
    if current is not None:
        cols["last_20d"] = b.mul(current.reindex(b.columns).fillna(0), axis=1).sum(axis=1)
    return pd.DataFrame(cols)


@dataclass
class SectorView:
    table: pd.DataFrame          # per sector: name, returns vs SPY, trend, momentum rank, betas
    scenarios: pd.DataFrame      # expected return vs SPY per scenario
    breadth: float               # share of sectors above their 200-day average
    rotation_3m: float           # cyclicals minus defensives, 3-month return, %
    prices: pd.DataFrame
    errors: Dict[str, str]


def analyze(prices: pd.DataFrame, factors: Optional[pd.DataFrame] = None,
            current_moves: Optional[pd.Series] = None, errors: Optional[Dict[str, str]] = None) -> SectorView:
    spy = prices[BENCH].dropna()
    rows = {}
    for sym, name in SECTORS.items():
        if sym not in prices:
            continue
        s = prices[sym].dropna()
        rel = lambda d: _ret(s, d) - _ret(spy, d)
        mom = (s.iloc[-22] / s.iloc[-253] - spy.iloc[-22] / spy.iloc[-253]) * 100 if len(s) > 253 else np.nan
        rows[sym] = {"name": name, "r1m": rel(21), "r3m": rel(63), "r6m": rel(126), "r12m": rel(252),
                     "trend_up": bool(s.iloc[-1] > s.rolling(200).mean().iloc[-1]) if len(s) > 200 else None,
                     "momentum": mom}
    t = pd.DataFrame(rows).T
    t["momentum_rank"] = t["momentum"].astype(float).rank(ascending=False)
    scen = pd.DataFrame()
    if factors is not None and len(factors):
        b = betas(prices, factors)
        t = t.join(b.add_prefix("beta_"))
        scen = scenario_map(b, typical_moves(factors), current_moves)
    trend = t["trend_up"].dropna()
    cyc = [c for c in CYCLICAL if c in prices]
    dfn = [c for c in DEFENSIVE if c in prices]
    rotation = float(np.mean([_ret(prices[c], 63) for c in cyc]) - np.mean([_ret(prices[c], 63) for c in dfn])) \
        if cyc and dfn else np.nan
    return SectorView(t, scen, float(trend.mean()) if len(trend) else np.nan, rotation, prices, errors or {})
