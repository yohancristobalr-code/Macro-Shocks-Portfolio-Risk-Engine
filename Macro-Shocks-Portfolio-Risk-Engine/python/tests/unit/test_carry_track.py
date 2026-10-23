"""
tests/unit/test_carry_track.py

Yen carry monitor (unwind signals, what followed them, alerts) and the
engine's track record (logging, same-day replacement, scoring).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from macro_shock.research import carry, track_record as tr

SURGES = ["2008-10-06", "2016-06-20", "2024-07-29"]


def usdjpy(end="2026-10-22", surge_at_end=False, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2000-01-03", end)
    r = rng.normal(0, 0.004, len(days))
    for d in SURGES:
        i = days.searchsorted(pd.Timestamp(d))
        r[i:i + 8] -= 0.007                                    # yen +5% in 8 days
    if surge_at_end:
        r[-8:] -= 0.007
    return pd.Series(110 * np.exp(np.cumsum(r)), days)


def nasdaq(fx, seed=1):
    """Falls 6% in the month after each surge, drifts up otherwise."""
    rng = np.random.default_rng(seed)
    days = fx.index
    r = rng.normal(0.0004, 0.004, len(days))
    for d in SURGES:
        i = days.searchsorted(pd.Timestamp(d))
        r[i + 1:i + 21] -= 0.003
    return pd.Series(1000 * np.exp(np.cumsum(r)), days)


def series(surge_at_end=False):
    months = pd.date_range("2000-01-01", "2026-09-01", freq="MS")
    return {"usdjpy": usdjpy(surge_at_end=surge_at_end), "us3m": pd.Series(4.3, months), "jp3m": pd.Series(0.5, months)}


class TestCarry:

    def test_unwind_dates_find_the_planted_surges(self):
        hits = carry.unwind_dates(usdjpy())
        for d in SURGES:
            assert any(abs((h - pd.Timestamp(d)).days) <= 12 for h in hits), d

    def test_assess_and_history(self):
        s = series()
        c = carry.assess(s, {"Nasdaq": nasdaq(s["usdjpy"])})
        assert c.differential == pytest.approx(3.8) and c.carry_to_risk > 0 and not c.unwind_now
        h = c.history["Nasdaq"]
        assert h["n"] >= len(SURGES) and h["mean_after"] < h["mean_all"]
        assert "After" in "\n".join(carry.lines(c))

    def test_unwind_alert(self):
        c = carry.assess(series(surge_at_end=True))
        assert c.unwind_now and carry.alerts(c)[0].startswith("YEN CARRY UNWIND")

    def test_fetch_falls_back_to_second_id(self):
        s = series()
        data = {"DEXJPUS": s["usdjpy"], "TB3MS": s["us3m"], "IRSTCI01JPM156N": s["jp3m"]}

        class Fred:
            def get_series(self, sid, observation_start=None, observation_end=None):
                return data[sid]
        out = carry.fetch(Fred(), date(2026, 10, 22))
        assert len(out["us3m"]) and len(out["jp3m"])


class TestTrackRecord:

    def nowcast(self):
        m = pd.Timestamp("2026-09-01")
        table = pd.DataFrame([{"month": m, "item": "headline", "mm": 0.30, "low": 0.10, "high": 0.50}])
        return SimpleNamespace(months=[m], table=table)

    def test_record_replaces_same_day(self, tmp_path):
        path = tmp_path / "p.csv"
        an = SimpleNamespace(table=pd.DataFrame({"mkt_6m": [2.0, 4.0, 6.0]}))
        tr.record(path, tr.forecasts(date(2026, 3, 2), self.nowcast(), an, SimpleNamespace(forecast=1.5)))
        log = tr.record(path, tr.forecasts(date(2026, 3, 2), self.nowcast(), an, None))
        assert len(log) == 3 and set(log["kind"]) == {"cpi", "analogs", "model"}
        assert log[log["kind"] == "analogs"].iloc[0]["value"] == 4.0

    def test_score(self, tmp_path):
        log = tr.record(tmp_path / "p.csv", tr.forecasts(date(2026, 3, 2), self.nowcast(),
                                                        SimpleNamespace(table=pd.DataFrame({"mkt_6m": [3.0]})), None))
        cpi = pd.Series([100.0, 100.4], pd.to_datetime(["2026-08-01", "2026-09-01"]))
        days = pd.bdate_range("2026-01-01", "2026-10-22")
        sp = pd.Series(np.linspace(5000, 5500, len(days)), days)
        sc = tr.score(log, cpi, sp).set_index("kind")
        assert sc.loc["cpi", "actual"] == pytest.approx(0.4) and sc.loc["cpi", "inside"]
        assert sc.loc["analogs", "actual"] > 0 and sc.loc["analogs", "same_sign"] == 1.0
        text = "\n".join(tr.summary(sc.reset_index(), log))
        assert "2 ya evaluables" in text and "dentro del rango 90%: 100%" in text

    def test_not_yet_due_is_pending(self, tmp_path):
        log = tr.record(tmp_path / "p.csv", tr.forecasts(date(2026, 10, 20), self.nowcast(), None, None))
        assert tr.score(log, pd.Series([100.0], pd.to_datetime(["2026-08-01"])), None).empty
        assert "1 pendientes" in "\n".join(tr.summary(pd.DataFrame(), log))
