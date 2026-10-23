"""
tests/unit/test_optimizer.py

Optimizer math against closed-form solutions, the allocation universe built
from synthetic French/FRED data, and the optimize command end to end.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.portfolio_impact import optimizer as opt
from macro_shock.portfolio_impact.portfolio_stress import load_portfolio
from macro_shock.research import allocation, french

from .test_sector_study import FakeFred, fred_data, french_texts


def random_cov(n: int, seed: int = 0) -> np.ndarray:
    a = np.random.default_rng(seed).normal(size=(n, n))
    return a @ a.T / n + np.eye(n) * 0.01


class TestOptimizerMath:

    def test_min_variance_two_assets_matches_closed_form(self):
        s1, s2, rho = 0.20, 0.10, 0.3
        cov = np.array([[s1 * s1, rho * s1 * s2], [rho * s1 * s2, s2 * s2]])
        w1 = (s2 * s2 - rho * s1 * s2) / (s1 * s1 + s2 * s2 - 2 * rho * s1 * s2)
        assert opt.min_variance(cov) == pytest.approx([w1, 1 - w1], abs=1e-6)

    def test_min_variance_respects_cap_and_beats_equal_weight(self):
        cov = random_cov(6)
        w = opt.min_variance(cov, max_weight=0.25)
        assert w.sum() == pytest.approx(1.0) and w.min() >= -1e-12 and w.max() <= 0.25 + 1e-9
        eq = np.full(6, 1 / 6)
        assert w @ cov @ w <= eq @ cov @ eq

    def test_infeasible_cap_raises(self):
        with pytest.raises(ValueError, match="infeasible"):
            opt.min_variance(random_cov(4), max_weight=0.2)

    def test_risk_parity_uncorrelated_is_inverse_volatility(self):
        cov = np.diag([0.04, 0.01, 0.0025])
        inv = 1 / np.sqrt(np.diag(cov))
        assert opt.risk_parity(cov) == pytest.approx(inv / inv.sum(), abs=1e-9)

    def test_risk_parity_equalizes_risk_contributions(self):
        cov = random_cov(7, seed=3)
        w = opt.risk_parity(cov)
        assert w.min() > 0 and w.sum() == pytest.approx(1.0)
        assert opt.risk_contributions(w, cov) == pytest.approx(np.full(7, 1 / 7), abs=1e-8)

    def test_apply_tilt_scales_and_renormalizes(self):
        w = pd.Series({"A": 0.5, "B": 0.25, "C": 0.25})
        tilted = opt.apply_tilt(w, {"A": -0.5, "B": 0.5})
        raw = pd.Series({"A": 0.25, "B": 0.375, "C": 0.25})
        pd.testing.assert_series_equal(tilted, raw / raw.sum())
        with pytest.raises(ValueError):
            opt.apply_tilt(w, {"A": -1.5})

    def test_portfolio_stats(self):
        idx = pd.bdate_range("2024-01-01", periods=4)
        r = pd.DataFrame({"A": [0.10, -0.50, 0.10, 0.0], "B": [0.0, 0.0, 0.0, 0.0]}, index=idx)
        s = opt.portfolio_stats(r, pd.Series({"A": 0.5, "B": 0.5}))
        assert s["max_drawdown"] == pytest.approx(1.05 * 0.75 / 1.05 - 1)
        assert s["volatility"] == pytest.approx((r["A"] * 0.5).std() * np.sqrt(252))


def synthetic_inputs(include_btc_series: bool = True):
    days = pd.bdate_range("2015-01-02", "2024-05-31")
    texts = french_texts(days)
    industries = french.daily_returns(french.INDUSTRIES_12, text=texts[french.INDUSTRIES_12])
    market = french.market_return(french.daily_returns(french.FACTORS_3, text=texts[french.FACTORS_3]))
    data = fred_data()
    if include_btc_series:
        all_days = pd.date_range("2014-01-01", "2024-06-30")
        rng = np.random.default_rng(5)
        data["CBBTCUSD"] = pd.Series(1000 * np.exp(np.cumsum(rng.normal(0, 0.03, len(all_days)))), index=all_days)
    return texts, industries, market, FakeFred(data)


class TestAllocation:

    def test_bond_returns_are_carry_minus_duration_times_change(self):
        y = pd.Series([4.00, 4.10, 4.10], index=pd.bdate_range("2024-01-01", periods=3))
        r = allocation.bond_returns(y, duration=8.0)
        assert r.iloc[0] == pytest.approx(0.04 / 252 - 8 * 0.001)
        assert r.iloc[1] == pytest.approx(0.041 / 252)

    def test_universe_has_sectors_bonds_and_bitcoin(self):
        _, industries, market, fred = synthetic_inputs()
        returns, assets = allocation.build_universe(fred, industries, market, date(2017, 1, 1), date(2024, 5, 31))
        assert "Other" not in returns.columns
        assert set(returns.columns) >= {"BusEq", "Utils", "UST10", "UST2", "BTC"}
        assert len(returns.columns) == 14 and not returns.isna().any().any()
        assert assets["UST10"].kind == "bond" and assets["UST2"].kind == "short_bond"
        assert assets["BTC"].kind == "bitcoin"
        # Sectors equal the market except Utilities, so their beta is ~1
        assert assets["BusEq"].sensitivity == pytest.approx(1.0, abs=0.01)

    def test_universe_without_bitcoin(self):
        _, industries, market, fred = synthetic_inputs()
        returns, assets = allocation.build_universe(fred, industries, market, date(2017, 1, 1),
                                                    date(2024, 5, 31), include_btc=False)
        assert "BTC" not in returns.columns and "BTC" not in assets

    def test_short_window_is_rejected(self):
        _, industries, market, fred = synthetic_inputs()
        with pytest.raises(ValueError, match="need at least one year"):
            allocation.build_universe(fred, industries, market, date(2024, 1, 1), date(2024, 5, 31))

    def test_regime_tilts_follow_evidence(self):
        sectors = pd.DataFrame([
            {"regime": "fragile_risk_on", "asset": "Telcm", "mean_pct": -0.6, "evidence": "consistent"},
            {"regime": "fragile_risk_on", "asset": "BusEq", "mean_pct": 0.9, "evidence": "robust"},
            {"regime": "fragile_risk_on", "asset": "Enrgy", "mean_pct": 0.4, "evidence": "unstable"},
            {"regime": "risk_off_correction", "asset": "Utils", "mean_pct": -1.3, "evidence": "consistent"},
        ])
        assert allocation.regime_tilts(sectors, "fragile_risk_on", 0.25) == {"Telcm": -0.25, "BusEq": 0.5}
        assert allocation.regime_tilts(sectors, "fragile_risk_on", 0.0) == {}
        assert allocation.regime_tilts(sectors, None, 0.25) == {}

    def test_optimize_and_write_portfolio_csv(self, tmp_path):
        _, industries, market, fred = synthetic_inputs()
        returns, assets = allocation.build_universe(fred, industries, market, date(2017, 1, 1), date(2024, 5, 31))
        alloc = allocation.optimize(returns, assets, "risk_parity", tilts={"Utils": -0.25})
        assert alloc.base.sum() == pytest.approx(1.0) and alloc.tilted.sum() == pytest.approx(1.0)
        assert alloc.tilted["Utils"] / alloc.base["Utils"] < alloc.tilted["BusEq"] / alloc.base["BusEq"]
        assert alloc.risk_share.sum() == pytest.approx(1.0)
        path = tmp_path / "opt.csv"
        allocation.to_portfolio_csv(alloc, 50000, path)
        df = load_portfolio(path)
        assert df["value"].sum() == pytest.approx(50000, abs=1)
        assert set(df["type"]) == {"equity", "bond", "short_bond", "bitcoin"}

    def test_min_variance_objective_caps_weights(self):
        _, industries, market, fred = synthetic_inputs()
        returns, assets = allocation.build_universe(fred, industries, market, date(2017, 1, 1), date(2024, 5, 31))
        alloc = allocation.optimize(returns, assets, "min_variance", max_weight=0.30)
        assert alloc.base.max() <= 0.30 + 1e-9
        with pytest.raises(ValueError, match="unknown objective"):
            allocation.optimize(returns, assets, "max_sharpe")


class TestStockBondMix:

    def _universe(self, btc=True):
        _, industries, market, fred = synthetic_inputs()
        return allocation.build_universe(fred, industries, market, date(2017, 1, 1), date(2024, 5, 31),
                                         include_btc=btc)

    def test_sleeves_are_risk_parity_within_each_class(self):
        returns, assets = self._universe()
        s = allocation.sleeves(returns, assets)
        assert set(s) == {"equity", "bonds", "bitcoin"}
        assert len(s["equity"]) == 11 and set(s["bonds"].index) == {"UST10", "UST2"}
        for w in s.values():
            assert w.sum() == pytest.approx(1.0)
        cov = (returns[s["bonds"].index].cov() * 252).to_numpy()
        assert opt.risk_contributions(s["bonds"].to_numpy(), cov) == pytest.approx([0.5, 0.5], abs=1e-8)

    def test_mix_weights_match_the_requested_split(self):
        returns, assets = self._universe()
        w = allocation.mix_weights(returns, assets, stocks=0.7, btc=0.03)
        cls = allocation.class_summary(w, w, assets)["weight"]
        assert cls["equity"] == pytest.approx(0.97 * 0.7)
        assert cls["bonds"] == pytest.approx(0.97 * 0.3)
        assert cls["bitcoin"] == pytest.approx(0.03)
        all_stocks = allocation.class_summary(*(2 * [allocation.mix_weights(returns, assets, 1.0, 0.0)]), assets)
        assert all_stocks.loc["equity", "weight"] == pytest.approx(1.0)
        with pytest.raises(ValueError):
            allocation.mix_weights(returns, assets, 1.2, 0.0)

    def test_bitcoin_weight_ignored_without_bitcoin(self):
        returns, assets = self._universe(btc=False)
        w = allocation.mix_weights(returns, assets, stocks=0.6, btc=0.05)
        cls = allocation.class_summary(w, w, assets)["weight"]
        assert cls["equity"] == pytest.approx(0.6) and "bitcoin" not in cls

    def test_frontier_and_choice(self):
        returns, assets = self._universe()
        frontier = allocation.drawdown_frontier(returns, assets, btc=0.03, stock_shares=(1.0, 0.8, 0.6, 0.4))
        assert list(frontier["stocks"]) == [1.0, 0.8, 0.6, 0.4]
        assert frontier["weight_equity"].is_monotonic_decreasing
        assert frontier["volatility"].is_monotonic_decreasing
        dds = frontier.set_index("stocks")["max_drawdown"]
        limit = -(dds[0.8] + dds[0.6]) / 2
        pick, ok = allocation.choose_for_drawdown(frontier, limit)
        assert ok and pick["stocks"] == 0.6
        pick, ok = allocation.choose_for_drawdown(frontier, 0.0001)
        assert not ok and pick["stocks"] == 0.4

    def test_drawdown_on_drops_missing_assets(self):
        idx = pd.bdate_range("2024-01-01", periods=4)
        r = pd.DataFrame({"A": [0.10, -0.50, 0.10, 0.0]}, index=idx)
        dd, trough = allocation.drawdown_on(r, pd.Series({"A": 0.5, "BTC": 0.5}))
        assert dd == pytest.approx(-0.5) and trough == idx[1].date()

    def test_choice_respects_the_long_history_too(self):
        frontier = pd.DataFrame({
            "stocks": [1.0, 0.8, 0.6, 0.4],
            "max_drawdown": [-0.30, -0.22, -0.18, -0.12],
            "max_drawdown_long": [-0.50, -0.38, -0.27, -0.19],
        })
        pick, ok = allocation.choose_for_drawdown(frontier, 0.25)
        assert ok and pick["stocks"] == 0.4                 # 0.8 passes recently, fails since 1990
        recent = frontier.drop(columns="max_drawdown_long")
        assert allocation.choose_for_drawdown(recent, 0.25)[0]["stocks"] == 0.8

    def test_frontier_adds_long_history_drawdown(self):
        _, industries, market, fred = synthetic_inputs()
        returns, assets = self._universe()
        history = allocation.long_returns(fred, industries, market, start=date(2015, 1, 1))
        frontier = allocation.drawdown_frontier(returns, assets, 0.03, (1.0, 0.5), long_returns=history)
        assert "max_drawdown_long" in frontier
        assert (frontier["max_drawdown_long"] <= 0).all()
        assert frontier.loc[0, "max_drawdown_long"] < frontier.loc[1, "max_drawdown_long"]

    def test_mix_objective(self):
        returns, assets = self._universe()
        alloc = allocation.optimize(returns, assets, "mix", mix={"stocks": 0.8, "btc": 0.03})
        assert alloc.classes.loc["equity", "weight"] == pytest.approx(0.97 * 0.8)
        with pytest.raises(ValueError, match="needs a stock share"):
            allocation.optimize(returns, assets, "mix")

    def test_long_history_drawdown_leaves_bitcoin_out(self):
        _, industries, market, fred = synthetic_inputs()
        returns, assets = allocation.build_universe(fred, industries, market, date(2017, 1, 1), date(2024, 5, 31))
        weights = pd.Series(1 / len(returns.columns), index=returns.columns)
        check = allocation.long_history_drawdown(fred, industries, market, weights, assets, start=date(2015, 1, 1))
        assert check["start"] < date(2015, 2, 1) and check["end"] == date(2024, 5, 31)
        assert -1 < check["max_drawdown"] < 0
        assert date(2015, 1, 1) <= check["trough"] <= date(2024, 5, 31)


class TestOptimizeCommand:

    def test_end_to_end(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        texts, _, _, fred = synthetic_inputs()
        monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fred)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["msre", "optimize", "--years", "5", "--value", "20000",
                                          "--output", "opt.csv"])
        cli.main()
        out = capsys.readouterr().out
        assert "PORTFOLIO OPTIMIZER — risk parity" in out
        assert "Current regime: risk_on_expansion" in out
        assert "Utilities" in out and "Risk share" in out and "Max drawdown" in out
        assert load_portfolio(tmp_path / "opt.csv")["value"].sum() == pytest.approx(20000, abs=1)

    def _patch(self, monkeypatch, tmp_path):
        from macro_shock.data import feed_provider
        texts, _, _, fred = synthetic_inputs()
        monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fred)
        monkeypatch.chdir(tmp_path)

    def test_max_drawdown_picks_from_the_frontier(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["msre", "optimize", "--years", "5", "--tilt", "0",
                                          "--max-drawdown", "0.25", "--output", "opt.csv"])
        cli.main()
        out = capsys.readouterr().out
        assert "stock/bond mix of risk-parity sleeves" in out
        assert "Stocks vs drawdown (limit -25%, in both the recent window and since 1990)" in out
        assert "DD 1990+" in out
        assert out.count("<- chosen") == 1
        assert "Risk share" in out
        assert "Longer history check" in out and "max drawdown" in out
        assert load_portfolio(tmp_path / "opt.csv")["value"].sum() == pytest.approx(100000, abs=1)

    def test_recent_only_skips_the_long_history_column(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["msre", "optimize", "--years", "5", "--tilt", "0",
                                          "--max-drawdown", "0.25", "--recent-only"])
        cli.main()
        out = capsys.readouterr().out
        assert "in the recent window only" in out and "DD 1990+" not in out

    def test_fixed_stock_share(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["msre", "optimize", "--tilt", "0", "--stocks", "0.7",
                                          "--btc-weight", "0", "--years", "5", "--output", "opt.csv"])
        cli.main()
        df = load_portfolio(tmp_path / "opt.csv")
        assert df.loc[df["type"] == "equity", "value"].sum() == pytest.approx(70000, abs=5)
        assert "bitcoin" not in set(df["type"]) or df.loc[df["type"] == "bitcoin", "value"].sum() == 0

    def test_invalid_stock_share_is_rejected(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["msre", "optimize", "--tilt", "0", "--stocks", "1.5"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert "--stocks must be between 0 and 1" in capsys.readouterr().out

    def test_tilt_off_skips_the_sector_study(self, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        from macro_shock.research import sector_study
        texts, _, _, fred = synthetic_inputs()
        monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fred)
        monkeypatch.setattr(sector_study, "run", lambda *a, **k: pytest.fail("sector study should not run"))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["msre", "optimize", "--tilt", "0", "--no-bitcoin",
                                          "--objective", "min_variance", "--years", "5"])
        cli.main()
        out = capsys.readouterr().out
        assert "minimum variance" in out and "Regime tilt: off" in out
        assert not any(line.strip().startswith("Bitcoin") for line in out.splitlines())
