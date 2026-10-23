"""
tests/unit/test_daily.py

The daily routine: alert rules, the CSV log, and the `daily` command with FRED
replaced by synthetic series that end today.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import daily

TODAY = date.today()


def row(run_date, regime="fragile_risk_on", vix=16.0, hy=320.0, spx=-1.0):
    return {"run_date": run_date.isoformat(), "data_through": run_date.isoformat(), "regime": regime,
            "vulnerability": 0.4, "vix": vix, "hy_bps": hy, "ig_bps": 90.0, "slope_2_10_bps": 30.0,
            "y10": 4.5, "spx_vs_high_pct": spx, "btc": 60000.0}


class TestAlerts:

    def test_first_run_has_no_comparison_alerts(self):
        assert daily.alerts(row(TODAY), None) == []

    def test_regime_change_comes_first(self):
        out = daily.alerts(row(TODAY, regime="risk_off_correction", vix=24.0),
                           row(TODAY - timedelta(days=1), vix=16.0))
        assert out[0].startswith("REGIME CHANGE: fragile_risk_on -> risk_off_correction")
        assert any(a.startswith("VIX up +8.0 pts") for a in out)

    def test_hy_widening_and_correction(self):
        out = daily.alerts(row(TODAY, hy=400.0, spx=-12.0), row(TODAY - timedelta(days=3), hy=330.0))
        assert any(a.startswith("HY spread up +70.0 bps") for a in out)
        assert any("12.0% below its 52-week high" in a for a in out)

    def test_small_moves_do_not_alert(self):
        assert daily.alerts(row(TODAY, vix=19.0, hy=360.0), row(TODAY - timedelta(days=1))) == []


class TestLog:

    def test_previous_row_skips_same_day_and_takes_latest(self, tmp_path):
        path = tmp_path / "log.csv"
        log = daily.read_log(path)
        for d in (TODAY - timedelta(days=3), TODAY - timedelta(days=1), TODAY):
            log = daily.write_log(log, row(d), path)
        prev = daily.previous_row(daily.read_log(path), TODAY)
        assert prev["run_date"] == (TODAY - timedelta(days=1)).isoformat()

    def test_same_day_run_replaces_its_row(self, tmp_path):
        path = tmp_path / "log.csv"
        log = daily.write_log(daily.read_log(path), row(TODAY, vix=15.0), path)
        log = daily.write_log(log, row(TODAY, vix=17.0), path)
        saved = daily.read_log(path)
        assert len(saved) == 1 and saved.loc[0, "vix"] == 17.0
        assert list(saved.columns) == daily.LOG_COLUMNS


def fred_today(vix=16.0):
    """FRED-like series ending today, calm market, every series the provider reads."""
    bdays = pd.bdate_range(end=TODAY, periods=300)
    days = pd.date_range(end=TODAY, periods=420)
    flat = lambda v, idx=bdays: pd.Series(v, index=idx)
    spx = pd.Series(np.linspace(5000, 5200, len(days)), index=days)
    return {
        "DGS2": flat(4.2), "DGS5": flat(4.3), "DGS10": flat(4.5), "DGS30": flat(4.8),
        "DFII10": flat(2.0), "T10YIE": flat(2.3), "BAMLH0A0HYM2": flat(3.1), "BAMLC0A0CM": flat(0.9),
        "VIXCLS": flat(vix), "SP500": spx, "CBBTCUSD": pd.Series(65000.0, index=days),
    }


class FakeFred:
    def __init__(self, data):
        self.data = data

    def get_series(self, sid, observation_start=None, observation_end=None, **kw):
        s = self.data[sid]
        return s[(s.index >= pd.Timestamp(observation_start)) & (s.index <= pd.Timestamp(observation_end))]


class TestDailyCommand:

    def _run(self, monkeypatch, tmp_path, *extra, data=None):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        fake = FakeFred(data or fred_today())
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
        monkeypatch.setattr(feed_provider.FREDFeedProvider, "_get_fred", lambda self: fake)
        monkeypatch.setattr(sys, "argv", ["msre", "daily", "--reports-dir", str(tmp_path), *extra])
        cli.main()

    def test_first_run_writes_report_and_log(self, monkeypatch, capsys, tmp_path):
        self._run(monkeypatch, tmp_path, "--quick")
        out = capsys.readouterr().out
        assert "DAILY MARKET REPORT" in out and "Regime:" in out and "ALERTS: none" in out
        assert "Bitcoin 65,000" in out
        assert (tmp_path / f"daily_{TODAY.isoformat()}.txt").exists()
        assert len(daily.read_log(tmp_path / "daily_log.csv")) == 1

    def test_liquidity_and_funding_components_shown_when_available(self, monkeypatch, capsys, tmp_path):
        data = fred_today()
        weeks = pd.date_range(end=TODAY, periods=1500, freq="W-FRI")
        data["STLFSI4"] = pd.Series(np.linspace(-1, 1, len(weeks)), index=weeks)
        data["NFCI"] = pd.Series(np.linspace(1, -1, len(weeks)), index=weeks)
        self._run(monkeypatch, tmp_path, "--quick", data=data)
        out = capsys.readouterr().out
        assert "liquidity 1.00" in out and "funding 0.00" in out

    def test_components_without_stress_indices(self, monkeypatch, capsys, tmp_path):
        self._run(monkeypatch, tmp_path, "--quick")
        out = capsys.readouterr().out
        assert "Components: volatility" in out and "liquidity" not in out

    def test_rerun_same_day_keeps_one_row(self, monkeypatch, capsys, tmp_path):
        self._run(monkeypatch, tmp_path, "--quick")
        self._run(monkeypatch, tmp_path, "--quick")
        assert len(daily.read_log(tmp_path / "daily_log.csv")) == 1

    def test_regime_change_against_previous_run(self, monkeypatch, capsys, tmp_path):
        prior = row(TODAY - timedelta(days=1), regime="risk_on_expansion", vix=10.0)
        daily.write_log(daily.read_log(tmp_path / "daily_log.csv"), prior, tmp_path / "daily_log.csv")
        self._run(monkeypatch, tmp_path, "--quick", data=fred_today(vix=16.0))
        out = capsys.readouterr().out
        # VIX 16, HY 310bps, IG 90bps, 2s10s +30bps -> mid-range vulnerability
        assert daily.read_log(tmp_path / "daily_log.csv").iloc[-1]["regime"] == "fragile_risk_on"
        assert "REGIME CHANGE: risk_on_expansion -> fragile_risk_on" in out
        assert "Regime changed - checklist:" in out
        assert "VIX up +6.0 pts" in out

    def test_sector_evidence_is_included(self, monkeypatch, capsys, tmp_path):
        from macro_shock.research import sector_study
        monkeypatch.setattr(sector_study, "run", lambda *a, **k: object())
        monkeypatch.setattr(sector_study, "supported_lines",
                            lambda study, regime: ["head", "    ** favoured Momentum   +0.96%"])
        self._run(monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert "What the evidence supports for this regime" in out and "favoured Momentum" in out

    def test_sector_study_failure_does_not_block_the_report(self, monkeypatch, capsys, tmp_path):
        from macro_shock.research import sector_study
        def boom(*a, **k):
            raise RuntimeError("Dartmouth unreachable")
        monkeypatch.setattr(sector_study, "run", boom)
        self._run(monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert "sector study unavailable today: Dartmouth unreachable" in out
        assert (tmp_path / f"daily_{TODAY.isoformat()}.txt").exists()

    def test_missing_key_exits(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "_PROJECT_ROOT", tmp_path)
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        monkeypatch.setattr(sys, "argv", ["msre", "daily", "--quick", "--reports-dir", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert "FRED_API_KEY is not set" in capsys.readouterr().out
