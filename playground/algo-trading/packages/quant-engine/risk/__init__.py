"""
risk package — position limits, drawdown monitoring, and VaR.

Sub-modules
-----------
risk.limits      — RiskLimits config dataclass
risk.manager     — RiskManager: vets / scales orders before execution
risk.var         — Historical VaR and CVaR (Expected Shortfall)
risk.monitor     — DrawdownMonitor: circuit-breaker on max drawdown
risk.correlation — Correlation concentration detection
"""
