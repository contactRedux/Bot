"""
data/__init__.py — Public interface for the data layer.

The data layer provides:
  - ``schemas``  — Pydantic models for all canonical data types
  - ``feeds``    — Feed implementations (yfinance, Alpaca, Binance, etc.)
  - ``store``    — DataStore SQLAlchemy persistence layer
  - ``pipeline`` — DataPipeline APScheduler orchestrator
"""

from data.schemas import (
    FundamentalSnapshot,
    NewsArticle,
    OHLCVBar,
    OrderBook,
    OrderBookLevel,
    Trade,
)
from data.store import DataStore
from data.pipeline import DataPipeline

__all__ = [
    # Schemas
    "OHLCVBar",
    "Trade",
    "OrderBook",
    "OrderBookLevel",
    "NewsArticle",
    "FundamentalSnapshot",
    # Infrastructure
    "DataStore",
    "DataPipeline",
]
