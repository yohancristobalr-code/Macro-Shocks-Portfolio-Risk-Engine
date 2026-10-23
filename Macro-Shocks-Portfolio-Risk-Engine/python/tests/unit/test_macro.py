"""
tests/unit/test_macro.py

The macro layer: the scenario markets are pricing (cross-asset moves scored
against four scenarios), the growth and inflation signals with publication
lags, the quadrant, leading indicators, the sectors-by-quadrant study, and
the `macro` and `daily` commands, on synthetic data with known answers.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import french, growth, macro, macro_scenario as ms, sector_study

from .test_sector_study import french_csv

TODAY = pd.Timestamp(date.today())


# ── Market scenario ──────────────────────────────────────────────────────────

def market(moves=None, end=TODAY, seed=0):
    """Three years of noisy levels for every market series, then ``moves`` over the last 20 trading days.

    ``moves`` maps a key to the total change over the window: bps for rates and spreads,
    a fraction for prices, points for the VIX.
    """
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=end, periods=900)
    base = {"y2": 4.0, "y10": 4.5, "real10": 2.0, "breakeven10": 2.4, "dollar": 120.0, "oil": 75.0,
            "equities": 5000.0, "nasdaq": 16000.0, "credit": 3.2, "vix": 16.0}
    out = {}
    ramp = np.clip(np.arange(len(days)) - (len(days) - 21), 0, 20) / 20
    for key, level in base.items():
        unit = ms.MARKET[key][2]
        if unit == "bps":
            s = level + np.cumsum(rng.normal(0, 0.03, len(days)))
            s = s + ramp * (moves or {}).get(key, 0.0) / 100
        elif unit == "%":
            s = level * np.exp(np.cumsum(rng.normal(0, 0.008, len(days))))
            s = s * (1 + ramp * (moves or {}).get(key, 0.0))
        else:
            s = level + np.cumsum(rng.normal(0, 0.3, len(days))) * 0.2
            s = s + ramp * (moves or {}).get(key, 0.0)
        out[ms.MARKET[key][0]] = pd.Series(s, index=days)
    return out


def by_key(fred_like):
    return {k: fred_like[sid] for k, (sid, _, _) in ms.MARKET.items() if sid in fred_like}


SUPPLY_SHOCK = {"oil": 0.25, "breakeven10": 40.0, "y10": 45.0, "y2": 5.0, "real10": 5.0,
                "nasdaq": -0.08, "equities": -0.05, "credit": 60.0}
GROWTH_SCARE = {"y10": -50.0, "real10": -40.0, "breakeven10": -10.0, "y2": -60.0, "equities": -0.10,
                "nasdaq": -0.12, "credit": 120.0, "vix": 12.0, "dollar": 0.04, "oil": -0.10}


class TestMarketScenario:

    def test_supply_shock_is_confirmed(self):
        result = ms.assess(by_key(market(SUPPLY_SHOCK)))
        assert result.best == "supply_shock" and result.status("supply_shock") == "CONFIRMED"
        assert result.status("disinflation") == "invalidated"
        match, against = result.conditions("supply_shock")
        assert any(m.startswith("WTI crude up") for m in match)

    def test_growth_scare_is_confirmed(self):
        result = ms.assess(by_key(market(GROWTH_SCARE)))
        assert result.best == "growth_scare" and result.status("growth_scare") == "CONFIRMED"
        assert result.status("reacceleration") == "invalidated"

    def test_quiet_market_confirms_nothing(self):
        result = ms.assess(by_key(market({})))
        assert all(result.status(k) != "CONFIRMED" for k in result.scores.index)

    def test_ten_year_split_into_real_and_breakeven(self):
        result = ms.assess(by_key(market(SUPPLY_SHOCK)))
        d = result.decomposition
        assert d["breakeven10"] > d["real10"] + 15          # +40 vs +5 planted, plus noise
        assert "inflation compensation (inflation fears)" in ms.decomposition_line(result)

    def test_stale_and_missing_series_are_left_out(self):
        data = by_key(market(SUPPLY_SHOCK))
        data["dollar"] = data["dollar"][data["dollar"].index < TODAY - pd.Timedelta(days=60)]
        data["vix"] = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        result = ms.assess(data)
        assert "dollar" not in result.moves.index and "vix" not in result.moves.index
        assert result.best == "supply_shock"

    def test_report_lines(self):
        lines = ms.report_lines(ms.assess(by_key(market(SUPPLY_SHOCK))))
        text = "\n".join(lines)
        assert "Supply shock / persistent inflation" in text and "CONFIRMED" in text
        assert "confirms:" in text and "2s10s curve" in text


# ── Growth and inflation signals ─────────────────────────────────────────────

class TestSignals:

    def test_falling_claims_mean_growth_up(self):
        weeks = pd.date_range("2020-01-04", periods=60, freq="W-SAT")
        assert growth.claims_signal(pd.Series(np.linspace(300e3, 200e3, 60), weeks)).iloc[-1] > 0
        assert growth.claims_signal(pd.Series(np.linspace(200e3, 300e3, 60), weeks)).iloc[-1] < 0

    def test_pace_signal_detects_acceleration(self):
        months = pd.date_range("2018-01-01", periods=40, freq="MS")
        rates = np.linspace(0.001, 0.006, 40)                 # monthly growth rising
        level = pd.Series(100 * np.cumprod(1 + rates), months)
        assert growth.pace_signal(level).iloc[-1] > 0
        assert growth.pace_signal(pd.Series(100 * np.cumprod(1 + rates[::-1]), months)).iloc[-1] < 0

    def test_scaled_has_no_look_ahead(self):
        s = pd.Series(np.random.default_rng(0).normal(size=100), pd.date_range("2000-01-01", periods=100, freq="MS"))
        a = growth.scaled(s)
        b = growth.scaled(pd.concat([s.iloc[:60], s.iloc[60:] * 50]))
        pd.testing.assert_series_equal(a.iloc[:60], b.iloc[:60])
        assert a.iloc[:growth.MIN_HISTORY].isna().all()

    def test_one_huge_outlier_does_not_mute_later_signals(self):
        idx = pd.date_range("2000-01-01", periods=120, freq="MS")
        x = pd.Series(np.where(np.arange(120) % 2, 1.0, -1.0), idx)
        x.iloc[80] = -500.0                                   # a 2020-style collapse
        z = growth.scaled(x)
        assert z.iloc[80] == -growth.CAP
        assert abs(z.iloc[100]) == pytest.approx(1.0)        # later moves keep their size

    def test_missing_month_does_not_blank_the_year_after(self):
        months = pd.date_range("2023-01-01", "2026-09-01", freq="MS")
        level = pd.Series(100 * 1.003 ** np.arange(len(months)), months)
        level = level.drop(pd.Timestamp("2025-10-01"))       # never published
        pace = growth.pace_signal(level)
        assert pace[pd.Timestamp("2026-09-01")] == pytest.approx(0.0, abs=1e-6)
        assert pace.loc["2025-10-01":].notna().all()
        jobs = growth.payrolls_signal(level * 1000)
        assert jobs.loc["2025-10-01":].notna().all()

    def _sig(self, values):
        return growth.Signals(values=values, raw={k: v for k, v in values.items()})

    def test_publication_lag(self):
        idx = pd.DatetimeIndex([pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-30")])
        sig = self._sig({"cpi": pd.Series([0.5, -0.7], idx)})
        assert growth.value_at(sig, "cpi", pd.Timestamp("2026-10-10"), lagged=True) == 0.5
        assert growth.value_at(sig, "cpi", pd.Timestamp("2026-10-18"), lagged=True) == -0.7
        assert growth.value_at(sig, "cpi", pd.Timestamp("2026-10-10"), lagged=False) == -0.7

    def test_stale_value_is_ignored(self):
        sig = self._sig({"cpi": pd.Series([0.5], pd.DatetimeIndex([pd.Timestamp("2025-01-31")]))})
        assert growth.value_at(sig, "cpi", pd.Timestamp("2026-10-10"), lagged=False) is None

    @pytest.mark.parametrize("g,i,quadrant", [(1, 1, "reacceleration"), (1, -1, "disinflation"),
                                              (-1, 1, "supply_shock"), (-1, -1, "growth_scare")])
    def test_quadrants(self, g, i, quadrant):
        when = pd.Timestamp("2026-10-20")
        monthly = pd.DatetimeIndex([pd.Timestamp("2026-09-30")])
        values = {k: pd.Series([float(g)], monthly) for k in ("indpro", "payrolls")}
        values["claims"] = pd.Series([float(g)], pd.DatetimeIndex([pd.Timestamp("2026-10-10")]))
        values.update({k: pd.Series([float(i)], monthly) for k in ("cpi", "core")})
        ax = growth.axes_at(self._sig(values), when, lagged=False)
        assert ax.quadrant == quadrant

    def test_growth_needs_two_signals(self):
        monthly = pd.DatetimeIndex([pd.Timestamp("2026-09-30")])
        values = {"indpro": pd.Series([1.0], monthly), "payrolls": pd.Series(dtype=float, index=pd.DatetimeIndex([])),
                  "claims": pd.Series(dtype=float, index=pd.DatetimeIndex([])),
                  "cpi": pd.Series([1.0], monthly), "core": pd.Series([1.0], monthly)}
        ax = growth.axes_at(self._sig(values), pd.Timestamp("2026-10-20"), lagged=False)
        assert ax.growth is None and ax.quadrant is None


# ── Synthetic macro history with known quadrants ─────────────────────────────

PHASES = [("1967-01-01", "1975-12-31", +1, +1), ("1976-01-01", "1983-12-31", -1, +1),
          ("1984-01-01", "1993-12-31", +1, -1), ("1994-01-01", "2003-12-31", -1, -1),
          ("2004-01-01", "2014-12-31", +1, +1), ("2015-01-01", "2030-12-31", -1, +1)]


def phase(d):
    for a, b, g, i in PHASES:
        if pd.Timestamp(a) <= d <= pd.Timestamp(b):
            return g, i
    return 1, 1


def macro_series(end=TODAY):
    """Monthly and weekly levels whose growth and inflation accelerate or slow by phase (see PHASES)."""
    months = pd.date_range("1960-01-01", end, freq="MS")
    # monthly growth rates drift up in "+" phases and down in "-" phases (never saturating),
    # so 3-month paces stay above / below 12-month ones throughout each phase
    g_rate, i_rate, gl, il, pl, payrolls_gain = 0.002, 0.003, [100.0], [30.0], [60000.0], [100.0]
    for m in months[1:]:
        g, i = phase(m)
        g_rate += 0.00005 * g
        i_rate += 0.00005 * i
        gl.append(gl[-1] * (1 + g_rate))
        il.append(il[-1] * (1 + i_rate))
        payrolls_gain.append(payrolls_gain[-1] + 2.0 * g)
        pl.append(pl[-1] + payrolls_gain[-1])
    weeks = pd.date_range("1966-01-01", end, freq="W-SAT")
    claims = [350e3]
    for w in weeks[1:]:
        g, _ = phase(w)
        claims.append(claims[-1] * (1 - 0.0005 * g))
    cpi = pd.Series(il, months)
    data = {"ICSA": pd.Series(claims, weeks), "INDPRO": pd.Series(gl, months), "PAYEMS": pd.Series(pl, months),
            "CPIAUCSL": cpi, "CPILFESL": cpi * 1.01}
    # last CPI = previous month, as on a real mid-month date
    for k in ("INDPRO", "PAYEMS", "CPIAUCSL", "CPILFESL"):
        data[k] = data[k][data[k].index < growth._monthly(data[k]).index[-1]]
    w = pd.date_range(end=end, periods=200, freq="W-SAT")
    data.update({"CCSA": pd.Series(1.9e6, w), "WEI": pd.Series(np.linspace(2.5, 1.5, 200), w),
                 "GDPNOW": pd.Series([1.8], pd.DatetimeIndex([pd.Timestamp(end).to_period("Q").to_timestamp()])),
                 "SAHMREALTIME": pd.Series([0.2, 0.3], months[-3:-1])})
    surveys = {"philly_activity": (5.0, -3.0), "philly_orders": (4.0, 2.0), "philly_prices": (30.0, 38.0),
               "empire_activity": (-2.0, 1.0), "empire_orders": (0.0, 3.0), "empire_prices": (25.0, 31.0)}
    for key, values in surveys.items():
        data[growth.SERIES[key][0]] = pd.Series(values, months[-3:-1])
    return data


def growth_by_key(data):
    return {k: data.get(sid, pd.Series(dtype=float, index=pd.DatetimeIndex([]))) for k, (sid, _) in growth.SERIES.items()}


@pytest.fixture(scope="module")
def sig():
    return growth.signals(growth_by_key(macro_series()))


class TestQuadrantHistory:

    @pytest.mark.parametrize("when,expected", [("1972-06-15", "reacceleration"), ("1980-06-16", "supply_shock"),
                                               ("1990-06-15", "disinflation"), ("2000-06-15", "growth_scare"),
                                               ("2010-06-15", "reacceleration"), ("2020-06-15", "supply_shock")])
    def test_history_follows_the_phases(self, sig, when, expected):
        assert growth.quadrant_history(sig, [pd.Timestamp(when)]).iloc[0] == expected

    def test_live_quadrant(self, sig):
        assert growth.axes_at(sig, TODAY, lagged=False).quadrant == "supply_shock"

    def test_nowcast_shifts_the_inflation_axis(self, sig):
        series = growth_by_key(macro_series())
        month = growth._monthly(series["cpi"]).index[-1] + pd.offsets.MonthBegin(1)
        # the synthetic trend is ~1.5%/month by now: 3% is far above it, -1% far below
        hot = growth.inflation_with_nowcast(series, sig, month, 3.0, 3.0)
        cold = growth.inflation_with_nowcast(series, sig, month, -1.0, -1.0)
        assert hot > 0 > cold


class TestLeading:

    def test_lines_and_pmi_hint(self):
        lead = growth.leading(growth_by_key(macro_series()))
        text = "\n".join(lead.lines)
        assert "Initial jobless claims" in text and "Weekly Economic Index" in text and "GDPNow" in text
        assert "below the 0.5 recession line" in text and "Philadelphia Fed: general activity" in text
        # activity: Philly 5 -> -3, NY -2 -> 1: average change -2.5; prices paid up in both
        hint = "\n".join(growth.pmi_hint(lead))
        assert "deteriorating (-2.5 pts" in hint and "prices paid rising" in hint

    def test_sahm_recession_signal(self):
        data = growth_by_key(macro_series())
        data["sahm"] = pd.Series([0.62], pd.DatetimeIndex([pd.Timestamp("2026-09-01")]))
        assert "RECESSION SIGNAL" in "\n".join(growth.leading(data).lines)

    def test_missing_optional_series(self):
        data = {k: v for k, v in growth_by_key(macro_series()).items() if k in growth.REQUIRED}
        lead = growth.leading(data)
        assert lead.sahm is None and growth.pmi_hint(lead) == []


# ── Sectors by quadrant ──────────────────────────────────────────────────────

def french_by_quadrant(days):
    """Energy beats the market by 0.15%/day in supply-shock phases and lags by 0.03%/day otherwise."""
    rng = np.random.default_rng(1)
    n = len(days)
    mkt = rng.normal(0.03, 1.0, n)
    shock = np.array([phase(d) == (-1, +1) for d in days])
    edge = np.where(shock, 0.15, -0.03)
    cols = list(french.INDUSTRY_SECTORS)
    ind = np.column_stack([mkt + (edge if c == "Enrgy" else 0.0) for c in cols])
    factors = np.column_stack([mkt - 0.01, rng.normal(0, 0.5, n), rng.normal(0, 0.5, n), np.full(n, 0.01)])
    return {french.INDUSTRIES_12: french_csv(cols, days, ind),
            french.FACTORS_3: french_csv(["Mkt-RF", "SMB", "HML", "RF"], days, factors),
            french.MOMENTUM: french_csv(["Mom"], days, rng.normal(0, 0.6, (n, 1)))}


@pytest.fixture(scope="module")
def french_texts():
    return french_by_quadrant(pd.bdate_range("1970-01-02", TODAY))


@pytest.fixture
def patched_french(monkeypatch, french_texts):
    monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: french_texts[dataset])


class TestQuadrantStudy:

    def test_planted_energy_effect(self, patched_french):
        sig = growth.signals(growth_by_key(macro_series()))
        study = macro.quadrant_study(sig, "supply_shock")
        energy = study.sectors[study.sectors["asset"] == "Enrgy"].set_index("regime")
        assert energy.loc["supply_shock", "ci_low"] > 0 and energy.loc["supply_shock", "evidence"] in ("robust", "consistent")
        assert energy.loc["disinflation", "mean_pct"] < 0
        assert set(study.regime_counts.index) == {"reacceleration", "disinflation", "supply_shock", "growth_scare"}
        lines = sector_study.supported_lines(study, "supply_shock")
        assert any("favoured Energy (XLE)" in l for l in lines)


# ── Commands ─────────────────────────────────────────────────────────────────

class FakeFred:
    def __init__(self, data):
        self.data = data

    def get_series(self, sid, observation_start=None, observation_end=None, **kw):
        s = self.data[sid]
        return s[(s.index >= pd.Timestamp(observation_start)) & (s.index <= pd.Timestamp(observation_end))]


@pytest.fixture(scope="module")
def all_fred():
    data = {**macro_series(), **market(SUPPLY_SHOCK)}
    bdays = pd.bdate_range(end=TODAY, periods=300)
    flat = lambda v: pd.Series(v, index=bdays)
    data.update({"DGS5": flat(4.3), "DGS30": flat(4.8), "BAMLC0A0CM": flat(0.9),
                 "CBBTCUSD": pd.Series(65000.0, index=pd.date_range(end=TODAY, periods=420))})
    return data


class TestCommands:

    def _cli(self, monkeypatch, data, *argv):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        fake = FakeFred(data)
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
        monkeypatch.setattr(feed_provider.FREDFeedProvider, "_get_fred", lambda self: fake)
        monkeypatch.setattr(sys, "argv", ["msre", *argv])
        cli.main()

    def test_macro_command(self, monkeypatch, capsys, all_fred, patched_french):
        self._cli(monkeypatch, all_fred, "macro")
        out = capsys.readouterr().out
        for part in ("1. MARKETS", "2. GROWTH", "3. INFLATION", "4. MACRO QUADRANT", "5. SECTORS AND STYLES"):
            assert part in out
        assert "Supply shock / persistent inflation" in out and "CONFIRMED" in out
        assert "Quadrant: Stagflation / supply shock" in out and "AGREE on supply_shock" in out
        assert "<- current" in out and "favoured Energy (XLE)" in out
        assert "CPI nowcast unavailable today." in out          # no energy price series in this fake

    def test_macro_quick_skips_sectors(self, monkeypatch, capsys, all_fred):
        self._cli(monkeypatch, all_fred, "macro", "--quick")
        out = capsys.readouterr().out
        assert "4. MACRO QUADRANT" in out and "5. SECTORS" not in out

    def test_macro_without_cpi_exits(self, monkeypatch, capsys, all_fred):
        data = {k: v for k, v in all_fred.items() if k != "CPIAUCSL"}
        with pytest.raises(SystemExit):
            self._cli(monkeypatch, data, "macro", "--quick")
        assert "CPIAUCSL is unavailable" in capsys.readouterr().out

    def test_daily_macro_line_and_quadrant_alert(self, monkeypatch, capsys, tmp_path, all_fred):
        from macro_shock.research import daily, sector_study as ss
        monkeypatch.setattr(ss, "run", lambda *a, **k: object())
        monkeypatch.setattr(ss, "supported_lines", lambda study, regime: ["head"])
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        prior = {c: None for c in daily.LOG_COLUMNS}
        prior.update({"run_date": yesterday, "data_through": yesterday, "regime": "fragile_risk_on",
                      "vulnerability": 0.4, "quadrant": "disinflation"})
        daily.write_log(daily.read_log(tmp_path / "daily_log.csv"), prior, tmp_path / "daily_log.csv")
        self._cli(monkeypatch, all_fred, "daily", "--reports-dir", str(tmp_path))
        out = capsys.readouterr().out
        assert "Macro: markets price supply_shock" in out and "data quadrant supply_shock" in out
        assert "MACRO QUADRANT CHANGE: disinflation -> supply_shock" in out
        log = daily.read_log(tmp_path / "daily_log.csv")
        assert log.iloc[-1]["scenario"] == "supply_shock" and log.iloc[-1]["quadrant"] == "supply_shock"

    def test_daily_quick_has_market_scenario_only(self, monkeypatch, capsys, tmp_path, all_fred):
        self._cli(monkeypatch, all_fred, "daily", "--quick", "--reports-dir", str(tmp_path))
        out = capsys.readouterr().out
        assert "Macro: markets price supply_shock" in out and "data quadrant" not in out
