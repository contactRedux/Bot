"""
execution/paper_broker.py — PaperBroker: simulated order execution.

What is a paper broker?
-----------------------
A paper broker executes orders "on paper" — no real money changes hands.
It is essential for two purposes:

1. **Backtesting** — the BacktestEngine feeds historical bars to strategies
   and routes their orders through PaperBroker so that fill logic is identical
   to what would happen in live trading.

2. **Paper trading** — running the full live pipeline against real market data
   but with simulated execution.  Lets you validate a strategy's live
   performance characteristics before risking real capital.

Slippage model
--------------
Real-world execution is never at the exact mid-price.  Market impact, bid-ask
spread, and order-book depth all cause the actual fill to be worse than the
quoted price.  PaperBroker models this with three additive components:

1. **Fixed slippage** (``fixed_slippage_pct``, default 0.0%):
   A constant percentage added to every fill regardless of size.
   Models the bid-ask spread for liquid instruments.

2. **Volume impact** (``vol_impact_pct``, default 0.0%):
   Additional slippage proportional to order size (not yet wired to actual
   volume data — uses a flat per-order percentage as a conservative proxy).

3. **Random noise** (``random_slippage_pct``, default 0.0%):
   Small normally-distributed noise term, representing execution timing
   uncertainty.  Uses a seeded RNG so backtests are reproducible.

Direction: slippage always hurts the trader.
    BUY  fill price = mark_price × (1 + total_slippage)
    SELL fill price = mark_price × (1 − total_slippage)

Commission model
----------------
Commission is computed as a flat rate per fill (``commission_rate``,
default 0.001 = 0.1%).  This is a conservative but simple model that
approximates Alpaca's commission-free model plus exchange fees.

Usage
-----
::

    from execution.paper_broker import PaperBroker
    from execution.base import FillEvent

    broker = PaperBroker(
        initial_cash=100_000.0,
        commission_rate=0.001,   # 0.1% per fill
        fixed_slippage_pct=0.0005,  # 0.05% bid-ask spread proxy
        seed=42,
    )

    broker.update_prices({"AAPL": 185.20, "BTC-USD": 62_000.0})
    fill = broker.submit_order(order)
    print(fill.fill_price, fill.commission)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from execution.base import ExecutionBroker, FillEvent, OrderStatus
from strategies.base import Order, OrderSide, OrderType

logger = logging.getLogger(__name__)


class PaperBroker(ExecutionBroker):
    """
    Simulated broker for backtesting and paper-trading.

    Parameters
    ----------
    initial_cash : float
        Starting cash balance for accounting purposes.
        The broker does NOT enforce a hard cash limit — strategies are
        responsible for staying within budget via the RiskManager.
    commission_rate : float
        Fraction of gross trade value charged as commission.
        Default 0.001 (0.1%).
    fixed_slippage_pct : float
        Fixed percentage slippage applied to every fill, regardless of
        order size.  Models bid-ask spread.  Default 0.0.
    vol_impact_pct : float
        Additional percentage slippage per fill (flat, not volume-scaled).
        Default 0.0.
    random_slippage_pct : float
        Std-dev of a normally-distributed noise term added to slippage.
        Default 0.0.
    seed : int
        RNG seed for reproducible random slippage.  Default 42.
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        commission_rate: float = 0.001,
        fixed_slippage_pct: float = 0.0,
        vol_impact_pct: float = 0.0,
        random_slippage_pct: float = 0.0,
        seed: int = 42,
    ) -> None:
        self._cash = float(initial_cash)
        self.commission_rate = commission_rate
        self.fixed_slippage_pct = fixed_slippage_pct
        self.vol_impact_pct = vol_impact_pct
        self.random_slippage_pct = random_slippage_pct
        self._rng = np.random.default_rng(seed)

        # Latest mark prices provided by the engine
        self._prices: dict[str, float] = {}
        # Pending orders (limit/stop orders not yet triggered)
        self._pending: dict[str, Order] = {}
        # Fill history
        self._fills: list[FillEvent] = []

    # ── Price feed ────────────────────────────────────────────────────────────

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update mark prices.  Called by the engine after each bar."""
        self._prices.update(prices)

    # ── Core interface ────────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> FillEvent:
        """
        Process an order and return a FillEvent.

        Market orders are filled immediately at the current mark price plus
        slippage.  Limit/stop orders are checked against mark price; if the
        limit is not met they are stored as pending (and will NOT be triggered
        in future bars unless you call ``check_pending_orders()``).
        """
        order_id = str(uuid.uuid4())[:8]
        mark_price = self._prices.get(order.ticker, 0.0)

        if mark_price <= 0.0:
            logger.warning(
                "PaperBroker: no price for %s — rejecting order %s", order.ticker, order_id
            )
            return FillEvent(
                order=order,
                status=OrderStatus.REJECTED,
                filled_quantity=0.0,
                fill_price=0.0,
                broker_order_id=order_id,
                metadata={"reason": "no_price_available"},
            )

        # ── Limit / stop feasibility check ────────────────────────────────────
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                return self._rejected(order, order_id, "limit_price_required")
            # Immediate fill if limit is already met; else pend
            if order.side == OrderSide.BUY and mark_price > order.limit_price:
                self._pending[order_id] = order
                logger.debug(
                    "PaperBroker: LIMIT BUY %s %.4f pending (mark=%.4f > limit=%.4f)",
                    order.ticker, order.quantity, mark_price, order.limit_price,
                )
                return FillEvent(
                    order=order,
                    status=OrderStatus.PENDING,
                    filled_quantity=0.0,
                    fill_price=0.0,
                    broker_order_id=order_id,
                )
            if order.side == OrderSide.SELL and mark_price < order.limit_price:
                self._pending[order_id] = order
                return FillEvent(
                    order=order,
                    status=OrderStatus.PENDING,
                    filled_quantity=0.0,
                    fill_price=0.0,
                    broker_order_id=order_id,
                )
            # Limit is already met — fill at limit price (more conservative)
            execution_price = float(order.limit_price)
        else:
            execution_price = mark_price

        return self._execute(order, execution_price, order_id)

    def check_pending_orders(self) -> list[FillEvent]:
        """
        Evaluate all pending limit/stop orders against current prices.

        Call this once per bar after ``update_prices()``.  Returns a list of
        FillEvents for any orders that were triggered.
        """
        triggered_ids: list[str] = []
        fills: list[FillEvent] = []

        for order_id, order in self._pending.items():
            mark_price = self._prices.get(order.ticker, 0.0)
            if mark_price <= 0.0:
                continue
            if order.order_type == OrderType.LIMIT:
                lp = order.limit_price or 0.0
                if (order.side == OrderSide.BUY and mark_price <= lp) or \
                   (order.side == OrderSide.SELL and mark_price >= lp):
                    fills.append(self._execute(order, lp, order_id))
                    triggered_ids.append(order_id)
            elif order.order_type == OrderType.STOP:
                sp = order.stop_price or 0.0
                if (order.side == OrderSide.BUY and mark_price >= sp) or \
                   (order.side == OrderSide.SELL and mark_price <= sp):
                    fills.append(self._execute(order, mark_price, order_id))
                    triggered_ids.append(order_id)

        for oid in triggered_ids:
            del self._pending[oid]

        return fills

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._pending:
            del self._pending[order_id]
            logger.debug("PaperBroker: cancelled order %s", order_id)
            return True
        return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        if order_id in self._pending:
            return OrderStatus.PENDING
        # Check fill history
        for fill in self._fills:
            if fill.broker_order_id == order_id:
                return fill.status
        return OrderStatus.CANCELLED  # unknown = treated as gone

    def get_account(self) -> dict[str, Any]:
        portfolio_value = sum(
            self._prices.get(t, 0.0) * 0.0  # positions tracked by Portfolio, not broker
            for t in self._prices
        )
        return {
            "cash": round(self._cash, 2),
            "portfolio_value": round(self._cash, 2),  # broker has no position view
            "buying_power": round(self._cash, 2),
            "currency": "USD",
            "broker": "paper",
        }

    @property
    def is_connected(self) -> bool:
        return True  # paper broker is always "connected"

    # ── Read-only state ───────────────────────────────────────────────────────

    @property
    def fills(self) -> list[FillEvent]:
        """All fills processed by this broker (read-only copy)."""
        return list(self._fills)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _execute(self, order: Order, base_price: float, order_id: str) -> FillEvent:
        """Compute slippage, commission, and create the FillEvent."""
        slippage = self._compute_slippage(order)
        if order.side == OrderSide.BUY:
            fill_price = base_price * (1.0 + slippage)
        else:
            fill_price = base_price * (1.0 - slippage)

        commission = fill_price * order.quantity * self.commission_rate
        self._cash += -fill_price * order.quantity * (1 if order.side == OrderSide.BUY else -1)
        self._cash -= commission

        fill = FillEvent(
            order=order,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            fill_price=round(fill_price, 6),
            commission=round(commission, 4),
            broker_order_id=order_id,
            slippage=round(slippage * base_price, 6),
            timestamp=datetime.now(timezone.utc),
        )
        self._fills.append(fill)
        logger.debug(
            "PaperBroker: FILL %s %s %.4f @ %.4f (slip=%.4f%%, comm=%.4f)",
            order.side.value.upper(), order.ticker, order.quantity,
            fill_price, slippage * 100, commission,
        )
        return fill

    def _compute_slippage(self, order: Order) -> float:
        """Return total slippage fraction for this order (always non-negative)."""
        noise = 0.0
        if self.random_slippage_pct > 0.0:
            noise = abs(float(self._rng.normal(0.0, self.random_slippage_pct)))
        return self.fixed_slippage_pct + self.vol_impact_pct + noise

    def _rejected(self, order: Order, order_id: str, reason: str) -> FillEvent:
        return FillEvent(
            order=order,
            status=OrderStatus.REJECTED,
            filled_quantity=0.0,
            fill_price=0.0,
            broker_order_id=order_id,
            metadata={"reason": reason},
        )
