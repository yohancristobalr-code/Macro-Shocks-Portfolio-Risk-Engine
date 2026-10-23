"""
research/metals.py

Gold, silver and copper. FRED stopped publishing gold and silver prices in
2022, so the sources are, in order:

  gold, silver  Stooq spot (xauusd, xagusd; decades of history), else the
                Binance USD-M perpetuals XAUUSDT / XAGUSDT (only since 2025)
  copper        Stooq COMEX futures (hg.f), else the IMF monthly price on FRED

The Binance perpetuals also give funding: leveraged positioning in gold and
silver, read like crypto funding.

  Gold vs real yield  gold usually falls when real yields rise; a regression
                      of log gold on the 10Y TIPS yield over 3 years says how
                      far gold sits above (rich) or below (cheap) that link
  Gold/silver ratio   high = silver cheap relative to gold (fear); low = risk-on
  Copper/gold ratio   growth gauge: it tends to move with the 10Y yield
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd
import structlog

from macro_shock.research import crypto, prices as px, stooq

logger = structlog.get_logger(__name__)

YAHOO = {"gold": "GC=F", "silver": "SI=F", "copper": "HG=F"}          # COMEX front-month futures
STOOQ = {"gold": "xauusd", "silver": "xagusd", "copper": "hg.f"}
BINANCE = {"gold": "XAUUSDT", "silver": "XAGUSDT"}
LABELS = {"gold": "Oro ($/oz)", "silver": "Plata ($/oz)", "copper": "Cobre ($/lb)"}


def binance_perp(symbol: str, get: Optional[Callable] = None) -> pd.Series:
    rows = (get or crypto.http_json)(f"{crypto.FUTURES_HOST}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=1500")
    return pd.Series([float(r[4]) for r in rows], index=pd.to_datetime([int(r[0]) for r in rows], unit="ms").normalize())


@dataclass
class MetalsData:
    prices: Dict[str, pd.Series] = field(default_factory=dict)
    source: Dict[str, str] = field(default_factory=dict)
    funding: Dict[str, float] = field(default_factory=dict)     # % annualized, Binance perpetuals
    errors: Dict[str, str] = field(default_factory=dict)


def fetch(fred=None, get_text: Optional[Callable] = None, get_json: Optional[Callable] = None) -> MetalsData:
    d = MetalsData()
    for metal, sym in STOOQ.items():
        try:
            d.prices[metal], d.source[metal] = px.yahoo(YAHOO[metal], get_json), "Yahoo (futuro COMEX)"
            continue
        except Exception as e:
            d.errors[f"{metal} (Yahoo)"] = str(e)
        try:
            d.prices[metal], d.source[metal] = stooq.daily_close(sym, get_text), "Stooq"
            continue
        except Exception as e:
            d.errors[f"{metal} (Stooq)"] = str(e)
        try:
            if metal in BINANCE:
                d.prices[metal], d.source[metal] = binance_perp(BINANCE[metal], get_json), "Binance perpetuo"
            elif fred is not None:
                s = pd.Series(fred.get_series("PCOPPUSDM"), dtype=float).dropna() / 2204.62   # $/t -> $/lb
                s.index = pd.DatetimeIndex(s.index)
                d.prices[metal], d.source[metal] = s, "FMI mensual (FRED)"
        except Exception as e:
            d.errors[f"{metal} (respaldo)"] = str(e)
    for metal, sym in BINANCE.items():
        try:
            f = crypto.fetch_funding(sym.replace("USDT", ""), get_json)
            d.funding[metal] = crypto.funding_annualized(f)
        except Exception as e:
            d.errors[f"funding {metal}"] = str(e)
    if not d.prices:
        raise ValueError("no metal prices (Stooq and Binance unavailable)")
    return d


def _ret(s: pd.Series, days: int) -> float:
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return float((s.iloc[-1] / past.iloc[-1] - 1) * 100) if len(past) else np.nan


@dataclass
class MetalsView:
    data: MetalsData
    table: pd.DataFrame
    gold_vs_real: Optional[Dict[str, float]]
    gold_silver: Optional[pd.Series]
    copper_gold: Optional[pd.Series]
    copper_gold_vs_10y: Optional[float]


def gold_vs_real_yield(gold: pd.Series, real10: pd.Series, years: int = 3) -> Optional[Dict[str, float]]:
    j = pd.concat([np.log(gold), real10], axis=1, keys=["g", "r"]).ffill().dropna()
    j = j[j.index > j.index[-1] - pd.DateOffset(years=years)]
    if len(j) < 120:
        return None
    slope, intercept = np.polyfit(j["r"], j["g"], 1)
    resid = j["g"] - (intercept + slope * j["r"])
    return {"slope_pct_per_100bp": float(slope * 100), "gap_pct": float(resid.iloc[-1] * 100),
            "gap_sd": float(resid.iloc[-1] / resid.std()), "corr": float(j.corr().iloc[0, 1])}


def analyze(d: MetalsData, real10: Optional[pd.Series] = None, y10: Optional[pd.Series] = None) -> MetalsView:
    rows = {}
    for metal, s in d.prices.items():
        s = s.dropna()
        high = s[s.index > s.index[-1] - pd.Timedelta(days=365)].max()
        rows[metal] = {"label": LABELS[metal], "price": float(s.iloc[-1]), "date": s.index[-1].date(),
                       "r1m": _ret(s, 30), "r3m": _ret(s, 91), "r1y": _ret(s, 365),
                       "vs_high": float((s.iloc[-1] / high - 1) * 100), "source": d.source[metal],
                       "funding": d.funding.get(metal, np.nan)}
    gv = gold_vs_real_yield(d.prices["gold"], real10) if "gold" in d.prices and real10 is not None and len(real10) else None
    gs = cg = corr = None
    if {"gold", "silver"} <= set(d.prices):
        gs = (d.prices["gold"] / d.prices["silver"]).dropna()
    if {"gold", "copper"} <= set(d.prices):
        cg = (d.prices["copper"] / d.prices["gold"] * 1000).dropna()
        if y10 is not None and len(y10):
            j = pd.concat([cg, y10], axis=1).ffill().dropna()
            j = j[j.index > j.index[-1] - pd.DateOffset(years=3)].iloc[::5]
            corr = float(j.corr().iloc[0, 1]) if len(j) > 30 else None
    return MetalsView(d, pd.DataFrame(rows).T, gv, gs, cg, corr)
