"""
backtesting package — event-driven simulation engine.

Sub-modules
-----------
backtesting.events      — Event dataclasses (BarEvent, OrderEvent, FillEvent, …)
backtesting.engine      — BacktestEngine: main simulation loop
backtesting.broker      — SimulatedBroker: fills with realistic slippage
backtesting.portfolio   — Portfolio: position + PnL tracking
backtesting.metrics     — Performance metric calculations (Sharpe, drawdown, …)
backtesting.report      — BacktestReport: serialisable results container
backtesting.runner      — CLI entry point (python -m backtesting.runner …)
backtesting.walkforward — Walk-forward cross-validation loop
"""
