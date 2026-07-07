"""
data/feeds/binance_feed.py — Real-time crypto streaming and order book via Binance WebSocket.

Binance is the largest cryptocurrency exchange by volume and provides:
1. **Real-time trade stream** — individual trade events as they occur.
2. **Kline (candlestick) stream** — bar events at the close of each interval.
3. **Depth (order book) stream** — incremental order book updates with
   configurable depth (5, 10, or 20 levels).

All of the above are delivered over WebSocket at zero cost (no API key
required for market data streams).  An API key is only needed for authenticated
endpoints (placing orders, viewing account balance).

Symbol format
-------------
Binance uses ``BTCUSDT`` format (no hyphen, quote currency appended directly).
Our canonical format is ``BTC-USD``.  This feed converts between the two:
    ``"BTC-USD"`` → ``"BTCUSDT"`` (the most liquid Binance pair)
    ``"ETH-USD"`` → ``"ETHUSDT"``

Historical klines (REST)
------------------------
Binance's ``/api/v3/klines`` endpoint returns historical 1-minute through
monthly OHLCV data.  Limits per request: 1000 bars.  For longer ranges we
paginate automatically.

Real-time streaming (WebSocket)
---------------------------------
The ``python-binance`` library's ``BinanceSocketManager`` provides async
WebSocket streams.  We use:
* ``symbol@kline_1m`` — 1-minute closed candles
* ``symbol@depth10@100ms`` — top 10 bid/ask levels, updated every 100ms

Both streams are exposed as async generators in this feed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

import structlog

from data.feeds.base import DataFeed
from data.schemas import OHLCVBar, OrderBook, OrderBookLevel

logger = structlog.get_logger(__name__)

# Canonical symbol → Binance symbol
# Most pairs trade against USDT (stablecoin pegged to USD)
_CANONICAL_TO_BINANCE: dict[str, str] = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
    "ADA-USD": "ADAUSDT",
    "DOGE-USD": "DOGEUSDT",
    "AVAX-USD": "AVAXUSDT",
    "MATIC-USD": "MATICUSDT",
    "DOT-USD": "DOTUSDT",
    "LINK-USD": "LINKUSDT",
    "LTC-USD": "LTCUSDT",
}

# Canonical interval → Binance kline interval
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1wk": "1w",
    "1mo": "1M",
}


def _to_binance_symbol(ticker: str) -> str:
    """Convert canonical ticker to Binance symbol format."""
    if ticker in _CANONICAL_TO_BINANCE:
        return _CANONICAL_TO_BINANCE[ticker]
    # Fallback: strip hyphen and append USDT for USD-quoted pairs
    parts = ticker.split("-")
    if len(parts) == 2:
        base, quote = parts
        return f"{base}{quote}T" if quote == "USD" else f"{base}{quote}"
    return ticker.replace("-", "")


class BinanceFeed(DataFeed):
    """
    Crypto data feed backed by the Binance API (``python-binance`` library).

    Supports:
    * **Historical klines** (REST, ``fetch_bars``) — 1m to monthly bars.
    * **Real-time bar streaming** (WebSocket, ``stream_bars``) — live kline events.
    * **Order book streaming** (WebSocket, ``stream_orderbook``) — live depth updates.

    Parameters
    ----------
    config : dict, optional
        Accepts:
        - ``"api_key"``    : Binance API key (required for auth'd endpoints only)
        - ``"secret_key"`` : Binance secret key
        - ``"testnet"``    : bool — use Binance testnet when True (default True)
        - ``"klines_per_request"`` : int, max bars per REST page (default 1000)

    Notes
    -----
    Market data streams (klines, depth) do NOT require an API key.  An API key
    is only needed when placing orders or querying account state.
    """

    SOURCE = "binance"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._api_key: str | None = self.config.get("api_key")
        self._secret_key: str | None = self.config.get("secret_key")
        self._testnet: bool = self.config.get("testnet", True)
        self._klines_per_request: int = self.config.get("klines_per_request", 1000)

    def _get_client(self):
        """Lazily create the synchronous Binance REST client."""
        try:
            from binance.client import Client
        except ImportError:
            raise ImportError(
                "python-binance is not installed.  "
                "Run: pip install 'quant-engine[data]'"
            )
        client = Client(
            api_key=self._api_key or "",
            api_secret=self._secret_key or "",
            testnet=self._testnet,
        )
        return client

    def fetch_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        """
        Fetch historical klines (candlestick data) from Binance REST API.

        Binance limits each request to 1000 bars.  For longer ranges this
        method paginates automatically: after each page it advances ``start``
        to the timestamp of the last bar and requests the next batch.

        Kline format from Binance
        -------------------------
        Each kline is a list of 12 elements:
        ``[open_time, open, high, low, close, volume, close_time, quote_volume,
           num_trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]``

        We use ``open_time`` as ``event_timestamp`` (consistent with our
        convention of using the bar open time as the bar's timestamp).

        Parameters
        ----------
        ticker :
            Canonical ticker (``"BTC-USD"``) or Binance symbol (``"BTCUSDT"``).
        interval :
            One of ``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``, ``"1h"``,
            ``"4h"``, ``"1d"``, ``"1wk"``, ``"1mo"``.
        start, end :
            UTC datetimes.

        Returns
        -------
        list[OHLCVBar]
            All bars in the range, sorted ascending by ``event_timestamp``.
        """
        client = self._get_client()
        binance_symbol = _to_binance_symbol(ticker)
        binance_interval = _INTERVAL_MAP.get(interval, interval)

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        fetch_ts = datetime.now(tz=timezone.utc)

        logger.info(
            "binance.fetch_bars",
            ticker=ticker,
            binance_symbol=binance_symbol,
            interval=interval,
        )

        all_klines: list = []
        current_start_ms = start_ms

        while current_start_ms < end_ms:
            klines = client.get_historical_klines(
                symbol=binance_symbol,
                interval=binance_interval,
                start_str=current_start_ms,
                end_str=end_ms,
                limit=self._klines_per_request,
            )
            if not klines:
                break
            all_klines.extend(klines)
            # Advance to next page: last bar's close time + 1ms
            current_start_ms = int(klines[-1][6]) + 1

        bars: list[OHLCVBar] = []
        for k in all_klines:
            try:
                open_time_ms = int(k[0])
                ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)

                bar = OHLCVBar(
                    ticker=ticker,
                    interval=interval,
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    event_timestamp=ts,
                    fetch_timestamp=fetch_ts,
                    source=self.SOURCE,
                    adjusted=False,  # crypto has no splits/dividends
                )
                bars.append(bar)
            except Exception as exc:
                logger.warning("binance.kline_parse_error", error=str(exc))

        logger.info("binance.fetch_bars.done", ticker=ticker, bars_returned=len(bars))
        return bars

    async def stream_bars(
        self, tickers: list[str], interval: str = "1m"
    ) -> AsyncIterator[OHLCVBar]:
        """
        Async generator that yields completed kline bars from Binance WebSocket.

        Binance pushes a kline event on every tick with ``kline.is_closed``
        indicating whether the current bar has closed.  We only yield bars
        when ``is_closed == True`` to avoid emitting partial/incomplete bars.

        Parameters
        ----------
        tickers :
            Canonical ticker symbols (``"BTC-USD"``, ``"ETH-USD"``).
        interval :
            Bar duration — ``"1m"`` is the standard for live strategies.

        Yields
        ------
        OHLCVBar
            One bar per completed kline event.
        """
        try:
            from binance import AsyncClient, BinanceSocketManager
        except ImportError:
            raise ImportError(
                "python-binance is not installed.  "
                "Run: pip install 'quant-engine[data]'"
            )

        binance_symbols = [_to_binance_symbol(t) for t in tickers]
        # Map binance symbol back to canonical for the output OHLCVBar
        reverse_map = {_to_binance_symbol(t): t for t in tickers}

        queue: asyncio.Queue[OHLCVBar] = asyncio.Queue()

        async def _stream():
            client = await AsyncClient.create(
                api_key=self._api_key or "",
                api_secret=self._secret_key or "",
                testnet=self._testnet,
            )
            bm = BinanceSocketManager(client)
            binance_interval = _INTERVAL_MAP.get(interval, interval)

            # Build a combined multiplex stream for all tickers
            streams = [
                f"{sym.lower()}@kline_{binance_interval}"
                for sym in binance_symbols
            ]
            async with bm.multiplex_socket(streams) as ms:
                while True:
                    msg = await ms.recv()
                    data = msg.get("data", {})
                    if data.get("e") != "kline":
                        continue
                    kline = data["k"]
                    if not kline.get("x", False):
                        continue  # bar not yet closed

                    fetch_ts = datetime.now(tz=timezone.utc)
                    binance_sym = kline["s"]
                    canonical_ticker = reverse_map.get(binance_sym, binance_sym)
                    ts = datetime.fromtimestamp(int(kline["t"]) / 1000, tz=timezone.utc)

                    try:
                        bar = OHLCVBar(
                            ticker=canonical_ticker,
                            interval=interval,
                            open=float(kline["o"]),
                            high=float(kline["h"]),
                            low=float(kline["l"]),
                            close=float(kline["c"]),
                            volume=float(kline["v"]),
                            event_timestamp=ts,
                            fetch_timestamp=fetch_ts,
                            source=self.SOURCE,
                            adjusted=False,
                        )
                        await queue.put(bar)
                    except Exception as exc:
                        logger.warning("binance.stream_parse_error", error=str(exc))

        stream_task = asyncio.create_task(_stream())
        logger.info("binance.stream_bars.started", tickers=tickers, interval=interval)

        try:
            while True:
                bar = await queue.get()
                yield bar
        finally:
            stream_task.cancel()
            logger.info("binance.stream_bars.stopped")

    async def stream_orderbook(
        self, tickers: list[str], depth: int = 10
    ) -> AsyncIterator[OrderBook]:
        """
        Async generator that yields order book snapshots from Binance.

        Subscribes to Binance's ``{symbol}@depth{depth}@100ms`` stream which
        delivers the top N bid/ask levels every 100ms.  The ``depth`` parameter
        must be 5, 10, or 20 (Binance's supported values).

        The order book is the primary input for the market-making strategy
        (Sub-Task 5) — it uses the bid/ask imbalance at the top of the book to
        determine optimal quote prices and detect short-term price pressure.

        Parameters
        ----------
        tickers :
            Canonical ticker symbols.
        depth :
            Number of price levels to include (5, 10, or 20).

        Yields
        ------
        OrderBook
            One snapshot per update event (approx. every 100ms per ticker).
        """
        try:
            from binance import AsyncClient, BinanceSocketManager
        except ImportError:
            raise ImportError(
                "python-binance is not installed.  "
                "Run: pip install 'quant-engine[data]'"
            )

        # Binance only supports specific depths
        valid_depths = {5, 10, 20}
        if depth not in valid_depths:
            depth = 10  # default to 10 levels

        binance_symbols = [_to_binance_symbol(t) for t in tickers]
        reverse_map = {_to_binance_symbol(t): t for t in tickers}
        queue: asyncio.Queue[OrderBook] = asyncio.Queue()

        async def _stream():
            client = await AsyncClient.create(
                api_key=self._api_key or "",
                api_secret=self._secret_key or "",
                testnet=self._testnet,
            )
            bm = BinanceSocketManager(client)

            streams = [
                f"{sym.lower()}@depth{depth}@100ms"
                for sym in binance_symbols
            ]
            async with bm.multiplex_socket(streams) as ms:
                while True:
                    msg = await ms.recv()
                    data = msg.get("data", {})
                    if "bids" not in data or "asks" not in data:
                        continue

                    fetch_ts = datetime.now(tz=timezone.utc)
                    # Extract symbol from stream name: "btcusdt@depth10@100ms"
                    stream_name = msg.get("stream", "")
                    binance_sym = stream_name.split("@")[0].upper()
                    canonical_ticker = reverse_map.get(binance_sym, binance_sym)

                    try:
                        bids = [
                            OrderBookLevel(price=float(b[0]), quantity=float(b[1]))
                            for b in data["bids"]
                        ]
                        asks = [
                            OrderBookLevel(price=float(a[0]), quantity=float(a[1]))
                            for a in data["asks"]
                        ]

                        snapshot = OrderBook(
                            ticker=canonical_ticker,
                            bids=sorted(bids, key=lambda x: x.price, reverse=True),
                            asks=sorted(asks, key=lambda x: x.price),
                            depth=depth,
                            event_timestamp=fetch_ts,
                            fetch_timestamp=fetch_ts,
                            source=self.SOURCE,
                        )
                        await queue.put(snapshot)
                    except Exception as exc:
                        logger.warning("binance.orderbook_parse_error", error=str(exc))

        stream_task = asyncio.create_task(_stream())
        logger.info("binance.stream_orderbook.started", tickers=tickers, depth=depth)

        try:
            while True:
                ob = await queue.get()
                yield ob
        finally:
            stream_task.cancel()
            logger.info("binance.stream_orderbook.stopped")
