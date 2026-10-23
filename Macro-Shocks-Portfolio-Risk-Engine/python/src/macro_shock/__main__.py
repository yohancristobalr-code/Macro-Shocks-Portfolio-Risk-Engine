"""
macro_shock/__main__.py

CLI entry point for the Macro Shock Risk Engine.
Enables: python -m macro_shock [command] [options]

Commands:
  monitor     Continuous real-time event monitoring (production mode)
  analyze     Analyze a single event from JSON or CLI flags
  backtest    Run historical event study backtest
  fomc-study  Bitcoin / S&P 500 / VIX reaction to FOMC announcements (FRED data)
  sector-study Sectors and styles that did best in each market regime (French + FRED)
  optimize    Sector / Treasury / Bitcoin weights by risk parity or minimum variance
  daily       Daily routine: market inputs, regime, alerts vs the previous run, saved report
  serve       Start the REST API server
  health      Run system health check
  demo        Run a canned scenario demonstration

Examples:
  python -m macro_shock monitor --config configs/production.yaml
  python -m macro_shock analyze --event-file event.json
  python -m macro_shock analyze --event-file event.json --data fred   # real FRED data
  python -m macro_shock analyze --event-file event.json --portfolio portfolio.csv
  python -m macro_shock backtest --start 2020-01-01 --end 2024-01-01
  python -m macro_shock fomc-study --output fomc_events.csv   # Bitcoin vs FOMC (FRED)
  python -m macro_shock sector-study                          # sectors by regime
  python -m macro_shock optimize --output portfolio_optimized.csv
  python -m macro_shock optimize --max-drawdown 0.25          # most equity risk within a -25% drawdown
  python -m macro_shock daily                                 # daily report saved under reports/
  python -m macro_shock serve --port 8000
  python -m macro_shock health --env production
  python -m macro_shock demo --scenario weekend_crisis
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import structlog

# Ensure src is importable when run as python -m macro_shock
_src = Path(__file__).parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


def _configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    import logging
    processors = [
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.stdlib.add_log_level,
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(processors=processors)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(p: Path) -> dict:
    """Read a YAML config, recursively merging the file named by its `_base` key."""
    import yaml
    with open(p) as f:
        cfg = yaml.safe_load(f) or {}
    base_name = cfg.pop("_base", None)
    if base_name:
        cfg = _deep_merge(_read_yaml(p.parent / base_name), cfg)
    return cfg


# Nested YAML keys the pipeline reads from the top level of its config dict.
_FLATTENED_KEYS = {
    ("pipeline", "fail_fast"): "fail_fast",
    ("nlp", "use_transformer"): "use_transformer",
    ("nlp", "transformer_model"): "transformer_model",
    ("logging", "audit_log_dir"): "audit_log_dir",
}


def _load_config(config_path: str | None, env: str) -> dict:
    if config_path:
        p = Path(config_path)
    else:
        p = Path(__file__).parent.parent.parent / "configs" / f"{env}.yaml"

    if not p.exists():
        return {}
    cfg = _read_yaml(p)
    for (section, key), flat_key in _FLATTENED_KEYS.items():
        value = cfg.get(section, {}).get(key)
        if value is not None and flat_key not in cfg:
            cfg[flat_key] = value
    return cfg


# ──────────────────────────────────────────────────────────────
# Command: monitor
# ──────────────────────────────────────────────────────────────

def cmd_monitor(args: argparse.Namespace) -> None:
    """Continuous real-time event monitoring loop."""
    import time
    from macro_shock.orchestration.pipeline import MacroShockPipeline
    from macro_shock.monitoring.alert_manager import HeartbeatMonitor

    cfg = _load_config(args.config, args.env)
    pipeline = MacroShockPipeline(config=cfg, environment=args.env)
    heartbeat = HeartbeatMonitor(interval_seconds=300)

    logger = structlog.get_logger("monitor")
    logger.info("monitor_started", env=args.env, poll_interval=args.interval)

    print(f"\n[MSRE] Monitor active | env={args.env} | poll={args.interval}s")
    print("[MSRE] Press Ctrl+C to stop.\n")

    try:
        while True:
            heartbeat.heartbeat()
            heartbeat.check()
            # In production: poll event feed here and call pipeline.process_raw_event(...)
            # Placeholder: log alive signal every interval
            logger.debug("monitor_alive")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("monitor_stopped")
        print("\n[MSRE] Monitor stopped.")


# ──────────────────────────────────────────────────────────────
# Command: analyze
# ──────────────────────────────────────────────────────────────

def cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze a single event and print the full risk report."""
    from macro_shock.orchestration.pipeline import MacroShockPipeline
    from macro_shock.data.ingestion import MarketStateBuilder

    portfolio = None
    if args.portfolio:
        from macro_shock.portfolio_impact.portfolio_stress import PortfolioFileError, load_portfolio
        try:
            portfolio = load_portfolio(args.portfolio)
        except PortfolioFileError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    cfg = _load_config(args.config, args.env)
    pipeline = MacroShockPipeline(config=cfg, environment=args.env)

    if args.event_file:
        with open(args.event_file) as f:
            raw_event = json.load(f)
    else:
        # Build minimal event from CLI flags
        if not args.title or not args.institution or not args.event_time:
            print("ERROR: --title, --institution, and --event-time are required without --event-file")
            sys.exit(1)
        raw_event = {
            "title": args.title,
            "institution": args.institution,
            "event_time": args.event_time,
            "speaker": args.speaker or "",
            "speaker_role": args.speaker_role or "",
            "prepared_remarks": args.text or "",
        }

    from datetime import datetime, timezone
    try:
        event_time = datetime.fromisoformat(raw_event["event_time"]).astimezone(timezone.utc)
    except Exception:
        event_time = datetime.now(timezone.utc)

    if args.data == "fred":
        from macro_shock.data.feed_provider import DataFeedError, FREDFeedProvider
        try:
            market_state = FREDFeedProvider(pipeline.calendar).get_snapshot(as_of=event_time)
        except DataFeedError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        data_source = "FRED, end-of-day"
    else:
        market_state = MarketStateBuilder.build_synthetic(
            as_of=event_time,
            calendar=pipeline.calendar,
            stress_level=args.stress,
            seed=42,
        )
        data_source = f"synthetic, stress={args.stress:.2f}"

    ctx = pipeline.process_raw_event(raw_event, market_state=market_state)

    # Print structured output
    _print_risk_report(ctx, data_source)
    if args.sectors and ctx.risk_score:
        _print_sector_suggestions(ctx.risk_score.regime.value, args.data)
    if portfolio is not None and ctx.scenario_tree:
        _print_portfolio_stress(portfolio, ctx.scenario_tree)

    if args.output:
        result = {}
        if ctx.risk_score:
            result["composite_score"] = ctx.risk_score.composite_score
            result["severity"] = ctx.risk_score.severity.value
            result["action_level"] = ctx.risk_score.recommended_action_level
            result["summary"] = ctx.risk_score.summary
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nOutput written to: {args.output}")


def _fmt(value, spec: str) -> str:
    return format(value, spec) if value is not None else "n/a"


def _print_market_state(state, data_source: str) -> None:
    yields, credit, vol, equity = state.yields, state.credit, state.volatility, state.equity
    drawdown = equity.spx_from_52w_high * 100 if equity and equity.spx_from_52w_high is not None else None
    print(f"\n  Market ({data_source}):")
    print(f"    10Y: {_fmt(yields and yields.y10, '.2f')}%  "
          f"| 2s10s: {_fmt(yields and yields.slope_2_10, '+.0f')}bps  "
          f"| HY: {_fmt(credit and credit.hy_spread_bps, '.0f')}bps")
    print(f"    VIX: {_fmt(vol and vol.vix_spot, '.1f')}  "
          f"| SPX vs 52w high: {_fmt(drawdown, '+.1f')}%")
    if state.gap_description:
        print(f"    Note: {state.gap_description}")


def _print_portfolio_stress(portfolio, tree) -> None:
    from macro_shock.portfolio_impact.portfolio_stress import stress_portfolio

    st = stress_portfolio(portfolio, tree)
    sep = "─" * 65
    print(f"  YOUR PORTFOLIO  ({len(portfolio)} positions, value {st.total_value:,.0f})")
    print(sep)
    print(f"  Expected:        {st.expected_pnl:>+12,.0f}  ({st.expected_pnl_pct:+.1f}%)")
    print(f"  Worst 5% case:   {st.pnl_5pct:>+12,.0f}  ({st.pnl_5pct_pct:+.1f}%)")
    print(f"\n  {'Scenario':<35} {'Prob':>6}  {'P&L':>12}  {'%':>7}")
    for _, r in st.scenario_pnl.sort_values("pnl").iterrows():
        print(f"  {r['scenario']:<35} {r['probability']:>5.1%}  {r['pnl']:>+12,.0f}  {r['pnl_pct']:>+6.1f}%")
    print(f"\n  Largest losses in '{st.worst_scenario}':")
    for name, pnl in st.worst_contributors().items():
        if pnl < 0:
            print(f"    {name:<30} {pnl:>+12,.0f}")
    print("  Scenario magnitudes are illustrative; use for sizing, not as a forecast.")
    print(sep + "\n")


def _print_risk_report(ctx, data_source: str = "synthetic") -> None:
    """Print a concise risk report to stdout."""
    sep = "─" * 65
    print(f"\n{sep}")
    print("  MACRO SHOCK RISK ENGINE — ANALYSIS REPORT")
    print(sep)

    if ctx.event:
        e = ctx.event
        print(f"  Event:    {e.title}")
        print(f"  Type:     {e.event_type.value}  |  Severity: {e.severity.value} ({e.severity_score:.1f})")
        print(f"  Weekend:  {e.is_weekend}  |  Gap Corridor: {e.full_weekend_gap}")
        if e.hours_until_next_open:
            print(f"  Hours to open: {e.hours_until_next_open:.1f}h")

    if ctx.market_state:
        _print_market_state(ctx.market_state, data_source)

    if ctx.policy_surprise:
        ps = ctx.policy_surprise
        hd = ps.hawkish_dovish
        stance = hd.stance.value if hd else "N/A"
        print(f"\n  Policy:   {stance}  |  Surprise: {ps.composite_surprise_magnitude:.2f}  |  Direction: {ps.net_direction:+.2f}")
        if hd and hd.crisis_language_detected:
            print("  ⚠  Crisis language detected")

    if ctx.risk_score:
        rs = ctx.risk_score
        print(f"\n  ┌─ COMPOSITE RISK SCORE: {rs.composite_score:.1f}/100  [{rs.severity.value}]")
        print(f"  │  Regime: {rs.regime.value}  ×{rs.regime_multiplier:.2f}  |  Gap: ×{rs.gap_risk_multiplier:.2f}")
        print(f"  │  Action: {rs.recommended_action_level}")
        print(f"  └─ Reliability: {rs.score_reliability}")

    if ctx.scenario_tree:
        tree = ctx.scenario_tree
        print(f"\n  Scenarios: E[equity]={tree.expected_equity_impact_pct:+.1f}%  "
              f"| 5th Pct={tree.tail_loss_5pct:.1f}%  "
              f"| E[ΔVix]={tree.expected_vix_change:+.1f}pts")
        if tree.monday_gap_estimate_pct is not None:
            print(f"             Monday gap est: {tree.monday_gap_estimate_pct:+.1f}%")
        print(f"  Bitcoin:   E[move]={tree.expected_btc_change_pct:+.1f}%  "
              f"| 5th Pct={tree.btc_tail_loss_5pct:.1f}%")

    if ctx.portfolio_impact:
        pi = ctx.portfolio_impact
        print(f"\n  Portfolio: {pi.action_level}")
        for h in pi.hedge_recommendations[:3]:
            print(f"    [{h.urgency}] {h.action} {h.asset_class.value.upper()} — {h.instrument_description[:55]}")

    if ctx.alerts:
        print(f"\n  Alerts: {len(ctx.alerts)}")
        for a in ctx.alerts[:3]:
            print(f"    [{a.level.value}] {a.title}")

    print(sep + "\n")


# ──────────────────────────────────────────────────────────────
# Command: sector-study
# ──────────────────────────────────────────────────────────────

def _run_sector_study(start: str = "1990-01-01", horizon: int = 21, end: str | None = None):
    from datetime import date

    from macro_shock.data.feed_provider import DataFeedError, fred_client
    from macro_shock.research import sector_study
    from macro_shock.research.french import FrenchDataError

    try:
        return sector_study.run(fred_client(), date.fromisoformat(start), horizon,
                                date.fromisoformat(end) if end else None)
    except (DataFeedError, FrenchDataError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: could not build the sector study ({e}). A data source may be down; try again later.")
        sys.exit(1)


def _print_sector_suggestions(regime: str, data_source: str) -> None:
    from macro_shock.research import sector_study

    study = _run_sector_study()
    sep = "─" * 72
    print(f"  SECTORS AND STYLES IN THIS REGIME ({study.first_date.year}-{study.last_date.year} history)")
    print(sep)
    if data_source != "fred":
        print("  Note: the regime above comes from synthetic market data; use --data fred for today's regime.")
    for line in sector_study.supported_lines(study, regime):
        print(line)
    print(sector_study.FOOTNOTE)
    print(sep + "\n")


def cmd_sector_study(args: argparse.Namespace) -> None:
    """Sectors and styles that historically did best after each market regime."""
    import pandas as pd

    from macro_shock.research import sector_study

    study = _run_sector_study(args.start, args.horizon, args.end)
    sep = "─" * 72
    print(f"\n{sep}")
    print("  SECTORS AND STYLES BY MARKET REGIME (French Data Library + FRED)")
    print(sep)
    print(f"  Period: {study.first_date} to {study.last_date}  |  window: {study.horizon} trading days, "
          f"non-overlapping  |  windows: {int(study.regime_counts.sum())}")
    print("  Regimes: " + "  ".join(f"{r} {n}" for r, n in study.regime_counts.items()))
    print(f"  Robustness: each result is re-estimated on the halves before / after {study.split_date}")
    pc = study.proxy_check
    if pc["n"]:
        print(f"  Credit proxy check: BAA10Y-based regime matches the ICE-based one on "
              f"{pc['agreement']:.0%} of {pc['n']} dates since {pc['since']}")
        m = pc["matrix"]
        cols = list(m.columns)
        print(f"    {'real (rows) vs proxy (cols)':<26}" + "".join(f"{c:>21}" for c in cols))
        for regime, row in m.iterrows():
            print(f"    {regime:<26}" + "".join(f"{int(row[c]):>21}" for c in cols))
    for regime in study.regime_counts.index:
        print()
        for line in sector_study.regime_lines(study, regime):
            print(line)

    print(f"\n  CURRENT REGIME ({study.current_date}): {study.current_regime}"
          + (f"  (BAA10Y proxy: {study.current_regime_proxy})"
             if study.current_regime_proxy != study.current_regime else ""))
    if study.current_regime:
        print("  What the evidence supports for it:")
        for line in sector_study.supported_lines(study, study.current_regime)[1:]:
            print(line)
    print(sector_study.FOOTNOTE)
    print(sep + "\n")

    if args.output:
        out = pd.concat([study.sectors.assign(kind="sector"), study.styles.assign(kind="style")])
        out.to_csv(args.output, index=False)
        print(f"Table written to: {args.output}")


# ──────────────────────────────────────────────────────────────
# Command: daily
# ──────────────────────────────────────────────────────────────

def cmd_daily(args: argparse.Namespace) -> None:
    """Daily routine: FRED market inputs, regime, alerts against the previous run, saved report."""
    import re
    from datetime import date, timedelta

    from macro_shock.data.feed_provider import DataFeedError, FREDFeedProvider, fred_client
    from macro_shock.event_detection.calendar import MarketCalendar
    from macro_shock.market_context.vulnerability_scorer import MarketVulnerabilityScorer
    from macro_shock.research import daily, sector_study

    today = date.today()
    try:
        snap = FREDFeedProvider(MarketCalendar()).get_snapshot()
    except DataFeedError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: could not read FRED ({e}). FRED may be down; try again later.")
        sys.exit(1)

    btc = None
    try:
        s = fred_client().get_series("CBBTCUSD", observation_start=str(today - timedelta(days=14)),
                                     observation_end=str(today)).dropna()
        btc = float(s.iloc[-1]) if len(s) else None
    except Exception:
        pass                                    # Bitcoin is informative only

    vuln = MarketVulnerabilityScorer().score(snap, log=False)
    found = re.search(r"through (\d{4}-\d{2}-\d{2})", snap.gap_description or "")
    data_through = date.fromisoformat(found.group(1)) if found else today
    components = {"volatility": vuln.volatility_vulnerability, "credit": vuln.credit_vulnerability,
                  "curve": vuln.yield_curve_vulnerability}
    row = daily.snapshot_row(today, data_through, vuln.regime.value, vuln.composite, snap, btc)

    reports = Path(args.reports_dir) if args.reports_dir else _PROJECT_ROOT / "reports"
    log_path = reports / "daily_log.csv"
    log = daily.read_log(log_path)
    previous = daily.previous_row(log, today)
    alert_lines = daily.alerts(row, previous)

    sector_lines = None
    if not args.quick:
        try:
            study = sector_study.run(fred_client(), date(1990, 1, 1))
            sector_lines = sector_study.supported_lines(study, row["regime"])[1:]
        except Exception as e:
            sector_lines = [f"    (sector study unavailable today: {e})"]

    lines = daily.render(row, alert_lines, previous, components, sector_lines)
    print("\n" + "\n".join(lines))
    daily.write_log(log, row, log_path)
    report = reports / f"daily_{today.isoformat()}.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved {report} and {log_path}")


# ──────────────────────────────────────────────────────────────
# Command: optimize
# ──────────────────────────────────────────────────────────────

OBJECTIVE_NAMES = {"risk_parity": "risk parity", "min_variance": "minimum variance",
                   "mix": "stock/bond mix of risk-parity sleeves"}


def cmd_optimize(args: argparse.Namespace) -> None:
    """Risk-based sector / Treasury / Bitcoin allocation, tilted by the current regime's evidence."""
    from datetime import date

    import pandas as pd

    from macro_shock.data.feed_provider import DataFeedError, fred_client
    from macro_shock.research import allocation, french, sector_study
    from macro_shock.research.french import FrenchDataError

    try:
        fred = fred_client()
        industries = french.daily_returns(french.INDUSTRIES_12)
        market = french.market_return(french.daily_returns(french.FACTORS_3))
        end = industries.index[-1]
        start = end - pd.DateOffset(years=args.years)
        returns, assets = allocation.build_universe(
            fred, industries, market, start.date(), end.date(), include_btc=not args.no_bitcoin)
        btc = 0.0 if args.no_bitcoin else args.btc_weight
        if not 0 <= btc < 1:
            raise ValueError("--btc-weight must be between 0 and 1")
        objective, mix, frontier, pick, within = args.objective, None, None, None, True
        history = allocation.long_returns(fred, industries, market)
        if args.max_drawdown is not None:
            frontier = allocation.drawdown_frontier(
                returns, assets, btc, long_returns=None if args.recent_only else history)
            pick, within = allocation.choose_for_drawdown(frontier, args.max_drawdown)
            mix, objective = {"stocks": float(pick["stocks"]), "btc": btc}, "mix"
        elif args.stocks is not None:
            if not 0 <= args.stocks <= 1:
                raise ValueError("--stocks must be between 0 and 1 (share of the non-Bitcoin money)")
            mix, objective = {"stocks": args.stocks, "btc": btc}, "mix"

        tilts, notes, regime = {}, [], None
        if args.tilt > 0:
            study = sector_study.run(fred, date(1990, 1, 1))
            regime = study.current_regime
            tilts = allocation.regime_tilts(study.sectors, regime, args.tilt)
            notes = allocation.style_notes(study.styles, regime)
        alloc = allocation.optimize(returns, assets, objective, args.max_weight, tilts, regime, notes, mix=mix)
        long_run = allocation.long_history_drawdown(fred, industries, market, alloc.tilted, assets,
                                                    returns=history)
    except (DataFeedError, FrenchDataError, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: could not build the allocation ({e}). A data source may be down; try again later.")
        sys.exit(1)

    sep = "─" * 72
    print(f"\n{sep}")
    print(f"  PORTFOLIO OPTIMIZER — {OBJECTIVE_NAMES[alloc.objective]}, daily data {alloc.start} to {alloc.end}")
    print(sep)
    if alloc.objective == "min_variance":
        print(f"  Max weight per asset: {args.max_weight:.0%}")
    if frontier is not None:
        long_col = "max_drawdown_long" in frontier
        rule = "in both the recent window and since 1990" if long_col else "in the recent window only"
        print(f"\n  Stocks vs drawdown (limit {-abs(args.max_drawdown):.0%}, {rule}); sectors and bonds "
              f"each risk-parity within their sleeve:")
        print(f"    {'Stocks':>7} {'Bonds':>6} {'BTC':>5} {'Vol':>6} {'Ret/yr':>7} {'Max DD':>7}"
              + (f" {'DD 1990+':>9}" if long_col else ""))
        for _, r in frontier.iterrows():
            mark = "  <- chosen" if r["stocks"] == pick["stocks"] else ""
            extra = f" {r['max_drawdown_long']:>9.1%}" if long_col else ""
            print(f"    {r['weight_equity']:>7.0%} {r['weight_bonds']:>6.0%} {r['weight_bitcoin']:>5.0%} "
                  f"{r['volatility']:>6.1%} {r['annual_return']:>7.1%} {r['max_drawdown']:>7.1%}{extra}{mark}")
        if not within:
            print(f"  ⚠  No mix kept the drawdown within {-abs(args.max_drawdown):.0%}; showing the least risky one.")
    if regime:
        applied = ", ".join(f"{sector_study.sector_label(a)} {t:+.0%}" for a, t in alloc.tilts.items())
        print(f"  Current regime: {regime}  ->  tilts: {applied or 'none (no robust/consistent sector evidence)'}")
        for note in alloc.notes:
            print(f"  Style evidence: {note} - express it with a style ETF; sector weights cannot")
    elif args.tilt <= 0:
        print("  Regime tilt: off")

    print(f"\n  {'Asset':<26} {'ETF':<5} {'Base':>7} {'Tilted':>8} {'Risk share':>11}")
    for code in alloc.tilted.sort_values(ascending=False).index:
        a = alloc.assets[code]
        print(f"  {a.label:<26} {a.etf:<5} {alloc.base[code]:>7.1%} {alloc.tilted[code]:>8.1%} "
              f"{alloc.risk_share[code]:>11.1%}")

    print(f"\n  {'Class':<10} {'Weight':>8} {'Risk share':>11}")
    for cls, r in alloc.classes.iterrows():
        print(f"  {cls:<10} {r['weight']:>8.1%} {r['risk_share']:>11.1%}")

    print(f"\n  {'':<10} {'Volatility':>11} {'Return/yr':>10} {'Max drawdown':>13}")
    for name in ("base", "tilted"):
        s = alloc.stats[name]
        print(f"  {name.capitalize():<10} {s['volatility']:>11.1%} {s['annual_return']:>10.1%} "
              f"{s['max_drawdown']:>13.1%}")
    print(f"\n  Longer history check, same stock/bond weights without Bitcoin, "
          f"{long_run['start'].year}-{long_run['end'].year}:")
    print(f"    max drawdown {long_run['max_drawdown']:.1%} (trough {long_run['trough']})")
    if args.max_drawdown is not None and long_run["max_drawdown"] < -abs(args.max_drawdown):
        print(f"    ⚠  Deeper than your {-abs(args.max_drawdown):.0%} limit: the {alloc.start.year}-"
              f"{alloc.end.year} window had no crash like this one. Consider a lower --max-drawdown.")
    print("  Historical figures with daily rebalancing; future drawdowns can be deeper. Not a forecast.")
    print(sep + "\n")

    allocation.to_portfolio_csv(alloc, args.value, args.output)
    print(f"Tilted weights written to {args.output} (value {args.value:,.0f}). Stress-test them with:")
    print(f"  python -m macro_shock analyze --title ... --institution ... --event-time ... "
          f"--data fred --portfolio {args.output}")


# ──────────────────────────────────────────────────────────────
# Command: fomc-study
# ──────────────────────────────────────────────────────────────

def cmd_fomc_study(args: argparse.Namespace) -> None:
    """Event study of Bitcoin, S&P 500 and VIX reactions to FOMC announcements."""
    from datetime import date

    import numpy as np

    from macro_shock.data.feed_provider import DataFeedError, fred_client
    from macro_shock.research import fomc_study as fs

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    dates = fs.announcement_dates(start, end)
    if not dates:
        print(f"ERROR: no FOMC announcements listed between {start} and {end}")
        sys.exit(1)

    try:
        series = fs.fetch_series(fred_client(), start, end)
    except DataFeedError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: could not download FRED data ({e}). FRED may be down; try again later.")
        sys.exit(1)

    events = fs.event_table(series, dates, args.threshold)
    summary = fs.summarize(events)
    base = fs.btc_baseline(series["btc"], events)
    unexplained = fs.validate_dates(series["fed_upper"], dates, start, end)
    betas = {
        "around FOMC": fs.event_beta(events),
        "all windows": fs.window_beta(series["btc"], series["spx"], dates[0], dates[-1]),
    }
    downside = fs.downside_ratio(series["btc"], series["spx"], dates[0], dates[-1])

    sep = "─" * 72
    print(f"\n{sep}")
    print("  FOMC EVENT STUDY — Bitcoin, S&P 500 and VIX (FRED daily data)")
    print(sep)
    print(f"  Period: {dates[0]} to {dates[-1]}  |  Announcements: {len(dates)}")
    print(f"  Surprise: 2Y yield move on the day; hawkish >= +{args.threshold:g}bps, dovish <= -{args.threshold:g}bps")
    print("  Reaction window: last close before the announcement -> close of the next day")

    if unexplained:
        print("\n  ⚠  DATE CHECK FAILED: FRED shows target-rate changes on")
        print("     " + ", ".join(str(d) for d in unexplained))
        print("     with no listed announcement just before. The date list may be wrong.")
    else:
        print("\n  Date check: every target-rate change in FRED matches a listed announcement ✓")

    print(f"\n  {'Stance':<9}{'N':>4}  {'BTC mean':>9}  {'BTC median':>10}  {'BTC up':>7}  {'SPX mean':>9}  {'VIX Δ':>6}")
    for stance, r in summary.iterrows():
        print(f"  {stance:<9}{int(r['n']):>4}  {r['btc_mean_pct']:>+8.2f}%  {r['btc_median_pct']:>+9.2f}%  "
              f"{r['btc_up_share']:>6.0%}  {r['spx_mean_pct']:>+8.2f}%  {r['vix_mean_change']:>+6.2f}")

    print(f"\n  BTC average absolute 2-day move: {base['fomc_abs_move_pct']:.2f}% around FOMC "
          f"vs {base['typical_abs_move_pct']:.2f}% on a typical 2-day window ({base['ratio']:.2f}x)")
    print(f"  Correlation BTC move vs 2Y surprise: {base['corr_btc_vs_surprise']:+.2f}")
    print(f"  Correlation BTC move vs S&P 500 move: {base['corr_btc_vs_spx']:+.2f}")

    print("\n  Bitcoin beta to the S&P 500 (2-day moves, 95% bootstrap interval):")
    for label, b in betas.items():
        print(f"    {label:<12} beta {b['beta']:>5.2f}  [{b['ci_low']:.2f} to {b['ci_high']:.2f}]  n={b['n']}")
    b = betas["all windows"]
    if not np.isnan(b["beta"]):
        print(f"    -> on average, a 1% S&P 500 move came with a {b['beta']:.1f}% Bitcoin move")
    print(f"    downside     ratio {downside['ratio']:>5.2f}  [{downside['ci_low']:.2f} to {downside['ci_high']:.2f}]  "
          f"n={downside['n']}  (windows where the S&P 500 fell 2% or more)")
    if not np.isnan(downside["ratio"]):
        print(f"    -> when the S&P 500 fell sharply, Bitcoin fell {downside['ratio']:.1f}x as much on average"
              f" (scenario engine setting: btc_tail_beta)")

    biggest = events.dropna(subset=["btc_pct"]).reindex(
        events["btc_pct"].abs().sort_values(ascending=False).index
    ).head(5)
    print("\n  Largest Bitcoin moves:")
    for _, r in biggest.iterrows():
        print(f"    {r['date']}  BTC {r['btc_pct']:+6.1f}%  | 2Y {r['surprise_bps']:+5.0f}bps "
              f"| rate {r['rate_change_bps']:+4.0f}bps  [{r['stance']}]")

    print("\n  Small sample and daily data: treat as associations, not causal effects.")
    print(sep + "\n")

    if args.output:
        events.to_csv(args.output, index=False)
        print(f"Event table written to: {args.output}")


# ──────────────────────────────────────────────────────────────
# Command: backtest
# ──────────────────────────────────────────────────────────────

def cmd_backtest(args: argparse.Namespace) -> None:
    """Run historical event study backtest."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "examples"))

    from macro_shock.orchestration.pipeline import MacroShockPipeline
    from macro_shock.backtesting.event_study import BacktestEngine, TimestampGuard, TransactionCostModel
    from macro_shock.data_schema.models import BacktestEvent, EventType

    cfg = _load_config(args.config, args.env)
    pipeline = MacroShockPipeline(config=cfg, environment=args.env)

    print(f"[Backtest] Loading synthetic events...")
    from synthetic_data_generator import generate_market_data_df, generate_synthetic_events
    df = generate_market_data_df(n_days=1500, stress_profile="normal")
    guard = TimestampGuard(df, timestamp_col="timestamp")
    engine = BacktestEngine(pipeline=pipeline, market_data_guard=guard,
                            cost_model=TransactionCostModel())

    raw_events = generate_synthetic_events()
    events = []
    for e in raw_events:
        try:
            events.append(BacktestEvent(
                event_id=e["event_id"], event_date=e["event_date"],
                event_type=EventType(e["event_type"]), is_weekend=e["is_weekend"],
                institution=e["institution"], speaker=e.get("speaker"),
                description=e["description"],
                realized_spx_next_session_return=e.get("realized_spx_next_session_return"),
                realized_10y_yield_change_bps=e.get("realized_10y_yield_change_bps"),
                realized_vix_change=e.get("realized_vix_change"),
                trading_halt_occurred=e.get("trading_halt_occurred", False),
                data_validated=True,
            ))
        except Exception as ex:
            print(f"  Skipping {e['event_id']}: {ex}")

    result = engine.run(events)
    print(f"\n{'─'*50}")
    print(f"  BACKTEST RESULTS  ({result.n_events} events)")
    print(f"{'─'*50}")
    print(f"  Score accuracy:      {result.score_accuracy:.3f}")
    print(f"  Precision@CRITICAL:  {result.precision_at_critical:.3f}")
    print(f"  Recall@CRITICAL:     {result.recall_at_critical:.3f}")
    print(f"  Max Drawdown:        {result.max_drawdown:.3f}")
    print(f"  ES (5%):             {result.expected_shortfall_5pct:.3f}")
    print(f"  Gap Estimate Error:  {result.avg_weekend_gap_estimate_error:.2f}%")
    print(f"  {result.notes}")
    print()


# ──────────────────────────────────────────────────────────────
# Command: serve
# ──────────────────────────────────────────────────────────────

def cmd_serve(args: argparse.Namespace) -> None:
    """Start the REST API server."""
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn not installed. Run: pip install uvicorn[standard]")
        sys.exit(1)

    os.environ.setdefault("MSRE_ENVIRONMENT", args.env)
    os.environ.setdefault("MSRE_CONFIG", args.config or f"python/configs/{args.env}.yaml")

    print(f"[MSRE] Starting API server | host={args.host}:{args.port} | env={args.env}")
    uvicorn.run(
        "macro_shock.orchestration.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info" if args.env != "production" else "warning",
        workers=1 if args.reload else args.workers,
    )


# ──────────────────────────────────────────────────────────────
# Command: health
# ──────────────────────────────────────────────────────────────

def cmd_health(args: argparse.Namespace) -> None:
    """Run system health check."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "scripts/health_check.py", "--env", args.env] +
        (["--verbose"] if args.verbose else []),
        cwd=Path(__file__).parent.parent.parent.parent,
    )
    sys.exit(result.returncode)


# ──────────────────────────────────────────────────────────────
# Command: demo
# ──────────────────────────────────────────────────────────────

def cmd_demo(args: argparse.Namespace) -> None:
    """Run a canned scenario demonstration."""
    import subprocess
    cmd = [
        sys.executable, "examples/run_end_to_end.py",
        "--env", args.env,
        "--event-scenario", args.scenario,
        "--stress-level", str(args.stress),
    ]
    if args.all:
        cmd = [sys.executable, "examples/run_end_to_end.py", "--env", args.env, "--run-all-scenarios"]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent.parent.parent)
    sys.exit(result.returncode)


# ──────────────────────────────────────────────────────────────
# Argument parser
# ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m macro_shock",
        description="Macro Shock Risk Engine — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env", default=os.getenv("MSRE_ENVIRONMENT", "research"),
                        choices=["research", "staging", "production"])
    parser.add_argument("--config", default=None, help="Path to YAML config file")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", default="console", choices=["console", "json"])

    sub = parser.add_subparsers(dest="command", required=True)

    # monitor
    p_mon = sub.add_parser("monitor", help="Continuous real-time event monitoring")
    p_mon.add_argument("--interval", type=int, default=30, help="Poll interval seconds")
    p_mon.set_defaults(func=cmd_monitor)

    # analyze
    p_ana = sub.add_parser("analyze", help="Analyze a single event")
    p_ana.add_argument("--event-file", help="Path to event JSON file")
    p_ana.add_argument("--title", help="Event title (if not using --event-file)")
    p_ana.add_argument("--institution", help="Institution name")
    p_ana.add_argument("--event-time", help="Event timestamp ISO 8601")
    p_ana.add_argument("--speaker", default="")
    p_ana.add_argument("--speaker-role", default="")
    p_ana.add_argument("--text", default="", help="Prepared remarks / raw text")
    p_ana.add_argument("--data", choices=["synthetic", "fred"], default="synthetic",
                       help="Market data source: synthetic (default) or fred (needs FRED_API_KEY)")
    p_ana.add_argument("--portfolio", default=None,
                       help="CSV of your positions (name,type,value[,sensitivity]) to stress-test; "
                            "see examples/portfolio_example.csv")
    p_ana.add_argument("--sectors", action="store_true",
                       help="Show sectors/styles that historically did best in the detected regime "
                            "(downloads French Data Library + FRED history)")
    p_ana.add_argument("--stress", type=float, default=0.35,
                       help="Market stress level 0-1 (synthetic data only)")
    p_ana.add_argument("--output", default=None, help="Write JSON output to file")
    p_ana.set_defaults(func=cmd_analyze)

    # backtest
    p_bt = sub.add_parser("backtest", help="Run historical event study backtest")
    p_bt.add_argument("--start", default=None)
    p_bt.add_argument("--end", default=None)
    p_bt.set_defaults(func=cmd_backtest)

    # serve
    p_fomc = sub.add_parser("fomc-study", help="Bitcoin/S&P 500/VIX reaction to FOMC (needs FRED_API_KEY)")
    p_fomc.add_argument("--start", default="2017-01-01", help="First announcement date (YYYY-MM-DD)")
    p_fomc.add_argument("--end", default="2025-12-31", help="Last announcement date (YYYY-MM-DD)")
    p_fomc.add_argument("--threshold", type=float, default=5.0,
                        help="2Y yield move (bps) that counts as a hawkish/dovish surprise")
    p_fomc.add_argument("--output", default=None, help="Write the per-event table to a CSV file")
    p_fomc.set_defaults(func=cmd_fomc_study)

    p_sec = sub.add_parser("sector-study", help="Sectors/styles by market regime (French Data Library + FRED)")
    p_sec.add_argument("--start", default="1990-01-01", help="First date (VIX history starts 1990)")
    p_sec.add_argument("--end", default=None, help="Last date (YYYY-MM-DD); default: latest data")
    p_sec.add_argument("--horizon", type=int, default=21, help="Window length in trading days (21 ~ 1 month)")
    p_sec.add_argument("--output", default=None, help="Write the full regime table to a CSV file")
    p_sec.set_defaults(func=cmd_sector_study)

    p_opt = sub.add_parser("optimize", help="Sector/Treasury/Bitcoin weights (French Data Library + FRED)")
    p_opt.add_argument("--objective", choices=["risk_parity", "min_variance"], default="risk_parity",
                       help="risk_parity: equal risk from each asset; min_variance: lowest volatility")
    p_opt.add_argument("--years", type=int, default=10, help="Lookback window in years")
    p_opt.add_argument("--max-weight", type=float, default=0.30, help="Per-asset cap (min_variance only)")
    p_opt.add_argument("--no-bitcoin", action="store_true", help="Leave Bitcoin out of the universe")
    p_opt.add_argument("--max-drawdown", type=float, default=None,
                       help="e.g. 0.25: pick the most stock-heavy mix whose historical drawdown stays "
                            "within -25%%")
    p_opt.add_argument("--recent-only", action="store_true",
                       help="With --max-drawdown, check the limit on the recent window only, not since 1990")
    p_opt.add_argument("--stocks", type=float, default=None,
                       help="Fixed share of the non-Bitcoin money in stocks (e.g. 0.7); bonds get the rest")
    p_opt.add_argument("--btc-weight", type=float, default=0.03,
                       help="Bitcoin weight with --max-drawdown or --stocks (default 3%%)")
    p_opt.add_argument("--tilt", type=float, default=0.25,
                       help="Regime tilt per evidence level: consistent +/-tilt, robust +/-2x tilt; 0 = off")
    p_opt.add_argument("--value", type=float, default=100000, help="Portfolio value for the output CSV")
    p_opt.add_argument("--output", default="portfolio_optimized.csv", help="Output CSV (analyze --portfolio format)")
    p_opt.set_defaults(func=cmd_optimize)

    p_day = sub.add_parser("daily", help="Daily market report: regime, alerts, evidence (needs FRED_API_KEY)")
    p_day.add_argument("--quick", action="store_true",
                       help="Skip the sector study (faster; no French Data Library download)")
    p_day.add_argument("--reports-dir", default=None,
                       help="Folder for reports and the CSV log (default: <project>/reports)")
    p_day.set_defaults(func=cmd_daily)

    p_srv = sub.add_parser("serve", help="Start the REST API server")
    p_srv.add_argument("--host", default="0.0.0.0")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.add_argument("--workers", type=int, default=2)
    p_srv.add_argument("--reload", action="store_true", help="Hot-reload (development only)")
    p_srv.set_defaults(func=cmd_serve)

    # health
    p_hc = sub.add_parser("health", help="System health check")
    p_hc.add_argument("--verbose", "-v", action="store_true")
    p_hc.set_defaults(func=cmd_health)

    # demo
    p_demo = sub.add_parser("demo", help="Run a canned scenario demonstration")
    p_demo.add_argument("--scenario", default="weekend_crisis",
                        choices=["weekend_crisis", "hawkish_surprise", "dovish_pivot", "friday_close_ambiguous"])
    p_demo.add_argument("--stress", type=float, default=0.4)
    p_demo.add_argument("--all", action="store_true", help="Run all scenarios")
    p_demo.set_defaults(func=cmd_demo)

    return parser


# Folder holding pyproject.toml: python/src/macro_shock/__main__.py -> parents[3]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_env() -> None:
    """
    Load a .env file (e.g. FRED_API_KEY): the first one found from the current
    folder upwards, else the project's own. Variables already set win.
    """
    from dotenv import find_dotenv, load_dotenv
    found = find_dotenv(usecwd=True)
    load_dotenv(found if found else _PROJECT_ROOT / ".env")


def main() -> None:
    _load_env()

    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level, args.log_format)
    args.func(args)


if __name__ == "__main__":
    main()
