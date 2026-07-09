"""
data/feeds/alpaca_feed.py — Real-time and historical equities data via Alpaca.

Alpaca provides two things in one API:
1. **Historical bars** (REST) — up to several years of 1-minute through daily
   OHLCV data for US equities and crypto via the ``alpaca-py`` SDK.
2. **Real-time streaming** (WebSocket) — live trade, quote, and bar events for
   subscribed symbols, pushed as they happen from the exchange.

This feed handles both use cases through the same class so the DataPipeline can
use a single adapter for Alpaca regardless of whether we are backtesting
(historical pull) or running live/paper (streaming push).

Authentication
--------------
Alpaca requires an API key and secret.  The credentials are read from
``config.settings``:

    settings.alpaca_api_key      → ALPACA_API_KEY in .env
    settings.alpaca_secret_key   → ALPACA_SECRET_KEY in .env

Paper trading uses the same API key set as live trading — the URL endpoint
determines whether orders are simulated or real.  For data only (no order
routing) the paper/live distinction is irrelevant.

alpaca-py SDK overview
----------------------
* ``StockHistoricalDataClient`` — REST client for bars, trades, quotes.
* ``StockDataStream``           — WebSocket client for real-time equities.
* ``CryptoHistoricalDataClient`` — REST for crypto bars.
* ``CryptoDataStream``           — WebSocket for real-time crypto.

We use ``StockHistoricalDataClient`` for equities history and
``StockDataStream`` for real-time equities.  CoinGecko handles crypto history;
Binance handles real-time crypto.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

import structlog

from data.feeds.base import DataFeed
from data.schemas import OHLCVBar

logger = structlog.get_logger(__name__)

# Canonical → Alpaca interval mapping
# Alpaca uses TimeFrame objects; we map from our string intervals.
_INTERVAL_TO_TIMEFRAME: dict[str, str] = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
    "1d": "1Day",
}


class AlpacaFeed(DataFeed):
    """
    Data feed backed by the Alpaca Markets API (``alpaca-py`` SDK).

    Supports both **historical bar pulls** (for backtesting) and **real-time
    bar streaming** (for paper/live trading).

    Parameters
    ----------
    config : dict, optional
        Expected keys:
        - ``"api_key"``    : Alpaca API key (falls back to settings)
        - ``"secret_key"`` : Alpaca secret key (falls back to settings)
        - ``"feed"``       : Market data feed — ``"iex"`` (free) or ``"sip"``
                             (paid, all US exchanges).  Defaults to ``"iex"``.

    Notes
    -----
    The free Alpaca data plan uses the IEX feed which covers ~80% of US equity
    volume.  For comprehensive coverage upgrade to the SIP feed.
    """

    SOURCE = "alpaca"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._api_key: str | None = self.config.get("api_key")
        self._secret_key: str | None = self.config.get("secret_key")
        self._feed: str = self.config.get("feed", "iex")

        # Lazy-initialize SDK clients so we don't fail on import if alpaca-py
        # is not installed
        self._hist_client = None
        self._stream_client = None

    def _get_hist_client(self):
        """Lazily create the Alpaca historical data REST client."""
        if self._hist_client is None:
            try:
                from alpaca.data.historical import StockHistoricalDataClient
            except ImportError:
                raise ImportError(
                    "alpaca-py is not installed.  Run: pip install 'quant-engine[data]'"
                )
            # alpaca-py accepts None keys for unauthenticated (free) access
            self._hist_client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._hist_client

    def fetch_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        """
        Fetch historical equity OHLCV bars from Alpaca's REST API.

        Alpaca paginates results automatically in alpaca-py.  The SDK handles
        cursor pagination internally — we receive a complete list of bars for
        the requested range without manual paging.

        Parameters
        ----------
        ticker :
            Alpaca symbol format: ``"AAPL"``, ``"MSFT"``.  (Not the yfinance
            ``"AAPL"`` format with caret for indices.)
        interval :
            One of ``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``, ``"1h"``, ``"1d"``.
        start, end :
            UTC datetimes.

        Returns
        -------
        list[OHLCVBar]
            Bars sorted ascending by ``event_timestamp``.
        """
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        except ImportError:
            raise ImportError(
                "alpaca-py is not installed.  Run: pip install 'quant-engine[data]'"
            )

        client = self._get_hist_client()
        fetch_ts = datetime.now(tz=timezone.utc)

        # Map our interval string to Alpaca TimeFrame
        tf = self._parse_timeframe(interval)

        logger.info(
            "alpaca.fetch_bars",
            ticker=ticker,
            interval=interval,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=tf,
            start=start,
            end=end,
            feed=self._feed,
            adjustment="all",  # split + dividend adjusted
        )

        response = client.get_stock_bars(request)
        raw_bars = response.get(ticker, [])

        bars: list[OHLCVBar] = []
        for b in raw_bars:
            try:
                # Alpaca bar timestamps are tz-aware; convert to UTC
                ts = b.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                bar = OHLCVBar(
                    ticker=ticker,
                    interval=interval,
                    open=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume),
                    event_timestamp=ts,
                    fetch_timestamp=fetch_ts,
                    source=self.SOURCE,
                    adjusted=True,
                )
                bars.append(bar)
            except Exception as exc:
                logger.warning("alpaca.bar_parse_error", ticker=ticker, error=str(exc))

        logger.info("alpaca.fetch_bars.done", ticker=ticker, bars_returned=len(bars))
        return bars

    async def stream_bars(
        self, tickers: list[str], interval: str = "1m"
    ) -> AsyncIterator[OHLCVBar]:
        """
        Async generator that yields live OHLCV bars from Alpaca's WebSocket.

        Alpaca's ``StockDataStream`` delivers completed 1-minute bars to
        subscribers.  This method subscribes to the requested tickers and
        re-emits each bar as a canonical ``OHLCVBar``.

        The generator runs indefinitely until the caller breaks the loop or
        cancels the enclosing task.

        Parameters
        ----------
        tickers :
            List of equity symbols to subscribe to.
        interval :
            Bar duration.  Alpaca's streaming only supports ``"1m"`` bars;
            larger intervals must be assembled by the feature pipeline from
            1-minute bars.

        Yields
        ------
        OHLCVBar
            One bar per completed bar event from the stream.
        """
        try:
            from alpaca.data.live import StockDataStream
        except ImportError:
            raise ImportError(
                "alpaca-py is not installed.  Run: pip install 'quant-engine[data]'"
            )

        import concurrent.futures as _cf

        loop = asyncio.get_event_loop()
        # Sentinel placed on the queue when the stream thread dies with an error.
        _error_sentinel: list[Exception] = []
        queue: asyncio.Queue[OHLCVBar] = asyncio.Queue()

        async def bar_handler(bar) -> None:
            """Alpaca callback — convert bar to OHLCVBar and enqueue."""
            fetch_ts = datetime.now(tz=timezone.utc)
            ts = bar.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            try:
                canonical = OHLCVBar(
                    ticker=bar.symbol,
                    interval="1m",
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume),
                    event_timestamp=ts,
                    fetch_timestamp=fetch_ts,
                    source=self.SOURCE,
                    adjusted=False,  # real-time bars are not adjusted
                )
                asyncio.run_coroutine_threadsafe(queue.put(canonical), loop)
            except Exception as exc:
                logger.warning("alpaca.stream_bar_parse_error", error=str(exc))

        stream = StockDataStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
        )

        # Patch _start_ws on this instance so that "auth failed" stops the
        # retry loop immediately.  The library's _run_forever only short-circuits
        # on "insufficient subscription"; all other ValueErrors — including
        # "auth failed" — are logged and retried indefinitely.  By setting
        # _should_run=False here we make the outer while-loop exit cleanly.
        _original_start_ws = stream._start_ws.__func__

        async def _patched_start_ws(self_stream) -> None:
            try:
                await _original_start_ws(self_stream)
            except ValueError as exc:
                if "auth" in str(exc).lower():
                    # Stop the retry loop by clearing the run flag, then
                    # propagate so the error is surfaced to the thread wrapper.
                    self_stream._should_run = False
                raise

        import types
        stream._start_ws = types.MethodType(_patched_start_ws, stream)

        def _run_and_signal() -> None:
            """Run stream.run(); on any exception push it onto _error_sentinel.

            The alpaca-py library logs every ValueError via log.exception before
            we can intercept it.  We silence that logger for the duration of the
            run so that auth failures don't produce noisy tracebacks — our own
            structured logger emits the single error line that matters.
            """
            import logging as _logging
            _alpaca_ws_logger = _logging.getLogger("alpaca.data.live.websocket")
            _prev_level = _alpaca_ws_logger.level
            _alpaca_ws_logger.setLevel(_logging.CRITICAL)
            try:
                stream.run()
            except Exception as exc:
                _error_sentinel.append(exc)
                asyncio.run_coroutine_threadsafe(
                    queue.put(None),  # type: ignore[arg-type]  # wakes queue.get()
                    loop,
                )
            finally:
                _alpaca_ws_logger.setLevel(_prev_level)

        stream.subscribe_bars(bar_handler, *tickers)

        # alpaca-py's stream.run() calls loop.run_until_complete() internally,
        # which conflicts with our already-running asyncio event loop.
        # Run it in a thread executor so it gets its own blocking call.
        executor = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpaca_stream")
        executor.submit(_run_and_signal)
        logger.info("alpaca.stream_bars.started", tickers=tickers)

        try:
            while True:
                bar = await queue.get()
                # None is the error/stop sentinel put by _run_and_signal
                if bar is None:
                    if _error_sentinel:
                        raise _error_sentinel[0]
                    return
                yield bar
        finally:
            try:
                stream.stop()
            except Exception:
                pass
            executor.shutdown(wait=False)
            logger.info("alpaca.stream_bars.stopped", tickers=tickers)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_timeframe(interval: str):
        """Convert our interval string to an Alpaca ``TimeFrame`` object."""
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        mapping = {
            "1m":  TimeFrame(1,  TimeFrameUnit.Minute),
            "5m":  TimeFrame(5,  TimeFrameUnit.Minute),
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
            "30m": TimeFrame(30, TimeFrameUnit.Minute),
            "1h":  TimeFrame(1,  TimeFrameUnit.Hour),
            "1d":  TimeFrame(1,  TimeFrameUnit.Day),
        }
        if interval not in mapping:
            raise ValueError(
                f"Unsupported Alpaca interval: {interval!r}.  "
                f"Choose from: {list(mapping)}"
            )
        return mapping[interval]
