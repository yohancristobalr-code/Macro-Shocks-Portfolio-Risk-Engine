"""
tests/unit/test_cpi_nowcast.py

CPI nowcast from energy prices: monthly averaging, the sub-item regressions
and their weights, the backtest (no look-ahead), year-on-year chaining, energy
alerts, the statement cross-check, and the `nowcast`, `daily` and
`analyze --inflation` commands on synthetic FRED-like data with known answers.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import cpi_nowcast as cn

TODAY = pd.Timestamp("2026-10-22")
WEIGHTS = {"gasoline": 0.50, "fuel_oil": 0.03, "electricity": 0.35, "utility_gas": 0.12}
HEADLINE = {"core": 0.79, "food": 0.14, "energy": 0.07}


def synthetic(today=TODAY, seed=1, drop=("2025-10-01",), cpi_lag=1):
    """FRED-like prices and CPI levels where each CPI item follows its prices with known coefficients;
    the last CPI is ``cpi_lag`` months before ``today``'s month."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("1995-01-02", today)
    weeks = pd.date_range("1995-01-02", today, freq="W-MON")

    def walk(idx, start, vol, season=0.0):
        level = start * np.exp(np.cumsum(rng.normal(0, vol, len(idx))))
        return pd.Series(level * (1 + season * np.sin(2 * np.pi * (idx.dayofyear - 80) / 365)), index=idx)

    prices = {"gasoline_retail": walk(weeks, 1.1, 0.02, 0.05), "diesel_retail": walk(weeks, 1.2, 0.02),
              "wti": walk(days, 20, 0.02), "brent": walk(days, 21, 0.02), "gasoline_nyh": walk(days, 0.6, 0.02),
              "ulsd_nyh": walk(days, 0.6, 0.02), "heating_oil": walk(days, 0.6, 0.02),
              "henry_hub": walk(days[days >= "1997-01-07"], 2.5, 0.03)}
    F = cn.build_frame(prices, {})
    months = pd.date_range("1995-01-01", cn.month_start(today) - pd.offsets.MonthBegin(cpi_lag), freq="MS")
    p = {k: F[f"p:{k}"].reindex(months).fillna(0.0) for k in ("gasoline_retail", "diesel_retail", "henry_hub")}
    season = pd.Series(4 * np.sin(2 * np.pi * (months.month - 3) / 12), index=months)
    noise = lambda sd: pd.Series(rng.normal(0, sd, len(months)), index=months)
    items = {
        "gasoline": 0.95 * p["gasoline_retail"] - season + noise(0.5),
        "fuel_oil": 0.8 * p["diesel_retail"] + noise(1.0),
        "electricity": 0.2 + 0.05 * p["henry_hub"].shift(1).fillna(0) + noise(0.4),
        "utility_gas": 0.15 * p["henry_hub"] + 0.10 * p["henry_hub"].shift(1).fillna(0) + noise(1.0),
    }
    energy = sum(WEIGHTS[k] * v for k, v in items.items())
    parts = {"core": 0.25 + noise(0.08), "food": 0.2 + noise(0.1), "energy": energy}
    headline = sum(HEADLINE[k] * v for k, v in parts.items()) + noise(0.01)

    def level(mm):
        lv = 100 * (1 + mm / 100).cumprod()
        return lv.drop(pd.DatetimeIndex(drop), errors="ignore")

    cpi = {k: level(v) for k, v in {**items, **parts, "headline": headline}.items()}
    return prices, cpi


def as_fred(prices, cpi):
    data = {cn.PRICES[k][0]: v for k, v in prices.items()}
    data.update({cn.CPI[k][0]: v for k, v in cpi.items()})
    return data


@pytest.fixture(scope="module")
def data():
    return synthetic()


@pytest.fixture(scope="module")
def nowcast(data):
    return cn.nowcast(*data)


class TestMonthlySeries:

    def test_monthly_average_weights_calendar_days_and_fills_the_last_month(self):
        s = pd.Series([1.0, 2.0, 4.0], index=pd.to_datetime(["2026-09-01", "2026-09-16", "2026-10-05"]))
        avg = cn.monthly_average(s)
        assert avg[pd.Timestamp("2026-09-01")] == pytest.approx((15 * 1 + 15 * 2) / 30)
        # October: days 1-4 carry September's last price, then 4.0 to the end of the month
        assert avg[pd.Timestamp("2026-10-01")] == pytest.approx((4 * 2 + 27 * 4) / 31)

    def test_change_next_to_a_missing_month_is_not_a_two_month_change(self):
        lv = pd.Series([100.0, 101.0, 103.0], index=pd.to_datetime(["2025-09-01", "2025-11-01", "2025-12-01"]))
        ch = cn.pct_change(lv)
        assert pd.isna(ch[pd.Timestamp("2025-10-01")]) and pd.isna(ch[pd.Timestamp("2025-11-01")])
        assert ch[pd.Timestamp("2025-12-01")] == pytest.approx(100 * (103 / 101 - 1))

    def test_nonneg_lstsq_drops_negative_weights(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(200, 3))
        y = X @ np.array([0.6, -0.2, 0.4])
        w = cn.nonneg_lstsq(X, y)
        assert (w >= 0).all() and w[1] == 0.0

    def test_crack_spread_formula(self):
        idx = pd.to_datetime(["2026-10-21"])
        prices = {"gasoline_nyh": pd.Series([2.40], idx), "ulsd_nyh": pd.Series([2.70], idx),
                  "wti": pd.Series([70.0], idx)}
        assert cn.crack_321(prices).iloc[0] == pytest.approx((2 * 2.40 + 2.70) * 42 / 3 - 70)


class TestModel:

    def test_weights_are_recovered(self, data):
        F = cn.build_frame(*data)
        cutoff = pd.Timestamp("2026-10-01")
        w_energy = cn.weights(F, "energy", list(WEIGHTS), cutoff)
        w_head = cn.weights(F, "headline", ["core", "food", "energy"], cutoff)
        for k, v in WEIGHTS.items():
            assert w_energy[k] == pytest.approx(v, abs=0.01)
        for k, v in HEADLINE.items():
            assert w_head[k] == pytest.approx(v, abs=0.03)

    def test_no_look_ahead(self, data):
        prices, cpi = data
        F = cn.build_frame(prices, cpi)
        month = pd.Timestamp("2015-06-01")
        before = cn.predict_month(F, month, month)
        changed = F.copy()
        changed.loc[changed.index >= month, list(cn.CPI)] = 99.0     # the future is replaced
        after = cn.predict_month(changed, month, month)
        assert after["values"]["headline"] == pytest.approx(before["values"]["headline"])
        assert after["values"]["gasoline"] == pytest.approx(before["values"]["gasoline"])

    def test_backtest_beats_the_naive_guess(self, nowcast):
        stats = nowcast.stats
        for target in ("gasoline", "energy", "headline"):
            assert stats.loc[target, "mae"] < 0.5 * stats.loc[target, "naive_mae"]
        assert stats.loc["gasoline", "n"] > 150

    def test_contributions_add_up(self, nowcast):
        t = nowcast.table[nowcast.table["month"] == nowcast.months[0]].set_index("item")
        items = list(WEIGHTS)
        assert t.loc[items, "contribution"].sum() == pytest.approx(t.loc["energy", "contribution"])
        assert t.loc[["core", "food", "energy"], "contribution"].sum() == pytest.approx(t.loc["headline", "mm"])
        assert (t.loc[items, "weight"] * t.loc[items, "mm"]).sum() == pytest.approx(t.loc["energy", "mm"])

    def test_ranges_bracket_the_nowcast(self, nowcast):
        t = nowcast.table[nowcast.table["kind"] == "nowcast"]
        assert ((t["low"] <= t["mm"]) & (t["mm"] <= t["high"])).all()

    def test_optional_item_missing_is_left_out(self, data):
        prices, cpi = data
        nc = cn.nowcast(prices, {k: v for k, v in cpi.items() if k != "fuel_oil"})
        assert "fuel_oil" not in set(nc.table["item"]) and "gasoline" in set(nc.table["item"])


class TestMonths:

    def test_target_is_the_month_after_the_last_cpi(self, nowcast):
        assert nowcast.last_cpi_month == pd.Timestamp("2026-09-01")
        assert nowcast.months == [pd.Timestamp("2026-10-01")]
        # Monday 2026-10-19 covers the week to the 25th
        assert nowcast.observed_share[pd.Timestamp("2026-10-01")] == pytest.approx(25 / 31)

    def test_two_months_when_the_new_month_has_a_gasoline_price(self, data):
        prices, cpi = synthetic(today=pd.Timestamp("2026-11-04"))
        cpi = {k: v[v.index <= "2026-09-01"] for k, v in cpi.items()}
        nc = cn.nowcast(prices, cpi)
        assert nc.months == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-11-01")]
        assert nc.observed_share[pd.Timestamp("2026-10-01")] == 1.0

    def test_yoy_missing_when_last_year_month_is_missing(self, nowcast):
        # October 2025 CPI was never published (government shutdown)
        assert nowcast.table["yoy"].isna().all()

    def test_yoy_chains_the_nowcast(self):
        prices, cpi = synthetic(drop=())
        nc = cn.nowcast(prices, cpi)
        m = nc.months[0]
        lv = cpi["headline"]
        expected = (lv.iloc[-1] * (1 + nc.value(m, "headline") / 100) / lv[m - pd.DateOffset(years=1)] - 1) * 100
        row = nc.table[(nc.table["month"] == m) & (nc.table["item"] == "headline")].iloc[0]
        assert row["yoy"] == pytest.approx(expected)


class TestAlertsAndCheck:

    def _series(self, start, end, freq="B"):
        """Flat at ``start``, then ``end`` over the last 10 days."""
        idx = pd.date_range(end=TODAY, periods=60, freq=freq)
        return pd.Series(np.where(idx > TODAY - pd.Timedelta(days=10), end, start), index=idx, dtype=float)

    def test_crude_spike_alerts(self):
        out = cn.energy_alerts({"wti": self._series(60, 80)})
        assert len(out) == 1 and out[0].startswith("WTI crude up") and "pressure on energy CPI" in out[0]

    def test_small_moves_and_falls(self):
        assert cn.energy_alerts({"wti": self._series(70, 72)}) == []
        out = cn.energy_alerts({"diesel_retail": self._series(5.0, 4.0, "W-MON")})
        assert "Diesel, retail down 20.0%" in out[0] and "relief" in out[0]

    def _nc(self, headline_mm):
        m = pd.Timestamp("2026-10-01")
        table = pd.DataFrame([{"month": m, "item": "headline", "label": "Headline CPI", "mm": headline_mm,
                               "low": np.nan, "high": np.nan, "weight": np.nan, "contribution": np.nan,
                               "yoy": None, "kind": "nowcast"},
                              {"month": m, "item": "energy", "label": "Energy", "mm": 2.0, "low": np.nan,
                               "high": np.nan, "weight": 0.07, "contribution": 0.14, "yoy": None,
                               "kind": "nowcast"}])
        return cn.CpiNowcast(pd.Timestamp("2026-09-01"), 0.3, table, pd.DataFrame(), pd.DataFrame(), {},
                             date(2026, 10, 19), {m: 0.8})

    def test_hawkish_with_high_inflation_is_consistent(self):
        assert any("CONSISTENT" in l for l in cn.inflation_check(self._nc(0.40), "hawkish"))

    def test_hawkish_with_low_inflation_contradicts(self):
        assert any("CONTRADICTS" in l for l in cn.inflation_check(self._nc(0.00), "very_hawkish"))

    def test_dovish_with_high_inflation_contradicts(self):
        assert any("CONTRADICTS" in l for l in cn.inflation_check(self._nc(0.40), "dovish"))

    def test_neutral_tone_or_inflation_near_target_is_no_signal(self):
        assert any("No clear confirmation" in l for l in cn.inflation_check(self._nc(0.40), "neutral"))
        assert any("No clear confirmation" in l for l in cn.inflation_check(self._nc(0.17), "hawkish"))


class FakeFred:
    def __init__(self, data):
        self.data = data

    def get_series(self, sid, observation_start=None, observation_end=None, **kw):
        s = self.data[sid]
        return s[(s.index >= pd.Timestamp(observation_start)) & (s.index <= pd.Timestamp(observation_end))]


@pytest.fixture(scope="module")
def fred_data():
    # CPI two months back: there is always a month to nowcast, whatever today's date
    return as_fred(*synthetic(today=pd.Timestamp(date.today()), drop=(), cpi_lag=2))


class TestCommands:

    def _cli(self, monkeypatch, data, *argv):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        fake = FakeFred(data)
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
        monkeypatch.setattr(feed_provider.FREDFeedProvider, "_get_fred", lambda self: fake)
        monkeypatch.setattr(sys, "argv", ["msre", *argv])
        cli.main()

    def test_nowcast_command(self, monkeypatch, capsys, tmp_path, fred_data):
        out_csv = tmp_path / "bt.csv"
        self._cli(monkeypatch, fred_data, "nowcast", "--output", str(out_csv))
        out = capsys.readouterr().out
        assert "CPI NOWCAST FROM ENERGY PRICES" in out and "Nowcast for" in out
        assert "Gasoline" in out and "Refining margin 3-2-1" in out and "better than naive" in out
        assert set(pd.read_csv(out_csv)["target"]) >= {"gasoline", "energy", "headline"}

    def test_nowcast_command_without_cpi_exits(self, monkeypatch, capsys, fred_data):
        data = {k: v for k, v in fred_data.items() if k != "CPIAUCSL"}
        with pytest.raises(SystemExit):
            self._cli(monkeypatch, data, "nowcast")
        assert "CPIAUCSL is unavailable" in capsys.readouterr().out

    def _daily_data(self, fred_data):
        """Add the market series the daily report reads (calm market, ending today)."""
        bdays = pd.bdate_range(end=date.today(), periods=300)
        days = pd.date_range(end=date.today(), periods=420)
        flat = lambda v: pd.Series(v, index=bdays)
        market = {"DGS2": flat(4.2), "DGS5": flat(4.3), "DGS10": flat(4.5), "DGS30": flat(4.8),
                  "DFII10": flat(2.0), "T10YIE": flat(2.3), "BAMLH0A0HYM2": flat(3.1), "BAMLC0A0CM": flat(0.9),
                  "VIXCLS": flat(16.0), "SP500": pd.Series(np.linspace(5000, 5200, len(days)), index=days),
                  "CBBTCUSD": pd.Series(65000.0, index=days)}
        return {**fred_data, **market}

    def test_daily_shows_energy_and_nowcast(self, monkeypatch, capsys, tmp_path, fred_data):
        from macro_shock.research import daily, sector_study
        monkeypatch.setattr(sector_study, "run", lambda *a, **k: object())
        monkeypatch.setattr(sector_study, "supported_lines", lambda study, regime: ["head"])
        self._cli(monkeypatch, self._daily_data(fred_data), "daily", "--reports-dir", str(tmp_path))
        out = capsys.readouterr().out
        assert "Energy: WTI" in out and "3-2-1 margin" in out
        assert "CPI nowcast" in out and "headline" in out
        log = daily.read_log(tmp_path / "daily_log.csv")
        assert list(log.columns) == daily.LOG_COLUMNS and log["wti"].notna().all()

    def test_daily_quick_shows_energy_without_nowcast(self, monkeypatch, capsys, tmp_path, fred_data):
        self._cli(monkeypatch, self._daily_data(fred_data), "daily", "--quick", "--reports-dir", str(tmp_path))
        out = capsys.readouterr().out
        assert "Energy: WTI" in out and "CPI nowcast" not in out

    def test_daily_energy_alert(self, monkeypatch, capsys, tmp_path, fred_data):
        data = self._daily_data(fred_data)
        idx = pd.bdate_range(end=date.today(), periods=300)
        data["DCOILWTICO"] = pd.Series(np.where(idx > idx[-1] - pd.Timedelta(days=10), 91.0, 70.0), index=idx)
        self._cli(monkeypatch, data, "daily", "--quick", "--reports-dir", str(tmp_path))
        assert "WTI crude up 30.0% in 30 days (70.00 -> 91.00)" in capsys.readouterr().out

    def test_old_log_without_energy_columns_still_works(self, monkeypatch, capsys, tmp_path, fred_data):
        from macro_shock.research import daily
        old = pd.DataFrame([{c: v for c, v in zip(daily.LOG_COLUMNS[:11],
                             [str(date.today() - timedelta(days=1)), str(date.today() - timedelta(days=1)),
                              "fragile_risk_on", 0.4, 16.0, 320.0, 90.0, 30.0, 4.5, -1.0, 60000.0])}])
        old.to_csv(tmp_path / "daily_log.csv", index=False)
        self._cli(monkeypatch, self._daily_data(fred_data), "daily", "--quick", "--reports-dir", str(tmp_path))
        log = daily.read_log(tmp_path / "daily_log.csv")
        assert len(log) == 2 and pd.isna(log.iloc[0]["wti"]) and pd.notna(log.iloc[1]["wti"])

    def test_analyze_inflation_cross_check(self, monkeypatch, capsys, fred_data):
        self._cli(monkeypatch, fred_data, "analyze", "--title", "FOMC", "--institution", "Federal Reserve",
                  "--event-time", "2026-10-28T14:00:00-04:00", "--inflation",
                  "--text", "Inflation too high. Further rate hikes may be appropriate.")
        out = capsys.readouterr().out
        assert "INFLATION CROSS-CHECK" in out and "CPI nowcast" in out and "Statement tone:" in out
