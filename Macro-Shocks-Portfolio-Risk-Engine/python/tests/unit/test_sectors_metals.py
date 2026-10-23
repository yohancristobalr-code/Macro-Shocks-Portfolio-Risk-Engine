"""
tests/unit/test_sectors_metals.py

S&P 500 sector ETFs, gold/silver/copper and the page-one view, on a fake
Stooq that answers CSV files and planted relationships: energy rises with
oil, utilities fall when yields rise, gold falls when real yields rise.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import macro_scenario as ms, metals as mt, sectors as sc, stooq, view as vw

TODAY = pd.Timestamp(date.today())
DAYS = pd.bdate_range(end=TODAY, periods=1600)


def factors(seed=0):
    rng = np.random.default_rng(seed)
    n = len(DAYS)
    return pd.DataFrame({"y10": 4 + np.cumsum(rng.normal(0, 0.05, n)), "oil": 70 * np.exp(np.cumsum(rng.normal(0, 0.02, n))),
                         "dollar": 110 * np.exp(np.cumsum(rng.normal(0, 0.004, n))),
                         "credit": 3.5 + np.cumsum(rng.normal(0, 0.03, n)).clip(-2, 4),
                         "real10": 2 + np.cumsum(rng.normal(0, 0.04, n))}, index=DAYS)


def market_prices(f, seed=1):
    rng = np.random.default_rng(seed)
    spy_r = rng.normal(0.0004, 0.01, len(DAYS))
    out = {"SPY": 400 * np.exp(np.cumsum(spy_r))}
    oil_r = np.log(f["oil"]).diff().fillna(0).to_numpy()
    dy = f["y10"].diff().fillna(0).to_numpy() * 100                  # bps
    for i, sym in enumerate(sc.SECTORS):
        extra = rng.normal(0, 0.004, len(DAYS))
        if sym == "XLE":
            extra += 0.5 * oil_r                                        # energy rides oil
        if sym == "XLU":
            extra -= 0.0008 * dy                                        # -0.08% per bp of 10Y
        if sym == "XLK":
            extra += 0.002                                              # strong momentum (+50%/yr vs SPY)
        out[sym] = 50 * np.exp(np.cumsum(spy_r + extra))
    gold = 1800 * np.exp(-0.15 * (f["real10"] - 2) + np.cumsum(rng.normal(0, 0.002, len(DAYS))))
    out.update({"xauusd": gold, "xagusd": gold / 80 * np.exp(np.cumsum(rng.normal(0, 0.004, len(DAYS)))),
                "hg.f": 4 * np.exp(np.cumsum(rng.normal(0, 0.01, len(DAYS))))})
    return pd.DataFrame(out, index=DAYS)


def fake_stooq(prices, missing=()):
    def get(url, timeout=20.0):
        sym = url.split("s=")[1].split("&")[0].replace(".us", "").upper() if ".us" in url else url.split("s=")[1].split("&")[0]
        if sym in missing or sym not in prices:
            return "No data"
        rows = "\n".join(f"{d.date()},1,1,1,{v:.6f},100" for d, v in prices[sym].items())
        return "Date,Open,High,Low,Close,Volume\n" + rows
    return get


@pytest.fixture(scope="module")
def f():
    return factors()


@pytest.fixture(scope="module")
def prices(f):
    return market_prices(f)


class TestStooq:

    def test_parse_and_errors(self, prices):
        get = fake_stooq(prices)
        s = stooq.daily_close("spy.us", get)
        assert len(s) == len(DAYS) and s.index[-1] == TODAY
        with pytest.raises(ValueError, match="No data"):
            stooq.daily_close("nope.us", get)


class TestSectors:

    def test_planted_betas_and_scenarios(self, prices, f):
        p, errors = sc.fetch(fake_stooq(prices))
        view = sc.analyze(p, f[["y10", "oil", "dollar", "credit"]], None, errors)
        t = view.table
        assert t.at["XLE", "beta_oil"] == pytest.approx(0.5, abs=0.12)
        assert t.at["XLU", "beta_y10"] == pytest.approx(-0.08, abs=0.03)
        assert t.at["XLK", "momentum_rank"] == 1
        # supply shock = oil up, curve steeper...: energy should top it
        assert view.scenarios["supply_shock"].idxmax() == "XLE"
        assert 0 <= view.breadth <= 1 and not errors

    def test_missing_sector_and_missing_spy(self, prices, f):
        p, errors = sc.fetch(fake_stooq(prices, missing=("XLRE",)))
        assert "XLRE" in errors and "XLRE" not in sc.analyze(p, None, None, errors).table.index
        with pytest.raises(ValueError, match="SPY"):
            sc.fetch(fake_stooq(prices, missing=("SPY",)))


class TestMetals:

    def test_gold_vs_real_yield_and_ratios(self, prices, f, monkeypatch):
        d = mt.fetch(get_text=fake_stooq(prices), get_json=lambda url, timeout=20.0: (_ for _ in ()).throw(OSError("blocked")))
        assert set(d.prices) == {"gold", "silver", "copper"} and d.source["gold"] == "Stooq"
        v = mt.analyze(d, f["real10"], f["y10"])
        assert v.gold_vs_real["slope_pct_per_100bp"] == pytest.approx(-15, abs=3)
        assert v.gold_silver.iloc[-1] > 0 and v.copper_gold is not None

    def test_binance_fallback_for_gold(self, prices):
        rows = [[int(d.value // 10**6), "0", "0", "0", "2400.5", "0"] for d in DAYS[-300:]]
        def get_json(url, timeout=20.0):
            if "klines" in url:
                return rows
            return [{"fundingTime": int(TODAY.value // 10**6), "fundingRate": "0.0002"}]
        d = mt.fetch(get_text=fake_stooq(prices, missing=("xauusd",)), get_json=get_json)
        assert d.source["gold"] == "Binance perpetuo" and d.prices["gold"].iloc[-1] == 2400.5
        assert d.funding["gold"] == pytest.approx(0.0002 * 3 * 365 * 100)


class TestView:

    def series(self, up=True):
        s = pd.Series(np.linspace(100, 150 if up else 60, 400), index=pd.bdate_range(end=TODAY, periods=400))
        return s

    def test_supply_shock_view(self):
        s = {"equities": self.series(False), "bonds": self.series(True), "credit": self.series(True),
             "gold": self.series(True), "copper": self.series(True), "oil": self.series(True),
             "dollar": self.series(True), "bitcoin": self.series(False)}
        av = vw.asset_view(s, "supply_shock", "CONFIRMED", "supply_shock").set_index("key")
        assert av.at["gold", "view"] == "Sobreponderar" and av.at["oil", "view"] == "Sobreponderar"
        assert av.at["equities", "view"] == "Infraponderar" and av.at["bonds", "view"] == "Infraponderar"
        assert av.at["gold", "conviction"] == pytest.approx(1.0)

    def test_partial_scenario_counts_half(self):
        av = vw.asset_view({"gold": self.series(True)}, "supply_shock", "partial", None).set_index("key")
        assert av.at["gold", "scenario"] == 0.5 and av.at["gold", "score"] == 2.5

    def test_vix_term(self):
        class Fred:
            def get_series(self, sid, observation_start=None, observation_end=None):
                idx = pd.bdate_range(end=TODAY, periods=100)
                return pd.Series(30.0 if sid == "VIXCLS" else 25.0, idx)
        term = vw.vix_term(Fred(), TODAY.date())
        assert term["ratio"].iloc[-1] == pytest.approx(1.2) and "inverted" in vw.vix_alert(term)[0]
