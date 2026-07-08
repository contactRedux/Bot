"""
backtesting/engine.py — BacktestEngine: the main simulation loop.

Architecture
------------
The BacktestEngine is an event-driven simulator.  It:

1. Loads historical bars from a ``DataStore`` (or accepts them directly).
2. Builds a sorted list of ``BarEvent`` objects in strict chronological order.
3. On each bar, it:
   a. Checks the broker for pending limit/stop order fills (``broker.process_bar``).
   b. Builds the feature matrix up to this bar using the ``FeaturePipeline``.
   c. Calls ``orchestrator.process_bar(ticker, bar, features)`` to get ``Order`` objects.
   d. Wraps each Order in an ``OrderEvent`` and calls ``broker.process_order``.
   e. Passes each ``FillEvent`` to the ``portfolio`` and ``orchestrator``.
   f. Marks the portfolio to market at the bar's close price.
4. At the end, computes metrics and returns a ``BacktestReport``.

Look-ahead bias prevention
--------------------------
Look-ahead bias is the most common source of false alpha in backtesting.
The engine prevents it by:

* Sorting all events by ``event_timestamp`` before the loop starts.
* Slicing the feature matrix to include only rows up to (and including)
  the current bar — ``features.iloc[:current_index + 1]``.
* Using ``bar.close`` (not the *next* bar's open) as the fill price.

``step()`` method for RL integration
--------------------------------------
In addition to ``run()``, the engine exposes a ``step(action)`` method that
advances the simulation by one bar and returns ``(obs, reward, done, info)``.
This is the interface used by the RL ``TradingEnv`` in Sub-Task 4.

Usage — full backtest
---------------------
::

    from backtesting.engine import BacktestEngine
    from data.store import DataStore

    store = DataStore("sqlite:///./algo_trading.db")
    report = BacktestEngine.from_datastore(
        store=store,
        tickers=["AAPL", "MSFT"],
        interval="1d",
        start=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, tzinfo=timezone.utc),
        orchestrator=orchestrator,
        initial_capital=100_000.0,
    ).run()
    print(report.metrics)

Usage — step mode (RL)
----------------------
::

    engine = BacktestEngine(bars=bars_dict, orchestrator=orchestrator)
    obs = engine.reset()
    while True:
        obs, reward, done, info = engine.step()
        if done:
            break
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backtesting.broker import SimulatedBroker
from backtesting.events import BarEvent, FillEvent, HaltEvent, OrderEvent
from backtesting.metrics import compute_metrics
from backtesting.portfolio import Portfolio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Event-driven backtesting engine.

    Parameters
    ----------
    bars : dict[str, list[OHLCVBar]]
        Historical bars per ticker.  Loaded from DataStore or provided directly.
    orchestrator : StrategyOrchestrator
        Aggregates strategy signals into orders.
    feature_pipeline : FeaturePipeline, optional
        If provided, features are built per-ticker on each bar.
        If None, an empty DataFrame is passed to strategies.
    initial_capital : float
        Starting portfolio cash.
    broker : SimulatedBroker, optional
        Custom broker instance.  Defaults to standard SimulatedBroker.
    bar_interval : str
        Bar interval string (``"1d"`` by default).  Used for metrics
        annualisation (252 days, 365 for crypto).
    halt_on_drawdown : float | None
        If set, stop the simulation if portfolio drawdown exceeds this
        fraction.  E.g. 0.30 halts if portfolio drops 30% from peak.
    """

    def __init__(
        self,
        bars: "dict[str, list[Any]]",  # OHLCVBar list per ticker
        orchestrator: "StrategyOrchestrator",  # noqa: F821
        feature_pipeline: "FeaturePipeline | None" = None,  # noqa: F821
        initial_capital: float = 100_000.0,
        broker: SimulatedBroker | None = None,
        bar_interval: str = "1d",
        halt_on_drawdown: float | None = None,
    ) -> None:
        self.bars = bars
        self.orchestrator = orchestrator
        self.feature_pipeline = feature_pipeline
        self.initial_capital = initial_capital
        self.broker = broker or SimulatedBroker()
        self.bar_interval = bar_interval
        self.halt_on_drawdown = halt_on_drawdown

        self.portfolio = Portfolio(initial_capital=initial_capital)

        # Build a flat sorted list of BarEvents from all tickers
        self._bar_events: list[BarEvent] = self._build_bar_events()
        # Index into _bar_events for step mode
        self._step_idx: int = 0
        # Cache feature DataFrames per ticker (built once, sliced per bar)
        self._feature_cache: dict[str, pd.DataFrame] = {}
        # Per-ticker bar DataFrames (for feature pipeline and slicing)
        self._bar_dfs: dict[str, pd.DataFrame] = {}
        # Per-ticker bar index (how many bars we've seen for each ticker)
        self._ticker_bar_count: dict[str, int] = {}

        self._halted: bool = False
        self._halt_reason: str = ""

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_datastore(
        cls,
        store: "DataStore",  # noqa: F821
        tickers: list[str],
        interval: str,
        start: datetime,
        end: datetime,
        orchestrator: "StrategyOrchestrator",  # noqa: F821
        feature_pipeline: "FeaturePipeline | None" = None,  # noqa: F821
        initial_capital: float = 100_000.0,
        broker: SimulatedBroker | None = None,
        halt_on_drawdown: float | None = None,
    ) -> "BacktestEngine":
        """
        Construct a BacktestEngine by loading bars from a DataStore.

        Parameters
        ----------
        store : DataStore
        tickers : list[str]
        interval : str          Bar duration (e.g. ``"1d"``).
        start, end : datetime   UTC date range.
        orchestrator, feature_pipeline, initial_capital, broker, halt_on_drawdown
            Passed directly to ``__init__``.
        """
        bars: dict[str, list] = {}
        for ticker in tickers:
            ticker_bars = store.read_bars(ticker, interval, start, end)
            if not ticker_bars:
                logger.warning("No bars found for %s [%s, %s]", ticker, start, end)
            bars[ticker] = ticker_bars
            logger.info("Loaded %d bars for %s", len(ticker_bars), ticker)

        return cls(
            bars=bars,
            orchestrator=orchestrator,
            feature_pipeline=feature_pipeline,
            initial_capital=initial_capital,
            broker=broker or SimulatedBroker(),
            bar_interval=interval,
            halt_on_drawdown=halt_on_drawdown,
        )

    # ── Full backtest ─────────────────────────────────────────────────────

    def run(self) -> "BacktestReport":  # noqa: F821
        """
        Run the full backtest from start to end.

        Returns
        -------
        BacktestReport
            Contains the equity curve, trade log, and computed metrics.
        """
        from backtesting.report import BacktestReport

        self.reset()

        n_bars = len(self._bar_events)
        logger.info(
            "BacktestEngine.run(): %d bar events across %d tickers",
            n_bars, len(self.bars),
        )

        for idx, bar_event in enumerate(self._bar_events):
            if self._halted:
                logger.info("Simulation halted: %s", self._halt_reason)
                break

            self._process_bar_event(bar_event)

            if (idx + 1) % 500 == 0:
                logger.debug(
                    "Progress: %d/%d bars | equity=%.2f",
                    idx + 1, n_bars, self.portfolio.total_equity,
                )

        # Force-close any remaining open positions at last price
        # (optional: some backtests prefer to leave positions open)

        return self._build_report()

    # ── Step mode (RL interface) ──────────────────────────────────────────

    def reset(self) -> np.ndarray:
        """
        Reset the engine to the start of the simulation.

        Returns
        -------
        np.ndarray
            Initial observation vector (for RL compatibility).
        """
        self.portfolio.reset()
        self.broker.reset()
        self.orchestrator.reset()
        self._step_idx = 0
        self._ticker_bar_count.clear()
        self._feature_cache.clear()
        self._halted = False
        self._halt_reason = ""
        self._bar_dfs.clear()
        logger.debug("BacktestEngine.reset()")
        return self._get_obs()

    def step(self, action: int | None = None) -> tuple[np.ndarray, float, bool, dict]:
        """
        Advance the simulation by one bar.

        Compatible with the Gymnasium ``Env.step()`` interface for use in RL
        training.  The ``action`` parameter is ignored in standard backtesting
        (strategies drive their own decisions).  When used from ``TradingEnv``,
        the RL action is injected via the orchestrator's active strategy.

        Parameters
        ----------
        action : int, optional
            RL action (0-6).  Ignored in strategy-driven backtesting.

        Returns
        -------
        obs : np.ndarray
            Observation vector.
        reward : float
            Incremental Sharpe-like reward (PnL normalised by rolling vol).
        done : bool
            True when all bars have been processed.
        info : dict
            Diagnostic info: price, equity, n_fills, etc.
        """
        if self._step_idx >= len(self._bar_events) or self._halted:
            return self._get_obs(), 0.0, True, {}

        equity_before = self.portfolio.total_equity
        bar_event = self._bar_events[self._step_idx]
        self._process_bar_event(bar_event)
        self._step_idx += 1

        equity_after = self.portfolio.total_equity
        pnl = equity_after - equity_before
        reward = self._compute_step_reward(pnl)

        done = self._step_idx >= len(self._bar_events) or self._halted
        info = {
            "timestamp": bar_event.timestamp,
            "ticker": bar_event.ticker,
            "price": bar_event.close,
            "equity": equity_after,
            "cash": self.portfolio.cash,
            "n_trades": len(self.portfolio.trade_log),
        }
        return self._get_obs(), reward, done, info

    # ── Internal bar processing ────────────────────────────────────────────

    def _process_bar_event(self, bar_event: BarEvent) -> None:
        """Process a single BarEvent through the full pipeline."""
        ticker = bar_event.ticker

        # 1. Process any pending limit/stop orders for this ticker before
        #    seeing the new bar price (correct temporal ordering)
        pending_fills = self.broker.process_bar(ticker, bar_event)
        for fill in pending_fills:
            self._handle_fill(fill)

        # 2. Build features up to (but not past) this bar
        features = self._get_features(ticker, bar_event)

        # 3. Convert BarEvent to pd.Series for orchestrator
        bar_series = pd.Series(bar_event.to_series())

        # 4. Run all strategies — get aggregated orders
        orders = self.orchestrator.process_bar(ticker, bar_series, features)

        # 5. Submit orders to broker
        for order in orders:
            order_event = self._order_to_event(order, bar_event.timestamp)
            fills = self.broker.process_order(order_event, bar_event)
            for fill in fills:
                self._handle_fill(fill)

        # 6. Mark portfolio to market at bar close
        self.portfolio.mark(
            prices={ticker: bar_event.close},
            timestamp=bar_event.timestamp,
        )

        # 7. Check halt condition
        if self.halt_on_drawdown is not None:
            curve = self.portfolio.equity_curve
            if len(curve) >= 2:
                peak = max(eq for _, eq in curve)
                current = curve[-1][1]
                if peak > 0 and (peak - current) / peak >= self.halt_on_drawdown:
                    self._halted = True
                    self._halt_reason = (
                        f"Drawdown {(peak - current)/peak:.1%} exceeded halt "
                        f"threshold {self.halt_on_drawdown:.1%}"
                    )

    def _handle_fill(self, fill: FillEvent) -> None:
        """Apply a fill to the portfolio and notify the orchestrator."""
        self.portfolio.on_fill(fill)
        # Update orchestrator's position tracking
        delta = fill.quantity if fill.side == "buy" else -fill.quantity
        self.orchestrator.update_position(fill.ticker, delta)
        self.orchestrator.update_capital(self.portfolio.total_equity)

    def _get_features(self, ticker: str, bar_event: BarEvent) -> pd.DataFrame:
        """
        Return the feature matrix for a ticker up to and including this bar.

        If a FeaturePipeline is available, it builds features from all bars
        seen so far.  Otherwise, returns an empty DataFrame.
        """
        if self.feature_pipeline is None:
            return pd.DataFrame()

        # Build bar DataFrame from the raw bars list up to current bar
        if ticker not in self._bar_dfs:
            raw_bars = self.bars.get(ticker, [])
            if raw_bars:
                data = {
                    "open":   [b.open for b in raw_bars],
                    "high":   [b.high for b in raw_bars],
                    "low":    [b.low for b in raw_bars],
                    "close":  [b.close for b in raw_bars],
                    "volume": [b.volume for b in raw_bars],
                }
                idx = pd.DatetimeIndex([b.event_timestamp for b in raw_bars])
                self._bar_dfs[ticker] = pd.DataFrame(data, index=idx)
            else:
                return pd.DataFrame()

        count = self._ticker_bar_count.get(ticker, 0) + 1
        self._ticker_bar_count[ticker] = count

        bar_df = self._bar_dfs[ticker].iloc[:count]

        # Use cached features if already computed up to this length
        cache_key = f"{ticker}:{count}"
        if cache_key not in self._feature_cache:
            try:
                self._feature_cache[cache_key] = self.feature_pipeline.build(bar_df)
            except Exception as e:
                logger.debug("Feature build failed for %s at bar %d: %s", ticker, count, e)
                return pd.DataFrame()

        return self._feature_cache[cache_key]

    # ── Utility ───────────────────────────────────────────────────────────

    def _build_bar_events(self) -> list[BarEvent]:
        """
        Flatten all ticker bars into a single time-sorted list of BarEvents.

        Within the same timestamp, bars are ordered alphabetically by ticker
        for determinism.
        """
        events: list[BarEvent] = []
        for ticker, ticker_bars in self.bars.items():
            for bar in ticker_bars:
                events.append(BarEvent(
                    timestamp=bar.event_timestamp,
                    ticker=ticker,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    interval=self.bar_interval,
                ))
        # Sort by (timestamp, ticker) for deterministic ordering
        events.sort(key=lambda e: (e.timestamp, e.ticker))
        return events

    def _order_to_event(self, order: Any, timestamp: datetime) -> OrderEvent:
        """Convert a strategy Order to an OrderEvent."""
        return OrderEvent(
            timestamp=timestamp,
            ticker=order.ticker,
            side=order.side.value if hasattr(order.side, "value") else str(order.side),
            quantity=order.quantity,
            order_type=(
                order.order_type.value
                if hasattr(order.order_type, "value")
                else str(order.order_type)
            ),
            strategy_id=order.strategy_id,
            confidence=order.confidence,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            order_id=str(uuid.uuid4()),
            metadata=dict(order.metadata) if order.metadata else {},
        )

    def _get_obs(self) -> np.ndarray:
        """Return a simple observation vector for RL step mode."""
        equity = self.portfolio.total_equity
        cash_ratio = self.portfolio.cash / (equity + 1e-8)
        unrealised = self.portfolio.unrealised_pnl / (self.initial_capital + 1e-8)
        return np.array([equity / self.initial_capital, cash_ratio, unrealised], dtype=np.float32)

    def _compute_step_reward(self, pnl: float) -> float:
        """Incremental Sharpe-like reward for RL step mode."""
        curve = self.portfolio.equity_curve
        if len(curve) < 10:
            return 0.0
        recent_equities = [eq for _, eq in curve[-20:]]
        returns = [
            (recent_equities[i] - recent_equities[i - 1]) / (recent_equities[i - 1] + 1e-8)
            for i in range(1, len(recent_equities))
        ]
        vol = float(np.std(returns)) + 1e-6
        return float(pnl / (self.initial_capital * vol))

    def _build_report(self) -> "BacktestReport":  # noqa: F821
        """Compute metrics and assemble the BacktestReport."""
        from backtesting.report import BacktestReport

        equity_curve = self.portfolio.equity_curve
        trade_log = self.portfolio.trade_log
        strategy_attr = self.portfolio.strategy_pnl_attribution()

        # Determine annualisation factor
        days_per_year = 252 if "d" in self.bar_interval else 365

        metrics = compute_metrics(
            equity_curve=equity_curve,
            trade_log=trade_log,
            initial_capital=self.initial_capital,
            strategy_attribution=strategy_attr,
            trading_days_per_year=days_per_year,
        )

        return BacktestReport(
            metrics=metrics,
            equity_curve=self.portfolio.equity_series(),
            trade_log=trade_log,
            strategy_attribution=strategy_attr,
            tickers=list(self.bars.keys()),
            bar_interval=self.bar_interval,
            halted=self._halted,
            halt_reason=self._halt_reason,
            initial_capital=self.initial_capital,
            positions_at_close=self.portfolio.positions_snapshot(),
        )
