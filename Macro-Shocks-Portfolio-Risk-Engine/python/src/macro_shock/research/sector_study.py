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
    sectors: pd.DataFrame           # regime_performance() rows, sectors in excess of the market
    styles: pd.DataFrame            # regime_performance() rows, long-short styles
    regime_counts: pd.Series
    first_date: date
    last_date: date
    horizon: int
    proxy_check: Dict[str, float]
    current_regime: Optional[str]          # latest date, real ICE credit spreads
    current_regime_proxy: Optional[str]    # latest date, BAA10Y proxy
    current_date: Optional[date]


def sector_label(code: str) -> str:
    name, etf = french.INDUSTRY_SECTORS.get(code, (code, "-"))
    return f"{name} ({etf})" if etf != "-" else name


def run(fred, start: date, horizon: int = 21, end: Optional[date] = None) -> SectorStudy:
    industries = french.daily_returns(french.INDUSTRIES_12)
    factors = french.daily_returns(french.FACTORS_3)
    momentum = french.daily_returns(french.MOMENTUM)

    idx = industries.index[industries.index >= pd.Timestamp(start)]
    if end is not None:
        idx = idx[idx <= pd.Timestamp(end)]
    market = french.market_return(factors)
    idx = idx.intersection(market.index).intersection(momentum.index)

    series = rh.fetch_series(fred, start, date.today())
    regimes = rh.regime_history(series, sr.window_starts(idx, horizon), "proxy")

    sectors = sr.regime_performance(industries.loc[idx], regimes, market, horizon)
    style_daily = pd.concat([factors.loc[idx, ["SMB", "HML"]], momentum.loc[idx, ["Mom"]]], axis=1)
    styles = sr.regime_performance(style_daily, regimes, None, horizon)

    latest = max(s.index[-1] for k, s in series.items() if k != "baa")
    now_real = rh.regime_history(series, [latest], "ice").iloc[0]
    now_proxy = rh.regime_history(series, [latest], "proxy").iloc[0]

    counts = sectors.groupby("regime")["n"].first().sort_values(ascending=False)
    return SectorStudy(
        sectors=sectors, styles=styles, regime_counts=counts,
        first_date=idx[0].date(), last_date=idx[-1].date(), horizon=horizon,
        proxy_check=rh.credit_proxy_agreement(series),
        current_regime=now_real, current_regime_proxy=now_proxy, current_date=latest.date(),
    )


def _fmt_row(label: str, r) -> str:
    if np.isnan(r["ci_low"]):
        interval, star = "  (too few windows)", ""
    else:
        interval = f"  [{r['ci_low']:+.2f} to {r['ci_high']:+.2f}]"
        star = " *" if (r["ci_low"] > 0 or r["ci_high"] < 0) else ""
    return f"    {label:<28} {r['mean_pct']:>+6.2f}%   {r['hit_rate']:>4.0%}{interval}{star}"


def regime_lines(study: SectorStudy, regime: str, top: int = 3) -> list:
    """Printable best/worst sectors and the styles for one regime."""
    if regime not in set(study.sectors["regime"]):
        return [f"  No historical windows for regime '{regime}'. With FRED inputs the engine "
                f"cannot identify it (crisis needs liquidity data), so there is nothing to compare."]
    n = int(study.regime_counts.get(regime, 0))
    lines = [f"  {regime}  (n={n} windows of {study.horizon} trading days)",
             f"    {'Sector vs market, next window':<28} {'mean':>7}   {'beat':>4}  95% interval"]
    picks = sr.suggestions(study.sectors, regime, top)
    for _, r in picks["best"].iterrows():
        lines.append(_fmt_row("+ " + sector_label(r["asset"]), r))
    for _, r in picks["worst"].iterrows():
        lines.append(_fmt_row("- " + sector_label(r["asset"]), r))
    lines.append(f"    {'Style (long-short)':<28}")
    for _, r in study.styles[study.styles["regime"] == regime].iterrows():
        lines.append(_fmt_row(french.STYLES.get(r["asset"], r["asset"]), r))
    return lines


FOOTNOTE = ("  * = 95% interval excludes zero. Historical association, not a forecast; "
            "'beat' = share of windows with a positive result.")
