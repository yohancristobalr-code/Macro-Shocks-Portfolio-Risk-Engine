"""
tests/unit/test_sector_study.py

Regime-history reconstruction and the end-to-end sector study, with FRED and
the French Data Library replaced by synthetic data.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.market_context.vulnerability_scorer import CALIBRATION, MarketVulnerabilityScorer
from macro_shock.research import french, regime_history as rh, sector_study

CALM = dict(vix=13.0, y2=1.0, y10=2.5)          # low VIX, +150bps curve
STRESSED = dict(vix=40.0, y2=4.0, y10=3.5)      # high VIX, inverted curve


class TestCreditProxy:

    def test_quantile_mapping_lands_on_calibration_knots(self):
        baa = pd.Series(np.linspace(1.0, 6.0, 1001))
        knots = rh.proxy_knots(baa)
        for k, hy, ig in zip(knots, CALIBRATION["hy_spread_bps"], CALIBRATION["ig_spread_bps"]):
            assert rh.baa_to_spreads(k, knots) == pytest.approx({"hy_spread_bps": hy, "ig_spread_bps": ig})
        assert rh.baa_to_spreads(99.0, knots)["hy_spread_bps"] == CALIBRATION["hy_spread_bps"][-1]

    def test_median_proxy_gives_median_credit_vulnerability(self):
        baa = pd.Series(np.linspace(1.0, 6.0, 1001))
        knots = rh.proxy_knots(baa)
        spreads = rh.baa_to_spreads(float(np.median(baa)), knots)
        scorer = MarketVulnerabilityScorer()
        assert scorer._percentile_rank(spreads["hy_spread_bps"], CALIBRATION["hy_spread_bps"]) == pytest.approx(0.5)


class TestClassify:

    def test_calm_and_stressed_markets(self):
        scorer = MarketVulnerabilityScorer()
        when = pd.Timestamp("2024-03-12")
        assert rh.classify(scorer, when, **CALM, hy_bps=260, ig_bps=55) == "risk_on_expansion"
        assert rh.classify(scorer, when, **STRESSED, hy_bps=900, ig_bps=250) == "risk_off_correction"

    def test_crisis_needs_the_stress_index(self):
        scorer = MarketVulnerabilityScorer()
        when = pd.Timestamp("2008-10-10")
        panic = dict(vix=70.0, y2=1.5, y10=3.8, hy_bps=1500, ig_bps=500)
        assert rh.classify(scorer, when, **panic) == "risk_off_correction"          # no liquidity data
        assert rh.classify(scorer, when, **panic, stress_pct=0.99, conditions_pct=0.98) == "crisis"

    def test_stress_percentiles_raise_vulnerability(self):
        scorer = MarketVulnerabilityScorer()
        from macro_shock.data_schema.models import LiquiditySnapshot
        from datetime import datetime, timezone
        ts = datetime(2024, 3, 12, tzinfo=timezone.utc)
        calm = rh.classify(scorer, pd.Timestamp("2024-03-12"), **CALM, hy_bps=330, ig_bps=95,
                           stress_pct=0.05, conditions_pct=0.05)
        tense = rh.classify(scorer, pd.Timestamp("2024-03-12"), **CALM, hy_bps=330, ig_bps=95,
                            stress_pct=0.95, conditions_pct=0.95)
        order = ["risk_on_expansion", "fragile_risk_on", "risk_off_correction", "crisis"]
        assert order.index(tense) > order.index(calm)


class TestHistoryPercentiles:

    def test_rank_within_history(self):
        s = pd.Series([3.0, 1.0, 2.0, 2.0], index=pd.date_range("2024-01-05", periods=4, freq="W-FRI"))
        assert list(rh.history_percentiles(s)) == [1.0, 0.25, 0.75, 0.75]


def fred_data(start="2014-01-01", end="2024-06-28", stress_from="2020-02-20", stress_to="2020-06-30",
              ice_from="2021-01-01", ice_matches_proxy=False):
    """
    Daily FRED-like inputs: calm (VIX 13, positive curve) with one stressed spell
    (VIX 40, inverted curve, wide credit). BAA10Y wobbles in calm times so its
    percentiles are well spread. ICE spreads exist only from ``ice_from``: tight
    by default, or the exact quantile image of BAA10Y with ``ice_matches_proxy``.
    """
    days = pd.bdate_range(start, end)
    stressed = (days >= stress_from) & (days <= stress_to)
    vix = np.where(stressed, 40.0, 13.0)
    y2 = np.where(stressed, 4.0, 1.0)
    y10 = np.where(stressed, 3.5, 2.5)
    baa = np.where(stressed, 4.5, 1.8 + 0.3 * np.sin(np.arange(len(days)) / 10))
    ice = days >= ice_from
    if ice_matches_proxy:
        knots = rh.proxy_knots(pd.Series(baa))
        mapped = [rh.baa_to_spreads(b, knots) for b in baa[ice]]
        hy = np.array([m["hy_spread_bps"] for m in mapped]) / 100
        ig = np.array([m["ig_spread_bps"] for m in mapped]) / 100
    else:
        hy, ig = np.where(stressed[ice], 9.0, 2.7), np.where(stressed[ice], 2.5, 0.6)
    return {
        "VIXCLS": pd.Series(vix, index=days), "DGS2": pd.Series(y2, index=days),
        "DGS10": pd.Series(y10, index=days), "BAA10Y": pd.Series(baa, index=days),
        "BAMLH0A0HYM2": pd.Series(hy, index=days[ice]),
        "BAMLC0A0CM": pd.Series(ig, index=days[ice]),
    }


class FakeFred:
    def __init__(self, data):
        self.data = data

    def get_series(self, sid, observation_start=None, observation_end=None, **kw):
        s = self.data[sid]
        return s[(s.index >= pd.Timestamp(observation_start)) & (s.index <= pd.Timestamp(observation_end))]


class TestRegimeHistory:

    def _series(self, **kw):
        return rh.fetch_series(FakeFred(fred_data(**kw)), date(2014, 1, 1), date(2024, 6, 28))

    def test_labels_follow_the_stressed_spell(self):
        series = self._series(ice_from="2014-01-01")
        labels = rh.regime_history(series, pd.to_datetime(["2019-06-03", "2020-04-01", "2022-06-01"]), "ice")
        assert list(labels) == ["risk_on_expansion", "risk_off_correction", "risk_on_expansion"]

    def test_proxy_flags_the_stressed_spell(self):
        labels = rh.regime_history(self._series(), pd.bdate_range("2020-03-02", "2020-06-26"), "proxy")
        assert set(labels) == {"risk_off_correction"}

    def test_missing_or_stale_inputs_give_no_label(self):
        series = self._series()
        series["vix"] = series["vix"][series["vix"].index < "2019-01-01"]
        assert rh.regime_history(series, [pd.Timestamp("2019-06-03")]).iloc[0] is None
        assert rh.regime_history(series, [pd.Timestamp("2018-06-01")], "ice").iloc[0] is None   # no ICE yet

    def test_proxy_agreement_is_measured_where_ice_exists(self):
        check = rh.credit_proxy_agreement(self._series())
        assert check["since"] == date(2021, 1, 1)
        assert check["n"] > 100
        assert 0.0 <= check["agreement"] <= 1.0

    def test_stress_index_lets_history_reach_crisis(self):
        data = fred_data(ice_from="2014-01-01")
        weeks = pd.date_range("1994-01-07", "2024-06-28", freq="W-FRI")
        crisis = (weeks >= "2020-03-01") & (weeks <= "2020-05-31")
        calm_2019 = weeks.year == 2019
        noise = np.random.default_rng(7).normal(0, 0.2, len(weeks))
        # Ordinary weeks wobble around -0.5; 2019 is unusually calm; spring 2020 is extreme stress
        stress = np.where(crisis, 5.0, np.where(calm_2019, -1.5, -0.5 + noise))
        data["STLFSI4"] = pd.Series(stress, index=weeks)
        data["NFCI"] = pd.Series(stress / 5, index=weeks)
        # Make the spell a full-blown panic: VIX 70 and very wide credit
        for sid, v in (("VIXCLS", 70.0), ("BAMLH0A0HYM2", 15.0), ("BAMLC0A0CM", 5.0)):
            s = data[sid]
            data[sid] = s.where(~((s.index >= "2020-03-02") & (s.index <= "2020-05-29")), v)
        series = rh.fetch_series(FakeFred(data), date(2014, 1, 1), date(2024, 6, 28))
        labels = rh.regime_history(series, pd.to_datetime(["2019-06-03", "2020-04-01"]), "ice")
        assert list(labels) == ["risk_on_expansion", "crisis"]

    def test_missing_stress_indices_do_not_stop_the_history(self):
        series = self._series()            # fred_data() has no STLFSI4/NFCI
        assert series["stress"].empty and series["conditions"].empty
        assert rh.regime_history(series, [pd.Timestamp("2020-04-01")]).iloc[0] == "risk_off_correction"

    def test_proxy_agrees_fully_when_ice_is_its_quantile_image(self):
        check = rh.credit_proxy_agreement(self._series(ice_matches_proxy=True))
        assert check["agreement"] == pytest.approx(1.0)

    def test_agreement_matrix_cross_tabulates_real_vs_proxy(self):
        check = rh.credit_proxy_agreement(self._series())
        m = check["matrix"]
        assert m.index.name == "real" and m.columns.name == "proxy"
        assert int(m.to_numpy().sum()) == check["n"]
        diagonal = sum(m.loc[r, r] for r in m.index if r in m.columns)
        assert diagonal / check["n"] == pytest.approx(check["agreement"])


def french_csv(columns, days, values) -> str:
    rows = [",".join([d.strftime("%Y%m%d")] + [f"{v:.4f}" for v in row]) for d, row in zip(days, values)]
    return "\r\n".join(["Synthetic French file", "", "  Average Value Weighted Returns -- Daily",
                        "," + ",".join(columns)] + rows + ["", "Copyright"])


def french_texts(days, stress_from="2020-02-20", stress_to="2020-06-30"):
    """Utilities beat the market by 0.2%/day in stress and lag by 0.05%/day otherwise."""
    rng = np.random.default_rng(0)
    n = len(days)
    mkt = rng.normal(0.04, 1.0, n)                      # percent
    stressed = (days >= stress_from) & (days <= stress_to)
    edge = np.where(stressed, 0.2, -0.05)
    cols = list(french.INDUSTRY_SECTORS)
    ind = np.column_stack([mkt + (edge if c == "Utils" else 0.0) for c in cols])
    factors = np.column_stack([mkt - 0.01, rng.normal(0, 0.5, n), rng.normal(0, 0.5, n), np.full(n, 0.01)])
    return {
        french.INDUSTRIES_12: french_csv(cols, days, ind),
        french.FACTORS_3: french_csv(["Mkt-RF", "SMB", "HML", "RF"], days, factors),
        french.MOMENTUM: french_csv(["Mom"], days, rng.normal(0, 0.6, (n, 1))),
    }


@pytest.fixture
def patched_sources(monkeypatch):
    days = pd.bdate_range("2015-01-02", "2024-05-31")
    texts = french_texts(days)
    monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
    fake = FakeFred(fred_data())
    from macro_shock.data import feed_provider
    monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
    return fake


class TestSectorStudy:

    def test_run_finds_the_planted_effect(self, patched_sources):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        utils = study.sectors[(study.sectors["asset"] == "Utils")].set_index("regime")
        assert utils.loc["risk_off_correction", "mean_pct"] > 1.0
        assert utils.loc["risk_off_correction", "ci_low"] > 0
        assert utils.loc["risk_on_expansion", "mean_pct"] < 0
        assert set(study.styles["asset"]) == {"SMB", "HML", "Mom"}
        assert study.current_regime == "risk_on_expansion"
        assert study.current_date == date(2024, 6, 28)
        assert study.proxy_check["n"] > 100

    def test_regime_lines_show_best_sector_with_evidence_marker(self, patched_sources):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        lines = sector_study.regime_lines(study, "risk_on_expansion", top=1)
        assert lines[0].startswith("  risk_on_expansion  (n=")
        worst = [l for l in lines if "- Utilities (XLU)" in l][0]
        assert worst.lstrip().startswith("**")            # lags in calm times, in both halves
        assert "halves" in worst
        assert any("Value vs growth" in l for l in lines)

    def test_supported_lines_keep_only_evidence_backed_results(self, patched_sources):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        lines = sector_study.supported_lines(study, "risk_on_expansion")
        assert any("lagging  Utilities (XLU)" in l for l in lines)
        assert all("Technology" not in l for l in lines)      # tracks the market: no evidence
        # The stressed spell sits in one half only, so nothing there can be robust
        assert "the evidence supports no tilt" in sector_study.supported_lines(study, "risk_off_correction")[-1]

    def test_other_industry_is_left_out(self, patched_sources):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        assert "Other" not in set(study.sectors["asset"])
        assert len(set(study.sectors["asset"])) == 11

    def test_supported_lines_list_favoured_before_lagging(self, patched_sources, monkeypatch):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        rows = study.sectors[study.sectors["regime"] == "risk_on_expansion"].copy()
        fake = pd.DataFrame([
            {**rows.iloc[0].to_dict(), "asset": "Hlth", "mean_pct": -0.2, "evidence": "consistent"},
            {**rows.iloc[0].to_dict(), "asset": "BusEq", "mean_pct": 0.5, "evidence": "robust"},
            {**rows.iloc[0].to_dict(), "asset": "Utils", "mean_pct": -0.9, "evidence": "robust"},
            {**rows.iloc[0].to_dict(), "asset": "Enrgy", "mean_pct": 0.1, "evidence": "consistent"},
        ])
        study.sectors = fake
        study.styles = study.styles.iloc[0:0]
        body = sector_study.supported_lines(study, "risk_on_expansion")[1:]
        order = [next(n for n in ("Technology", "Energy", "Utilities", "Healthcare") if n in l) for l in body]
        assert order == ["Technology", "Energy", "Utilities", "Healthcare"]

    def test_split_date_is_mid_sample(self, patched_sources):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        assert date(2019, 6, 1) < study.split_date < date(2020, 1, 31)

    def test_end_date_limits_the_sample(self, patched_sources):
        full = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        half = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10, end=date(2019, 12, 31))
        assert half.last_date <= date(2019, 12, 31) < full.last_date
        assert half.regime_counts.sum() < full.regime_counts.sum()
        assert half.current_regime == full.current_regime        # today's regime is unaffected

    def test_regime_without_history_is_explained(self, patched_sources):
        study = sector_study.run(patched_sources, date(2015, 1, 1), horizon=10)
        assert "cannot identify it" in sector_study.regime_lines(study, "crisis")[0]


class TestSectorCommands:

    def test_sector_study_command(self, patched_sources, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        monkeypatch.chdir(tmp_path)
        out_csv = tmp_path / "sectors.csv"
        monkeypatch.setattr(sys, "argv", ["msre", "sector-study", "--start", "2015-01-01",
                                          "--horizon", "10", "--output", str(out_csv)])
        cli.main()
        out = capsys.readouterr().out
        assert "SECTORS AND STYLES BY MARKET REGIME" in out
        assert "Credit proxy check: BAA10Y-based regime matches the ICE-based one on" in out
        assert "real (rows) vs proxy (cols)" in out
        assert "Robustness: each result is re-estimated on the halves" in out
        assert "What the evidence supports for it:" in out
        assert "CURRENT REGIME (2024-06-28): risk_on_expansion" in out
        assert set(pd.read_csv(out_csv)["kind"]) == {"sector", "style"}

    def test_analyze_with_sectors(self, patched_sources, monkeypatch, capsys, tmp_path):
        from macro_shock import __main__ as cli
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "_run_sector_study",
                            lambda start="1990-01-01", horizon=21: sector_study.run(patched_sources, date(2015, 1, 1), 10))
        monkeypatch.setattr(sys, "argv", ["msre", "analyze", "--title", "FOMC", "--institution", "Federal Reserve",
                                          "--event-time", "2024-03-12T14:00:00-04:00", "--text", "Inflation too high.",
                                          "--sectors"])
        cli.main()
        out = capsys.readouterr().out
        assert "SECTORS AND STYLES IN THIS REGIME" in out
        assert "halves split at" in out
        assert "synthetic market data; use --data fred" in out
