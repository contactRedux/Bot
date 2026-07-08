"""
backtesting/events.py — Event dataclasses for the event-driven backtesting engine.

Event-driven architecture
--------------------------
The backtesting engine operates on a priority queue of events ordered by
``timestamp``.  Each bar advance produces a ``BarEvent``; strategies respond
with ``OrderEvent`` objects; the simulated broker produces ``FillEvent`` objects;
the engine can inject ``HaltEvent`` to stop the simulation early.

Event hierarchy
---------------

    Event (base)
    ├── BarEvent    — new OHLCV bar available for a ticker
    ├── SignalEvent — a strategy has generated a trade signal (informational)
    ├── OrderEvent  — a strategy wants to place an order
    ├── FillEvent   — an order was (fully or partially) filled by the broker
    └── HaltEvent   — simulation should stop (e.g. drawdown breached)

Using typed dataclasses (not a class hierarchy with __lt__) lets us store all
events in a standard ``heapq`` priority queue sorted by timestamp.

Why an event queue?
-------------------
The event queue enforces strict temporal causality.  A strategy at time T can
only react to events with ``timestamp <= T``.  This prevents look-ahead bias
by construction — no amount of future data can leak backward through the queue.

Usage
-----
::

    from backtesting.events import BarEvent, OrderEvent, FillEvent, HaltEvent
    from datetime import datetime, timezone

    bar = BarEvent(
        timestamp=datetime(2023, 6, 1, tzinfo=timezone.utc),
        ticker="AAPL",
        open=185.0, high=187.5, low=184.0, close=186.5, volume=55_000_000,
    )
    order = OrderEvent(
        timestamp=bar.timestamp,
        ticker="AAPL",
        side="buy",
        quantity=10.0,
        order_type="market",
        strategy_id="momentum",
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# EventType enum — used for routing and filtering
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    BAR = "bar"
    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"
    HALT = "halt"


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """
    Base class for all simulation events.

    The ``sort_index`` field is the primary sort key when events are stored in
    a ``heapq`` priority queue.  It encodes ``(timestamp, priority)`` so that
    within the same timestamp, ``BarEvent`` (priority 0) is processed before
    ``OrderEvent`` (priority 1) which is processed before ``FillEvent`` (priority 2).

    Comparison methods are defined on the base class so that events from
    *different* subclasses can be compared and sorted correctly — Python's
    ``@dataclass(order=True)`` generates per-class comparisons that raise
    ``TypeError`` when comparing across types.
    """

    # ── sort key for heapq — set by __post_init__ in subclasses ──────────────
    sort_index: tuple = field(default=(datetime.min, 0))
    # ── event type ────────────────────────────────────────────────────────────
    event_type: EventType = field(default=EventType.BAR)
    # ── wall-clock simulation time ────────────────────────────────────────────
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Rich comparisons on sort_index — work across all subclasses
    def __lt__(self, other: "Event") -> bool:  # type: ignore[override]
        return self.sort_index < other.sort_index

    def __le__(self, other: "Event") -> bool:
        return self.sort_index <= other.sort_index

    def __gt__(self, other: "Event") -> bool:
        return self.sort_index > other.sort_index

    def __ge__(self, other: "Event") -> bool:
        return self.sort_index >= other.sort_index

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.sort_index == other.sort_index

    def __hash__(self) -> int:
        return id(self)


# ---------------------------------------------------------------------------
# BarEvent
# ---------------------------------------------------------------------------

@dataclass
class BarEvent(Event):
    """
    Emitted once per bar per ticker.

    The backtesting engine generates one BarEvent per (ticker, timestamp) pair
    in strict ascending time order.  Strategies receive this event via the
    orchestrator's ``process_bar()`` call.

    Attributes
    ----------
    ticker : str
    open, high, low, close : float  — OHLCV data for the bar.
    volume : float
    interval : str               — Bar duration (``"1d"``, ``"1h"``, etc.).
    """

    ticker: str = field(default="", compare=False)
    open: float = field(default=0.0, compare=False)
    high: float = field(default=0.0, compare=False)
    low: float = field(default=0.0, compare=False)
    close: float = field(default=0.0, compare=False)
    volume: float = field(default=0.0, compare=False)
    interval: str = field(default="1d", compare=False)

    def __post_init__(self) -> None:
        self.event_type = EventType.BAR
        self.sort_index = (self.timestamp, 0)

    def to_series(self) -> dict[str, Any]:
        """Convert to a dict compatible with ``pd.Series`` for strategy consumption."""
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


# ---------------------------------------------------------------------------
# SignalEvent (informational only — not routed to broker)
# ---------------------------------------------------------------------------

@dataclass
class SignalEvent(Event):
    """
    Records that a strategy generated a trade signal.

    SignalEvents are informational — they are written to the trade log and
    report but are NOT routed to the broker.  The actual trade intent is
    expressed as an ``OrderEvent``.

    Attributes
    ----------
    ticker : str
    strategy_id : str
    signal : float          — Signal strength in [-1, +1].
    confidence : float      — Model confidence in [0, 1].
    """

    ticker: str = field(default="", compare=False)
    strategy_id: str = field(default="", compare=False)
    signal: float = field(default=0.0, compare=False)
    confidence: float = field(default=0.5, compare=False)

    def __post_init__(self) -> None:
        self.event_type = EventType.SIGNAL
        self.sort_index = (self.timestamp, 1)


# ---------------------------------------------------------------------------
# OrderEvent
# ---------------------------------------------------------------------------

@dataclass
class OrderEvent(Event):
    """
    Requests the SimulatedBroker to fill a trade.

    Generated by the BacktestEngine after the StrategyOrchestrator produces
    ``Order`` objects.  The engine wraps each ``Order`` in an ``OrderEvent``
    and pushes it onto the event queue.

    Attributes
    ----------
    ticker : str
    side : str              — ``"buy"`` or ``"sell"``.
    quantity : float        — Number of shares/coins to trade.
    order_type : str        — ``"market"``, ``"limit"``, ``"stop"``.
    strategy_id : str
    confidence : float
    limit_price : float | None
    stop_price : float | None
    order_id : str          — Unique order identifier.
    """

    ticker: str = field(default="", compare=False)
    side: str = field(default="buy", compare=False)
    quantity: float = field(default=0.0, compare=False)
    order_type: str = field(default="market", compare=False)
    strategy_id: str = field(default="", compare=False)
    confidence: float = field(default=0.5, compare=False)
    limit_price: float | None = field(default=None, compare=False)
    stop_price: float | None = field(default=None, compare=False)
    order_id: str = field(default="", compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        self.event_type = EventType.ORDER
        self.sort_index = (self.timestamp, 2)


# ---------------------------------------------------------------------------
# FillEvent
# ---------------------------------------------------------------------------

@dataclass
class FillEvent(Event):
    """
    Confirms that an order was filled by the SimulatedBroker.

    The SimulatedBroker generates one FillEvent per filled order and places it
    on the event queue.  The Portfolio and StrategyOrchestrator consume fills
    to update their positions.

    Attributes
    ----------
    ticker : str
    side : str              — ``"buy"`` or ``"sell"``.
    quantity : float        — Actual quantity filled.
    fill_price : float      — Actual fill price (after slippage).
    commission : float      — Transaction cost in base currency.
    strategy_id : str
    order_id : str          — Matches the originating OrderEvent.order_id.
    slippage : float        — Signed slippage vs. mid-price (fill_price − mid).
    """

    ticker: str = field(default="", compare=False)
    side: str = field(default="buy", compare=False)
    quantity: float = field(default=0.0, compare=False)
    fill_price: float = field(default=0.0, compare=False)
    commission: float = field(default=0.0, compare=False)
    strategy_id: str = field(default="", compare=False)
    order_id: str = field(default="", compare=False)
    slippage: float = field(default=0.0, compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        self.event_type = EventType.FILL
        self.sort_index = (self.timestamp, 3)

    @property
    def trade_value(self) -> float:
        """Gross trade value (fill_price × quantity), before commission."""
        return self.fill_price * self.quantity

    @property
    def net_cost(self) -> float:
        """
        Net cash impact of the fill.

        Positive = cash leaves the portfolio (buy).
        Negative = cash enters the portfolio (sell proceeds).
        """
        sign = 1.0 if self.side == "buy" else -1.0
        return sign * self.fill_price * self.quantity + self.commission


# ---------------------------------------------------------------------------
# HaltEvent
# ---------------------------------------------------------------------------

@dataclass
class HaltEvent(Event):
    """
    Signals the BacktestEngine to stop the simulation immediately.

    Can be generated by the risk monitor when a drawdown limit is breached,
    or injected externally to interrupt a running simulation.

    Attributes
    ----------
    reason : str        — Human-readable reason for the halt.
    """

    reason: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        self.event_type = EventType.HALT
        self.sort_index = (self.timestamp, 99)
