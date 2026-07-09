"""
data/feeds/yfinance_feed.py — Historical OHLCV data via Yahoo Finance (yfinance).

Yahoo Finance provides free daily and intraday OHLCV data for US equities,
ETFs, indices, and crypto.  It is the primary data source for backtesting
because:

* No API key required — anyone can run the backtests out of the box.
* Adjusted close prices are available by default, preventing artificial
  discontinuities from splits and dividends.
* Long history — typically 20+ years of daily data for major equities.

Limitations
-----------
* Rate limiting — Yahoo Finance has unpublished rate limits.  Requesting too
  many tickers simultaneously will result in 429 errors.  Use batching with
  short sleeps between requests when fetching large universes.
* Intraday data is only available for the last 60 days.
* Data quality can be inconsistent for small-cap or non-US tickers.

Interval mapping
----------------
yfinance uses its own interval string format.  This feed maps the canonical
system intervals to yfinance strings:

    System  →  yfinance
    "1m"    →  "1m"
    "5m"    →  "5m"
    "15m"   →  "15m"
    "1h"    →  "1h"
    "1d"    →  "1d"
    "1wk"   →  "1wk"
    "1mo"   →  "1mo"
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import structlog

from data.feeds.base import DataFeed
from data.schemas import OHLCVBar

logger = structlog.get_logger(__name__)

# yfinance uses its own interval strings; they happen to match ours for most
# timeframes but we map explicitly to be safe.
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1d": "1d",
    "1wk": "1wk",
    "1mo": "1mo",
}


class YFinanceFeed(DataFeed):
    """
    Historical OHLCV feed backed by Yahoo Finance via the ``yfinance`` library.

    This is a **synchronous, pull-only** feed — it does not support real-time
    streaming.  Use ``AlpacaFeed`` or ``BinanceFeed`` for live data.

    Parameters
    ----------
    config : dict, optional
        Accepts an optional ``"request_delay"`` key (float, seconds) to
        throttle requests and avoid rate-limiting.  Defaults to 0.5 s.

    Example
    -------
    ::

        from data.feeds.yfinance_feed import YFinanceFeed
        from datetime import datetime, timezone

        feed = YFinanceFeed()
        bars = feed.fetch_bars(
            ticker="AAPL",
            interval="1d",
            start=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        print(bars[0])
    """

    SOURCE = "yfinance"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._request_delay: float = self.config.get("request_delay", 0.5)

    def fetch_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        """
        Download historical OHLCV bars from Yahoo Finance.

        yfinance returns a DataFrame indexed by the bar open time.  We treat
        the bar open time as ``event_timestamp`` (the bar close would be
        ``open + interval``, but for daily data the open time at market open
        is the conventional event timestamp).

        Adjusted prices (``auto_adjust=True``) are used by default because
        backtesting on unadjusted prices introduces look-ahead bias through
        artificial price gaps at split/dividend dates.

        Parameters
        ----------
        ticker :
            Yahoo Finance ticker symbol.  Examples: ``"AAPL"``, ``"BTC-USD"``,
            ``"^VIX"``.
        interval :
            One of ``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``, ``"1h"``,
            ``"1d"``, ``"1wk"``, ``"1mo"``.
        start, end :
            UTC datetimes for the requested range.

        Returns
        -------
        list[OHLCVBar]
            Bars sorted ascending by ``event_timestamp``.  Empty list if
            Yahoo Finance returns no data for the requested range.
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError(
                "yfinance is not installed.  Run: pip install 'quant-engine[data]'"
            )

        yf_interval = _INTERVAL_MAP.get(interval, interval)
        fetch_ts = datetime.now(tz=timezone.utc)

        logger.info(
            "yfinance.fetch_bars",
            ticker=ticker,
            interval=interval,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        # multi_level_column was removed in yfinance ≥ 0.2.31 — drop it.
        # For a single ticker, yfinance already returns a flat column index.
        dl_kwargs: dict = dict(
            tickers=ticker,
            start=start,
            end=end,
            interval=yf_interval,
            auto_adjust=True,
            progress=False,
        )
        import inspect as _inspect
        if "multi_level_column" in _inspect.signature(yf.download).parameters:
            dl_kwargs["multi_level_column"] = False
        df: pd.DataFrame = yf.download(**dl_kwargs)

        # yfinance ≥ 0.2.31 returns a MultiIndex even for single tickers.
        # Flatten it so row access works the same way as before.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty:
            logger.warning("yfinance.fetch_bars.empty", ticker=ticker, interval=interval)
            return []

        # yfinance may return tz-aware or tz-naive index depending on interval
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            try:
                bar = OHLCVBar(
                    ticker=ticker,
                    interval=interval,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                    event_timestamp=ts.to_pydatetime(),
                    fetch_timestamp=fetch_ts,
                    source=self.SOURCE,
                    adjusted=True,
                )
                bars.append(bar)
            except Exception as exc:
                logger.warning(
                    "yfinance.bar_parse_error",
                    ticker=ticker,
                    ts=str(ts),
                    error=str(exc),
                )

        # Throttle to avoid hitting Yahoo Finance rate limits
        time.sleep(self._request_delay)

        logger.info(
            "yfinance.fetch_bars.done",
            ticker=ticker,
            bars_returned=len(bars),
        )
        return bars

    def fetch_bars_multi(
        self,
        tickers: list[str],
        interval: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[OHLCVBar]]:
        """
        Fetch historical bars for multiple tickers in a single yfinance call.

        yfinance can download multiple tickers in one request, which is more
        efficient than calling ``fetch_bars`` in a loop.  Use this method when
        building a universe for backtesting.

        Parameters
        ----------
        tickers :
            List of Yahoo Finance ticker symbols.
        interval, start, end :
            Same as ``fetch_bars``.

        Returns
        -------
        dict[str, list[OHLCVBar]]
            Mapping from ticker symbol to its list of bars.
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError(
                "yfinance is not installed.  Run: pip install 'quant-engine[data]'"
            )

        yf_interval = _INTERVAL_MAP.get(interval, interval)
        fetch_ts = datetime.now(tz=timezone.utc)

        logger.info(
            "yfinance.fetch_bars_multi",
            tickers=tickers,
            interval=interval,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        df: pd.DataFrame = yf.download(
            tickers=tickers,
            start=start,
            end=end,
            interval=yf_interval,
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )

        result: dict[str, list[OHLCVBar]] = {t: [] for t in tickers}

        if df.empty:
            logger.warning("yfinance.fetch_bars_multi.empty", tickers=tickers)
            return result

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        for ticker in tickers:
            try:
                # Multi-ticker download uses a MultiIndex column; single-ticker fallback
                if len(tickers) == 1:
                    ticker_df = df
                else:
                    ticker_df = df[ticker]

                for ts, row in ticker_df.iterrows():
                    try:
                        bar = OHLCVBar(
                            ticker=ticker,
                            interval=interval,
                            open=float(row["Open"]),
                            high=float(row["High"]),
                            low=float(row["Low"]),
                            close=float(row["Close"]),
                            volume=float(row["Volume"]),
                            event_timestamp=ts.to_pydatetime(),
                            fetch_timestamp=fetch_ts,
                            source=self.SOURCE,
                            adjusted=True,
                        )
                        result[ticker].append(bar)
                    except Exception as exc:
                        logger.warning(
                            "yfinance.multi_bar_parse_error",
                            ticker=ticker,
                            ts=str(ts),
                            error=str(exc),
                        )
            except KeyError:
                logger.warning("yfinance.ticker_missing_in_response", ticker=ticker)

        time.sleep(self._request_delay)
        return result
