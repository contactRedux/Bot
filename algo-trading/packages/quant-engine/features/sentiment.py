"""
features/sentiment.py — FinBERT NLP sentiment scoring and aggregation.

This module scores news articles using FinBERT (``ProsusAI/finbert``), a
BERT model fine-tuned on financial text, and aggregates per-article scores
into per-ticker time-series features used by the Sentiment strategy.

FinBERT overview
-----------------
FinBERT was trained on:
  * Reuters news corpus (financial domain)
  * Financial PhraseBank (annotated by domain experts)

It outputs three probabilities: ``positive``, ``negative``, ``neutral``.
We map these to a scalar signal in ``[-1, +1]``:

    score = P(positive) − P(negative)

This gives:
  * +1.0 = maximally positive sentiment
  * -1.0 = maximally negative sentiment
  *  0.0 = perfectly neutral or balanced

Why FinBERT over general BERT?
-------------------------------
General sentiment models trained on social media or product reviews perform
poorly on financial text because financial language is domain-specific:
* "The company missed earnings" — clearly negative (general models get this right)
* "The company beat estimates by 5%" — positive (often misclassified by general models)
* "The stock is down 3% on strong volume" — ambiguous (general models struggle)
* "Regulatory headwinds remain a concern" — subtle negative

FinBERT's domain-specific pre-training dramatically reduces these misclassifications.

Aggregation strategy
---------------------
A single article's score is noisy.  The strategy-level signal comes from
aggregating multiple articles over a time window:

  mean_score      — average sentiment (direction signal)
  score_std       — standard deviation (uncertainty measure)
  article_count   — number of articles (conviction proxy — more articles = more signal)
  score_momentum  — mean_score(short_window) − mean_score(long_window)
  decay_weighted  — exponentially decayed score (recent articles weighted more)

Decay function
--------------
Sentiment has a short shelf life.  An article published 3 days ago should
have less influence than one published 30 minutes ago.  We apply an exponential
decay with configurable half-life:

  decayed_score = score * exp(−λ * hours_since_publication)
  λ = ln(2) / half_life_hours

Model loading
-------------
FinBERT is loaded lazily (only when ``score_article`` is first called) to
avoid the ~2GB model download and ~5s load time at import.  In production,
the model stays resident in GPU memory and handles batch inference.

For backtesting, article scoring is done in bulk offline (store results in
the DataStore's ``sentiment_score`` column) so the feature pipeline just
reads pre-computed scores rather than running inference at each bar.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from data.schemas import NewsArticle

# Lazy globals — populated on first call to score_article()
_tokenizer = None
_model = None
_device = None


# ── Model loading ─────────────────────────────────────────────────────────────

def _load_finbert() -> None:
    """
    Load the FinBERT model and tokenizer into module-level globals.

    Called lazily on first use.  Downloads the model on first run (~440 MB).
    Subsequent runs use the Hugging Face cache at ``~/.cache/huggingface``.
    """
    global _tokenizer, _model, _device

    if _model is not None:
        return

    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        raise ImportError(
            "transformers and torch are required for FinBERT sentiment scoring.  "
            "Run: pip install 'quant-engine[ml]'"
        )

    import torch

    model_name = "ProsusAI/finbert"
    _tokenizer = AutoTokenizer.from_pretrained(model_name)
    _model = AutoModelForSequenceClassification.from_pretrained(model_name)
    _model.eval()

    # Use GPU if available; fall back to CPU
    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _model = _model.to(_device)


# ── Article scoring ───────────────────────────────────────────────────────────

def score_article(text: str) -> float:
    """
    Score a single text fragment using FinBERT.

    Maps the FinBERT output to a scalar in ``[-1, +1]``:
        score = P(positive) − P(negative)

    FinBERT label order: ``[positive, negative, neutral]``
    (confirmed from ProsusAI/finbert model card)

    Parameters
    ----------
    text : str
        Article title, headline, or body text (max ~512 tokens).
        Longer text is automatically truncated by the tokenizer.

    Returns
    -------
    float
        Sentiment score in ``[-1, +1]``.  Returns 0.0 on error.
    """
    if not text or not text.strip():
        return 0.0

    _load_finbert()

    try:
        import torch

        inputs = _tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        inputs = {k: v.to(_device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = _model(**inputs)

        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
        probs = probs.cpu().numpy()[0]

        # FinBERT label order: positive=0, negative=1, neutral=2
        p_positive = float(probs[0])
        p_negative = float(probs[1])
        score = p_positive - p_negative
        return float(np.clip(score, -1.0, 1.0))

    except Exception:
        return 0.0


def score_articles_batch(texts: list[str], batch_size: int = 32) -> list[float]:
    """
    Score a list of text fragments in batches for efficiency.

    Batching is critical for GPU throughput — processing 32 articles at once
    is ~10× faster than processing them one by one.

    Parameters
    ----------
    texts : list[str]
        List of article texts to score.
    batch_size : int
        Number of articles per inference batch.

    Returns
    -------
    list[float]
        Scores in the same order as the input texts.
    """
    if not texts:
        return []

    _load_finbert()

    try:
        import torch

        scores: list[float] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = _tokenizer(
                batch,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            inputs = {k: v.to(_device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = _model(**inputs)

            probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
            probs = probs.cpu().numpy()

            for p in probs:
                score = float(p[0] - p[1])  # positive − negative
                scores.append(float(np.clip(score, -1.0, 1.0)))

        return scores

    except Exception:
        return [0.0] * len(texts)


# ── Aggregation ───────────────────────────────────────────────────────────────

# ── Source quality weights ────────────────────────────────────────────────────

# Bloomberg news is from a curated institutional feed and therefore given a
# higher base weight than free-tier sources.  The multiplier is applied to the
# per-article weight *before* normalisation, so Bloomberg articles contribute
# proportionally more to the aggregated signal when both sources are present.
#
# Rationale: Bloomberg articles are typically more precise, less duplicated,
# and closer to the primary information event than re-syndicated free feeds.
_SOURCE_QUALITY_WEIGHTS: dict[str, float] = {
    "bloomberg": 2.0,  # institutional — higher quality
    "newsapi": 1.0,    # standard
    "gdelt": 0.8,      # aggregated/re-syndicated — slightly lower quality
}
_DEFAULT_SOURCE_QUALITY_WEIGHT = 1.0


def aggregate_sentiment(
    articles: list[NewsArticle],
    ticker: str,
    as_of: datetime,
    window_hours: int = 24,
    decay_half_life_hours: float = 6.0,
) -> pd.Series:
    """
    Aggregate FinBERT scores for a ticker over a time window.

    Filters articles associated with ``ticker`` published within
    ``window_hours`` before ``as_of``, applies exponential decay weighting,
    and returns summary statistics.

    Source quality weighting
    ------------------------
    Bloomberg articles receive a 2× quality multiplier relative to free-tier
    sources (NewsAPI = 1×, GDELT = 0.8×).  This multiplier is applied on top
    of the exponential time-decay weight before normalisation, so a Bloomberg
    article from 3 hours ago outweighs a NewsAPI article of the same age.
    The effective weight for each article is:

        w_i = quality(source_i) × exp(−λ × hours_ago_i)

    All weights are then normalised to sum to 1 before computing the
    decay-weighted score.

    Parameters
    ----------
    articles : list[NewsArticle]
        Candidate articles (typically from DataStore.read_news).
        Only those with ``sentiment_score`` set are used (pre-scored articles).
    ticker : str
        The ticker to filter for.
    as_of : datetime
        The reference timestamp (simulation clock time in backtests).
        Only articles with ``event_timestamp < as_of`` are included.
    window_hours : int
        How many hours of history to include.
    decay_half_life_hours : float
        Half-life for exponential time decay of article scores.  A short
        half-life (e.g. 6h) makes the signal very reactive; a long one
        (e.g. 48h) makes it more persistent.

    Returns
    -------
    pd.Series
        Index: ``["sentiment_mean", "sentiment_std", "article_count",
                   "sentiment_decayed", "sentiment_momentum"]``
        All values are ``float``; NaN when no articles are found.
    """
    _default = pd.Series(
        {
            "sentiment_mean": 0.0,
            "sentiment_std": 0.0,
            "article_count": 0.0,
            "sentiment_decayed": 0.0,
            "sentiment_momentum": 0.0,
        }
    )

    if not articles:
        return _default

    # Filter to ticker and time window
    cutoff = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of
    cutoff_start = cutoff.timestamp() - window_hours * 3600

    relevant: list[tuple[float, float, float]] = []  # (hours_ago, score, quality_w)
    for a in articles:
        if ticker not in a.tickers:
            continue
        if a.sentiment_score is None:
            continue
        ts = a.event_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts >= cutoff:
            continue  # future article — skip (look-ahead guard)
        article_ts = ts.timestamp()
        if article_ts < cutoff_start:
            continue  # outside window

        hours_ago = (cutoff.timestamp() - article_ts) / 3600
        quality_w = _SOURCE_QUALITY_WEIGHTS.get(a.source, _DEFAULT_SOURCE_QUALITY_WEIGHT)
        relevant.append((hours_ago, a.sentiment_score, quality_w))

    if not relevant:
        return _default

    hours_arr = np.array([r[0] for r in relevant])
    scores_arr = np.array([r[1] for r in relevant])
    quality_arr = np.array([r[2] for r in relevant])

    # Exponential decay weights × source quality multiplier
    lam = math.log(2) / decay_half_life_hours
    weights = quality_arr * np.exp(-lam * hours_arr)
    weights /= weights.sum()  # normalize

    decay_weighted_score = float(np.dot(weights, scores_arr))
    mean_score = float(np.mean(scores_arr))
    std_score = float(np.std(scores_arr, ddof=1)) if len(scores_arr) > 1 else 0.0

    # Momentum: recent half vs. old half of window
    half_window = window_hours / 2
    recent_mask = hours_arr <= half_window
    older_mask = hours_arr > half_window
    recent_mean = float(np.mean(scores_arr[recent_mask])) if recent_mask.any() else 0.0
    older_mean = float(np.mean(scores_arr[older_mask])) if older_mask.any() else 0.0
    momentum = recent_mean - older_mean

    return pd.Series({
        "sentiment_mean": mean_score,
        "sentiment_std": std_score,
        "article_count": float(len(relevant)),
        "sentiment_decayed": decay_weighted_score,
        "sentiment_momentum": momentum,
    })


def build_sentiment_timeseries(
    articles: list[NewsArticle],
    ticker: str,
    price_index: pd.DatetimeIndex,
    window_hours: int = 24,
    decay_half_life_hours: float = 6.0,
) -> pd.DataFrame:
    """
    Build a time-series of sentiment features aligned to a price bar index.

    For each bar in ``price_index``, calls ``aggregate_sentiment`` with the
    bar's timestamp as ``as_of``.  This produces a properly time-aligned
    sentiment feature DataFrame for use in the FeaturePipeline.

    Parameters
    ----------
    articles : list[NewsArticle]
        All articles for the ticker's history (pre-loaded from DataStore).
    ticker : str
        Ticker to aggregate sentiment for.
    price_index : pd.DatetimeIndex
        The timestamp index of the price bar DataFrame.
    window_hours, decay_half_life_hours :
        Same as in ``aggregate_sentiment``.

    Returns
    -------
    pd.DataFrame
        Columns: sentiment features, index = price_index.
    """
    rows = []
    for ts in price_index:
        row = aggregate_sentiment(
            articles, ticker, ts, window_hours, decay_half_life_hours
        )
        rows.append(row)

    return pd.DataFrame(rows, index=price_index)
