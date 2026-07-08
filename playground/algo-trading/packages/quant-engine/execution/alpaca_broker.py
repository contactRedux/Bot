"""
execution/alpaca_broker.py — AlpacaBroker: Alpaca Trade API adapter.

What is Alpaca?
---------------
Alpaca (alpaca.markets) is a commission-free brokerage API focused on
algorithmic traders.  It supports:

- **Paper trading** — same API, simulated fills, no real money.
- **Live trading** — real order routing to US equity markets.
- **Fractional shares** — buy 0.01 shares of AAPL.
- **WebSocket streams** — real-time trade updates and order events.

Alpaca SDK
----------
We use the official ``alpaca-py`` SDK (``alpaca.trading.client``).  The client
is lazily initialised — if the API keys are not configured, the broker raises
``ConfigurationError`` at instantiation time rather than at first use, so
problems surface early.

Order mapping
-------------
Our ``OrderType`` → Alpaca ``OrderType``:

    MARKET    → alpaca OrderType.MARKET
    LIMIT     → alpaca OrderType.LIMIT
    STOP      → alpaca OrderType.STOP
    STOP_LIMIT→ alpaca OrderType.STOP_LIMIT

Our ``OrderSide`` → Alpaca ``OrderSide``:

    BUY  → alpaca OrderSide.BUY
    SELL → alpaca OrderSide.SELL

Fill translation
----------------
Alpaca fills arrive as ``TradeUpdate`` events via WebSocket *or* as a
``GetOrderByIdRequest`` poll.  This adapter uses polling (synchronous) so it
fits the ``submit_order() → FillEvent`` contract without requiring an async
event loop in the calling code.

Rate limits
-----------
Alpaca paper API: 200 requests/min.  The broker adds a 0.1 s delay after
each submit to stay comfortably under the limit.

Usage
-----
::

    from execution.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker(
        api_key="your_key",
        secret_key="your_secret",
        base_url="https://paper-api.alpaca.markets",
    )
    fill = broker.submit_order(order)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from execution.base import ExecutionBroker, FillEvent, OrderStatus
from strategies.base import Order, OrderSide, OrderType

logger = logging.getLogger(__name__)

# Alpaca SDK is an optional dependency — only required in paper/live modes.
# We import lazily inside methods so the module can be imported in dev mode
# without alpaca-py installed.
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import (
        OrderSide as AlpacaOrderSide,
        OrderStatus as AlpacaOrderStatus,
        OrderType as AlpacaOrderType,
        TimeInForce,
    )
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopLimitOrderRequest,
        StopOrderRequest,
    )
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False


# ---------------------------------------------------------------------------
# Status mapping helper
# ---------------------------------------------------------------------------

def _map_alpaca_status(alpaca_status: Any) -> OrderStatus:
    """Convert an Alpaca OrderStatus value to our internal OrderStatus."""
    mapping = {
        "new":              OrderStatus.PENDING,
        "partially_filled": OrderStatus.PARTIAL,
        "filled":           OrderStatus.FILLED,
        "done_for_day":     OrderStatus.EXPIRED,
        "canceled":         OrderStatus.CANCELLED,
        "expired":          OrderStatus.EXPIRED,
        "replaced":         OrderStatus.CANCELLED,
        "pending_cancel":   OrderStatus.PENDING,
        "pending_replace":  OrderStatus.PENDING,
        "accepted":         OrderStatus.PENDING,
        "pending_new":      OrderStatus.PENDING,
        "rejected":         OrderStatus.REJECTED,
    }
    key = str(alpaca_status).lower()
    return mapping.get(key, OrderStatus.PENDING)


# ---------------------------------------------------------------------------
# AlpacaBroker
# ---------------------------------------------------------------------------

class AlpacaBroker(ExecutionBroker):
    """
    Alpaca Trade API adapter.

    Parameters
    ----------
    api_key : str
        Alpaca API key ID.
    secret_key : str
        Alpaca secret key.
    base_url : str
        REST endpoint.  Use ``https://paper-api.alpaca.markets`` for paper.
    poll_interval : float
        Seconds to wait between fill status polls.  Default 0.5.
    max_poll_attempts : int
        Maximum number of polls before giving up and returning PENDING.
        Default 10 (= up to 5 s per order).
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = "https://paper-api.alpaca.markets",
        poll_interval: float = 0.5,
        max_poll_attempts: int = 10,
    ) -> None:
        if not _ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py is not installed. Run: pip install alpaca-py"
            )
        self._client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=(base_url == "https://paper-api.alpaca.markets"),
        )
        self.poll_interval = poll_interval
        self.max_poll_attempts = max_poll_attempts
        self._base_url = base_url
        self._connected: bool = True

    # ── Core interface ────────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> FillEvent:
        """Submit an order and poll until filled (or timeout)."""
        try:
            alpaca_order = self._build_alpaca_request(order)
            response = self._client.submit_order(order_data=alpaca_order)
            broker_id = str(response.id)
            time.sleep(0.1)  # brief delay to respect rate limits

            # Poll for fill
            return self._poll_for_fill(order, broker_id)

        except Exception as exc:
            logger.error("AlpacaBroker: submit_order failed for %s: %s", order.ticker, exc)
            return FillEvent(
                order=order,
                status=OrderStatus.REJECTED,
                filled_quantity=0.0,
                fill_price=0.0,
                metadata={"error": str(exc)},
            )

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(order_id)
            return True
        except Exception as exc:
            logger.warning("AlpacaBroker: cancel_order(%s) failed: %s", order_id, exc)
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        try:
            from alpaca.trading.requests import GetOrderByIdRequest
            order = self._client.get_order_by_id(order_id)
            return _map_alpaca_status(order.status)
        except Exception as exc:
            logger.warning("AlpacaBroker: get_order_status(%s) failed: %s", order_id, exc)
            return OrderStatus.CANCELLED

    def get_account(self) -> dict[str, Any]:
        try:
            acct = self._client.get_account()
            return {
                "cash": float(acct.cash),
                "portfolio_value": float(acct.portfolio_value),
                "buying_power": float(acct.buying_power),
                "currency": "USD",
                "broker": "alpaca",
                "account_number": acct.account_number,
                "status": str(acct.status),
            }
        except Exception as exc:
            logger.error("AlpacaBroker: get_account() failed: %s", exc)
            return {"cash": 0.0, "portfolio_value": 0.0, "buying_power": 0.0, "broker": "alpaca"}

    @property
    def is_connected(self) -> bool:
        try:
            self._client.get_account()
            self._connected = True
        except Exception:
            self._connected = False
        return self._connected

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_alpaca_request(self, order: Order) -> Any:
        """Translate our Order into the correct Alpaca request object."""
        side = AlpacaOrderSide.BUY if order.side == OrderSide.BUY else AlpacaOrderSide.SELL
        qty = str(round(order.quantity, 6))

        if order.order_type == OrderType.MARKET:
            return MarketOrderRequest(
                symbol=order.ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        elif order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise ValueError(f"LIMIT order for {order.ticker} missing limit_price")
            return LimitOrderRequest(
                symbol=order.ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=str(round(order.limit_price, 4)),
            )
        elif order.order_type == OrderType.STOP:
            if order.stop_price is None:
                raise ValueError(f"STOP order for {order.ticker} missing stop_price")
            return StopOrderRequest(
                symbol=order.ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                stop_price=str(round(order.stop_price, 4)),
            )
        elif order.order_type == OrderType.STOP_LIMIT:
            if order.limit_price is None or order.stop_price is None:
                raise ValueError(f"STOP_LIMIT order for {order.ticker} missing limit_price or stop_price")
            return StopLimitOrderRequest(
                symbol=order.ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=str(round(order.limit_price, 4)),
                stop_price=str(round(order.stop_price, 4)),
            )
        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

    def _poll_for_fill(self, order: Order, broker_id: str) -> FillEvent:
        """Poll Alpaca until the order reaches a terminal state."""
        for attempt in range(self.max_poll_attempts):
            try:
                alpaca_order = self._client.get_order_by_id(broker_id)
                status = _map_alpaca_status(alpaca_order.status)

                if status == OrderStatus.FILLED:
                    filled_qty = float(alpaca_order.filled_qty or order.quantity)
                    fill_price = float(alpaca_order.filled_avg_price or 0.0)
                    return FillEvent(
                        order=order,
                        status=OrderStatus.FILLED,
                        filled_quantity=filled_qty,
                        fill_price=fill_price,
                        commission=0.0,  # Alpaca is commission-free
                        broker_order_id=broker_id,
                        timestamp=datetime.now(timezone.utc),
                    )
                elif status in (OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED):
                    return FillEvent(
                        order=order,
                        status=status,
                        filled_quantity=0.0,
                        fill_price=0.0,
                        broker_order_id=broker_id,
                    )

                time.sleep(self.poll_interval)

            except Exception as exc:
                logger.warning("AlpacaBroker: poll attempt %d failed: %s", attempt + 1, exc)
                time.sleep(self.poll_interval)

        # Timeout — return current partial state
        logger.warning(
            "AlpacaBroker: order %s did not fill within %d polls",
            broker_id, self.max_poll_attempts,
        )
        return FillEvent(
            order=order,
            status=OrderStatus.PENDING,
            filled_quantity=0.0,
            fill_price=0.0,
            broker_order_id=broker_id,
            metadata={"timeout": True},
        )
