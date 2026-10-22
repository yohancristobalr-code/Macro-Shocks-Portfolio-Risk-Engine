"""
tests/unit/test_fomc_study.py

Unit tests for the FOMC / Bitcoin event study. All market data is synthetic.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import fomc_study as fs


def _s(points: dict) -> pd.Series:
    return pd.Series(list(points.values()), index=pd.to_datetime(list(points.keys())))


# Wednesday 2022-06-15: +10bps 2Y surprise, +75bps hike effective the next day
HIKE_DAY = date(2022, 6, 15)


def hike_series() -> dict:
    return {
        "y2": _s({"2022-06-14": 3.40, "2022-06-15": 3.50, "2022-06-16": 3.20}),
        "fed_upper": _s({"2022-06-14": 1.00, "2022-06-15": 1.00, "2022-06-16": 1.75}),
        "btc": _s({"2022-06-14": 100.0, "2022-06-15": 105.0, "2022-06-16": 110.0}),
        "spx": _s({"2022-06-14": 4000.0, "2022-06-15": 4100.0, "2022-06-16": 3960.0}),
        "vix": _s({"2022-06-14": 30.0, "2022-06-15": 29.0, "2022-06-16": 32.0}),
    }


class TestAnnouncementDates:

    def test_list_is_sorted_unique_and_complete(self):
        dates = fs.announcement_dates()
        assert dates == sorted(set(dates))
        assert len(dates) == 73          # 8 per year, 2020: 7 scheduled + 2 unscheduled
        assert dates[0] == date(2017, 2, 1) and dates[-1] == date(2025, 12, 10)

    def test_non_wednesday_dates_are_the_known_exceptions(self):
        odd = {d for d in fs.announcement_dates() if d.weekday() != 2}
        assert odd == {
            date(2018, 11, 8), date(2020, 11, 5), date(2024, 11, 7),   # post-election Thursdays
            date(2020, 3, 3), date(2020, 3, 15),                       # unscheduled, COVID
        }

    def test_unscheduled_flag(self):
        events = fs.event_table(hike_series(), [HIKE_DAY])
        assert not events.loc[0, "unscheduled"]
        assert fs._is_unscheduled(date(2020, 3, 15))

    def test_range_filter(self):
        dates = fs.announcement_dates(date(2022, 1, 1), date(2022, 12, 31))
        assert len(dates) == 8 and all(d.year == 2022 for d in dates)


class TestEventTable:

    def test_reactions_use_close_before_to_next_day_close(self):
        row = fs.event_table(hike_series(), [HIKE_DAY]).iloc[0]
        assert row["surprise_bps"] == pytest.approx(10.0)     # 3.40 -> 3.50, announcement day only
        assert row["stance"] == "hawkish"
        assert row["rate_change_bps"] == pytest.approx(75.0)
        assert row["btc_pct"] == pytest.approx(10.0)          # 100 -> 110
        assert row["spx_pct"] == pytest.approx(-1.0)          # 4000 -> 3960
        assert row["vix_change"] == pytest.approx(2.0)        # 30 -> 32

    def test_sunday_emergency_action_uses_friday_to_monday(self):
        series = {
            "y2": _s({"2020-03-13": 0.50, "2020-03-16": 0.36}),
            "fed_upper": _s({"2020-03-14": 1.25, "2020-03-15": 1.25, "2020-03-16": 0.25}),
            "btc": _s({"2020-03-13": 5000.0, "2020-03-14": 5300.0, "2020-03-15": 5400.0,
                       "2020-03-16": 5000.0}),
            "spx": _s({"2020-03-13": 2700.0, "2020-03-16": 2400.0}),
            "vix": _s({"2020-03-13": 57.0, "2020-03-16": 82.0}),
        }
        row = fs.event_table(series, [date(2020, 3, 15)]).iloc[0]
        assert row["unscheduled"]
        assert row["surprise_bps"] == pytest.approx(-14.0)
        assert row["stance"] == "dovish"
        assert row["rate_change_bps"] == pytest.approx(-100.0)
        assert row["btc_pct"] == pytest.approx((5000 / 5300 - 1) * 100)   # Saturday -> Monday
        assert row["spx_pct"] == pytest.approx((2400 / 2700 - 1) * 100)

    def test_missing_data_gives_nan_not_error(self):
        series = hike_series()
        series["btc"] = _s({"2022-06-14": 100.0})            # nothing after the event
        row = fs.event_table(series, [HIKE_DAY]).iloc[0]
        assert np.isnan(row["btc_pct"])
        assert row["stance"] == "hawkish"

    @pytest.mark.parametrize("surprise,expected", [
        (5.0, "hawkish"), (4.9, "neutral"), (-5.0, "dovish"), (0.0, "neutral"), (np.nan, "n/a"),
    ])
    def test_classify(self, surprise, expected):
        assert fs.classify(surprise, 5.0) == expected


class TestSummaryAndBaseline:

    def _events(self) -> pd.DataFrame:
        return pd.DataFrame({
            "date": [date(2022, 1, d) for d in (5, 12, 19, 26)],
            "stance": ["hawkish", "hawkish", "dovish", "neutral"],
            "surprise_bps": [10.0, 6.0, -8.0, 1.0],
            "btc_pct": [-4.0, -2.0, 6.0, 1.0],
            "spx_pct": [-1.0, -0.5, 1.5, 0.2],
            "vix_change": [2.0, 1.0, -3.0, 0.0],
        })

    def test_summarize_by_stance(self):
        summary = fs.summarize(self._events())
        assert summary.loc["hawkish", "n"] == 2
        assert summary.loc["hawkish", "btc_mean_pct"] == pytest.approx(-3.0)
        assert summary.loc["hawkish", "btc_up_share"] == pytest.approx(0.0)
        assert summary.loc["dovish", "btc_mean_pct"] == pytest.approx(6.0)
        assert summary.loc["all", "n"] == 4
        assert summary.loc["all", "btc_up_share"] == pytest.approx(0.5)

    def test_baseline_compares_fomc_moves_with_typical_two_day_moves(self):
        events = self._events()
        days = pd.date_range("2022-01-01", "2022-01-31")
        btc = pd.Series(100.0 * 1.01 ** np.arange(len(days)), index=days)   # +1%/day
        base = fs.btc_baseline(btc, events)
        assert base["typical_abs_move_pct"] == pytest.approx(2.01)          # 1.01**2 - 1
        assert base["fomc_abs_move_pct"] == pytest.approx(3.25)             # mean(|-4|,|-2|,|6|,|1|)
        assert base["ratio"] == pytest.approx(3.25 / 2.01)
        assert base["corr_btc_vs_surprise"] < -0.9


class TestValidateDates:

    def test_change_the_day_after_an_announcement_is_explained(self):
        fed = _s({"2022-06-14": 1.00, "2022-06-15": 1.00, "2022-06-16": 1.75})
        assert fs.validate_dates(fed, [HIKE_DAY], date(2022, 6, 1), date(2022, 6, 30)) == []

    def test_change_without_announcement_is_reported(self):
        fed = _s({"2022-06-14": 1.00, "2022-06-16": 1.75, "2022-07-28": 2.50})
        assert fs.validate_dates(fed, [HIKE_DAY], date(2022, 6, 1), date(2022, 7, 31)) == [date(2022, 7, 28)]


class FakeFred:
    def __init__(self, data: dict):
        self.data, self.calls = data, []

    def get_series(self, series_id, observation_start=None, observation_end=None, **kwargs):
        self.calls.append((series_id, observation_start, observation_end))
        s = self.data[series_id]
        return s[(s.index >= pd.Timestamp(observation_start)) & (s.index <= pd.Timestamp(observation_end))]


def synthetic_fred(drop_hike: bool = False) -> FakeFred:
    """2022 market with the actual 2022 hike path effective the day after each hike meeting."""
    days = pd.date_range("2021-11-01", "2023-02-28")
    bdays = pd.bdate_range("2021-11-01", "2023-02-28")
    rng = np.random.default_rng(0)
    upper = pd.Series(0.25, index=days)
    for d, new in [("2022-03-16", 0.50), ("2022-05-04", 1.00), ("2022-06-15", 1.75),
                   ("2022-07-27", 2.50), ("2022-09-21", 3.25), ("2022-11-02", 4.00),
                   ("2022-12-14", 4.50)]:
        if drop_hike and d == "2022-07-27":
            d = "2022-08-10"            # simulate a misdated meeting
        upper[upper.index > pd.Timestamp(d)] = new
    btc = pd.Series(40000 * np.exp(np.cumsum(rng.normal(0, 0.03, len(days)))), index=days)
    btc.iloc[10] = np.nan                                       # FRED gap
    return FakeFred({
        "CBBTCUSD": btc,
        "SP500": pd.Series(4000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(bdays)))), index=bdays),
        "VIXCLS": pd.Series(25 + rng.normal(0, 1, len(bdays)), index=bdays),
        "DGS2": pd.Series(2 + np.cumsum(rng.normal(0, 0.05, len(bdays))), index=bdays),
        "DFEDTARU": upper,
    })


class TestFetchSeries:

    def test_downloads_every_series_padded_and_without_gaps(self):
        fake = synthetic_fred()
        series = fs.fetch_series(fake, date(2022, 1, 1), date(2022, 12, 31))
        assert set(series) == set(fs.SERIES)
        assert {c[0] for c in fake.calls} == set(fs.SERIES.values())
        assert all(c[1] == "2021-12-02" and c[2] == "2023-01-30" for c in fake.calls)
        assert not series["btc"].isna().any()


class TestFomcStudyCommand:

    def _run(self, monkeypatch, fake, *extra):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
        monkeypatch.setattr(sys, "argv", ["msre", "fomc-study", "--start", "2022-01-01",
                                          "--end", "2022-12-31", *extra])
        cli.main()

    def test_prints_study_and_writes_csv(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        out_csv = tmp_path / "events.csv"
        self._run(monkeypatch, synthetic_fred(), "--output", str(out_csv))
        out = capsys.readouterr().out
        assert "Announcements: 8" in out
        assert "Date check: every target-rate change" in out
        assert "hawkish" in out and "dovish" in out
        events = pd.read_csv(out_csv)
        assert len(events) == 8
        assert list(events["rate_change_bps"]) == [0, 25, 50, 75, 75, 75, 75, 50]

    def test_warns_when_dates_do_not_match_rate_changes(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        self._run(monkeypatch, synthetic_fred(drop_hike=True))
        out = capsys.readouterr().out
        assert "DATE CHECK FAILED" in out
        assert "2022-08-11" in out

    def test_missing_api_key_exits_with_clear_error(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        monkeypatch.setattr(sys, "argv", ["msre", "fomc-study"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert "FRED_API_KEY is not set" in capsys.readouterr().out
