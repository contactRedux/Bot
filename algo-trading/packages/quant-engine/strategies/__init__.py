"""
strategies/__init__.py — Strategy engine package.

Imports the public API for convenient access:

    from strategies import (
        BaseStrategy, Order, OrderSide, OrderType,
        MomentumStrategy, MeanReversionStrategy, StatArbStrategy,
        MarketMakingStrategy, SentimentStrategy, MacroFactorStrategy,
        VWAPReversionStrategy, KellyVolStrategy, StrategyOrchestrator,
    )
"""

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.stat_arb import StatArbStrategy
from strategies.market_making import MarketMakingStrategy
from strategies.sentiment import SentimentStrategy, ScoredArticle
from strategies.macro_factor import MacroFactorStrategy, MacroRegime
from strategies.vwap_reversion import VWAPReversionStrategy
from strategies.kelly_vol import KellyVolStrategy
from strategies.orchestrator import StrategyOrchestrator

__all__ = [
    "BaseStrategy",
    "Order",
    "OrderSide",
    "OrderType",
    "TickerState",
    "MomentumStrategy",
    "MeanReversionStrategy",
    "StatArbStrategy",
    "MarketMakingStrategy",
    "SentimentStrategy",
    "ScoredArticle",
    "MacroFactorStrategy",
    "MacroRegime",
    "VWAPReversionStrategy",
    "KellyVolStrategy",
    "StrategyOrchestrator",
]
