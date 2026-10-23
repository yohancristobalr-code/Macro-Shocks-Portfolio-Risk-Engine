"""
research/macro.py

The `macro` report: the scenario markets are pricing, leading growth
indicators, the CPI nowcast, the growth x inflation quadrant from published
data, and which sectors and styles did best in each quadrant since 1972.

Markets look ahead and data look back, so the report puts both side by side:
when they agree the reading is stronger; when they disagree, either the data
are about to turn or the market is overreacting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

import pandas as pd
import structlog

from macro_shock.research import cpi_nowcast, growth, macro_scenario as ms
from macro_shock.research import sector_regimes as sr
from macro_shock.research import sector_study

logger = structlog.get_logger(__name__)

QUADRANT_START = date(1972, 1, 1)       # claims since 1967, plus time for the signals' scale


@dataclass
class MacroReport:
    today: date
    market: Optional[ms.MarketScenario]
    market_error: Optional[str]
    axes: growth.Axes
    leading: growth.Leading
    nowcast: Optional[cpi_nowcast.CpiNowcast]
    nowcast_axis: Optional[float]
    study: Optional[sector_study.SectorStudy]
    study_error: Optional[str] = None


def quadrant_study(sig: growth.Signals, current: Optional[str], start: date = QUADRANT_START,
                   horizon: int = 21, end: Optional[date] = None) -> sector_study.SectorStudy:
    """Sectors and styles over the window after each quadrant, labelled with what was public then."""
    u = sector_study.universe(start, end)
    labels = growth.quadrant_history(sig, sr.window_starts(u.idx, horizon)).dropna()
    sectors, styles, split = sector_study.label_tables(u, labels, horizon)
    counts = sectors.groupby("regime")["n"].first().sort_values(ascending=False)
    return sector_study.SectorStudy(
        sectors=sectors, styles=styles, regime_counts=counts,
        first_date=u.idx[0].date(), last_date=u.idx[-1].date(), horizon=horizon,
        proxy_check={"n": 0}, split_date=split.date(),
        current_regime=current, current_regime_proxy=current, current_date=None,
    )


def nowcast_axis(series, sig: growth.Signals, nc: Optional[cpi_nowcast.CpiNowcast]):
    """Inflation axis if the first unpublished CPI comes in at the nowcast (core at its trend)."""
    if nc is None or not nc.months:
        return None, None
    month = nc.months[0]
    head, core = nc.value(month, "headline"), nc.value(month, "core")
    if head is None or core is None:
        return None, None
    return growth.inflation_with_nowcast(series, sig, month, head, core), month


def run(fred, today: Optional[date] = None, window: int = ms.WINDOW, sectors: bool = True,
        study_start: date = QUADRANT_START) -> MacroReport:
    today = today or date.today()
    market, market_error = None, None
    try:
        market = ms.assess(ms.fetch(fred, today), window)
    except Exception as e:
        market_error = str(e)
    series = growth.fetch(fred, growth.FULL_HISTORY_START, today)
    sig = growth.signals(series)
    axes = growth.axes_at(sig, today, lagged=False)
    nc = None
    try:
        nc = cpi_nowcast.run(fred, today)
    except Exception as e:
        logger.warning("cpi_nowcast_unavailable", error=str(e))
    axis, _ = nowcast_axis(series, sig, nc)
    study, study_error = None, None
    if sectors:
        try:
            study = quadrant_study(sig, axes.quadrant, study_start)
        except Exception as e:
            study_error = str(e)
    return MacroReport(today, market, market_error, axes, growth.leading(series), nc, axis, study, study_error)


def agreement_line(market: Optional[ms.MarketScenario], quadrant: Optional[str]) -> str:
    if market is None or market.best is None or quadrant is None:
        return "  Markets vs data: not enough data to compare."
    best, status = market.best, market.status(market.best)
    if status not in ("CONFIRMED", "partial"):
        return f"  Markets vs data: markets price no clear scenario; data say {quadrant}."
    if best == quadrant:
        return f"  Markets vs data: AGREE on {quadrant}. Both point the same way: the stronger reading."
    return (f"  Markets vs data: DISAGREE. Markets price {best} ({status}); published data say {quadrant}. "
            "Markets look ahead: either the data are about to turn, or the market is overreacting.")


def summary_line(market: Optional[ms.MarketScenario], quadrant: Optional[str] = None) -> Optional[str]:
    """One line for the daily report."""
    if market is None and quadrant is None:
        return None
    parts = [f"markets price {ms.summary(market)}" if market is not None else "markets n/a"]
    if quadrant is not None:
        parts.append(f"data quadrant {quadrant}")
    return "  Macro: " + "  | ".join(parts) + "  (details: `macro`)"


def report_lines(r: MacroReport) -> List[str]:
    sep = "─" * 72
    lines = [sep, "  MACRO SCENARIO: MARKETS, GROWTH AND INFLATION (FRED)", sep, "", "  1. MARKETS"]
    if r.market is not None:
        lines += ms.report_lines(r.market)
    else:
        lines.append(f"  Market data unavailable ({r.market_error}).")

    lines += ["", "  2. GROWTH: leading indicators"] + r.leading.lines + [""] + growth.pmi_hint(r.leading)

    lines += ["", "  3. INFLATION"]
    line = cpi_nowcast.summary_line(r.nowcast) if r.nowcast is not None else None
    lines.append(line or "  CPI nowcast unavailable today.")

    month = r.nowcast.months[0] if r.nowcast is not None and r.nowcast.months else None
    lines += ["", "  4. MACRO QUADRANT (published data)"] + growth.axes_lines(r.axes, r.nowcast_axis, month)
    q = r.axes.quadrant
    lines.append(f"  Quadrant: {growth.QUADRANT_NAMES.get(q, 'n/a')}")
    lines.append(agreement_line(r.market, q))

    if r.study is not None:
        s = r.study
        lines += ["", f"  5. SECTORS AND STYLES BY QUADRANT ({s.first_date.year}-{s.last_date.year}, "
                      f"next {s.horizon} trading days, French Data Library)",
                  "  Windows: " + "  ".join(f"{k} {int(n)}" for k, n in s.regime_counts.items())]
        order = ([q] if q in s.regime_counts.index else []) + [k for k in s.regime_counts.index if k != q]
        for quadrant in order:
            lines.append("")
            head = sector_study.supported_lines(s, quadrant)
            if quadrant == q:
                head[0] = head[0] + "   <- current"
            lines += head
        lines.append(sector_study.FOOTNOTE)
    elif r.study_error:
        lines += ["", f"  5. Sector study unavailable ({r.study_error})."]
    lines += ["", "  Data, not advice: markets price expectations, the quadrant describes the recent past.", sep]
    return lines
