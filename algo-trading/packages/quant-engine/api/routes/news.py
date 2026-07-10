"""
api/routes/news.py — News articles and sentiment feed endpoints.

Endpoints
---------
GET  /api/news
    Return the most recent news articles stored in the DataStore.
    Query parameters:
        ticker      — filter to a specific ticker
        limit       — max rows to return (default 100)
        scored_only — only return articles with a sentiment score

POST /api/news/fetch
    Trigger an on-demand live news pull for a specific ticker.
    Queries NewsAPI (if configured) and GDELT directly and writes any
    new articles to the DataStore.  Returns the articles found.
    Body: { "ticker": "AAPL", "limit": 20 }
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.deps import AppState, get_app_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/news", tags=["news"])


def _format_articles(articles) -> list[dict]:
    result = []
    for a in articles:
        score = a.sentiment_score or 0.0
        if score > 0.1:
            label = "positive"
        elif score < -0.1:
            label = "negative"
        else:
            label = "neutral"
        article_ticker = (a.tickers[0] if a.tickers else None) or "GENERAL"
        result.append({
            "id": a.article_id,
            "ticker": article_ticker,
            "headline": a.title,
            "source": a.source,
            "sentiment_score": round(score, 4),
            "sentiment_label": label,
            "published_at": a.event_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "url": a.url,
        })
    return result


@router.get("")
async def get_news(
    ticker: str | None = Query(default=None, description="Filter by ticker, e.g. AAPL"),
    limit: int = Query(default=100, ge=1, le=500, description="Max articles to return"),
    scored_only: bool = Query(default=False, description="Only return sentiment-scored articles"),
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return recent news articles from the DataStore, newest first."""
    store = state.data_store
    if store is None:
        return {"articles": [], "count": 0}

    tickers = [ticker] if ticker else None

    # Look back 30 days — wide enough to always have something
    end = datetime.now(UTC)
    start = end - timedelta(days=30)

    try:
        articles = store.read_news(
            tickers=tickers,
            start=start,
            end=end,
            max_results=limit,
            scored_only=scored_only,
        )
    except Exception:
        logger.exception("news.read_failed")
        return {"articles": [], "count": 0}

    result = _format_articles(articles)
    return {"articles": result, "count": len(result)}


class FetchNewsRequest(BaseModel):
    ticker: str
    limit: int = 20


@router.post("/fetch")
async def fetch_news_for_ticker(
    body: FetchNewsRequest,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    On-demand live news pull for a specific ticker.

    Queries NewsAPI (if configured) and GDELT directly, writes new articles
    to the DataStore, and returns what was found.  Useful for pulling articles
    for tickers not in the pipeline's watched universe.
    """
    import asyncio

    ticker = body.ticker.upper().strip()
    if not ticker:
        return {"articles": [], "count": 0, "inserted": 0}

    store = state.data_store
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    all_articles: list = []

    loop = asyncio.get_event_loop()

    # NewsAPI pull
    try:
        from config.settings import settings
        if settings.newsapi_key:
            from data.feeds.newsapi_feed import NewsApiFeed
            feed = NewsApiFeed(config={"api_key": settings.newsapi_key})
            articles = await loop.run_in_executor(
                None,
                lambda: feed.fetch_news(tickers=[ticker], start=start, end=end, max_results=body.limit),
            )
            all_articles.extend(articles)
    except Exception as exc:
        logger.warning("news.fetch.newsapi_error ticker=%s error=%s", ticker, exc)

    # GDELT pull
    try:
        from data.feeds.gdelt_feed import GdeltFeed
        gdelt = GdeltFeed()
        articles = await loop.run_in_executor(
            None,
            lambda: gdelt.fetch_news(tickers=[ticker], start=start, end=end, max_results=min(body.limit * 5, 100)),
        )
        all_articles.extend(articles)
    except Exception as exc:
        logger.warning("news.fetch.gdelt_error ticker=%s error=%s", ticker, exc)

    inserted = 0
    if store is not None and all_articles:
        try:
            inserted = store.write_news(all_articles)
        except Exception as exc:
            logger.warning("news.fetch.write_error error=%s", exc)

    result = _format_articles(all_articles)
    # de-dup by id in case both sources returned the same article
    seen: set[str] = set()
    deduped = []
    for r in result:
        if r["id"] not in seen:
            seen.add(r["id"])
            deduped.append(r)
    deduped.sort(key=lambda x: x["published_at"], reverse=True)

    return {"articles": deduped[: body.limit], "count": len(deduped[: body.limit]), "inserted": inserted}
