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

# ---------------------------------------------------------------------------
# Lightweight keyword-based sentiment fallback
# Used when FinBERT has not yet scored an article (sentiment_score is None).
# ---------------------------------------------------------------------------
_POSITIVE_WORDS = frozenset([
    # Earnings / financials
    "beat", "beats", "beating", "topped", "topped", "surpassed", "record",
    "record-high", "all-time", "record-breaking", "blowout", "blowout",
    "profit", "profits", "profitable", "profitability",
    "revenue", "revenues", "earnings", "eps", "dividend", "dividends",
    "buyback", "buybacks", "repurchase", "repurchases",
    "guidance", "raised", "raise", "raised-guidance",
    # Price / market movement
    "surge", "surges", "surged", "surging",
    "rally", "rallies", "rallied", "rallying",
    "rise", "rises", "rose", "rising",
    "gain", "gains", "gained", "gaining",
    "jump", "jumps", "jumped", "jumping",
    "soar", "soars", "soared", "soaring",
    "skyrocket", "skyrockets", "skyrocketed",
    "climb", "climbs", "climbed", "climbing",
    "advance", "advances", "advanced", "advancing",
    "higher", "upside", "upbeat",
    # Analyst sentiment
    "upgrade", "upgrades", "upgraded", "upgrading",
    "outperform", "outperforms", "outperformed",
    "overweight", "buy", "strong-buy", "strong buy",
    "bullish", "bull", "target", "raised-target",
    "positive", "optimistic", "optimism", "confident", "confidence",
    # Business / strategic
    "expand", "expands", "expanded", "expansion",
    "growth", "growing", "grew",
    "deal", "deals", "agreement", "agreements", "partner", "partnership",
    "contract", "contracts", "win", "wins", "won", "award", "awards",
    "launch", "launches", "launched", "breakthrough", "breakthroughs",
    "approved", "approval", "authorize", "authorized",
    "innovation", "innovative", "patent", "patents",
    "acquisition", "acquires", "acquired", "merger",
    "ipo", "debut", "listing",
    "revenue-beat", "profit-beat", "guidance-raise",
    "success", "successful", "successfull",
    "boost", "boosts", "boosted", "boosting",
    "exceed", "exceeds", "exceeded", "exceeding",
    "accelerate", "accelerates", "accelerated",
    "opportunity", "opportunities",
    "uptrend", "momentum",
    "recovery", "recovers", "recovered", "rebound", "rebounds", "rebounded",
    "invest", "invests", "invested", "investing", "investment",
    "bullrun", "upswing",
])

_NEGATIVE_WORDS = frozenset([
    # Earnings / financials
    "miss", "misses", "missed", "missing", "disappoints", "disappointed",
    "below", "shortfall", "shortfalls",
    "loss", "losses", "losing", "lost",
    "deficit", "deficits", "write-down", "write-off", "impairment",
    "cut", "cuts", "cutting", "slashed", "slashes", "slashing",
    "guidance-cut", "reduced-guidance",
    # Price / market movement
    "fall", "falls", "fell", "falling",
    "drop", "drops", "dropped", "dropping",
    "decline", "declines", "declined", "declining",
    "plunge", "plunges", "plunged", "plunging",
    "tumble", "tumbles", "tumbled", "tumbling",
    "crash", "crashes", "crashed", "crashing",
    "slump", "slumps", "slumped", "slumping",
    "slide", "slides", "slid", "sliding",
    "tank", "tanks", "tanked", "tanking",
    "collapse", "collapses", "collapsed",
    "selloff", "sell-off",
    "lower", "downside", "downturn", "downtrend",
    # Analyst sentiment
    "downgrade", "downgrades", "downgraded", "downgrading",
    "underperform", "underperforms", "underperformed",
    "underweight", "sell", "strong-sell", "strong sell",
    "bearish", "bear", "lowered-target", "target-cut",
    "negative", "pessimistic", "pessimism",
    "warning", "warn", "warns", "warned", "caution",
    "concern", "concerns", "worried", "worry",
    # Legal / regulatory
    "lawsuit", "lawsuits", "litigation", "litigating",
    "investigation", "investigated", "probe", "probing",
    "fraud", "fraudulent", "scandal",
    "fine", "fined", "penalty", "penalized",
    "recall", "recalls", "recalled",
    "ban", "banned", "blocked", "rejected",
    "violation", "violates",
    # Business stress
    "layoff", "layoffs", "layoff", "fired", "fire",
    "job-cuts", "workforce-reduction", "restructuring",
    "bankrupt", "bankruptcy", "insolvent", "insolvency", "default", "defaulted",
    "liquidation", "liquidate",
    "downsize", "downsizing",
    "recall", "safety",
    "delay", "delays", "delayed",
    "cancel", "cancels", "cancelled", "cancellation",
    "risk", "risks", "risky",
    "uncertain", "uncertainty",
    "weak", "weakening", "weakness",
    "sluggish", "stagnant", "stagnation",
    "headwind", "headwinds",
    "shortfall", "gap", "slower", "slowing",
    "suspended", "suspension",
    "exit", "exits", "exited",
    "write-down",
    "selloff",
])


def _keyword_score(text: str) -> float:
    """
    Return a rough sentiment score in [-1, +1] based on financial keyword counts.

    Used as a fallback when FinBERT has not scored the article.  Not as accurate
    as FinBERT but far better than always returning 0.
    """
    if not text:
        return 0.0
    # Strip punctuation from each word before matching
    words = [w.strip(".,!?;:'\"()[]") for w in text.lower().split()]
    pos = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg = sum(1 for w in words if w in _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 4)


def _resolve_score(article) -> float:
    """
    Return the best available sentiment score for an article.

    Priority:
      1. FinBERT score already stored on the article (most accurate).
      2. Keyword heuristic applied to title + body (fast fallback).
    """
    stored = getattr(article, "sentiment_score", None)
    if stored is not None:
        return float(stored)
    # Combine title and any available body text for a richer signal
    title = getattr(article, "title", "") or ""
    body = getattr(article, "body", "") or ""
    text = f"{title} {body}".strip()
    return _keyword_score(text)


def _format_articles(articles) -> list[dict]:
    result = []
    for a in articles:
        score = _resolve_score(a)
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

    Queries yfinance (free, no key), NewsAPI (if configured), and GDELT,
    writes new articles to the DataStore, and returns what was found.
    Useful for pulling articles for tickers not in the pipeline's watched universe.
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

    # ── yfinance news pull (free, no API key required) ────────────────────────
    # yfinance returns recent news items with titles and URLs for any ticker.
    try:
        def _yfin_news() -> list:
            import hashlib
            import yfinance as yf  # type: ignore[import]
            t = yf.Ticker(ticker)
            raw_items = t.news or []
            result = []
            for item in raw_items[:body.limit]:
                title = item.get("content", {}).get("title") or item.get("title") or ""
                url = (item.get("content", {}).get("canonicalUrl", {}) or {}).get("url") or item.get("url") or ""
                # yfinance changed its schema in newer versions — handle both
                pub_raw = (item.get("content", {}).get("pubDate") or
                           item.get("providerPublishTime") or
                           item.get("publishedAt") or "")
                try:
                    if isinstance(pub_raw, (int, float)):
                        pub_dt = datetime.fromtimestamp(float(pub_raw), tz=UTC)
                    else:
                        pub_dt = datetime.fromisoformat(str(pub_raw).replace("Z", "+00:00"))
                except Exception:
                    pub_dt = datetime.now(UTC)
                if not title:
                    continue
                art_id = hashlib.sha256(f"yfinance|{title}|{pub_dt.isoformat()}".encode()).hexdigest()[:32]
                from data.schemas import NewsArticle as _NA
                result.append(_NA(
                    article_id=art_id,
                    title=title,
                    body=None,
                    url=url or None,
                    source="yfinance",
                    author=None,
                    tickers=[ticker],
                    event_timestamp=pub_dt,
                    fetch_timestamp=datetime.now(UTC),
                    sentiment_score=None,
                ))
            return result

        yf_articles = await asyncio.wait_for(
            loop.run_in_executor(None, _yfin_news),
            timeout=15.0,
        )
        all_articles.extend(yf_articles)
        logger.info("news.fetch.yfinance ticker=%s count=%d", ticker, len(yf_articles))
    except asyncio.TimeoutError:
        logger.warning("news.fetch.yfinance_timeout ticker=%s", ticker)
    except Exception as exc:
        logger.warning("news.fetch.yfinance_error ticker=%s error=%s", ticker, exc)

    # ── NewsAPI pull ──────────────────────────────────────────────────────────
    try:
        from config.settings import settings
        if settings.newsapi_key:
            from data.feeds.newsapi_feed import NewsApiFeed
            feed = NewsApiFeed(config={"api_key": settings.newsapi_key})
            newsapi_articles = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: feed.fetch_news(tickers=[ticker], start=start, end=end, max_results=body.limit),
                ),
                timeout=20.0,
            )
            all_articles.extend(newsapi_articles)
    except asyncio.TimeoutError:
        logger.warning("news.fetch.newsapi_timeout ticker=%s", ticker)
    except Exception as exc:
        logger.warning("news.fetch.newsapi_error ticker=%s error=%s", ticker, exc)

    # ── GDELT pull (best-effort, may be rate-limited) ─────────────────────────
    try:
        from data.feeds.gdelt_feed import GdeltFeed
        gdelt = GdeltFeed()
        gdelt_articles = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: gdelt.fetch_news(tickers=[ticker], start=start, end=end, max_results=min(body.limit * 5, 100)),
            ),
            timeout=20.0,
        )
        all_articles.extend(gdelt_articles)
    except asyncio.TimeoutError:
        logger.warning("news.fetch.gdelt_timeout ticker=%s", ticker)
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
