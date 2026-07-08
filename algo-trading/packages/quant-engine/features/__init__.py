"""
features/__init__.py — Public interface for the feature engineering layer.

Import pattern::

    from features.technical import add_all_technical
    from features.pipeline import FeaturePipeline
    # or
    from features import FeaturePipeline, add_all_technical
"""

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

from features.statistical import (
    rolling_pearson_correlation,
    rolling_spearman_correlation,
    compute_spread,
    compute_spread_zscore,
    engle_granger_test,
    johansen_test,
    ou_half_life,
    pair_features,
)

from features.fundamental import (
    snapshots_to_dataframe,
    add_fundamental_features,
    align_fundamentals_to_price_index,
    compute_sector_zscores,
)

from features.sentiment import (
    score_article,
    score_articles_batch,
    aggregate_sentiment,
    build_sentiment_timeseries,
)

from features.macro import (
    fetch_vix,
    add_vix_features,
    fetch_yield_curve_slope,
    add_yield_curve_features,
    add_usd_features,
    classify_macro_regime,
    macro_risk_scalar,
    build_macro_features,
)

from features.pipeline import FeaturePipeline

__all__ = [
    # Technical
    "add_ema", "add_macd", "add_adx", "add_ichimoku",
    "add_rsi", "add_stochastic", "add_roc", "add_williams_r",
    "add_bollinger_bands", "add_atr", "add_keltner_channels",
    "add_historical_volatility", "add_vwap", "add_obv",
    "add_volume_zscore", "add_chaikin_money_flow", "add_all_technical",
    # Statistical
    "rolling_pearson_correlation", "rolling_spearman_correlation",
    "compute_spread", "compute_spread_zscore",
    "engle_granger_test", "johansen_test", "ou_half_life", "pair_features",
    # Fundamental
    "snapshots_to_dataframe", "add_fundamental_features",
    "align_fundamentals_to_price_index", "compute_sector_zscores",
    # Sentiment
    "score_article", "score_articles_batch",
    "aggregate_sentiment", "build_sentiment_timeseries",
    # Macro
    "fetch_vix", "add_vix_features",
    "fetch_yield_curve_slope", "add_yield_curve_features",
    "add_usd_features", "classify_macro_regime",
    "macro_risk_scalar", "build_macro_features",
    # Pipeline
    "FeaturePipeline",
]
