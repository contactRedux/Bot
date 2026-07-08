"""
strategies/sentiment.py — FinBERT news sentiment strategy.

Strategy logic
--------------
News sentiment is one of the few genuine *alpha* sources that is orthogonal
to price-based signals.  When a company releases positive news (earnings beat,
product launch, regulatory approval), the price reaction often takes hours or
days to fully incorporate the information.

This strategy:
1. Aggregates FinBERT sentiment scores over a rolling ``sentiment_window_hours``
   window per ticker.
2. Computes a z-score of the current window's mean score relative to a longer
   baseline period.
3. If the z-score crosses ``entry_z_score`` AND there are enough articles
   (``min_article_count``), emits a directional order:
       sentiment z > +threshold  →  BUY (positive news surprise)
       sentiment z < −threshold  →  SELL (negative news surprise)
4. Size is scaled by ``article_count`` (more articles = higher conviction).
5. Position is held for at most ``max_hold_bars`` bars, then force-closed.

Exponential decay
------------------
Recent articles have more information value than old ones.  An article from
3 hours ago about an earnings release is less relevant than one from 10 minutes
ago.  We apply exponential decay:

    weight(t) = exp(−λ × hours_since_pub)
    λ = ln(2) / decay_half_life_hours

This means an article published ``decay_half_life_hours`` ago has half the
weight of a freshly published article.

FinBERT score mapping
---------------------
FinBERT outputs: positive_prob, negative_prob, neutral_prob
Score = positive_prob − negative_prob ∈ [−1, +1]

The ``sentiment_score`` field on NewsArticle objects (from features/sentiment.py)
already contains this pre-computed scalar.

Configuration (strategy_config.yaml)
-------------------------------------
    sentiment_window_hours  : rolling window for aggregation (default 4h)
    entry_z_score           : z-score threshold to trigger   (default 2.0)
    min_article_count       : minimum articles to act        (default 3)
    decay_half_life_hours   : decay half-life                (default 2.0)
    max_hold_bars           : force-exit after N bars        (default 8)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ScoredArticle — lightweight container for sentiment data
# ---------------------------------------------------------------------------

class ScoredArticle:
    """
    Minimal representation of a sentiment-scored news article.

    Parameters match the ``NewsArticle`` schema in data/schemas.py.  We use
    a plain class here to avoid importing data layer types from the strategy.
    """

    def __init__(
        self,
        ticker: str,
        sentiment_score: float,  # FinBERT score ∈ [-1, +1]
        published_at: datetime,
        headline: str = "",
    ) -> None:
        self.ticker = ticker
        self.sentiment_score = float(np.clip(sentiment_score, -1.0, 1.0))
        self.published_at = published_at
        self.headline = headline


# ---------------------------------------------------------------------------
# SentimentStrategy
# ---------------------------------------------------------------------------

class SentimentStrategy(BaseStrategy):
    """
    FinBERT rolling sentiment signal strategy.

    Parameters
    ----------
    config : dict
        From strategy_config.yaml ``sentiment`` section.
    tickers : list[str]
        Tickers to monitor.
    base_position_size : float
        Position size per article-count-unit of conviction.
    """

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        base_position_size: float = 50.0,
    ) -> None:
        super().__init__("sentiment", config, tickers)
        self.base_position_size = base_position_size

        self.window_hours: float = config.get("sentiment_window_hours", 4.0)
        self.entry_z: float = config.get("entry_z_score", 2.0)
        self.min_articles: int = int(config.get("min_article_count", 3))
        self.decay_half_life: float = config.get("decay_half_life_hours", 2.0)
        self.max_hold_bars: int = int(config.get("max_hold_bars", 8))

        # Rolling article buffer: ticker → list of ScoredArticle
        self._articles: dict[str, list[ScoredArticle]] = {t: [] for t in tickers}
        # Baseline sentiment distribution (longer history for z-score denominator)
        self._baseline_scores: dict[str, list[float]] = {t: [] for t in tickers}
        # Current wall-clock time (updated via set_current_time for backtesting)
        self._current_time: datetime = datetime.now(timezone.utc)

        self._pending_orders: list[Order] = []

    def set_current_time(self, dt: datetime) -> None:
        """Called by the orchestrator to advance the simulated clock."""
        self._current_time = dt

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_news(self, ticker: str, article: Any) -> None:
        """
        Receive a scored article.  Stores it and may trigger a signal.

        Parameters
        ----------
        article : ScoredArticle or NewsArticle (from data.schemas)
            Any object with ``.sentiment_score`` and ``.published_at`` attributes.
        """
        if not self._enabled or ticker not in self.tickers:
            return

        # Normalise to ScoredArticle interface
        score = float(getattr(article, "sentiment_score", 0.0))
        pub_at = getattr(article, "published_at", self._current_time)
        if not isinstance(pub_at, datetime):
            pub_at = self._current_time
        # Ensure UTC
        if pub_at.tzinfo is None:
            pub_at = pub_at.replace(tzinfo=timezone.utc)

        scored = ScoredArticle(ticker=ticker, sentiment_score=score, published_at=pub_at)
        self._articles.setdefault(ticker, []).append(scored)
        # Prune articles older than 5× the window (no longer relevant for baseline)
        max_age_hours = self.window_hours * 10
        self._prune_old(ticker, max_age_hours)

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        if not self._enabled:
            return

        state = self._state_for(ticker)
        close = float(bar.get("close", 0.0))

        # Advance internal clock to bar time
        if hasattr(bar, "name") and isinstance(bar.name, (datetime, pd.Timestamp)):
            self._current_time = pd.Timestamp(bar.name).to_pydatetime()
            if self._current_time.tzinfo is None:
                self._current_time = self._current_time.replace(tzinfo=timezone.utc)

        # Force-close after max hold bars
        if not state.is_flat:
            state.bars_in_position += 1
            if state.bars_in_position >= self.max_hold_bars:
                side = OrderSide.SELL if state.is_long else OrderSide.BUY
                self._pending_orders.append(
                    self._make_order(ticker, side, abs(state.position), 1.0,
                                     reason="max_hold_expiry")
                )
                state.position = 0.0
                logger.info("Sentiment [%s] force-close after %d bars", ticker, self.max_hold_bars)
            return

        # Compute aggregated sentiment signal
        result = self._aggregate_sentiment(ticker)
        if result is None:
            return

        mean_score, article_count, z_score = result

        if article_count < self.min_articles:
            return  # Not enough articles for a reliable signal

        if abs(z_score) < self.entry_z:
            return  # Signal not strong enough

        # Scale position by article count (more coverage = higher conviction)
        article_size_factor = min(2.0, article_count / self.min_articles)
        qty = self.base_position_size * article_size_factor
        confidence = min(1.0, abs(z_score) / (self.entry_z * 2))

        if z_score > self.entry_z:
            side = OrderSide.BUY
            state.position = qty
        else:
            side = OrderSide.SELL
            state.position = -qty

        state.entry_price = close
        state.bars_in_position = 0

        self._pending_orders.append(self._make_order(
            ticker, side, qty, confidence,
            sentiment_z=round(z_score, 3),
            article_count=article_count,
            mean_score=round(mean_score, 4),
        ))
        logger.info(
            "Sentiment [%s] %s z=%.2f articles=%d score=%.3f",
            ticker, side.value, z_score, article_count, mean_score,
        )

    def generate_orders(self) -> list[Order]:
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _aggregate_sentiment(
        self, ticker: str
    ) -> tuple[float, int, float] | None:
        """
        Compute decay-weighted mean score, article count, and z-score.

        Returns (mean_score, article_count, z_score) or None if insufficient data.
        """
        articles = self._articles.get(ticker, [])
        if not articles:
            return None

        now = self._current_time
        lambda_decay = math.log(2) / (self.decay_half_life + 1e-8)
        window_cutoff = self.window_hours * 3600  # seconds

        scores, weights = [], []
        for art in articles:
            age_seconds = (now - art.published_at).total_seconds()
            if age_seconds < 0:
                age_seconds = 0
            if age_seconds > window_cutoff:
                continue
            age_hours = age_seconds / 3600
            w = math.exp(-lambda_decay * age_hours)
            scores.append(art.sentiment_score)
            weights.append(w)

        if not scores:
            return None

        weights_arr = np.array(weights)
        scores_arr = np.array(scores)
        total_weight = weights_arr.sum() + 1e-8
        mean_score = float(np.dot(weights_arr, scores_arr) / total_weight)
        article_count = len(scores)

        # Baseline distribution for z-score normalisation
        baseline = self._baseline_scores.get(ticker, [])
        baseline.append(mean_score)
        self._baseline_scores[ticker] = baseline[-100:]  # keep last 100 windows

        if len(baseline) < 5:
            return None  # Not enough baseline

        mu_base = np.mean(baseline[:-1])  # exclude current
        std_base = np.std(baseline[:-1]) + 1e-6
        z_score = float((mean_score - mu_base) / std_base)

        return mean_score, article_count, z_score

    def _prune_old(self, ticker: str, max_age_hours: float) -> None:
        """Remove articles older than max_age_hours from the buffer."""
        now = self._current_time
        cutoff_seconds = max_age_hours * 3600
        self._articles[ticker] = [
            a for a in self._articles.get(ticker, [])
            if (now - a.published_at).total_seconds() < cutoff_seconds
        ]
