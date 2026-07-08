"""
execution/factory.py — BrokerFactory: returns the correct broker for TRADING_MODE.

Why a factory?
--------------
The rest of the codebase never directly instantiates a broker — it always calls
``BrokerFactory.create()``.  This decouples the execution layer from the
configuration layer:

    TRADING_MODE=dev    → PaperBroker (no external connections)
    TRADING_MODE=paper  → PaperBroker (same, but acknowledges live data feeds)
    TRADING_MODE=live   → AlpacaBroker for equities, BinanceBroker for crypto

The factory also validates that required API keys are present in the settings
before attempting to instantiate live brokers.  This surfaces configuration
errors at startup rather than mid-strategy-run.

Asset routing
-------------
The live path routes equity tickers to Alpaca and crypto tickers to Binance.
Crypto detection uses a simple heuristic:

    is_crypto = ticker ends with "-USD", "-USDT", or matches known crypto names

For mixed portfolios (equities + crypto), the factory can return a
``RoutingBroker`` that dispatches each order to the correct sub-broker.

Usage
-----
::

    from execution.factory import BrokerFactory
    from config.settings import settings

    broker = BrokerFactory.create(settings)
    fill = broker.submit_order(order)
"""

from __future__ import annotations

import logging
from typing import Any

from execution.base import ExecutionBroker, FillEvent, OrderStatus
from strategies.base import Order

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known crypto tickers (heuristic — extend as needed)
# ---------------------------------------------------------------------------

_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-BTC", "-ETH", "-BNB", "-USDC")
_CRYPTO_BASES = {"BTC", "ETH", "SOL", "BNB", "ADA", "DOT", "AVAX", "MATIC",
                 "LINK", "UNI", "DOGE", "XRP", "LTC", "BCH"}


def _is_crypto(ticker: str) -> bool:
    """Return True if the ticker looks like a crypto asset."""
    upper = ticker.upper()
    if any(upper.endswith(sfx) for sfx in _CRYPTO_SUFFIXES):
        return True
    base = upper.split("-")[0]
    return base in _CRYPTO_BASES


# ---------------------------------------------------------------------------
# RoutingBroker — dispatches equity vs crypto to the right sub-broker
# ---------------------------------------------------------------------------

class RoutingBroker(ExecutionBroker):
    """
    Routes orders to AlpacaBroker (equities) or BinanceBroker (crypto).

    Used in live mode when the portfolio contains both asset classes.

    Parameters
    ----------
    equity_broker : ExecutionBroker
        Handles non-crypto tickers.
    crypto_broker : ExecutionBroker
        Handles crypto tickers.
    """

    def __init__(
        self,
        equity_broker: ExecutionBroker,
        crypto_broker: ExecutionBroker,
    ) -> None:
        self._equity = equity_broker
        self._crypto = crypto_broker

    def _route(self, order: Order) -> ExecutionBroker:
        return self._crypto if _is_crypto(order.ticker) else self._equity

    def submit_order(self, order: Order) -> FillEvent:
        return self._route(order).submit_order(order)

    def cancel_order(self, order_id: str) -> bool:
        # Try crypto first (identified by symbol:id format), then equity
        if ":" in order_id:
            return self._crypto.cancel_order(order_id)
        return self._equity.cancel_order(order_id)

    def get_order_status(self, order_id: str) -> OrderStatus:
        if ":" in order_id:
            return self._crypto.get_order_status(order_id)
        return self._equity.get_order_status(order_id)

    def get_account(self) -> dict[str, Any]:
        equity_acct = self._equity.get_account()
        crypto_acct = self._crypto.get_account()
        return {
            "equity": equity_acct,
            "crypto": crypto_acct,
            "cash": equity_acct.get("cash", 0.0) + crypto_acct.get("cash", 0.0),
            "portfolio_value": (
                equity_acct.get("portfolio_value", 0.0)
                + crypto_acct.get("portfolio_value", 0.0)
            ),
            "buying_power": (
                equity_acct.get("buying_power", 0.0)
                + crypto_acct.get("buying_power", 0.0)
            ),
            "broker": "routing",
        }

    def update_prices(self, prices: dict[str, float]) -> None:
        self._equity.update_prices(prices)
        self._crypto.update_prices(prices)

    @property
    def is_connected(self) -> bool:
        return self._equity.is_connected and self._crypto.is_connected


# ---------------------------------------------------------------------------
# BrokerFactory
# ---------------------------------------------------------------------------

class BrokerFactory:
    """
    Static factory that creates the right broker based on settings.

    Methods
    -------
    create(settings, initial_cash, **paper_kwargs)
        Build and return the appropriate ExecutionBroker.
    """

    @staticmethod
    def create(
        settings: Any,
        initial_cash: float = 100_000.0,
        **paper_kwargs: Any,
    ) -> ExecutionBroker:
        """
        Instantiate the correct broker for the current trading mode.

        Parameters
        ----------
        settings : Settings
            The application settings object (from ``config.settings``).
        initial_cash : float
            Starting cash for PaperBroker.  Ignored for live brokers.
        **paper_kwargs
            Additional keyword arguments forwarded to PaperBroker
            (e.g. ``commission_rate``, ``fixed_slippage_pct``).

        Returns
        -------
        ExecutionBroker
            A fully configured broker instance.

        Raises
        ------
        ValueError
            If live mode is requested but required API keys are missing.
        """
        mode = str(getattr(settings, "trading_mode", "dev")).lower()

        if mode in ("dev", "paper"):
            from execution.paper_broker import PaperBroker
            broker = PaperBroker(initial_cash=initial_cash, **paper_kwargs)
            logger.info(
                "BrokerFactory: created PaperBroker (mode=%s, initial_cash=%.2f)",
                mode, initial_cash,
            )
            return broker

        if mode == "live":
            return BrokerFactory._create_live_broker(settings, initial_cash)

        raise ValueError(f"Unknown trading mode: {mode!r}. Expected dev, paper, or live.")

    @staticmethod
    def _create_live_broker(settings: Any, initial_cash: float) -> ExecutionBroker:
        """Build the live broker(s).  Validates API keys first."""
        alpaca_key = getattr(settings, "alpaca_api_key", None)
        alpaca_secret = getattr(settings, "alpaca_secret_key", None)
        alpaca_url = getattr(settings, "alpaca_base_url", "https://api.alpaca.markets")
        binance_key = getattr(settings, "binance_api_key", None)
        binance_secret = getattr(settings, "binance_secret_key", None)
        binance_testnet = getattr(settings, "binance_testnet", True)

        has_alpaca = bool(alpaca_key and alpaca_secret)
        has_binance = bool(binance_key and binance_secret)

        if not has_alpaca and not has_binance:
            raise ValueError(
                "TRADING_MODE=live requires at least one broker configured. "
                "Set ALPACA_API_KEY + ALPACA_SECRET_KEY and/or "
                "BINANCE_API_KEY + BINANCE_SECRET_KEY in your .env file."
            )

        if has_alpaca and has_binance:
            from execution.alpaca_broker import AlpacaBroker
            from execution.binance_broker import BinanceBroker
            equity_broker = AlpacaBroker(
                api_key=alpaca_key,
                secret_key=alpaca_secret,
                base_url=alpaca_url,
            )
            crypto_broker = BinanceBroker(
                api_key=binance_key,
                secret_key=binance_secret,
                testnet=binance_testnet,
            )
            logger.info("BrokerFactory: created RoutingBroker (Alpaca + Binance, live)")
            return RoutingBroker(equity_broker, crypto_broker)

        if has_alpaca:
            from execution.alpaca_broker import AlpacaBroker
            broker = AlpacaBroker(
                api_key=alpaca_key,
                secret_key=alpaca_secret,
                base_url=alpaca_url,
            )
            logger.info("BrokerFactory: created AlpacaBroker (live)")
            return broker

        # Only Binance
        from execution.binance_broker import BinanceBroker
        broker = BinanceBroker(
            api_key=binance_key,
            secret_key=binance_secret,
            testnet=binance_testnet,
        )
        logger.info("BrokerFactory: created BinanceBroker (live, testnet=%s)", binance_testnet)
        return broker
