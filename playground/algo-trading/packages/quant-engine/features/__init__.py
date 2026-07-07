"""
features package — feature engineering pipeline.

Sub-modules
-----------
features.technical   — Technical indicators (RSI, MACD, Bollinger, ATR, VWAP, …)
features.statistical — Cross-asset stats (cointegration, spread z-scores)
features.fundamental — Fundamental ML features (P/E z-score, EPS growth, …)
features.sentiment   — FinBERT NLP sentiment scoring and aggregation
features.macro       — Macro regime features (VIX, yield curve, USD)
features.pipeline    — FeaturePipeline: chains all modules into a feature matrix
"""
