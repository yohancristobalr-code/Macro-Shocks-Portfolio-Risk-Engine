"""
research/macro_scenario.py

Which macro scenario is the market pricing right now?

Each scenario is a pattern of moves across assets over the last ``window``
trading days (the conditional scenario map used for cross-asset risk
reviews): for example, a supply shock shows oil and inflation compensation up,
the curve steeper, the Nasdaq down and credit wider. Every move is measured
against its normal size (its standard deviation over the last three years), so
a 10 bps move in a calm bond market counts more than in a volatile one.

A scenario's score is the average of its conditions, each worth between -1
(two standard deviations the wrong way) and +1 (two the right way). It
describes what prices already reflect, not what will happen next.

The 10Y yield is also split into its real part (TIPS yield, DFII10) and
inflation compensation (breakeven, T10YIE): a rise driven by real yields
points to growth or a hawkish Fed, one driven by breakevens to inflation fears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# key: (FRED id, label, unit of the change)
MARKET: Dict[str, Tuple[str, str, str]] = {
    "y2": ("DGS2", "2Y yield", "bps"),
    "y10": ("DGS10", "10Y yield", "bps"),
    "real10": ("DFII10", "10Y real yield (TIPS)", "bps"),
    "breakeven10": ("T10YIE", "10Y breakeven inflation", "bps"),
    "dollar": ("DTWEXBGS", "Broad dollar index", "%"),
    "oil": ("DCOILWTICO", "WTI crude", "%"),
    "equities": ("SP500", "S&P 500", "%"),
    "nasdaq": ("NASDAQCOM", "Nasdaq Composite", "%"),
    "credit": ("BAMLH0A0HYM2", "HY credit spread", "bps"),
    "vix": ("VIXCLS", "VIX", "pts"),
}
# derived: 2s10s curve, bps
CURVE = ("curve", "2s10s curve", "bps")

SCENARIOS: Dict[str, Tuple[str, Dict[str, int]]] = {
    "reacceleration": ("Reacceleration with cost pressure",
                       {"y2": +1, "real10": +1, "breakeven10": +1, "oil": +1, "equities": +1, "credit": -1}),
    "disinflation": ("Disinflation / soft landing",
                     {"y10": -1, "breakeven10": -1, "oil": -1, "equities": +1, "credit": -1, "vix": -1}),
    "supply_shock": ("Supply shock / persistent inflation",
                     {"oil": +1, "breakeven10": +1, "curve": +1, "nasdaq": -1, "credit": +1}),
    "growth_scare": ("Growth scare / liquidation",
                     {"y10": -1, "real10": -1, "equities": -1, "credit": +1, "vix": +1, "dollar": +1}),
}

WINDOW = 20                 # trading days
HISTORY_YEARS = 3           # for the normal size of a move
CLIP = 2.0                  # standard deviations that count fully
CONFIRMED = 0.35
PARTIAL = 0.15
INVALIDATED = -0.15
STALE_DAYS = 10             # a series whose last value is older than this is left out


def fetch(fred, end: date, years: int = HISTORY_YEARS + 1) -> Dict[str, pd.Series]:
    """Every market series; ones that fail become empty, with a warning."""
    start = end - timedelta(days=365 * years + 60)
    out = {}
    for key, (sid, _, _) in MARKET.items():
        try:
            s = pd.Series(fred.get_series(sid, observation_start=str(start), observation_end=str(end)),
                          dtype=float).dropna().sort_index()
            s.index = pd.DatetimeIndex(s.index)
        except Exception as e:
            logger.warning("macro_input_unavailable", series=sid, error=str(e))
            s = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        out[key] = s
    return out


def levels(series: Dict[str, pd.Series]) -> pd.DataFrame:
    """Business-day panel of levels, each series carried forward at most 5 days; drops stale series."""
    present = {k: s for k, s in series.items() if len(s)}
    if not present:
        return pd.DataFrame()
    last = max(s.index[-1] for s in present.values())
    fresh = {k: s for k, s in present.items() if (last - s.index[-1]).days <= STALE_DAYS}
    for k in set(present) - set(fresh):
        logger.warning("macro_input_stale", series=MARKET[k][0], last=str(present[k].index[-1].date()))
    days = pd.bdate_range(min(s.index[0] for s in fresh.values()), last)
    panel = pd.DataFrame({k: s.reindex(days.union(s.index)).ffill(limit=5).reindex(days) for k, s in fresh.items()})
    if {"y2", "y10"} <= set(panel):
        panel["curve"] = panel["y10"] - panel["y2"]
    return panel


def unit(key: str) -> str:
    return CURVE[2] if key == "curve" else MARKET[key][2]


def label(key: str) -> str:
    return CURVE[1] if key == "curve" else MARKET[key][1]


def changes(panel: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """Rolling ``window``-day change of each series: bps for rates, % (log) for prices, points for the VIX."""
    out = {}
    for key in panel:
        s = panel[key]
        if unit(key) == "bps":
            out[key] = (s - s.shift(window)) * 100
        elif unit(key) == "%":
            out[key] = np.log(s / s.shift(window)) * 100
        else:
            out[key] = s - s.shift(window)
    return pd.DataFrame(out, index=panel.index)


@dataclass
class MarketScenario:
    as_of: date
    window: int
    moves: pd.DataFrame                 # index: key; columns: label, unit, change, z
    scores: pd.Series                   # scenario key -> score, best first
    decomposition: Optional[Dict[str, float]]

    @property
    def best(self) -> Optional[str]:
        return self.scores.index[0] if len(self.scores) else None

    def status(self, key: str) -> str:
        s = self.scores[key]
        if s >= CONFIRMED:
            return "CONFIRMED"
        if s >= PARTIAL:
            return "partial"
        if s <= INVALIDATED:
            return "invalidated"
        return "no signal"

    def conditions(self, key: str) -> Tuple[List[str], List[str]]:
        """(matching, contradicting) moves for a scenario, as short text."""
        match, against = [], []
        for var, sign in SCENARIOS[key][1].items():
            if var not in self.moves.index or pd.isna(self.moves.at[var, "z"]):
                continue
            z = float(self.moves.at[var, "z"])
            text = f"{self.moves.at[var, 'label']} {'up' if z > 0 else 'down'} ({z:+.1f}sd)"
            (match if sign * z > 0 else against).append(text)
        return match, against


def score(z: pd.Series) -> pd.Series:
    """Each scenario's average alignment with the moves (-1 to +1); variables without data are skipped."""
    out = {}
    for key, (_, signs) in SCENARIOS.items():
        vals = [float(np.clip(sign * z[v], -CLIP, CLIP) / CLIP) for v, sign in signs.items()
                if v in z.index and pd.notna(z[v])]
        out[key] = float(np.mean(vals)) if len(vals) >= max(3, len(signs) // 2) else np.nan
    return pd.Series(out).dropna().sort_values(ascending=False)


def assess(series: Dict[str, pd.Series], window: int = WINDOW) -> MarketScenario:
    panel = levels(series)
    if panel.empty:
        raise ValueError("no market data for the macro scenario")
    ch = changes(panel, window)
    recent = ch[ch.index > ch.index[-1] - pd.DateOffset(years=HISTORY_YEARS)]
    latest = ch.iloc[-1]
    size = recent.std()
    z = latest / size.replace(0, np.nan)
    moves = pd.DataFrame({"label": [label(k) for k in ch.columns], "unit": [unit(k) for k in ch.columns],
                          "change": latest.to_numpy(), "z": z.to_numpy()}, index=ch.columns)
    decomp = None
    if {"y10", "real10", "breakeven10"} <= set(ch.columns) and latest[["y10", "real10", "breakeven10"]].notna().all():
        decomp = {"y10": float(latest["y10"]), "real10": float(latest["real10"]),
                  "breakeven10": float(latest["breakeven10"])}
    return MarketScenario(panel.index[-1].date(), window, moves, score(z), decomp)


def score_history(series: Dict[str, pd.Series], window: int = WINDOW, days: int = 260) -> pd.DataFrame:
    """Each scenario's score on each of the last ``days`` trading days (scale: the last 3 years' moves)."""
    panel = levels(series)
    if panel.empty:
        return pd.DataFrame()
    ch = changes(panel, window)
    size = ch[ch.index > ch.index[-1] - pd.DateOffset(years=HISTORY_YEARS)].std().replace(0, np.nan)
    rows = {d: score(ch.loc[d] / size) for d in ch.index[-days:]}
    return pd.DataFrame(rows).T.reindex(columns=list(SCENARIOS))


def decomposition_line(ms: MarketScenario) -> Optional[str]:
    d = ms.decomposition
    if not d:
        return None
    if abs(d["real10"]) >= abs(d["breakeven10"]):
        driver = "real yields (growth or a hawkish Fed)"
    else:
        driver = "inflation compensation (inflation fears)"
    return (f"  10Y {d['y10']:+.0f} bps in {ms.window} days = real {d['real10']:+.0f} + "
            f"inflation compensation {d['breakeven10']:+.0f}: driven by {driver}")


def _fmt_change(change: float, unit_: str) -> str:
    if pd.isna(change):
        return "n/a"
    return f"{change:+.0f} {unit_}" if unit_ == "bps" else f"{change:+.1f}{'%' if unit_ == '%' else ' pts'}"


def report_lines(ms: MarketScenario) -> List[str]:
    lines = [f"  What markets are pricing (last {ms.window} trading days, through {ms.as_of}):",
             f"    {'Move':<26}{'change':>10}{'vs normal':>12}"]
    for key, r in ms.moves.iterrows():
        z = "n/a" if pd.isna(r["z"]) else f"{r['z']:+.1f} sd"
        lines.append(f"    {r['label']:<26}{_fmt_change(r['change'], r['unit']):>10}{z:>12}")
    line = decomposition_line(ms)
    if line:
        lines += ["", line]
    lines += ["", f"    {'Scenario':<40}{'score':>7}   status"]
    for key in ms.scores.index:
        lines.append(f"    {SCENARIOS[key][0]:<40}{ms.scores[key]:>+7.2f}   {ms.status(key)}")
    if ms.best is not None:
        match, against = ms.conditions(ms.best)
        lines.append(f"    Best match, {SCENARIOS[ms.best][0]}:")
        lines.append("      confirms:    " + (", ".join(match) or "none"))
        lines.append("      contradicts: " + (", ".join(against) or "none"))
    lines.append("  sd = standard deviations of a normal move over the last 3 years. Score: -1 to +1; "
                 f"confirmed >= {CONFIRMED}.")
    return lines


def summary(ms: MarketScenario) -> str:
    if ms.best is None:
        return "no scenario"
    return f"{ms.best} ({ms.scores[ms.best]:+.2f}, {ms.status(ms.best)})"
