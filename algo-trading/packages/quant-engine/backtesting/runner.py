"""
backtesting/runner.py — CLI entry point for running backtests.

Usage
-----
::

    # Run all strategies on AAPL and MSFT, 2020–2024
    python -m backtesting.runner \\
        --strategies all \\
        --tickers AAPL MSFT \\
        --start 2020-01-01 \\
        --end 2024-01-01 \\
        --capital 100000 \\
        --interval 1d

    # Walk-forward validation with 4 folds
    python -m backtesting.runner \\
        --walk-forward \\
        --n-splits 4 \\
        --tickers AAPL MSFT BTC-USD \\
        --start 2018-01-01 \\
        --end 2024-01-01

    # Save the report to a JSON file
    python -m backtesting.runner \\
        --tickers AAPL \\
        --start 2022-01-01 \\
        --end 2023-12-31 \\
        --output reports/aapl_2022_2023.json

Environment
-----------
Reads ``DATABASE_URL`` from the environment (or ``.env`` file via pydantic-settings).
Defaults to ``sqlite:///./algo_trading.db`` if not set.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _build_orchestrator(strategy_names: list[str], tickers: list[str], capital: float):
    """
    Build a StrategyOrchestrator from the requested strategy names.

    If ``strategy_names == ["all"]``, all 6 built-in strategies are included.

    Returns a configured StrategyOrchestrator ready for backtesting.
    """
    from config.settings import settings
    from strategies.orchestrator import StrategyOrchestrator
    from strategies.momentum import MomentumStrategy
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.stat_arb import StatArbStrategy
    from strategies.market_making import MarketMakingStrategy
    from strategies.sentiment import SentimentStrategy
    from strategies.macro_factor import MacroFactorStrategy

    _STRATEGY_MAP = {
        "momentum":       MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "stat_arb":       StatArbStrategy,
        "market_making":  MarketMakingStrategy,
        "sentiment":      SentimentStrategy,
    }

    if strategy_names == ["all"]:
        strategy_names = list(_STRATEGY_MAP.keys())

    built_strategies = []
    for name in strategy_names:
        if name == "stat_arb":
            # StatArbStrategy needs pairs, not plain tickers
            if len(tickers) >= 2:
                pairs = [(tickers[i], tickers[i + 1]) for i in range(0, len(tickers) - 1, 2)]
                built_strategies.append(StatArbStrategy(config={"enabled": True, "allocation_weight": 1.0}, pairs=pairs))
            else:
                logger.warning("stat_arb requires at least 2 tickers — skipping")
            continue
        if name not in _STRATEGY_MAP:
            logger.warning("Unknown strategy: %s — skipping", name)
            continue
        cfg = {"enabled": True, "allocation_weight": 1.0}
        built_strategies.append(_STRATEGY_MAP[name](config=cfg, tickers=tickers))

    macro_cfg = {"enabled": True, "allocation_weight": 0.0}
    macro = MacroFactorStrategy(config=macro_cfg, tickers=tickers)

    portfolio_config = {
        "max_position_pct": getattr(settings, "max_position_pct", 0.10)
    }

    return StrategyOrchestrator(
        strategies=built_strategies,
        macro_strategy=macro,
        total_capital=capital,
        config=portfolio_config,
    )


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///./algo_trading.db")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point for backtesting.runner.

    Returns
    -------
    int
        Exit code (0 = success, 1 = error).
    """
    parser = argparse.ArgumentParser(
        prog="python -m backtesting.runner",
        description="Run a backtest on historical data from the DataStore.",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["all"],
        metavar="NAME",
        help="Strategy names to include (default: all). "
             "Options: momentum, mean_reversion, stat_arb, market_making, sentiment",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        required=True,
        metavar="TICKER",
        help="Ticker symbols to backtest (e.g. AAPL MSFT BTC-USD)",
    )
    parser.add_argument(
        "--start",
        required=True,
        metavar="YYYY-MM-DD",
        help="Backtest start date (inclusive)",
    )
    parser.add_argument(
        "--end",
        required=True,
        metavar="YYYY-MM-DD",
        help="Backtest end date (inclusive)",
    )
    parser.add_argument(
        "--interval",
        default="1d",
        help="Bar interval (default: 1d). Options: 1d, 1h, 15m, 5m",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Initial capital in USD (default: 100,000)",
    )
    parser.add_argument(
        "--slippage-pct",
        type=float,
        default=0.0005,
        dest="slippage_pct",
        help="Slippage as a fraction of close price per side (default: 0.0005 = 5 bps)",
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=0.005,
        dest="commission",
        help="Commission per share/unit (default: $0.005)",
    )
    parser.add_argument(
        "--halt-drawdown",
        type=float,
        default=None,
        dest="halt_drawdown",
        help="Stop simulation if drawdown exceeds this fraction (e.g. 0.30 = 30%%)",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Save report to a JSON file (optional)",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        dest="walk_forward",
        help="Run walk-forward out-of-sample validation instead of single backtest",
    )
    parser.add_argument(
        "--n-splits",
        type=int,
        default=4,
        dest="n_splits",
        help="Number of walk-forward folds (default: 4; only used with --walk-forward)",
    )
    parser.add_argument(
        "--oos-days",
        type=int,
        default=252,
        dest="oos_days",
        help="OOS window size in days (default: 252; only used with --walk-forward)",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="DATABASE_URL",
        help="SQLAlchemy database URL (overrides DATABASE_URL env var)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    start_dt = _parse_date(args.start)
    end_dt = _parse_date(args.end)

    db_url = args.db or _get_database_url()
    logger.info("Using database: %s", db_url)

    # ── Imports after logging is configured ──────────────────────────────
    from data.store import DataStore
    from backtesting.broker import SimulatedBroker, FixedPercentageSlippage
    from backtesting.engine import BacktestEngine

    store = DataStore(db_url)
    broker = SimulatedBroker(
        slippage_model=FixedPercentageSlippage(pct=args.slippage_pct),
        commission_per_share=args.commission,
    )

    if args.walk_forward:
        # ── Walk-forward mode ─────────────────────────────────────────────
        from backtesting.walkforward import WalkForwardBacktest

        # Load all bars (training + OOS)
        all_bars: dict = {}
        for ticker in args.tickers:
            all_bars[ticker] = store.read_bars(ticker, args.interval, start_dt, end_dt)
            logger.info("Loaded %d bars for %s", len(all_bars[ticker]), ticker)

        def _orch_factory():
            return _build_orchestrator(args.strategies, args.tickers, args.capital)

        def _broker_factory():
            return SimulatedBroker(
                slippage_model=FixedPercentageSlippage(pct=args.slippage_pct),
                commission_per_share=args.commission,
            )

        wfb = WalkForwardBacktest(
            bars=all_bars,
            orchestrator_factory=_orch_factory,
            n_splits=args.n_splits,
            oos_size_days=args.oos_days,
            initial_capital=args.capital,
            broker_factory=_broker_factory,
        )
        results = wfb.run()
        agg = results.aggregate_metrics()
        print("\n=== Walk-Forward Aggregate Metrics ===")
        for k, v in agg.items():
            if isinstance(v, dict):
                print(f"  {k:<25}: mean={v['mean']:+.3f}  std={v['std']:.3f}")
            else:
                print(f"  {k:<25}: {v}")

        if args.output:
            import json
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(results.to_dict(), indent=2, default=str))
            logger.info("Walk-forward results saved to %s", args.output)

    else:
        # ── Single backtest mode ──────────────────────────────────────────
        orchestrator = _build_orchestrator(args.strategies, args.tickers, args.capital)

        engine = BacktestEngine.from_datastore(
            store=store,
            tickers=args.tickers,
            interval=args.interval,
            start=start_dt,
            end=end_dt,
            orchestrator=orchestrator,
            initial_capital=args.capital,
            broker=broker,
            halt_on_drawdown=args.halt_drawdown,
        )

        logger.info("Running backtest …")
        report = engine.run()
        print(report.summary())

        if args.output:
            report.save(args.output)
            logger.info("Report saved to %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
