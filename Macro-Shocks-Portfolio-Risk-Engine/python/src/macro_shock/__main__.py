"""
macro_shock/__main__.py

CLI entry point for the Macro Shock Risk Engine.
Enables: python -m macro_shock [command] [options]

Commands:
  monitor     Continuous real-time event monitoring (production mode)
  analyze     Analyze a single event from JSON or CLI flags
  backtest    Run historical event study backtest
  fomc-study  Bitcoin / S&P 500 / VIX reaction to FOMC announcements (FRED data)
  serve       Start the REST API server
  health      Run system health check
  demo        Run a canned scenario demonstration

Examples:
  python -m macro_shock monitor --config configs/production.yaml
  python -m macro_shock analyze --event-file event.json
  python -m macro_shock analyze --event-file event.json --data fred   # real FRED data
  python -m macro_shock backtest --start 2020-01-01 --end 2024-01-01
  python -m macro_shock fomc-study --output fomc_events.csv   # Bitcoin vs FOMC (FRED)
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
# Command: fomc-study
# ──────────────────────────────────────────────────────────────

def cmd_fomc_study(args: argparse.Namespace) -> None:
    """Event study of Bitcoin, S&P 500 and VIX reactions to FOMC announcements."""
    from datetime import date

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


def main() -> None:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))  # e.g. FRED_API_KEY from a local .env file

    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level, args.log_format)
    args.func(args)


if __name__ == "__main__":
    main()
