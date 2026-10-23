"""
research/growth.py

Growth, and the growth x inflation quadrant.

The quadrant uses the direction of each axis, from published data only:

  growth     jobless claims (4-week average vs 26-week, falling = growth up),
             industrial production and payrolls (3-month pace vs 12-month)
  inflation  headline and core CPI (3-month annualized vs 12-month)

Each signal is divided by its own typical size up to that date (median
absolute value, expanding: robust to the 2020 collapse) and capped at +/-3,
so the axes are comparable and nothing from the future enters. A month or two
missing inside a series (October 2025 CPI, never published because of the
government shutdown) is filled by log-linear interpolation, only to compute
paces. History applies publication lags (claims ~5 days after the week,
payrolls ~1 week after the month, CPI ~2.5 weeks, industrial production ~3
weeks), so a past date only sees what was published by then; the live
reading uses whatever FRED already has.

  growth up,   inflation up    reacceleration
  growth up,   inflation down  disinflation
  growth down, inflation up    supply_shock (stagflation)
  growth down, inflation down  growth_scare

The keys match research/macro_scenario.py, so the data quadrant and the
scenario markets are pricing can be compared directly.

Leading indicators shown alongside (not part of the quadrant, too little
history): NY Fed Weekly Economic Index, Atlanta Fed GDPNow, the real-time Sahm
rule, continuing claims, and the Philadelphia and New York Fed manufacturing
surveys, published before the S&P Global flash PMI. S&P Global and ISM data
are not public, so the surveys give the PMI's likely direction, not its level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

SERIES: Dict[str, Tuple[str, str]] = {
    "claims": ("ICSA", "Initial jobless claims"),
    "continuing": ("CCSA", "Continuing claims"),
    "indpro": ("INDPRO", "Industrial production"),
    "payrolls": ("PAYEMS", "Nonfarm payrolls"),
    "cpi": ("CPIAUCSL", "Headline CPI"),
    "core": ("CPILFESL", "Core CPI"),
    "wei": ("WEI", "NY Fed Weekly Economic Index"),
    "gdpnow": ("GDPNOW", "Atlanta Fed GDPNow"),
    "sahm": ("SAHMREALTIME", "Sahm rule, real time"),
    "philly_activity": ("GACDFSA066MSFRBPHI", "Philadelphia Fed: activity"),
    "philly_orders": ("NOCDFSA066MSFRBPHI", "Philadelphia Fed: new orders"),
    "philly_prices": ("PPCDFSA066MSFRBPHI", "Philadelphia Fed: prices paid"),
    "empire_activity": ("GACDINA066MSFRBNY", "New York Fed: activity"),
    "empire_orders": ("NOCDINA066MSFRBNY", "New York Fed: new orders"),
    "empire_prices": ("PPCDINA066MSFRBNY", "New York Fed: prices paid"),
}
REQUIRED = ("claims", "indpro", "payrolls", "cpi", "core")
FULL_HISTORY_START = date(1967, 1, 1)

# days after the period's end until the figure is public (conservative)
PUBLICATION_LAG = {"claims": 5, "indpro": 21, "payrolls": 10, "cpi": 17, "core": 17}
MAX_AGE_DAYS = {"claims": 21, "indpro": 75, "payrolls": 75, "cpi": 75, "core": 75}

GROWTH = ("claims", "indpro", "payrolls")
INFLATION = ("cpi", "core")
SIGNAL_LABELS = {"claims": "jobless claims", "indpro": "industrial production", "payrolls": "payrolls",
                 "cpi": "headline CPI", "core": "core CPI"}
QUADRANTS = {(True, True): "reacceleration", (True, False): "disinflation",
             (False, True): "supply_shock", (False, False): "growth_scare"}
QUADRANT_NAMES = {"reacceleration": "Reacceleration (growth up, inflation up)",
                  "disinflation": "Disinflation (growth up, inflation down)",
                  "supply_shock": "Stagflation / supply shock (growth down, inflation up)",
                  "growth_scare": "Slowdown (growth down, inflation down)"}
SAHM_RECESSION = 0.5
MIN_HISTORY = 24            # signal observations before its scale is trusted
CAP = 3.0                   # largest |scaled signal|
GAP_MONTHS = 2              # interior gaps up to this long are interpolated

SURVEYS = (("activity", "General activity"), ("orders", "New orders"), ("prices", "Prices paid"))


def fetch(fred, start: date, end: date) -> Dict[str, pd.Series]:
    out = {}
    for key, (sid, _) in SERIES.items():
        try:
            s = pd.Series(fred.get_series(sid, observation_start=str(start), observation_end=str(end)),
                          dtype=float).dropna().sort_index()
            s.index = pd.DatetimeIndex(s.index)
        except Exception as e:
            if key in REQUIRED:
                raise ValueError(f"FRED series {sid} is unavailable ({e})")
            logger.warning("growth_input_unavailable", series=sid, error=str(e))
            s = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        out[key] = s
    return out


# ── Signals ──────────────────────────────────────────────────────────────────

def _monthly(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.DatetimeIndex([pd.Timestamp(d).to_period("M").to_timestamp() for d in s.index])
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(pd.date_range(s.index[0], s.index[-1], freq="MS"))


def _filled(level: pd.Series) -> pd.Series:
    """Monthly levels with short interior gaps filled log-linearly (for paces only)."""
    m = _monthly(level)
    return np.exp(np.log(m).interpolate(limit=GAP_MONTHS, limit_area="inside"))


def claims_signal(claims: pd.Series) -> pd.Series:
    """4-week vs 26-week average of weekly claims, in %, sign flipped: positive = claims falling = growth up."""
    return -(claims.rolling(4).mean() / claims.rolling(26).mean() - 1) * 100


def pace_signal(level: pd.Series) -> pd.Series:
    """3-month annualized growth minus 12-month growth (percentage points): positive = accelerating."""
    m = _filled(level)
    return ((m / m.shift(3)) ** 4 - 1) * 100 - (m / m.shift(12) - 1) * 100


def payrolls_signal(level: pd.Series) -> pd.Series:
    """Average monthly job gain over 3 months minus over 12 months (thousands): positive = accelerating."""
    d = _filled(level).diff()
    return d.rolling(3, min_periods=2).mean() - d.rolling(12, min_periods=10).mean()


def scale(signal: pd.Series) -> pd.Series:
    """Typical size of the signal up to each date: expanding median absolute value."""
    return signal.dropna().abs().expanding().median()


def scaled(signal: pd.Series) -> pd.Series:
    """Signal in units of its typical size so far, capped at +/-CAP: sign kept, no look-ahead."""
    s = signal.dropna()
    out = (s / scale(s).replace(0, np.nan)).clip(-CAP, CAP)
    out[np.arange(len(s)) < MIN_HISTORY] = np.nan
    return out


@dataclass
class Signals:
    """Each scaled signal, indexed by the date its period ends."""
    values: Dict[str, pd.Series]
    raw: Dict[str, pd.Series]


def signals(series: Dict[str, pd.Series]) -> Signals:
    raw = {
        "claims": claims_signal(series["claims"]),
        "indpro": pace_signal(series["indpro"]),
        "payrolls": payrolls_signal(series["payrolls"]),
        "cpi": pace_signal(series["cpi"]),
        "core": pace_signal(series["core"]),
    }
    values = {}
    for k, s in raw.items():
        v = scaled(s)
        if k != "claims":           # monthly: index at the month's last day
            v.index = v.index + pd.offsets.MonthEnd(0)
        values[k] = v
    return Signals(values, raw)


def value_at(sig: Signals, key: str, when: pd.Timestamp, lagged: bool) -> Optional[float]:
    """Latest scaled value of ``key`` public at ``when`` (with publication lag if ``lagged``)."""
    s = sig.values[key].dropna()
    lag = pd.Timedelta(days=PUBLICATION_LAG[key] if lagged else 0)
    s = s[s.index + lag <= when]
    if s.empty or (when - s.index[-1]).days > MAX_AGE_DAYS[key] + (PUBLICATION_LAG[key] if lagged else 0):
        return None
    return float(s.iloc[-1])


@dataclass
class Axes:
    when: pd.Timestamp
    growth: Optional[float]
    inflation: Optional[float]
    parts: Dict[str, Optional[float]]

    @property
    def quadrant(self) -> Optional[str]:
        if self.growth is None or self.inflation is None:
            return None
        return QUADRANTS[(self.growth > 0, self.inflation > 0)]


def axes_at(sig: Signals, when, lagged: bool = True) -> Axes:
    when = pd.Timestamp(when)
    parts = {k: value_at(sig, k, when, lagged) for k in (*GROWTH, *INFLATION)}
    g = [parts[k] for k in GROWTH if parts[k] is not None]
    i = [parts[k] for k in INFLATION if parts[k] is not None]
    return Axes(when, float(np.mean(g)) if len(g) >= 2 else None, float(np.mean(i)) if i else None, parts)


def quadrant_history(sig: Signals, dates: Sequence) -> pd.Series:
    """Quadrant on each date from what was published by then."""
    return pd.Series([axes_at(sig, d, lagged=True).quadrant for d in dates], index=pd.DatetimeIndex(dates))


def inflation_with_nowcast(series: Dict[str, pd.Series], sig: Signals, month: pd.Timestamp,
                           headline_mm: float, core_mm: float) -> Optional[float]:
    """Inflation axis if next CPI comes in at the nowcast (headline) and trend (core)."""
    vals = []
    for key, mm in (("cpi", headline_mm), ("core", core_mm)):
        lv = _monthly(series[key]).dropna()
        if lv.empty or month <= lv.index[-1]:
            continue
        extended = pd.concat([lv, pd.Series([lv.iloc[-1] * (1 + mm / 100)], index=[month])])
        raw = pace_signal(extended).dropna()
        history = sig.raw[key].dropna()
        if raw.empty or month not in raw.index or history.empty:
            continue
        size = float(scale(history).iloc[-1])
        if size > 0:
            vals.append(float(np.clip(raw[month] / size, -CAP, CAP)))
    return float(np.mean(vals)) if vals else None


# ── Leading indicators ───────────────────────────────────────────────────────

def _change(s: pd.Series, days: int) -> Optional[float]:
    if len(s) < 2:
        return None
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return None if past.empty else float(s.iloc[-1] - past.iloc[-1])


@dataclass
class Leading:
    lines: List[str]
    sahm: Optional[float]
    survey_activity: Optional[float]      # mean of the regional activity indexes, latest month
    survey_activity_change: Optional[float]
    survey_prices_change: Optional[float]


def leading(series: Dict[str, pd.Series]) -> Leading:
    lines = []
    claims = series["claims"]
    if len(claims) >= 26:
        avg4, avg26 = claims.iloc[-4:].mean(), claims.iloc[-26:].mean()
        lines.append(f"    {'Initial jobless claims':<34}{claims.iloc[-1] / 1000:>8.0f}k  {claims.index[-1].date()}"
                     f"   4-week avg {avg4 / 1000:.0f}k vs 26-week {avg26 / 1000:.0f}k ({(avg4 / avg26 - 1) * 100:+.1f}%)")
    cont = series.get("continuing", pd.Series(dtype=float))
    if len(cont):
        yr = cont[cont.index <= cont.index[-1] - pd.Timedelta(days=364)]
        yoy = f"   {(cont.iloc[-1] / yr.iloc[-1] - 1) * 100:+.1f}% in a year" if len(yr) else ""
        lines.append(f"    {'Continuing claims':<34}{cont.iloc[-1] / 1e6:>8.2f}M  {cont.index[-1].date()}{yoy}")
    wei = series.get("wei", pd.Series(dtype=float))
    if len(wei):
        ch = _change(wei, 91)
        lines.append(f"    {'NY Fed Weekly Economic Index':<34}{wei.iloc[-1]:>+8.2f}%  {wei.index[-1].date()}"
                     + (f"   {ch:+.2f} pts in 13 weeks" if ch is not None else "")
                     + "   (scaled to GDP growth)")
    gdp = series.get("gdpnow", pd.Series(dtype=float))
    if len(gdp):
        lines.append(f"    {'Atlanta Fed GDPNow':<34}{gdp.iloc[-1]:>+8.2f}%  quarter of {gdp.index[-1].date()}"
                     "   (annualized real GDP)")
    sahm_s = series.get("sahm", pd.Series(dtype=float))
    sahm = float(sahm_s.iloc[-1]) if len(sahm_s) else None
    if sahm is not None:
        flag = "RECESSION SIGNAL" if sahm >= SAHM_RECESSION else f"below the {SAHM_RECESSION} recession line"
        lines.append(f"    {'Sahm rule (real time)':<34}{sahm:>8.2f}   {sahm_s.index[-1].date()}   {flag}")

    survey_rows, act, act_ch, price_ch = [], [], [], []
    for region, name in (("philly", "Philadelphia Fed"), ("empire", "New York Fed")):
        for part, label in SURVEYS:
            s = series.get(f"{region}_{part}", pd.Series(dtype=float))
            if len(s) < 2:
                continue
            ch = float(s.iloc[-1] - s.iloc[-2])
            survey_rows.append(f"    {name + ': ' + label.lower():<34}{s.iloc[-1]:>+8.1f}   "
                               f"{s.index[-1].strftime('%b %Y')}   {ch:+.1f} vs previous month")
            if part == "activity":
                act.append(float(s.iloc[-1]))
                act_ch.append(ch)
            if part == "prices":
                price_ch.append(ch)
    if survey_rows:
        lines += ["", "    Regional manufacturing surveys (diffusion indexes, 0 = no change; out before the flash PMI):"]
        lines += survey_rows
    mean = lambda v: float(np.mean(v)) if v else None
    return Leading(lines, sahm, mean(act), mean(act_ch), mean(price_ch))


def pmi_hint(lead: Leading) -> List[str]:
    out = []
    if lead.survey_activity_change is not None:
        direction = "improving" if lead.survey_activity_change > 0 else "deteriorating"
        level = "expanding" if (lead.survey_activity or 0) > 0 else "contracting"
        out.append(f"  PMI hint: regional manufacturing {direction} ({lead.survey_activity_change:+.1f} pts; "
                   f"{level} on average)")
    if lead.survey_prices_change is not None:
        direction = "rising" if lead.survey_prices_change > 0 else "easing"
        out.append(f"  PMI input-price hint: prices paid {direction} ({lead.survey_prices_change:+.1f} pts)")
    if out:
        out.append("  Direction only: the S&P Global and ISM indexes themselves are not public.")
    return out


# ── Report ───────────────────────────────────────────────────────────────────

def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:+.2f}"


def axes_lines(ax: Axes, nowcast_axis: Optional[float] = None, nowcast_month: Optional[pd.Timestamp] = None) -> List[str]:
    g = ", ".join(f"{SIGNAL_LABELS[k]} {_fmt(ax.parts[k])}" for k in GROWTH)
    i = ", ".join(f"{SIGNAL_LABELS[k]} {_fmt(ax.parts[k])}" for k in INFLATION)
    lines = [f"    Growth axis    {_fmt(ax.growth):>6}  ({g})  -> "
             + ("n/a" if ax.growth is None else "rising" if ax.growth > 0 else "slowing"),
             f"    Inflation axis {_fmt(ax.inflation):>6}  ({i})  -> "
             + ("n/a" if ax.inflation is None else "rising" if ax.inflation > 0 else "easing")]
    if nowcast_axis is not None and nowcast_month is not None:
        lines.append(f"    Inflation axis with the {nowcast_month.strftime('%B')} CPI nowcast: {nowcast_axis:+.2f} -> "
                     + ("rising" if nowcast_axis > 0 else "easing"))
    lines.append("    Each signal: recent pace vs the past year, in units of its typical size (0 = no change in trend).")
    return lines
