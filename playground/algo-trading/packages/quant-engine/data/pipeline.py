"""
data/pipeline.py — DataPipeline: APScheduler-based orchestrator for all data feeds.

The DataPipeline is the "always-on" component of the quant-engine that keeps
the database up to date with fresh market data.  It runs two types of jobs:

1. **Continuous streaming jobs** — Alpaca (equities) and Binance (crypto)
   WebSocket streams that feed real-time bars directly into the DataStore.
   These are long-running async tasks that only apply in ``paper`` or ``live``
   mode.

2. **Scheduled polling jobs** — REST feeds that are called on a fixed interval:
   * Historical bar backfill (daily, at midnight): yfinance + CoinGecko
   * News (every 15 minutes): NewsAPI + GDELT
   * Fundamentals (weekly, on Sundays): Alpha Vantage + SEC EDGAR

APScheduler integration
------------------------
We use APScheduler's ``AsyncIOScheduler`` so all jobs run in the same asyncio
event loop as the FastAPI server (Sub-Task 9).  This avoids the overhead of
thread pools for I/O-bound jobs.

Job scheduling summary
-----------------------
  | Job                      | Trigger         | Mode required |
  |--------------------------|-----------------|---------------|
  | stream_alpaca_bars       | continuous      | paper, live   |
  | stream_binance_bars      | continuous      | paper, live   |
  | stream_binance_orderbook | continuous      | paper, live   |
  | poll_yfinance            | interval 1h     | all           |
  | poll_coingecko           | interval 1h     | all           |
  | poll_newsapi             | interval 15min  | all           |
  | poll_gdelt               | interval 15min  | all           |
  | poll_fundamentals        | cron weekly     | all           |

Configuration
-------------
All feed configs are drawn from ``config.settings``:

    settings.alpaca_api_key      → AlpacaFeed
    settings.binance_api_key     → BinanceFeed
    settings.newsapi_key         → NewsApiFeed
    settings.alpha_vantage_key   → AlphaVantageFeed

Tickers and polling intervals are configured via ``config/strategy_config.yaml``
or overridden when constructing the DataPipeline.

Usage
-----
::

    from data.pipeline import DataPipeline
    from data.store import DataStore

    store = DataStore("sqlite:///./algo_trading.db")
    pipeline = DataPipeline(
        store=store,
        equity_tickers=["AAPL", "MSFT", "GOOGL"],
        crypto_tickers=["BTC-USD", "ETH-USD"],
    )
    await pipeline.start()
    # ... runs indefinitely ...
    await pipeline.stop()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

import structlog

from config.settings import TradingMode, settings
from data.store import DataStore

logger = structlog.get_logger(__name__)

# ── Default ticker universes (override via constructor) ───────────────────────
_DEFAULT_EQUITY_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
_DEFAULT_CRYPTO_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD"]


class DataPipeline:
    """
    Orchestrates all data feeds and keeps the DataStore fresh.

    Parameters
    ----------
    store : DataStore
        The DataStore instance to write all fetched data into.
    equity_tickers : list[str]
        US equity tickers to fetch (yfinance, Alpaca).
    crypto_tickers : list[str]
        Crypto tickers in ``"BASE-USD"`` format (CoinGecko, Binance).
    bar_interval : str
        Bar interval for historical and streaming data (default ``"1d"``).
    news_poll_minutes : int
        How often to poll NewsAPI and GDELT (default 15).
    fundamental_poll_days : int
        How often to refresh fundamental data (default 7).

    Notes
    -----
    * In ``dev`` mode, streaming jobs are not started (no WebSocket connections).
    * All scheduled jobs are run in the asyncio event loop so they are
      compatible with FastAPI's lifespan context manager.
    """

    def __init__(
        self,
        store: DataStore,
        equity_tickers: list[str] | None = None,
        crypto_tickers: list[str] | None = None,
        bar_interval: str = "1d",
        news_poll_minutes: int = 15,
        fundamental_poll_days: int = 7,
    ) -> None:
        self.store = store
        self.equity_tickers: list[str] = equity_tickers or _DEFAULT_EQUITY_TICKERS
        self.crypto_tickers: list[str] = crypto_tickers or _DEFAULT_CRYPTO_TICKERS
        self.bar_interval = bar_interval
        self.news_poll_minutes = news_poll_minutes
        self.fundamental_poll_days = fundamental_poll_days

        self._scheduler = None
        self._streaming_tasks: list[asyncio.Task] = []
        self._running = False

        # Lazily initialized feed instances
        self._yfinance_feed = None
        self._alpaca_feed = None
        self._coingecko_feed = None
        self._binance_feed = None
        self._newsapi_feed = None
        self._gdelt_feed = None
        self._av_feed = None
        self._edgar_feed = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start the data pipeline.

        1. Runs a one-time historical backfill for all tickers (up to 1 year).
        2. Schedules recurring polling jobs.
        3. Starts real-time streaming tasks (paper/live mode only).
        """
        if self._running:
            logger.warning("pipeline.already_running")
            return

        logger.info(
            "pipeline.starting",
            mode=settings.trading_mode.value,
            equity_tickers=self.equity_tickers,
            crypto_tickers=self.crypto_tickers,
        )

        # Initial historical backfill (non-blocking — runs in executor)
        asyncio.create_task(self._initial_backfill())

        # Set up APScheduler
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError:
            raise ImportError(
                "APScheduler is not installed.  Run: pip install APScheduler"
            )

        self._scheduler = AsyncIOScheduler(timezone="UTC")

        # News polling (every N minutes, all modes)
        self._scheduler.add_job(
            self._poll_news,
            "interval",
            minutes=self.news_poll_minutes,
            id="poll_news",
            name="Poll NewsAPI + GDELT",
        )

        # Historical bar refresh (every hour, all modes)
        self._scheduler.add_job(
            self._poll_bars,
            "interval",
            hours=1,
            id="poll_bars",
            name="Refresh OHLCV bars",
        )

        # Fundamental data refresh (weekly)
        self._scheduler.add_job(
            self._poll_fundamentals,
            "interval",
            days=self.fundamental_poll_days,
            id="poll_fundamentals",
            name="Refresh fundamental snapshots",
        )

        self._scheduler.start()

        # Start streaming tasks for paper/live modes
        if settings.trading_mode in (TradingMode.PAPER, TradingMode.LIVE):
            self._start_streaming_tasks()

        self._running = True
        logger.info("pipeline.started")

    async def stop(self) -> None:
        """Gracefully stop all pipeline jobs and streaming tasks."""
        if not self._running:
            return

        logger.info("pipeline.stopping")

        # Cancel streaming tasks
        for task in self._streaming_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._streaming_tasks.clear()

        # Stop the scheduler
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

        self._running = False
        logger.info("pipeline.stopped")

    # ── Streaming tasks (paper/live only) ─────────────────────────────────────

    def _start_streaming_tasks(self) -> None:
        """Create asyncio tasks for all real-time streaming feeds."""
        if self.equity_tickers:
            task = asyncio.create_task(
                self._stream_alpaca_bars(),
                name="stream_alpaca_bars",
            )
            self._streaming_tasks.append(task)

        if self.crypto_tickers:
            task = asyncio.create_task(
                self._stream_binance_bars(),
                name="stream_binance_bars",
            )
            self._streaming_tasks.append(task)

            ob_task = asyncio.create_task(
                self._stream_binance_orderbook(),
                name="stream_binance_orderbook",
            )
            self._streaming_tasks.append(ob_task)

        logger.info("pipeline.streaming_tasks_started", count=len(self._streaming_tasks))

    async def _stream_alpaca_bars(self) -> None:
        """
        Continuously stream real-time equity bars from Alpaca WebSocket.

        Writes each completed bar directly to the DataStore as it arrives.
        Reconnects automatically if the stream drops (simple retry loop).
        """
        feed = self._get_alpaca_feed()
        retry_delay = 5  # seconds between reconnection attempts

        while True:
            try:
                logger.info("pipeline.alpaca_stream.connecting", tickers=self.equity_tickers)
                async for bar in feed.stream_bars(self.equity_tickers, interval="1m"):
                    self.store.write_bars([bar])
                    logger.debug(
                        "pipeline.alpaca_bar_received",
                        ticker=bar.ticker,
                        close=bar.close,
                    )
            except asyncio.CancelledError:
                logger.info("pipeline.alpaca_stream.cancelled")
                return
            except Exception as exc:
                logger.error(
                    "pipeline.alpaca_stream.error",
                    error=str(exc),
                    retry_in=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)  # exponential backoff, cap at 60s

    async def _stream_binance_bars(self) -> None:
        """Continuously stream real-time crypto bars from Binance WebSocket."""
        feed = self._get_binance_feed()
        retry_delay = 5

        while True:
            try:
                logger.info("pipeline.binance_stream.connecting", tickers=self.crypto_tickers)
                async for bar in feed.stream_bars(self.crypto_tickers, interval="1m"):
                    self.store.write_bars([bar])
                    logger.debug(
                        "pipeline.binance_bar_received",
                        ticker=bar.ticker,
                        close=bar.close,
                    )
            except asyncio.CancelledError:
                logger.info("pipeline.binance_stream.cancelled")
                return
            except Exception as exc:
                logger.error(
                    "pipeline.binance_stream.error",
                    error=str(exc),
                    retry_in=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    async def _stream_binance_orderbook(self) -> None:
        """
        Stream live order book snapshots from Binance.

        Order book data is NOT stored in the DataStore (too high volume).
        Instead this task maintains an in-memory ``latest_orderbooks`` dict
        that market-making strategies can query synchronously.
        """
        feed = self._get_binance_feed()
        retry_delay = 5

        while True:
            try:
                logger.info(
                    "pipeline.binance_orderbook.connecting",
                    tickers=self.crypto_tickers,
                )
                async for snapshot in feed.stream_orderbook(self.crypto_tickers, depth=10):
                    # Store in-memory for strategy consumption (not persisted)
                    self.latest_orderbooks[snapshot.ticker] = snapshot
                    logger.debug(
                        "pipeline.orderbook_received",
                        ticker=snapshot.ticker,
                        mid=(
                            (snapshot.asks[0].price + snapshot.bids[0].price) / 2
                            if snapshot.asks and snapshot.bids
                            else None
                        ),
                    )
            except asyncio.CancelledError:
                logger.info("pipeline.binance_orderbook.cancelled")
                return
            except Exception as exc:
                logger.error(
                    "pipeline.binance_orderbook.error",
                    error=str(exc),
                    retry_in=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    # ── Scheduled polling jobs ────────────────────────────────────────────────

    async def _initial_backfill(self) -> None:
        """
        Run a one-time historical backfill for all configured tickers.

        This is called once on pipeline start.  It fetches up to 1 year of
        daily bars for equities (yfinance) and crypto (CoinGecko), and the
        most recent 100 news articles (NewsAPI + GDELT).

        Each ticker is checked against the DataStore first — if recent data
        already exists, only the missing gap is fetched (incremental fill).
        """
        logger.info("pipeline.initial_backfill.starting")
        end = datetime.now(tz=timezone.utc)
        default_start = end - timedelta(days=365)

        loop = asyncio.get_event_loop()

        # Equity historical bars (yfinance)
        yf_feed = self._get_yfinance_feed()
        for ticker in self.equity_tickers:
            start = self._get_backfill_start(ticker, self.bar_interval, default_start)
            if start >= end:
                continue
            logger.info("pipeline.backfill.equity", ticker=ticker, start=start)
            bars = await loop.run_in_executor(
                None, yf_feed.fetch_bars, ticker, self.bar_interval, start, end
            )
            inserted = self.store.write_bars(bars)
            logger.info(
                "pipeline.backfill.equity.done",
                ticker=ticker,
                bars=len(bars),
                inserted=inserted,
            )

        # Crypto historical bars (CoinGecko)
        cg_feed = self._get_coingecko_feed()
        for ticker in self.crypto_tickers:
            start = self._get_backfill_start(ticker, "1d", default_start)
            if start >= end:
                continue
            days = max(1, (end - start).days)
            logger.info("pipeline.backfill.crypto", ticker=ticker, days=days)
            bars = await loop.run_in_executor(
                None, cg_feed.fetch_market_chart, ticker, days
            )
            inserted = self.store.write_bars(bars)
            logger.info(
                "pipeline.backfill.crypto.done",
                ticker=ticker,
                bars=len(bars),
                inserted=inserted,
            )

        # Initial news fetch
        await self._poll_news()

        logger.info("pipeline.initial_backfill.done")

    async def _poll_bars(self) -> None:
        """
        Hourly job: fetch any new bars since the last stored timestamp.

        For daily bars this is essentially a no-op until the next market day.
        For intraday bars it fetches the last hour of data for all tickers.
        """
        loop = asyncio.get_event_loop()
        end = datetime.now(tz=timezone.utc)
        fallback_start = end - timedelta(hours=2)

        # Equity bars
        yf_feed = self._get_yfinance_feed()
        for ticker in self.equity_tickers:
            start = self._get_backfill_start(ticker, self.bar_interval, fallback_start)
            if start >= end:
                continue
            try:
                bars = await loop.run_in_executor(
                    None, yf_feed.fetch_bars, ticker, self.bar_interval, start, end
                )
                self.store.write_bars(bars)
            except Exception as exc:
                logger.error("pipeline.poll_bars.equity_error", ticker=ticker, error=str(exc))

        # Crypto bars
        cg_feed = self._get_coingecko_feed()
        for ticker in self.crypto_tickers:
            start = self._get_backfill_start(ticker, "1d", fallback_start)
            if start >= end:
                continue
            try:
                bars = await loop.run_in_executor(
                    None, cg_feed.fetch_market_chart, ticker, 2  # last 2 days
                )
                self.store.write_bars(bars)
            except Exception as exc:
                logger.error("pipeline.poll_bars.crypto_error", ticker=ticker, error=str(exc))

    async def _poll_news(self) -> None:
        """
        Every-N-minutes job: fetch fresh news from NewsAPI and GDELT.

        Both sources are polled.  GDELT provides broader macro news coverage;
        NewsAPI provides more targeted company-specific articles.
        """
        loop = asyncio.get_event_loop()
        all_tickers = self.equity_tickers + [t.split("-")[0] for t in self.crypto_tickers]
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(minutes=self.news_poll_minutes * 2)  # overlap window

        # NewsAPI
        if settings.newsapi_key:
            newsapi_feed = self._get_newsapi_feed()
            try:
                articles = await loop.run_in_executor(
                    None,
                    lambda: newsapi_feed.fetch_news(
                        tickers=all_tickers, start=start, end=end, max_results=100
                    ),
                )
                inserted = self.store.write_news(articles)
                logger.info(
                    "pipeline.poll_news.newsapi",
                    fetched=len(articles),
                    inserted=inserted,
                )
            except Exception as exc:
                logger.error("pipeline.poll_news.newsapi_error", error=str(exc))

        # GDELT (always available — no API key needed)
        gdelt_feed = self._get_gdelt_feed()
        try:
            articles = await loop.run_in_executor(
                None,
                lambda: gdelt_feed.fetch_news(
                    tickers=all_tickers[:5],  # limit to top 5 tickers for GDELT
                    start=start,
                    end=end,
                    max_results=250,
                ),
            )
            inserted = self.store.write_news(articles)
            logger.info(
                "pipeline.poll_news.gdelt",
                fetched=len(articles),
                inserted=inserted,
            )
        except Exception as exc:
            logger.error("pipeline.poll_news.gdelt_error", error=str(exc))

    async def _poll_fundamentals(self) -> None:
        """
        Weekly job: refresh fundamental data for all equity tickers.

        Uses Alpha Vantage (primary) and SEC EDGAR (secondary/fallback).
        Runs sequentially with rate-limit delays embedded in the feeds.
        """
        loop = asyncio.get_event_loop()

        for ticker in self.equity_tickers:
            # Alpha Vantage (preferred — includes consensus estimates)
            if settings.alpha_vantage_key:
                av_feed = self._get_av_feed()
                try:
                    snapshots = await loop.run_in_executor(
                        None,
                        lambda t=ticker: av_feed.fetch_fundamentals(t, period="quarterly"),
                    )
                    inserted = self.store.write_fundamentals(snapshots)
                    logger.info(
                        "pipeline.poll_fundamentals.av",
                        ticker=ticker,
                        fetched=len(snapshots),
                        inserted=inserted,
                    )
                except Exception as exc:
                    logger.error(
                        "pipeline.poll_fundamentals.av_error",
                        ticker=ticker,
                        error=str(exc),
                    )

            # SEC EDGAR (EPS from actual filings — always available)
            edgar_feed = self._get_edgar_feed()
            try:
                snapshots = await loop.run_in_executor(
                    None,
                    lambda t=ticker: edgar_feed.fetch_fundamentals(t, period="quarterly"),
                )
                inserted = self.store.write_fundamentals(snapshots)
                logger.info(
                    "pipeline.poll_fundamentals.edgar",
                    ticker=ticker,
                    fetched=len(snapshots),
                    inserted=inserted,
                )
            except Exception as exc:
                logger.error(
                    "pipeline.poll_fundamentals.edgar_error",
                    ticker=ticker,
                    error=str(exc),
                )

    # ── Feed factory helpers ──────────────────────────────────────────────────

    def _get_yfinance_feed(self):
        if self._yfinance_feed is None:
            from data.feeds.yfinance_feed import YFinanceFeed
            self._yfinance_feed = YFinanceFeed()
        return self._yfinance_feed

    def _get_alpaca_feed(self):
        if self._alpaca_feed is None:
            from data.feeds.alpaca_feed import AlpacaFeed
            self._alpaca_feed = AlpacaFeed(config={
                "api_key": settings.alpaca_api_key,
                "secret_key": settings.alpaca_secret_key,
            })
        return self._alpaca_feed

    def _get_coingecko_feed(self):
        if self._coingecko_feed is None:
            from data.feeds.coingecko_feed import CoinGeckoFeed
            self._coingecko_feed = CoinGeckoFeed()
        return self._coingecko_feed

    def _get_binance_feed(self):
        if self._binance_feed is None:
            from data.feeds.binance_feed import BinanceFeed
            self._binance_feed = BinanceFeed(config={
                "api_key": settings.binance_api_key,
                "secret_key": settings.binance_secret_key,
                "testnet": settings.binance_testnet,
            })
        return self._binance_feed

    def _get_newsapi_feed(self):
        if self._newsapi_feed is None:
            from data.feeds.newsapi_feed import NewsApiFeed
            self._newsapi_feed = NewsApiFeed(config={"api_key": settings.newsapi_key})
        return self._newsapi_feed

    def _get_gdelt_feed(self):
        if self._gdelt_feed is None:
            from data.feeds.gdelt_feed import GdeltFeed
            self._gdelt_feed = GdeltFeed()
        return self._gdelt_feed

    def _get_av_feed(self):
        if self._av_feed is None:
            from data.feeds.alpha_vantage_feed import AlphaVantageFeed
            self._av_feed = AlphaVantageFeed(config={"api_key": settings.alpha_vantage_key})
        return self._av_feed

    def _get_edgar_feed(self):
        if self._edgar_feed is None:
            from data.feeds.sec_edgar_feed import SecEdgarFeed
            self._edgar_feed = SecEdgarFeed()
        return self._edgar_feed

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_backfill_start(
        self,
        ticker: str,
        interval: str,
        default_start: datetime,
    ) -> datetime:
        """
        Determine the start date for an incremental bar fetch.

        Returns the timestamp one bar after the latest stored bar.  If no data
        exists, returns ``default_start``.  This prevents re-downloading bars
        we already have.
        """
        latest = self.store.get_latest_bar_timestamp(ticker, interval)
        if latest is None:
            return default_start

        # Add one interval worth of time to avoid re-fetching the last bar
        interval_offsets = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
            "1wk": timedelta(weeks=1),
            "1mo": timedelta(days=30),
        }
        offset = interval_offsets.get(interval, timedelta(days=1))
        return latest.replace(tzinfo=timezone.utc) + offset

    # ── Public accessors ──────────────────────────────────────────────────────

    @property
    def latest_orderbooks(self) -> dict:
        """
        In-memory cache of the latest order book snapshot per crypto ticker.

        Updated by ``_stream_binance_orderbook`` as new snapshots arrive.
        Read by market-making strategies to get the current order book state.

        Returns
        -------
        dict[str, OrderBook]
            Mapping from canonical ticker to its latest ``OrderBook`` snapshot.
        """
        if not hasattr(self, "_latest_orderbooks"):
            self._latest_orderbooks: dict = {}
        return self._latest_orderbooks

    @property
    def is_running(self) -> bool:
        """True if the pipeline has been started and not yet stopped."""
        return self._running
