"""
tests/unit/test_feed_provider.py

Unit tests for the FRED market data feed provider.
FRED is replaced by an in-memory fake; no network access is needed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.data.feed_provider import DataFeedError, FREDFeedProvider
from macro_shock.event_detection.calendar import MarketCalendar

# Tuesday 2024-03-12, 15:00 ET (during the session) and 17:00 ET (after the close)
DURING_SESSION = datetime(2024, 3, 12, 19, 0, tzinfo=timezone.utc)
AFTER_CLOSE = datetime(2024, 3, 12, 21, 0, tzinfo=timezone.utc)


def _series(points: dict) -> pd.Series:
    return pd.Series(list(points.values()), index=pd.to_datetime(list(points.keys())))


def _three_days(fri: float, mon: float, tue: float) -> pd.Series:
    return _series({"2024-03-08": fri, "2024-03-11": mon, "2024-03-12": tue})


def default_data() -> dict:
    return {
        "DGS2": _three_days(4.50, 4.60, 4.70),
        "DGS5": _three_days(4.10, 4.20, 4.30),
        "DGS10": _three_days(4.05, 4.15, 4.25),
        "DGS30": _three_days(4.20, 4.30, 4.40),
        "DFII10": _three_days(1.80, 1.85, 1.90),
        "T10YIE": _three_days(2.25, 2.30, 2.35),
        "BAMLH0A0HYM2": _three_days(3.00, 3.10, 3.20),   # percent
        "BAMLC0A0CM": _three_days(0.90, 0.95, 1.00),
        "VIXCLS": _three_days(14.0, 14.5, 15.0),
        "SP500": _series({
            "2023-01-03": 6000.0,   # outside the 52-week window
            "2023-06-01": 5200.0,   # 52-week high
            "2024-03-08": 4950.0,
            "2024-03-11": 4940.0,
            "2024-03-12": 5000.0,
        }),
    }


class FakeFred:
    def __init__(self, data: dict):
        self.data = data
        self.calls = []

    def get_series(self, series_id, observation_start=None, observation_end=None, **kwargs):
        self.calls.append((series_id, observation_start, observation_end))
        if series_id not in self.data:
            raise ValueError(f"Bad Request. The series does not exist: {series_id}")
        s = self.data[series_id]
        return s[(s.index >= pd.Timestamp(observation_start)) & (s.index <= pd.Timestamp(observation_end))]


class FailingFred:
    def get_series(self, series_id, **kwargs):
        raise ValueError("Bad Request. The value for variable api_key is not registered.")


def make_provider(data: dict | None = None) -> tuple[FREDFeedProvider, FakeFred]:
    provider = FREDFeedProvider(MarketCalendar(), api_key="test-key")
    fake = FakeFred(data if data is not None else default_data())
    provider._fred = fake
    return provider, fake


class TestFREDFeedProvider:

    def test_maps_every_field_the_scorer_reads(self):
        provider, _ = make_provider()
        snap = provider.get_snapshot(as_of=DURING_SESSION)

        assert snap.yields.y10 == pytest.approx(4.15)
        assert snap.yields.slope_2_10 == pytest.approx(-45.0)       # (4.15 - 4.60) * 100
        assert snap.yields.real_yield_10 == pytest.approx(1.85)
        assert snap.credit.hy_spread_bps == pytest.approx(310.0)    # 3.10% -> bps
        assert snap.credit.ig_spread_bps == pytest.approx(95.0)
        assert snap.volatility.vix_spot == pytest.approx(14.5)
        assert snap.equity.spx_level == pytest.approx(4940.0)
        assert snap.equity.spx_from_52w_high == pytest.approx(4940 / 5200 - 1)
        assert snap.equity.spx_1d_return == pytest.approx(4940 / 4950 - 1)
        assert snap.data_completeness == pytest.approx(0.5)         # 4 of 8 groups
        assert not snap.has_critical_data_gap

    def test_reports_date_of_latest_observation(self):
        provider, _ = make_provider()
        # Saturday 2024-03-09: the last close is Friday 2024-03-08
        snap = provider.get_snapshot(as_of=datetime(2024, 3, 9, 15, 0, tzinfo=timezone.utc))
        assert "through 2024-03-08" in snap.gap_description

    def test_event_during_session_uses_previous_close(self):
        provider, fake = make_provider()
        snap = provider.get_snapshot(as_of=DURING_SESSION)
        # The 2024-03-12 close already contains the reaction to the event
        assert snap.volatility.vix_spot == pytest.approx(14.5)
        assert {end for _, _, end in fake.calls} == {"2024-03-11"}

    def test_event_after_close_uses_same_day_close(self):
        provider, _ = make_provider()
        snap = provider.get_snapshot(as_of=AFTER_CLOSE)
        assert snap.volatility.vix_spot == pytest.approx(15.0)
        assert snap.equity.spx_level == pytest.approx(5000.0)

    def test_missing_observations_are_skipped(self):
        data = default_data()
        data["VIXCLS"] = _three_days(14.0, float("nan"), 15.0)   # FRED holiday gap
        provider, _ = make_provider(data)
        snap = provider.get_snapshot(as_of=DURING_SESSION)
        assert snap.volatility.vix_spot == pytest.approx(14.0)

    def test_stale_series_is_treated_as_missing(self):
        data = default_data()
        data["VIXCLS"] = _series({"2024-01-02": 13.0})
        provider, _ = make_provider(data)
        snap = provider.get_snapshot(as_of=DURING_SESSION)
        assert snap.volatility is None
        assert snap.yields is not None
        assert snap.data_completeness == pytest.approx(3 / 8)

    def test_discontinued_ted_spread_is_not_requested(self):
        provider, fake = make_provider()
        provider.get_snapshot(as_of=DURING_SESSION)
        assert "TEDRATE" not in {sid for sid, _, _ in fake.calls}

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        provider = FREDFeedProvider(MarketCalendar())
        with pytest.raises(DataFeedError, match="FRED_API_KEY"):
            provider.get_snapshot(as_of=DURING_SESSION)

    def test_no_usable_data_raises_with_fred_error(self):
        provider = FREDFeedProvider(MarketCalendar(), api_key="bad-key")
        provider._fred = FailingFred()
        with pytest.raises(DataFeedError, match="api_key is not registered"):
            provider.get_snapshot(as_of=DURING_SESSION)

    def test_snapshot_runs_through_full_pipeline(self):
        from macro_shock.orchestration.pipeline import MacroShockPipeline
        provider, _ = make_provider()
        pipeline = MacroShockPipeline(config={"use_transformer": False}, environment="research")
        raw_event = {
            "title": "FOMC Press Conference",
            "institution": "Federal Reserve",
            "event_time": "2024-03-12T15:00:00-04:00",
            "prepared_remarks": "Inflation remains too high. We are prepared to raise rates further.",
        }
        ctx = pipeline.process_raw_event(raw_event, market_state=provider.get_snapshot(as_of=DURING_SESSION))
        assert ctx.failed_stages == []
        assert ctx.risk_score is not None


class TestAnalyzeCommandWithFRED:

    def _run(self, monkeypatch, *extra):
        from macro_shock import __main__ as cli
        monkeypatch.setattr(sys, "argv", [
            "msre", "analyze", "--title", "FOMC", "--institution", "Federal Reserve",
            "--event-time", "2024-03-12T15:00:00-04:00",
            "--text", "Inflation remains too high.", "--data", "fred", *extra,
        ])
        cli.main()

    def test_without_api_key_exits_with_clear_error(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)                       # no .env to pick up
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        with pytest.raises(SystemExit) as exc:
            self._run(monkeypatch)
        assert exc.value.code == 1
        assert "FRED_API_KEY is not set" in capsys.readouterr().out

    def test_reports_fred_market_data(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FRED_API_KEY", "test-key")
        monkeypatch.setattr(FREDFeedProvider, "_get_fred", lambda self: FakeFred(default_data()))
        self._run(monkeypatch)
        out = capsys.readouterr().out
        assert "Market (FRED, end-of-day)" in out
        assert "VIX: 14.5" in out
        assert "COMPOSITE RISK SCORE" in out
