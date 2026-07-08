"""
data/feeds/bloomberg_feed.py — Bloomberg B-PIPE adapter (optional dependency).

This module implements a Bloomberg data feed using the ``blpapi`` Python SDK.
``blpapi`` is an **optional** dependency — the module imports cleanly when it
is absent.  The feed gracefully signals unavailability at instantiation time
rather than at import time, so the rest of the system never fails to start
because Bloomberg is not installed or not configured.

Availability check
------------------
Call ``BloombergFeed.is_available()`` (class method) to determine whether
``blpapi`` is installed *and* a Bloomberg session can be opened.  The
``DataPipeline`` calls this before constructing the feed and skips Bloomberg
gracefully when it returns ``False``.

Supported operations
--------------------
* ``fetch_bars``  — historical OHLCV via BDP/BDH (HistoricalDataRequest)
* ``fetch_news``  — headline + body via NEWS_STORY_RT_REQUEST where available

Bloomberg ticker mapping
------------------------
Bloomberg uses a different ticker format from the rest of the system:

    Platform ticker → Bloomberg ticker
    "AAPL"          → "AAPL US Equity"
    "BTC-USD"       → (unsupported; crypto is not covered by B-PIPE)
    "MSFT"          → "MSFT US Equity"

The ``_to_bloomberg_ticker`` helper applies this mapping.  Unknown mappings
fall through unchanged so callers can pass native Bloomberg tickers directly.

Session management
------------------
The Bloomberg session is opened lazily on first use and reused for all
subsequent calls.  The session is **synchronous** — Bloomberg's async API is
not used here because the existing pipeline runs all feed calls through
``asyncio.run_in_executor``, so a synchronous implementation is correct.

Design constraints (Phase 2 non-goals)
---------------------------------------
* Not a tick-by-tick streaming plant (``stream_bars`` is not implemented)
* Not a Bloomberg Terminal replacement (no EQS screens, DES pages, etc.)
* Free-source fallbacks are never removed from the pipeline
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import structlog

from data.feeds.base import DataFeed
from data.schemas import NewsArticle, OHLCVBar

logger = structlog.get_logger(__name__)

# ── Optional blpapi import ─────────────────────────────────────────────────────

try:
    import blpapi  # type: ignore[import-untyped]

    _BLPAPI_AVAILABLE = True
except ImportError:
    blpapi = None  # type: ignore[assignment]
    _BLPAPI_AVAILABLE = False


# ── Bloomberg interval map ─────────────────────────────────────────────────────

# Maps platform interval strings to Bloomberg periodicitySelection values
_INTERVAL_TO_BBG_PERIODICITY: dict[str, str] = {
    "1d": "DAILY",
    "1wk": "WEEKLY",
    "1mo": "MONTHLY",
}

# Bloomberg field names for OHLCV
_BBG_OHLCV_FIELDS = ["PX_OPEN", "PX_HIGH", "PX_LOW", "PX_LAST", "PX_VOLUME"]


# ── Ticker mapping ─────────────────────────────────────────────────────────────

def _to_bloomberg_ticker(ticker: str) -> str | None:
    """
    Convert a platform-canonical ticker to Bloomberg yellow-key format.

    Returns ``None`` for tickers that cannot be mapped (e.g. crypto BASE-USD
    pairs, which are not supported by B-PIPE equity data).

    Examples
    --------
    >>> _to_bloomberg_ticker("AAPL")
    'AAPL US Equity'
    >>> _to_bloomberg_ticker("BTC-USD")
    None
    """
    # Crypto tickers use "BASE-QUOTE" format — not covered by B-PIPE
    if "-" in ticker:
        return None
    # Index tickers (e.g. ^GSPC) are not directly mappable via this simple rule
    if ticker.startswith("^"):
        return None
    return f"{ticker} US Equity"


def _make_article_id(source: str, title: str, published_at: str) -> str:
    """Create a stable article identifier from its key fields."""
    raw = f"{source}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── BloombergFeed ──────────────────────────────────────────────────────────────

class BloombergFeed(DataFeed):
    """
    Bloomberg B-PIPE data feed adapter.

    Requires ``blpapi`` to be installed and a Bloomberg Desktop or B-PIPE
    server to be reachable at the configured host/port.  When either
    precondition is not met the feed's ``is_available()`` method returns
    ``False`` and the ``DataPipeline`` falls back to free-tier sources.

    Parameters
    ----------
    config : dict, optional
        Accepted keys:

        ``"host"`` : str
            Bloomberg server hostname (default ``"localhost"``).
        ``"port"`` : int
            Bloomberg server port (default ``8194``).
        ``"app_name"`` : str | None
            Registered application name for B-PIPE (default ``None``).
        ``"timeout_seconds"`` : int
            Request timeout in seconds (default ``30``).

    Notes
    -----
    Pass ``config`` directly or rely on the ``DataPipeline`` which reads the
    values from ``config.settings``.

    Example
    -------
    ::

        from data.feeds.bloomberg_feed import BloombergFeed
        from datetime import datetime, timezone

        feed = BloombergFeed(config={
            "host": "localhost",
            "port": 8194,
            "app_name": "myapp",
            "timeout_seconds": 30,
        })

        if BloombergFeed.is_available():
            bars = feed.fetch_bars(
                "AAPL",
                "1d",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 6, 30, tzinfo=timezone.utc),
            )
    """

    SOURCE = "bloomberg"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._host: str = self.config.get("host", "localhost")
        self._port: int = int(self.config.get("port", 8194))
        self._app_name: str | None = self.config.get("app_name")
        self._timeout_ms: int = int(self.config.get("timeout_seconds", 30)) * 1000
        self._session: Any = None  # blpapi.Session, opened lazily

    # ── Availability ──────────────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        """
        Return ``True`` if ``blpapi`` is installed.

        Does NOT attempt a live connection — connection errors are handled
        gracefully inside ``_get_session``.  This method is intended for a
        fast pre-check that avoids constructing the feed at all when the
        library is absent.
        """
        return _BLPAPI_AVAILABLE

    # ── Session management ────────────────────────────────────────────────────

    def _get_session(self) -> Any:
        """
        Open (or reuse) a Bloomberg API session.

        Raises
        ------
        RuntimeError
            If ``blpapi`` is not installed or the session fails to start.
        """
        if not _BLPAPI_AVAILABLE:
            raise RuntimeError(
                "blpapi is not installed.  "
                "Run: pip install 'quant-engine[bloomberg]'"
            )

        if self._session is not None:
            return self._session

        session_options = blpapi.SessionOptions()
        session_options.setServerHost(self._host)
        session_options.setServerPort(self._port)
        if self._app_name:
            session_options.setAuthenticationOptions(
                f"AuthenticationMode=APPLICATION_ONLY;"
                f"ApplicationAuthenticationType=APPNAME_AND_KEY;"
                f"ApplicationName={self._app_name}"
            )

        session = blpapi.Session(session_options)
        if not session.start():
            raise RuntimeError(
                f"Failed to start Bloomberg session at {self._host}:{self._port}. "
                "Ensure the Bloomberg Desktop or B-PIPE server is running."
            )

        if not session.openService("//blp/refdata"):
            session.stop()
            raise RuntimeError("Failed to open Bloomberg //blp/refdata service.")

        self._session = session
        logger.info(
            "bloomberg.session.opened",
            host=self._host,
            port=self._port,
        )
        return self._session

    def _close_session(self) -> None:
        """Stop the Bloomberg session if it is open."""
        if self._session is not None:
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None

    # ── Historical OHLCV ──────────────────────────────────────────────────────

    def fetch_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        """
        Fetch historical OHLCV bars via Bloomberg HistoricalDataRequest.

        Only daily/weekly/monthly intervals are supported by BDH.  Sub-daily
        intervals fall back to an empty list (the pipeline's yfinance/Alpaca
        feeds cover intraday).

        Parameters
        ----------
        ticker :
            Platform-canonical ticker (e.g. ``"AAPL"``).  Crypto tickers
            (``"BTC-USD"``) return an empty list — not covered by B-PIPE.
        interval :
            Bar duration string (``"1d"``, ``"1wk"``, ``"1mo"``).
        start, end :
            UTC datetime range (inclusive).

        Returns
        -------
        list[OHLCVBar]
            Bars sorted ascending by ``event_timestamp``.  Returns ``[]`` when
            the ticker is not mapped or the interval is unsupported.
        """
        bbg_ticker = _to_bloomberg_ticker(ticker)
        if bbg_ticker is None:
            logger.debug("bloomberg.fetch_bars.unsupported_ticker", ticker=ticker)
            return []

        periodicity = _INTERVAL_TO_BBG_PERIODICITY.get(interval)
        if periodicity is None:
            logger.debug(
                "bloomberg.fetch_bars.unsupported_interval",
                ticker=ticker,
                interval=interval,
            )
            return []

        try:
            session = self._get_session()
        except RuntimeError as exc:
            logger.error("bloomberg.fetch_bars.session_error", error=str(exc))
            return []

        fetch_ts = datetime.now(tz=UTC)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        refdata_service = session.getService("//blp/refdata")
        request = refdata_service.createRequest("HistoricalDataRequest")
        request.getElement("securities").appendValue(bbg_ticker)
        for field in _BBG_OHLCV_FIELDS:
            request.getElement("fields").appendValue(field)
        request.set("startDate", start_str)
        request.set("endDate", end_str)
        request.set("periodicitySelection", periodicity)

        session.sendRequest(request)

        bars: list[OHLCVBar] = []
        try:
            while True:
                event = session.nextEvent(self._timeout_ms)
                for msg in event:
                    security_data = msg.getElement("securityData")
                    field_data_array = security_data.getElement("fieldData")
                    for i in range(field_data_array.numValues()):
                        point = field_data_array.getValue(i)
                        try:
                            dt_val = point.getElementAsDatetime("date")
                            bar_dt = datetime(
                                dt_val.year, dt_val.month, dt_val.day,
                                tzinfo=UTC,
                            )
                            bar = OHLCVBar(
                                ticker=ticker,
                                interval=interval,
                                open=float(point.getElementAsFloat("PX_OPEN")),
                                high=float(point.getElementAsFloat("PX_HIGH")),
                                low=float(point.getElementAsFloat("PX_LOW")),
                                close=float(point.getElementAsFloat("PX_LAST")),
                                volume=float(point.getElementAsFloat("PX_VOLUME")),
                                event_timestamp=bar_dt,
                                fetch_timestamp=fetch_ts,
                                source=self.SOURCE,
                                adjusted=True,
                            )
                            bars.append(bar)
                        except Exception as exc:
                            logger.warning(
                                "bloomberg.fetch_bars.parse_error", error=str(exc)
                            )

                if event.eventType() == blpapi.Event.RESPONSE:
                    break
        except Exception as exc:
            logger.error(
                "bloomberg.fetch_bars.request_error",
                ticker=ticker,
                error=str(exc),
            )
            return []

        bars.sort(key=lambda b: b.event_timestamp)
        logger.info(
            "bloomberg.fetch_bars.done",
            ticker=ticker,
            interval=interval,
            bars=len(bars),
        )
        return bars

    # ── News ──────────────────────────────────────────────────────────────────

    def fetch_news(
        self,
        tickers: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 100,
    ) -> list[NewsArticle]:
        """
        Fetch news headlines and bodies via Bloomberg NEWS_STORY_RT_REQUEST.

        Bloomberg's news service availability depends on the subscription tier.
        When unavailable (e.g. B-PIPE without a news entitlement) the request
        returns no results and the pipeline falls back to NewsAPI / GDELT.

        Parameters
        ----------
        tickers :
            Platform-canonical tickers to search for.  Crypto tickers are
            skipped.
        start, end :
            UTC datetime range filter.
        max_results :
            Maximum articles to return.

        Returns
        -------
        list[NewsArticle]
            Articles sorted descending by ``event_timestamp`` (newest first).
        """
        try:
            session = self._get_session()
        except RuntimeError as exc:
            logger.error("bloomberg.fetch_news.session_error", error=str(exc))
            return []

        fetch_ts = datetime.now(tz=UTC)
        articles: list[NewsArticle] = []
        seen_ids: set[str] = set()

        bbg_tickers = []
        for t in (tickers or []):
            mapped = _to_bloomberg_ticker(t)
            if mapped:
                bbg_tickers.append((t, mapped))

        if not bbg_tickers:
            return []

        try:
            refdata_service = session.getService("//blp/refdata")
            request = refdata_service.createRequest("ReferenceDataRequest")
            for _orig, bbg in bbg_tickers:
                request.getElement("securities").appendValue(bbg)
            request.getElement("fields").appendValue("NEWS_STORY_RT")

            session.sendRequest(request)

            while True:
                event = session.nextEvent(self._timeout_ms)
                for msg in event:
                    security_data_arr = msg.getElement("securityData")
                    for i in range(security_data_arr.numValues()):
                        sec_data = security_data_arr.getValue(i)
                        bbg_sec = sec_data.getElementAsString("security")
                        # Reverse-map bbg ticker → platform ticker
                        orig_ticker = next(
                            (orig for orig, bbg in bbg_tickers if bbg == bbg_sec),
                            bbg_sec,
                        )
                        if not sec_data.hasElement("fieldData"):
                            continue
                        field_data = sec_data.getElement("fieldData")
                        if not field_data.hasElement("NEWS_STORY_RT"):
                            continue
                        news_arr = field_data.getElement("NEWS_STORY_RT")
                        for j in range(min(news_arr.numValues(), max_results)):
                            story = news_arr.getValue(j)
                            try:
                                headline = story.getElementAsString("HEADLINE")
                                body = (
                                    story.getElementAsString("BODY")
                                    if story.hasElement("BODY")
                                    else None
                                )
                                pub_dt_val = story.getElementAsDatetime("TIME")
                                pub_dt = datetime(
                                    pub_dt_val.year,
                                    pub_dt_val.month,
                                    pub_dt_val.day,
                                    pub_dt_val.hours,
                                    pub_dt_val.minutes,
                                    pub_dt_val.seconds,
                                    tzinfo=UTC,
                                )

                                if start and pub_dt < start:
                                    continue
                                if end and pub_dt > end:
                                    continue

                                article_id = _make_article_id(
                                    self.SOURCE, headline, pub_dt.isoformat()
                                )
                                if article_id in seen_ids:
                                    continue
                                seen_ids.add(article_id)

                                articles.append(
                                    NewsArticle(
                                        article_id=article_id,
                                        title=headline,
                                        body=body,
                                        url=None,
                                        source=self.SOURCE,
                                        author=None,
                                        tickers=[orig_ticker],
                                        event_timestamp=pub_dt,
                                        fetch_timestamp=fetch_ts,
                                        sentiment_score=None,
                                    )
                                )
                            except Exception as exc:
                                logger.warning(
                                    "bloomberg.fetch_news.parse_error",
                                    error=str(exc),
                                )
                if event.eventType() == blpapi.Event.RESPONSE:
                    break
        except Exception as exc:
            logger.error("bloomberg.fetch_news.request_error", error=str(exc))
            return []

        articles.sort(key=lambda a: a.event_timestamp, reverse=True)
        logger.info("bloomberg.fetch_news.done", articles=len(articles))
        return articles
