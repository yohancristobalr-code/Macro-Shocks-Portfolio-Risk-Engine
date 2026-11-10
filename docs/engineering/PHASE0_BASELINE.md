# Phase 0 — Safety baseline

Date: 2026-11-09

## Scope and isolation
- Working branch: `safety/phase-0-1-point-in-time-quality`
- Base branch: `main`
- All changes for this work are isolated on the working branch. Do not commit directly to `main`.
- This document records the starting state before Phase 1 changes.

## Baseline source files reviewed
- `python/src/macro_shock/market_context/vulnerability_scorer.py`
- `python/src/macro_shock/scenario_engine/scenario_tree.py`
- `python/src/macro_shock/research/regime_history.py`
- `python/src/macro_shock/research/macro_scenario.py`
- `python/src/macro_shock/backtesting/event_study.py`
- `python/src/macro_shock/research/track_record.py`
- `python/src/macro_shock/risk_scoring/composite_scorer.py`

## Confirmed baseline risks
1. Historical regime percentiles and BAA proxy knots are computed from the full available sample, creating look-ahead in historical classifications.
2. Historical macro-scenario scores normalize past moves using a volatility estimate from the latest three-year sample rather than the information available on each historical date.
3. Vulnerability scoring renormalizes weights over available dimensions, but missing dimensions remain zero in the component object consumed by regime classification. With no inputs it assigns a numeric composite of 0.5.
4. The percentile interpolation has endpoint discontinuities: values at/below P10 map to 0 while values immediately above P10 start near 0.10; a similar jump exists at P90.
5. Scenario templates include fixed `base_prob` weights and are described as probabilities despite lacking demonstrated empirical calibration.
6. The gap-confidence expression in the scenario engine was previously identified as inversely related to data quality and requires correction/verification in the implementation.
7. Forecast track-record writes replace same-day forecasts with matching keys, so individual forecast emissions are not preserved as an immutable ledger.
8. Composite risk scoring computes a score even when data quality is low; quality is reported but does not gate scoreability.

## Baseline limitations
- This is a static source review, not a runtime test result.
- No market-data backtest, calibration study, or out-of-sample validation is claimed.
- Existing CI history is not a substitute for tests run against this branch.
- Numerical thresholds, scenario weights, and calibration constants remain heuristic unless separately validated.

## Phase 1 acceptance principles
- No historical feature may use observations published after its as-of timestamp.
- Missing data must remain distinguishable from a measured zero.
- Insufficient coverage must produce an explicit unknown/not-scoreable state, not a confident regime label.
- Heuristic scenario weights must not be presented as empirically calibrated probabilities.
- Forecast emissions must be append-only and uniquely identifiable.
- Tests must cover edge cases and point-in-time behavior; passing unit tests alone does not establish market predictive validity.
