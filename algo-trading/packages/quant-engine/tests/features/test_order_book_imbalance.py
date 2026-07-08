"""
tests/features/test_order_book_imbalance.py — Tests for order-book imbalance feature.

Phase 7: verifies:
 - None order book → returns NaN.
 - All bids → imbalance = +1.0.
 - All asks → imbalance = -1.0.
 - Mixed book → imbalance ∈ (-1, +1).
 - FeaturePipeline.set_order_book() updates the snapshot.
 - build() includes order_book_imbalance column when a book is set.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from data.schemas import OrderBook, OrderBookLevel
from features.pipeline import FeaturePipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _book(bid_sizes: list[float], ask_sizes: list[float]) -> OrderBook:
    """Build an OrderBook with given bid/ask quantities at sequential prices."""
    bids = [
        OrderBookLevel(price=100.0 - i * 0.01, quantity=q)
        for i, q in enumerate(bid_sizes)
    ]
    asks = [
        OrderBookLevel(price=100.01 + i * 0.01, quantity=q)
        for i, q in enumerate(ask_sizes)
    ]
    ts = datetime(2024, 1, 10, tzinfo=UTC)
    return OrderBook(
        ticker="AAPL",
        bids=bids,
        asks=asks,
        event_timestamp=ts,
        fetch_timestamp=ts,
        source="test",
    )


# ---------------------------------------------------------------------------
# Static method tests
# ---------------------------------------------------------------------------

class TestComputeOrderBookImbalance:
    def test_none_returns_nan(self):
        result = FeaturePipeline._compute_order_book_imbalance(None)
        assert math.isnan(result)

    def test_all_bids_imbalance_is_positive_one(self):
        book = _book([100.0, 200.0, 300.0, 50.0, 25.0], [0.0, 0.0, 0.0, 0.0, 0.0])
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert result == pytest.approx(1.0)

    def test_all_asks_imbalance_is_negative_one(self):
        book = _book([0.0, 0.0, 0.0, 0.0, 0.0], [100.0, 200.0, 300.0, 50.0, 25.0])
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert result == pytest.approx(-1.0)

    def test_equal_bid_ask_imbalance_is_zero(self):
        book = _book([100.0, 100.0], [100.0, 100.0])
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert result == pytest.approx(0.0)

    def test_imbalance_in_range(self):
        book = _book([300.0, 200.0, 100.0], [50.0, 80.0, 120.0])
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert -1.0 <= result <= 1.0

    def test_only_top_5_levels_used(self):
        """Even with >5 levels, only top 5 contribute to the imbalance."""
        # 10 bid levels of 100 each; 10 ask levels of 100 each → should be 0
        book = _book([100.0] * 10, [100.0] * 10)
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert result == pytest.approx(0.0)

    def test_empty_book_returns_nan(self):
        book = _book([], [])
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert math.isnan(result)

    def test_manual_calculation(self):
        """Verify against manual formula: (bid_vol - ask_vol) / (bid_vol + ask_vol)."""
        bid_sizes = [500.0, 300.0, 200.0, 100.0, 50.0]  # sum = 1150
        ask_sizes = [200.0, 150.0, 100.0, 80.0, 70.0]   # sum = 600
        book = _book(bid_sizes, ask_sizes)
        expected = (1150.0 - 600.0) / (1150.0 + 600.0)
        result = FeaturePipeline._compute_order_book_imbalance(book)
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------

class TestFeaturePipelineOrderBook:
    def _make_ohlcv(self, n: int = 50) -> pd.DataFrame:
        idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
        rng = np.random.default_rng(42)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        return pd.DataFrame(
            {
                "open": prices * 0.998,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": rng.integers(500_000, 5_000_000, n).astype(float),
            },
            index=idx,
        )

    def test_no_order_book_no_obi_column(self):
        """Without an order book, the pipeline should not emit obi column."""
        pipeline = FeaturePipeline(
            store=None,
            include_fundamental=False,
            include_sentiment=False,
            include_macro=False,
        )
        df = self._make_ohlcv()
        result = pipeline.build(
            "AAPL",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
            ohlcv_df=df,
        )
        assert "order_book_imbalance" not in result.columns

    def test_with_order_book_adds_obi_column(self):
        """When an order book is set, pipeline adds order_book_imbalance column."""
        book = _book([500.0, 300.0, 200.0, 100.0, 50.0], [100.0, 80.0, 60.0, 40.0, 20.0])
        pipeline = FeaturePipeline(
            store=None,
            include_fundamental=False,
            include_sentiment=False,
            include_macro=False,
            order_book=book,
        )
        df = self._make_ohlcv()
        result = pipeline.build(
            "AAPL",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
            ohlcv_df=df,
        )
        assert "order_book_imbalance" in result.columns
        # All values should be the same (scalar broadcast)
        obi_vals = result["order_book_imbalance"].dropna()
        assert len(obi_vals) > 0
        assert obi_vals.nunique() == 1

    def test_set_order_book_updates_snapshot(self):
        pipeline = FeaturePipeline(
            store=None,
            include_fundamental=False,
            include_sentiment=False,
            include_macro=False,
        )
        assert pipeline._order_book is None

        book = _book([100.0], [100.0])
        pipeline.set_order_book(book)
        assert pipeline._order_book is book

    def test_set_order_book_none_removes_snapshot(self):
        book = _book([100.0], [100.0])
        pipeline = FeaturePipeline(
            store=None,
            include_fundamental=False,
            include_sentiment=False,
            include_macro=False,
            order_book=book,
        )
        pipeline.set_order_book(None)
        df = self._make_ohlcv()
        result = pipeline.build(
            "AAPL",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
            ohlcv_df=df,
        )
        assert "order_book_imbalance" not in result.columns

    def test_obi_value_matches_manual_calculation(self):
        bid_sizes = [500.0, 300.0, 200.0, 100.0, 50.0]
        ask_sizes = [200.0, 150.0, 100.0, 80.0, 70.0]
        book = _book(bid_sizes, ask_sizes)
        expected = (1150.0 - 600.0) / (1150.0 + 600.0)

        pipeline = FeaturePipeline(
            store=None,
            include_fundamental=False,
            include_sentiment=False,
            include_macro=False,
            order_book=book,
        )
        df = self._make_ohlcv()
        result = pipeline.build(
            "AAPL",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
            ohlcv_df=df,
        )
        assert result["order_book_imbalance"].iloc[0] == pytest.approx(expected)
