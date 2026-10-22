"""
portfolio_impact/optimizer.py

Long-only, fully invested portfolio construction from a covariance matrix.

Only risk-based objectives are offered: minimum variance and equal risk
contribution (risk parity). Both need no expected-return forecasts, which is
deliberate: historical mean returns are too noisy to optimize on, and
mean-variance optimizers built on them are notorious for extreme, unstable
weights.

Plain numpy, no solver dependency:
- minimum variance: projected gradient descent onto the capped simplex
- risk parity: cyclical coordinate descent (Griveau-Billion, Richard and
  Roncalli, 2013), which converges to the unique long-only solution
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd


def project_capped_simplex(v: np.ndarray, cap: float = 1.0) -> np.ndarray:
    """Euclidean projection of ``v`` onto {w : 0 <= w <= cap, sum(w) = 1}."""
    n = len(v)
    if cap * n < 1 - 1e-12:
        raise ValueError(f"max weight {cap:.0%} is infeasible for {n} assets (needs >= {1 / n:.0%})")
    lo, hi = v.min() - 1.0, v.max()
    for _ in range(200):                        # bisection on the shift tau
        tau = (lo + hi) / 2
        total = np.clip(v - tau, 0.0, cap).sum()
        if total > 1:
            lo = tau
        else:
            hi = tau
    return np.clip(v - (lo + hi) / 2, 0.0, cap)


def min_variance(cov: np.ndarray, max_weight: float = 1.0, iters: int = 20000, tol: float = 1e-12) -> np.ndarray:
    """Long-only minimum-variance weights with an optional per-asset cap."""
    n = cov.shape[0]
    step = 1.0 / np.linalg.eigvalsh(cov).max()
    w = project_capped_simplex(np.full(n, 1.0 / n), max_weight)
    for _ in range(iters):
        new = project_capped_simplex(w - step * (cov @ w), max_weight)
        if np.abs(new - w).max() < tol:
            return new
        w = new
    return w


def risk_parity(cov: np.ndarray, budgets: Optional[np.ndarray] = None,
                iters: int = 10000, tol: float = 1e-12) -> np.ndarray:
    """Long-only weights whose risk contributions w_i (cov w)_i are proportional to ``budgets``."""
    n = cov.shape[0]
    b = np.full(n, 1.0 / n) if budgets is None else np.asarray(budgets, float) / np.sum(budgets)
    w = 1.0 / np.sqrt(np.diag(cov))
    w /= w.sum()
    for _ in range(iters):
        prev = w.copy()
        for i in range(n):
            others = cov[i] @ w - cov[i, i] * w[i]
            w[i] = (-others + np.sqrt(others ** 2 + 4 * cov[i, i] * b[i])) / (2 * cov[i, i])
        if np.abs(w / w.sum() - prev / prev.sum()).max() < tol:
            break
    return w / w.sum()


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Share of portfolio variance coming from each asset (sums to 1)."""
    rc = w * (cov @ w)
    return rc / rc.sum()


def apply_tilt(weights: pd.Series, tilts: Dict[str, float]) -> pd.Series:
    """
    Scale weights by (1 + tilt) per asset (e.g. +0.25 overweight, -0.25
    underweight) and renormalize. Assets not in ``tilts`` are unchanged before
    renormalization.
    """
    factors = pd.Series({a: 1.0 + tilts.get(a, 0.0) for a in weights.index})
    if (factors < 0).any():
        raise ValueError("a tilt below -100% would make a weight negative")
    tilted = weights * factors
    return tilted / tilted.sum()


def portfolio_stats(daily_returns: pd.DataFrame, weights: pd.Series,
                    periods_per_year: int = 252) -> Dict[str, float]:
    """Annualized volatility and return, and maximum drawdown, of a daily-rebalanced portfolio."""
    r = daily_returns[weights.index].fillna(0.0) @ weights
    growth = (1 + r).cumprod()
    years = len(r) / periods_per_year
    return {
        "volatility": float(r.std() * np.sqrt(periods_per_year)),
        "annual_return": float(growth.iloc[-1] ** (1 / years) - 1) if years > 0 else np.nan,
        "max_drawdown": float((growth / growth.cummax() - 1).min()),
    }
