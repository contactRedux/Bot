"""
execution package — order routing adapters.

Sub-modules
-----------
execution.base         — ExecutionBroker abstract interface + OrderStatus enum
execution.paper_broker — PaperBroker: simulated fills with configurable slippage
execution.alpaca_broker — AlpacaBroker: Alpaca Trade API adapter (equities)
execution.binance_broker — BinanceBroker: Binance API adapter (crypto)
execution.factory      — BrokerFactory: returns the correct broker for TRADING_MODE
"""
