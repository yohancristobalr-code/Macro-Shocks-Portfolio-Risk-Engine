"""
research/crypto.py

Crypto axis: ten coins, their leverage, sentiment and liquidity, a crypto
regime with its historical evidence, and the link to the macro quadrant.

Every source is public and needs no API key (nothing to authorize):

  Binance spot (data-api.binance.vision)  daily prices since 2017
  Binance USD-M futures (fapi.binance.com)  funding rates, open interest (30 days)
  CoinGecko                                 market cap, rank, all-time high, BTC dominance
  alternative.me                            Crypto Fear & Greed index
  DefiLlama                                 total stablecoin supply (the ecosystem's "dry powder")

Each source is optional: if one fails or is blocked in your country, the rest
still runs and the failure is listed.

Universe: BTC, ETH, SOL, AAVE and ONDO (chosen by the user), plus XRP, BNB,
DOGE, ADA and LINK: the largest non-stablecoins with 5+ years of Binance
history, covering payments, exchange, smart-contract platforms, oracles,
memes, DeFi lending (AAVE) and tokenized real-world assets (ONDO).
Stablecoins are left out of the price analysis (their price is fixed) and
used as a liquidity gauge instead.

Crypto regime, from BTC: trend (above or below its 200-day average) x
volatility (30-day realized above or below its 1-year median). Evidence: each
coin's return relative to BTC over the next 30 days in each regime, and BTC's
own return in each macro quadrant, graded with the same split-half
robustness as the sector studies. Coins with short histories (ONDO since
2024) simply have fewer windows.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from macro_shock.research import sector_regimes as sr

logger = structlog.get_logger(__name__)

# symbol: (CoinGecko id, segment)
COINS: Dict[str, Tuple[str, str]] = {
    "BTC": ("bitcoin", "Reserva de valor"),
    "ETH": ("ethereum", "Plataforma de contratos"),
    "SOL": ("solana", "Plataforma de contratos"),
    "XRP": ("ripple", "Pagos"),
    "BNB": ("binancecoin", "Exchange"),
    "DOGE": ("dogecoin", "Meme"),
    "ADA": ("cardano", "Plataforma de contratos"),
    "LINK": ("chainlink", "Oráculos"),
    "AAVE": ("aave", "DeFi: préstamos"),
    "ONDO": ("ondo-finance", "Activos reales tokenizados"),
}
SPOT_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
FUTURES_HOST = "https://fapi.binance.com"
HISTORY_START = datetime(2017, 8, 1, tzinfo=timezone.utc)
HORIZON = 30                     # days ahead for the evidence
TREND_DAYS, VOL_DAYS = 200, 30

FUNDING_HOT = 30.0               # % annualized: crowded longs
FUNDING_COLD = 0.0               # below: shorts pay, crowded shorts
GREED, FEAR = 75, 25
DRAWDOWN_ALERT = -20.0           # % from the 1-year high
CORR_RISK = 0.6                  # BTC-Nasdaq correlation: trading as a risk asset

REGIME_ES = {"calm_uptrend": "Tendencia alcista tranquila", "volatile_uptrend": "Tendencia alcista volátil",
             "calm_downtrend": "Tendencia bajista tranquila", "volatile_downtrend": "Tendencia bajista volátil"}


def http_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": "macro-shock-engine/1.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def _to_dates(ms_values) -> pd.DatetimeIndex:
    return pd.to_datetime(pd.Series(ms_values, dtype="int64"), unit="ms").dt.normalize().values


# ── Fetch ────────────────────────────────────────────────────────────────────

def fetch_klines(symbol: str, get: Callable = None) -> pd.Series:
    """Daily close in USDT since 2017 (paginated, 1000 days per call); tries the public mirror first."""
    get = get or http_json
    last_error = None
    for host in SPOT_HOSTS:
        try:
            rows, start = [], _ms(HISTORY_START)
            while True:
                batch = get(f"{host}/api/v3/klines?symbol={symbol}USDT&interval=1d&limit=1000&startTime={start}")
                rows += batch
                if len(batch) < 1000:
                    break
                start = int(batch[-1][0]) + 86_400_000
            if not rows:
                raise ValueError("no data")
            s = pd.Series([float(r[4]) for r in rows], index=_to_dates([int(r[0]) for r in rows]), name=symbol)
            return s[~s.index.duplicated(keep="last")].sort_index()
        except Exception as e:
            last_error = e
    raise ValueError(f"Binance spot {symbol}USDT unavailable ({last_error})")


def fetch_funding(symbol: str, get: Callable = None) -> pd.Series:
    """Funding rate per 8h settlement, last 1000 (about 11 months)."""
    rows = (get or http_json)(f"{FUTURES_HOST}/fapi/v1/fundingRate?symbol={symbol}USDT&limit=1000")
    return pd.Series([float(r["fundingRate"]) for r in rows],
                     index=pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms")).sort_index()


def fetch_open_interest(symbol: str, get: Callable = None) -> pd.Series:
    """Daily open interest in USD, last 30 days (Binance keeps no longer history)."""
    rows = (get or http_json)(f"{FUTURES_HOST}/futures/data/openInterestHist?symbol={symbol}USDT&period=1d&limit=30")
    return pd.Series([float(r["sumOpenInterestValue"]) for r in rows],
                     index=_to_dates([int(r["timestamp"]) for r in rows])).sort_index()


def fetch_fear_greed(get: Callable = None) -> pd.Series:
    data = (get or http_json)("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    return pd.Series([int(d["value"]) for d in data],
                     index=pd.to_datetime([int(d["timestamp"]) for d in data], unit="s").normalize()).sort_index()


def fetch_stablecoins(get: Callable = None) -> pd.Series:
    """Total stablecoin supply in USD (DefiLlama)."""
    rows = (get or http_json)("https://stablecoins.llama.fi/stablecoincharts/all")
    return pd.Series([float(r["totalCirculatingUSD"]["peggedUSD"]) for r in rows],
                     index=pd.to_datetime([int(r["date"]) for r in rows], unit="s").normalize()).sort_index()


def fetch_markets(get: Callable = None) -> pd.DataFrame:
    """Market cap, rank and all-time high per coin (CoinGecko)."""
    ids = ",".join(cid for cid, _ in COINS.values())
    rows = (get or http_json)(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}")
    by_id = {cid: sym for sym, (cid, _) in COINS.items()}
    df = pd.DataFrame([{"symbol": by_id.get(r["id"]), "market_cap": r.get("market_cap"), "rank": r.get("market_cap_rank"),
                        "ath_change": r.get("ath_change_percentage")} for r in rows])
    return df.dropna(subset=["symbol"]).set_index("symbol")


def fetch_dominance(get: Callable = None) -> Dict[str, float]:
    d = (get or http_json)("https://api.coingecko.com/api/v3/global")["data"]
    return {"btc": float(d["market_cap_percentage"]["btc"]), "eth": float(d["market_cap_percentage"].get("eth", np.nan)),
            "total_usd": float(d["total_market_cap"]["usd"])}


@dataclass
class CryptoData:
    prices: pd.DataFrame
    funding: Dict[str, pd.Series] = field(default_factory=dict)
    open_interest: Dict[str, pd.Series] = field(default_factory=dict)
    fear_greed: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    stablecoins: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    markets: pd.DataFrame = field(default_factory=pd.DataFrame)
    dominance: Dict[str, float] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


def fetch(get: Callable = None) -> CryptoData:
    errors, prices = {}, {}
    for sym in COINS:
        try:
            prices[sym] = fetch_klines(sym, get)
        except Exception as e:
            errors[f"precio {sym}"] = str(e)
    if "BTC" not in prices:
        raise ValueError(f"no BTC price from Binance ({errors.get('precio BTC')})")
    data = CryptoData(pd.DataFrame(prices).sort_index())

    def attempt(name, fn):
        try:
            return fn()
        except Exception as e:
            errors[name] = str(e)
            return None
    for sym in COINS:
        f = attempt(f"funding {sym}", lambda: fetch_funding(sym, get))
        if f is not None and len(f):
            data.funding[sym] = f
        oi = attempt(f"interés abierto {sym}", lambda: fetch_open_interest(sym, get))
        if oi is not None and len(oi):
            data.open_interest[sym] = oi
        if f"funding {sym}" in errors and sym == "BTC":
            break                      # futures blocked: skip the other coins' calls
    data.fear_greed = attempt("Fear & Greed", lambda: fetch_fear_greed(get))
    data.stablecoins = attempt("stablecoins", lambda: fetch_stablecoins(get))
    data.markets = attempt("CoinGecko mercados", lambda: fetch_markets(get))
    data.dominance = attempt("CoinGecko dominancia", lambda: fetch_dominance(get)) or {}
    data.fear_greed = data.fear_greed if data.fear_greed is not None else pd.Series(dtype=float)
    data.stablecoins = data.stablecoins if data.stablecoins is not None else pd.Series(dtype=float)
    data.markets = data.markets if data.markets is not None else pd.DataFrame()
    data.errors = errors
    for k, v in errors.items():
        logger.warning("crypto_source_unavailable", source=k, error=v)
    return data


# ── Analytics ────────────────────────────────────────────────────────────────

def _ret(s: pd.Series, days: int) -> float:
    s = s.dropna()
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)] if len(s) else s
    return float((s.iloc[-1] / past.iloc[-1] - 1) * 100) if len(past) else np.nan


def funding_annualized(f: pd.Series, days: int = 7) -> float:
    """Average funding over the last ``days``, annualized (3 settlements a day), in %."""
    recent = f[f.index > f.index[-1] - pd.Timedelta(days=days)]
    return float(recent.mean() * 3 * 365 * 100)


def metrics(data: CryptoData, nasdaq: Optional[pd.Series] = None) -> pd.DataFrame:
    p = data.prices
    rets = p.pct_change(fill_method=None)
    last90 = rets[rets.index > rets.index[-1] - pd.Timedelta(days=90)]
    btc = last90["BTC"]
    nq = None
    if nasdaq is not None and len(nasdaq):
        nq = nasdaq.pct_change().dropna()
    rows = []
    for sym in p.columns:
        s = p[sym].dropna()
        if s.empty:
            continue
        r = last90[sym].dropna()
        both = pd.concat([r, btc], axis=1).dropna()
        beta = float(np.cov(both.iloc[:, 0], both.iloc[:, 1])[0, 1] / both.iloc[:, 1].var()) if len(both) > 20 else np.nan
        high = s[s.index > s.index[-1] - pd.Timedelta(days=365)].max()
        row = {"symbol": sym, "segment": COINS[sym][1], "price": float(s.iloc[-1]), "date": s.index[-1].date(),
               "r7": _ret(s, 7), "r30": _ret(s, 30), "r90": _ret(s, 90), "r365": _ret(s, 365),
               "vol30": float(rets[sym].dropna().iloc[-30:].std() * np.sqrt(365) * 100),
               "dd_1y": float((s.iloc[-1] / high - 1) * 100),
               "beta_btc": beta, "corr_btc": float(both.corr().iloc[0, 1]) if len(both) > 20 else np.nan,
               "corr_nasdaq": np.nan, "funding": np.nan, "oi_30d": np.nan, "history_years": len(s) / 365}
        if nq is not None:
            j = pd.concat([rets[sym], nq], axis=1, join="inner").dropna()
            j = j[j.index > j.index[-1] - pd.Timedelta(days=90)]
            row["corr_nasdaq"] = float(j.corr().iloc[0, 1]) if len(j) > 20 else np.nan
        if sym in data.funding:
            row["funding"] = funding_annualized(data.funding[sym])
        if sym in data.open_interest and len(data.open_interest[sym]) > 1:
            oi = data.open_interest[sym]
            row["oi_30d"] = float((oi.iloc[-1] / oi.iloc[0] - 1) * 100)
        if len(data.markets) and sym in data.markets.index:
            row.update({"market_cap": data.markets.at[sym, "market_cap"], "rank": data.markets.at[sym, "rank"],
                        "ath_change": data.markets.at[sym, "ath_change"]})
        rows.append(row)
    return pd.DataFrame(rows).set_index("symbol")


def regime_series(btc: pd.Series) -> pd.Series:
    """Crypto regime on each day from BTC alone (no look-ahead: trailing windows only)."""
    btc = btc.dropna()
    trend_up = btc > btc.rolling(TREND_DAYS).mean()
    vol = btc.pct_change().rolling(VOL_DAYS).std() * np.sqrt(365)
    vol_high = vol > vol.rolling(365, min_periods=180).median()
    labels = np.where(trend_up, np.where(vol_high, "volatile_uptrend", "calm_uptrend"),
                      np.where(vol_high, "volatile_downtrend", "calm_downtrend"))
    out = pd.Series(labels, index=btc.index)
    valid = btc.rolling(TREND_DAYS).mean().notna() & vol.rolling(365, min_periods=180).median().notna()
    return out[valid]


def evidence(prices: pd.DataFrame, labels: pd.Series, relative_to_btc: bool = True,
             horizon: int = HORIZON) -> pd.DataFrame:
    """split_robustness() rows per coin, one coin at a time so short histories do not cut the others."""
    rets = prices.pct_change(fill_method=None)
    btc = rets["BTC"]
    tables = []
    cols = [c for c in prices.columns if c != "BTC"] if relative_to_btc else list(prices.columns)
    for sym in cols:
        r = rets[[sym]].dropna()
        if len(r) < horizon * 12:
            continue
        fwd, lab = sr.window_returns(r, labels, btc.reindex(r.index) if relative_to_btc else None, horizon)
        if len(fwd) < 16:
            continue
        tables.append(sr.split_robustness(fwd, lab, fwd.index[len(fwd) // 2]))
    return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


@dataclass
class CryptoView:
    data: CryptoData
    table: pd.DataFrame
    regime: Optional[str]
    regime_since: Optional[date]
    by_regime: pd.DataFrame           # coins vs BTC, next 30 days, per crypto regime
    by_quadrant: pd.DataFrame         # BTC and ETH, next 30 days, per macro quadrant
    alerts: List[str]


def alerts(view_table: pd.DataFrame, data: CryptoData) -> List[str]:
    out = []
    for sym, r in view_table.iterrows():
        if pd.notna(r["funding"]) and r["funding"] >= FUNDING_HOT:
            out.append(f"{sym}: funding {r['funding']:+.0f}% anualizado, largos apalancados en exceso (riesgo de liquidaciones)")
        elif pd.notna(r["funding"]) and r["funding"] < FUNDING_COLD:
            out.append(f"{sym}: funding negativo ({r['funding']:+.0f}%), cortos dominantes (posible rebote por cierre de cortos)")
        if sym in ("BTC", "ETH") and pd.notna(r["dd_1y"]) and r["dd_1y"] <= DRAWDOWN_ALERT:
            out.append(f"{sym}: {r['dd_1y']:.0f}% por debajo de su máximo de 12 meses")
    if "BTC" in view_table.index and pd.notna(view_table.at["BTC", "corr_nasdaq"]) and view_table.at["BTC", "corr_nasdaq"] >= CORR_RISK:
        out.append(f"BTC se mueve con el Nasdaq (correlación 90 días {view_table.at['BTC', 'corr_nasdaq']:.2f}): "
                   "cotiza como activo de riesgo, no como refugio")
    fg = data.fear_greed
    if len(fg) and fg.iloc[-1] >= GREED:
        out.append(f"Fear & Greed {fg.iloc[-1]:.0f}: codicia extrema")
    elif len(fg) and fg.iloc[-1] <= FEAR:
        out.append(f"Fear & Greed {fg.iloc[-1]:.0f}: miedo extremo")
    st = data.stablecoins
    if len(st) > 31:
        ch = _ret(st, 30)
        if ch < 0:
            out.append(f"La oferta de stablecoins cae {ch:+.1f}% en 30 días: sale liquidez del ecosistema")
    return out


def analyze(data: CryptoData, nasdaq: Optional[pd.Series] = None, quadrants: Optional[pd.Series] = None) -> CryptoView:
    table = metrics(data, nasdaq)
    labels = regime_series(data.prices["BTC"])
    regime = labels.iloc[-1] if len(labels) else None
    since = None
    if regime is not None:
        changes = labels[labels != regime]
        since = (labels[labels.index > changes.index[-1]].index[0] if len(changes) else labels.index[0]).date()
    by_regime = evidence(data.prices, labels)
    by_quadrant = pd.DataFrame()
    if quadrants is not None and len(quadrants.dropna()):
        q = quadrants.dropna()
        daily = q.reindex(data.prices.index, method="ffill")
        by_quadrant = evidence(data.prices[[c for c in ("BTC", "ETH") if c in data.prices]], daily.dropna(),
                               relative_to_btc=False)
    return CryptoView(data, table, regime, since, by_regime, by_quadrant, alerts(table, data))


# ── Text report ──────────────────────────────────────────────────────────────

def supported(table: pd.DataFrame, label: Optional[str]) -> pd.DataFrame:
    if table.empty or label is None:
        return pd.DataFrame()
    t = table[(table["regime"] == label) & table["evidence"].isin(["robust", "consistent"])]
    return t.sort_values("mean_pct", ascending=False)


def report_lines(view: CryptoView, quadrant: Optional[str] = None) -> List[str]:
    sep = "─" * 72
    t = view.table
    f = lambda v, spec: "n/a" if v is None or pd.isna(v) else format(v, spec)
    lines = [sep, "  CRYPTO (Binance, CoinGecko, alternative.me, DefiLlama)", sep,
             f"  Crypto regime: {view.regime} (since {view.regime_since})" if view.regime else "  Crypto regime: n/a",
             f"    {'Coin':<6}{'price':>12}{'7d':>8}{'30d':>8}{'90d':>8}{'vol30':>8}{'vs 1y hi':>9}{'beta':>6}"
             f"{'corr NQ':>8}{'funding':>9}"]
    for sym, r in t.iterrows():
        lines.append(f"    {sym:<6}{f(r['price'], ',.4g'):>12}{f(r['r7'], '+.1f'):>8}{f(r['r30'], '+.1f'):>8}"
                     f"{f(r['r90'], '+.1f'):>8}{f(r['vol30'], '.0f'):>8}{f(r['dd_1y'], '+.0f'):>9}{f(r['beta_btc'], '.2f'):>6}"
                     f"{f(r['corr_nasdaq'], '+.2f'):>8}{f(r['funding'], '+.0f'):>8}%")
    d = view.data
    extra = []
    if d.dominance:
        extra.append(f"BTC dominance {d.dominance['btc']:.1f}%")
    if len(d.fear_greed):
        extra.append(f"Fear & Greed {d.fear_greed.iloc[-1]:.0f}")
    if len(d.stablecoins):
        extra.append(f"stablecoins {d.stablecoins.iloc[-1] / 1e9:,.0f}bn ({_ret(d.stablecoins, 30):+.1f}% in 30 days)")
    if extra:
        lines += ["", "  " + "  |  ".join(extra)]
    lines += ["", "  ALERTS:" if view.alerts else "  ALERTS: none"] + [f"    ! {a}" for a in view.alerts]
    for title, table, label in (("Coins vs BTC, next 30 days, in this crypto regime", view.by_regime, view.regime),
                                ("BTC / ETH, next 30 days, in this macro quadrant", view.by_quadrant, quadrant)):
        s = supported(table, label)
        lines += ["", f"  {title} ({label}):"]
        if s.empty:
            lines.append("    No result held up in both halves of the history: the evidence supports no tilt.")
        for _, r in s.iterrows():
            mark = "**" if r["evidence"] == "robust" else "* "
            lines.append(f"    {mark} {r['asset']:<6}{r['mean_pct']:>+7.2f}%  beat {r['hit_rate']:.0%}  "
                         f"[{r['ci_low']:+.2f} to {r['ci_high']:+.2f}]  n={int(r['n'])}")
    if d.errors:
        lines += ["", "  Sources unavailable: " + "; ".join(sorted(d.errors))]
    lines += ["  Historical association, not a forecast. Funding: annualized 7-day average.", sep]
    return lines
