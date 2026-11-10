from datetime import datetime, timezone

import pandas as pd

from macro_shock.data_schema.models import MarketSessionState, MarketStateSnapshot, RegimeType
from macro_shock.market_context.vulnerability_scorer import MarketVulnerabilityScorer
from macro_shock.research.regime_history import history_percentiles
from macro_shock.research.track_record import record, read


def test_empty_market_state_is_not_scoreable_or_classified_as_risk_on():
    state = MarketStateSnapshot(
        timestamp=datetime.now(timezone.utc),
        session_state=MarketSessionState.CLOSED_OVERNIGHT,
    )
    result = MarketVulnerabilityScorer().score(state, log=False)

    assert result.data_quality == 0.0
    assert result.scoreable is False
    assert result.regime == RegimeType.UNKNOWN


def test_percentile_rank_is_continuous_at_calibration_endpoints():
    calibration = (10.0, 20.0, 30.0, 40.0, 50.0)
    below = MarketVulnerabilityScorer._percentile_rank(10.0, calibration)
    just_above = MarketVulnerabilityScorer._percentile_rank(10.000001, calibration)
    at_upper = MarketVulnerabilityScorer._percentile_rank(50.0, calibration)
    just_below = MarketVulnerabilityScorer._percentile_rank(49.999999, calibration)

    assert below == 0.0
    assert 0.0 <= just_above < 0.001
    assert at_upper == 1.0
    assert 0.999 < just_below <= 1.0


def test_history_percentiles_use_only_information_available_at_each_date():
    dates = pd.date_range("2020-01-01", periods=3, freq="D")
    values = pd.Series([10.0, 20.0, 10000.0], index=dates)
    ranks = history_percentiles(values)

    assert ranks.iloc[0] == 0.0
    assert ranks.iloc[1] == 0.5
    # The future extreme observation cannot change the first two historical ranks.
    truncated = history_percentiles(values.iloc[:2])
    pd.testing.assert_series_equal(ranks.iloc[:2], truncated)


def test_track_record_appends_revisions_instead_of_replacing(tmp_path):
    path = tmp_path / "predictions.csv"
    first = {"run_date": "2026-11-09", "kind": "model", "target": "sp500_3m",
             "due": "2027-02-09", "value": 2.0, "low": float("nan"), "high": float("nan")}
    revised = {**first, "value": 3.0}

    first_log = record(path, [first])
    second_log = record(path, [revised])
    persisted = read(path)

    assert len(first_log) == 1
    assert len(second_log) == 2
    assert len(persisted) == 2
    assert persisted["forecast_id"].nunique() == 2
    assert set(persisted["value"].astype(float)) == {2.0, 3.0}
