"""
execution/base.py — Abstract broker interface, FillEvent, and OrderStatus.

Design philosophy
-----------------
All broker adapters (paper, Alpaca, Binance) implement exactly one interface:
``ExecutionBroker``.  The RiskManager and BacktestEngine never import a
concrete broker — they only type-hint against ``ExecutionBroker``.  This
means swapping paper → live requires only a one-line change in
``execution/factory.py``.

Order → Fill lifecycle
-----------------------
1. ``RiskManager.check_order(order)`` returns an ``OrderDecision``.
2. If ``decision.approved``, call ``broker.submit_order(decision.order)``.
3. The broker returns a ``FillEvent`` (synchronous for paper; awaited for
   live brokers via ``submit_order_async``).
4. ``BacktestEngine`` / live engine calls ``portfolio.on_fill(fill)`` to
   update cash and positions.

OrderStatus enum
----------------
PENDING     — submitted, awaiting acknowledgement from the venue
FILLED      — fully executed
PARTIAL     — partially executed (live orders only)
CANCELLED   — cancelled before fill
REJECTED    — rejected by the venue (e.g. insufficient funds)
EXPIRED     — limit order expired unfilled
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from strategies.base import Order

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OrderStatus
# ---------------------------------------------------------------------------

class OrderStatus(str, Enum):
    PENDING   = "pending"
    FILLED    = "filled"
    PARTIAL   = "partial"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"
    EXPIRED   = "expired"


# ---------------------------------------------------------------------------
# FillEvent
# ---------------------------------------------------------------------------

@dataclass
class FillEvent:
    """
    Represents a completed (or partially completed) order execution.

    Produced by ``ExecutionBroker.submit_order()`` and consumed by
    ``Portfolio.on_fill()``.

    Attributes
    ----------
    order : Order
        The original order that generated this fill.
    status : OrderStatus
        Final state of the order after broker processing.
    filled_quantity : float
        Actual quantity executed.  May be less than ``order.quantity``
        for partial fills or 0 for rejections.
    fill_price : float
        Execution price per unit (including any slippage).
    commission : float
        Brokerage commission for this fill (dollar amount).
    timestamp : datetime
        UTC time the fill was processed.
    broker_order_id : str
        Exchange/broker reference ID (empty string for paper broker).
    slippage : float
        Slippage applied (fill_price − mid_price), positive = adverse.
    metadata : dict
        Additional broker-specific fields.
    """

    order: Order
    status: OrderStatus
    filled_quantity: float
    fill_price: float
    commission: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker_order_id: str = ""
    slippage: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ticker(self) -> str:
        return self.order.ticker

    @property
    def side(self) -> str:
        return self.order.side.value

    @property
    def strategy_id(self) -> str:
        return self.order.strategy_id

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def net_value(self) -> float:
        """
        Signed cash flow of this fill from the portfolio's perspective.

        Positive = cash received (sell).  Negative = cash paid (buy).
        """
        gross = self.fill_price * self.filled_quantity
        if self.side == "buy":
            return -(gross + self.commission)
        return gross - self.commission

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "side": self.side,
            "strategy_id": self.strategy_id,
            "status": self.status.value,
            "filled_quantity": round(self.filled_quantity, 6),
            "fill_price": round(self.fill_price, 6),
            "commission": round(self.commission, 4),
            "slippage": round(self.slippage, 6),
            "net_value": round(self.net_value, 2),
            "broker_order_id": self.broker_order_id,
            "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# ExecutionBroker — abstract interface
# ---------------------------------------------------------------------------

class ExecutionBroker(abc.ABC):
    """
    Abstract base class for all broker adapters.

    Concrete subclasses
    -------------------
    ``PaperBroker``   — instant fills at last-known price + configurable slippage
    ``AlpacaBroker``  — Alpaca Trade API (equities, paper and live)
    ``BinanceBroker`` — Binance REST API (crypto, testnet and live)

    All subclasses must implement:

    * ``submit_order(order)``       — synchronous submit; returns FillEvent
    * ``cancel_order(order_id)``    — cancel a pending order
    * ``get_order_status(order_id)``— query current status
    * ``get_account()``             — return account info dict
    * ``is_connected``              — property; True if broker is reachable

    The optional ``submit_order_async`` default delegates to the sync version;
    live broker subclasses should override it with a true async implementation.
    """

    @abc.abstractmethod
    def submit_order(self, order: Order) -> FillEvent:
        """
        Submit an order and return the fill result.

        For paper brokers this is synchronous and instant.
        For live brokers this may block until the order is acknowledged.
        """

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order by broker order ID.

        Returns True if the cancellation succeeded, False otherwise.
        """

    @abc.abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Return the current status of a submitted order."""

    @abc.abstractmethod
    def get_account(self) -> dict[str, Any]:
        """
        Return a normalised account info dict.

        Minimum keys: ``cash``, ``portfolio_value``, ``buying_power``.
        """

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool:
        """True if the broker connection is healthy."""

    # ── Default implementations (may be overridden) ───────────────────────────

    def update_prices(self, prices: dict[str, float]) -> None:
        """
        Provide the latest mark prices to the broker.

        Called by the engine after each bar.  The PaperBroker uses these for
        fill pricing; live brokers ignore them (they use live quotes).
        """

    def heartbeat(self) -> bool:
        """
        Check connection health.  Returns True if connection is ok.

        Default implementation delegates to ``is_connected``.
        """
        return self.is_connected
