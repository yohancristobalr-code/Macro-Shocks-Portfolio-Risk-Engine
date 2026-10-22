"""
tests/unit/test_portfolio_stress.py

Unit tests for stress-testing a user's portfolio against the scenario tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.data_schema.models import RegimeType, ScenarioOutcome, ScenarioTree
from macro_shock.portfolio_impact.portfolio_stress import (
    POSITION_TYPES, PortfolioFileError, load_portfolio, stress_portfolio,
)

EXAMPLE = Path(__file__).parents[3] / "examples" / "portfolio_example.csv"


def scenario(name, prob, **moves) -> ScenarioOutcome:
    return ScenarioOutcome(name=name, description=name, probability=prob, **moves)


def two_scenario_tree() -> ScenarioTree:
    return ScenarioTree(
        event_id="t", regime=RegimeType.FRAGILE_RISK_ON,
        scenarios=[
            scenario("Calm", 0.9, equity_impact_pct=1.0, yield_10y_change_bps=-10.0,
                     yield_2y_change_bps=-5.0, credit_hy_change_bps=-20.0, gold_change_pct=0.5,
                     oil_change_pct=1.0, btc_change_pct=2.0, dxy_change_pct=-0.5),
            scenario("Crash", 0.1, equity_impact_pct=-10.0, yield_10y_change_bps=20.0,
                     yield_2y_change_bps=30.0, credit_hy_change_bps=150.0, gold_change_pct=3.0,
                     oil_change_pct=-6.0, btc_change_pct=-12.0, dxy_change_pct=2.0),
        ],
    )


def write(tmp_path, text: str) -> Path:
    p = tmp_path / "portfolio.csv"
    p.write_text(text)
    return p


class TestLoadPortfolio:

    def test_example_file_is_valid(self):
        df = load_portfolio(EXAMPLE)
        assert len(df) == 8
        assert df["value"].sum() == pytest.approx(100000)
        assert set(df["type"]) <= set(POSITION_TYPES)

    def test_empty_sensitivity_uses_type_default(self, tmp_path):
        df = load_portfolio(write(tmp_path, "name,type,value,sensitivity\nTLT,bond,1000,\nHYG,high_yield,500,3\n"))
        assert list(df["sensitivity"]) == [7.0, 3.0]

    def test_sensitivity_column_is_optional_and_case_is_ignored(self, tmp_path):
        df = load_portfolio(write(tmp_path, "Name, Type, Value\nSPY, Equity, 1000\n"))
        assert df.loc[0, "type"] == "equity" and df.loc[0, "sensitivity"] == 1.0

    @pytest.mark.parametrize("text,message", [
        ("name,type\nSPY,equity\n", "missing column(s): value"),
        ("name,type,value\nSPY,stocks,100\n", "Unknown position type(s): stocks"),
        ("name,type,value\nSPY,equity,lots\n", "non-numeric value for: SPY"),
        ("name,type,value\n", "has no positions"),
    ])
    def test_invalid_files_raise_clear_errors(self, tmp_path, text, message):
        with pytest.raises(PortfolioFileError, match=message.replace("(", r"\(").replace(")", r"\)")):
            load_portfolio(write(tmp_path, text))

    def test_missing_file(self, tmp_path):
        with pytest.raises(PortfolioFileError, match="not found"):
            load_portfolio(tmp_path / "nope.csv")


class TestStressPortfolio:

    @pytest.mark.parametrize("ptype,sens,crash_pct", [
        ("equity", 1.2, -12.0),        # beta 1.2 x -10%
        ("bond", 8.0, -1.6),           # -8 years x +20bps
        ("short_bond", 2.0, -0.6),     # -2 years x +30bps
        ("high_yield", 4.0, -6.0),     # -4 years x +150bps
        ("gold", 1.0, 3.0),
        ("oil", 1.0, -6.0),
        ("bitcoin", 1.3, -15.6),
        ("usd", 1.0, 2.0),
        ("cash", 0.0, 0.0),
    ])
    def test_each_type_maps_to_its_market_move(self, tmp_path, ptype, sens, crash_pct):
        df = load_portfolio(write(tmp_path, f"name,type,value,sensitivity\nX,{ptype},1000,{sens}\n"))
        st = stress_portfolio(df, two_scenario_tree())
        assert st.position_pnl.loc["X", "Crash"] == pytest.approx(1000 * crash_pct / 100)

    def test_aggregates_probability_weighted_and_tail(self, tmp_path):
        df = load_portfolio(write(tmp_path, "name,type,value\nStocks,equity,6000\nBTC,bitcoin,2000\nCash,cash,2000\n"))
        st = stress_portfolio(df, two_scenario_tree())
        calm, crash = 6000 * 0.01 + 2000 * 0.02, 6000 * -0.10 + 2000 * -0.12
        assert st.total_value == pytest.approx(10000)
        assert st.expected_pnl == pytest.approx(0.9 * calm + 0.1 * crash)
        assert st.expected_pnl_pct == pytest.approx((0.9 * calm + 0.1 * crash) / 100)
        assert st.pnl_5pct == pytest.approx(crash)          # 10% crash covers the 5th percentile
        assert st.worst_scenario == "Crash"
        assert list(st.worst_contributors(2).index) == ["Stocks", "BTC"]

    def test_short_position_gains_in_a_crash(self, tmp_path):
        df = load_portfolio(write(tmp_path, "name,type,value\nShort SPY,equity,-1000\n"))
        assert stress_portfolio(df, two_scenario_tree()).position_pnl.loc["Short SPY", "Crash"] == pytest.approx(100)


class TestAnalyzeWithPortfolio:

    def _run(self, monkeypatch, *extra):
        from macro_shock import __main__ as cli
        monkeypatch.setattr(sys, "argv", [
            "msre", "analyze", "--title", "FOMC", "--institution", "Federal Reserve",
            "--event-time", "2024-03-12T14:00:00-04:00",
            "--text", "Persistent inflation is a concern. Inflation too high.", *extra,
        ])
        cli.main()

    def test_prints_portfolio_section(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        self._run(monkeypatch, "--portfolio", str(EXAMPLE))
        out = capsys.readouterr().out
        assert "YOUR PORTFOLIO  (8 positions, value 100,000)" in out
        assert "Worst 5% case:" in out and "Largest losses in" in out

    def test_bad_portfolio_exits_before_running(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        bad = write(tmp_path, "name,type,value\nSPY,stocks,100\n")
        with pytest.raises(SystemExit) as exc:
            self._run(monkeypatch, "--portfolio", str(bad))
        assert exc.value.code == 1
        assert "Unknown position type(s): stocks" in capsys.readouterr().out
