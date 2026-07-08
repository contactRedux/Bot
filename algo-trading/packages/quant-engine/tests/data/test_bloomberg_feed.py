"""
tests/data/test_bloomberg_feed.py — Unit tests for BloombergFeed.

These tests cover:
1. Smoke import — BloombergFeed imports cleanly even when blpapi is absent.
2. is_available() — returns False when blpapi is absent.
3. fetch_bars() — happy path with a mocked blpapi session.
4. fetch_bars() — unsupported ticker (crypto) returns empty list.
5. fetch_bars() — unsupported interval (sub-daily) returns empty list.
6. fetch_bars() — session start failure returns empty list (graceful).
7. fetch_news() — happy path with a mocked blpapi session.
8. fetch_news() — no equity tickers returns empty list immediately.
9. Pipeline graceful fallback — DataPipeline._get_bloomberg_feed() returns None
   when blpapi is absent or bloomberg_app_name is not set.
10. Sentiment source quality weighting — Bloomberg articles weighted higher.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ── Helper: build a minimal blpapi stub ──────────────────────────────────────

def _make_blpapi_stub() -> ModuleType:
    """
    Build a minimal blpapi module stub sufficient for BloombergFeed's code paths.

    BloombergFeed only calls a small subset of the blpapi surface:
      * blpapi.SessionOptions()
      * blpapi.Session(options)  → session.start(), session.openService()
      * session.getService()     → service.createRequest()
      * request.getElement()     → element.appendValue(), request.set()
      * session.sendRequest()
      * session.nextEvent()      → event.eventType(), event.__iter__()
      * msg.getElement("securityData") → ...
      * blpapi.Event.RESPONSE
    """
    stub = ModuleType("blpapi")

    # Event type sentinel
    class Event:
        RESPONSE = "RESPONSE"

        def __init__(self, event_type, messages=None):
            self._event_type = event_type
            self._messages = messages or []

        def eventType(self):  # noqa: N802
            return self._event_type

        def __iter__(self):
            return iter(self._messages)

    stub.Event = Event

    # SessionOptions stub
    class SessionOptions:
        def setServerHost(self, host): pass  # noqa: N802
        def setServerPort(self, port): pass  # noqa: N802
        def setAuthenticationOptions(self, opts): pass  # noqa: N802

    stub.SessionOptions = SessionOptions

    # Session stub (default: start succeeds, openService succeeds)
    class Session:
        def __init__(self, options):
            self._started = True
            self._service = None

        def start(self):
            return True

        def openService(self, name):  # noqa: N802
            return True

        def getService(self, name):  # noqa: N802
            svc = MagicMock()
            return svc

        def sendRequest(self, request):  # noqa: N802
            pass

        def nextEvent(self, timeout_ms=0):  # noqa: N802
            # Returns a terminal RESPONSE event with no messages by default
            return Event(Event.RESPONSE, [])

        def stop(self):
            pass

    stub.Session = Session

    return stub


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def blpapi_stub():
    """Inject a blpapi stub into sys.modules for the duration of the test."""
    stub = _make_blpapi_stub()
    with patch.dict(sys.modules, {"blpapi": stub}):
        # Also patch the module-level flag inside bloomberg_feed
        with patch("data.feeds.bloomberg_feed._BLPAPI_AVAILABLE", True), \
             patch("data.feeds.bloomberg_feed.blpapi", stub):
            yield stub


@pytest.fixture()
def feed_config():
    return {
        "host": "localhost",
        "port": 8194,
        "app_name": "test_app",
        "timeout_seconds": 5,
    }


# ── Test 1: Smoke import — module loads cleanly without blpapi ────────────────

def test_bloomberg_feed_imports_without_blpapi():
    """BloombergFeed must be importable even when blpapi is not installed."""
    # The import at module level already happened; the key assertion is that
    # no ImportError was raised.  We also check the class is accessible.
    from data.feeds.bloomberg_feed import BloombergFeed
    assert BloombergFeed is not None


# ── Test 2: is_available() without blpapi ────────────────────────────────────

def test_is_available_false_when_blpapi_absent():
    """is_available() returns False when blpapi is not installed."""
    with patch("data.feeds.bloomberg_feed._BLPAPI_AVAILABLE", False):
        from data.feeds.bloomberg_feed import BloombergFeed
        assert BloombergFeed.is_available() is False


# ── Test 3: is_available() with blpapi stub ──────────────────────────────────

def test_is_available_true_when_blpapi_present(blpapi_stub):
    """is_available() returns True when blpapi is importable."""
    from data.feeds.bloomberg_feed import BloombergFeed
    assert BloombergFeed.is_available() is True


# ── Test 4: fetch_bars — unsupported crypto ticker ───────────────────────────

def test_fetch_bars_crypto_ticker_returns_empty(blpapi_stub, feed_config):
    """Crypto tickers are not covered by B-PIPE and must return []."""
    from data.feeds.bloomberg_feed import BloombergFeed

    feed = BloombergFeed(config=feed_config)
    result = feed.fetch_bars(
        "BTC-USD",
        "1d",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 31, tzinfo=UTC),
    )
    assert result == []


# ── Test 5: fetch_bars — unsupported sub-daily interval ──────────────────────

def test_fetch_bars_intraday_interval_returns_empty(blpapi_stub, feed_config):
    """Intraday intervals (e.g. '1m', '1h') are not supported by BDH."""
    from data.feeds.bloomberg_feed import BloombergFeed

    feed = BloombergFeed(config=feed_config)
    for interval in ("1m", "5m", "15m", "1h"):
        result = feed.fetch_bars(
            "AAPL",
            interval,
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 31, tzinfo=UTC),
        )
        assert result == [], f"Expected empty for interval={interval}"


# ── Test 6: fetch_bars — happy path with mocked data ─────────────────────────

def test_fetch_bars_happy_path(blpapi_stub, feed_config):
    """fetch_bars returns OHLCVBar objects when Bloomberg returns valid data."""
    from data.feeds.bloomberg_feed import BloombergFeed

    # Build a fake field data point
    fake_date = MagicMock()
    fake_date.year = 2024
    fake_date.month = 1
    fake_date.day = 15

    fake_point = MagicMock()
    fake_point.getElementAsDatetime.return_value = fake_date
    fake_point.getElementAsFloat.side_effect = lambda field: {
        "PX_OPEN": 180.0,
        "PX_HIGH": 185.0,
        "PX_LOW": 179.0,
        "PX_LAST": 183.0,
        "PX_VOLUME": 1_000_000.0,
    }[field]

    fake_field_data_array = MagicMock()
    fake_field_data_array.numValues.return_value = 1
    fake_field_data_array.getValue.return_value = fake_point

    fake_security_data = MagicMock()
    fake_security_data.getElement.return_value = fake_field_data_array

    fake_msg = MagicMock()
    fake_msg.getElement.return_value = fake_security_data

    # The session returns one RESPONSE event with one message
    fake_event = blpapi_stub.Event(blpapi_stub.Event.RESPONSE, [fake_msg])

    feed = BloombergFeed(config=feed_config)

    # Override _get_session to return a mock that yields our fake event
    mock_session = MagicMock()
    mock_session.getService.return_value.createRequest.return_value = MagicMock()
    mock_session.sendRequest.return_value = None
    mock_session.nextEvent.return_value = fake_event
    feed._session = mock_session

    bars = feed.fetch_bars(
        "AAPL",
        "1d",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 31, tzinfo=UTC),
    )

    assert len(bars) == 1
    bar = bars[0]
    assert bar.ticker == "AAPL"
    assert bar.interval == "1d"
    assert bar.open == 180.0
    assert bar.high == 185.0
    assert bar.low == 179.0
    assert bar.close == 183.0
    assert bar.volume == 1_000_000.0
    assert bar.source == "bloomberg"
    assert bar.adjusted is True
    assert bar.event_timestamp == datetime(2024, 1, 15, tzinfo=UTC)


# ── Test 7: fetch_bars — session start failure returns empty list ─────────────

def test_fetch_bars_session_failure_returns_empty(feed_config):
    """When _get_session raises RuntimeError, fetch_bars returns []."""
    with patch("data.feeds.bloomberg_feed._BLPAPI_AVAILABLE", True):
        stub = _make_blpapi_stub()

        class FailingSession:
            def __init__(self, options):
                pass
            def start(self):
                return False  # session start fails

        stub.Session = FailingSession
        with patch("data.feeds.bloomberg_feed.blpapi", stub):
            from data.feeds.bloomberg_feed import BloombergFeed
            feed = BloombergFeed(config=feed_config)
            result = feed.fetch_bars(
                "AAPL",
                "1d",
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 31, tzinfo=UTC),
            )
            assert result == []


# ── Test 8: fetch_news — no equity tickers ───────────────────────────────────

def test_fetch_news_no_equity_tickers(blpapi_stub, feed_config):
    """fetch_news with only crypto tickers (unmappable) returns []."""
    from data.feeds.bloomberg_feed import BloombergFeed

    feed = BloombergFeed(config=feed_config)
    result = feed.fetch_news(
        tickers=["BTC-USD", "ETH-USD"],
        max_results=10,
    )
    assert result == []


# ── Test 9: fetch_news — happy path ──────────────────────────────────────────

def test_fetch_news_happy_path(blpapi_stub, feed_config):
    """fetch_news returns NewsArticle objects for equity tickers."""
    from data.feeds.bloomberg_feed import BloombergFeed

    fake_time = MagicMock()
    fake_time.year = 2024
    fake_time.month = 1
    fake_time.day = 15
    fake_time.hours = 10
    fake_time.minutes = 30
    fake_time.seconds = 0

    fake_story = MagicMock()
    fake_story.getElementAsString.side_effect = lambda f: {
        "HEADLINE": "AAPL beats earnings",
        "BODY": "Apple reported strong Q1 results.",
    }[f]
    fake_story.hasElement.side_effect = lambda f: f == "BODY"
    fake_story.getElementAsDatetime.return_value = fake_time

    fake_news_arr = MagicMock()
    fake_news_arr.numValues.return_value = 1
    fake_news_arr.getValue.return_value = fake_story

    fake_field_data = MagicMock()
    fake_field_data.hasElement.return_value = True
    fake_field_data.getElement.return_value = fake_news_arr

    fake_sec_data = MagicMock()
    fake_sec_data.getElementAsString.return_value = "AAPL US Equity"
    fake_sec_data.hasElement.return_value = True
    fake_sec_data.getElement.return_value = fake_field_data

    fake_security_data_arr = MagicMock()
    fake_security_data_arr.numValues.return_value = 1
    fake_security_data_arr.getValue.return_value = fake_sec_data

    fake_msg = MagicMock()
    fake_msg.getElement.return_value = fake_security_data_arr

    fake_event = blpapi_stub.Event(blpapi_stub.Event.RESPONSE, [fake_msg])

    feed = BloombergFeed(config=feed_config)

    mock_session = MagicMock()
    mock_session.getService.return_value.createRequest.return_value = MagicMock()
    mock_session.sendRequest.return_value = None
    mock_session.nextEvent.return_value = fake_event
    feed._session = mock_session

    articles = feed.fetch_news(
        tickers=["AAPL"],
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 31, tzinfo=UTC),
        max_results=10,
    )

    assert len(articles) == 1
    article = articles[0]
    assert article.title == "AAPL beats earnings"
    assert article.body == "Apple reported strong Q1 results."
    assert article.source == "bloomberg"
    assert "AAPL" in article.tickers
    assert article.event_timestamp == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)


# ── Test 10: Pipeline _get_bloomberg_feed — no blpapi ────────────────────────

def test_pipeline_get_bloomberg_feed_no_blpapi():
    """DataPipeline._get_bloomberg_feed() returns None when blpapi is absent."""
    with patch("data.feeds.bloomberg_feed._BLPAPI_AVAILABLE", False):
        from data.pipeline import DataPipeline
        from data.store import DataStore

        store = DataStore("sqlite:///:memory:")
        pipeline = DataPipeline(store=store)
        feed = pipeline._get_bloomberg_feed()
        assert feed is None


# ── Test 11: Pipeline _get_bloomberg_feed — no app name ──────────────────────

def test_pipeline_get_bloomberg_feed_no_app_name(blpapi_stub):
    """DataPipeline._get_bloomberg_feed() returns None when app_name is unset."""
    with patch("config.settings.settings") as mock_settings:
        mock_settings.bloomberg_app_name = None
        mock_settings.bloomberg_host = "localhost"
        mock_settings.bloomberg_port = 8194
        mock_settings.bloomberg_timeout_seconds = 30

        from data.pipeline import DataPipeline
        from data.store import DataStore

        store = DataStore("sqlite:///:memory:")
        pipeline = DataPipeline(store=store)
        feed = pipeline._get_bloomberg_feed()
        assert feed is None


# ── Test 12: _to_bloomberg_ticker mapping ────────────────────────────────────

def test_to_bloomberg_ticker():
    """_to_bloomberg_ticker maps equity tickers and rejects crypto/indices."""
    from data.feeds.bloomberg_feed import _to_bloomberg_ticker

    assert _to_bloomberg_ticker("AAPL") == "AAPL US Equity"
    assert _to_bloomberg_ticker("MSFT") == "MSFT US Equity"
    assert _to_bloomberg_ticker("BTC-USD") is None
    assert _to_bloomberg_ticker("ETH-USD") is None
    assert _to_bloomberg_ticker("^GSPC") is None


# ── Test 13: BloombergFeed in __all__ export ──────────────────────────────────

def test_bloomberg_feed_exported_from_feeds_package():
    """BloombergFeed is accessible via the data.feeds package."""
    from data.feeds import BloombergFeed
    assert BloombergFeed.SOURCE == "bloomberg"


# ── Test 14: Sentiment source quality weighting ──────────────────────────────

def test_sentiment_bloomberg_weighted_higher():
    """
    Bloomberg articles receive a 2× quality multiplier in aggregate_sentiment.

    When one Bloomberg article and one NewsAPI article with identical scores
    are both present, the Bloomberg article dominates the decay-weighted score.
    """
    from data.schemas import NewsArticle
    from features.sentiment import aggregate_sentiment

    as_of = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    pub_ts = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)  # 1 hour ago
    fetch_ts = as_of

    bbg_article = NewsArticle(
        article_id="bbg-001",
        title="Bloomberg: strong earnings",
        body=None,
        url=None,
        source="bloomberg",
        author=None,
        tickers=["AAPL"],
        event_timestamp=pub_ts,
        fetch_timestamp=fetch_ts,
        sentiment_score=1.0,  # maximally positive
    )
    newsapi_article = NewsArticle(
        article_id="news-001",
        title="NewsAPI: mixed outlook",
        body=None,
        url=None,
        source="newsapi",
        author=None,
        tickers=["AAPL"],
        event_timestamp=pub_ts,
        fetch_timestamp=fetch_ts,
        sentiment_score=-1.0,  # maximally negative
    )

    result = aggregate_sentiment(
        articles=[bbg_article, newsapi_article],
        ticker="AAPL",
        as_of=as_of,
        window_hours=24,
        decay_half_life_hours=6.0,
    )

    # Bloomberg weight = 2.0; NewsAPI weight = 1.0 (same age, same decay factor)
    # normalised weights: bloomberg = 2/3, newsapi = 1/3
    # decay_weighted_score = (2/3)*1.0 + (1/3)*(-1.0) = 1/3 ≈ 0.333
    expected = (2.0 / 3.0) * 1.0 + (1.0 / 3.0) * (-1.0)
    assert abs(result["sentiment_decayed"] - expected) < 1e-6


def test_sentiment_no_bloomberg_uses_uniform_weights():
    """
    Without Bloomberg articles, only the free-tier quality weights apply.

    A single NewsAPI article with score=0.5 should produce sentiment_decayed≈0.5
    (only one article, so weight=1.0 after normalisation).
    """
    from data.schemas import NewsArticle
    from features.sentiment import aggregate_sentiment

    as_of = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    pub_ts = datetime(2024, 1, 15, 11, 30, 0, tzinfo=UTC)
    fetch_ts = as_of

    article = NewsArticle(
        article_id="news-002",
        title="Company reports steady growth",
        body=None,
        url=None,
        source="newsapi",
        author=None,
        tickers=["MSFT"],
        event_timestamp=pub_ts,
        fetch_timestamp=fetch_ts,
        sentiment_score=0.5,
    )

    result = aggregate_sentiment(
        articles=[article],
        ticker="MSFT",
        as_of=as_of,
        window_hours=24,
        decay_half_life_hours=6.0,
    )

    assert abs(result["sentiment_decayed"] - 0.5) < 1e-6
    assert result["article_count"] == 1.0
