"""
research/fomc_study.py

Event study: how do Bitcoin, the S&P 500 and the VIX react to FOMC announcements?

Method:
- Monetary surprise = change in the 2-year Treasury yield (DGS2) from the last close
  before the announcement to the first close on or after it. The 2Y yield reprices
  the expected policy path, so its move is a standard market-based surprise measure.
- Asset reaction = change from the last close before the announcement day to the
  first close on or after the following day. The two-day window always contains the
  announcement, whatever time-of-day convention a daily series uses (Bitcoin trades
  24/7 and FRED's CBBTCUSD is a daily series).
- Events are classified hawkish / dovish / neutral by the size of the surprise.

Caveats: daily data mixes the announcement with other news in the window; the sample
is small (one event every ~6 weeks); results are associations, not causal estimates.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# FOMC policy announcement dates (last day of each meeting), 2017-2025.
# True marks unscheduled meetings. The 2020-03-17/18 meeting was replaced by
# the 2020-03-15 emergency action. November meetings in 2018, 2020 and 2024
# ended on Thursday because of US elections.
# Compiled without access to federalreserve.gov: validate_dates() cross-checks
# them against FRED's target-rate series every time the study runs.
FOMC_ANNOUNCEMENTS = (
    ("2017-02-01", False), ("2017-03-15", False), ("2017-05-03", False), ("2017-06-14", False),
    ("2017-07-26", False), ("2017-09-20", False), ("2017-11-01", False), ("2017-12-13", False),
    ("2018-01-31", False), ("2018-03-21", False), ("2018-05-02", False), ("2018-06-13", False),
    ("2018-08-01", False), ("2018-09-26", False), ("2018-11-08", False), ("2018-12-19", False),
    ("2019-01-30", False), ("2019-03-20", False), ("2019-05-01", False), ("2019-06-19", False),
    ("2019-07-31", False), ("2019-09-18", False), ("2019-10-30", False), ("2019-12-11", False),
    ("2020-01-29", False), ("2020-03-03", True), ("2020-03-15", True), ("2020-04-29", False),
    ("2020-06-10", False), ("2020-07-29", False), ("2020-09-16", False), ("2020-11-05", False),
    ("2020-12-16", False),
    ("2021-01-27", False), ("2021-03-17", False), ("2021-04-28", False), ("2021-06-16", False),
    ("2021-07-28", False), ("2021-09-22", False), ("2021-11-03", False), ("2021-12-15", False),
    ("2022-01-26", False), ("2022-03-16", False), ("2022-05-04", False), ("2022-06-15", False),
    ("2022-07-27", False), ("2022-09-21", False), ("2022-11-02", False), ("2022-12-14", False),
    ("2023-02-01", False), ("2023-03-22", False), ("2023-05-03", False), ("2023-06-14", False),
    ("2023-07-26", False), ("2023-09-20", False), ("2023-11-01", False), ("2023-12-13", False),
    ("2024-01-31", False), ("2024-03-20", False), ("2024-05-01", False), ("2024-06-12", False),
    ("2024-07-31", False), ("2024-09-18", False), ("2024-11-07", False), ("2024-12-18", False),
    ("2025-01-29", False), ("2025-03-19", False), ("2025-05-07", False), ("2025-06-18", False),
    ("2025-07-30", False), ("2025-09-17", False), ("2025-10-29", False), ("2025-12-10", False),
)

# FRED series used by the study
SERIES = {
    "btc": "CBBTCUSD",      # Coinbase Bitcoin, USD, daily
    "spx": "SP500",         # S&P 500 close
    "vix": "VIXCLS",        # VIX close
    "y2": "DGS2",           # 2-year Treasury constant-maturity yield, %
    "fed_upper": "DFEDTARU",  # Fed funds target range, upper bound, %
}

STANCES = ("hawkish", "neutral", "dovish")


def announcement_dates(start: Optional[date] = None, end: Optional[date] = None) -> List[date]:
    out = []
    for d, _ in FOMC_ANNOUNCEMENTS:
        dt = date.fromisoformat(d)
        if (start is None or dt >= start) and (end is None or dt <= end):
            out.append(dt)
    return out


def _is_unscheduled(d: date) -> bool:
    return dict(FOMC_ANNOUNCEMENTS).get(d.isoformat(), False)


def fetch_series(fred, start: date, end: date) -> Dict[str, pd.Series]:
    """Download every study series from FRED, padded so windows at the edges are complete."""
    out = {}
    for key, series_id in SERIES.items():
        s = fred.get_series(
            series_id,
            observation_start=str(start - timedelta(days=30)),
            observation_end=str(end + timedelta(days=30)),
        )
        out[key] = s.dropna().sort_index()
    return out


def _last_before(s: pd.Series, d: date) -> Optional[float]:
    sub = s[s.index < pd.Timestamp(d)]
    return float(sub.iloc[-1]) if len(sub) else None


def _first_on_or_after(s: pd.Series, d: date) -> Optional[float]:
    sub = s[s.index >= pd.Timestamp(d)]
    return float(sub.iloc[0]) if len(sub) else None


def _last_on_or_before(s: pd.Series, d: date) -> Optional[float]:
    sub = s[s.index <= pd.Timestamp(d)]
    return float(sub.iloc[-1]) if len(sub) else None


def _diff(after: Optional[float], before: Optional[float], scale: float = 1.0) -> float:
    if after is None or before is None:
        return np.nan
    return (after - before) * scale


def _pct(after: Optional[float], before: Optional[float]) -> float:
    if after is None or before is None or before == 0:
        return np.nan
    return (after / before - 1) * 100


def classify(surprise_bps: float, threshold_bps: float) -> str:
    if np.isnan(surprise_bps):
        return "n/a"
    if surprise_bps >= threshold_bps:
        return "hawkish"
    if surprise_bps <= -threshold_bps:
        return "dovish"
    return "neutral"


def event_table(
    series: Dict[str, pd.Series],
    dates: List[date],
    surprise_threshold_bps: float = 5.0,
) -> pd.DataFrame:
    """One row per announcement with the surprise, the decision and each asset's reaction."""
    rows = []
    for d in dates:
        window_end = d + timedelta(days=1)
        surprise = _diff(_first_on_or_after(series["y2"], d), _last_before(series["y2"], d), 100)
        rows.append({
            "date": d,
            "unscheduled": _is_unscheduled(d),
            "rate_change_bps": _diff(
                _last_on_or_before(series["fed_upper"], d + timedelta(days=3)),
                _last_before(series["fed_upper"], d),
                100,
            ),
            "surprise_bps": surprise,
            "stance": classify(surprise, surprise_threshold_bps),
            "btc_pct": _pct(_first_on_or_after(series["btc"], window_end), _last_before(series["btc"], d)),
            "spx_pct": _pct(_first_on_or_after(series["spx"], window_end), _last_before(series["spx"], d)),
            "vix_change": _diff(_first_on_or_after(series["vix"], window_end), _last_before(series["vix"], d)),
        })
    return pd.DataFrame(rows)


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    """Average reaction by stance, plus all events."""
    groups = [(stance, events[events["stance"] == stance]) for stance in STANCES]
    groups.append(("all", events[events["stance"] != "n/a"]))
    rows = []
    for name, g in groups:
        btc = g["btc_pct"].dropna()
        rows.append({
            "stance": name,
            "n": len(g),
            "btc_mean_pct": btc.mean(),
            "btc_median_pct": btc.median(),
            "btc_up_share": (btc > 0).mean() if len(btc) else np.nan,
            "spx_mean_pct": g["spx_pct"].mean(),
            "vix_mean_change": g["vix_change"].mean(),
        })
    return pd.DataFrame(rows).set_index("stance")


def btc_baseline(btc: pd.Series, events: pd.DataFrame) -> Dict[str, float]:
    """Are FOMC windows more volatile for Bitcoin than an ordinary two-day window?"""
    two_day = btc.pct_change(2).dropna() * 100
    first, last = events["date"].min(), events["date"].max()
    two_day = two_day[(two_day.index >= pd.Timestamp(first)) & (two_day.index <= pd.Timestamp(last))]
    event_moves = events["btc_pct"].dropna()
    typical = two_day.abs().mean()
    on_events = event_moves.abs().mean()
    return {
        "typical_abs_move_pct": typical,
        "fomc_abs_move_pct": on_events,
        "ratio": on_events / typical if typical else np.nan,
        "corr_btc_vs_surprise": events["btc_pct"].corr(events["surprise_bps"]),
        "corr_btc_vs_spx": events["btc_pct"].corr(events["spx_pct"]),
    }


def validate_dates(fed_upper: pd.Series, dates: List[date], start: date, end: date) -> List[date]:
    """
    Target-rate changes in FRED that no listed announcement explains.

    A change takes effect on the announcement day or within the following three days
    (usually the next day). An unexplained change means the date list is missing or
    misdating a meeting.
    """
    s = fed_upper[(fed_upper.index >= pd.Timestamp(start)) & (fed_upper.index <= pd.Timestamp(end))]
    changes = s[s.diff().fillna(0) != 0].index
    unexplained = []
    for ts in changes:
        changed = ts.date()
        if not any(0 <= (changed - d).days <= 3 for d in dates):
            unexplained.append(changed)
    return unexplained


def beta(x: pd.Series, y: pd.Series, n_boot: int = 2000, seed: int = 0) -> Dict[str, float]:
    """
    OLS slope of y on x with a percentile-bootstrap 95% interval.

    The bootstrap makes no normality assumption, which matters for Bitcoin's
    fat-tailed returns. A fixed seed keeps the interval reproducible.
    """
    mask = x.notna() & y.notna()
    xv, yv = x[mask].to_numpy(dtype=float), y[mask].to_numpy(dtype=float)
    n = len(xv)
    if n < 5 or np.var(xv) == 0:
        return {"beta": np.nan, "ci_low": np.nan, "ci_high": np.nan, "n": n}

    slope = np.cov(xv, yv, bias=True)[0, 1] / np.var(xv)

    idx = np.random.default_rng(seed).integers(0, n, size=(n_boot, n))
    xs, ys = xv[idx], yv[idx]
    xd = xs - xs.mean(axis=1, keepdims=True)
    yd = ys - ys.mean(axis=1, keepdims=True)
    var = (xd ** 2).sum(axis=1)
    ok = var > 0
    boots = (xd * yd).sum(axis=1)[ok] / var[ok]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"beta": float(slope), "ci_low": float(lo), "ci_high": float(hi), "n": n}


def event_beta(events: pd.DataFrame) -> Dict[str, float]:
    """Bitcoin's beta to the S&P 500 in the FOMC reaction windows."""
    return beta(events["spx_pct"], events["btc_pct"])


def _two_session_moves(btc: pd.Series, spx: pd.Series, start: date, end: date):
    """% moves of the S&P 500 and Bitcoin over non-overlapping two-session windows."""
    spx = spx[(spx.index >= pd.Timestamp(start)) & (spx.index <= pd.Timestamp(end))]
    btc_on_spx_days = btc.reindex(spx.index, method="ffill")
    every_other = spx.index[::2]
    spx_ret = spx.reindex(every_other).pct_change() * 100
    btc_ret = btc_on_spx_days.reindex(every_other).pct_change() * 100
    return spx_ret, btc_ret


def window_beta(btc: pd.Series, spx: pd.Series, start: date, end: date) -> Dict[str, float]:
    """
    Bitcoin's beta to the S&P 500 over all non-overlapping two-session windows
    between ``start`` and ``end``. Same window length as the event study, many
    more observations, so a much tighter interval.
    """
    return beta(*_two_session_moves(btc, spx, start, end))


def downside_ratio(
    btc: pd.Series,
    spx: pd.Series,
    start: date,
    end: date,
    threshold_pct: float = -2.0,
    n_boot: int = 2000,
    seed: int = 0,
) -> Dict[str, float]:
    """
    How far Bitcoin falls, on average, when the S&P 500 falls sharply:
    mean Bitcoin move / mean S&P 500 move over the two-session windows where
    the S&P 500 moved ``threshold_pct`` or worse. This is the multiplier the
    scenario engine applies to equity losses in tail scenarios.
    """
    spx_ret, btc_ret = _two_session_moves(btc, spx, start, end)
    mask = (spx_ret <= threshold_pct) & btc_ret.notna()
    xv, yv = spx_ret[mask].to_numpy(dtype=float), btc_ret[mask].to_numpy(dtype=float)
    n = len(xv)
    if n < 5:
        return {"ratio": np.nan, "ci_low": np.nan, "ci_high": np.nan, "n": n}

    idx = np.random.default_rng(seed).integers(0, n, size=(n_boot, n))
    boots = yv[idx].mean(axis=1) / xv[idx].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"ratio": float(yv.mean() / xv.mean()), "ci_low": float(lo), "ci_high": float(hi), "n": n}
