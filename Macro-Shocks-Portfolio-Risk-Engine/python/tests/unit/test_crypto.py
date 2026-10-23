"""
tests/unit/test_crypto.py

The crypto axis on fake APIs that answer in the documented Binance,
CoinGecko, alternative.me and DefiLlama formats; Fed liquidity; the data
health check; and the `crypto` command.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import crypto as cr, data_health, liquidity as lq

TODAY = pd.Timestamp(date.today())
STARTS = {"ONDO": pd.Timestamp("2024-01-18"), "SOL": pd.Timestamp("2020-08-11")}


def synthetic_prices(seed=0):
    """BTC random walk; SOL beats BTC by 0.4%/day in uptrends and lags by 0.2%/day in downtrends."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2017-08-17", TODAY, freq="D")
    btc = pd.Series(4000 * np.exp(np.cumsum(rng.normal(0.0008, 0.035, len(days)))), days)
    up = (btc > btc.rolling(cr.TREND_DAYS).mean()).to_numpy()
    out = {"BTC": btc}
    for i, sym in enumerate(c for c in cr.COINS if c != "BTC"):
        r = np.log(btc).diff().fillna(0).to_numpy() * 1.2 + rng.normal(0, 0.02, len(days))
        if sym == "SOL":
            r = np.log(btc).diff().fillna(0).to_numpy() + np.where(np.roll(up, 1), 0.004, -0.002) + rng.normal(0, 0.01, len(days))
        s = pd.Series(10 * np.exp(np.cumsum(r)), days)
        out[sym] = s[s.index >= STARTS.get(sym, days[0])]
    return out


class FakeAPI:
    """Answers each public endpoint in its documented JSON shape."""

    def __init__(self, prices, funding_btc=0.0004, fail=()):
        self.prices, self.funding_btc, self.fail, self.calls = prices, funding_btc, set(fail), []

    def __call__(self, url, timeout=20.0):
        self.calls.append(url)
        if any(f in url for f in self.fail):
            raise OSError("HTTP Error 451: restricted location")
        if "/api/v3/klines" in url:
            sym = re.search(r"symbol=(\w+?)USDT", url).group(1)
            start = pd.to_datetime(int(re.search(r"startTime=(\d+)", url).group(1)), unit="ms")
            s = self.prices[sym]
            s = s[s.index >= start].iloc[:1000]
            return [[int(d.value // 10**6), "0", "0", "0", f"{v:.8f}", "1", int(d.value // 10**6) + 86399999, "1", 1, "1", "1", "0"]
                    for d, v in s.items()]
        if "fundingRate" in url:
            sym = re.search(r"symbol=(\w+?)USDT", url).group(1)
            times = pd.date_range(end=TODAY, periods=1000, freq="8h")
            rate = self.funding_btc if sym == "BTC" else 0.0001
            return [{"symbol": f"{sym}USDT", "fundingTime": int(t.value // 10**6), "fundingRate": f"{rate:.8f}",
                     "markPrice": "1"} for t in times]
        if "openInterestHist" in url:
            days = pd.date_range(end=TODAY, periods=30)
            return [{"symbol": "X", "sumOpenInterest": "1", "sumOpenInterestValue": f"{1e9 * (1 + i / 100):.2f}",
                     "timestamp": int(d.value // 10**6)} for i, d in enumerate(days)]
        if "alternative.me" in url:
            days = pd.date_range(end=TODAY, periods=400)[::-1]            # the API lists newest first
            return {"data": [{"value": str(80 if i == 0 else 50), "value_classification": "x",
                              "timestamp": str(int(d.timestamp()))} for i, d in enumerate(days)]}
        if "stablecoincharts" in url:
            days = pd.date_range(end=TODAY, periods=800)
            return [{"date": str(int(d.timestamp())), "totalCirculatingUSD": {"peggedUSD": 2.6e11 - i * 1e8}}
                    for i, d in enumerate(days)]
        if "coins/markets" in url:
            return [{"id": cid, "market_cap": 1e9 * (10 - i), "market_cap_rank": i + 1, "ath_change_percentage": -30.0}
                    for i, (cid, _) in enumerate(cr.COINS.values())]
        if "publicreporting.cftc.gov" in url:
            from .test_valuation_positioning import cftc_rows
            return cftc_rows(0.0)
        if "/global" in url:
            return {"data": {"market_cap_percentage": {"btc": 58.2, "eth": 11.0}, "total_market_cap": {"usd": 3.1e12}}}
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture(scope="module")
def prices():
    return synthetic_prices()


@pytest.fixture(scope="module")
def data(prices):
    return cr.fetch(FakeAPI(prices))


class TestFetch:

    def test_all_sources_parse(self, data):
        assert list(data.prices.columns) == list(cr.COINS)
        assert data.prices["ONDO"].first_valid_index() == STARTS["ONDO"]
        assert data.prices["BTC"].index[-1] == TODAY
        assert set(data.funding) == set(cr.COINS) and len(data.open_interest["BTC"]) == 30
        assert data.fear_greed.iloc[-1] == 80 and data.fear_greed.index.is_monotonic_increasing
        assert data.dominance["btc"] == 58.2 and data.markets.at["ONDO", "rank"] == 10
        assert not data.errors

    def test_klines_paginate(self, prices):
        api = FakeAPI(prices)
        s = cr.fetch_klines("BTC", api)
        assert len(s) == len(prices["BTC"]) and sum("klines" in u for u in api.calls) >= 3

    def test_spot_mirror_falls_back(self, prices):
        api = FakeAPI(prices, fail=("data-api.binance.vision",))
        assert len(cr.fetch_klines("ETH", api)) == len(prices["ETH"])

    def test_blocked_futures_and_coingecko_do_not_stop_the_rest(self, prices):
        api = FakeAPI(prices, fail=("fapi.binance.com", "coingecko"))
        d = cr.fetch(api)
        assert not d.funding and d.markets.empty and len(d.prices.columns) == 10
        assert "funding BTC" in d.errors and "CoinGecko mercados" in d.errors
        assert sum("fundingRate" in u for u in api.calls) == 1        # stops after BTC fails

    def test_no_btc_is_an_error(self, prices):
        with pytest.raises(ValueError, match="no BTC price"):
            cr.fetch(FakeAPI(prices, fail=("BTCUSDT",)))


class TestAnalytics:

    def test_metrics(self, data):
        t = cr.metrics(data)
        assert t.at["BTC", "beta_btc"] == pytest.approx(1.0) and t.at["BTC", "corr_btc"] == pytest.approx(1.0)
        assert t.at["BTC", "funding"] == pytest.approx(0.0004 * 3 * 365 * 100)
        assert t.at["ETH", "oi_30d"] == pytest.approx(29.0)
        assert t.at["ONDO", "history_years"] < 3.5

    def test_regime_uses_only_the_past(self, prices):
        btc = prices["BTC"]
        a = cr.regime_series(btc)
        b = cr.regime_series(pd.concat([btc.iloc[:2000], btc.iloc[2000:] * 3]))
        pd.testing.assert_series_equal(a.iloc[:1500], b.iloc[:1500])
        assert set(a.unique()) <= set(cr.REGIME_ES)

    def test_planted_sol_effect(self, data):
        view = cr.analyze(data)
        sol = view.by_regime[view.by_regime["asset"] == "SOL"].set_index("regime")
        up = [r for r in sol.index if "uptrend" in r]
        # ~16 windows per regime: too few per half for a grade, but the effect and its interval are clear
        assert all(sol.loc[r, "mean_pct"] > 5 and sol.loc[r, "ci_low"] > 0 for r in up)
        down = [r for r in sol.index if "downtrend" in r]
        assert all(sol.loc[r, "mean_pct"] < sol.loc[u, "mean_pct"] for r in down for u in up)

    def test_alerts(self, data):
        view = cr.analyze(data)
        text = "\n".join(view.alerts)
        assert "BTC: funding +44% anualizado" in text and "Fear & Greed 80: codicia extrema" in text
        assert "stablecoins cae" in text

    def test_quadrant_evidence(self, data):
        months = pd.date_range("2017-02-01", TODAY, freq="MS") - pd.Timedelta(days=1)
        q = pd.Series(np.where(np.arange(len(months)) % 24 < 12, "reacceleration", "growth_scare"), months)
        view = cr.analyze(data, quadrants=q)
        assert set(view.by_quadrant["asset"]) == {"BTC", "ETH"}
        assert set(view.by_quadrant["regime"]) == {"reacceleration", "growth_scare"}

    def test_price_format_and_small_negative_funding(self, data):
        assert cr.fmt_price(81226.4) == "81,226" and cr.fmt_price(104.234) == "104.23" and cr.fmt_price(0.43621) == "0.4362"
        t = cr.metrics(data)
        t.loc["ADA", "funding"] = -0.3                                 # noise, not crowded shorts
        assert not any(a.startswith("ADA") for a in cr.alerts(t, data))
        t.loc["ADA", "funding"] = -12.0
        assert any(a.startswith("ADA: funding negativo") for a in cr.alerts(t, data))

    def test_report_lines(self, data):
        text = "\n".join(cr.report_lines(cr.analyze(data), "reacceleration"))
        assert "CRYPTO" in text and "ONDO" in text and "BTC dominance 58.2%" in text and "ALERTS:" in text


class TestLiquidity:

    def series(self):
        weeks = pd.date_range(end=TODAY, periods=300, freq="W-WED")
        days = pd.bdate_range(end=TODAY, periods=1500)
        walk = 7_000_000 + np.cumsum(np.random.default_rng(1).normal(-1500, 20_000, 300))
        walk[-1] = walk[-14] - 50_000                                                   # falling over 13 weeks
        return {"walcl": pd.Series(walk, weeks),                                         # millions
                "tga": pd.Series(800_000.0, weeks), "rrp": pd.Series(100.0, days),               # millions, billions
                "y2": pd.Series(3.6, days), "ffr": pd.Series(3.9, days)}

    def test_net_liquidity_units(self):
        s = self.series()
        net = lq.net_liquidity(s)
        assert net.iloc[-1] == pytest.approx(s["walcl"].iloc[-1] / 1000 - 800 - 100)
        assert lq.expectations(s).iloc[-1] == pytest.approx(-30)

    def test_assess_and_link(self):
        s = self.series()
        asset = lq.net_liquidity(s) * 10                               # moves exactly with liquidity
        liq = lq.assess(s, {"Fake": asset})
        assert liq.change_13w < 0 and liq.links["Fake"] > 0.9
        text = "\n".join(lq.lines(liq))
        assert "Fed net liquidity" in text and "cuts" in text

    def test_no_balance_sheet_is_an_error(self):
        s = self.series()
        s["walcl"] = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        with pytest.raises(ValueError):
            lq.assess(s)


class TestDataHealth:

    def test_statuses(self):
        today = date(2026, 10, 22)
        daily = pd.Series(1.0, pd.bdate_range(end="2026-10-21", periods=50))
        stale = pd.Series(1.0, pd.bdate_range(end="2026-08-01", periods=50))
        monthly = pd.Series(1.0, pd.date_range(end="2026-09-01", periods=24, freq="MS"))
        h = data_health.check({"FRED": {"ok": daily, "old": stale, "cpi": monthly,
                                        "missing": pd.Series(dtype=float)}}, today).set_index("series")
        assert h.loc["ok", "status"] == "ok" and h.loc["cpi", "status"] == "ok" and h.loc["cpi", "freq"] == "mensual"
        assert h.loc["old", "status"] == "stale" and h.loc["missing", "status"] == "failed"
        assert list(h["status"])[0] == "failed"
        ahead = data_health.check({"Cripto": {"btc": pd.Series(1.0, pd.date_range(end="2026-10-23", periods=30))}}, today)
        assert ahead.iloc[0]["age"] == 0 and ahead.iloc[0]["status"] == "ok"          # UTC one day ahead
        oecd = pd.Series(1.0, pd.date_range(end="2026-08-01", periods=24, freq="MS"))     # ~2.5 months late
        assert data_health.check({"FRED": {"jp3m": oecd}}, today).iloc[0]["status"] == "ok"
        z1 = pd.Series(1.0, pd.date_range(end="2026-04-01", periods=40, freq="QS"))      # Q2 published in September
        assert data_health.check({"FRED": {"equities": z1}}, today).iloc[0]["status"] == "ok"
        short = data_health.check({"FRED": {"two": pd.Series(1.0, pd.DatetimeIndex(["2026-08-01", "2026-09-01"]))}}, today)
        assert short.iloc[0]["status"] == "ok"                          # unknown frequency: no false alarm


class TestCryptoCommand:

    def test_command_without_fred(self, monkeypatch, capsys, prices):
        from macro_shock import __main__ as cli
        monkeypatch.setattr(cr, "http_json", FakeAPI(prices))
        monkeypatch.setattr(cli, "_PROJECT_ROOT", Path("/nonexistent"))
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        monkeypatch.chdir("/")
        monkeypatch.setattr(sys, "argv", ["msre", "crypto"])
        cli.main()
        out = capsys.readouterr().out
        assert "FRED part skipped" in out and "Crypto regime:" in out and "SOL" in out
