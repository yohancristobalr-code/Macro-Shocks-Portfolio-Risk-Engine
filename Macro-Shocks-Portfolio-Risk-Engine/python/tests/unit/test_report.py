"""
tests/unit/test_report.py

Historical analogs, the predictive model's out-of-sample test, the SVG
charts, and the `report` command end to end on synthetic FRED and French
data.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import analogs as an, charts, french

from .test_cpi_nowcast import as_fred, synthetic
from .test_crypto import FakeAPI, synthetic_prices
from .test_sectors_metals import factors as sector_factors, fake_stooq, market_prices
from .test_macro import SUPPLY_SHOCK, TODAY, FakeFred, french_by_quadrant, macro_series, market


def panel(n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("1995-01", periods=n, freq="M")
    return pd.DataFrame(rng.normal(size=(n, len(an.FEATURES))), index=idx, columns=list(an.FEATURES))


class TestAnalogs:

    def test_closest_month_comes_first_and_analogs_are_spread_out(self):
        df = panel()
        target = df.index[100]
        picked, dist = an.find(df, df.loc[target] + 0.01, n=5)
        assert picked[0] == target
        assert all(abs((a - b).n) >= an.MIN_GAP for i, a in enumerate(picked) for b in picked[:i])

    def test_recent_months_are_excluded(self):
        df = panel()
        picked, _ = an.find(df, df.iloc[-1], n=5)
        assert all(p <= df.index[-1] - an.EXCLUDE_RECENT for p in picked)

    def test_forward_returns(self):
        monthly = pd.DataFrame({"x": [0.10, 0.10, 0.0]}, index=pd.period_range("2020-01", periods=3, freq="M"))
        fwd = an.forward(monthly, 2)
        assert fwd.iloc[0]["x"] == pytest.approx((1.10 * 1.0 - 1) * 100)


def french_with_signal(df, beta=0.04, seed=3):
    """Daily French files where next-3-month excess returns load on the 'vix' feature of ``df``."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(df.index[0].to_timestamp(), (df.index[-1] + 4).to_timestamp(how="end"))
    month = days.to_period("M")
    signal = df["vix"].shift(1).reindex(month).fillna(0).to_numpy()      # last month's state drives this month
    mkt = rng.normal(0.0, 0.6, len(days)) + beta * signal
    cols = list(french.INDUSTRY_SECTORS)
    from .test_sector_study import french_csv
    return {french.FACTORS_3: french_csv(["Mkt-RF", "SMB", "HML", "RF"], days,
                                         np.column_stack([mkt, rng.normal(0, .5, len(days)), rng.normal(0, .5, len(days)),
                                                          np.full(len(days), 0.01)])),
            french.INDUSTRIES_12: french_csv(cols, days, np.column_stack([mkt] * len(cols))),
            french.MOMENTUM: french_csv(["Mom"], days, rng.normal(0, .5, (len(days), 1)))}


class TestModel:

    def test_planted_signal_has_positive_oos_r2(self, monkeypatch):
        df = panel(n=380)
        texts = french_with_signal(df, beta=0.15)
        monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
        m = an.model(df, df.iloc[-1])
        assert m.useful and m.oos_r2 > 0.05 and m.n_oos > 100
        assert max(m.coefficients, key=lambda k: abs(m.coefficients[k])) == "vix"

    def test_pure_noise_is_flagged_as_not_useful(self, monkeypatch):
        df = panel(n=380)
        texts = french_with_signal(df, beta=0.0)
        monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
        assert not an.model(df, df.iloc[-1]).useful


class TestCharts:

    def test_line_chart_is_svg_with_legend_and_last_value(self):
        idx = pd.date_range("2024-01-01", periods=50, freq="W")
        svg = charts.line_chart({"WTI": pd.Series(np.linspace(70, 90, 50), idx)}, "Crudo", unit=" $", zero=True)
        assert svg.startswith('<figure class="chart">') and "<svg" in svg and "WTI: 90 $" in svg

    def test_empty_chart_says_so(self):
        assert "Sin datos" in charts.line_chart({"x": pd.Series(dtype=float)}, "Vacío")


@pytest.fixture(scope="module")
def report_fred():
    data = {**macro_series(), **market(SUPPLY_SHOCK)}
    data.update(as_fred(*synthetic(today=TODAY, drop=(), cpi_lag=2)))
    data.update({k: v for k, v in macro_series().items() if k in ("CPIAUCSL", "CPILFESL")})
    days = pd.bdate_range("1985-01-01", TODAY)
    rng = np.random.default_rng(5)
    data["BAA10Y"] = pd.Series(2.0 + np.cumsum(rng.normal(0, 0.02, len(days))).clip(-1.5, 3), days)
    bdays = pd.bdate_range(end=TODAY, periods=300)
    data.update({"DGS5": pd.Series(4.3, bdays), "DGS30": pd.Series(4.8, bdays), "BAMLC0A0CM": pd.Series(0.9, bdays),
                 "CBBTCUSD": pd.Series(65000.0, index=pd.date_range(end=TODAY, periods=420))})
    weeks = pd.date_range(end=TODAY, periods=330, freq="W-WED")
    data.update({"WALCL": pd.Series(7e6 + np.cumsum(rng.normal(0, 2e4, 330)), weeks),
                 "WTREGEN": pd.Series(800_000.0, weeks),          # FRED: millions "RRPONTSYD": pd.Series(150.0, days[-1600:]),
                 "DFF": pd.Series(3.9, days[-1600:])})
    data["VXVCLS"] = data["VIXCLS"] * 1.1
    from .test_valuation_positioning import valuation_series
    vs = valuation_series()
    data.update({"NCBEILQ027S": vs["equities"], "CP": vs["profits"], "GDP": vs["gdp"]})
    from .test_carry_track import series as carry_series
    cs = carry_series()
    data.update({"DEXJPUS": cs["usdjpy"], "IR3TIB01USM156N": cs["us3m"], "IR3TIB01JPM156N": cs["jp3m"]})
    # long histories for the analogs (VIX, yields and oil since 1990)
    for sid, level, vol in (("VIXCLS", 18.0, 0.5), ("DGS2", 4.0, 0.03), ("DGS10", 4.5, 0.03), ("DCOILWTICO", 60.0, 0.5)):
        long = pd.Series(level + np.cumsum(rng.normal(0, vol, len(days))) * 0.1, days).clip(lower=1.0)
        data[sid] = pd.concat([long[long.index < data[sid].index[0]], data[sid]])
    return data


class TestReportCommand:

    def test_full_report(self, monkeypatch, capsys, tmp_path, report_fred):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        from macro_shock.research import crypto
        monkeypatch.setattr(crypto, "http_json", FakeAPI(synthetic_prices()))
        from macro_shock.research import agenda, prices, stooq
        monkeypatch.setattr(stooq, "http_text", fake_stooq(market_prices(sector_factors())))
        monkeypatch.setattr(prices, "http_json", lambda url, timeout=20.0: (_ for _ in ()).throw(OSError("blocked")))
        monkeypatch.setattr(agenda, "_get", lambda url: {"release_dates": [
            {"release_name": "Consumer Price Index", "date": str(TODAY.date())}]})
        texts = french_by_quadrant(pd.bdate_range("1970-01-02", TODAY))
        monkeypatch.setattr(french, "download", lambda dataset, timeout=60.0: texts[dataset])
        fake = FakeFred(report_fred)
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
        monkeypatch.setattr(feed_provider.FREDFeedProvider, "_get_fred", lambda self: fake)
        out_file = tmp_path / "informe.html"
        monkeypatch.setattr(sys, "argv", ["msre", "report", "--output", str(out_file)])
        cli.main()
        out = capsys.readouterr().out
        html = out_file.read_text(encoding="utf-8")
        assert "Report written to" in out and "incomplete" not in out
        for part in ("Lectura ejecutiva", "1. Qué descuenta el mercado", "2. Crecimiento", "3. Energía e IPC",
                     "4. Régimen de riesgo", "5. Valoración de la bolsa", "6. Posicionamiento (CFTC)", "7. Evidencia sectorial", "Próximos 14 días", "PER aproximado", "Índice COT", "8. Sectores del S&amp;P 500", "Mapa de escenarios",
                     "9. Metales", "Ratio oro/plata", "Visión sistemática por activo", "Sectores preferidos",
                     "VIX / VIX 3 meses", "10. Análogos históricos",
                     "Meses más parecidos a hoy", "Modelo predictivo", "Shock de oferta", "11. Cripto", "ONDO",
                     "Alertas cripto", "12. Liquidez de la Fed", "Liquidez neta", "13. Carry del yen", "subidas bruscas del yen",
                     "14. Historial de aciertos", "previsiones registradas", "15. Salud de los datos", "al día"):
            assert part in html, part
        assert html.count("<svg") >= 10
        assert "Secciones con datos incompletos" not in html
        log = (tmp_path / "predicciones.csv").read_text()
        assert "cpi" in log and "analogs" in log and "model" in log

    def test_quick_report_and_missing_section(self, monkeypatch, capsys, tmp_path, report_fred):
        from macro_shock import __main__ as cli
        from macro_shock.data import feed_provider
        from macro_shock.research import crypto
        monkeypatch.setattr(crypto, "http_json", FakeAPI(synthetic_prices(), fail=("binance",)))
        data = {k: v for k, v in report_fred.items() if k != "GASREGW"}     # CPI nowcast cannot run
        fake = FakeFred(data)
        monkeypatch.setattr(feed_provider, "fred_client", lambda api_key=None: fake)
        monkeypatch.setattr(feed_provider.FREDFeedProvider, "_get_fred", lambda self: fake)
        out_file = tmp_path / "q.html"
        monkeypatch.setattr(sys, "argv", ["msre", "report", "--quick", "--output", str(out_file)])
        cli.main()
        out = capsys.readouterr().out
        html = out_file.read_text(encoding="utf-8")
        assert "section 'ipc' incomplete" in out and "section 'cripto' incomplete" in out
        assert "falló" in html                                      # data health lists the failed series
        assert "Sección no disponible" in html and "Omitida (--quick)" in html and "Lectura ejecutiva" in html
