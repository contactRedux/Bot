"""
strategies/base.py — BaseStrategy interface and Order/Signal dataclasses.

Every strategy in the ``strategies`` package extends ``BaseStrategy``.  The
common interface guarantees the ``StrategyOrchestrator`` can treat all
strategies uniformly regardless of their internal implementation.

Order lifecycle
---------------
A strategy emits ``Order`` objects via ``generate_orders()``.  Orders flow:

    BaseStrategy.generate_orders()
        → StrategyOrchestrator (aggregation + weighting)
        → RiskManager (position limit checks — Sub-Task 7)
        → ExecutionBroker (paper / Alpaca / Binance — Sub-Task 8)

The strategy never directly submits orders to a broker — it only expresses
intent.  Risk and execution layers can modify or veto orders downstream.

Bar / event model
-----------------
Each strategy exposes three event hooks that the orchestrator calls on every
simulation step:

    on_bar(bar)             — new OHLCV bar available
    on_news(article)        — new scored NewsArticle available
    on_fundamental(snap)    — new FundamentalSnapshot available

After all hooks have been called, the orchestrator calls ``generate_orders()``
to collect the strategy's current trade intentions.

State management
----------------
Strategies maintain their own internal state (positions, cooldowns, z-scores,
regime flags) between bars.  The state should be entirely in Python (no DB
writes inside a strategy) — the orchestrator is responsible for persistence.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


# ---------------------------------------------------------------------------
# Order dataclass
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """
    A trade intention emitted by a strategy.

    Attributes
    ----------
    ticker : str
        Asset symbol (e.g. ``'AAPL'``, ``'BTC-USD'``).
    side : OrderSide
        ``BUY`` or ``SELL``.
    quantity : float
        Fractional units to trade.  For stocks: number of shares.
        For crypto: number of coins.  Always positive.
    order_type : OrderType
        Execution type.  Most strategies emit ``MARKET`` orders;
        ``MarketMakingStrategy`` emits ``LIMIT`` orders.
    strategy_id : str
        Identifies which strategy generated this order.
    confidence : float
        Model confidence in [0, 1].  Used by the position sizer
        (higher confidence → larger allocation fraction).
    limit_price : float or None
        Required for ``LIMIT`` and ``STOP_LIMIT`` orders.
    stop_price : float or None
        Required for ``STOP`` and ``STOP_LIMIT`` orders.
    timestamp : datetime
        UTC wall-clock time when the order was generated.
    metadata : dict
        Optional extra fields for logging / debugging.
    """

    ticker: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    strategy_id: str = ""
    confidence: float = 0.5
    limit_price: float | None = None
    stop_price: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"Order quantity must be positive, got {self.quantity}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "strategy_id": self.strategy_id,
            "confidence": self.confidence,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "timestamp": self.timestamp.isoformat(),
            **self.metadata,
        }


# ---------------------------------------------------------------------------
# StrategyState — convenience container for per-ticker mutable state
# ---------------------------------------------------------------------------

@dataclass
class TickerState:
    """
    Mutable per-ticker state shared across a strategy's lifetime.

    Strategies typically maintain one TickerState per symbol in a dict.
    Using a dataclass rather than raw dict prevents typo-driven bugs.
    """
    position: float = 0.0           # shares/coins currently held (signed: + long, - short)
    entry_price: float = 0.0        # average entry price of the current position
    bars_in_position: int = 0       # bars since position was opened
    cooldown_bars_remaining: int = 0
    last_signal: float = 0.0
    last_confidence: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_flat(self) -> bool:
        return abs(self.position) < 1e-8

    @property
    def is_long(self) -> bool:
        return self.position > 1e-8

    @property
    def is_short(self) -> bool:
        return self.position < -1e-8


# ---------------------------------------------------------------------------
# BaseStrategy
# ---------------------------------------------------------------------------

class BaseStrategy(abc.ABC):
    """
    Abstract base class for all trading strategies.

    Parameters
    ----------
    strategy_id : str
        Stable snake_case identifier used in Order.strategy_id and logging.
    config : dict
        Strategy parameters loaded from strategy_config.yaml.
    tickers : list[str]
        Symbols this strategy instance monitors.
    """

    def __init__(
        self,
        strategy_id: str,
        config: dict[str, Any],
        tickers: list[str],
    ) -> None:
        self.strategy_id = strategy_id
        self.config = config
        self.tickers = list(tickers)
        # Per-ticker state initialised lazily on first bar
        self._state: dict[str, TickerState] = {t: TickerState() for t in tickers}
        self._bar_count: int = 0
        self._enabled: bool = config.get("enabled", True)

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        """
        Called by the orchestrator on every new OHLCV bar.

        Parameters
        ----------
        ticker : str
            Symbol for this bar.
        bar : pd.Series
            Current bar with keys: open, high, low, close, volume.
        features : pd.DataFrame
            Full feature matrix up to (and including) this bar.
            Shape: (n_history_bars, n_features).  The last row is current bar.
        """

    def on_news(self, ticker: str, article: Any) -> None:
        """Called when a new scored NewsArticle is available for a ticker."""

    def on_fundamental(self, ticker: str, snapshot: Any) -> None:
        """Called when a new FundamentalSnapshot is available for a ticker."""

    # ── Required interface ────────────────────────────────────────────────────

    @abc.abstractmethod
    def generate_orders(self) -> list[Order]:
        """
        Return the strategy's current trade intentions as a list of Orders.

        Called once per bar cycle after all on_bar/on_news/on_fundamental
        hooks have been invoked.

        Returns
        -------
        list[Order]
            May be empty if the strategy has no trade intentions this bar.
        """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _state_for(self, ticker: str) -> TickerState:
        if ticker not in self._state:
            self._state[ticker] = TickerState()
        return self._state[ticker]

    def _make_order(
        self,
        ticker: str,
        side: OrderSide,
        quantity: float,
        confidence: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **metadata: Any,
    ) -> Order:
        """Convenience factory that stamps strategy_id on every order."""
        return Order(
            ticker=ticker,
            side=side,
            quantity=max(quantity, 1e-8),
            order_type=order_type,
            strategy_id=self.strategy_id,
            confidence=max(0.0, min(1.0, confidence)),
            limit_price=limit_price,
            stop_price=stop_price,
            metadata=dict(metadata),
        )

    def reset(self) -> None:
        """Reset all per-ticker state (called at the start of a new backtest run)."""
        self._state = {t: TickerState() for t in self.tickers}
        self._bar_count = 0

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def allocation_weight(self) -> float:
        return float(self.config.get("allocation_weight", 0.1))
