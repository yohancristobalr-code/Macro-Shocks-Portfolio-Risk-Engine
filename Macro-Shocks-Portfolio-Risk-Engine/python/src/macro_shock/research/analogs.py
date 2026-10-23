"""
research/analogs.py

Historical analogs and a predictive check, on a monthly panel since 1990.

State of the market at each month-end, from what was public then:
VIX, 2s10s curve, Moody's Baa spread (BAA10Y), 12-month change of the 10Y
yield, 12-month change of WTI, and the growth and inflation axes of
research/growth.py (with publication lags). Each feature is standardized
over the whole history.

Analogs: the months closest to today's state (Euclidean distance on the
standardized features), at least a year apart and not in the last two years,
with what the market, the 10Y yield, oil and sectors did over the following
months, against all months as a baseline.

Predictive model: ridge regression of the next 3 months' US market excess
return (French Data Library) on the same features, refitted every month from
2005 on data available up to then. Out-of-sample R2 compares its errors with
those of the historical average (Campbell-Thompson): R2 <= 0 means the model
does not beat the average and its forecast should be ignored. Targets
overlap (monthly data, 3-month horizon), so the hit rate overstates
precision; the R2 is the honest test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from macro_shock.research import french, growth

MARKET_SERIES = {"vix": "VIXCLS", "y2": "DGS2", "y10": "DGS10", "baa": "BAA10Y", "oil": "DCOILWTICO"}
FEATURES = {"vix": "VIX", "curve": "Curva 2s10s (pb)", "baa": "Spread Baa (%)", "y10_12m": "Δ 10 años 12m (pb)",
            "oil_12m": "Δ WTI 12m (%)", "growth": "Eje crecimiento", "inflation": "Eje inflación"}
START = date(1990, 1, 1)
EXCLUDE_RECENT = 24          # months
MIN_GAP = 12                 # months between analogs
N_ANALOGS = 5
HORIZONS = (3, 6, 12)
MODEL_HORIZON = 3
OOS_FROM = pd.Period("2005-01", "M")
RIDGE = 5.0


def fetch(fred, end: date) -> Dict[str, pd.Series]:
    out = {}
    for key, sid in MARKET_SERIES.items():
        s = pd.Series(fred.get_series(sid, observation_start="1985-01-01", observation_end=str(end)),
                      dtype=float).dropna().sort_index()
        s.index = pd.DatetimeIndex(s.index)
        out[key] = s
    return out


def _month_end(s: pd.Series) -> pd.Series:
    return s.groupby(s.index.to_period("M")).last()


def _period_end(p: pd.Period) -> pd.Timestamp:
    return p.to_timestamp(how="end").normalize()


def panel(market: Dict[str, pd.Series], sig: growth.Signals) -> pd.DataFrame:
    """Monthly features (raw units), indexed by month."""
    m = {k: _month_end(s) for k, s in market.items()}
    df = pd.DataFrame({
        "vix": m["vix"],
        "curve": (m["y10"] - m["y2"]) * 100,
        "baa": m["baa"],
        "y10_12m": (m["y10"] - m["y10"].shift(12)) * 100,
        "oil_12m": (m["oil"] / m["oil"].shift(12) - 1) * 100,
    })
    df = df[df.index >= pd.Period(START, "M")]
    axes = [growth.axes_at(sig, _period_end(p), lagged=True) for p in df.index]
    df["growth"] = [a.growth for a in axes]
    df["inflation"] = [a.inflation for a in axes]
    return df.astype(float)


def today_state(market: Dict[str, pd.Series], axes: growth.Axes) -> pd.Series:
    def at_lag(s: pd.Series, months: int) -> Optional[float]:
        past = s[s.index <= s.index[-1] - pd.DateOffset(months=months)]
        return float(past.iloc[-1]) if len(past) else None
    y10, oil = market["y10"], market["oil"]
    y10_ago, oil_ago = at_lag(y10, 12), at_lag(oil, 12)
    return pd.Series({
        "vix": float(market["vix"].iloc[-1]),
        "curve": float((market["y10"].iloc[-1] - market["y2"].iloc[-1]) * 100),
        "baa": float(market["baa"].iloc[-1]),
        "y10_12m": (float(y10.iloc[-1]) - y10_ago) * 100 if y10_ago is not None else np.nan,
        "oil_12m": (float(oil.iloc[-1]) / oil_ago - 1) * 100 if oil_ago else np.nan,
        "growth": axes.growth if axes.growth is not None else np.nan,
        "inflation": axes.inflation if axes.inflation is not None else np.nan,
    })


# ── Outcomes ─────────────────────────────────────────────────────────────────

def monthly_returns(daily: pd.DataFrame) -> pd.DataFrame:
    return (1 + daily).groupby(daily.index.to_period("M")).prod() - 1


def forward(monthly: pd.DataFrame, months: int) -> pd.DataFrame:
    """Compounded return (%) over the ``months`` after each month."""
    growth_ = (1 + monthly).cumprod()
    return (growth_.shift(-months) / growth_ - 1) * 100


def forward_drawdown(daily_market: pd.Series, months: int = 6) -> pd.Series:
    """Worst peak-to-trough fall (%) within the ``months`` after each month-end."""
    level = (1 + daily_market).cumprod()
    out = {}
    for p in level.index.to_period("M").unique():
        end = _period_end(p)
        window = level[(level.index > end) & (level.index <= end + pd.DateOffset(months=months))]
        if len(window) < 15 * months:
            continue
        start = level[level.index <= end]
        path = pd.concat([start.iloc[-1:], window])
        out[p] = float((path / path.cummax() - 1).min() * 100)
    return pd.Series(out)


@dataclass
class Analogs:
    table: pd.DataFrame          # one row per analog month: distance, features, outcomes
    baseline: pd.Series          # same outcomes averaged over all months
    today: pd.Series
    sectors_best: List[tuple]    # (sector, mean excess %) over the analogs, next 6 months
    sectors_worst: List[tuple]


def standardize(df: pd.DataFrame, today: pd.Series):
    mu, sd = df.mean(), df.std().replace(0, np.nan)
    return (df - mu) / sd, (today - mu) / sd


def find(df: pd.DataFrame, today: pd.Series, n: int = N_ANALOGS):
    """(analog months, distance of every candidate month)."""
    z, zt = standardize(df, today)
    cols = [c for c in z.columns if pd.notna(zt[c])]
    cand = z[cols].dropna()
    cand = cand[cand.index <= df.index[-1] - EXCLUDE_RECENT]
    dist = np.sqrt(((cand - zt[cols]) ** 2).sum(axis=1)).sort_values()
    picked: List[pd.Period] = []
    for p in dist.index:
        if all(abs((p - q).n) >= MIN_GAP for q in picked):
            picked.append(p)
        if len(picked) == n:
            break
    return picked, dist


def analogs(df: pd.DataFrame, today: pd.Series, market: Dict[str, pd.Series], n: int = N_ANALOGS) -> Analogs:
    factors = french.daily_returns(french.FACTORS_3)
    industries = french.daily_returns(french.INDUSTRIES_12).drop(columns=["Other"], errors="ignore")
    mkt_daily = french.market_return(factors)
    mkt_m = monthly_returns(mkt_daily.to_frame("mkt"))
    outcomes = pd.DataFrame({f"mkt_{h}m": forward(mkt_m, h)["mkt"] for h in HORIZONS})
    outcomes["dd_6m"] = forward_drawdown(mkt_daily, 6)
    y10, oil = _month_end(market["y10"]), _month_end(market["oil"])
    outcomes["y10_6m"] = (y10.shift(-6) - y10) * 100
    outcomes["oil_6m"] = (oil.shift(-6) / oil - 1) * 100
    ind6 = forward(monthly_returns(industries), 6).sub(forward(mkt_m, 6)["mkt"], axis=0)

    picked, dist = find(df, today, n)
    rows = []
    for p in picked:
        row = {"month": str(p), "distance": float(dist[p])}
        row.update({k: df.at[p, k] for k in df.columns})
        row.update({k: outcomes.at[p, k] if p in outcomes.index else np.nan for k in outcomes.columns})
        rows.append(row)
    table = pd.DataFrame(rows)
    base = outcomes[outcomes.index.isin(df.index)].mean()
    sect = ind6.reindex(picked).mean().dropna().sort_values(ascending=False)
    from macro_shock.research.sector_study import sector_label
    best = [(sector_label(k), float(v)) for k, v in sect.head(3).items()]
    worst = [(sector_label(k), float(v)) for k, v in sect.tail(3).iloc[::-1].items()]
    return Analogs(table, base, today, best, worst)


# ── Predictive model ─────────────────────────────────────────────────────────

def _ridge(X: np.ndarray, y: np.ndarray, lam: float = RIDGE) -> np.ndarray:
    Xc = np.column_stack([np.ones(len(X)), X])
    pen = np.eye(Xc.shape[1]) * lam
    pen[0, 0] = 0.0
    return np.linalg.solve(Xc.T @ Xc + pen, Xc.T @ y)


@dataclass
class Model:
    oos_r2: float
    hit_rate: float
    n_oos: int
    forecast: float              # next 3 months' market excess return, %
    error_band: float            # typical out-of-sample error (MAE), %
    baseline: float              # historical average 3-month excess return, %
    coefficients: Dict[str, float]

    @property
    def useful(self) -> bool:
        return self.oos_r2 > 0


def model(df: pd.DataFrame, today: pd.Series, horizon: int = MODEL_HORIZON) -> Model:
    factors = french.daily_returns(french.FACTORS_3)
    excess = monthly_returns((factors["Mkt-RF"]).to_frame("x"))
    target = forward(excess, horizon)["x"]
    cols = [c for c in df.columns if pd.notna(today[c])]
    data = df[cols].join(target.rename("y"), how="inner").dropna()
    preds, actual, means = [], [], []
    for p in data.index[data.index >= OOS_FROM]:
        train = data[data.index <= p - horizon]           # targets fully known at p
        if len(train) < 120:
            continue
        mu, sd = train[cols].mean(), train[cols].std().replace(0, np.nan)
        beta = _ridge(((train[cols] - mu) / sd).to_numpy(), train["y"].to_numpy())
        x = ((data.loc[p, cols] - mu) / sd).to_numpy()
        preds.append(float(beta[0] + x @ beta[1:]))
        actual.append(float(data.at[p, "y"]))
        means.append(float(train["y"].mean()))
    preds, actual, means = map(np.array, (preds, actual, means))
    oos = 1 - ((actual - preds) ** 2).sum() / ((actual - means) ** 2).sum() if len(preds) else np.nan
    hit = float((np.sign(preds) == np.sign(actual)).mean()) if len(preds) else np.nan
    mu, sd = data[cols].mean(), data[cols].std().replace(0, np.nan)
    beta = _ridge(((data[cols] - mu) / sd).to_numpy(), data["y"].to_numpy())
    fc = float(beta[0] + ((today[cols] - mu) / sd).to_numpy() @ beta[1:])
    return Model(float(oos), hit, len(preds), fc, float(np.abs(actual - preds).mean()) if len(preds) else np.nan,
                 float(data["y"].mean()), dict(zip(cols, beta[1:].tolist())))
