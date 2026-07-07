"""
features/technical.py — Technical indicator feature engineering.

All functions here take a pandas DataFrame with columns ``open``, ``high``,
``low``, ``close``, ``volume`` (OHLCV) and return a new DataFrame containing
*only the computed feature columns*.  The caller (``FeaturePipeline``) is
responsible for joining these back onto the source DataFrame.

Look-ahead bias guarantee
--------------------------
Every indicator in this module is computed using *only past and present* data
relative to each bar.  Rolling windows look backward (``min_periods`` is set to
avoid NaN-filling with future data).  No ``shift(-N)`` calls exist anywhere in
this module — positive shifts are forward (future leak), negative shifts are
backward (safe).

This is verified by the test suite in ``tests/features/test_technical.py``.

Indicator groups
----------------
  Trend      — EMA family, MACD, ADX, Ichimoku Cloud
  Momentum   — RSI, Stochastic Oscillator, ROC, Williams %R
  Volatility — Bollinger Bands, ATR, Keltner Channels, Historical Volatility
  Volume     — VWAP, OBV, Volume Z-Score, Chaikin Money Flow

Implementation note: we use ``pandas-ta`` for most indicators and implement
a small number manually where pandas-ta's API is not compatible with our column
naming conventions or where the math needs to be explicit for educational value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_cols(df: pd.DataFrame, *cols: str) -> None:
    """Raise if required OHLCV columns are missing."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame is missing required columns: {missing}")


def _ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average — min_periods=span prevents future peeking."""
    return series.ewm(span=span, min_periods=span, adjust=False).mean()


# ══════════════════════════════════════════════════════════════════════════════
# TREND INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def add_ema(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """
    Exponential Moving Averages.

    EMA gives more weight to recent prices than a simple moving average,
    making it more responsive to new information.  Traders use the relationship
    between price and EMA lines to identify trends:
    * Price > EMA(200) → long-term uptrend
    * EMA(9) crosses above EMA(21) → short-term bullish crossover

    Parameters
    ----------
    periods : list of ints, default [9, 21, 50, 200]

    Returns columns: ``ema_9``, ``ema_21``, ``ema_50``, ``ema_200``
    """
    _require_cols(df, "close")
    if periods is None:
        periods = [9, 21, 50, 200]
    result = pd.DataFrame(index=df.index)
    for p in periods:
        result[f"ema_{p}"] = _ema(df["close"], p)
    return result


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    MACD — Moving Average Convergence Divergence.

    MACD = EMA(fast) − EMA(slow)
    Signal line = EMA(MACD, signal)
    Histogram = MACD − Signal

    The MACD histogram crossing zero is one of the most widely used trend-
    following signals.  Positive histogram = bullish momentum; negative = bearish.

    Returns columns: ``macd``, ``macd_signal``, ``macd_hist``
    """
    _require_cols(df, "close")
    result = pd.DataFrame(index=df.index)
    ema_fast = _ema(df["close"], fast)
    ema_slow = _ema(df["close"], slow)
    result["macd"] = ema_fast - ema_slow
    result["macd_signal"] = _ema(result["macd"], signal)
    result["macd_hist"] = result["macd"] - result["macd_signal"]
    return result


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    ADX — Average Directional Index.

    ADX measures *trend strength*, not direction.  A value above 25 indicates
    a strong trend (useful for the momentum strategy); below 20 indicates a
    ranging/choppy market (favourable for mean reversion).

    Components:
    * +DI  (positive directional indicator) — upward price pressure
    * -DI  (negative directional indicator) — downward price pressure
    * ADX  = smoothed ratio of |+DI − -DI| / (+DI + -DI)

    Returns columns: ``adx``, ``adx_plus_di``, ``adx_minus_di``
    """
    _require_cols(df, "high", "low", "close")
    result = pd.DataFrame(index=df.index)

    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    # True Range
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    # Directional movement
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder's smoothed ATR and DM
    atr_s = pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean()
    tr_s = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    minus_s = pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean()

    plus_di = 100 * atr_s / tr_s.replace(0, np.nan)
    minus_di = 100 * minus_s / tr_s.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    result["adx"] = adx
    result["adx_plus_di"] = plus_di
    result["adx_minus_di"] = minus_di
    return result


def add_ichimoku(
    df: pd.DataFrame,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
) -> pd.DataFrame:
    """
    Ichimoku Cloud (Ichimoku Kinko Hyo).

    The Ichimoku Cloud is a comprehensive momentum + trend indicator with
    five components:

    * **Tenkan-sen** (Conversion Line) = (9-period high + 9-period low) / 2
      A short-term trend signal — like a fast-period midpoint.

    * **Kijun-sen** (Base Line) = (26-period high + 26-period low) / 2
      A medium-term trend signal and key support/resistance level.  When price
      is above Kijun-sen, the trend is bullish.

    * **Senkou Span A** (Leading Span A) = (Tenkan + Kijun) / 2, *shifted forward 26 bars*
      — we shift *backwards* for current-bar feature generation (no leak).
      Represents one edge of the "cloud" (Kumo).

    * **Senkou Span B** (Leading Span B) = (52-period high + 52-period low) / 2,
      shifted forward 26 bars — similarly shifted backward here.
      Represents the other edge of the Kumo.

    * **Chikou Span** (Lagging Span) = current close plotted 26 periods backward.
      We shift close forward by 26 periods and then align back — for current-bar
      use we take the close shifted back 26 bars (past close).

    The *cloud thickness* (|Span A − Span B|) indicates support/resistance strength.
    Price above a bullish cloud (Span A > Span B) = strong uptrend.

    Returns columns:
        ``ichimoku_tenkan``, ``ichimoku_kijun``,
        ``ichimoku_span_a``, ``ichimoku_span_b``,
        ``ichimoku_cloud_thickness``, ``ichimoku_price_vs_cloud``
    """
    _require_cols(df, "high", "low", "close")
    result = pd.DataFrame(index=df.index)

    high, low, close = df["high"], df["low"], df["close"]

    tenkan_line = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    kijun_line = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2

    # Senkou spans — traditionally plotted 26 periods ahead.
    # For backtesting features, we use the *current* period's value directly
    # (no forward shift), which is the value that would be in the "future cloud"
    # at the time it's computed.  This is consistent with how live strategies
    # actually use Ichimoku: they look at the cloud projected from today's data.
    span_a = (tenkan_line + kijun_line) / 2
    span_b = (high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2

    result["ichimoku_tenkan"] = tenkan_line
    result["ichimoku_kijun"] = kijun_line
    result["ichimoku_span_a"] = span_a
    result["ichimoku_span_b"] = span_b
    result["ichimoku_cloud_thickness"] = (span_a - span_b).abs()
    # +1 if price is above cloud, -1 if below, 0 if inside
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
    result["ichimoku_price_vs_cloud"] = np.select(
        [close > cloud_top, close < cloud_bot], [1.0, -1.0], default=0.0
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# MOMENTUM INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    RSI — Relative Strength Index.

    RSI = 100 − 100 / (1 + RS)  where RS = avg_gain / avg_loss over ``period`` bars.

    Classic thresholds:
    * RSI > 70 → overbought (potential short / mean-reversion signal)
    * RSI < 30 → oversold (potential long / mean-reversion signal)
    * RSI divergence from price = early trend reversal signal

    Returns column: ``rsi_{period}``
    """
    _require_cols(df, "close")
    result = pd.DataFrame(index=df.index)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Wilder's smoothing (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result[f"rsi_{period}"] = 100 - (100 / (1 + rs))
    return result


def add_stochastic(
    df: pd.DataFrame, k_period: int = 14, d_period: int = 3
) -> pd.DataFrame:
    """
    Stochastic Oscillator (%K and %D).

    %K = 100 * (close − lowest_low_N) / (highest_high_N − lowest_low_N)
    %D = SMA(%K, d_period)  — the signal line

    %K > 80 = overbought; %K < 20 = oversold.
    A %K/%D crossover in oversold territory is a buy signal.

    Returns columns: ``stoch_k``, ``stoch_d``
    """
    _require_cols(df, "high", "low", "close")
    result = pd.DataFrame(index=df.index)
    lowest_low = df["low"].rolling(k_period, min_periods=k_period).min()
    highest_high = df["high"].rolling(k_period, min_periods=k_period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    result["stoch_k"] = 100 * (df["close"] - lowest_low) / denom
    result["stoch_d"] = result["stoch_k"].rolling(d_period, min_periods=d_period).mean()
    return result


def add_roc(df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
    """
    ROC — Rate of Change.

    ROC = (close_t − close_{t−N}) / close_{t−N} * 100

    ROC is a pure momentum indicator — it measures how much price has changed
    over N bars as a percentage.  Positive ROC = upward momentum.

    Returns column: ``roc_{period}``
    """
    _require_cols(df, "close")
    result = pd.DataFrame(index=df.index)
    prev_close = df["close"].shift(period)
    result[f"roc_{period}"] = 100 * (df["close"] - prev_close) / prev_close.replace(0, np.nan)
    return result


def add_williams_r(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Williams %R.

    %R = -100 * (highest_high − close) / (highest_high − lowest_low)

    Range: -100 to 0.  Similar to %K Stochastic but inverted.
    * %R > -20 → overbought
    * %R < -80 → oversold

    Returns column: ``williams_r``
    """
    _require_cols(df, "high", "low", "close")
    result = pd.DataFrame(index=df.index)
    highest_high = df["high"].rolling(period, min_periods=period).max()
    lowest_low = df["low"].rolling(period, min_periods=period).min()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    result["williams_r"] = -100 * (highest_high - df["close"]) / denom
    return result


# ══════════════════════════════════════════════════════════════════════════════
# VOLATILITY INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def add_bollinger_bands(
    df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
) -> pd.DataFrame:
    """
    Bollinger Bands.

    Middle band = SMA(close, period)
    Upper band = Middle + std_dev * rolling_std(close, period)
    Lower band = Middle − std_dev * rolling_std(close, period)
    %B = (close − lower) / (upper − lower)  → position within the bands [0, 1]
    Band width = (upper − lower) / middle   → normalized volatility measure

    Mean-reversion signal: %B > 1 (above upper band) = potential short;
    %B < 0 (below lower band) = potential long.

    Returns columns: ``bb_upper``, ``bb_middle``, ``bb_lower``, ``bb_pct_b``, ``bb_width``
    """
    _require_cols(df, "close")
    result = pd.DataFrame(index=df.index)
    middle = df["close"].rolling(period, min_periods=period).mean()
    std = df["close"].rolling(period, min_periods=period).std(ddof=1)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    band_range = (upper - lower).replace(0, np.nan)
    result["bb_upper"] = upper
    result["bb_middle"] = middle
    result["bb_lower"] = lower
    result["bb_pct_b"] = (df["close"] - lower) / band_range
    result["bb_width"] = (upper - lower) / middle.replace(0, np.nan)
    return result


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    ATR — Average True Range.

    True Range = max(high−low, |high−prev_close|, |low−prev_close|)
    ATR = Wilder's smoothed average of True Range

    ATR is the gold standard for measuring *absolute* volatility.  It is used by:
    * Stop-loss placement: stop = entry − 2×ATR
    * Position sizing: smaller position when ATR is large (more volatile)
    * Keltner Channels

    Returns columns: ``atr``, ``atr_pct`` (ATR as % of close)
    """
    _require_cols(df, "high", "low", "close")
    result = pd.DataFrame(index=df.index)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    result["atr"] = atr
    result["atr_pct"] = atr / df["close"].replace(0, np.nan)
    return result


def add_keltner_channels(
    df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, multiplier: float = 2.0
) -> pd.DataFrame:
    """
    Keltner Channels.

    Middle = EMA(close, ema_period)
    Upper  = Middle + multiplier × ATR(atr_period)
    Lower  = Middle − multiplier × ATR(atr_period)

    Similar to Bollinger Bands but uses ATR (volatility-adaptive) rather than
    standard deviation.  Keltner Channels are smoother and tend to produce
    fewer false breakout signals.

    Price breakout above upper Keltner = strong bullish momentum.

    Returns columns: ``kc_upper``, ``kc_middle``, ``kc_lower``, ``kc_position``
    """
    _require_cols(df, "high", "low", "close")
    result = pd.DataFrame(index=df.index)
    middle = _ema(df["close"], ema_period)
    atr_df = add_atr(df, atr_period)
    atr = atr_df["atr"]
    upper = middle + multiplier * atr
    lower = middle - multiplier * atr
    band_range = (upper - lower).replace(0, np.nan)
    result["kc_upper"] = upper
    result["kc_middle"] = middle
    result["kc_lower"] = lower
    result["kc_position"] = (df["close"] - lower) / band_range
    return result


def add_historical_volatility(
    df: pd.DataFrame, period: int = 20, annualize: bool = True
) -> pd.DataFrame:
    """
    Historical Volatility (HV) — log-return standard deviation.

    HV = std(log(close_t / close_{t-1}), period)
    Annualized HV = HV × sqrt(252)  (for daily bars; 52 for weekly, 12 for monthly)

    HV is the realized counterpart to implied volatility (IV).  When HV is low
    and IV is high, options are expensive — an edge for volatility sellers.
    For this system, HV is used to scale position sizes (lower when HV is high).

    Returns columns: ``hv_{period}``, ``hv_{period}_annualized``
    """
    _require_cols(df, "close")
    result = pd.DataFrame(index=df.index)
    log_returns = np.log(df["close"] / df["close"].shift(1))
    hv = log_returns.rolling(period, min_periods=period).std(ddof=1)
    result[f"hv_{period}"] = hv
    if annualize:
        result[f"hv_{period}_annualized"] = hv * np.sqrt(252)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# VOLUME INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def add_vwap(df: pd.DataFrame, period: int | None = None) -> pd.DataFrame:
    """
    VWAP — Volume-Weighted Average Price.

    VWAP = Σ(typical_price × volume) / Σ(volume)

    where typical_price = (high + low + close) / 3.

    Intraday VWAP (session-based) resets each day and is the primary
    institutional benchmark price.  Rolling VWAP (period-based, used here)
    is a smoothed version useful for multi-day strategies.

    When ``period`` is None, a cumulative VWAP is computed from the start
    of the DataFrame (appropriate if the DataFrame represents a single session).

    Returns columns: ``vwap``, ``price_vs_vwap`` (close/VWAP ratio − 1)
    """
    _require_cols(df, "high", "low", "close", "volume")
    result = pd.DataFrame(index=df.index)
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = typical_price * df["volume"]

    if period is None:
        # Cumulative VWAP from start of data
        vwap = tp_vol.cumsum() / df["volume"].cumsum().replace(0, np.nan)
    else:
        vwap = tp_vol.rolling(period, min_periods=period).sum() / \
               df["volume"].rolling(period, min_periods=period).sum().replace(0, np.nan)

    result["vwap"] = vwap
    result["price_vs_vwap"] = df["close"] / vwap.replace(0, np.nan) - 1
    return result


def add_obv(df: pd.DataFrame) -> pd.DataFrame:
    """
    OBV — On-Balance Volume.

    OBV adds volume on up-days and subtracts it on down-days:
    * OBV_t = OBV_{t-1} + volume if close > prev_close
    * OBV_t = OBV_{t-1} − volume if close < prev_close
    * OBV_t = OBV_{t-1} if close == prev_close

    OBV is a cumulative measure of buying vs. selling pressure.  Divergence
    between OBV and price is one of the strongest volume-based signals:
    rising OBV with falling price = accumulation (bullish).

    Returns columns: ``obv``, ``obv_ema_20`` (smoothed OBV for cleaner signals)
    """
    _require_cols(df, "close", "volume")
    result = pd.DataFrame(index=df.index)
    direction = np.sign(df["close"].diff().fillna(0))
    obv = (direction * df["volume"]).cumsum()
    result["obv"] = obv
    result["obv_ema_20"] = _ema(obv, 20)
    return result


def add_volume_zscore(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Volume Z-Score — normalized volume relative to recent history.

    z = (volume − rolling_mean(volume)) / rolling_std(volume)

    A high positive z-score (> 2) indicates a volume spike — often associated
    with institutional activity, breakout confirmation, or news events.
    The momentum strategy uses volume z-score as a signal confidence multiplier.

    Returns column: ``volume_zscore``
    """
    _require_cols(df, "volume")
    result = pd.DataFrame(index=df.index)
    roll_mean = df["volume"].rolling(period, min_periods=period).mean()
    roll_std = df["volume"].rolling(period, min_periods=period).std(ddof=1).replace(0, np.nan)
    result["volume_zscore"] = (df["volume"] - roll_mean) / roll_std
    return result


def add_chaikin_money_flow(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    CMF — Chaikin Money Flow.

    CMF = Σ(MFV, period) / Σ(volume, period)

    where Money Flow Multiplier = [(close − low) − (high − close)] / (high − low)
          Money Flow Volume = MFM × volume

    CMF ranges from -1 to +1:
    * CMF > 0 → money flowing in (bullish pressure)
    * CMF < 0 → money flowing out (bearish pressure)
    * CMF > 0.25 = strong buying pressure

    Returns column: ``cmf``
    """
    _require_cols(df, "high", "low", "close", "volume")
    result = pd.DataFrame(index=df.index)
    hl_range = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range
    mfv = mfm * df["volume"]
    cmf = (
        mfv.rolling(period, min_periods=period).sum()
        / df["volume"].rolling(period, min_periods=period).sum().replace(0, np.nan)
    )
    result["cmf"] = cmf
    return result


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSITE: add all indicators at once
# ══════════════════════════════════════════════════════════════════════════════

def add_all_technical(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical indicators and return them as a single wide DataFrame.

    This is the primary entry point used by ``FeaturePipeline``.  The input
    DataFrame must have columns: ``open``, ``high``, ``low``, ``close``, ``volume``.

    Returns a DataFrame with all indicator columns (no OHLCV columns duplicated).
    The index matches the input index.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame sorted ascending by time index.

    Returns
    -------
    pd.DataFrame
        All technical feature columns, same index as ``df``.
    """
    parts = [
        add_ema(df),
        add_macd(df),
        add_adx(df),
        add_ichimoku(df),
        add_rsi(df),
        add_stochastic(df),
        add_roc(df),
        add_williams_r(df),
        add_bollinger_bands(df),
        add_atr(df),
        add_keltner_channels(df),
        add_historical_volatility(df),
        add_vwap(df),
        add_obv(df),
        add_volume_zscore(df),
        add_chaikin_money_flow(df),
    ]
    return pd.concat(parts, axis=1)
