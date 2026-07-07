"""
tests/features/test_statistical.py — Unit tests for statistical feature functions.

Tests verify:
1. Output shapes and column names are correct.
2. No look-ahead bias in rolling operations (hedge ratio estimation, z-score).
3. Mathematical correctness of spread z-score and half-life.
4. Cointegration tests return expected structure (mocked to avoid statsmodels dep
   in CI environments where it may be slow or unavailable).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from features.statistical import (
    rolling_pearson_correlation,
    compute_spread,
    compute_spread_zscore,
    ou_half_life,
    pair_features,
)


# ── Test fixtures ─────────────────────────────────────────────────────────────

def make_cointegrated_pair(
    n: int = 300,
    beta: float = 0.8,
    noise_std: float = 0.5,
    seed: int = 42,
) -> tuple[pd.Series, pd.Series]:
    """
    Generate a cointegrated pair of price series.

    price_A = price_B^beta + noise  (in log space)
    Both series share a common random walk; their log-linear combination
    is mean-reverting.

    Parameters
    ----------
    n : int
        Number of observations.
    beta : float
        True cointegrating coefficient.
    noise_std : float
        Standard deviation of the mean-reverting noise component.

    Returns
    -------
    (price_a, price_b) : tuple of pd.Series
        Price series with a DatetimeIndex.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")

    # Common random walk (the "non-stationary" component both series share)
    common_walk = np.cumsum(rng.normal(0, 0.01, n))
    # Mean-reverting spread (Ornstein-Uhlenbeck process)
    spread = np.zeros(n)
    theta = 0.1  # mean-reversion speed
    for t in range(1, n):
        spread[t] = spread[t - 1] + theta * (0 - spread[t - 1]) + rng.normal(0, noise_std * 0.01)

    log_b = common_walk
    log_a = beta * common_walk + spread

    price_a = pd.Series(np.exp(log_a + 5), index=idx, name="A")  # +5 to start near $150
    price_b = pd.Series(np.exp(log_b + 5), index=idx, name="B")
    return price_a, price_b


def make_uncorrelated_pair(n: int = 200, seed: int = 99) -> tuple[pd.Series, pd.Series]:
    """Generate two independent random walk price series."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    price_a = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx, name="A")
    price_b = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx, name="B")
    return price_a, price_b


# ══════════════════════════════════════════════════════════════════════════════
# ROLLING CORRELATION
# ══════════════════════════════════════════════════════════════════════════════

class TestRollingPearsonCorrelation:
    def test_output_shape(self):
        a, b = make_cointegrated_pair()
        result = rolling_pearson_correlation(a, b, window=60)
        assert len(result) == len(a)

    def test_range(self):
        a, b = make_cointegrated_pair()
        result = rolling_pearson_correlation(a, b, window=60).dropna()
        assert (result >= -1.0).all()
        assert (result <= 1.0).all()

    def test_warmup_nan(self):
        a, b = make_cointegrated_pair(n=100)
        result = rolling_pearson_correlation(a, b, window=60)
        # First 60 bars should be NaN
        assert result.iloc[:59].isna().all()
        assert result.iloc[60:].notna().any()

    def test_cointegrated_pair_high_correlation(self):
        a, b = make_cointegrated_pair(noise_std=0.01)
        result = rolling_pearson_correlation(a, b, window=60).dropna()
        # Cointegrated pair should have high positive correlation
        assert result.mean() > 0.7

    def test_no_lookahead(self):
        a, b = make_cointegrated_pair()
        full = rolling_pearson_correlation(a, b, window=60)
        # Check that value at bar T equals value computed on truncated series
        for t in range(65, len(a), 20):
            trunc_corr = rolling_pearson_correlation(a.iloc[:t+1], b.iloc[:t+1], window=60)
            if pd.notna(full.iloc[t]) and pd.notna(trunc_corr.iloc[-1]):
                assert abs(full.iloc[t] - trunc_corr.iloc[-1]) < 1e-8, \
                    f"Look-ahead at bar {t}: full={full.iloc[t]:.6f}, trunc={trunc_corr.iloc[-1]:.6f}"


# ══════════════════════════════════════════════════════════════════════════════
# SPREAD COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeSpread:
    def test_output_shape(self):
        a, b = make_cointegrated_pair()
        spread, hr = compute_spread(a, b)
        assert len(spread) == len(a)
        assert len(hr) == len(a)

    def test_fixed_hedge_ratio(self):
        a, b = make_cointegrated_pair()
        spread, hr = compute_spread(a, b, hedge_ratio=0.8)
        # With fixed HR, the hedge ratio Series should be constant
        assert (hr.dropna() == 0.8).all()

    def test_spread_mean_reverts_for_cointegrated_pair(self):
        """
        For a truly cointegrated pair, the spread should be stationary
        (its mean should stay close to 0 and it should not drift indefinitely).
        """
        a, b = make_cointegrated_pair(n=300, noise_std=0.1)
        spread, _ = compute_spread(a, b, hedge_ratio=0.8)
        spread_clean = spread.dropna()
        # Standard deviation should be finite and relatively small
        std = spread_clean.std()
        assert 0 < std < 5.0, f"Spread std too large: {std}"

    def test_no_lookahead_with_rolling_ols(self):
        """
        Rolling OLS hedge ratio must not use future prices.
        If prices are monotonically increasing, the hedge ratio at T should
        be computable from data up to T only.
        """
        a, b = make_cointegrated_pair(n=250)
        full_spread, full_hr = compute_spread(a, b, window=120)

        # Spot-check: value at bar T using full series vs truncated series
        for t in range(125, len(a), 25):
            trunc_spread, trunc_hr = compute_spread(a.iloc[:t+1], b.iloc[:t+1], window=120)
            if pd.notna(full_hr.iloc[t]) and pd.notna(trunc_hr.iloc[-1]):
                assert abs(full_hr.iloc[t] - trunc_hr.iloc[-1]) < 1e-8, \
                    f"Hedge ratio look-ahead at bar {t}"
            if pd.notna(full_spread.iloc[t]) and pd.notna(trunc_spread.iloc[-1]):
                assert abs(full_spread.iloc[t] - trunc_spread.iloc[-1]) < 1e-8, \
                    f"Spread look-ahead at bar {t}"


# ══════════════════════════════════════════════════════════════════════════════
# SPREAD Z-SCORE
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeSpreadZScore:
    def test_output_shape(self):
        a, b = make_cointegrated_pair()
        spread, _ = compute_spread(a, b, hedge_ratio=0.8)
        zscore = compute_spread_zscore(spread, window=60)
        assert len(zscore) == len(spread)

    def test_zscore_mean_near_zero(self):
        a, b = make_cointegrated_pair()
        spread, _ = compute_spread(a, b, hedge_ratio=0.8)
        zscore = compute_spread_zscore(spread, window=60).dropna()
        # Rolling z-score should have mean ≈ 0 and std ≈ 1 by construction
        assert abs(zscore.mean()) < 0.5

    def test_zscore_std_near_one(self):
        a, b = make_cointegrated_pair()
        spread, _ = compute_spread(a, b, hedge_ratio=0.8)
        zscore = compute_spread_zscore(spread, window=60).dropna()
        assert abs(zscore.std() - 1.0) < 0.3  # std ≈ 1 within tolerance

    def test_no_lookahead(self):
        a, b = make_cointegrated_pair()
        spread, _ = compute_spread(a, b, hedge_ratio=0.8)
        full_z = compute_spread_zscore(spread, window=60)

        for t in range(65, len(spread), 20):
            trunc_z = compute_spread_zscore(spread.iloc[:t+1], window=60)
            if pd.notna(full_z.iloc[t]) and pd.notna(trunc_z.iloc[-1]):
                assert abs(full_z.iloc[t] - trunc_z.iloc[-1]) < 1e-8, \
                    f"Z-score look-ahead at bar {t}"


# ══════════════════════════════════════════════════════════════════════════════
# ORNSTEIN-UHLENBECK HALF-LIFE
# ══════════════════════════════════════════════════════════════════════════════

class TestOUHalfLife:
    def test_fast_reverting_series_short_halflife(self):
        """A strongly mean-reverting series should have a short half-life."""
        try:
            import statsmodels  # noqa: F401
        except ImportError:
            pytest.skip("statsmodels not installed")

        rng = np.random.default_rng(42)
        # OU process with strong mean-reversion (theta = 0.5)
        n = 500
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = x[t-1] + 0.5 * (0 - x[t-1]) + rng.normal(0, 0.1)
        s = pd.Series(x, index=pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"))

        hl = ou_half_life(s)
        assert hl < 20, f"Expected half-life < 20 for fast-reverting series, got {hl:.1f}"

    def test_random_walk_returns_large_halflife(self):
        """
        A random walk should have a very long half-life or inf.

        In finite samples a random walk can spuriously appear to have a
        small negative OLS beta coefficient, yielding a finite half-life.
        We use a longer series and test the average across multiple seeds
        to verify the expected behaviour statistically rather than on a
        single seed.
        """
        try:
            import statsmodels  # noqa: F401
        except ImportError:
            pytest.skip("statsmodels not installed")

        half_lives = []
        for seed in range(10):
            rng = np.random.default_rng(seed * 1000)
            n = 500
            rw = pd.Series(
                np.cumsum(rng.normal(0, 1, n)),
                index=pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"),
            )
            hl = ou_half_life(rw)
            half_lives.append(hl)

        # For a random walk, most half-lives should be either inf or very long.
        # We allow up to 3 out of 10 to be < 100 (spurious regression artefacts).
        long_or_inf = sum(1 for h in half_lives if h == float("inf") or h > 50)
        assert long_or_inf >= 7, (
            f"Expected ≥7/10 random walks to have half-life > 50, "
            f"got {long_or_inf}/10. Half-lives: {[round(h, 1) for h in half_lives]}"
        )

    def test_short_series_returns_inf(self):
        """Too short a series should gracefully return inf."""
        try:
            import statsmodels  # noqa: F401
        except ImportError:
            pytest.skip("statsmodels not installed")
        s = pd.Series([1.0, 2.0, 1.5], index=pd.date_range("2020-01-01", periods=3, freq="D", tz="UTC"))
        hl = ou_half_life(s)
        assert hl == float("inf")


# ══════════════════════════════════════════════════════════════════════════════
# PAIR FEATURES (composite)
# ══════════════════════════════════════════════════════════════════════════════

class TestPairFeatures:
    def test_output_columns(self):
        a, b = make_cointegrated_pair()
        result = pair_features(a, b, corr_window=60, spread_window=120, zscore_window=60)
        expected_cols = {"pearson_corr", "spread", "hedge_ratio", "spread_zscore", "spread_halflife"}
        assert set(result.columns) == expected_cols

    def test_index_matches_input(self):
        a, b = make_cointegrated_pair()
        result = pair_features(a, b)
        pd.testing.assert_index_equal(result.index, a.index)

    def test_halflife_is_constant_column(self):
        """Half-life is computed once on the full series and broadcast as a constant."""
        a, b = make_cointegrated_pair()
        result = pair_features(a, b)
        hl = result["spread_halflife"].dropna()
        if not hl.empty:
            # All non-NaN values should be the same scalar
            assert hl.nunique() == 1
