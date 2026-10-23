"""
research/cpi_nowcast.py

Nowcast of the next US CPI release from energy prices, built bottom-up from
CPI sub-items, in the spirit of the Cleveland Fed's inflation nowcast.

1. Each energy sub-item of the CPI (gasoline, fuel oil, electricity, utility
   gas) is regressed on the monthly change of the prices that drive it
   (retail gasoline and diesel, Henry Hub natural gas, with lags), plus month
   dummies: the CPI items are seasonally adjusted, the prices are not.
2. Energy CPI = the sub-items weighted by their measured share of it, and
   headline CPI = core, food and energy weighted the same way. The shares come
   from a non-negative regression on the last five years of published CPI, so
   they track the relative importance of each item without hard-coding it.
3. Core and food have no daily price data: they enter at their 3-month trend.

Monthly price averages give every calendar day the latest observation, so the
month in progress is filled with the last price, as the Cleveland Fed does.
The backtest refits everything on the data before each month since 2010 and
compares the result with the published CPI and with a naive guess. It uses
full months of price data and today's vintage of CPI, so a live nowcast made
mid-month is less precise than the backtest suggests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# key: (FRED id, label, unit)
PRICES: Dict[str, Tuple[str, str, str]] = {
    "gasoline_retail": ("GASREGW", "Gasoline, retail", "$/gal"),
    "diesel_retail": ("GASDESW", "Diesel, retail", "$/gal"),
    "wti": ("DCOILWTICO", "WTI crude", "$/bbl"),
    "brent": ("DCOILBRENTEU", "Brent crude", "$/bbl"),
    "gasoline_nyh": ("DGASNYH", "Gasoline, NY Harbor", "$/gal"),
    "ulsd_nyh": ("DDFUELNYH", "Diesel ULSD, NY Harbor", "$/gal"),
    "heating_oil": ("DHOILNYH", "Heating oil, NY Harbor", "$/gal"),
    "henry_hub": ("DHHNGSP", "Natural gas, Henry Hub", "$/MMBtu"),
}
CPI: Dict[str, Tuple[str, str]] = {
    "headline": ("CPIAUCSL", "Headline CPI"),
    "core": ("CPILFESL", "Core (trend)"),
    "food": ("CPIUFDSL", "Food (trend)"),
    "energy": ("CPIENGSL", "Energy"),
    "gasoline": ("CUSR0000SETB01", "Gasoline"),
    "fuel_oil": ("CUSR0000SEHE", "Fuel oil"),
    "electricity": ("CUSR0000SEHF01", "Electricity"),
    "utility_gas": ("CUSR0000SEHF02", "Utility gas"),
}
REQUIRED_CPI = ("headline", "energy", "core")
KEY_PRICE = "gasoline_retail"       # its last observation decides which months can be nowcast
KEY_PRICE_DAYS = 7                  # weekly survey: each Monday price stands for its week

# Energy sub-items and the (price, lag in months) that drive each one
ITEMS: Dict[str, Sequence[Tuple[str, int]]] = {
    "gasoline": (("gasoline_retail", 0),),
    "fuel_oil": (("diesel_retail", 0), ("diesel_retail", 1)),
    "electricity": (("henry_hub", 1), ("henry_hub", 2), ("henry_hub", 3)),
    "utility_gas": (("henry_hub", 0), ("henry_hub", 1), ("henry_hub", 2)),
}
TRENDS = ("core", "food")

HISTORY_START = date(1990, 1, 1)
BACKTEST_FROM = pd.Timestamp("2010-01-01")
TREND_MONTHS = 3
WEIGHT_MONTHS = 60
MIN_TRAIN = 60
CRACK_GALLONS = 42.0                # gallons per barrel

# daily alerts: move over the last 30 days, in %
CRUDE_MOVE = 15.0
RETAIL_MOVE = 10.0
ALERT_PRICES = {"wti": CRUDE_MOVE, "brent": CRUDE_MOVE, "gasoline_retail": RETAIL_MOVE,
                "diesel_retail": RETAIL_MOVE}

FOOTNOTE = ("  Ranges: 5th-95th percentile of backtest errors since 2010 (full months of data).\n"
            "  Mid-month the rest of the month is filled with the latest price, so errors are larger.\n"
            "  Core and food enter at their 3-month trend: this is an energy nowcast, not a full forecast.")


class NowcastError(Exception):
    """Raised when a required series is unavailable."""


def _empty() -> pd.Series:
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def fetch(fred, ids: Dict[str, str], start: date, end: date, required: Sequence[str] = ()) -> Dict[str, pd.Series]:
    """FRED series by key; optional ones that fail become empty with a warning."""
    out = {}
    for key, sid in ids.items():
        try:
            s = fred.get_series(sid, observation_start=str(start), observation_end=str(end))
            s = pd.Series(s, dtype=float).dropna().sort_index()
            s.index = pd.DatetimeIndex(s.index)
        except Exception as e:
            if key in required:
                raise NowcastError(f"FRED series {sid} is unavailable ({e})")
            logger.warning("nowcast_input_unavailable", series=sid, error=str(e))
            s = _empty()
        out[key] = s
    return out


def fetch_prices(fred, start: date, end: date) -> Dict[str, pd.Series]:
    return fetch(fred, {k: v[0] for k, v in PRICES.items()}, start, end)


# ── Monthly series ───────────────────────────────────────────────────────────

def month_start(d) -> pd.Timestamp:
    return pd.Timestamp(d).to_period("M").to_timestamp()


def monthly_average(s: pd.Series) -> pd.Series:
    """Average over calendar days, each carrying the latest observation; the last month is filled to its end."""
    if s.empty:
        return pd.Series(dtype=float)
    daily = s.resample("D").last().ffill()
    month_end = month_start(s.index[-1]) + pd.offsets.MonthEnd(0)
    daily = daily.reindex(pd.date_range(daily.index[0], month_end, freq="D")).ffill()
    return daily.resample("MS").mean()


def _continuous(s: pd.Series) -> pd.Series:
    """Monthly series on a gap-free month-start index (missing months stay NaN)."""
    s = s.copy()
    s.index = pd.DatetimeIndex([month_start(d) for d in s.index])
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(pd.date_range(s.index[0], s.index[-1], freq="MS"))


def pct_change(s: pd.Series) -> pd.Series:
    """Month-on-month change in %; a month next to a missing one is NaN, never a 2-month change."""
    if s.empty:
        return pd.Series(dtype=float)
    s = _continuous(s)
    return (s / s.shift(1) - 1) * 100


def build_frame(prices: Dict[str, pd.Series], cpi: Dict[str, pd.Series]) -> pd.DataFrame:
    """CPI m/m changes (columns by CPI key) and price m/m changes (columns 'p:<price key>')."""
    cols = {key: pct_change(s) for key, s in cpi.items() if len(s)}
    cols.update({f"p:{key}": pct_change(monthly_average(s)) for key, s in prices.items() if len(s)})
    starts = [c.index[0] for c in cols.values() if len(c)]
    ends = [c.index[-1] for c in cols.values() if len(c)]
    index = pd.date_range(min(starts), max(ends), freq="MS")
    return pd.DataFrame({k: v.reindex(index) for k, v in cols.items()}, index=index)


# ── Models ───────────────────────────────────────────────────────────────────

def _design(F: pd.DataFrame, spec: Sequence[Tuple[str, int]], index: pd.DatetimeIndex) -> pd.DataFrame:
    X = pd.DataFrame({"const": 1.0}, index=index)
    for key, lag in spec:
        X[f"{key}_l{lag}"] = F[f"p:{key}"].shift(lag).reindex(index)
    for m in range(2, 13):
        X[f"m{m}"] = (index.month == m).astype(float)
    return X


def available_spec(F: pd.DataFrame, item: str, month: pd.Timestamp) -> List[Tuple[str, int]]:
    """The item's predictors that have data for ``month``."""
    out = []
    for key, lag in ITEMS[item]:
        col = f"p:{key}"
        if col in F and month in F.index and pd.notna(F[col].shift(lag).get(month)):
            out.append((key, lag))
    return out


class Designs:
    """Regression matrices per item and predictor set, built once per frame."""

    def __init__(self, F: pd.DataFrame):
        self.F = F
        self._cache: Dict[Tuple[str, Tuple[Tuple[str, int], ...]], Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def get(self, item: str, spec: Sequence[Tuple[str, int]]):
        key = (item, tuple(spec))
        if key not in self._cache:
            X = _design(self.F, spec, self.F.index)
            y = self.F[item]
            self._cache[key] = (X.to_numpy(), y.to_numpy(), (X.notna().all(axis=1) & y.notna()).to_numpy())
        return self._cache[key]


def predict_item(F: pd.DataFrame, item: str, month: pd.Timestamp, cutoff: pd.Timestamp,
                 designs: Optional[Designs] = None) -> Optional[float]:
    """Item m/m for ``month`` from a regression fitted on the months before ``cutoff``."""
    designs = designs or Designs(F)
    X, y, ok = designs.get(item, available_spec(F, item, month))
    train = ok & (F.index < cutoff)
    if train.sum() < MIN_TRAIN:
        return None
    coef, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
    return float(X[F.index.get_loc(month)] @ coef)


def nonneg_lstsq(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least squares with non-negative weights (drops negative ones and refits until none are left)."""
    active = list(range(X.shape[1]))
    w = np.zeros(X.shape[1])
    while active:
        sol, *_ = np.linalg.lstsq(X[:, active], y, rcond=None)
        if (sol >= 0).all():
            w[active] = sol
            break
        active = [a for a, v in zip(active, sol) if v > 0]
    return w


def weights(F: pd.DataFrame, target: str, parts: Sequence[str], cutoff: pd.Timestamp) -> Optional[Dict[str, float]]:
    """Share of each part in ``target``, measured on the last WEIGHT_MONTHS months before ``cutoff``."""
    rows = F.loc[F.index < cutoff, [target, *parts]].dropna().iloc[-WEIGHT_MONTHS:]
    if len(rows) < 24:
        return None
    return dict(zip(parts, nonneg_lstsq(rows[list(parts)].to_numpy(), rows[target].to_numpy())))


def trend(F: pd.DataFrame, key: str, cutoff: pd.Timestamp) -> Optional[float]:
    s = F.loc[F.index < cutoff, key].dropna().iloc[-TREND_MONTHS:]
    return float(s.mean()) if len(s) else None


def items_in(F: pd.DataFrame) -> List[str]:
    return [i for i in ITEMS if i in F and F[i].notna().any()]


def predict_month(F: pd.DataFrame, month: pd.Timestamp, cutoff: pd.Timestamp,
                  designs: Optional[Designs] = None) -> Optional[Dict[str, object]]:
    """Every sub-item, energy and headline for ``month``, using only data before ``cutoff``."""
    designs = designs or Designs(F)
    items = items_in(F)
    item_hat = {i: predict_item(F, i, month, cutoff, designs) for i in items}
    items = [i for i in items if item_hat[i] is not None]
    w_energy = weights(F, "energy", items, cutoff) if items else None
    parts = [p for p in (*TRENDS, "energy") if p in F]
    w_head = weights(F, "headline", parts, cutoff)
    trends = {k: trend(F, k, cutoff) for k in TRENDS if k in F}
    if w_energy is None or w_head is None or any(v is None for v in trends.values()):
        return None
    energy = sum(w_energy[i] * item_hat[i] for i in items)
    values = {**item_hat, **trends, "energy": energy}
    headline = sum(w_head[p] * values[p] for p in parts)
    contrib = {p: w_head[p] * values[p] for p in parts}
    contrib.update({i: w_head.get("energy", 0.0) * w_energy[i] * item_hat[i] for i in items})
    return {"values": {**values, "headline": headline}, "items": items,
            "w_energy": w_energy, "w_head": w_head, "contrib": contrib}


# ── Backtest ─────────────────────────────────────────────────────────────────

def backtest(F: pd.DataFrame, start: pd.Timestamp = BACKTEST_FROM) -> pd.DataFrame:
    """One row per month and target: published value, nowcast and naive guess (all fits before the month)."""
    rows, designs = [], Designs(F)
    for month in F.index[(F.index >= start) & F["headline"].notna()]:
        pred = predict_month(F, month, month, designs)
        if pred is None:
            continue
        for target in (*pred["items"], "energy", "headline"):
            actual = F.at[month, target]
            if pd.isna(actual):
                continue
            history = F.loc[F.index < month, target].dropna()
            naive = trend(F, "headline", month) if target == "headline" else float(history.mean())
            rows.append({"month": month.date(), "target": target, "actual": float(actual),
                         "nowcast": float(pred["values"][target]), "naive": naive})
    return pd.DataFrame(rows, columns=["month", "target", "actual", "nowcast", "naive"])


def backtest_stats(bt: pd.DataFrame) -> pd.DataFrame:
    """Per target: months, mean absolute error of the nowcast and the naive guess, 5%/95% error quantiles."""
    out = {}
    for target, g in bt.groupby("target"):
        err = g["actual"] - g["nowcast"]
        out[target] = {"n": len(g), "mae": float(err.abs().mean()),
                       "naive_mae": float((g["actual"] - g["naive"]).abs().mean()),
                       "q05": float(err.quantile(0.05)), "q95": float(err.quantile(0.95))}
    return pd.DataFrame(out).T


# ── Live nowcast ─────────────────────────────────────────────────────────────

@dataclass
class CpiNowcast:
    last_cpi_month: pd.Timestamp
    last_headline: float               # m/m %, last published
    table: pd.DataFrame                # one row per target month and item
    stats: pd.DataFrame
    backtest: pd.DataFrame
    prices: Dict[str, pd.Series]
    key_price_through: date
    observed_share: Dict[pd.Timestamp, float]

    @property
    def months(self) -> List[pd.Timestamp]:
        return sorted(self.table["month"].unique()) if len(self.table) else []

    def value(self, month: pd.Timestamp, item: str) -> Optional[float]:
        r = self.table[(self.table["month"] == month) & (self.table["item"] == item)]
        return None if r.empty else float(r.iloc[0]["mm"])


def target_months(F: pd.DataFrame, prices: Dict[str, pd.Series]) -> List[pd.Timestamp]:
    """Months after the last published CPI that already have retail gasoline data."""
    last_cpi = F["headline"].dropna().index[-1]
    key = prices.get(KEY_PRICE, _empty())
    if key.empty:
        return []
    last = month_start(key.index[-1])
    return list(pd.date_range(last_cpi + pd.offsets.MonthBegin(1), last, freq="MS"))


def yoy(level: pd.Series, month: pd.Timestamp, path: Dict[pd.Timestamp, float]) -> Optional[float]:
    """Year-on-year % for ``month`` from the last published level chained with the nowcast m/m path."""
    lv = _continuous(level)
    last = lv.dropna().index[-1]
    base = lv.get(month - pd.DateOffset(years=1))
    if base is None or pd.isna(base):
        return None
    value = float(lv[last])
    for m in pd.date_range(last + pd.offsets.MonthBegin(1), month, freq="MS"):
        if m not in path:
            return None
        value *= 1 + path[m] / 100
    return (value / float(base) - 1) * 100


def nowcast(prices: Dict[str, pd.Series], cpi: Dict[str, pd.Series]) -> CpiNowcast:
    F = build_frame(prices, cpi)
    if "headline" not in F or F["headline"].dropna().empty:
        raise NowcastError("no published CPI to build on")
    months = target_months(F, prices)
    if months:
        F = F.reindex(F.index.union(pd.DatetimeIndex(months)))
    bt = backtest(F)
    stats = backtest_stats(bt) if len(bt) else pd.DataFrame()
    last_cpi = F["headline"].dropna().index[-1]
    cutoff = last_cpi + pd.offsets.MonthBegin(1)

    rows, paths = [], {}
    for month in months:
        pred = predict_month(F, month, cutoff)
        if pred is None:
            continue
        v = pred["values"]
        for item in (*pred["items"], "energy", *[t for t in TRENDS if t in v], "headline"):
            paths.setdefault(item, {})[month] = v[item]
        for item in (*pred["items"], "energy", *[t for t in TRENDS if t in v], "headline"):
            s = stats.loc[item] if item in stats.index else None
            weight = pred["w_energy"].get(item) if item in pred["items"] else pred["w_head"].get(item)
            rows.append({
                "month": month, "item": item, "label": CPI[item][1], "mm": v[item],
                "low": v[item] + s["q05"] if s is not None else np.nan,
                "high": v[item] + s["q95"] if s is not None else np.nan,
                "weight": weight if weight is not None else np.nan,   # headline has none
                "contribution": pred["contrib"].get(item, np.nan) if item != "headline" else np.nan,
                "yoy": (yoy(cpi[item], month, paths[item]) if item in cpi and len(cpi[item]) else None),
                "kind": "trend" if item in TRENDS else "nowcast",
            })

    key = prices.get(KEY_PRICE, _empty())
    through = key.index[-1].date() if len(key) else None
    shares = {}
    for month in months:
        month_end = month + pd.offsets.MonthEnd(0)
        covered = pd.Timestamp(through) + pd.Timedelta(days=KEY_PRICE_DAYS - 1) if through else month_end
        shares[month] = min(max((covered - month).days + 1, 0) / month_end.day, 1.0)
    table = pd.DataFrame(rows, columns=["month", "item", "label", "mm", "low", "high", "weight",
                                        "contribution", "yoy", "kind"])
    return CpiNowcast(last_cpi, float(F.at[last_cpi, "headline"]), table, stats, bt, prices, through, shares)


def run(fred, today: Optional[date] = None) -> CpiNowcast:
    today = today or date.today()
    prices = fetch_prices(fred, HISTORY_START, today)
    if prices[KEY_PRICE].empty:
        raise NowcastError(f"FRED series {PRICES[KEY_PRICE][0]} (retail gasoline) returned no data")
    cpi = fetch(fred, {k: v[0] for k, v in CPI.items()}, HISTORY_START, today, REQUIRED_CPI)
    return nowcast(prices, cpi)


# ── Energy prices and alerts ─────────────────────────────────────────────────

def crack_321(prices: Dict[str, pd.Series]) -> pd.Series:
    """3-2-1 crack spread, $/bbl: (2 bbl gasoline + 1 bbl diesel) / 3 - 1 bbl WTI, NY Harbor spot."""
    parts = [prices.get(k, _empty()) for k in ("gasoline_nyh", "ulsd_nyh", "wti")]
    if any(p.empty for p in parts):
        return _empty()
    df = pd.concat(parts, axis=1, keys=["gas", "ulsd", "wti"]).dropna()
    return (2 * df["gas"] + df["ulsd"]) * CRACK_GALLONS / 3 - df["wti"]


def _asof(s: pd.Series, d: pd.Timestamp) -> Optional[float]:
    sub = s[s.index <= d]
    return None if sub.empty else float(sub.iloc[-1])


def latest_levels(prices: Dict[str, pd.Series]) -> Dict[str, float]:
    """Last value of each price and of the crack spread (NaN when missing)."""
    out = {k: float(s.iloc[-1]) if len(s) else np.nan for k, s in prices.items()}
    crack = crack_321(prices)
    out["crack_321"] = float(crack.iloc[-1]) if len(crack) else np.nan
    return out


def move(s: pd.Series, days: int) -> Optional[Tuple[float, float, float]]:
    """(earlier value, latest value, % change) over the last ``days`` calendar days."""
    if s.empty:
        return None
    before = _asof(s, s.index[-1] - pd.Timedelta(days=days))
    if before is None or before == 0:
        return None
    now = float(s.iloc[-1])
    return before, now, (now / before - 1) * 100


def energy_alerts(prices: Dict[str, pd.Series], days: int = 30) -> List[str]:
    out = []
    for key, limit in ALERT_PRICES.items():
        m = move(prices.get(key, _empty()), days)
        if m and abs(m[2]) >= limit:
            direction = "up" if m[2] > 0 else "down"
            effect = "pressure on energy CPI" if m[2] > 0 else "relief for energy CPI"
            out.append(f"{PRICES[key][1]} {direction} {abs(m[2]):.1f}% in {days} days "
                       f"({m[0]:.2f} -> {m[1]:.2f}): {effect}")
    return out


def price_lines(prices: Dict[str, pd.Series]) -> List[str]:
    """Latest price, this month's average vs last month's, and the change over a year."""
    lines = [f"    {'Price':<26}{'latest':>10}  {'as of':<11}{'month avg':>10}{'1 year':>9}"]
    for key, (sid, label, unit) in PRICES.items():
        s = prices.get(key, _empty())
        if s.empty:
            lines.append(f"    {label:<26}{'n/a':>10}  ({sid} unavailable)")
            continue
        avg = monthly_average(s)
        mo = (avg.iloc[-1] / avg.iloc[-2] - 1) * 100 if len(avg) > 1 else np.nan
        yr = move(s, 365)
        lines.append(f"    {label:<26}{s.iloc[-1]:>10.2f}  {s.index[-1].date()!s:<11}"
                     f"{mo:>+9.1f}%{(yr[2] if yr else np.nan):>+8.1f}%   {unit}")
    crack = crack_321(prices)
    if len(crack):
        avg = monthly_average(crack)
        diff = avg.iloc[-1] - avg.iloc[-2] if len(avg) > 1 else np.nan
        lines.append(f"    {'Refining margin 3-2-1':<26}{crack.iloc[-1]:>10.2f}  {crack.index[-1].date()!s:<11}"
                     f"{diff:>+8.2f}$ {'':>8}   $/bbl")
    return lines


# ── Report ───────────────────────────────────────────────────────────────────

def _month_name(m: pd.Timestamp) -> str:
    return m.strftime("%B %Y")


def _f(v, spec: str) -> str:
    return "n/a" if v is None or pd.isna(v) else format(v, spec)


def table_lines(nc: CpiNowcast, month: pd.Timestamp) -> List[str]:
    t = nc.table[nc.table["month"] == month]
    lines = [f"    {'CPI item':<14}{'share':>7}{'m/m':>9}  {'90% range':<18}{'to headline':>12}{'y/y':>8}"]
    for _, r in t.iterrows():
        if r["item"] == "energy":
            lines.append("    " + "-" * 66)
        if r["item"] == "headline":
            lines.append("    " + "=" * 66)
        if r["kind"] == "trend":
            rng = "3-month avg"
        else:
            rng = f"[{r['low']:+.2f} to {r['high']:+.2f}]" if pd.notna(r["low"]) else ""
        name = ("  " if r["item"] in ITEMS else "") + r["label"]
        contribution = f"{r['contribution']:+.2f} pts" if pd.notna(r["contribution"]) else ""
        change = f"{r['yoy']:+.1f}%" if r["yoy"] is not None and pd.notna(r["yoy"]) else "n/a"
        share = format(r["weight"], ".0%") if pd.notna(r["weight"]) else ""
        lines.append(f"    {name:<14}{share:>7}{r['mm']:>+8.2f}%  {rng:<18}"
                     f"{contribution:>12}{change:>8}")
    return lines


def stats_lines(nc: CpiNowcast) -> List[str]:
    if nc.stats.empty:
        return ["    (not enough history for a backtest)"]
    lines = [f"    {'Target':<14}{'months':>7}{'error (MAE)':>13}{'naive guess':>13}   better than naive"]
    for item in (*ITEMS, "energy", "headline"):
        if item not in nc.stats.index:
            continue
        s = nc.stats.loc[item]
        gain = 1 - s["mae"] / s["naive_mae"] if s["naive_mae"] else np.nan
        lines.append(f"    {CPI[item][1]:<14}{int(s['n']):>7}{s['mae']:>12.2f}%{s['naive_mae']:>12.2f}%"
                     f"   {'yes' if gain > 0 else 'NO'} ({gain:+.0%})")
    return lines


def report_lines(nc: CpiNowcast) -> List[str]:
    sep = "─" * 72
    lines = [sep, "  CPI NOWCAST FROM ENERGY PRICES (FRED)", sep,
             f"  Last published CPI: {_month_name(nc.last_cpi_month)} (headline {nc.last_headline:+.2f}% m/m)",
             f"  Retail gasoline data through {nc.key_price_through}"]
    if not nc.months:
        lines += ["", "  No month to nowcast yet: wait for the first weekly retail gasoline price of the month."]
    for month in nc.months:
        share = nc.observed_share.get(month, 1.0)
        lines += ["", f"  Nowcast for {_month_name(month)}  ({share:.0%} of the month observed)"]
        lines += table_lines(nc, month)
    lines += ["", "  Energy prices:"] + price_lines(nc.prices)
    lines += [f"    ! {a}" for a in energy_alerts(nc.prices)]
    lines += ["", "  Backtest since 2010 (m/m, each month fitted only on earlier data):"] + stats_lines(nc)
    lines += ["  Naive guess: the item's historical average; for headline, its 3-month average.",
              "  Share: weight within energy for its items, within headline otherwise (measured on 5 years).",
              FOOTNOTE, sep]
    return lines


def summary_line(nc: CpiNowcast) -> Optional[str]:
    """One line for the daily report: the first month still unpublished."""
    if not nc.months:
        return None
    m = nc.months[0]
    head = nc.table[(nc.table["month"] == m) & (nc.table["item"] == "headline")]
    energy = nc.value(m, "energy")
    if head.empty:
        return None
    h = head.iloc[0]
    rng = f" [{h['low']:+.2f} to {h['high']:+.2f}]" if pd.notna(h["low"]) else ""
    return (f"  CPI nowcast {_month_name(m)}: headline {h['mm']:+.2f}% m/m{rng}, "
            f"energy {_f(energy, '+.1f')}%  (details: `nowcast`)")


# ── Cross-check with a policy statement ──────────────────────────────────────

TARGET_ANNUAL = 2.0     # Fed inflation target, %
BAND = 1.0              # +/- percentage points around it counted as "near target"
HAWKISH = {"hawkish", "very_hawkish"}
DOVISH = {"dovish", "very_dovish", "crisis_easing"}


def annualized(mm: float) -> float:
    return ((1 + mm / 100) ** 12 - 1) * 100


def inflation_check(nc: CpiNowcast, stance: str) -> List[str]:
    """Does the statement's tone match the inflation pressure measured from energy prices?"""
    if not nc.months:
        return ["  No CPI month to nowcast yet; no cross-check."]
    m = nc.months[0]
    head, energy = nc.value(m, "headline"), nc.value(m, "energy")
    if head is None:
        return ["  CPI nowcast unavailable; no cross-check."]
    ann = annualized(head)
    if ann > TARGET_ANNUAL + BAND:
        pressure = "above"
    elif ann < TARGET_ANNUAL - BAND:
        pressure = "below"
    else:
        pressure = "near"
    lines = [f"  CPI nowcast {_month_name(m)}: headline {head:+.2f}% m/m ({ann:+.1f}% annualized, "
             f"{pressure} the 2% target), energy {_f(energy, '+.1f')}% m/m",
             f"  Statement tone: {stance}"]
    tone = "hawkish" if stance in HAWKISH else "dovish" if stance in DOVISH else None
    if tone is None or pressure == "near":
        lines.append("  -> No clear confirmation either way: rely on the market data above.")
    elif (tone == "hawkish") == (pressure == "above"):
        lines.append(f"  -> CONSISTENT: a {tone} tone with inflation {pressure} target makes the reading more credible.")
    else:
        lines.append(f"  -> CONTRADICTS: a {tone} tone with inflation {pressure} target; "
                     "treat the reading with caution and watch the 2Y yield.")
    lines.append("  The nowcast uses today's data, not the data on the event date.")
    return lines
