"""
execution/binance_broker.py — BinanceBroker: Binance REST API adapter (crypto).

What is Binance?
----------------
Binance is the world's largest cryptocurrency exchange by trading volume.
This adapter handles order routing for crypto assets (BTC, ETH, SOL, etc.)
in both testnet (simulated) and live environments.

Binance SDK
-----------
We use the ``python-binance`` library (``binance.client.Client``).  The
testnet base URLs are substituted automatically when ``testnet=True``.

Ticker format
-------------
Our system uses Yahoo Finance-style tickers (``BTC-USD``, ``ETH-USD``).
Binance uses its own format (``BTCUSDT``, ``ETHUSDT``).  This adapter
normalises tickers via ``_to_binance_symbol()``:

    BTC-USD → BTCUSDT
    ETH-BTC → ETHBTC
    SOL-USD → SOLUSDT

Order mapping
-------------
Our ``OrderType`` → Binance side/type:

    MARKET     → MARKET
    LIMIT      → LIMIT + GTC
    STOP       → STOP_LOSS_LIMIT (Binance requires a limit price for stops)
    STOP_LIMIT → STOP_LOSS_LIMIT

Quantity precision
------------------
Binance enforces strict LOT_SIZE filters — quantities must be rounded to
the correct number of decimal places per symbol.  This adapter applies a
conservative 6 decimal place rounding; a production implementation should
fetch and cache exchange info filters.

Usage
-----
::

    from execution.binance_broker import BinanceBroker

    broker = BinanceBroker(
        api_key="your_key",
        secret_key="your_secret",
        testnet=True,
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

try:
    from binance.client import Client as BinanceClient
    from binance.exceptions import BinanceAPIException
    _BINANCE_AVAILABLE = True
except ImportError:
    _BINANCE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------

def _to_binance_symbol(ticker: str) -> str:
    """
    Convert Yahoo Finance-style ticker to Binance symbol.

    Examples
    --------
    >>> _to_binance_symbol("BTC-USD")
    'BTCUSDT'
    >>> _to_binance_symbol("ETH-BTC")
    'ETHBTC'
    >>> _to_binance_symbol("BTCUSDT")
    'BTCUSDT'
    """
    if "-" not in ticker:
        return ticker.upper()
    base, quote = ticker.upper().split("-", 1)
    # USD and USDT are interchangeable in Binance naming
    if quote in ("USD", "USDC"):
        quote = "USDT"
    return f"{base}{quote}"


def _map_binance_status(status: str) -> OrderStatus:
    mapping = {
        "NEW":              OrderStatus.PENDING,
        "PARTIALLY_FILLED": OrderStatus.PARTIAL,
        "FILLED":           OrderStatus.FILLED,
        "CANCELED":         OrderStatus.CANCELLED,
        "REJECTED":         OrderStatus.REJECTED,
        "EXPIRED":          OrderStatus.EXPIRED,
        "PENDING_CANCEL":   OrderStatus.PENDING,
    }
    return mapping.get(status.upper(), OrderStatus.PENDING)


# ---------------------------------------------------------------------------
# BinanceBroker
# ---------------------------------------------------------------------------

class BinanceBroker(ExecutionBroker):
    """
    Binance REST API adapter for crypto order execution.

    Parameters
    ----------
    api_key : str
        Binance API key.
    secret_key : str
        Binance secret key.
    testnet : bool
        If True, route to Binance Spot Testnet.  Default True.
    poll_interval : float
        Seconds between order status polls.  Default 0.5.
    max_poll_attempts : int
        Maximum poll attempts before returning PENDING.  Default 10.
    """

    # Testnet base URLs
    _TESTNET_BASE_URL = "https://testnet.binance.vision/api"
    _TESTNET_STREAM_URL = "wss://testnet.binance.vision/ws"

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = True,
        poll_interval: float = 0.5,
        max_poll_attempts: int = 10,
    ) -> None:
        if not _BINANCE_AVAILABLE:
            raise ImportError(
                "python-binance is not installed. Run: pip install python-binance"
            )
        self._testnet = testnet
        self.poll_interval = poll_interval
        self.max_poll_attempts = max_poll_attempts

        self._client = BinanceClient(
            api_key=api_key,
            api_secret=secret_key,
            testnet=testnet,
        )
        self._connected: bool = True

    # ── Core interface ────────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> FillEvent:
        """Submit an order to Binance and poll until filled (or timeout)."""
        symbol = _to_binance_symbol(order.ticker)
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        qty = str(round(order.quantity, 6))

        try:
            if order.order_type == OrderType.MARKET:
                response = self._client.create_order(
                    symbol=symbol,
                    side=side,
                    type="MARKET",
                    quantity=qty,
                )
            elif order.order_type == OrderType.LIMIT:
                if order.limit_price is None:
                    raise ValueError(f"LIMIT order for {order.ticker} missing limit_price")
                response = self._client.create_order(
                    symbol=symbol,
                    side=side,
                    type="LIMIT",
                    quantity=qty,
                    price=str(round(order.limit_price, 8)),
                    timeInForce="GTC",
                )
            elif order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
                sp = order.stop_price
                lp = order.limit_price or (sp * 1.001 if sp and side == "BUY" else sp * 0.999 if sp else None)
                if sp is None or lp is None:
                    raise ValueError(f"STOP order for {order.ticker} missing stop_price")
                response = self._client.create_order(
                    symbol=symbol,
                    side=side,
                    type="STOP_LOSS_LIMIT",
                    quantity=qty,
                    price=str(round(lp, 8)),
                    stopPrice=str(round(sp, 8)),
                    timeInForce="GTC",
                )
            else:
                raise ValueError(f"Unsupported order type: {order.order_type}")

            broker_id = str(response.get("orderId", ""))
            time.sleep(0.1)
            return self._poll_for_fill(order, symbol, broker_id, response)

        except Exception as exc:
            logger.error("BinanceBroker: submit_order failed for %s: %s", order.ticker, exc)
            return FillEvent(
                order=order,
                status=OrderStatus.REJECTED,
                filled_quantity=0.0,
                fill_price=0.0,
                metadata={"error": str(exc)},
            )

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.

        Note: Binance requires both symbol and order ID for cancellation.
        This implementation stores the symbol in the order_id as
        ``{symbol}:{order_id}`` when returned by submit_order.
        """
        try:
            if ":" in order_id:
                symbol, oid = order_id.split(":", 1)
                self._client.cancel_order(symbol=symbol, orderId=int(oid))
            else:
                logger.warning("BinanceBroker: cannot cancel without symbol in order_id")
                return False
            return True
        except Exception as exc:
            logger.warning("BinanceBroker: cancel_order(%s) failed: %s", order_id, exc)
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        try:
            if ":" in order_id:
                symbol, oid = order_id.split(":", 1)
                result = self._client.get_order(symbol=symbol, orderId=int(oid))
                return _map_binance_status(result.get("status", "NEW"))
        except Exception as exc:
            logger.warning("BinanceBroker: get_order_status(%s) failed: %s", order_id, exc)
        return OrderStatus.CANCELLED

    def get_account(self) -> dict[str, Any]:
        try:
            acct = self._client.get_account()
            balances = {
                b["asset"]: float(b["free"])
                for b in acct.get("balances", [])
                if float(b["free"]) > 0
            }
            usdt_balance = balances.get("USDT", 0.0)
            return {
                "cash": usdt_balance,
                "portfolio_value": usdt_balance,
                "buying_power": usdt_balance,
                "balances": balances,
                "currency": "USDT",
                "broker": "binance",
                "testnet": self._testnet,
            }
        except Exception as exc:
            logger.error("BinanceBroker: get_account() failed: %s", exc)
            return {"cash": 0.0, "portfolio_value": 0.0, "buying_power": 0.0, "broker": "binance"}

    @property
    def is_connected(self) -> bool:
        try:
            self._client.ping()
            self._connected = True
        except Exception:
            self._connected = False
        return self._connected

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _poll_for_fill(
        self,
        order: Order,
        symbol: str,
        broker_id: str,
        initial_response: dict[str, Any],
    ) -> FillEvent:
        """Poll until the order reaches a terminal state."""
        # Check if already filled in the initial response
        initial_status = _map_binance_status(initial_response.get("status", "NEW"))
        if initial_status == OrderStatus.FILLED:
            return self._build_fill_event(order, initial_response, broker_id, symbol)

        for attempt in range(self.max_poll_attempts):
            try:
                result = self._client.get_order(symbol=symbol, orderId=int(broker_id))
                status = _map_binance_status(result.get("status", "NEW"))

                if status in (OrderStatus.FILLED, OrderStatus.REJECTED,
                               OrderStatus.CANCELLED, OrderStatus.EXPIRED):
                    return self._build_fill_event(order, result, status == OrderStatus.FILLED and broker_id or "", symbol)

                time.sleep(self.poll_interval)
            except Exception as exc:
                logger.warning("BinanceBroker: poll attempt %d failed: %s", attempt + 1, exc)
                time.sleep(self.poll_interval)

        logger.warning(
            "BinanceBroker: order %s did not fill within %d polls", broker_id, self.max_poll_attempts
        )
        return FillEvent(
            order=order,
            status=OrderStatus.PENDING,
            filled_quantity=0.0,
            fill_price=0.0,
            broker_order_id=f"{symbol}:{broker_id}",
            metadata={"timeout": True},
        )

    def _build_fill_event(
        self,
        order: Order,
        response: dict[str, Any],
        broker_id: str,
        symbol: str,
    ) -> FillEvent:
        """Build a FillEvent from a Binance order response dict."""
        status = _map_binance_status(response.get("status", "NEW"))
        filled_qty = float(response.get("executedQty", 0.0))
        # Binance provides cumulativeQuoteQty (total quote spent/received)
        quote_qty = float(response.get("cummulativeQuoteQty", 0.0))
        fill_price = (quote_qty / filled_qty) if filled_qty > 0 else 0.0
        # Binance charges ~0.1% maker/taker fee
        commission = quote_qty * 0.001 if filled_qty > 0 else 0.0

        return FillEvent(
            order=order,
            status=status,
            filled_quantity=filled_qty,
            fill_price=round(fill_price, 8),
            commission=round(commission, 8),
            broker_order_id=f"{symbol}:{broker_id}",
            timestamp=datetime.now(timezone.utc),
        )
