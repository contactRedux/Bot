"""
features/macro.py — Macro-regime feature engineering.

Macro features describe the overall economic and market environment.  Unlike
microstructure features (technical indicators), macro features change slowly
and condition the *risk appetite* of the entire system, not just individual
strategies.

The ``StrategyOrchestrator`` (Sub-Task 5) reads these features and applies
global scaling to all strategy allocations when the macro regime is unfavourable.

Macro feature taxonomy
-----------------------

**VIX — CBOE Volatility Index**
    VIX measures the market's expectation of 30-day implied volatility
    derived from S&P 500 option prices.  It is the canonical "fear gauge":

    * VIX < 15    → low volatility, complacency, favourable for momentum
    * 15 < VIX < 25 → normal market conditions
    * VIX > 25    → elevated fear; reduce equity exposure by 50%
    * VIX > 40    → crisis regime; dramatic position reduction warranted

    We fetch VIX as ticker ``^VIX`` from yfinance.

**Yield Curve Slope (10Y − 2Y spread)**
    The difference between the 10-year and 2-year US Treasury yields.

    * Positive spread (normal curve): long-term rates > short-term → healthy economy
    * Inverted curve (spread < 0): short-term rates > long-term → recession signal
      Every US recession since 1955 has been preceded by a yield curve inversion
      (with a typical 6–18 month lag).

    We approximate the yield curve slope using Alpha Vantage's daily yield data
    or fetch directly from FRED (Federal Reserve Economic Data) via httpx.

**USD Index Momentum**
    The US Dollar Index (DXY) measures USD strength against a basket of
    currencies.  Rising USD = tighter global financial conditions, typically
    bearish for risk assets (equities, crypto) and especially bearish for
    emerging market assets.

    We compute a simple momentum signal: 20-day ROC of the DXY.

**Macro Regime Classification**
    Combines the above into a discrete regime label for the StrategyOrchestrator:

    ``RISK_ON``   — low VIX, positive yield curve, neutral USD
    ``RISK_OFF``  — high VIX or inverted yield curve
    ``CRISIS``    — VIX > 40 or very strong USD momentum (flight to safety)

Look-ahead bias
---------------
VIX and yield data are fetched as historical time series.  The feature
value at bar T uses only data available up to and including T.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import numpy as np
import pandas as pd

MacroRegime = Literal["RISK_ON", "RISK_OFF", "CRISIS"]

# VIX thresholds
_VIX_LOW = 15.0
_VIX_ELEVATED = 25.0
_VIX_CRISIS = 40.0

# Yield curve inversion threshold
_YIELD_INVERSION = 0.0

# FRED API for US Treasury yields (no key required)
_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"


# ── VIX features ─────────────────────────────────────────────────────────────

def fetch_vix(
    start: datetime,
    end: datetime,
) -> pd.Series:
    """
    Fetch historical VIX (CBOE Volatility Index) from Yahoo Finance.

    VIX is available as ticker ``^VIX`` via yfinance with a long daily history.

    Parameters
    ----------
    start, end : datetime
        UTC date range.

    Returns
    -------
    pd.Series
        Daily VIX close values, DatetimeIndex (UTC).
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance is required.  Run: pip install 'quant-engine[data]'")

    df = yf.download(
        "^VIX",
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        multi_level_column=False,
    )
    if df.empty:
        return pd.Series(dtype=float)

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    return df["Close"].rename("vix")


def add_vix_features(vix: pd.Series, regime_window: int = 20) -> pd.DataFrame:
    """
    Derive trading features from VIX time series.

    Parameters
    ----------
    vix : pd.Series
        Daily VIX close values.
    regime_window : int
        Rolling window for VIX smoothing and percentile ranking.

    Returns
    -------
    pd.DataFrame
        Columns: ``vix``, ``vix_ma``, ``vix_zscore``, ``vix_percentile``,
        ``vix_regime`` (-1=risk_off, 0=neutral, 1=risk_on)
    """
    result = pd.DataFrame(index=vix.index)
    result["vix"] = vix
    result["vix_ma"] = vix.rolling(regime_window, min_periods=regime_window).mean()

    roll_std = vix.rolling(regime_window, min_periods=regime_window).std(ddof=1).replace(0, np.nan)
    roll_mean = vix.rolling(regime_window, min_periods=regime_window).mean()
    result["vix_zscore"] = (vix - roll_mean) / roll_std

    # Rolling percentile rank: where is today's VIX within trailing history?
    result["vix_percentile"] = vix.rolling(252, min_periods=60).rank(pct=True)

    # Regime classification based on absolute VIX level
    result["vix_regime"] = np.select(
        [vix > _VIX_CRISIS, vix > _VIX_ELEVATED],
        [-2.0, -1.0],  # CRISIS, RISK_OFF
        default=np.where(vix < _VIX_LOW, 1.0, 0.0),  # RISK_ON or NEUTRAL
    )

    return result


# ── Yield curve features ──────────────────────────────────────────────────────

def fetch_yield_curve_slope(
    start: datetime,
    end: datetime,
) -> pd.Series:
    """
    Fetch the US 10Y−2Y yield curve slope from FRED.

    Uses the FRED CSV API (no API key required for individual series downloads).
    Series IDs:
      * ``DGS10`` — 10-year constant maturity Treasury yield
      * ``DGS2``  — 2-year constant maturity Treasury yield

    Parameters
    ----------
    start, end : datetime
        UTC date range.

    Returns
    -------
    pd.Series
        Daily 10Y−2Y spread (percentage points), DatetimeIndex.
        Negative = inverted yield curve.
    """
    try:
        import httpx
    except ImportError:
        raise ImportError("httpx is required.  Run: pip install httpx")

    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    def _fetch_fred_series(series_id: str) -> pd.Series:
        url = f"{_FRED_BASE}?id={series_id}&vintage_date={end_str}"
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(
            StringIO(resp.text),
            index_col=0,
            parse_dates=True,
            na_values=".",
        )
        s = df.iloc[:, 0].dropna().astype(float)
        s.index = pd.to_datetime(s.index).tz_localize("UTC")
        return s.loc[start_str:end_str]

    try:
        y10 = _fetch_fred_series("DGS10")
        y2 = _fetch_fred_series("DGS2")
        slope = (y10 - y2).dropna().rename("yield_curve_slope")
        return slope
    except Exception:
        # Fallback: return an empty series — pipeline will handle NaNs gracefully
        return pd.Series(dtype=float, name="yield_curve_slope")


def add_yield_curve_features(slope: pd.Series, ma_window: int = 20) -> pd.DataFrame:
    """
    Derive features from the yield curve slope time series.

    Returns
    -------
    pd.DataFrame
        Columns: ``yield_curve_slope``, ``yield_curve_inverted`` (bool as float),
        ``yield_curve_ma``, ``yield_curve_momentum``
    """
    result = pd.DataFrame(index=slope.index)
    result["yield_curve_slope"] = slope
    result["yield_curve_inverted"] = (slope < _YIELD_INVERSION).astype(float)
    result["yield_curve_ma"] = slope.rolling(ma_window, min_periods=ma_window).mean()
    result["yield_curve_momentum"] = slope.diff(ma_window)  # rate of change of slope
    return result


# ── USD momentum features ─────────────────────────────────────────────────────

def fetch_dxy(
    start: datetime,
    end: datetime,
) -> pd.Series:
    """
    Fetch the US Dollar Index (DXY) from Yahoo Finance as ``DX-Y.NYB``.

    Returns
    -------
    pd.Series
        Daily DXY close values.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance is required.")

    df = yf.download(
        "DX-Y.NYB",
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        multi_level_column=False,
    )
    if df.empty:
        return pd.Series(dtype=float)

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    return df["Close"].rename("dxy")


def add_usd_features(dxy: pd.Series, momentum_window: int = 20) -> pd.DataFrame:
    """
    Derive USD momentum features.

    Returns
    -------
    pd.DataFrame
        Columns: ``dxy``, ``usd_momentum`` (N-period ROC),
        ``usd_trend`` (EMA ratio), ``usd_regime`` (1=strong, -1=weak, 0=neutral)
    """
    result = pd.DataFrame(index=dxy.index)
    result["dxy"] = dxy

    prev = dxy.shift(momentum_window)
    result["usd_momentum"] = 100 * (dxy - prev) / prev.replace(0, np.nan)

    ema_fast = dxy.ewm(span=10, min_periods=10, adjust=False).mean()
    ema_slow = dxy.ewm(span=50, min_periods=50, adjust=False).mean()
    result["usd_trend"] = ema_fast / ema_slow.replace(0, np.nan) - 1

    result["usd_regime"] = np.select(
        [result["usd_momentum"] > 2.0, result["usd_momentum"] < -2.0],
        [1.0, -1.0],
        default=0.0,
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# MACRO REGIME CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def classify_macro_regime(
    vix: float | None,
    yield_slope: float | None,
    usd_momentum: float | None,
) -> MacroRegime:
    """
    Classify the current macro regime from three key indicators.

    This is the function called by the ``StrategyOrchestrator`` at each bar
    to determine global risk scaling.

    Parameters
    ----------
    vix : float | None
        Current VIX level.
    yield_slope : float | None
        Current 10Y−2Y yield curve slope (percentage points).
    usd_momentum : float | None
        20-day USD momentum (%).

    Returns
    -------
    MacroRegime
        One of ``"CRISIS"``, ``"RISK_OFF"``, or ``"RISK_ON"``.
    """
    # Crisis: VIX above crisis threshold or extremely strong USD flight
    if (vix is not None and vix > _VIX_CRISIS) or (
        usd_momentum is not None and usd_momentum > 5.0
    ):
        return "CRISIS"

    # Risk-off: elevated VIX or inverted yield curve
    if (vix is not None and vix > _VIX_ELEVATED) or (
        yield_slope is not None and yield_slope < _YIELD_INVERSION
    ):
        return "RISK_OFF"

    return "RISK_ON"


def macro_risk_scalar(regime: MacroRegime) -> float:
    """
    Convert a macro regime into a position-size scalar for the orchestrator.

    The StrategyOrchestrator multiplies all computed order quantities by this
    scalar to scale down risk in unfavourable macro environments.

    Returns
    -------
    float
        ``1.0`` = full sizing (RISK_ON)
        ``0.5`` = half sizing (RISK_OFF)
        ``0.25`` = quarter sizing (CRISIS)
    """
    if regime == "CRISIS":
        return 0.25
    if regime == "RISK_OFF":
        return 0.5
    return 1.0


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSITE: fetch and combine all macro features
# ══════════════════════════════════════════════════════════════════════════════

def build_macro_features(
    start: datetime,
    end: datetime,
    price_index: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """
    Fetch all macro data and compute the full macro feature DataFrame.

    Fetches VIX, yield curve, and DXY data for the given range, computes
    all derived features, aligns to a common daily index, and optionally
    reindexes to ``price_index`` with forward-fill.

    Parameters
    ----------
    start, end : datetime
        UTC date range for data fetching.
    price_index : pd.DatetimeIndex, optional
        If provided, features are forward-filled onto this index.

    Returns
    -------
    pd.DataFrame
        All macro features on a daily (or price_index) DatetimeIndex.
        Columns include: vix_*, yield_curve_*, usd_*, macro_regime_scalar.
    """
    # VIX
    vix_series = fetch_vix(start, end)
    vix_features = add_vix_features(vix_series) if not vix_series.empty else pd.DataFrame()

    # Yield curve
    slope = fetch_yield_curve_slope(start, end)
    yc_features = add_yield_curve_features(slope) if not slope.empty else pd.DataFrame()

    # USD
    dxy = fetch_dxy(start, end)
    usd_features = add_usd_features(dxy) if not dxy.empty else pd.DataFrame()

    # Concatenate on common daily index
    parts = [f for f in [vix_features, yc_features, usd_features] if not f.empty]

    if not parts:
        idx = price_index if price_index is not None else pd.DatetimeIndex([])
        return pd.DataFrame(index=idx)

    macro = pd.concat(parts, axis=1)

    # Add composite regime scalar per row
    def _row_scalar(row: pd.Series) -> float:
        vix_val = row.get("vix")
        slope_val = row.get("yield_curve_slope")
        usd_val = row.get("usd_momentum")
        regime = classify_macro_regime(
            float(vix_val) if pd.notna(vix_val) else None,
            float(slope_val) if pd.notna(slope_val) else None,
            float(usd_val) if pd.notna(usd_val) else None,
        )
        return macro_risk_scalar(regime)

    macro["macro_risk_scalar"] = macro.apply(_row_scalar, axis=1)

    # Align to price_index with forward-fill if requested
    if price_index is not None:
        combined_idx = macro.index.union(price_index).sort_values()
        macro = macro.reindex(combined_idx).ffill(limit=5).reindex(price_index)

    return macro
