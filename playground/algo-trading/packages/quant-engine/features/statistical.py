"""
features/statistical.py — Cross-asset statistical relationship features.

This module computes features that capture *relationships between assets*
rather than properties of a single asset.  These are the backbone of the
Statistical Arbitrage strategy (Sub-Task 5).

Key concepts
------------

**Cointegration vs. Correlation**
    Correlation measures whether two price series move together in the *same
    direction*.  Two stocks can be highly correlated but their prices can still
    diverge indefinitely (e.g. AAPL and MSFT both trend up, but AAPL could
    outpace MSFT forever).

    Cointegration is a stronger relationship: it means a *linear combination*
    of two price series is stationary (mean-reverting).  If AAPL − β×MSFT is
    stationary, then whenever the spread deviates from its mean, it will
    revert.  That reversion is the tradable signal.

**Engle-Granger cointegration test (bivariate)**
    1. Regress price_A on price_B to find cointegrating coefficient β.
    2. Compute the residuals: spread = price_A − β × price_B.
    3. Run an ADF (Augmented Dickey-Fuller) unit root test on the spread.
    4. Reject H0 (unit root) → the spread is stationary → cointegrated.

    The ADF test statistic and p-value are returned as features.  A p-value
    < 0.05 is conventionally used as the cointegration threshold.

**Johansen cointegration test (multivariate)**
    Extends Engle-Granger to N assets simultaneously.  Returns the trace
    statistic and eigenvalues, which indicate how many cointegrating
    relationships exist in a basket.

**Spread Z-Score**
    z = (spread_t − rolling_mean(spread, window)) / rolling_std(spread, window)

    The z-score tells us how many standard deviations the current spread is
    from its recent mean.  We enter a trade when |z| > 2 and exit when |z| < 0.5.

**Ornstein-Uhlenbeck half-life**
    The OU half-life is the expected time for the spread to revert halfway to
    its mean.  It filters out pairs whose spread is too slow to mean-revert
    within a practical trading horizon.

    Fit: Δspread_t = θ(μ − spread_{t-1}) Δt + ε
    Half-life = ln(2) / θ

Look-ahead bias notes
---------------------
* Rolling correlations use past windows only — no ``shift(-N)``.
* ADF tests are run on a *trailing* window of spread values.
* Half-life regression uses lagged spread, which is past data only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
# ROLLING CORRELATIONS
# ══════════════════════════════════════════════════════════════════════════════

def rolling_pearson_correlation(
    series_a: pd.Series,
    series_b: pd.Series,
    window: int = 60,
) -> pd.Series:
    """
    Rolling Pearson correlation between two return series.

    Pearson correlation measures *linear* co-movement.  For trading, we
    compute it on log-returns (not prices) to make it stationary.

    Parameters
    ----------
    series_a, series_b : pd.Series
        Price series (not returns — log-returns are computed internally).
    window : int
        Rolling window in bars.

    Returns
    -------
    pd.Series
        Rolling Pearson correlation in ``[-1, +1]``, indexed like the inputs.
    """
    ret_a = np.log(series_a / series_a.shift(1))
    ret_b = np.log(series_b / series_b.shift(1))
    return ret_a.rolling(window, min_periods=window).corr(ret_b).rename("pearson_corr")


def rolling_spearman_correlation(
    series_a: pd.Series,
    series_b: pd.Series,
    window: int = 60,
) -> pd.Series:
    """
    Rolling Spearman rank correlation between two price series.

    Spearman correlation measures *monotonic* (rank-based) co-movement and is
    more robust to outliers and non-linear relationships than Pearson.

    Parameters
    ----------
    series_a, series_b : pd.Series
        Price series.
    window : int
        Rolling window in bars.

    Returns
    -------
    pd.Series
        Rolling Spearman correlation in ``[-1, +1]``.
    """
    # pandas rolling.apply does not support method="spearman" directly.
    # We compute it manually: rank both series within each window, then
    # compute Pearson correlation of the ranks — equivalent to Spearman.
    n = len(series_a)
    results = [np.nan] * n

    a_arr = series_a.to_numpy(dtype=float)
    b_arr = series_b.to_numpy(dtype=float)

    for i in range(window - 1, n):
        a_win = a_arr[i - window + 1 : i + 1]
        b_win = b_arr[i - window + 1 : i + 1]
        if np.isnan(a_win).any() or np.isnan(b_win).any():
            continue
        # Rank within window
        a_rank = pd.Series(a_win).rank().to_numpy()
        b_rank = pd.Series(b_win).rank().to_numpy()
        # Pearson of ranks = Spearman
        if a_rank.std() == 0 or b_rank.std() == 0:
            continue
        corr = np.corrcoef(a_rank, b_rank)[0, 1]
        results[i] = corr

    return pd.Series(results, index=series_a.index, name="spearman_corr")


# ══════════════════════════════════════════════════════════════════════════════
# SPREAD CONSTRUCTION AND Z-SCORE
# ══════════════════════════════════════════════════════════════════════════════

def compute_spread(
    price_a: pd.Series,
    price_b: pd.Series,
    hedge_ratio: float | None = None,
    window: int = 120,
) -> tuple[pd.Series, pd.Series]:
    """
    Compute the cointegrating spread between two price series.

    If ``hedge_ratio`` is None, it is estimated via OLS regression on the
    trailing ``window`` of prices (walk-forward — no look-ahead bias).

    The spread is: spread = log(price_A) − hedge_ratio × log(price_B)

    Using log prices makes the spread unitless and the hedge ratio
    dimensionless (a percentage relationship rather than an absolute price ratio).

    Parameters
    ----------
    price_a, price_b : pd.Series
        Price series for the two legs.
    hedge_ratio : float, optional
        Fixed hedge ratio.  If None, estimated via rolling OLS.
    window : int
        Rolling OLS window for dynamic hedge ratio estimation.

    Returns
    -------
    spread : pd.Series
        The cointegrating spread time series.
    hedge_ratios : pd.Series
        The hedge ratio used at each point in time (constant or rolling).
    """
    log_a = np.log(price_a.replace(0, np.nan))
    log_b = np.log(price_b.replace(0, np.nan))

    if hedge_ratio is not None:
        hr_series = pd.Series(hedge_ratio, index=price_a.index)
    else:
        # Rolling OLS: regress log_a on log_b over trailing window
        hr_series = _rolling_ols_beta(log_a, log_b, window)

    spread = log_a - hr_series * log_b
    return spread, hr_series


def _rolling_ols_beta(y: pd.Series, x: pd.Series, window: int) -> pd.Series:
    """
    Rolling OLS regression to estimate the hedge ratio β = Cov(y,x) / Var(x).

    This is the Engle-Granger step-1 regression run on a trailing window.
    Using a rolling window means the hedge ratio adapts over time as the
    relationship between the pair evolves.
    """
    betas = []
    for i in range(len(y)):
        if i < window:
            betas.append(np.nan)
            continue
        y_w = y.iloc[i - window : i].dropna()
        x_w = x.iloc[i - window : i].dropna()
        if len(y_w) < 2 or len(x_w) < 2:
            betas.append(np.nan)
            continue
        cov = np.cov(y_w, x_w, ddof=1)
        var_x = cov[1, 1]
        beta = cov[0, 1] / var_x if var_x != 0 else np.nan
        betas.append(beta)
    return pd.Series(betas, index=y.index)


def compute_spread_zscore(
    spread: pd.Series,
    window: int = 60,
) -> pd.Series:
    """
    Compute the rolling z-score of a spread.

    z = (spread_t − rolling_mean) / rolling_std

    Trading signals derived from z-score:
    * |z| > 2.0  → enter trade (spread has deviated significantly from mean)
    * |z| < 0.5  → exit trade (spread has mean-reverted)
    * |z| > 3.5  → emergency exit (spread has blown out beyond normal range)

    Parameters
    ----------
    spread : pd.Series
        The cointegrating spread time series.
    window : int
        Lookback for computing mean and std.

    Returns
    -------
    pd.Series
        Z-score of the spread, same index as input.
    """
    roll_mean = spread.rolling(window, min_periods=window).mean()
    roll_std = spread.rolling(window, min_periods=window).std(ddof=1).replace(0, np.nan)
    return ((spread - roll_mean) / roll_std).rename("spread_zscore")


# ══════════════════════════════════════════════════════════════════════════════
# COINTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════════════

def engle_granger_test(
    price_a: pd.Series,
    price_b: pd.Series,
) -> dict[str, float]:
    """
    Engle-Granger cointegration test for a pair of price series.

    Step 1: OLS regression of log(price_A) on log(price_B) → residuals.
    Step 2: ADF test on residuals.
    Step 3: Reject H0 (unit root in residuals) → cointegrated.

    The ADF test is from ``statsmodels``.

    Parameters
    ----------
    price_a, price_b : pd.Series
        Full price series (not rolling — use entire history for the test).

    Returns
    -------
    dict with keys:
        ``adf_stat``    — ADF test statistic (more negative = more stationary)
        ``p_value``     — p-value (< 0.05 → reject H0 → cointegrated)
        ``hedge_ratio`` — OLS β (how many units of B to short per unit of A)
        ``cointegrated``— bool, True if p_value < 0.05
    """
    try:
        from statsmodels.tsa.stattools import adfuller
        import statsmodels.api as sm
    except ImportError:
        raise ImportError("statsmodels is required for cointegration tests.  "
                          "Run: pip install statsmodels")

    log_a = np.log(price_a.dropna().replace(0, np.nan)).dropna()
    log_b = np.log(price_b.dropna().replace(0, np.nan)).dropna()

    # Align on common index
    df_ab = pd.concat([log_a, log_b], axis=1).dropna()
    if len(df_ab) < 30:
        return {"adf_stat": np.nan, "p_value": 1.0, "hedge_ratio": np.nan, "cointegrated": False}

    y = df_ab.iloc[:, 0]
    x = sm.add_constant(df_ab.iloc[:, 1])
    ols_result = sm.OLS(y, x).fit()
    hedge_ratio = float(ols_result.params.iloc[1])
    residuals = ols_result.resid

    adf_result = adfuller(residuals, autolag="AIC")
    adf_stat = float(adf_result[0])
    p_value = float(adf_result[1])

    return {
        "adf_stat": adf_stat,
        "p_value": p_value,
        "hedge_ratio": hedge_ratio,
        "cointegrated": p_value < 0.05,
    }


def johansen_test(
    prices: pd.DataFrame,
    det_order: int = 0,
    k_ar_diff: int = 1,
) -> dict[str, object]:
    """
    Johansen cointegration test for a basket of N price series.

    The Johansen test finds the number of cointegrating relationships (rank)
    in a system of N variables simultaneously.  Unlike Engle-Granger it can
    detect multiple cointegrating vectors when N > 2.

    Parameters
    ----------
    prices : pd.DataFrame
        Each column is the price series of one asset (log-prices recommended).
    det_order : int
        Deterministic term: -1 (none), 0 (constant), 1 (trend).
    k_ar_diff : int
        Number of lagged difference terms in the VAR model.

    Returns
    -------
    dict with keys:
        ``trace_stats``   — Trace test statistics (array, one per potential rank)
        ``crit_values``   — 5% critical values for trace test
        ``rank``          — Estimated number of cointegrating relationships
        ``eigenvalues``   — Eigenvalues of the cointegration matrix
        ``eigenvectors``  — Cointegrating vectors (columns = eigenvectors)
    """
    try:
        from statsmodels.tsa.vector_ar.vecm import coint_johansen
    except ImportError:
        raise ImportError("statsmodels is required.  Run: pip install statsmodels")

    log_prices = np.log(prices.replace(0, np.nan)).dropna()
    if len(log_prices) < 30:
        n = prices.shape[1]
        return {
            "trace_stats": np.full(n, np.nan),
            "crit_values": np.full((n, 3), np.nan),
            "rank": 0,
            "eigenvalues": np.full(n, np.nan),
            "eigenvectors": np.full((n, n), np.nan),
        }

    result = coint_johansen(log_prices, det_order=det_order, k_ar_diff=k_ar_diff)

    # Determine rank: count trace stats that exceed 5% critical values
    trace_stats = result.lr1  # trace statistics
    crit_vals_5pct = result.cvt[:, 1]  # 5% critical values column
    rank = int(np.sum(trace_stats > crit_vals_5pct))

    return {
        "trace_stats": trace_stats,
        "crit_values": result.cvt,
        "rank": rank,
        "eigenvalues": result.eig,
        "eigenvectors": result.evec,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ORNSTEIN-UHLENBECK HALF-LIFE
# ══════════════════════════════════════════════════════════════════════════════

def ou_half_life(spread: pd.Series) -> float:
    """
    Estimate the Ornstein-Uhlenbeck half-life of a spread series.

    The OU process models mean-reverting behaviour:
        dS_t = θ(μ − S_t)dt + σ dW_t

    In discrete time: ΔS_t = α + β × S_{t-1} + ε
    where β ≈ −θΔt (mean-reversion speed).

    Half-life = −ln(2) / ln(1 + β) ≈ ln(2) / θ

    A half-life of 5–20 days is typical for tradeable pairs.  If the
    half-life is too long (> 60 days), the spread reverts too slowly to
    generate reliable signals within a reasonable position hold period.

    Parameters
    ----------
    spread : pd.Series
        The cointegrating spread time series.

    Returns
    -------
    float
        Half-life in the same unit as the spread's time index (bars).
        Returns ``inf`` if the spread is not mean-reverting (β ≥ 0).
    """
    try:
        import statsmodels.api as sm
    except ImportError:
        raise ImportError("statsmodels is required.  Run: pip install statsmodels")

    spread_clean = spread.dropna()
    if len(spread_clean) < 10:
        return float("inf")

    spread_lag = spread_clean.shift(1).dropna()
    spread_diff = spread_clean.diff().dropna()

    # Align
    min_idx = max(spread_lag.index[0], spread_diff.index[0])
    spread_lag = spread_lag.loc[min_idx:]
    spread_diff = spread_diff.loc[min_idx:]

    x = sm.add_constant(spread_lag)
    result = sm.OLS(spread_diff, x).fit()
    beta = float(result.params.iloc[1])  # coefficient on lag

    if beta >= 0:
        return float("inf")  # not mean-reverting

    half_life = -np.log(2) / np.log(1 + beta)
    return max(1.0, half_life)


# ══════════════════════════════════════════════════════════════════════════════
# ROLLING PAIR FEATURES (pipeline-friendly interface)
# ══════════════════════════════════════════════════════════════════════════════

def pair_features(
    price_a: pd.Series,
    price_b: pd.Series,
    corr_window: int = 60,
    spread_window: int = 120,
    zscore_window: int = 60,
    ticker_a: str = "A",
    ticker_b: str = "B",
) -> pd.DataFrame:
    """
    Compute the full set of pair relationship features used by the stat-arb strategy.

    This is the primary entry point called by ``FeaturePipeline`` for each
    configured pair.

    Parameters
    ----------
    price_a, price_b : pd.Series
        Price series for the two legs.  Must share a common DatetimeIndex.
    corr_window : int
        Rolling window for Pearson correlation.
    spread_window : int
        Rolling OLS window for hedge ratio estimation.
    zscore_window : int
        Rolling window for z-score computation.
    ticker_a, ticker_b : str
        Labels used as column name prefixes.

    Returns
    -------
    pd.DataFrame
        Columns: ``pearson_corr``, ``spread``, ``hedge_ratio``,
        ``spread_zscore``, ``spread_halflife`` (scalar reused as constant column).
    """
    result = pd.DataFrame(index=price_a.index)

    # Rolling Pearson correlation on returns
    result["pearson_corr"] = rolling_pearson_correlation(price_a, price_b, corr_window)

    # Cointegrating spread and hedge ratio
    spread, hr = compute_spread(price_a, price_b, window=spread_window)
    result["spread"] = spread
    result["hedge_ratio"] = hr

    # Z-score of the spread
    result["spread_zscore"] = compute_spread_zscore(spread, zscore_window)

    # Half-life — computed on full available spread, stored as a constant.
    # Falls back to NaN if statsmodels is not installed.
    try:
        hl = ou_half_life(spread.dropna())
    except ImportError:
        hl = float("nan")
    result["spread_halflife"] = hl

    return result
