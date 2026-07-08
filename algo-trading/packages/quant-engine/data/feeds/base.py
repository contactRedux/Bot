"""
data/feeds/base.py — Abstract base class for all data feed implementations.

Every data feed in the system (yfinance, Alpaca, Binance, NewsAPI, etc.) must
extend ``DataFeed`` and implement the relevant methods.  This enforces a
consistent interface so the DataPipeline and DataStore can treat all feeds
uniformly without knowing their internals.

Design notes
------------
* ``fetch_bars``     — synchronous historical OHLCV pull (used in backtesting)
* ``stream_bars``    — async generator for real-time bar delivery (paper/live)
* ``fetch_news``     — synchronous historical news pull
* ``fetch_fundamentals`` — synchronous fundamental data pull
* ``stream_orderbook`` — async generator for live order book snapshots

A concrete feed need only implement the methods it supports; the base class
raises ``NotImplementedError`` with a descriptive message for any unsupported
method, making gaps obvious at runtime rather than silently.
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import AsyncIterator

from data.schemas import FundamentalSnapshot, NewsArticle, OHLCVBar, OrderBook


class DataFeed(abc.ABC):
    """
    Abstract base class for all market-data feeds.

    Every subclass must declare a ``SOURCE`` class attribute — a short,
    machine-readable string identifying the provider (e.g. ``"yfinance"``,
    ``"alpaca"``, ``"binance"``).  This value is written into the ``source``
    field of every canonical record produced by the feed, allowing the
    DataStore to track data provenance.

    Parameters
    ----------
    config : dict
        Provider-specific configuration.  At minimum this will contain any
        API keys or connection parameters read from ``config.settings``.
    """

    SOURCE: str = "unknown"

    def __init__(self, config: dict | None = None) -> None:
        self.config: dict = config or {}

    # ── Historical OHLCV ──────────────────────────────────────────────────────

    def fetch_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        """
        Fetch historical OHLCV bars for a single ticker.

        Parameters
        ----------
        ticker :
            Ticker symbol in the provider's native format.
        interval :
            Bar duration string: ``"1m"``, ``"5m"``, ``"15m"``, ``"1h"``,
            ``"1d"``.
        start, end :
            UTC datetime bounds (inclusive).  The returned bars will have
            ``event_timestamp`` within ``[start, end]``.

        Returns
        -------
        list[OHLCVBar]
            Bars sorted ascending by ``event_timestamp``.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support fetch_bars")

    # ── Real-time streaming ───────────────────────────────────────────────────

    async def stream_bars(
        self, tickers: list[str], interval: str = "1m"
    ) -> AsyncIterator[OHLCVBar]:
        """
        Async generator yielding real-time ``OHLCVBar`` objects as they arrive.

        Only relevant for feeds backed by a WebSocket stream (Alpaca, Binance).
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support stream_bars")
        yield  # make this a generator to satisfy the type checker

    async def stream_orderbook(
        self, tickers: list[str], depth: int = 10
    ) -> AsyncIterator[OrderBook]:
        """Async generator yielding real-time ``OrderBook`` snapshots."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support stream_orderbook"
        )
        yield

    # ── News ──────────────────────────────────────────────────────────────────

    def fetch_news(
        self,
        tickers: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 100,
    ) -> list[NewsArticle]:
        """Fetch historical news articles, optionally filtered by ticker and time."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support fetch_news")

    # ── Fundamentals ──────────────────────────────────────────────────────────

    def fetch_fundamentals(
        self,
        ticker: str,
        period: str = "quarterly",
    ) -> list[FundamentalSnapshot]:
        """Fetch fundamental data snapshots for a single ticker."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support fetch_fundamentals"
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} source={self.SOURCE!r}>"
