"""
research/regime_history.py

Rebuild the market regime the engine would have reported on past dates, from
FRED data, using the engine's own MarketVulnerabilityScorer.

Live, with `analyze --data fred`, the regime comes from four inputs: VIX, the
2s10s slope, and the ICE BofA HY and IG spreads. FRED only publishes the last
~3 years of the ICE series, so history uses Moody's Baa - 10Y spread (BAA10Y,
since 1986) instead, quantile-mapped onto the scorer's calibration: a BAA10Y
value at its own historical p-th percentile becomes the HY/IG spread at the
scorer's p-th calibration knot. The scorer only looks at percentile ranks, so
the proxy enters the classification exactly as a real spread at the same
percentile would. credit_proxy_agreement() measures how often both versions
give the same regime over the years where FRED has the ICE series.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from macro_shock.data_schema.models import (
    CreditMarketSnapshot, MarketSessionState, MarketStateSnapshot, VolatilitySnapshot, YieldCurveSnapshot,
)
from macro_shock.market_context.vulnerability_scorer import CALIBRATION, MarketVulnerabilityScorer

SERIES = {
    "vix": "VIXCLS",
    "y2": "DGS2",
    "y10": "DGS10",
    "baa": "BAA10Y",          # Moody's Baa corporate minus 10Y Treasury, %, since 1986
    "hy": "BAMLH0A0HYM2",     # ICE BofA HY OAS, %; FRED keeps ~3 years
    "ig": "BAMLC0A0CM",       # ICE BofA IG OAS, %; FRED keeps ~3 years
}

KNOT_PERCENTILES = (10, 25, 50, 75, 90)
MAX_STALENESS_DAYS = 7


def fetch_series(fred, start: date, end: date) -> Dict[str, pd.Series]:
    """Every regime input from FRED; the BAA10Y history is always fetched in full for its percentiles."""
    out = {}
    for key, sid in SERIES.items():
        obs_start = "1986-01-01" if key == "baa" else str(start)
        out[key] = fred.get_series(sid, observation_start=obs_start, observation_end=str(end)).dropna().sort_index()
    return out


def proxy_knots(baa: pd.Series) -> np.ndarray:
    return np.percentile(baa.dropna().to_numpy(dtype=float), KNOT_PERCENTILES)


def baa_to_spreads(baa_value: float, knots: np.ndarray) -> Dict[str, float]:
    """HY and IG spreads (bps) at the same calibration percentile as ``baa_value``."""
    return {
        "hy_spread_bps": float(np.interp(baa_value, knots, CALIBRATION["hy_spread_bps"])),
        "ig_spread_bps": float(np.interp(baa_value, knots, CALIBRATION["ig_spread_bps"])),
    }


def _asof(s: pd.Series, d: pd.Timestamp) -> Optional[float]:
    sub = s[s.index <= d]
    if sub.empty or (d - sub.index[-1]).days > MAX_STALENESS_DAYS:
        return None
    return float(sub.iloc[-1])


def classify(
    scorer: MarketVulnerabilityScorer, when: pd.Timestamp,
    vix: float, y2: float, y10: float, hy_bps: float, ig_bps: float,
) -> str:
    ts = datetime(when.year, when.month, when.day, 21, tzinfo=timezone.utc)
    state = MarketStateSnapshot(
        timestamp=ts,
        session_state=MarketSessionState.CLOSED_OVERNIGHT,
        yields=YieldCurveSnapshot(timestamp=ts, y2=y2, y10=y10),
        volatility=VolatilitySnapshot(timestamp=ts, vix_spot=vix),
        credit=CreditMarketSnapshot(timestamp=ts, hy_spread_bps=hy_bps, ig_spread_bps=ig_bps),
    )
    return scorer.score(state, log=False).regime.value


def regime_history(
    series: Dict[str, pd.Series], dates: Iterable[pd.Timestamp], credit: str = "proxy",
    scorer: Optional[MarketVulnerabilityScorer] = None,
) -> pd.Series:
    """
    Regime label for each date (None when an input is missing or stale).
    ``credit="proxy"`` uses BAA10Y, ``credit="ice"`` the real ICE spreads.
    """
    scorer = scorer or MarketVulnerabilityScorer()
    knots = proxy_knots(series["baa"])
    out = {}
    for d in pd.DatetimeIndex(dates):
        vix, y2, y10 = (_asof(series[k], d) for k in ("vix", "y2", "y10"))
        if credit == "proxy":
            baa = _asof(series["baa"], d)
            spreads = baa_to_spreads(baa, knots) if baa is not None else None
        else:
            hy, ig = _asof(series["hy"], d), _asof(series["ig"], d)
            spreads = {"hy_spread_bps": hy * 100, "ig_spread_bps": ig * 100} if None not in (hy, ig) else None
        if None in (vix, y2, y10) or spreads is None:
            out[d] = None
            continue
        out[d] = classify(scorer, d, vix, y2, y10, spreads["hy_spread_bps"], spreads["ig_spread_bps"])
    return pd.Series(out, dtype=object)


def credit_proxy_agreement(series: Dict[str, pd.Series], step: int = 5) -> Dict[str, float]:
    """Share of dates (every ``step``-th ICE observation) where proxy and real credit give the same regime."""
    dates = series["hy"].index.intersection(series["ig"].index)[::step]
    proxy = regime_history(series, dates, "proxy")
    real = regime_history(series, dates, "ice")
    both = proxy.notna() & real.notna()
    n = int(both.sum())
    return {
        "agreement": float((proxy[both] == real[both]).mean()) if n else np.nan,
        "n": n,
        "since": dates[0].date() if len(dates) else None,
    }
