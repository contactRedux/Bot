"""
strategies package — trading strategy implementations.

Sub-modules
-----------
strategies.base           — BaseStrategy interface + Order dataclass
strategies.momentum       — Trend-following via LSTM/Transformer ensemble
strategies.mean_reversion — Bollinger Band z-score counter-trend strategy
strategies.stat_arb       — Cointegration pairs trading
strategies.market_making  — RL-guided bid/ask quoting strategy
strategies.sentiment      — FinBERT sentiment event-driven strategy
strategies.macro_factor   — VIX + yield curve + earnings top-down strategy
strategies.orchestrator   — StrategyOrchestrator: aggregates all signals
"""
