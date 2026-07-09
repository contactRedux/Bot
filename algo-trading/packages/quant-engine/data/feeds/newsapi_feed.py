"""
data/feeds/newsapi_feed.py — News headlines and ticker search via NewsAPI.org.

NewsAPI.org provides an HTTP API for fetching breaking news from thousands of
sources.  In this system it is the primary news ingestion source for the
Sentiment strategy (Sub-Task 5).

Free tier limits
----------------
* 100 requests/day
* Headlines only (no full article body) on the free plan
* Up to 100 articles per request
* Historical search limited to the past 30 days (Developer plan extends to 3 months)

Usage patterns
--------------
Two NewsAPI endpoints are used:

1. **Top Headlines** (``/v2/top-headlines``) — real-time breaking news,
   optionally filtered by category, country, or source.  Used for general
   market news polling.

2. **Everything** (``/v2/everything``) — full-text search by keyword.
   We search for each ticker symbol and its company name to capture relevant
   articles.  Example: ``q="AAPL OR Apple Inc"``

Both return the same article structure:
    ``{title, description, content, url, author, publishedAt, source: {name}}``

We map ``publishedAt`` to ``event_timestamp`` (UTC) — this is the authoritative
timestamp used by the backtesting engine.

Article de-duplication
----------------------
The ``article_id`` field in ``NewsArticle`` is a SHA-256 hash of
``(source + title + event_timestamp)``.  This allows the DataStore to use
``INSERT OR IGNORE`` semantics to avoid duplicating the same article when
polling repeatedly.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import structlog

from data.feeds.base import DataFeed
from data.schemas import NewsArticle

logger = structlog.get_logger(__name__)


def _make_article_id(source: str, title: str, published_at: str) -> str:
    """Create a stable article identifier from its key fields."""
    raw = f"{source}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class NewsApiFeed(DataFeed):
    """
    News feed backed by NewsAPI.org (``newsapi-python`` library).

    Parameters
    ----------
    config : dict, optional
        Required keys:
        - ``"api_key"`` : NewsAPI.org API key (read from settings by default)

    Example
    -------
    ::

        from data.feeds.newsapi_feed import NewsApiFeed
        from datetime import datetime, timezone

        feed = NewsApiFeed(config={"api_key": "your-key"})

        # Search for Apple news
        articles = feed.fetch_news(tickers=["AAPL"], max_results=20)
        for a in articles:
            print(a.title, a.sentiment_score)
    """

    SOURCE = "newsapi"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._api_key: str | None = self.config.get("api_key")

    def _get_client(self):
        """Lazily create the NewsAPI client."""
        if not self._api_key:
            raise ValueError(
                "NewsAPI key is required.  Set NEWSAPI_KEY in .env or "
                "pass config={'api_key': 'your-key'}."
            )
        try:
            from newsapi import NewsApiClient
        except ImportError:
            raise ImportError(
                "newsapi-python is not installed.  "
                "Run: pip install 'quant-engine[data]'"
            )
        return NewsApiClient(api_key=self._api_key)

    def fetch_news(
        self,
        tickers: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 100,
    ) -> list[NewsArticle]:
        """
        Fetch news articles from NewsAPI.

        If ``tickers`` is provided, searches for each ticker separately and
        merges the results (de-duplication via ``article_id``).  If no tickers
        are given, fetches general business/financial top headlines.

        Parameters
        ----------
        tickers :
            List of ticker symbols.  We search for each as a keyword query.
            Example: ``["AAPL", "MSFT"]``
        start, end :
            UTC datetimes for the search window.  NewsAPI free tier limits
            historical search to 30 days; Developer plan extends to 3 months.
        max_results :
            Maximum number of articles to return (capped at 100 by NewsAPI).

        Returns
        -------
        list[NewsArticle]
            Articles sorted descending by ``event_timestamp`` (newest first).
            ``sentiment_score`` is ``None`` — it is populated later by the
            sentiment feature module.
        """
        client = self._get_client()
        fetch_ts = datetime.now(tz=timezone.utc)

        # Format timestamps for NewsAPI (ISO 8601 without timezone suffix)
        from_ts = start.strftime("%Y-%m-%dT%H:%M:%S") if start else None
        to_ts = end.strftime("%Y-%m-%dT%H:%M:%S") if end else None

        seen_ids: set[str] = set()
        articles: list[NewsArticle] = []

        def _parse_articles(raw_articles: list[dict], article_tickers: list[str]) -> None:
            for raw in raw_articles:
                try:
                    published_at = raw.get("publishedAt", "")
                    source_name = raw.get("source", {}).get("name", "unknown")
                    title = raw.get("title", "") or ""
                    article_id = _make_article_id(source_name, title, published_at)

                    if article_id in seen_ids:
                        continue
                    seen_ids.add(article_id)

                    # Parse ISO 8601 timestamp → UTC datetime
                    # NewsAPI returns "2024-01-15T10:30:00Z"
                    if published_at:
                        ts = datetime.fromisoformat(
                            published_at.replace("Z", "+00:00")
                        )
                    else:
                        ts = fetch_ts

                    article = NewsArticle(
                        article_id=article_id,
                        title=title,
                        body=raw.get("content"),  # truncated on free tier
                        url=raw.get("url"),
                        source=self.SOURCE,
                        author=raw.get("author"),
                        tickers=article_tickers,
                        event_timestamp=ts,
                        fetch_timestamp=fetch_ts,
                        sentiment_score=None,
                    )
                    articles.append(article)
                except Exception as exc:
                    logger.warning(
                        "newsapi.article_parse_error", error=str(exc)
                    )

        if tickers:
            # Search for each ticker separately so each article is tagged only
            # with the ticker it was actually fetched for, not all queried tickers.
            for ticker in tickers:
                logger.info("newsapi.fetch_news", ticker=ticker, max_results=max_results)
                try:
                    response = client.get_everything(
                        q=ticker,
                        from_param=from_ts,
                        to=to_ts,
                        language="en",
                        sort_by="publishedAt",
                        page_size=min(max_results, 100),
                    )
                    _parse_articles(response.get("articles", []), [ticker])
                except Exception as exc:
                    logger.error("newsapi.fetch_news.error", ticker=ticker, error=str(exc))
        else:
            # Fetch general business top headlines
            logger.info("newsapi.fetch_top_headlines")
            try:
                response = client.get_top_headlines(
                    category="business",
                    language="en",
                    page_size=min(max_results, 100),
                )
                _parse_articles(response.get("articles", []), [])
            except Exception as exc:
                logger.error("newsapi.fetch_top_headlines.error", error=str(exc))

        # Sort newest first
        articles.sort(key=lambda a: a.event_timestamp, reverse=True)
        logger.info("newsapi.fetch_news.done", articles_returned=len(articles))
        return articles

    def fetch_top_headlines(self, max_results: int = 100) -> list[NewsArticle]:
        """
        Convenience method to fetch current top business headlines.

        This is what the DataPipeline calls on its regular polling schedule
        (e.g., every 15 minutes) to keep the news store fresh.

        Returns
        -------
        list[NewsArticle]
            Top headlines, sorted newest first.
        """
        return self.fetch_news(tickers=None, max_results=max_results)
