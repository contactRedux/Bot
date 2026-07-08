"""
execution package — order routing adapters.

Sub-modules
-----------
execution.base           — ExecutionBroker abstract interface, FillEvent, OrderStatus
execution.paper_broker   — PaperBroker: simulated fills with configurable slippage model
execution.alpaca_broker  — AlpacaBroker: Alpaca Trade API adapter (equities)
execution.binance_broker — BinanceBroker: Binance REST API adapter (crypto)
execution.factory        — BrokerFactory: returns the correct broker for TRADING_MODE

Quick-start
-----------
::

    from execution.factory import BrokerFactory
    from config.settings import settings

    broker = BrokerFactory.create(settings, initial_cash=100_000.0)
    fill = broker.submit_order(order)
    if fill.is_filled:
        portfolio.on_fill(fill)
"""

from execution.base import ExecutionBroker, FillEvent, OrderStatus
from execution.paper_broker import PaperBroker
from execution.factory import BrokerFactory, RoutingBroker

__all__ = [
    # Base
    "ExecutionBroker",
    "FillEvent",
    "OrderStatus",
    # Concrete brokers
    "PaperBroker",
    # Factory
    "BrokerFactory",
    "RoutingBroker",
]
