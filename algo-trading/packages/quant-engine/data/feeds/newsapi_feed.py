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
   All tickers are batched into a single OR query to stay within the
   100 requests/day free-tier limit.  Per-article ticker attribution is
   done via title/description substring matching after the fetch.

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

# NewsAPI ``/v2/everything`` OR-query is capped at 500 chars; we stay safe at 490.
_MAX_QUERY_CHARS = 490


def _make_article_id(source: str, title: str, published_at: str) -> str:
    """Create a stable article identifier from its key fields."""
    raw = f"{source}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _attribute_tickers(title: str, description: str | None, tickers: list[str]) -> list[str]:
    """
    Return the subset of *tickers* that appear in the article title or description.

    Case-insensitive substring match.  If none match, fall back to the full
    list so the article remains visible (over-attribution beats invisibility).
    """
    haystack = (title + " " + (description or "")).lower()
    matched = [t for t in tickers if t.lower() in haystack]
    return matched if matched else list(tickers)


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
        Fetch news articles from NewsAPI using a **single batched request**.

        All tickers are combined into one ``OR`` query (e.g. ``AAPL OR MSFT OR
        NVDA``) so the entire poll cycle costs exactly **one API request**
        regardless of how many tickers are watched.  Per-article ticker
        attribution is resolved via title/description substring matching after
        the fetch.

        If ``tickers`` is not provided, fetches general business top headlines.

        Parameters
        ----------
        tickers :
            List of ticker symbols to search for.
        start, end :
            UTC datetimes for the search window.
        max_results :
            Maximum number of articles to return (capped at 100 by NewsAPI).

        Returns
        -------
        list[NewsArticle]
            Articles sorted descending by ``event_timestamp`` (newest first).
            ``sentiment_score`` is ``None`` — populated later by the sentiment module.
        """
        client = self._get_client()
        fetch_ts = datetime.now(tz=timezone.utc)

        # Format timestamps for NewsAPI (ISO 8601 without timezone suffix)
        from_ts = start.strftime("%Y-%m-%dT%H:%M:%S") if start else None
        to_ts = end.strftime("%Y-%m-%dT%H:%M:%S") if end else None

        seen_ids: set[str] = set()
        articles: list[NewsArticle] = []

        def _parse_raw(raw_articles: list[dict], query_tickers: list[str]) -> None:
            """Convert raw NewsAPI dicts → NewsArticle, attributing tickers by title match."""
            for raw in raw_articles:
                try:
                    published_at = raw.get("publishedAt", "")
                    source_name = raw.get("source", {}).get("name", "unknown")
                    title = raw.get("title", "") or ""
                    description = raw.get("description") or ""
                    article_id = _make_article_id(source_name, title, published_at)

                    if article_id in seen_ids:
                        continue
                    seen_ids.add(article_id)

                    # Parse ISO 8601 timestamp → UTC datetime
                    if published_at:
                        ts = datetime.fromisoformat(
                            published_at.replace("Z", "+00:00")
                        )
                    else:
                        ts = fetch_ts

                    # Attribute only the tickers that actually appear in the article
                    attributed = (
                        _attribute_tickers(title, description, query_tickers)
                        if query_tickers
                        else []
                    )

                    article = NewsArticle(
                        article_id=article_id,
                        title=title,
                        body=raw.get("content"),  # truncated on free tier
                        url=raw.get("url"),
                        source=self.SOURCE,
                        author=raw.get("author"),
                        tickers=attributed,
                        event_timestamp=ts,
                        fetch_timestamp=fetch_ts,
                        sentiment_score=None,
                    )
                    articles.append(article)
                except Exception as exc:
                    logger.warning("newsapi.article_parse_error", error=str(exc))

        if tickers:
            # Build a single OR query — one request for all tickers.
            # Truncate the query if it would exceed NewsAPI's 500-char limit.
            query_parts: list[str] = []
            for t in tickers:
                candidate = " OR ".join(query_parts + [t])
                if len(candidate) > _MAX_QUERY_CHARS:
                    break
                query_parts.append(t)

            query = " OR ".join(query_parts)
            logger.info("newsapi.fetch_news", query=query, max_results=max_results)
            try:
                response = client.get_everything(
                    q=query,
                    from_param=from_ts,
                    to=to_ts,
                    language="en",
                    sort_by="publishedAt",
                    page_size=min(max_results, 100),
                )
                _parse_raw(response.get("articles", []), tickers)
            except Exception as exc:
                logger.error("newsapi.fetch_news.error", query=query, error=str(exc))
        else:
            # Fetch general business top headlines
            logger.info("newsapi.fetch_top_headlines")
            try:
                response = client.get_top_headlines(
                    category="business",
                    language="en",
                    page_size=min(max_results, 100),
                )
                _parse_raw(response.get("articles", []), [])
            except Exception as exc:
                logger.error("newsapi.fetch_top_headlines.error", error=str(exc))

        # Sort newest first
        articles.sort(key=lambda a: a.event_timestamp, reverse=True)
        logger.info("newsapi.fetch_news.done", articles_returned=len(articles))
        return articles

    def fetch_top_headlines(self, max_results: int = 100) -> list[NewsArticle]:
        """
        Convenience method to fetch current top business headlines.

        Returns
        -------
        list[NewsArticle]
            Top headlines, sorted newest first.
        """
        return self.fetch_news(tickers=None, max_results=max_results)
