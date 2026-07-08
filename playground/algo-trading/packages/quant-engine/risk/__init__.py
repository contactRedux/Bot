"""
risk package — position limits, drawdown monitoring, and VaR.

Sub-modules
-----------
risk.limits      — RiskLimits config dataclass
risk.manager     — RiskManager: vets / scales orders before execution
risk.var         — Historical VaR and CVaR (Expected Shortfall)
risk.monitor     — DrawdownMonitor: circuit-breaker on max drawdown
risk.correlation — Correlation concentration detection

Quick-start
-----------
::

    from risk.limits import RiskLimits
    from risk.manager import RiskManager, OrderDecisionType
    from risk.monitor import DrawdownMonitor
    from risk.var import HistoricalVaR
    from risk.correlation import CorrelationChecker
"""

from risk.limits import RiskLimits
from risk.var import HistoricalVaR, VaRResult
from risk.monitor import DrawdownMonitor, RiskAlert
from risk.correlation import CorrelationChecker, CorrelationResult, CorrelatedPair
from risk.manager import RiskManager, OrderDecision, OrderDecisionType

__all__ = [
    # Limits
    "RiskLimits",
    # VaR
    "HistoricalVaR", "VaRResult",
    # Monitor
    "DrawdownMonitor", "RiskAlert",
    # Correlation
    "CorrelationChecker", "CorrelationResult", "CorrelatedPair",
    # Manager
    "RiskManager", "OrderDecision", "OrderDecisionType",
]
