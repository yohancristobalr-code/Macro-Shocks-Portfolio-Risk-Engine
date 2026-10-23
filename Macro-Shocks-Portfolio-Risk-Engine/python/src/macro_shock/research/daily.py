"""
research/daily.py

Daily routine: today's market inputs from FRED, the engine's regime, alerts
against the previous run, and a dated report plus a CSV log.

Alert thresholds are deliberately few and simple so they are easy to act on:
a regime change, VIX up 5+ points, HY spreads 50+ bps wider than the previous
run, and the S&P 500 10%+ below its 52-week high.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LOG_COLUMNS = ["run_date", "data_through", "regime", "vulnerability", "vix", "hy_bps", "ig_bps",
               "slope_2_10_bps", "y10", "spx_vs_high_pct", "btc"]

VIX_JUMP = 5.0          # points
HY_WIDENING = 50.0      # bps
SPX_CORRECTION = -10.0  # % below the 52-week high


def snapshot_row(run_date: date, data_through: date, regime: str, vulnerability: float,
                 snap, btc: Optional[float]) -> Dict[str, object]:
    """One log row from a MarketStateSnapshot (missing inputs become NaN)."""
    y, c, v, e = snap.yields, snap.credit, snap.volatility, snap.equity
    get = lambda obj, attr: getattr(obj, attr) if obj is not None and getattr(obj, attr) is not None else np.nan
    dd = get(e, "spx_from_52w_high")
    return {
        "run_date": run_date.isoformat(),
        "data_through": data_through.isoformat(),
        "regime": regime,
        "vulnerability": round(float(vulnerability), 3),
        "vix": get(v, "vix_spot"),
        "hy_bps": get(c, "hy_spread_bps"),
        "ig_bps": get(c, "ig_spread_bps"),
        "slope_2_10_bps": get(y, "slope_2_10"),
        "y10": get(y, "y10"),
        "spx_vs_high_pct": dd * 100 if not np.isnan(dd) else np.nan,
        "btc": btc if btc is not None else np.nan,
    }


def read_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.read_csv(path)


def previous_row(log: pd.DataFrame, run_date: date) -> Optional[Dict[str, object]]:
    """Latest logged run before ``run_date`` (earlier runs the same day are ignored)."""
    earlier = log[log["run_date"] < run_date.isoformat()].sort_values("run_date")
    return None if earlier.empty else earlier.iloc[-1].to_dict()


def write_log(log: pd.DataFrame, row: Dict[str, object], path: Path) -> pd.DataFrame:
    """Append ``row``, replacing an earlier run from the same day."""
    log = log[log["run_date"] != row["run_date"]]
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)[LOG_COLUMNS]
    path.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(path, index=False)
    return log


def alerts(today: Dict[str, object], previous: Optional[Dict[str, object]]) -> List[str]:
    out = []
    spx = today.get("spx_vs_high_pct")
    if spx is not None and not pd.isna(spx) and spx <= SPX_CORRECTION:
        out.append(f"S&P 500 is {spx:.1f}% below its 52-week high (correction territory)")
    if previous is None:
        return out
    if today["regime"] != previous.get("regime"):
        out.insert(0, f"REGIME CHANGE: {previous.get('regime')} -> {today['regime']} "
                      f"(previous run {previous.get('run_date')})")
    for key, limit, label, unit in (("vix", VIX_JUMP, "VIX", " pts"), ("hy_bps", HY_WIDENING, "HY spread", " bps")):
        now, before = today.get(key), previous.get(key)
        if None in (now, before) or pd.isna(now) or pd.isna(before):
            continue
        if now - before >= limit:
            out.append(f"{label} up {now - before:+.1f}{unit} since {previous.get('run_date')} "
                       f"({before:.1f} -> {now:.1f})")
    return out


REGIME_CHANGE_CHECKLIST = [
    "Run `sector-study` and read the robust (**) and consistent (*) results for the new regime.",
    "Re-run `optimize --max-drawdown <your limit>`: the regime tilt may change the weights.",
    "Stress-test your portfolio: `analyze ... --data fred --portfolio portfolio.csv`.",
    "Change positions only if the evidence is ** or *, never on a single day's move.",
]


@dataclass
class DailyReport:
    lines: List[str]
    row: Dict[str, object]
    alerts: List[str]


def render(row: Dict[str, object], alert_lines: List[str], previous: Optional[Dict[str, object]],
           components: Dict[str, float], sector_lines: Optional[List[str]]) -> List[str]:
    sep = "─" * 72
    fmt = lambda v, spec: "n/a" if v is None or pd.isna(v) else format(v, spec)
    lines = [sep, f"  DAILY MARKET REPORT — run {row['run_date']}, FRED data through {row['data_through']}", sep,
             f"  Regime: {row['regime']}   (vulnerability {row['vulnerability']:.2f}; 0 calm - 1 stressed)",
             "  Components: " + "  ".join(f"{k} {v:.2f}" for k, v in components.items()),
             "",
             f"  10Y {fmt(row['y10'], '.2f')}%  | 2s10s {fmt(row['slope_2_10_bps'], '+.0f')}bps  "
             f"| HY {fmt(row['hy_bps'], '.0f')}bps  | IG {fmt(row['ig_bps'], '.0f')}bps",
             f"  VIX {fmt(row['vix'], '.1f')}  | S&P 500 vs 52w high {fmt(row['spx_vs_high_pct'], '+.1f')}%  "
             f"| Bitcoin {fmt(row['btc'], ',.0f')}"]
    if previous is not None:
        lines.append(f"  Previous run {previous.get('run_date')}: regime {previous.get('regime')}, "
                     f"VIX {fmt(previous.get('vix'), '.1f')}, HY {fmt(previous.get('hy_bps'), '.0f')}bps")
    lines += ["", "  ALERTS:" if alert_lines else "  ALERTS: none"]
    lines += [f"    ! {a}" for a in alert_lines]
    if any(a.startswith("REGIME CHANGE") for a in alert_lines):
        lines += ["", "  Regime changed - checklist:"] + [f"    {i}. {s}" for i, s in enumerate(REGIME_CHANGE_CHECKLIST, 1)]
    if sector_lines:
        lines += ["", "  What the evidence supports for this regime (sector study, since 1990):"]
        lines += sector_lines
    lines += ["", "  Data, not advice: act only on robust evidence and your own plan.", sep]
    return lines
