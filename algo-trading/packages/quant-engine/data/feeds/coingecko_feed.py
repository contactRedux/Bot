"""
data/feeds/coingecko_feed.py — Historical and polling-based crypto OHLCV via CoinGecko.

CoinGecko is used for **historical crypto backtesting data** — it provides free
daily OHLCV data going back several years for most major cryptocurrencies.  For
real-time crypto data we use Binance (see ``binance_feed.py``).

CoinGecko vs Binance for historical data
-----------------------------------------
* CoinGecko free tier covers daily candles going back years — sufficient for
  backtesting with daily/weekly bars.
* Binance provides up to ~3 years of 1-minute historical klines via REST, but
  only for pairs actively traded on Binance (e.g. ``"BTCUSDT"``).
* For backtesting, CoinGecko is preferred because it has a longer history and
  covers more coins, especially smaller-cap tokens.

Ticker format
-------------
CoinGecko uses its own coin IDs (e.g. ``"bitcoin"``, ``"ethereum"``) rather
than the exchange ticker format.  This feed accepts both:
* CoinGecko IDs: ``"bitcoin"``, ``"ethereum"``
* Common symbols: ``"BTC"``, ``"ETH"`` (mapped to CoinGecko IDs internally)

A subset of common mappings is maintained in ``_SYMBOL_TO_ID``.  For coins
not in the table, pass the CoinGecko ID directly.

OHLCV availability
------------------
CoinGecko's ``/coins/{id}/ohlc`` endpoint returns candlestick data with
granularity depending on the requested range:
* 1–2 days   → 30-minute candles
* 3–30 days  → 4-hour candles
* 31–90 days → 4-hour candles
* 91+ days   → daily candles

For backtesting at daily resolution, request ranges > 90 days per call.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog

from data.feeds.base import DataFeed
from data.schemas import OHLCVBar

logger = structlog.get_logger(__name__)

# Common crypto symbol → CoinGecko coin ID mapping
_SYMBOL_TO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "ATOM": "cosmos",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
}


def _resolve_coin_id(ticker: str) -> str:
    """
    Convert a ticker/symbol to a CoinGecko coin ID.

    Handles formats like ``"BTC-USD"`` (our canonical crypto format), bare
    symbols (``"BTC"``), and raw CoinGecko IDs (``"bitcoin"``).
    """
    # Strip quote currency: "BTC-USD" → "BTC"
    base = ticker.split("-")[0].upper()
    return _SYMBOL_TO_ID.get(base, ticker.lower())


class CoinGeckoFeed(DataFeed):
    """
    Crypto OHLCV feed backed by the CoinGecko public API (``pycoingecko``).

    Supports historical bar pulls only — use ``BinanceFeed`` for real-time data.

    Parameters
    ----------
    config : dict, optional
        Accepts ``"vs_currency"`` (default ``"usd"``) and
        ``"request_delay"`` (float seconds, default 1.2 to stay within
        CoinGecko's free-tier rate limit of ~50 calls/minute).

    Rate limits
    -----------
    CoinGecko's free API allows ~10–50 calls/minute depending on server load.
    We default to a 1.2-second delay between requests, which keeps us well
    under the limit for sequential fetching.
    """

    SOURCE = "coingecko"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._vs_currency: str = self.config.get("vs_currency", "usd")
        self._request_delay: float = self.config.get("request_delay", 1.2)
        self._client = None

    def _get_client(self):
        """Lazily create the pycoingecko client."""
        if self._client is None:
            try:
                from pycoingecko import CoinGeckoAPI
            except ImportError:
                raise ImportError(
                    "pycoingecko is not installed.  Run: pip install 'quant-engine[data]'"
                )
            self._client = CoinGeckoAPI()
        return self._client

    def fetch_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        """
        Fetch historical OHLCV candles from CoinGecko.

        CoinGecko's OHLCV endpoint returns data in arrays of
        ``[timestamp_ms, open, high, low, close]`` (no separate volume in the
        OHLC endpoint).  We supplement with market-cap data for an approximate
        volume figure when needed, but default volume to 0 since the OHLC
        endpoint does not include it.

        For actual volume data, use ``/coins/{id}/market_chart/range`` which
        provides ``prices``, ``market_caps``, and ``total_volumes`` arrays,
        but *not* OHLCV.  A production-grade system would join both endpoints.
        For backtesting strategy logic that uses volume (VWAP, OBV) we
        recommend Binance for crypto data when volume accuracy matters.

        Parameters
        ----------
        ticker :
            Ticker in any of: ``"BTC-USD"``, ``"BTC"``, ``"bitcoin"``.
        interval :
            Canonical interval string.  CoinGecko's OHLC granularity is
            determined by the date range, not a direct parameter.  For ranges
            > 90 days the granularity is always daily regardless of this value.
        start, end :
            UTC datetimes.

        Returns
        -------
        list[OHLCVBar]
            Bars sorted ascending by ``event_timestamp``.
        """
        client = self._get_client()
        coin_id = _resolve_coin_id(ticker)

        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        fetch_ts = datetime.now(tz=timezone.utc)

        days = max(1, (end - start).days)

        logger.info(
            "coingecko.fetch_bars",
            ticker=ticker,
            coin_id=coin_id,
            interval=interval,
            days=days,
        )

        # CoinGecko OHLC endpoint — returns [timestamp_ms, open, high, low, close]
        raw: list[list[float]] = client.get_coin_ohlc_by_id(
            id=coin_id,
            vs_currency=self._vs_currency,
            days=str(days),
        )

        bars: list[OHLCVBar] = []
        for entry in raw:
            try:
                ts_ms, o, h, l, c = entry  # no volume in OHLC endpoint
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

                # Filter to requested range
                if ts < start or ts > end:
                    continue

                bar = OHLCVBar(
                    ticker=ticker.upper() if "-" not in ticker else ticker,
                    interval=interval,
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=0.0,  # OHLC endpoint has no volume; see docstring
                    event_timestamp=ts,
                    fetch_timestamp=fetch_ts,
                    source=self.SOURCE,
                    adjusted=False,  # crypto is not adjusted
                )
                bars.append(bar)
            except Exception as exc:
                logger.warning("coingecko.bar_parse_error", error=str(exc))

        time.sleep(self._request_delay)
        logger.info("coingecko.fetch_bars.done", ticker=ticker, bars_returned=len(bars))
        return sorted(bars, key=lambda b: b.event_timestamp)

    def fetch_market_chart(
        self,
        ticker: str,
        days: int = 365,
    ) -> list[OHLCVBar]:
        """
        Fetch OHLCV-style data using CoinGecko's market_chart/range endpoint
        which includes volume data (unlike the /ohlc endpoint).

        This is a more complete data source for strategies that need volume.
        Returns daily bars built from the prices + total_volumes arrays.

        Parameters
        ----------
        ticker :
            Same format as ``fetch_bars``.
        days :
            Number of days of history to retrieve.

        Returns
        -------
        list[OHLCVBar]
            Daily bars.  ``open == close == last_price`` (market chart doesn't
            give true OHLCV) but volume is accurate.
        """
        client = self._get_client()
        coin_id = _resolve_coin_id(ticker)
        fetch_ts = datetime.now(tz=timezone.utc)

        logger.info("coingecko.fetch_market_chart", ticker=ticker, days=days)

        data = client.get_coin_market_chart_by_id(
            id=coin_id,
            vs_currency=self._vs_currency,
            days=days,
            interval="daily",
        )

        prices: list[list[float]] = data.get("prices", [])
        volumes: list[list[float]] = data.get("total_volumes", [])

        # Build a lookup: timestamp_ms → volume
        vol_map = {int(v[0]): v[1] for v in volumes}

        bars: list[OHLCVBar] = []
        for price_entry in prices:
            try:
                ts_ms = int(price_entry[0])
                price = float(price_entry[1])
                volume = float(vol_map.get(ts_ms, 0.0))
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

                bar = OHLCVBar(
                    ticker=ticker.upper() if "-" not in ticker else ticker,
                    interval="1d",
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=volume,
                    event_timestamp=ts,
                    fetch_timestamp=fetch_ts,
                    source=self.SOURCE,
                    adjusted=False,
                )
                bars.append(bar)
            except Exception as exc:
                logger.warning("coingecko.chart_parse_error", error=str(exc))

        time.sleep(self._request_delay)
        return sorted(bars, key=lambda b: b.event_timestamp)
