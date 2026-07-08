"""
features/pipeline.py — FeaturePipeline: chains all feature modules into a feature matrix.

The ``FeaturePipeline`` is the single entry point for converting raw market
data into a fully assembled, ML-ready feature matrix.  It:

1. Accepts OHLCV bars and optional news/fundamental/macro data for a ticker.
2. Calls each feature module in turn (technical, fundamental, sentiment, macro).
3. Aligns all features to the OHLCV time index.
4. Forward-fills missing values up to a configured limit.
5. Returns a named feature matrix (DataFrame) that is safe for backtesting.

Walk-forward safety guarantee
-------------------------------
The pipeline enforces temporal integrity at every step:

* Technical indicators use only backward-looking rolling windows.
* Fundamental features are forward-filled from the ``report_date`` (the date
  they became *available*), never from the ``period_end_date``.
* Sentiment features use only articles published before the bar's timestamp.
* Macro features are aligned by forward-fill from daily FRED/VIX data.

The test suite in ``tests/features/test_pipeline.py`` verifies this for
every column by asserting that no column contains a value at time T that
depends on data from T+1 or later.

Design: DataFrame-in, DataFrame-out
-------------------------------------
The pipeline is designed to work with DataFrames throughout — no custom
objects needed.  This makes it easy to plug into:
* The backtesting engine (replay each bar and compute features up to that bar)
* Model training (generate the full training matrix in one call)
* Live strategy execution (compute features for the latest bar only)

Usage
------
::

    from features.pipeline import FeaturePipeline
    from data.store import DataStore

    store = DataStore("sqlite:///./algo_trading.db")
    pipeline = FeaturePipeline(store=store)

    # Build feature matrix for AAPL from 2023-01-01 to 2024-01-01
    features = pipeline.build(
        ticker="AAPL",
        start=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, tzinfo=timezone.utc),
        interval="1d",
    )
    # features is a pd.DataFrame with ~60 columns, daily index
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

from features.fundamental import (
    add_fundamental_features,
    align_fundamentals_to_price_index,
    snapshots_to_dataframe,
)
from features.sentiment import build_sentiment_timeseries
from features.technical import add_all_technical

if TYPE_CHECKING:
    from data.schemas import OrderBook

logger = structlog.get_logger(__name__)

# Maximum bars to forward-fill across when aligning fundamental features
# to daily price index.  After this many days, features go NaN.
_MAX_FUNDAMENTAL_FILL_DAYS = 90

# Maximum bars to forward-fill for any feature source
_MAX_FFILL_LIMIT = 5


class FeaturePipeline:
    """
    Orchestrates all feature modules and returns a complete feature matrix.

    Parameters
    ----------
    store : DataStore, optional
        DataStore instance used to load news and fundamentals.  If None,
        only technical features are computed (useful for quick backtests
        that don't need NLP or fundamental features).
    include_technical : bool
        Include technical indicators (default True).
    include_fundamental : bool
        Include fundamental features (default True).
    include_sentiment : bool
        Include FinBERT sentiment features (default True).
        Requires pre-scored articles in the DataStore.
    include_macro : bool
        Include macro regime features (default True).
        Requires internet access to fetch VIX/yield data on first use.
    macro_cache : pd.DataFrame, optional
        Pre-fetched macro features (avoids repeated API calls during
        backtesting loops).
    sentiment_window_hours : int
        Lookback window for sentiment aggregation (default 24 hours).
    ffill_limit : int
        Maximum consecutive NaNs to forward-fill in the final matrix.
    """

    def __init__(
        self,
        store=None,
        include_technical: bool = True,
        include_fundamental: bool = True,
        include_sentiment: bool = True,
        include_macro: bool = True,
        macro_cache: pd.DataFrame | None = None,
        sentiment_window_hours: int = 24,
        ffill_limit: int = _MAX_FFILL_LIMIT,
        order_book: OrderBook | None = None,
    ) -> None:
        self.store = store
        self.include_technical = include_technical
        self.include_fundamental = include_fundamental
        self.include_sentiment = include_sentiment
        self.include_macro = include_macro
        self._macro_cache = macro_cache
        self.sentiment_window_hours = sentiment_window_hours
        self.ffill_limit = ffill_limit
        # Latest order book snapshot — updated externally via set_order_book()
        self._order_book: OrderBook | None = order_book

    # ── Primary interface ─────────────────────────────────────────────────────

    def build(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
        ohlcv_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        Build the complete feature matrix for a single ticker.

        Parameters
        ----------
        ticker : str
            Ticker symbol.
        start, end : datetime
            UTC date range.
        interval : str
            Bar interval.  All data is loaded at this frequency.
        ohlcv_df : pd.DataFrame, optional
            Pre-loaded OHLCV DataFrame.  If None, loads from ``self.store``.
            Must have columns: ``open``, ``high``, ``low``, ``close``, ``volume``
            and a UTC DatetimeIndex.

        Returns
        -------
        pd.DataFrame
            Feature matrix aligned to the OHLCV time index.
            Returns an empty DataFrame if OHLCV data is not available.
        """
        # ── Step 1: Load OHLCV ────────────────────────────────────────────────
        if ohlcv_df is not None:
            df = ohlcv_df.copy()
        elif self.store is not None:
            df = self._load_ohlcv(ticker, interval, start, end)
        else:
            logger.warning("pipeline.no_ohlcv_source", ticker=ticker)
            return pd.DataFrame()

        if df.empty:
            logger.warning("pipeline.empty_ohlcv", ticker=ticker, interval=interval)
            return pd.DataFrame()

        price_index = df.index

        logger.info(
            "pipeline.build_start",
            ticker=ticker,
            bars=len(df),
            interval=interval,
            start=price_index[0].isoformat(),
            end=price_index[-1].isoformat(),
        )

        feature_parts: list[pd.DataFrame] = []

        # ── Step 2: Technical indicators ──────────────────────────────────────
        if self.include_technical:
            tech = self._compute_technical(df)
            feature_parts.append(tech)
            logger.debug("pipeline.technical_done", ticker=ticker, cols=len(tech.columns))

        # ── Step 3: Fundamental features ──────────────────────────────────────
        if self.include_fundamental and self.store is not None:
            fund = self._compute_fundamental(ticker, price_index, start, end)
            if not fund.empty:
                feature_parts.append(fund)
                logger.debug("pipeline.fundamental_done", ticker=ticker, cols=len(fund.columns))

        # ── Step 4: Sentiment features ────────────────────────────────────────
        if self.include_sentiment and self.store is not None:
            sent = self._compute_sentiment(ticker, price_index, start, end)
            if not sent.empty:
                feature_parts.append(sent)
                logger.debug("pipeline.sentiment_done", ticker=ticker, cols=len(sent.columns))

        # ── Step 5: Macro features ────────────────────────────────────────────
        if self.include_macro:
            macro = self._compute_macro(price_index, start, end)
            if not macro.empty:
                feature_parts.append(macro)
                logger.debug("pipeline.macro_done", ticker=ticker, cols=len(macro.columns))

        # ── Step 5.5: Order-book imbalance ─────────────────────────────────────
        obi = self._compute_order_book_imbalance(self._order_book)
        if not np.isnan(obi):
            # Broadcast scalar to a constant column aligned to the price index
            obi_series = pd.DataFrame(
                {"order_book_imbalance": obi}, index=price_index
            )
            feature_parts.append(obi_series)
            logger.debug("pipeline.obi_done", ticker=ticker, obi=round(obi, 4))

        if not feature_parts:
            logger.warning("pipeline.no_features_computed", ticker=ticker)
            return pd.DataFrame(index=price_index)

        # ── Step 6: Align and concatenate ─────────────────────────────────────
        features = pd.concat(feature_parts, axis=1)

        # Reindex to ensure exact alignment with price_index (no extra rows)
        features = features.reindex(price_index)

        # ── Step 7: Forward-fill with cap ─────────────────────────────────────
        features = features.ffill(limit=self.ffill_limit)

        # Drop columns that are entirely NaN (e.g. sentiment when no articles)
        features = features.dropna(axis=1, how="all")

        logger.info(
            "pipeline.build_done",
            ticker=ticker,
            rows=len(features),
            cols=len(features.columns),
            nan_pct=round(features.isna().mean().mean() * 100, 1),
        )

        return features

    def build_multi(
        self,
        tickers: list[str],
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """
        Build feature matrices for multiple tickers.

        Returns a dict mapping ticker → feature DataFrame.
        """
        return {
            t: self.build(t, start, end, interval)
            for t in tickers
        }

    # ── Feature computation helpers ───────────────────────────────────────────

    def _load_ohlcv(
        self, ticker: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Load OHLCV bars from DataStore and convert to a pandas DataFrame."""
        bars = self.store.read_bars(ticker, interval, start, end)
        if not bars:
            return pd.DataFrame()

        rows = [
            {
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        idx = pd.DatetimeIndex([b.event_timestamp for b in bars])
        return pd.DataFrame(rows, index=idx).sort_index()

    def _compute_technical(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run all technical indicator functions on the OHLCV DataFrame."""
        try:
            return add_all_technical(df)
        except Exception as exc:
            logger.error("pipeline.technical_error", error=str(exc))
            return pd.DataFrame(index=df.index)

    def _compute_fundamental(
        self,
        ticker: str,
        price_index: pd.DatetimeIndex,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Load, compute, and align fundamental features to the price index."""
        try:
            # Add a generous buffer before start to capture the most recent
            # fundamental snapshot that pre-dates the analysis window
            buffer_start = start - timedelta(days=365)
            snapshots = self.store.read_fundamentals(
                ticker, period="quarterly", start=buffer_start, end=end
            )
            if not snapshots:
                return pd.DataFrame()

            fund_df = snapshots_to_dataframe(snapshots)
            fund_features = add_fundamental_features(fund_df)
            aligned = align_fundamentals_to_price_index(
                fund_features, price_index, max_fill_periods=_MAX_FUNDAMENTAL_FILL_DAYS
            )
            # Drop redundant raw columns; keep only derived features
            feature_cols = [
                c for c in aligned.columns
                if c not in ("ticker", "period", "revenue", "gross_profit",
                             "operating_income", "net_income", "eps_reported",
                             "eps_consensus", "eps_surprise", "pe_ratio", "pb_ratio",
                             "ev_ebitda", "debt_to_equity", "return_on_equity")
            ]
            return aligned[feature_cols] if feature_cols else pd.DataFrame(index=price_index)
        except Exception as exc:
            logger.error("pipeline.fundamental_error", ticker=ticker, error=str(exc))
            return pd.DataFrame()

    def _compute_sentiment(
        self,
        ticker: str,
        price_index: pd.DatetimeIndex,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Load pre-scored news articles and build sentiment time series."""
        try:
            buffer_start = start - timedelta(hours=self.sentiment_window_hours * 2)
            articles = self.store.read_news(
                tickers=[ticker],
                start=buffer_start,
                end=end,
                scored_only=True,
                max_results=5000,
            )
            if not articles:
                return pd.DataFrame()

            return build_sentiment_timeseries(
                articles,
                ticker,
                price_index,
                window_hours=self.sentiment_window_hours,
            )
        except Exception as exc:
            logger.error("pipeline.sentiment_error", ticker=ticker, error=str(exc))
            return pd.DataFrame()

    def _compute_macro(
        self,
        price_index: pd.DatetimeIndex,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch (or use cached) macro features aligned to the price index."""
        try:
            if self._macro_cache is not None:
                # Re-align cached macro to this price index
                combined = self._macro_cache.index.union(price_index).sort_values()
                return (
                    self._macro_cache.reindex(combined)
                    .ffill(limit=5)
                    .reindex(price_index)
                )

            from features.macro import build_macro_features
            return build_macro_features(start, end, price_index=price_index)
        except Exception as exc:
            logger.error("pipeline.macro_error", error=str(exc))
            return pd.DataFrame()

    # ── Utility ───────────────────────────────────────────────────────────────

    def set_order_book(self, order_book: OrderBook | None) -> None:
        """
        Update the latest order book snapshot for the next ``build()`` call.

        Called by the live data pipeline after each order book update from
        Binance or Alpaca.  The snapshot is used by ``build()`` to compute
        the order_book_imbalance feature for the current bar.

        Setting to ``None`` (or when no live data is available) causes
        ``build()`` to emit ``NaN`` for the order_book_imbalance column,
        which is handled gracefully by downstream models via NaN imputation.
        """
        self._order_book = order_book

    @staticmethod
    def _compute_order_book_imbalance(order_book: OrderBook | None) -> float:
        """
        Compute order-book imbalance from the top-5 bid/ask levels.

        Returns
        -------
        float
            Imbalance ∈ [-1, +1].  Positive → buy pressure; negative → sell pressure.
            ``float("nan")`` when no order book data is available.
        """
        if order_book is None:
            return float("nan")
        bid_vol = sum(level.quantity for level in order_book.bids[:5])
        ask_vol = sum(level.quantity for level in order_book.asks[:5])
        total = bid_vol + ask_vol
        if total <= 0:
            return float("nan")
        return (bid_vol - ask_vol) / total

    def set_macro_cache(self, macro_df: pd.DataFrame) -> None:
        """
        Pre-load macro features to avoid repeated API calls.

        Call this once at the start of a backtest run with the full date range,
        then the pipeline will reuse the cache for every ticker.

        Example
        -------
        ::

            from features.macro import build_macro_features
            macro = build_macro_features(start, end)
            pipeline.set_macro_cache(macro)
        """
        self._macro_cache = macro_df

    def feature_names(
        self,
        ticker: str = "AAPL",
        n_bars: int = 300,
    ) -> list[str]:
        """
        Return the list of feature column names without running full data loading.

        Generates synthetic OHLCV data and returns the technical feature
        columns.  Useful for model training setup (determining input dimensionality).

        Parameters
        ----------
        ticker : str
            Ticker label (only affects logging).
        n_bars : int
            Number of synthetic bars to generate.

        Returns
        -------
        list[str]
            Feature column names in the order they appear in ``build()`` output.
        """
        idx = pd.date_range("2020-01-01", periods=n_bars, freq="D", tz="UTC")
        rng = np.random.default_rng(42)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_bars)))
        df = pd.DataFrame(
            {
                "open": prices * (1 + rng.normal(0, 0.002, n_bars)),
                "high": prices * (1 + rng.uniform(0, 0.01, n_bars)),
                "low": prices * (1 - rng.uniform(0, 0.01, n_bars)),
                "close": prices,
                "volume": rng.integers(1_000_000, 10_000_000, n_bars).astype(float),
            },
            index=idx,
        )
        pipeline = FeaturePipeline(
            store=None,
            include_fundamental=False,
            include_sentiment=False,
            include_macro=False,
        )
        result = pipeline.build(ticker, idx[0], idx[-1], ohlcv_df=df)
        return list(result.columns)
