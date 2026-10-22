"""
research/allocation.py

Sector / asset-class allocation from data already validated in this project:

- 11 US sectors: Kenneth French daily industry returns ("Other" excluded)
- 10Y and 2Y Treasuries: daily total return approximated from FRED yields as
  carry minus duration x yield change
- Bitcoin (optional): FRED CBBTCUSD

Weights come from a risk-based optimizer (no return forecasts), then an
optional tilt from the sector study's evidence for the current regime.
Gold is not offered (FRED discontinued its gold price series) and cash is left
to the investor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from macro_shock.portfolio_impact import optimizer as opt
from macro_shock.research import french

# FRED Treasury yield series used as bond proxies: (series, label, ETF, duration in years)
BONDS = {
    "UST10": ("DGS10", "10Y Treasuries", "IEF", 8.0),
    "UST2": ("DGS2", "2Y Treasuries", "SHY", 1.9),
}
BTC_SERIES = "CBBTCUSD"
EVIDENCE_TILT = {"robust": 2.0, "consistent": 1.0}   # multiples of the base tilt strength


@dataclass
class Asset:
    code: str
    label: str
    etf: str
    kind: str            # portfolio_stress position type
    sensitivity: float   # beta for equity/bitcoin, duration for bonds


def bond_returns(yields_pct: pd.Series, duration: float, periods_per_year: int = 252) -> pd.Series:
    """Daily total return of a constant-maturity bond: carry minus duration x yield change."""
    y = yields_pct / 100.0
    return (y.shift(1) / periods_per_year - duration * y.diff()).iloc[1:]


def build_universe(
    fred, industries: pd.DataFrame, market: pd.Series, start: date, end: date, include_btc: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, Asset]]:
    """Daily decimal returns on the sector calendar between ``start`` and ``end``, plus asset metadata."""
    sectors = industries.drop(columns=["Other"], errors="ignore")
    days = sectors.index[(sectors.index >= pd.Timestamp(start)) & (sectors.index <= pd.Timestamp(end))]
    returns = sectors.loc[days].copy()
    mkt = market.reindex(days)
    assets = {}
    for code in returns.columns:
        name, etf = french.INDUSTRY_SECTORS[code]
        beta = float(returns[code].cov(mkt) / mkt.var())
        assets[code] = Asset(code, name, etf, "equity", round(beta, 2))

    fetch_from, fetch_to = str(pd.Timestamp(start) - pd.Timedelta(days=10)), str(end)
    for code, (sid, label, etf, duration) in BONDS.items():
        y = fred.get_series(sid, observation_start=fetch_from, observation_end=fetch_to).dropna()
        y = y.reindex(y.index.union(days)).ffill().reindex(days)
        returns[code] = bond_returns(y, duration).reindex(days)
        kind = "bond" if code == "UST10" else "short_bond"
        assets[code] = Asset(code, label, etf, kind, duration)

    if include_btc:
        btc = fred.get_series(BTC_SERIES, observation_start=fetch_from, observation_end=fetch_to).dropna()
        btc = btc.reindex(btc.index.union(days)).ffill().reindex(days)
        returns["BTC"] = btc.pct_change()
        assets["BTC"] = Asset("BTC", "Bitcoin", "-", "bitcoin", 1.0)

    returns = returns.dropna()
    if len(returns) < 252:
        raise ValueError(f"only {len(returns)} days with data for every asset; need at least one year")
    return returns, assets


def regime_tilts(sectors: pd.DataFrame, regime: Optional[str], strength: float) -> Dict[str, float]:
    """Tilt per sector code from robust/consistent evidence for ``regime``: +/- strength (x2 if robust)."""
    if not regime or strength <= 0:
        return {}
    t = sectors[(sectors["regime"] == regime) & sectors["evidence"].isin(list(EVIDENCE_TILT))]
    return {r["asset"]: float(np.sign(r["mean_pct"]) * strength * EVIDENCE_TILT[r["evidence"]])
            for _, r in t.iterrows()}


def style_notes(styles: pd.DataFrame, regime: Optional[str]) -> List[str]:
    """Robust/consistent style results, which sector weights cannot express."""
    if not regime:
        return []
    t = styles[(styles["regime"] == regime) & styles["evidence"].isin(list(EVIDENCE_TILT))]
    return [f"{french.STYLES.get(r['asset'], r['asset'])} {'favoured' if r['mean_pct'] > 0 else 'lagging'} "
            f"({r['evidence']})" for _, r in t.iterrows()]


@dataclass
class Allocation:
    objective: str
    start: date
    end: date
    assets: Dict[str, Asset]
    base: pd.Series
    tilted: pd.Series
    risk_share: pd.Series
    stats: Dict[str, Dict[str, float]]
    regime: Optional[str]
    tilts: Dict[str, float]
    notes: List[str] = field(default_factory=list)


def optimize(
    returns: pd.DataFrame, assets: Dict[str, Asset], objective: str = "risk_parity",
    max_weight: float = 0.30, tilts: Optional[Dict[str, float]] = None,
    regime: Optional[str] = None, notes: Optional[List[str]] = None,
) -> Allocation:
    cov = returns.cov().to_numpy() * 252
    if objective == "min_variance":
        w = opt.min_variance(cov, max_weight)
    elif objective == "risk_parity":
        w = opt.risk_parity(cov)
    else:
        raise ValueError(f"unknown objective {objective!r}")
    base = pd.Series(w, index=returns.columns)
    tilted = opt.apply_tilt(base, tilts or {})
    return Allocation(
        objective=objective, start=returns.index[0].date(), end=returns.index[-1].date(), assets=assets,
        base=base, tilted=tilted,
        risk_share=pd.Series(opt.risk_contributions(tilted.to_numpy(), cov), index=returns.columns),
        stats={"base": opt.portfolio_stats(returns, base), "tilted": opt.portfolio_stats(returns, tilted)},
        regime=regime, tilts=tilts or {}, notes=notes or [],
    )


def to_portfolio_csv(alloc: Allocation, value: float, path: str | Path) -> None:
    """Write the tilted weights in the analyze --portfolio format."""
    rows = []
    for code, w in alloc.tilted.items():
        a = alloc.assets[code]
        name = f"{a.label} ({a.etf})" if a.etf != "-" else a.label
        rows.append({"name": name, "type": a.kind, "value": round(w * value, 2), "sensitivity": a.sensitivity})
    header = (f"# Optimized portfolio ({alloc.objective}, {alloc.start} to {alloc.end}"
              f"{', regime ' + alloc.regime if alloc.regime else ''}) written by msre optimize\n")
    with open(path, "w", newline="") as f:
        f.write(header)
        pd.DataFrame(rows).to_csv(f, index=False)
