"""
execution/trading_engine.py — TradingEngine: the live paper/live trading loop.

Architecture
------------
The TradingEngine is an asyncio background task that runs continuously while
the API server is up.  Its job is to:

1. **Pull the latest completed bar** from the DataStore for each configured
   ticker (on the configured interval cadence).
2. **Build the feature matrix** for that ticker using the FeaturePipeline.
3. **Dispatch the bar** to the StrategyOrchestrator, which runs all enabled
   strategies and aggregates their signals into a final order list.
4. **Gate each order** through the RiskManager (halt check, position limits,
   daily-loss limit).
5. **Submit approved orders** to the ExecutionBroker (PaperBroker in
   paper/dev mode; AlpacaBroker/BinanceBroker in live mode).
6. **Update portfolio state** and broadcast fills + portfolio snapshots to
   all connected WebSocket clients.
7. **Update the DrawdownMonitor** so the risk circuit-breakers fire
   automatically if equity degrades.

Loop cadence
------------
The loop fires on a fixed interval that matches the configured bar interval:

    bar_interval = "1d"  → fires once per day (at market close + 5 min offset)
    bar_interval = "1h"  → fires once per hour
    bar_interval = "1m"  → fires every minute (only in paper/live mode)

In dev mode the loop always uses ``poll_seconds=60`` regardless of bar
interval so the developer can watch signals appear without waiting 24 hours.

The loop does NOT re-process bars it has already seen — it tracks the
``last_processed_timestamp`` per ticker so repeated fires are idempotent.

Usage
-----
The engine is created and managed by the FastAPI lifespan in ``api/main.py``:

::

    engine = TradingEngine(
        store=state.data_store,
        orchestrator=state.orchestrator,
        broker=state.broker,
        risk_manager=state.risk_manager,
        monitor=state.monitor,
        portfolio=portfolio,
        tickers=["AAPL", "MSFT", "NVDA", "BTC-USD"],
        bar_interval="1d",
    )
    await engine.start()
    # ... server running ...
    await engine.stop()

Control endpoints (``POST /api/trading/start`` and ``POST /api/trading/stop``)
call ``engine.start()`` and ``engine.stop()`` at runtime without restarting
the API server.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# How many historical bars to load for feature computation on each tick.
# The feature pipeline needs at least 200 bars for all indicators to be
# well-defined (RSI-14, MACD, BB-20, etc.).
_FEATURE_LOOKBACK_BARS = 300

# Mapping from bar interval string → approximate seconds per bar.
# Used to compute how long to sleep between loop iterations.
_INTERVAL_SECONDS: dict[str, int] = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "30m": 1800,
    "1h":  3600,
    "4h":  14400,
    "1d":  86400,
    "1w":  604800,
}

# In dev mode, override cadence to this many seconds so the loop is
# observable without waiting a full bar interval.
_DEV_POLL_SECONDS = 60


class TradingEngine:
    """
    Live trading loop that dispatches bars → strategies → orders → fills.

    Parameters
    ----------
    store : DataStore
        DataStore instance used to read bars and news.
    orchestrator : StrategyOrchestrator
        Aggregates strategy signals into orders.
    broker : ExecutionBroker
        PaperBroker (paper/dev) or live broker.
    risk_manager : RiskManager, optional
        If provided, gates every order before it reaches the broker.
    monitor : DrawdownMonitor, optional
        If provided, receives equity updates after every bar.
    portfolio : Portfolio, optional
        If provided, receives fills and marks prices each bar.  When None
        a fresh in-memory Portfolio is created on ``start()``.
    feature_pipeline : FeaturePipeline, optional
        If None, an empty feature DataFrame is passed to strategies.
    tickers : list[str]
        Universe of tickers to trade.
    bar_interval : str
        Bar interval used to both read bars and schedule the loop cadence.
    trading_mode : str
        ``"dev"`` | ``"paper"`` | ``"live"``.  Affects poll cadence.
    initial_capital : float
        Used to create a Portfolio if none is provided.
    """

    def __init__(
        self,
        store: Any,
        orchestrator: Any,
        broker: Any,
        risk_manager: Any = None,
        monitor: Any = None,
        portfolio: Any = None,
        feature_pipeline: Any = None,
        tickers: list[str] | None = None,
        bar_interval: str = "1d",
        trading_mode: str = "paper",
        initial_capital: float = 100_000.0,
        app_state: Any = None,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.broker = broker
        self.risk_manager = risk_manager
        self.monitor = monitor
        self.bar_interval = bar_interval
        self.trading_mode = trading_mode
        self.initial_capital = initial_capital
        self.tickers: list[str] = list(tickers or [])
        # Reference to AppState for populating latest_signals / equity_history
        self._app_state = app_state

        # Portfolio — create fresh if not supplied
        if portfolio is not None:
            self.portfolio = portfolio
        else:
            from backtesting.portfolio import Portfolio as BtPortfolio
            self.portfolio = BtPortfolio(initial_capital=initial_capital)

        # Feature pipeline — create a basic one if not supplied
        if feature_pipeline is not None:
            self._feature_pipeline = feature_pipeline
        else:
            try:
                from features.pipeline import FeaturePipeline
                self._feature_pipeline = FeaturePipeline(
                    store=store,
                    include_technical=True,
                    include_fundamental=False,   # off by default; slow on first run
                    include_sentiment=True,
                    include_macro=False,         # off by default; needs internet
                )
            except Exception as exc:
                logger.warning("FeaturePipeline init failed: %s — using empty features", exc)
                self._feature_pipeline = None

        # Per-ticker tracking: last bar timestamp we processed
        self._last_processed: dict[str, datetime] = {}

        # Engine state
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._loop_count: int = 0

        # WebSocket broadcaster (imported lazily to avoid circular imports)
        self._ws_manager: Any = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background trading loop."""
        if self._running:
            logger.warning("TradingEngine already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="trading_engine_loop")
        logger.info(
            "TradingEngine started (mode=%s interval=%s tickers=%s)",
            self.trading_mode, self.bar_interval, self.tickers,
        )
        try:
            from api.ws.feed import broadcast_trading_status
            await broadcast_trading_status(running=True, mode=self.trading_mode)
        except Exception:
            pass

    async def stop(self) -> None:
        """Stop the background trading loop gracefully."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TradingEngine stopped (loops completed=%d)", self._loop_count)
        try:
            from api.ws.feed import broadcast_trading_status
            await broadcast_trading_status(running=False, mode=self.trading_mode)
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._running and (self._task is not None and not self._task.done())

    @property
    def loop_count(self) -> int:
        return self._loop_count

    @property
    def status(self) -> dict[str, Any]:
        """Return a status snapshot for the /api/trading/status endpoint."""
        return {
            "running": self.is_running,
            "trading_mode": self.trading_mode,
            "bar_interval": self.bar_interval,
            "tickers": self.tickers,
            "loop_count": self._loop_count,
            "last_processed": {
                t: ts.isoformat() for t, ts in self._last_processed.items()
            },
            "portfolio": {
                "cash": round(getattr(self.portfolio, "cash", 0.0), 2),
                "total_equity": round(
                    getattr(self.portfolio, "total_equity", 0.0), 2
                ),
            },
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """
        Main trading loop.  Runs until ``stop()`` is called.

        Each iteration:
        1. Sleeps until the next bar boundary.
        2. For every ticker, reads the latest unprocessed bar from the store.
        3. Builds features, dispatches to orchestrator, submits approved orders.
        4. Broadcasts portfolio + fill events to WebSocket clients.
        """
        poll_seconds = self._compute_poll_seconds()
        logger.info("TradingEngine loop starting (poll_seconds=%d)", poll_seconds)

        while self._running:
            try:
                await self._tick()
                self._loop_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("TradingEngine tick error: %s", exc)

            # Sleep until next iteration
            try:
                await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """Execute one full bar-processing iteration across all tickers."""
        now = datetime.now(UTC)
        loop = asyncio.get_event_loop()

        for ticker in self.tickers:
            try:
                await self._process_ticker(ticker, now, loop)
            except Exception as exc:
                logger.error("TradingEngine: error processing %s: %s", ticker, exc)

        # Broadcast consolidated portfolio update after processing all tickers
        await self._broadcast_portfolio()

    async def _process_ticker(
        self, ticker: str, now: datetime, loop: asyncio.AbstractEventLoop
    ) -> None:
        """
        Load the latest bar for a ticker, build features, and run strategies.
        """
        # ── 1. Load latest bar from store ────────────────────────────────────
        lookback_start = now - timedelta(
            seconds=_INTERVAL_SECONDS.get(self.bar_interval, 86400)
            * _FEATURE_LOOKBACK_BARS
        )

        bars = await loop.run_in_executor(
            None,
            lambda: self.store.read_bars(
                ticker, self.bar_interval, lookback_start, now
            ),
        )

        if not bars:
            logger.debug("TradingEngine: no bars for %s", ticker)
            return

        latest_bar = bars[-1]
        latest_ts = latest_bar.event_timestamp

        # Skip if we've already processed this bar
        if self._last_processed.get(ticker) == latest_ts:
            logger.debug("TradingEngine: %s already processed at %s", ticker, latest_ts)
            # Broadcast a skipped-tick so the dashboard shows the engine is polling
            close_price = latest_bar.close
            equity = getattr(self.portfolio, "total_equity", self.initial_capital)
            try:
                from api.ws.feed import broadcast_engine_tick
                await broadcast_engine_tick(
                    ticker=ticker,
                    close=close_price,
                    orders=0,
                    equity=equity,
                    bar_ts=latest_ts.isoformat(),
                    skipped=True,
                    skip_reason="already processed",
                )
            except Exception:
                pass
            return

        # ── 2. Build feature matrix ───────────────────────────────────────────
        features = await self._build_features(ticker, bars, loop)

        # ── 3. Update broker prices ───────────────────────────────────────────
        close_price = latest_bar.close
        self.broker.update_prices({ticker: close_price})
        if self.risk_manager is not None:
            prices = [b.close for b in bars[-60:]]
            self.risk_manager.update_price_history(ticker, prices)

        # ── 4. Check pending limit/stop orders ────────────────────────────────
        if hasattr(self.broker, "check_pending_orders"):
            pending_fills = self.broker.check_pending_orders()
            for fill in pending_fills:
                await self._handle_fill(fill, latest_ts)

        # ── 5. Convert bar to pd.Series and dispatch to orchestrator ──────────
        bar_series = pd.Series({
            "open": latest_bar.open,
            "high": latest_bar.high,
            "low": latest_bar.low,
            "close": latest_bar.close,
            "volume": latest_bar.volume,
        })

        orders = self.orchestrator.process_bar(ticker, bar_series, features)

        # ── 6. Cache signals for /api/signals REST endpoint ───────────────────
        if orders and self._app_state is not None:
            for order in orders:
                sig_dict = {
                    "ticker": order.ticker,
                    "strategy_id": order.strategy_id,
                    "signal": round(1.0 if order.side.value == "buy" else -1.0, 4),
                    "confidence": round(order.confidence, 4),
                    "timestamp": latest_ts.isoformat(),
                }
                self._app_state.latest_signals.append(sig_dict)
                # Keep only the last 500 signals in memory
                if len(self._app_state.latest_signals) > 500:
                    self._app_state.latest_signals.pop(0)

        # ── 7. Gate orders through RiskManager and submit ─────────────────────
        for order in orders:
            await self._submit_order(order, latest_ts)

        # ── 8. Mark portfolio to market ───────────────────────────────────────
        self.portfolio.mark(prices={ticker: close_price}, timestamp=latest_ts)
        equity = getattr(self.portfolio, "total_equity", self.initial_capital)

        # ── 9. Update orchestrator capital + DrawdownMonitor ─────────────────
        self.orchestrator.update_capital(equity)
        if self.monitor is not None:
            alert = self.monitor.update(current_equity=equity, timestamp=latest_ts)
            if alert.halt_triggered:
                logger.critical(
                    "TradingEngine: HALT triggered for %s — %s", ticker, alert.reason
                )
                await self._broadcast_risk_alert(alert)

        # ── 10. Populate equity_history in app state ──────────────────────────
        if self._app_state is not None:
            self._app_state.equity_history.append(equity)
            # Keep rolling window of 2000 points
            if len(self._app_state.equity_history) > 2000:
                self._app_state.equity_history.pop(0)

        # ── 11. Record processed timestamp ───────────────────────────────────
        self._last_processed[ticker] = latest_ts

        logger.info(
            "TradingEngine tick: %s close=%.4f equity=%.2f orders=%d",
            ticker, close_price, equity, len(orders),
        )

        # ── 12. Broadcast tick to WS clients ──────────────────────────────────
        try:
            from api.ws.feed import broadcast_engine_tick
            await broadcast_engine_tick(
                ticker=ticker,
                close=close_price,
                orders=len(orders),
                equity=equity,
                bar_ts=latest_ts.isoformat(),
                skipped=False,
            )
        except Exception:
            pass

    async def _build_features(
        self,
        ticker: str,
        bars: list,
        loop: asyncio.AbstractEventLoop,
    ) -> pd.DataFrame:
        """Build features for the latest bar window."""
        if self._feature_pipeline is None or not bars:
            return pd.DataFrame()

        start = bars[0].event_timestamp
        end = bars[-1].event_timestamp

        # Build the OHLCV DataFrame from in-memory bars (avoid a second DB read)
        rows = [
            {"open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume}
            for b in bars
        ]
        idx = pd.DatetimeIndex([b.event_timestamp for b in bars])
        ohlcv_df = pd.DataFrame(rows, index=idx).sort_index()

        try:
            features = await loop.run_in_executor(
                None,
                lambda: self._feature_pipeline.build(
                    ticker=ticker,
                    start=start,
                    end=end,
                    interval=self.bar_interval,
                    ohlcv_df=ohlcv_df,
                ),
            )
            return features
        except Exception as exc:
            logger.warning("TradingEngine: feature build failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    async def _submit_order(self, order: Any, bar_ts: datetime) -> None:
        """Gate order through RiskManager (if present) and submit to broker."""
        if self.risk_manager is not None:
            decision = self.risk_manager.check_order(order, self.portfolio)
            if not decision.approved:
                logger.info(
                    "TradingEngine: order rejected [%s] %s %s %.4f — %s",
                    decision.check_name, order.side, order.ticker,
                    order.quantity, decision.reason,
                )
                return
            order_to_submit = decision.order
        else:
            order_to_submit = order

        try:
            fill = self.broker.submit_order(order_to_submit)
        except Exception as exc:
            logger.error("TradingEngine: broker submit_order failed: %s", exc)
            return

        from execution.base import OrderStatus
        if fill.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
            await self._handle_fill(fill, bar_ts)

    async def _handle_fill(self, fill: Any, bar_ts: datetime) -> None:
        """Apply fill to portfolio, update orchestrator, broadcast to WS."""
        # The fill from PaperBroker (execution/base.FillEvent) is different
        # from backtesting.events.FillEvent — adapt it to the Portfolio's API.
        try:
            # Portfolio.on_fill expects backtesting.events.FillEvent
            # Construct one from the execution FillEvent
            from backtesting.events import FillEvent as BtFillEvent
            bt_fill = BtFillEvent(
                timestamp=getattr(fill, "timestamp", bar_ts),
                ticker=fill.ticker,
                side=fill.side,
                quantity=fill.filled_quantity,
                fill_price=fill.fill_price,
                commission=fill.commission,
                strategy_id=fill.strategy_id,
                order_id=getattr(fill, "broker_order_id", ""),
                slippage=getattr(fill, "slippage", 0.0),
            )
            self.portfolio.on_fill(bt_fill)
        except Exception as exc:
            logger.warning("TradingEngine: portfolio.on_fill failed: %s", exc)

        # Update orchestrator position tracking
        try:
            delta = fill.filled_quantity if fill.side == "buy" else -fill.filled_quantity
            self.orchestrator.update_position(fill.ticker, delta)
            self.orchestrator.update_capital(
                getattr(self.portfolio, "total_equity", self.initial_capital)
            )
        except Exception as exc:
            logger.warning("TradingEngine: orchestrator position update failed: %s", exc)

        # Broadcast fill event to WebSocket clients
        await self._broadcast_fill(fill)

        logger.info(
            "TradingEngine FILL: %s %s %.4f @ %.4f (comm=%.4f)",
            fill.side.upper(), fill.ticker, fill.filled_quantity,
            fill.fill_price, fill.commission,
        )

    # ── WebSocket helpers ─────────────────────────────────────────────────────

    def _get_ws_manager(self) -> Any:
        """Lazily import the WS manager singleton to avoid circular imports."""
        if self._ws_manager is None:
            try:
                from api.ws.feed import manager
                self._ws_manager = manager
            except Exception:
                pass
        return self._ws_manager

    async def _broadcast_fill(self, fill: Any) -> None:
        mgr = self._get_ws_manager()
        if mgr is None:
            return
        try:
            from api.ws.feed import broadcast_fill
            await broadcast_fill(fill.to_dict() if hasattr(fill, "to_dict") else {
                "ticker": fill.ticker,
                "side": fill.side,
                "quantity": fill.filled_quantity,
                "fill_price": fill.fill_price,
                "commission": fill.commission,
                "strategy_id": fill.strategy_id,
                "timestamp": datetime.now(UTC).isoformat(),
            })
        except Exception as exc:
            logger.debug("TradingEngine: broadcast_fill error: %s", exc)

    async def _broadcast_portfolio(self) -> None:
        try:
            from api.ws.feed import broadcast_portfolio_update
            equity = getattr(self.portfolio, "total_equity", 0.0)
            cash = getattr(self.portfolio, "cash", 0.0)
            await broadcast_portfolio_update(equity=equity, cash=cash)
        except Exception as exc:
            logger.debug("TradingEngine: broadcast_portfolio error: %s", exc)

    async def _broadcast_risk_alert(self, alert: Any) -> None:
        try:
            from api.ws.feed import broadcast_risk_alert
            await broadcast_risk_alert({
                "halt_triggered": alert.halt_triggered,
                "reason": alert.reason,
                "alert_type": getattr(alert, "alert_type", ""),
                "current_equity": round(alert.current_equity, 2),
                "drawdown_pct": round(alert.drawdown_pct * 100, 3),
                "daily_loss_pct": round(alert.daily_loss_pct * 100, 3),
            })
        except Exception as exc:
            logger.debug("TradingEngine: broadcast_risk_alert error: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_poll_seconds(self) -> int:
        """
        Return the number of seconds to sleep between loop iterations.

        Dev and paper modes both use 60 s so that the engine is observable
        without waiting a full bar interval (1 h or 1 d).  Live mode uses the
        true bar cadence to avoid excessive API calls.
        """
        if self.trading_mode in ("dev", "paper"):
            return _DEV_POLL_SECONDS
        return _INTERVAL_SECONDS.get(self.bar_interval, 3600)
