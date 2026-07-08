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

Quick-start
-----------
::

    from backtesting.engine import BacktestEngine
    from backtesting.broker import SimulatedBroker
    from backtesting.portfolio import Portfolio
    from backtesting.events import BarEvent, FillEvent
    from backtesting.metrics import compute_metrics
    from backtesting.report import BacktestReport
"""

from backtesting.events import (
    BarEvent,
    Event,
    EventType,
    FillEvent,
    HaltEvent,
    OrderEvent,
    SignalEvent,
)
from backtesting.broker import (
    FixedPercentageSlippage,
    HalfSpreadSlippage,
    SimulatedBroker,
)
from backtesting.portfolio import Portfolio, Position
from backtesting.metrics import compute_metrics
from backtesting.report import BacktestReport
from backtesting.engine import BacktestEngine
from backtesting.walkforward import WalkForwardBacktest, WalkForwardResults

__all__ = [
    # Events
    "Event", "EventType", "BarEvent", "SignalEvent", "OrderEvent",
    "FillEvent", "HaltEvent",
    # Broker
    "SimulatedBroker", "FixedPercentageSlippage", "HalfSpreadSlippage",
    # Portfolio
    "Portfolio", "Position",
    # Metrics
    "compute_metrics",
    # Report
    "BacktestReport",
    # Engine
    "BacktestEngine",
    # Walk-forward
    "WalkForwardBacktest", "WalkForwardResults",
]
