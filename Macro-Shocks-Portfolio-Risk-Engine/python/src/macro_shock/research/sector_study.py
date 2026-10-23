"""
research/sector_study.py

End-to-end sector/style-by-regime study: French Data Library returns, FRED
regime inputs, the engine's regime classifier, and printable suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional

import numpy as np
import pandas as pd

from macro_shock.research import french
from macro_shock.research import regime_history as rh
from macro_shock.research import sector_regimes as sr


@dataclass
class SectorStudy:
    sectors: pd.DataFrame           # split_robustness() rows, sectors in excess of the market
    styles: pd.DataFrame            # split_robustness() rows, long-short styles
    regime_counts: pd.Series
    first_date: date
    last_date: date
    horizon: int
    proxy_check: Dict[str, float]
    split_date: date                       # halves for the robustness grade
    current_regime: Optional[str]          # latest date, real ICE credit spreads
    current_regime_proxy: Optional[str]    # latest date, BAA10Y proxy
    current_date: Optional[date]


def sector_label(code: str) -> str:
    name, etf = french.INDUSTRY_SECTORS.get(code, (code, "-"))
    return f"{name} ({etf})" if etf != "-" else name


def run(fred, start: date, horizon: int = 21, end: Optional[date] = None) -> SectorStudy:
    # "Other" is French's catch-all industry: no ETF tracks it, so it cannot be acted on
    industries = french.daily_returns(french.INDUSTRIES_12).drop(columns=["Other"], errors="ignore")
    factors = french.daily_returns(french.FACTORS_3)
    momentum = french.daily_returns(french.MOMENTUM)

    idx = industries.index[industries.index >= pd.Timestamp(start)]
    if end is not None:
        idx = idx[idx <= pd.Timestamp(end)]
    market = french.market_return(factors)
    idx = idx.intersection(market.index).intersection(momentum.index)

    series = rh.fetch_series(fred, start, date.today())
    regimes = rh.regime_history(series, sr.window_starts(idx, horizon), "proxy")
    split = idx[len(idx) // 2]

    fwd, labels = sr.window_returns(industries.loc[idx], regimes, market, horizon)
    sectors = sr.split_robustness(fwd, labels, split)
    style_daily = pd.concat([factors.loc[idx, ["SMB", "HML"]], momentum.loc[idx, ["Mom"]]], axis=1)
    fwd, labels = sr.window_returns(style_daily, regimes, None, horizon)
    styles = sr.split_robustness(fwd, labels, split)

    latest = max(series[k].index[-1] for k in ("vix", "y2", "y10", "hy", "ig") if len(series[k]))
    now_real = rh.regime_history(series, [latest], "ice").iloc[0]
    now_proxy = rh.regime_history(series, [latest], "proxy").iloc[0]

    counts = sectors.groupby("regime")["n"].first().sort_values(ascending=False)
    return SectorStudy(
        sectors=sectors, styles=styles, regime_counts=counts,
        first_date=idx[0].date(), last_date=idx[-1].date(), horizon=horizon,
        proxy_check=rh.credit_proxy_agreement(series),
        split_date=split.date(),
        current_regime=now_real, current_regime_proxy=now_proxy, current_date=latest.date(),
    )


MARKERS = {"robust": "**", "consistent": "*", "unstable": "!", "": ""}


def _halves(r) -> str:
    if np.isnan(r.get("mean_pct_h1", np.nan)) or np.isnan(r.get("mean_pct_h2", np.nan)):
        return ""
    return f"  halves {r['mean_pct_h1']:+.2f} / {r['mean_pct_h2']:+.2f}"


def _fmt_row(label: str, r) -> str:
    if np.isnan(r["ci_low"]):
        interval = "  (too few windows)"
    else:
        interval = f"  [{r['ci_low']:+.2f} to {r['ci_high']:+.2f}]"
    mark = MARKERS.get(r.get("evidence", ""), "")
    return (f"    {mark:<2} {label:<28} {r['mean_pct']:>+6.2f}%   {r['hit_rate']:>4.0%}"
            f"{interval}{_halves(r)}")


def _no_history(regime: str) -> list:
    return [f"  No historical windows for regime '{regime}'. With FRED inputs the engine "
            f"cannot identify it (crisis needs liquidity data), so there is nothing to compare."]


def regime_lines(study: SectorStudy, regime: str, top: int = 3) -> list:
    """Printable best/worst sectors and the styles for one regime, with evidence markers."""
    if regime not in set(study.sectors["regime"]):
        return _no_history(regime)
    n = int(study.regime_counts.get(regime, 0))
    lines = [f"  {regime}  (n={n} windows of {study.horizon} trading days)",
             f"       {'Sector vs market, next window':<28} {'mean':>7}   {'beat':>4}  95% interval"]
    picks = sr.suggestions(study.sectors, regime, top)
    for _, r in picks["best"].iterrows():
        lines.append(_fmt_row("+ " + sector_label(r["asset"]), r))
    for _, r in picks["worst"].iterrows():
        lines.append(_fmt_row("- " + sector_label(r["asset"]), r))
    lines.append(f"       {'Style (long-short)':<28}")
    for _, r in study.styles[study.styles["regime"] == regime].iterrows():
        lines.append(_fmt_row(french.STYLES.get(r["asset"], r["asset"]), r))
    return lines


def supported_lines(study: SectorStudy, regime: str) -> list:
    """Only the robust and consistent results for ``regime``: what the evidence supports."""
    if regime not in set(study.sectors["regime"]):
        return _no_history(regime)
    picked = []
    for table, label in ((study.sectors, sector_label),
                         (study.styles, lambda a: french.STYLES.get(a, a))):
        t = table[(table["regime"] == regime) & table["evidence"].isin(["robust", "consistent"])]
        picked += [(r["mean_pct"], label(r["asset"]), r) for _, r in t.iterrows()]
    favoured = sorted((p for p in picked if p[0] > 0), key=lambda p: -p[0])
    lagging = sorted((p for p in picked if p[0] <= 0), key=lambda p: p[0])
    rows = [_fmt_row(f"favoured {name}", r) for _, name, r in favoured]
    rows += [_fmt_row(f"lagging  {name}", r) for _, name, r in lagging]
    n = int(study.regime_counts.get(regime, 0))
    head = f"  {regime}  (n={n} windows; halves split at {study.split_date})"
    if not rows:
        return [head, "    No sector or style held up in both halves of the history for this regime:",
                "    the evidence supports no tilt."]
    return [head] + rows


FOOTNOTE = ("  ** robust: clear in the full sample and in each half  |  * consistent: clear overall, "
            "same sign in both halves\n"
            "  ! unstable: clear overall but the halves disagree - ignore  |  halves = mean in "
            "each half of the period\n"
            "  Historical association, not a forecast; 'beat' = share of windows with a positive result.")
