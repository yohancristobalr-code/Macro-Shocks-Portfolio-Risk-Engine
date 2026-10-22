"""
tests/unit/test_sector_regimes.py

Unit tests for the French Data Library reader and the sector-by-regime study.
All data is synthetic, in the library's file format.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import french
from macro_shock.research import sector_regimes as sr

# Same layout as 12_Industry_Portfolios_daily.CSV, CRLF line endings
FRENCH_SAMPLE = "\r\n".join([
    "This file was created by CMPT_IND_RETS_DAILY using the 202508 CRSP database.",
    "It contains value- and equal-weighted returns for 12 industry portfolios.",
    "",
    "  Average Value Weighted Returns -- Daily",
    ",NoDur,Enrgy,Utils",
    "19260701,   0.10,  -0.20,   0.30",
    "19260702,   0.50, -99.99,   1.00",
    "",
    "  Average Equal Weighted Returns -- Daily",
    ",NoDur,Enrgy,Utils",
    "19260701,   9.00,   9.00,   9.00",
    "",
    " Annual Returns:",
    ",NoDur,Enrgy,Utils",
    "1927,  10.00,  20.00,  30.00",
    "",
    "Copyright 2025 Kenneth R. French",
    "",
])


class TestFrenchReader:

    def test_parses_every_table_with_titles_dates_and_missing_values(self):
        tables = french.parse_tables(FRENCH_SAMPLE)
        assert [t for t, _ in tables] == [
            "Average Value Weighted Returns -- Daily",
            "Average Equal Weighted Returns -- Daily",
            "Annual Returns:",
        ]
        vw = tables[0][1]
        assert list(vw.columns) == ["NoDur", "Enrgy", "Utils"]
        assert vw.index[0] == pd.Timestamp("1926-07-01")
        assert vw.loc["1926-07-02", "Utils"] == pytest.approx(1.0)
        assert np.isnan(vw.loc["1926-07-02", "Enrgy"])
        assert tables[2][1].index[0] == pd.Timestamp("1927-01-01")

    def test_daily_returns_uses_first_table_as_decimals(self):
        df = french.daily_returns(french.INDUSTRIES_12, text=FRENCH_SAMPLE)
        assert df.loc["1926-07-01", "NoDur"] == pytest.approx(0.001)
        assert len(df) == 2

    def test_market_return_adds_risk_free_rate(self):
        factors = pd.DataFrame({"Mkt-RF": [0.01, -0.02], "RF": [0.0001, 0.0001]})
        assert list(french.market_return(factors)) == pytest.approx([0.0101, -0.0199])

    def test_file_without_tables_raises(self):
        with pytest.raises(french.FrenchDataError, match="No data tables"):
            french.parse_tables("Just some notes.\r\n\r\nCopyright\r\n")

    def test_download_reads_csv_inside_zip(self, monkeypatch):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("12_Industry_Portfolios_Daily.CSV", FRENCH_SAMPLE.encode("cp1252"))

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        seen = {}
        def fake_urlopen(url, timeout):
            seen["url"] = url
            return Resp(buf.getvalue())
        monkeypatch.setattr(french.urllib.request, "urlopen", fake_urlopen)
        text = french.download(french.INDUSTRIES_12)
        assert seen["url"].endswith("/ftp/12_Industry_Portfolios_daily_CSV.zip")
        assert "Average Value Weighted Returns" in text

    def test_download_failure_raises_clear_error(self, monkeypatch):
        def boom(url, timeout):
            raise OSError("network unreachable")
        monkeypatch.setattr(french.urllib.request, "urlopen", boom)
        with pytest.raises(french.FrenchDataError, match="network unreachable"):
            french.download(french.FACTORS_3)

    def test_every_12_industry_column_has_a_sector_label(self):
        assert set(french.INDUSTRY_SECTORS) == {
            "NoDur", "Durbl", "Manuf", "Enrgy", "Chems", "BusEq",
            "Telcm", "Utils", "Shops", "Hlth", "Money", "Other",
        }


def make_market(days: int = 400, regime_block: int = 40):
    """
    Daily data where 'Defensive' beats the market by 0.1%/day in 'stress' and
    lags it by 0.05%/day in 'calm'; 'Tracker' always equals the market.
    """
    idx = pd.bdate_range("2020-01-01", periods=days)
    rng = np.random.default_rng(0)
    market = pd.Series(rng.normal(0.0004, 0.01, days), index=idx)
    labels = np.where((np.arange(days) // regime_block) % 2 == 0, "calm", "stress")
    regimes = pd.Series(labels, index=idx)
    edge = np.where(labels == "stress", 0.001, -0.0005)
    assets = pd.DataFrame({"Defensive": market + edge, "Tracker": market}, index=idx)
    return assets, market, regimes


class TestRegimePerformance:

    def test_forward_returns_start_the_day_after(self):
        daily = pd.DataFrame({"A": [0.10, 0.10, -0.50, 0.20]}, index=pd.bdate_range("2024-01-01", periods=4))
        fwd = sr.forward_returns(daily, horizon=2)
        assert fwd["A"].iloc[0] == pytest.approx((1.10 * 0.50 - 1) * 100)
        assert np.isnan(fwd["A"].iloc[-1])

    def test_window_starts_do_not_overlap(self):
        idx = pd.bdate_range("2024-01-01", periods=10)
        assert list(sr.window_starts(idx, 4)) == [idx[0], idx[4], idx[8]]

    def test_excess_returns_by_regime(self):
        assets, market, regimes = make_market()
        # 20-day windows inside 40-day regime blocks: every window has one regime
        table = sr.regime_performance(assets, regimes, market, horizon=20)
        get = lambda r, a: table[(table["regime"] == r) & (table["asset"] == a)].iloc[0]
        stress, calm = get("stress", "Defensive"), get("calm", "Defensive")
        assert stress["mean_pct"] > 1.5 and stress["hit_rate"] == 1.0 and stress["ci_low"] > 0
        assert calm["mean_pct"] < -0.5 and calm["hit_rate"] == 0.0 and calm["ci_high"] < 0
        assert get("stress", "Tracker")["mean_pct"] == pytest.approx(0.0, abs=1e-9)
        assert stress["n"] + calm["n"] == 400 // 20 - 1      # last window has no forward data

    def test_raw_returns_without_market(self):
        assets, _, regimes = make_market()
        table = sr.regime_performance(assets[["Tracker"]], regimes, horizon=20)
        assert table["mean_pct"].abs().max() > 0          # not netted against anything

    def test_small_samples_get_no_interval(self):
        assets, market, regimes = make_market(days=120)
        table = sr.regime_performance(assets, regimes, market, horizon=20, min_windows=8)
        assert table["n"].max() < 8
        assert table["ci_low"].isna().all()

    def test_unlabelled_dates_are_skipped(self):
        assets, market, regimes = make_market()
        regimes[regimes == "calm"] = None
        table = sr.regime_performance(assets, regimes, market, horizon=20)
        assert set(table["regime"]) == {"stress"}

    def test_suggestions_rank_and_flag_significance(self):
        assets, market, regimes = make_market()
        table = sr.regime_performance(assets, regimes, market, horizon=20)
        s = sr.suggestions(table, "stress", top=1)
        assert s["best"].iloc[0]["asset"] == "Defensive" and s["best"].iloc[0]["significant"]
        assert s["worst"].iloc[0]["asset"] == "Tracker" and not s["worst"].iloc[0]["significant"]


class TestSplitRobustness:

    @staticmethod
    def _windows():
        """60 windows, all in one regime; the split falls between window 30 and 31."""
        rng = np.random.default_rng(1)
        idx = pd.bdate_range("2000-01-03", periods=60, freq="21B")
        half1, half2 = np.arange(60) < 30, np.arange(60) >= 30
        alternating = np.tile([1.1, -0.9], 15)                      # mean +0.1, wide interval
        fwd = pd.DataFrame({
            "Robust": 1.0 + rng.normal(0, 0.2, 60),
            "Consistent": np.where(half1, np.resize(alternating, 60), 1.0 + rng.normal(0, 0.1, 60)),
            "Unstable": np.where(half1, 2.0 + rng.normal(0, 0.1, 60), -0.3 + rng.normal(0, 0.05, 60)),
            "Nothing": np.tile([1.0, -1.0], 30),
        }, index=idx)
        labels = pd.Series("calm", index=idx)
        return fwd, labels, idx[29]

    def test_grades(self):
        fwd, labels, split = self._windows()
        table = sr.split_robustness(fwd, labels, split).set_index("asset")
        assert table.loc["Robust", "evidence"] == "robust"
        assert table.loc["Consistent", "evidence"] == "consistent"
        assert table.loc["Unstable", "evidence"] == "unstable"
        assert table.loc["Nothing", "evidence"] == ""
        assert table.loc["Robust", "n_h1"] == 30 and table.loc["Robust", "n_h2"] == 30

    def test_half_with_too_few_windows_gets_no_grade(self):
        fwd, labels, _ = self._windows()
        table = sr.split_robustness(fwd, labels, fwd.index[3]).set_index("asset")   # 4 vs 56 windows
        assert (table["evidence"] == "").all()

    def test_regime_performance_matches_window_summary(self):
        assets, market, regimes = make_market()
        direct = sr.regime_performance(assets, regimes, market, horizon=20)
        fwd, labels = sr.window_returns(assets, regimes, market, horizon=20)
        pd.testing.assert_frame_equal(direct, sr.summarize(fwd, labels))
