"""
portfolio_impact/portfolio_stress.py

Stress-test a user's own portfolio against the scenario tree.

Each position is mapped to one market move the scenario engine already
produces (equity, 10Y / 2Y yields, HY spreads, gold, oil, Bitcoin, USD), scaled
by a sensitivity: beta for price exposures, duration for rate and spread
exposures. The result is the portfolio's gain or loss in every scenario, its
probability-weighted expectation and its 5th percentile.

The scenario magnitudes are illustrative templates conditioned on the event,
so these figures are a sizing aid, not a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from macro_shock.data_schema.models import ScenarioOutcome, ScenarioTree
from macro_shock.scenario_engine.scenario_tree import _percentile_outcome


@dataclass(frozen=True)
class PositionType:
    description: str
    default_sensitivity: float
    # % return of the position per unit of sensitivity in a scenario
    move: Callable[[ScenarioOutcome], float]


POSITION_TYPES: Dict[str, PositionType] = {
    "equity": PositionType("stocks / equity funds; sensitivity = beta to the S&P 500", 1.0,
                           lambda s: s.equity_impact_pct),
    "bond": PositionType("long government bonds; sensitivity = duration (years) on the 10Y yield", 7.0,
                         lambda s: -s.yield_10y_change_bps / 100),
    "short_bond": PositionType("short government bonds; sensitivity = duration (years) on the 2Y yield", 2.0,
                               lambda s: -s.yield_2y_change_bps / 100),
    "high_yield": PositionType("high-yield credit; sensitivity = spread duration (years)", 4.0,
                               lambda s: -s.credit_hy_change_bps / 100),
    "gold": PositionType("gold; sensitivity = beta to gold", 1.0, lambda s: s.gold_change_pct),
    "oil": PositionType("oil / energy commodities; sensitivity = beta to oil", 1.0, lambda s: s.oil_change_pct),
    "bitcoin": PositionType("Bitcoin and other crypto; sensitivity = beta to Bitcoin", 1.0,
                            lambda s: s.btc_change_pct),
    "usd": PositionType("long US dollar index exposure; sensitivity = beta to DXY", 1.0,
                        lambda s: s.dxy_change_pct),
    "cash": PositionType("cash; no market exposure", 0.0, lambda s: 0.0),
}


class PortfolioFileError(ValueError):
    """The portfolio file is missing, malformed or has an invalid row."""


def load_portfolio(path: str | Path) -> pd.DataFrame:
    """
    Read a portfolio CSV with columns ``name,type,value[,sensitivity]``.

    ``value`` is the position's market value in any single currency (negative
    for shorts). An empty ``sensitivity`` uses the type's default.
    """
    path = Path(path)
    if not path.exists():
        raise PortfolioFileError(f"Portfolio file not found: {path}")
    try:
        df = pd.read_csv(path, comment="#", skipinitialspace=True)
    except Exception as e:
        raise PortfolioFileError(f"Could not read {path}: {e}")

    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"name", "type", "value"} - set(df.columns)
    if missing:
        raise PortfolioFileError(f"{path} is missing column(s): {', '.join(sorted(missing))}")
    if "sensitivity" not in df.columns:
        df["sensitivity"] = np.nan
    if df.empty:
        raise PortfolioFileError(f"{path} has no positions")

    df["type"] = df["type"].astype(str).str.strip().str.lower()
    unknown = sorted(set(df["type"]) - set(POSITION_TYPES))
    if unknown:
        raise PortfolioFileError(
            f"Unknown position type(s): {', '.join(unknown)}. "
            f"Valid types: {', '.join(POSITION_TYPES)}"
        )
    for col in ("value", "sensitivity"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df["value"].isna().any():
        bad = df.loc[df["value"].isna(), "name"].tolist()
        raise PortfolioFileError(f"Missing or non-numeric value for: {', '.join(map(str, bad))}")

    defaults = df["type"].map(lambda t: POSITION_TYPES[t].default_sensitivity)
    df["sensitivity"] = df["sensitivity"].fillna(defaults)
    return df[["name", "type", "value", "sensitivity"]].reset_index(drop=True)


@dataclass
class PortfolioStress:
    total_value: float
    scenario_pnl: pd.DataFrame      # one row per scenario: name, probability, pnl, pnl_pct
    position_pnl: pd.DataFrame      # positions x scenarios, P&L in currency
    expected_pnl: float
    pnl_5pct: float                 # 5th percentile of the scenario P&L distribution
    worst_scenario: str

    @property
    def expected_pnl_pct(self) -> float:
        return self.expected_pnl / self.total_value * 100 if self.total_value else np.nan

    @property
    def pnl_5pct_pct(self) -> float:
        return self.pnl_5pct / self.total_value * 100 if self.total_value else np.nan

    def worst_contributors(self, n: int = 3) -> pd.Series:
        """Positions with the largest loss in the worst scenario."""
        return self.position_pnl[self.worst_scenario].sort_values().head(n)


def stress_portfolio(portfolio: pd.DataFrame, tree: ScenarioTree) -> PortfolioStress:
    names = [s.name for s in tree.scenarios]
    probs = np.array([s.probability for s in tree.scenarios])

    position_pnl = pd.DataFrame(index=portfolio["name"], columns=names, dtype=float)
    for _, pos in portfolio.iterrows():
        ptype = POSITION_TYPES[pos["type"]]
        position_pnl.loc[pos["name"]] = [
            pos["value"] * pos["sensitivity"] * ptype.move(s) / 100 for s in tree.scenarios
        ]

    totals = position_pnl.sum(axis=0).to_numpy(dtype=float)
    total_value = float(portfolio["value"].sum())

    scenario_pnl = pd.DataFrame({
        "scenario": names,
        "probability": probs,
        "pnl": totals,
        "pnl_pct": totals / total_value * 100 if total_value else np.nan,
    })
    return PortfolioStress(
        total_value=total_value,
        scenario_pnl=scenario_pnl,
        position_pnl=position_pnl,
        expected_pnl=float(probs @ totals),
        pnl_5pct=_percentile_outcome(totals, probs, 0.05),
        worst_scenario=names[int(np.argmin(totals))],
    )


def describe_types() -> List[str]:
    return [f"{name:<11} {t.description} (default {t.default_sensitivity:g})"
            for name, t in POSITION_TYPES.items()]
