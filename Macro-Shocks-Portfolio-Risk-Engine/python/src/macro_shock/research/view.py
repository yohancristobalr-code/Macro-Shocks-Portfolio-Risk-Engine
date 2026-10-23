"""
research/view.py

Page one: a systematic view per asset, like the allocation table that opens
bank research, but built from the engine's own signals with fixed rules.

For each asset, signals worth +1 (supports it) or -1 (against it):
  trend      price above its 200-day average
  momentum   3-month return positive
  scenario   the asset's direction in the scenario markets price (x1 if
             confirmed, x0.5 if partial; nothing if no clear scenario)
  quadrant   the asset's direction in the macro quadrant of the published data

Directions per scenario follow the standard cross-asset playbook (PLAYBOOK
below; for bonds, + means prices up, i.e. yields down). The sum gives
overweight (>= +2), underweight (<= -2) or neutral, and conviction is the
share of the maximum. A systematic signal from transparent rules, not advice.

Also the VIX term structure: VIX above its 3-month version (VXVCLS) means
the market prices more stress now than later, which usually happens in
selloffs.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

PLAYBOOK = {
    "reacceleration": {"equities": +1, "bonds": -1, "credit": +1, "gold": 0, "oil": +1, "dollar": +1, "bitcoin": +1, "copper": +1},
    "disinflation": {"equities": +1, "bonds": +1, "credit": +1, "gold": +1, "oil": -1, "dollar": -1, "bitcoin": +1, "copper": 0},
    "supply_shock": {"equities": -1, "bonds": -1, "credit": -1, "gold": +1, "oil": +1, "dollar": 0, "bitcoin": -1, "copper": 0},
    "growth_scare": {"equities": -1, "bonds": +1, "credit": -1, "gold": +1, "oil": -1, "dollar": +1, "bitcoin": -1, "copper": -1},
}
ASSETS = {"equities": "Bolsa EE. UU. (S&P 500)", "bonds": "Bonos del Tesoro largos", "credit": "Crédito high yield",
          "gold": "Oro", "copper": "Cobre", "oil": "Petróleo (WTI)", "dollar": "Dólar", "bitcoin": "Bitcoin"}
OVER, UNDER = 2.0, -2.0


def price_signals(s: Optional[pd.Series], invert: bool = False) -> Dict[str, Optional[int]]:
    """Trend and 3-month momentum, each +1/-1; ``invert`` for yields and spreads (up is bad for the asset)."""
    if s is None or len(s.dropna()) < 210:
        return {"trend": None, "momentum": None}
    s = s.dropna()
    sign = -1 if invert else 1
    trend = 1 if s.iloc[-1] > s.rolling(200).mean().iloc[-1] else -1
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=91)]
    mom = 1 if len(past) and s.iloc[-1] > past.iloc[-1] else -1
    return {"trend": sign * trend, "momentum": sign * mom}


def asset_view(series: Dict[str, Optional[pd.Series]], scenario: Optional[str], scenario_status: Optional[str],
               quadrant: Optional[str], extra: Optional[Dict[str, Dict[str, int]]] = None) -> pd.DataFrame:
    """``extra``: more signals per asset, e.g. {"valuation": {"equities": -1}, "positioning": {"gold": -1}}."""
    extra = extra or {}
    weight = {"CONFIRMED": 1.0, "partial": 0.5}.get(scenario_status or "", 0.0)
    rows = []
    for key, label in ASSETS.items():
        s = series.get(key)
        sig = price_signals(s, invert=key in ("bonds", "credit"))
        scen = PLAYBOOK[scenario][key] * weight if scenario in PLAYBOOK and weight else None
        quad = PLAYBOOK[quadrant][key] if quadrant in PLAYBOOK else None
        more = {name: sigs[key] for name, sigs in extra.items() if sigs.get(key)}
        parts = [v for v in (sig["trend"], sig["momentum"], scen, quad, *more.values()) if v is not None]
        if not parts:
            continue
        total = float(sum(parts))
        maximum = float(len(parts) - (0 if scen is None else 1 - weight))
        view = "Sobreponderar" if total >= OVER else "Infraponderar" if total <= UNDER else "Neutral"
        rows.append({"key": key, "asset": label, "trend": sig["trend"], "momentum": sig["momentum"],
                     "scenario": scen, "quadrant": quad, "valuation": more.get("valuation"),
                     "positioning": more.get("positioning"), "score": total, "view": view,
                     "conviction": abs(total) / maximum if maximum else 0.0})
    return pd.DataFrame(rows)


def vix_term(fred, end: date) -> Optional[pd.DataFrame]:
    """VIX, 3-month VIX and their ratio (> 1: backwardation, stress now)."""
    start = str(end - timedelta(days=365 * 3))
    vix = pd.Series(fred.get_series("VIXCLS", observation_start=start, observation_end=str(end)), dtype=float).dropna()
    v3 = pd.Series(fred.get_series("VXVCLS", observation_start=start, observation_end=str(end)), dtype=float).dropna()
    j = pd.concat([vix, v3], axis=1, keys=["vix", "vix3m"]).dropna()
    if j.empty:
        return None
    j.index = pd.DatetimeIndex(j.index)
    j["ratio"] = j["vix"] / j["vix3m"]
    return j


def vix_alert(term: Optional[pd.DataFrame]) -> List[str]:
    if term is None or term.empty or term["ratio"].iloc[-1] < 1.0:
        return []
    return [f"VIX term structure inverted: VIX {term['vix'].iloc[-1]:.1f} above its 3-month version "
            f"{term['vix3m'].iloc[-1]:.1f} (ratio {term['ratio'].iloc[-1]:.2f}): stress priced for now"]
