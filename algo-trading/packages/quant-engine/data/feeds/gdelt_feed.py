"""
data/feeds/gdelt_feed.py — GDELT Global Knowledge Graph (GKG) financial news events.

GDELT (Global Database of Events, Language, and Tone) is a massive, free,
real-time database that monitors broadcast, print, and web news media from
nearly every country in the world.  It is updated every 15 minutes.

Why GDELT for trading?
----------------------
GDELT's Global Knowledge Graph (GKG) file provides:
* Dozens of financial-relevance ``THEMES`` per article (e.g. ``ECON_INFLATION``,
  ``ECON_BANKRUPTCY``, ``COMPANY_MERGER``, ``MARKET_CRASH``)
* A ``TONE`` score:  overall tone, positive score, negative score, polarity,
  activity, self/group reference density
* Named entities: organisations, persons, locations
* Source URL and exact publication timestamp

This is far richer than NewsAPI for systematic macro and event-driven signals.

GDELT GKG API
-------------
GDELT does not have a traditional REST API.  Instead it exposes:

1. **GKG Last Update list** (``http://data.gdeltproject.org/gdeltv2/lastupdate.txt``)
   — a text file pointing to the three most recent GKG master files
   (full, export, mentions), updated every 15 minutes.

2. **GKG CSV files** — tab-separated CSVs compressed as ``.zip``.  Each file
   covers a 15-minute window.  The GKG file is the largest and most useful.

3. **DOC API** (``https://api.gdeltproject.org/api/v2/doc/doc``) — full-text
   search API over the last 3 months of articles.  This is the most practical
   for our use case — we can search by theme, tone, and keyword.

We use the **DOC API** in this implementation because:
* It's accessible via a simple HTTP GET request (no SDK needed)
* It supports filtering by ``SOURCELANG:english`` and financial themes
* It returns JSON with article metadata, tone, and URLs
* It does not require downloading and parsing large GKG CSV files

GDELT DOC API endpoint
----------------------
``https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&maxrecords=250&format=json``

Key parameters:
* ``query``      : Full-text search.  Supports operators like ``theme:ECON_``
* ``mode``       : ``artlist`` — returns article metadata list
* ``maxrecords`` : 1–250 (GDELT maximum)
* ``startdatetime`` / ``enddatetime`` : ``YYYYMMDDHHMMSS`` format (UTC)
* ``format``     : ``json``

Financial themes filter
-----------------------
We filter for articles tagged with any of these GDELT themes:
    ECON_BANKRUPTCY, ECON_INFLATION, ECON_MARKETCRASH, COMPANY_MERGER,
    ECON_STOCKMARKET, TAX_POLICY, INTEREST_RATE, USFED

Tone filtering
--------------
GDELT provides a composite tone score.  We parse it from article metadata
when available and map to our ``sentiment_score`` field.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from data.feeds.base import DataFeed
from data.schemas import NewsArticle

logger = structlog.get_logger(__name__)

# GDELT DOC API base URL
_GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Financial-relevance GDELT V2 themes — any article tagged with these is
# considered potentially relevant to equity/macro strategies.
_FINANCIAL_THEMES = [
    "ECON_BANKRUPTCY",
    "ECON_INFLATION",
    "ECON_MARKETCRASH",
    "ECON_STOCKMARKET",
    "COMPANY_MERGER",
    "COMPANY_ACQUIRE",
    "ECON_TRADE",
    "TAX_POLICY",
    "INTEREST_RATE",
    "USFED",
    "UNEMPLOYMENT",
]

# GDELT tone score range is roughly -100 to +100; we normalize to [-1, +1]
_TONE_NORMALIZER = 100.0


def _make_article_id(source: str, title: str, published_at: str) -> str:
    """Stable ID from key fields (same logic as newsapi_feed for consistency)."""
    raw = f"{source}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _parse_gdelt_datetime(dt_str: str) -> datetime | None:
    """Parse GDELT datetime strings into a UTC datetime.

    GDELT returns dates in two formats depending on the API endpoint:
    - ``YYYYMMDDHHMMSS``       (DOC API artlist, no separators)
    - ``YYYYMMDDTHHMMSSZ``     (some GKG fields, compact ISO with T and Z)
    """
    if not dt_str:
        return None
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


class GdeltFeed(DataFeed):
    """
    News feed backed by the GDELT Project's DOC API v2.

    Queries the GDELT full-text search API for financially relevant articles
    and maps results to ``NewsArticle`` records.

    Parameters
    ----------
    config : dict, optional
        Accepts ``"themes"`` (list of GDELT theme strings to filter by,
        defaults to ``_FINANCIAL_THEMES``) and ``"max_records"`` (int,
        default 250 — GDELT's maximum per request).

    Notes
    -----
    GDELT does not require an API key.  The DOC API is freely accessible.
    Be courteous with request rates — GDELT recommends no more than a few
    requests per minute for bulk data collection.
    """

    SOURCE = "gdelt"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._themes: list[str] = self.config.get("themes", _FINANCIAL_THEMES)
        self._max_records: int = min(self.config.get("max_records", 250), 250)
        self._http_client = httpx.Client(timeout=30.0)

    def fetch_news(
        self,
        tickers: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 250,
    ) -> list[NewsArticle]:
        """
        Query GDELT DOC API for financially relevant news articles.

        Builds a query that combines optional ticker keyword search with
        financial theme filtering.  GDELT's ``theme:`` operator filters for
        articles tagged with specific event categories in its knowledge graph.

        Parameters
        ----------
        tickers :
            Ticker symbols to search for as keywords.  GDELT will find articles
            that mention the ticker symbol *or* the associated company name
            (company name matching is imprecise for shorter symbols).
        start, end :
            UTC datetime bounds.  GDELT covers the last 3 months via the DOC API.
            For older data, bulk GKG files are required.
        max_results :
            Number of articles to return (capped at 250 by GDELT).

        Returns
        -------
        list[NewsArticle]
            Articles sorted descending by ``event_timestamp``.
        """
        fetch_ts = datetime.now(tz=timezone.utc)
        max_records = min(max_results, self._max_records, 250)

        # Build the GDELT query string
        # Theme filter: any article must match at least one financial theme
        theme_clause = " OR ".join(f"theme:{t}" for t in self._themes[:5])
        query_parts = [f"({theme_clause})", "sourcelang:english"]

        if tickers:
            ticker_clause = " OR ".join(tickers)
            query_parts.insert(0, f"({ticker_clause})")

        query = " ".join(query_parts)

        params: dict[str, Any] = {
            "query": query,
            "mode": "artlist",
            "maxrecords": max_records,
            "format": "json",
            "sort": "DateDesc",
        }

        if start:
            params["startdatetime"] = start.strftime("%Y%m%d%H%M%S")
        if end:
            params["enddatetime"] = end.strftime("%Y%m%d%H%M%S")

        logger.info(
            "gdelt.fetch_news",
            tickers=tickers,
            query=query,
            max_records=max_records,
        )

        import random
        import time as _time
        max_retries = 4
        data: dict = {}
        for attempt in range(max_retries):
            try:
                response = self._http_client.get(_GDELT_DOC_API, params=params)
                response.raise_for_status()
                data = response.json()
                break
            except Exception as exc:
                is_last = attempt == max_retries - 1
                if is_last:
                    logger.warning("gdelt.fetch_news.failed", error=str(exc), attempts=max_retries)
                    return []
                backoff = (2 ** attempt) + random.uniform(0, 1)
                logger.debug("gdelt.fetch_news.retry", attempt=attempt + 1, backoff_s=round(backoff, 2), error=str(exc))
                _time.sleep(backoff)

        raw_articles = data.get("articles", [])
        articles: list[NewsArticle] = []
        seen_ids: set[str] = set()

        for raw in raw_articles:
            try:
                title = raw.get("title", "") or ""
                source_name = raw.get("domain", "gdelt")
                url = raw.get("url", "")

                # GDELT returns seendatetime as "YYYYMMDDHHMMSS"
                seen_dt_str = raw.get("seendate", "")
                ts = _parse_gdelt_datetime(seen_dt_str) or fetch_ts

                article_id = _make_article_id(source_name, title, seen_dt_str)
                if article_id in seen_ids:
                    continue
                seen_ids.add(article_id)

                # Extract tone score if present
                # GDELT tone is in the article metadata under "tone" as a float
                tone_raw = raw.get("tone")
                sentiment: float | None = None
                if tone_raw is not None:
                    try:
                        # Normalize from [-100, +100] range to [-1, +1]
                        sentiment = max(-1.0, min(1.0, float(tone_raw) / _TONE_NORMALIZER))
                    except (TypeError, ValueError):
                        sentiment = None

                # Associate only the tickers that appear in the article title
                # using the same alias-aware attribution as newsapi_feed.
                if tickers:
                    from data.feeds.newsapi_feed import _attribute_tickers
                    article_tickers = _attribute_tickers(title, None, list(tickers))
                    # No match → leave empty; caller maps [] → "GENERAL"
                else:
                    article_tickers = []

                article = NewsArticle(
                    article_id=article_id,
                    title=title,
                    body=None,  # DOC API returns titles only; full text requires scraping
                    url=url if url else None,
                    source=self.SOURCE,
                    author=None,
                    tickers=article_tickers,
                    event_timestamp=ts,
                    fetch_timestamp=fetch_ts,
                    sentiment_score=sentiment,
                )
                articles.append(article)
            except Exception as exc:
                logger.warning("gdelt.article_parse_error", error=str(exc))

        articles.sort(key=lambda a: a.event_timestamp, reverse=True)
        logger.info("gdelt.fetch_news.done", articles_returned=len(articles))
        return articles

    def fetch_recent_financial_news(self, hours: int = 4) -> list[NewsArticle]:
        """
        Fetch financially-themed articles published within the last N hours.

        This is what the DataPipeline uses on its regular polling cycle to keep
        the macro news store fresh without specifying individual tickers.

        Parameters
        ----------
        hours :
            Look-back window in hours.  GDELT's 15-minute update cadence means
            a 4-hour window will typically return 16 GKG update cycles of data.

        Returns
        -------
        list[NewsArticle]
        """
        from datetime import timedelta

        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(hours=hours)
        return self.fetch_news(start=start, end=end, max_results=250)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http_client.close()
