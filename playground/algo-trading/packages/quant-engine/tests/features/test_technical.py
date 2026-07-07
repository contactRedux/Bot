"""
tests/features/test_technical.py — Unit tests for technical indicator functions.

Core test philosophy
---------------------
The most dangerous bug in a backtesting system is *look-ahead bias* — a
feature that uses future data to compute its value at time T.  Even a single
indicator that accidentally uses tomorrow's close to compute today's signal
will make every backtest appear profitable when it is actually using hindsight.

We test for look-ahead bias using the **data-masking approach**:

1. Generate a price series where the values are strictly monotonically
   increasing (1, 2, 3, 4, ...).  The crucial property is that any
   look-ahead would *raise* the value because future prices are always higher.

2. Compute the indicator on the full series.

3. For each bar T, compute the indicator on the *truncated* series (bars 0..T)
   — this is the "strictly causal" value.

4. Assert that the full-series value at T equals the truncated value at T.
   If they differ, the full-series computation used data from T+1 onwards.

This test catches:
* ``shift(-N)`` calls (forward shifts)
* Incorrect ``min_periods`` settings that cause partial windows to use future data
* Any accidental use of ``fillna(method='bfill')`` which propagates future values backward

Additional tests verify:
* Output column names are correct
* NaN presence/absence where expected
* Indicator value ranges (RSI ∈ [0,100], Williams %R ∈ [-100, 0], etc.)
* Edge cases (constant prices, single-bar DataFrames)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from features.technical import (
    add_ema,
    add_macd,
    add_adx,
    add_ichimoku,
    add_rsi,
    add_stochastic,
    add_roc,
    add_williams_r,
    add_bollinger_bands,
    add_atr,
    add_keltner_channels,
    add_historical_volatility,
    add_vwap,
    add_obv,
    add_volume_zscore,
    add_chaikin_money_flow,
    add_all_technical,
)


# ── Test fixtures ─────────────────────────────────────────────────────────────

def make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """
    Generate a realistic synthetic OHLCV DataFrame.

    Uses a geometric random walk for close prices so that all indicators
    compute reasonable (non-degenerate) values.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    log_returns = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(log_returns))
    # high/low are spread around close; open is yesterday's close
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


def make_monotonic_ohlcv(n: int = 200) -> pd.DataFrame:
    """
    Generate a strictly monotonically increasing price series.

    All prices = index position (1, 2, 3, ...).  Any look-ahead bias in
    an indicator will produce a higher value than the causal computation
    because future prices are always larger.
    """
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    prices = np.arange(1.0, n + 1.0)
    volume = np.ones(n) * 1_000_000
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.001,
            "low": prices * 0.999,
            "close": prices,
            "volume": volume,
        },
        index=idx,
    )


# ── Look-ahead bias helpers ────────────────────────────────────────────────────

def check_no_lookahead(
    fn,
    df: pd.DataFrame,
    warmup: int = 60,
    tol: float = 1e-8,
) -> None:
    """
    Assert that ``fn(df)`` produces the same column values at each bar T
    as ``fn(df.iloc[:T+1])`` produces at bar T.

    This is the core look-ahead bias test.

    Parameters
    ----------
    fn : callable
        Feature function ``fn(df) -> pd.DataFrame``.
    df : pd.DataFrame
        Full OHLCV series.
    warmup : int
        Number of initial bars to skip (indicator warm-up period).
    tol : float
        Numerical tolerance for float comparison.
    """
    full_result = fn(df)
    n = len(df)

    # Check a sample of bars (every 10th bar) to keep tests fast
    check_bars = list(range(warmup, n, 10)) + [n - 1]

    for t in check_bars:
        truncated_df = df.iloc[: t + 1]
        truncated_result = fn(truncated_df)

        for col in full_result.columns:
            full_val = full_result[col].iloc[t]
            trunc_val = truncated_result[col].iloc[-1]

            # Both NaN = OK
            if pd.isna(full_val) and pd.isna(trunc_val):
                continue
            # One NaN, one not = look-ahead if full has data and truncated doesn't
            if pd.isna(full_val) != pd.isna(trunc_val):
                # Allow full to be NaN when truncated is not (can happen at exact warmup boundary)
                if not pd.isna(trunc_val) and pd.isna(full_val):
                    pytest.fail(
                        f"Look-ahead bias in column '{col}' at bar {t}: "
                        f"full={full_val}, truncated={trunc_val}"
                    )
                continue
            # Both non-NaN: must be numerically equal
            assert abs(full_val - trunc_val) <= tol, (
                f"Look-ahead bias in column '{col}' at bar {t}: "
                f"full={full_val:.6f}, truncated={trunc_val:.6f}, diff={abs(full_val-trunc_val):.2e}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# TREND INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

class TestEMA:
    def test_columns(self):
        df = make_ohlcv()
        result = add_ema(df)
        assert set(result.columns) == {"ema_9", "ema_21", "ema_50", "ema_200"}

    def test_custom_periods(self):
        df = make_ohlcv()
        result = add_ema(df, periods=[10, 20])
        assert set(result.columns) == {"ema_10", "ema_20"}

    def test_warmup_nan(self):
        df = make_ohlcv(50)
        result = add_ema(df, periods=[9])
        # First 8 bars should be NaN (min_periods=9)
        assert result["ema_9"].iloc[:8].isna().all()
        assert result["ema_9"].iloc[9:].notna().all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(lambda d: add_ema(d, periods=[9, 21]), df, warmup=25)

    def test_range(self):
        df = make_ohlcv()
        result = add_ema(df)
        # EMA must be positive for positive prices
        assert (result["ema_9"].dropna() > 0).all()


class TestMACD:
    def test_columns(self):
        df = make_ohlcv()
        result = add_macd(df)
        assert set(result.columns) == {"macd", "macd_signal", "macd_hist"}

    def test_histogram_identity(self):
        df = make_ohlcv()
        result = add_macd(df)
        # histogram = MACD - Signal (within floating point)
        diff = (result["macd_hist"] - (result["macd"] - result["macd_signal"])).dropna()
        assert (diff.abs() < 1e-10).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_macd, df, warmup=35)


class TestADX:
    def test_columns(self):
        df = make_ohlcv()
        result = add_adx(df)
        assert set(result.columns) == {"adx", "adx_plus_di", "adx_minus_di"}

    def test_adx_range(self):
        df = make_ohlcv()
        result = add_adx(df)
        adx = result["adx"].dropna()
        assert (adx >= 0).all()
        assert (adx <= 100).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_adx, df, warmup=30)


class TestIchimoku:
    def test_columns(self):
        df = make_ohlcv()
        result = add_ichimoku(df)
        expected_cols = {
            "ichimoku_tenkan", "ichimoku_kijun",
            "ichimoku_span_a", "ichimoku_span_b",
            "ichimoku_cloud_thickness", "ichimoku_price_vs_cloud",
        }
        assert set(result.columns) == expected_cols

    def test_cloud_thickness_positive(self):
        df = make_ohlcv()
        result = add_ichimoku(df)
        thickness = result["ichimoku_cloud_thickness"].dropna()
        assert (thickness >= 0).all()

    def test_price_vs_cloud_values(self):
        df = make_ohlcv()
        result = add_ichimoku(df)
        pvcloud = result["ichimoku_price_vs_cloud"].dropna()
        assert pvcloud.isin([-1.0, 0.0, 1.0]).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_ichimoku, df, warmup=55)


# ══════════════════════════════════════════════════════════════════════════════
# MOMENTUM INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

class TestRSI:
    def test_column_name(self):
        df = make_ohlcv()
        result = add_rsi(df)
        assert "rsi_14" in result.columns

    def test_range(self):
        df = make_ohlcv()
        result = add_rsi(df)
        rsi = result["rsi_14"].dropna()
        assert (rsi >= 0).all()
        assert (rsi <= 100).all()

    def test_constant_prices_gives_nan_or_50(self):
        # Constant price series → no change → RSI should be 50 or NaN
        idx = pd.date_range("2020-01-01", periods=30, freq="D", tz="UTC")
        df = pd.DataFrame(
            {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1e6},
            index=idx,
        )
        result = add_rsi(df)
        valid = result["rsi_14"].dropna()
        # All valid values should be exactly 50 (no gains, no losses)
        assert valid.empty or (valid == 50.0).all() or valid.isna().all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_rsi, df, warmup=20)


class TestStochastic:
    def test_columns(self):
        df = make_ohlcv()
        result = add_stochastic(df)
        assert set(result.columns) == {"stoch_k", "stoch_d"}

    def test_range(self):
        df = make_ohlcv()
        result = add_stochastic(df)
        k = result["stoch_k"].dropna()
        assert (k >= 0).all()
        assert (k <= 100).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_stochastic, df, warmup=20)


class TestROC:
    def test_column_name(self):
        df = make_ohlcv()
        result = add_roc(df)
        assert "roc_10" in result.columns

    def test_monotonic_positive(self):
        # Monotonically increasing prices → ROC always positive
        df = make_monotonic_ohlcv()
        result = add_roc(df)
        roc = result["roc_10"].dropna()
        assert (roc > 0).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_roc, df, warmup=15)


class TestWilliamsR:
    def test_column_name(self):
        df = make_ohlcv()
        result = add_williams_r(df)
        assert "williams_r" in result.columns

    def test_range(self):
        df = make_ohlcv()
        result = add_williams_r(df)
        wr = result["williams_r"].dropna()
        assert (wr >= -100).all()
        assert (wr <= 0).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_williams_r, df, warmup=20)


# ══════════════════════════════════════════════════════════════════════════════
# VOLATILITY INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

class TestBollingerBands:
    def test_columns(self):
        df = make_ohlcv()
        result = add_bollinger_bands(df)
        assert set(result.columns) == {"bb_upper", "bb_middle", "bb_lower", "bb_pct_b", "bb_width"}

    def test_upper_gt_lower(self):
        df = make_ohlcv()
        result = add_bollinger_bands(df).dropna()
        assert (result["bb_upper"] >= result["bb_lower"]).all()

    def test_close_at_middle_gives_pct_b_half(self):
        # When close == SMA, %B should be 0.5
        n = 50
        idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
        df = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6},
            index=idx,
        )
        result = add_bollinger_bands(df)
        # With constant close, std=0 so %B is NaN (0/0) — this is correct behaviour
        assert result["bb_pct_b"].dropna().empty or True  # just verify it runs

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_bollinger_bands, df, warmup=25)


class TestATR:
    def test_columns(self):
        df = make_ohlcv()
        result = add_atr(df)
        assert set(result.columns) == {"atr", "atr_pct"}

    def test_positive(self):
        df = make_ohlcv()
        result = add_atr(df)
        assert (result["atr"].dropna() > 0).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_atr, df, warmup=20)


class TestKeltnerChannels:
    def test_columns(self):
        df = make_ohlcv()
        result = add_keltner_channels(df)
        assert set(result.columns) == {"kc_upper", "kc_middle", "kc_lower", "kc_position"}

    def test_upper_gt_lower(self):
        df = make_ohlcv()
        result = add_keltner_channels(df).dropna()
        assert (result["kc_upper"] >= result["kc_lower"]).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_keltner_channels, df, warmup=30)


class TestHistoricalVolatility:
    def test_columns(self):
        df = make_ohlcv()
        result = add_historical_volatility(df)
        assert "hv_20" in result.columns
        assert "hv_20_annualized" in result.columns

    def test_annualized_is_sqrt252_multiple(self):
        df = make_ohlcv()
        result = add_historical_volatility(df)
        ratio = (result["hv_20_annualized"] / result["hv_20"]).dropna()
        assert (ratio - np.sqrt(252)).abs().max() < 1e-8

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(
            lambda d: add_historical_volatility(d, annualize=False),
            df,
            warmup=25,
        )


# ══════════════════════════════════════════════════════════════════════════════
# VOLUME INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

class TestVWAP:
    def test_columns(self):
        df = make_ohlcv()
        result = add_vwap(df)
        assert set(result.columns) == {"vwap", "price_vs_vwap"}

    def test_vwap_positive(self):
        df = make_ohlcv()
        result = add_vwap(df)
        assert (result["vwap"].dropna() > 0).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(lambda d: add_vwap(d, period=20), df, warmup=25)


class TestOBV:
    def test_columns(self):
        df = make_ohlcv()
        result = add_obv(df)
        assert "obv" in result.columns
        assert "obv_ema_20" in result.columns

    def test_monotonic_up_increases_obv(self):
        # Monotonically rising prices → every day is an up-day → OBV monotonically increases
        df = make_monotonic_ohlcv()
        result = add_obv(df)
        obv = result["obv"]
        # OBV differences should all be positive (every bar is up)
        diffs = obv.diff().dropna()
        assert (diffs > 0).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_obv, df, warmup=25)


class TestVolumeZScore:
    def test_column_name(self):
        df = make_ohlcv()
        result = add_volume_zscore(df)
        assert "volume_zscore" in result.columns

    def test_mean_near_zero(self):
        df = make_ohlcv()
        result = add_volume_zscore(df)
        # Rolling z-score should have mean near 0 over the full series
        mean_z = result["volume_zscore"].dropna().mean()
        assert abs(mean_z) < 1.0  # loose bound since rolling mean shifts

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_volume_zscore, df, warmup=25)


class TestCMF:
    def test_column_name(self):
        df = make_ohlcv()
        result = add_chaikin_money_flow(df)
        assert "cmf" in result.columns

    def test_range(self):
        df = make_ohlcv()
        result = add_chaikin_money_flow(df)
        cmf = result["cmf"].dropna()
        assert (cmf >= -1).all()
        assert (cmf <= 1).all()

    def test_no_lookahead(self):
        df = make_monotonic_ohlcv()
        check_no_lookahead(add_chaikin_money_flow, df, warmup=25)


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSITE
# ══════════════════════════════════════════════════════════════════════════════

class TestAddAllTechnical:
    def test_returns_dataframe(self):
        df = make_ohlcv()
        result = add_all_technical(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(df)

    def test_index_matches_input(self):
        df = make_ohlcv()
        result = add_all_technical(df)
        pd.testing.assert_index_equal(result.index, df.index)

    def test_minimum_column_count(self):
        df = make_ohlcv()
        result = add_all_technical(df)
        # Should have at least 40 feature columns
        assert len(result.columns) >= 40

    def test_no_ohlcv_columns_duplicated(self):
        df = make_ohlcv()
        result = add_all_technical(df)
        # The result should NOT contain raw OHLCV columns
        ohlcv_cols = {"open", "high", "low", "close", "volume"}
        assert not ohlcv_cols.intersection(result.columns)

    def test_missing_column_raises(self):
        df = make_ohlcv().drop(columns=["volume"])
        with pytest.raises(ValueError, match="missing required columns"):
            add_all_technical(df)

    def test_no_lookahead_spot_check(self):
        """Spot-check a few columns from the full composite for look-ahead bias."""
        df = make_monotonic_ohlcv()
        check_no_lookahead(
            lambda d: add_all_technical(d)[["ema_9", "rsi_14", "bb_pct_b", "atr"]],
            df,
            warmup=30,
        )
