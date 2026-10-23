"""
tests/unit/test_valuation_positioning.py

Equity valuation from FRED (Z.1 market value, profits, GDP), CFTC positioning
(COT index, extremes, contrarian signals), the release calendar, and the
extra signals in the page-one view.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import agenda, positioning as po, valuation as va, view as vw

TODAY = pd.Timestamp(date.today())


def valuation_series(pe_now=30.0):
    q = pd.date_range("1951-01-01", TODAY - pd.DateOffset(months=4), freq="QS")
    n = len(q)
    pe = 15 + 5 * np.sin(np.arange(n) / 12)
    pe[-1] = pe_now
    profits = pd.Series(np.linspace(30, 3000, n), q)
    mv = profits * pe * 1000                                           # millions
    gdp = pd.Series(np.linspace(300, 30000, n), q)
    days = pd.bdate_range("2003-01-02", TODAY)
    return {"equities": mv, "profits": profits, "gdp": gdp, "real10": pd.Series(2.0, days)}


def cftc_rows(net_now=0.4, weeks=170):
    rows = []
    dates = pd.date_range(end=TODAY, periods=weeks, freq="W-TUE")[::-1]           # newest first, like the API
    for i, d in enumerate(dates):
        net = net_now if i == 0 else 0.1 * np.sin(i / 5)
        oi = 1_000_000
        rows.append({"report_date_as_yyyy_mm_dd": d.strftime("%Y-%m-%dT00:00:00.000"),
                     "noncomm_positions_long_all": str(int(oi * (0.3 + net / 2))),
                     "noncomm_positions_short_all": str(int(oi * (0.3 - net / 2))), "open_interest_all": str(oi)})
    return rows


class TestValuation:

    def test_today_values_and_percentiles(self):
        s = valuation_series()
        days = pd.bdate_range(end=TODAY, periods=300)
        sp = pd.Series(np.linspace(100, 110, 300), days)
        v = va.assess(s, sp, with_returns=False)
        assert v.now["pe"] > 30 and v.percentiles["pe"] == pytest.approx(1.0)
        assert v.now["erp"] == pytest.approx(v.now["ey"] - 2.0)

    def test_forward_link(self, monkeypatch):
        s = valuation_series(pe_now=19.9)
        q = va.quarterly(s)
        fwd = pd.Series(20 - q["pe"].to_numpy(), q.index)                # cheap -> high returns
        monkeypatch.setattr(va, "forward_returns", lambda years=5: fwd.iloc[:-20])
        v = va.assess(s, None)
        assert v.next5y["pe"]["same_fifth"] < v.next5y["pe"]["all"]


class TestPositioning:

    def fake(self, extremes=("13874A",), fail=()):
        def get(url, timeout=20.0):
            code = url.split("cftc_contract_market_code%3D%27")[1].split("%27")[0]
            if code in fail:
                raise OSError("down")
            return cftc_rows(0.4 if code in extremes else 0.0)
        return get

    def test_extremes_signals_and_alerts(self):
        p = po.fetch(self.fake())
        assert p.table.at["sp500", "cot_index"] == pytest.approx(100)
        assert p.signals()["equities"] == -1 and p.signals()["gold"] == 0
        assert p.alerts()[0].startswith("S&P 500 (E-mini): especuladores en máximos de posición larga")

    def test_partial_and_total_failure(self):
        p = po.fetch(self.fake(fail=("133741",)))
        assert "bitcoin" in p.errors and "bitcoin" not in p.table.index
        with pytest.raises(ValueError, match="CFTC"):
            po.fetch(self.fake(fail=tuple(c for c, _, _ in po.CONTRACTS.values())))


class TestAgenda:

    def test_filters_key_releases_and_adds_fomc(self):
        def get(url):
            return {"release_dates": [{"release_name": "Consumer Price Index", "date": "2026-10-24"},
                                      {"release_name": "H.8 Assets and Liabilities", "date": "2026-10-24"},
                                      {"release_name": "Employment Situation", "date": "2026-11-06"}]}
        a = agenda.upcoming(date(2026, 10, 23), get=get, api_key="x")
        assert list(a["event"]) == ["IPC (CPI)", "Decisión de la Fed (FOMC), 14:00 ET", "Empleo (nóminas)"]


class TestViewExtras:

    def test_valuation_and_positioning_signals(self):
        s = pd.Series(np.linspace(100, 150, 400), index=pd.bdate_range(end=TODAY, periods=400))
        base = vw.asset_view({"equities": s}, None, None, None).set_index("key")
        more = vw.asset_view({"equities": s}, None, None, None,
                             {"valuation": {"equities": -1}, "positioning": {"equities": -1}}).set_index("key")
        assert base.at["equities", "view"] == "Sobreponderar" and more.at["equities", "view"] == "Neutral"
        assert more.at["equities", "valuation"] == -1
