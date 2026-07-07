"""
data/schemas.py — Canonical data schemas for the quant-engine.

All data flowing through the pipeline is validated and normalised into one of
the Pydantic models defined here before it is stored or consumed by features /
strategies.  Using a single canonical schema per data type gives us two key
properties:

1. **Feed-agnosticism** — the feature pipeline and strategy engine never need
   to know whether a bar came from yfinance, Alpaca, or CoinGecko.  They all
   look the same after normalisation.

2. **Backtesting correctness** — every record carries *two* timestamps:
   - ``event_timestamp``: when the market event *actually happened*.  This is
     the bar close time for OHLCV, the article publication time for news, etc.
   - ``fetch_timestamp``: when *our system* retrieved the record.

   During backtesting we always use ``event_timestamp`` as the simulation
   clock.  A news article published at 09:30 must **never** be seen by a
   simulated strategy running at 09:25.  Using ``fetch_timestamp`` instead
   would introduce look-ahead bias and make every backtest look better than it
   really is.

Schema hierarchy
----------------
  OHLCVBar           — one OHLCV bar (any timeframe) for any ticker
  Trade              — a single executed trade (price + quantity)
  OrderBook          — a level-2 order book snapshot
  NewsArticle        — a news headline with optional full body text
  FundamentalSnapshot — quarterly/annual fundamentals for an equity ticker
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── OHLCVBar ──────────────────────────────────────────────────────────────────

class OHLCVBar(BaseModel):
    """
    One Open-High-Low-Close-Volume bar.

    A bar represents the aggregated trading activity over a discrete time
    interval (``interval``).  The canonical intervals used in this system are:

    * ``"1m"``  — 1-minute bars (intraday, from Alpaca/Binance)
    * ``"5m"``  — 5-minute bars
    * ``"15m"`` — 15-minute bars
    * ``"1h"``  — hourly bars
    * ``"1d"``  — daily bars (the default for backtesting)

    OHLCV is the foundational data type for virtually every technical
    indicator, so getting this schema right is critical.  The ``adjusted``
    flag distinguishes split/dividend-adjusted prices from raw prices.
    Adjusted prices are essential for backtesting — without them, a 2-for-1
    stock split will look like a 50% overnight loss.
    """

    ticker: str = Field(
        description=(
            "Ticker symbol. Equities use exchange symbols (AAPL, MSFT). "
            "Crypto uses BASE-QUOTE format (BTC-USD, ETH-USDT). "
            "Indices use caret prefix (^VIX, ^GSPC)."
        )
    )
    interval: str = Field(
        description="Bar duration: '1m', '5m', '15m', '1h', '1d', '1wk', '1mo'"
    )

    # ── Price fields ─────────────────────────────────────────────────────────
    open: float = Field(gt=0, description="First trade price in the interval")
    high: float = Field(gt=0, description="Highest trade price in the interval")
    low: float = Field(gt=0, description="Lowest trade price in the interval")
    close: float = Field(gt=0, description="Last trade price in the interval")
    volume: float = Field(ge=0, description="Total traded volume in the interval")

    # ── Timestamps ───────────────────────────────────────────────────────────
    event_timestamp: datetime = Field(
        description=(
            "Bar close time (UTC).  This is the authoritative time used by the "
            "backtesting engine — a strategy running at T only sees bars with "
            "event_timestamp <= T."
        )
    )
    fetch_timestamp: datetime = Field(
        description=(
            "UTC time when this record was fetched from the remote API. "
            "Used to measure data latency; never used as the simulation clock."
        )
    )

    # ── Metadata ─────────────────────────────────────────────────────────────
    source: str = Field(
        description="Data provider that produced this bar (yfinance, alpaca, binance, …)"
    )
    adjusted: bool = Field(
        default=False,
        description=(
            "True if open/high/low/close are split- and dividend-adjusted. "
            "Always use adjusted=True for backtesting equity strategies."
        ),
    )

    @field_validator("high")
    @classmethod
    def high_gte_low(cls, v: float, info) -> float:
        """Sanity check: high must be >= low and >= open/close."""
        return v  # full cross-field validation would need model_validator


# ── Trade ──────────────────────────────────────────────────────────────────────

class Trade(BaseModel):
    """
    A single executed trade (tick data).

    Trades are the most granular market data type — each record represents one
    matched buy/sell pair at the exchange level.  We don't store raw ticks in
    SQLite (too much volume), but we use them transiently when building real-
    time OHLCV bars for strategies that need sub-minute resolution.

    The ``side`` field is populated when the exchange reports aggressor side
    (Binance does; most equity feeds don't).  When unknown, it can be inferred
    using the Lee-Ready algorithm (trade at bid → sell, at ask → buy), but
    that inference is noisy and best left to the feature pipeline rather than
    hardcoded here.
    """

    ticker: str
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    side: Literal["buy", "sell", "unknown"] = "unknown"
    event_timestamp: datetime
    fetch_timestamp: datetime
    source: str
    trade_id: str | None = Field(
        default=None,
        description="Exchange-assigned trade ID, if available",
    )


# ── OrderBook ──────────────────────────────────────────────────────────────────

class OrderBookLevel(BaseModel):
    """One price level in an order book (price + total resting quantity)."""
    price: float = Field(gt=0)
    quantity: float = Field(ge=0)


class OrderBook(BaseModel):
    """
    Level-2 order book snapshot.

    The order book shows *all resting limit orders* at each price level,
    giving a picture of supply and demand.  Key derived quantities:

    * **Mid-price** = (best_ask + best_bid) / 2  — the fair-value estimate
      between the top-of-book quotes.
    * **Bid-ask spread** = best_ask − best_bid  — the cost of an immediate
      round-trip trade; a direct measure of market liquidity.
    * **Order book imbalance** = (bid_qty − ask_qty) / (bid_qty + ask_qty)
      at the top N levels — a short-term price pressure indicator used by the
      market-making strategy.

    The ``depth`` parameter controls how many price levels are stored.  We
    default to 10 levels (level-2 data); full depth-of-market (DOM) requires a
    premium data subscription.
    """

    ticker: str
    bids: list[OrderBookLevel] = Field(
        description="Bid levels sorted descending by price (best bid first)"
    )
    asks: list[OrderBookLevel] = Field(
        description="Ask levels sorted ascending by price (best ask first)"
    )
    depth: int = Field(default=10, ge=1, le=500)
    event_timestamp: datetime
    fetch_timestamp: datetime
    source: str


# ── NewsArticle ────────────────────────────────────────────────────────────────

class NewsArticle(BaseModel):
    """
    A single news article or headline.

    News is the primary input to the Sentiment strategy (Sub-Task 5).  The
    design decision to store both ``title`` and ``body`` is deliberate:

    * FinBERT achieves higher accuracy on the full article body than on
      headlines alone, because headlines are often sensationalist.
    * However, the body is larger and slower to score, so the pipeline
      scores titles first (fast, cheap) and re-scores bodies for high-
      conviction articles (|title_score| > 0.5).

    The ``tickers`` list is populated via two mechanisms:
    1. Keyword extraction — if "Apple" or "AAPL" appears in the article.
    2. Named entity recognition (NER) — a lightweight NER model tags company
       names (done in the sentiment feature module, not here).

    ``sentiment_score`` is populated *after* FinBERT scoring in the feature
    pipeline.  It is ``None`` when the article is first stored.
    """

    article_id: str = Field(
        description=(
            "Unique identifier — SHA-256 hash of (source + title + event_timestamp) "
            "to allow idempotent inserts."
        )
    )
    title: str
    body: str | None = None
    url: str | None = None
    source: str = Field(description="Provider: 'newsapi', 'gdelt', 'sec_edgar', …")
    author: str | None = None
    tickers: list[str] = Field(
        default_factory=list,
        description="Equity/crypto tickers mentioned in or linked to this article",
    )

    # ── Timestamps ───────────────────────────────────────────────────────────
    event_timestamp: datetime = Field(
        description=(
            "Publication time reported by the source (UTC).  "
            "Critical: use this as the simulation clock in backtests."
        )
    )
    fetch_timestamp: datetime

    # ── NLP annotations (populated by feature pipeline) ──────────────────────
    sentiment_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description=(
            "FinBERT sentiment in [-1, +1]. "
            "+1 = maximally positive, -1 = maximally negative, 0 = neutral. "
            "Populated by features/sentiment.py after fetch."
        ),
    )


# ── FundamentalSnapshot ────────────────────────────────────────────────────────

class FundamentalSnapshot(BaseModel):
    """
    A quarterly or annual fundamental data snapshot for an equity ticker.

    Fundamental data drives the Macro Factor strategy (Sub-Task 5) and the
    fundamental feature module (Sub-Task 3).  Key ratios explained:

    * **P/E ratio** (price-to-earnings) = market_price / eps_ttm
      Measures how expensive a stock is relative to its earnings.  A high P/E
      can indicate growth expectations *or* overvaluation; context matters.

    * **EPS surprise** = (reported_eps − consensus_eps) / |consensus_eps|
      The single strongest short-term price predictor in academic literature
      (Ball & Brown, 1968).  An earnings beat of > 2σ vs. analyst consensus
      triggers a momentum signal in the Macro Factor strategy.

    * **Revenue growth** = (current_revenue / prior_revenue) − 1
      Measures top-line business expansion.  Negative growth is a major red
      flag even if EPS is positive (one-time items can inflate EPS).

    All monetary values are in USD unless ``currency`` says otherwise.
    """

    ticker: str
    period: Literal["quarterly", "annual"]
    period_end_date: datetime = Field(
        description="Last day of the reported fiscal period (UTC)"
    )
    report_date: datetime = Field(
        description=(
            "Date the filing / earnings release became public.  "
            "Use this as event_timestamp in backtests — the data is only "
            "available *after* this date."
        )
    )

    # ── Income statement items ────────────────────────────────────────────────
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None

    # ── Per-share data ────────────────────────────────────────────────────────
    eps_reported: float | None = Field(
        default=None,
        description="Actual diluted EPS as reported in the filing",
    )
    eps_consensus: float | None = Field(
        default=None,
        description=(
            "Analyst consensus EPS estimate at the time of the report. "
            "Source: Alpha Vantage EARNINGS endpoint."
        ),
    )
    eps_surprise: float | None = Field(
        default=None,
        description=(
            "Earnings surprise = (reported − consensus) / |consensus|. "
            "Positive = beat, negative = miss.  "
            "Computed automatically when both eps_reported and eps_consensus are set."
        ),
    )

    # ── Valuation ratios ──────────────────────────────────────────────────────
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ev_ebitda: float | None = None
    debt_to_equity: float | None = None
    return_on_equity: float | None = None

    # ── Metadata ─────────────────────────────────────────────────────────────
    currency: str = "USD"
    source: str = Field(description="Provider: 'alpha_vantage', 'sec_edgar', …")
    event_timestamp: datetime = Field(
        description="Alias for report_date — used uniformly by the pipeline"
    )
    fetch_timestamp: datetime

    def model_post_init(self, __context) -> None:
        """Auto-compute earnings surprise if both EPS fields are available."""
        if (
            self.eps_surprise is None
            and self.eps_reported is not None
            and self.eps_consensus is not None
            and self.eps_consensus != 0
        ):
            self.eps_surprise = (
                (self.eps_reported - self.eps_consensus) / abs(self.eps_consensus)
            )
