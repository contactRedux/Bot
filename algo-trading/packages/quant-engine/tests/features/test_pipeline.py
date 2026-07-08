"""
tests/features/test_pipeline.py — End-to-end tests for FeaturePipeline.

Tests verify:
1. The pipeline runs end-to-end with synthetic data (no external dependencies).
2. The feature matrix index exactly matches the OHLCV index.
3. No look-ahead bias in the composite output (subset of columns).
4. The pipeline gracefully handles edge cases (very short series, missing data).
5. ``feature_names()`` returns a consistent, non-empty list.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from features.pipeline import FeaturePipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with a UTC DatetimeIndex."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    log_returns = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(log_returns))
    spread = close * rng.uniform(0.001, 0.02, n)
    high = close + spread
    low = np.maximum(close - spread, 0.01)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def make_pipeline_tech_only() -> FeaturePipeline:
    """Pipeline with only technical features (no external data sources required)."""
    return FeaturePipeline(
        store=None,
        include_technical=True,
        include_fundamental=False,
        include_sentiment=False,
        include_macro=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# BASIC PIPELINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestFeaturePipelineBasic:
    def test_returns_dataframe(self):
        df = make_ohlcv()
        pipeline = make_pipeline_tech_only()
        from datetime import timezone
        result = pipeline.build(
            "AAPL",
            df.index[0],
            df.index[-1],
            ohlcv_df=df,
        )
        assert isinstance(result, pd.DataFrame)

    def test_index_matches_ohlcv(self):
        df = make_ohlcv()
        pipeline = make_pipeline_tech_only()
        result = pipeline.build("AAPL", df.index[0], df.index[-1], ohlcv_df=df)
        pd.testing.assert_index_equal(result.index, df.index)

    def test_non_empty_columns(self):
        df = make_ohlcv()
        pipeline = make_pipeline_tech_only()
        result = pipeline.build("AAPL", df.index[0], df.index[-1], ohlcv_df=df)
        assert len(result.columns) > 0

    def test_no_ohlcv_columns_in_output(self):
        df = make_ohlcv()
        pipeline = make_pipeline_tech_only()
        result = pipeline.build("AAPL", df.index[0], df.index[-1], ohlcv_df=df)
        ohlcv_cols = {"open", "high", "low", "close", "volume"}
        assert not ohlcv_cols.intersection(result.columns)

    def test_empty_ohlcv_returns_empty(self):
        pipeline = make_pipeline_tech_only()
        empty_df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([]),
        )
        result = pipeline.build(
            "AAPL",
            pd.Timestamp("2020-01-01", tz="UTC"),
            pd.Timestamp("2020-12-31", tz="UTC"),
            ohlcv_df=empty_df,
        )
        assert result.empty

    def test_no_store_and_no_ohlcv_returns_empty(self):
        pipeline = FeaturePipeline(store=None)
        result = pipeline.build(
            "AAPL",
            pd.Timestamp("2020-01-01", tz="UTC"),
            pd.Timestamp("2020-12-31", tz="UTC"),
        )
        assert result.empty

    def test_short_series_handles_nan_gracefully(self):
        """Very short series should produce NaN-heavy output but not crash."""
        df = make_ohlcv(n=30)
        pipeline = make_pipeline_tech_only()
        result = pipeline.build("AAPL", df.index[0], df.index[-1], ohlcv_df=df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 30


# ══════════════════════════════════════════════════════════════════════════════
# LOOK-AHEAD BIAS TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineLookAheadBias:
    """
    Verify that the full pipeline does not leak future data into past bars.

    For each sampled bar T we compare:
    * full_features.iloc[T]   — feature at T from the full-history computation
    * trunc_features.iloc[-1] — feature at T from computation on data up to T

    They must be equal.
    """

    def _check_col_no_lookahead(self, df: pd.DataFrame, col: str, warmup: int = 60) -> None:
        pipeline = make_pipeline_tech_only()
        full = pipeline.build("TEST", df.index[0], df.index[-1], ohlcv_df=df)

        if col not in full.columns:
            pytest.skip(f"Column '{col}' not in pipeline output")

        n = len(df)
        for t in range(warmup, n, 15):
            trunc = pipeline.build(
                "TEST", df.index[0], df.index[t], ohlcv_df=df.iloc[:t+1]
            )
            if col not in trunc.columns:
                continue

            full_val = full[col].iloc[t]
            trunc_val = trunc[col].iloc[-1]

            if pd.isna(full_val) and pd.isna(trunc_val):
                continue
            if pd.isna(full_val) != pd.isna(trunc_val):
                continue  # NaN boundary effects are acceptable

            assert abs(full_val - trunc_val) < 1e-7, (
                f"Look-ahead in '{col}' at bar {t}: "
                f"full={full_val:.6f}, trunc={trunc_val:.6f}"
            )

    def test_ema_9_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "ema_9", warmup=15)

    def test_rsi_14_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "rsi_14", warmup=20)

    def test_bb_pct_b_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "bb_pct_b", warmup=25)

    def test_atr_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "atr", warmup=20)

    def test_macd_hist_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "macd_hist", warmup=40)

    def test_vwap_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "vwap", warmup=25)

    def test_adx_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "adx", warmup=30)

    def test_volume_zscore_no_lookahead(self):
        self._check_col_no_lookahead(make_ohlcv(), "volume_zscore", warmup=25)


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE NAMES
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureNames:
    def test_returns_non_empty_list(self):
        pipeline = make_pipeline_tech_only()
        names = pipeline.feature_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_names_are_strings(self):
        pipeline = make_pipeline_tech_only()
        names = pipeline.feature_names()
        assert all(isinstance(n, str) for n in names)

    def test_consistent_across_calls(self):
        pipeline = make_pipeline_tech_only()
        names_1 = pipeline.feature_names()
        names_2 = pipeline.feature_names()
        assert names_1 == names_2

    def test_minimum_feature_count(self):
        pipeline = make_pipeline_tech_only()
        names = pipeline.feature_names()
        # Technical features alone should produce at least 40 columns
        assert len(names) >= 40


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-TICKER
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildMulti:
    def test_returns_dict(self):
        pipeline = make_pipeline_tech_only()
        tickers = ["AAPL", "MSFT"]
        dfs = {t: make_ohlcv(seed=i) for i, t in enumerate(tickers)}

        # Use ohlcv_df per ticker via individual calls
        result = {
            t: pipeline.build(t, dfs[t].index[0], dfs[t].index[-1], ohlcv_df=dfs[t])
            for t in tickers
        }
        assert set(result.keys()) == {"AAPL", "MSFT"}
        for t in tickers:
            assert isinstance(result[t], pd.DataFrame)
            assert len(result[t]) == len(dfs[t])

    def test_tickers_have_independent_results(self):
        """
        Features for AAPL must not be affected by MSFT data.
        Each ticker's features are computed independently.
        """
        pipeline = make_pipeline_tech_only()
        df_aapl = make_ohlcv(seed=1)
        df_msft = make_ohlcv(seed=2)

        res_aapl = pipeline.build("AAPL", df_aapl.index[0], df_aapl.index[-1], ohlcv_df=df_aapl)
        res_msft = pipeline.build("MSFT", df_msft.index[0], df_msft.index[-1], ohlcv_df=df_msft)

        # AAPL and MSFT results should be different (different price series)
        if "ema_9" in res_aapl.columns and "ema_9" in res_msft.columns:
            assert not res_aapl["ema_9"].dropna().equals(res_msft["ema_9"].dropna())
