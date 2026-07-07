"""
data/feeds/__init__.py — Convenience re-exports for all data feed implementations.

Import pattern::

    from data.feeds import AlpacaFeed, YFinanceFeed, BinanceFeed
    # or
    from data.feeds.alpaca_feed import AlpacaFeed
"""

from data.feeds.base import DataFeed
from data.feeds.yfinance_feed import YFinanceFeed
from data.feeds.alpaca_feed import AlpacaFeed
from data.feeds.coingecko_feed import CoinGeckoFeed
from data.feeds.binance_feed import BinanceFeed
from data.feeds.newsapi_feed import NewsApiFeed
from data.feeds.gdelt_feed import GdeltFeed
from data.feeds.alpha_vantage_feed import AlphaVantageFeed
from data.feeds.sec_edgar_feed import SecEdgarFeed

__all__ = [
    "DataFeed",
    "YFinanceFeed",
    "AlpacaFeed",
    "CoinGeckoFeed",
    "BinanceFeed",
    "NewsApiFeed",
    "GdeltFeed",
    "AlphaVantageFeed",
    "SecEdgarFeed",
]
