"""
api/routes/news.py — News articles and sentiment feed endpoints.

Endpoints
---------
GET  /api/news
    Return the most recent news articles stored in the DataStore.
    Optional query parameters:
        ticker    — filter to a specific ticker
        limit     — max rows to return (default 100)
        scored_only — only return articles with a sentiment score
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from api.deps import AppState, get_app_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("")
async def get_news(
    ticker: str | None = Query(default=None, description="Filter by ticker, e.g. AAPL"),
    limit: int = Query(default=100, ge=1, le=500, description="Max articles to return"),
    scored_only: bool = Query(default=False, description="Only return sentiment-scored articles"),
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Return recent news articles from the DataStore, newest first.

    In paper/live mode the DataPipeline polls NewsAPI and GDELT every 15 minutes
    and writes articles here.  In dev mode articles are only present if a
    backtest or manual fetch has run first.
    """
    store = state.data_store
    if store is None:
        return {"articles": [], "count": 0}

    tickers = [ticker] if ticker else None

    # Look back 7 days by default — wide enough to always have something
    end = datetime.now(UTC)
    start = end - timedelta(days=7)

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

    result = []
    for a in articles:
        score = a.sentiment_score or 0.0
        if score > 0.1:
            label = "positive"
        elif score < -0.1:
            label = "negative"
        else:
            label = "neutral"

        # Use the first ticker stored on the article (or "GENERAL")
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

    return {"articles": result, "count": len(result)}
