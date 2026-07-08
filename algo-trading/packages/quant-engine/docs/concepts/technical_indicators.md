# Technical Indicators — Math and Implementation

> **Code links:** [`features/technical.py`](../../features/technical.py)

All 40 indicators implemented in `features/technical.py` are listed here with their formulas, calculation steps, and trading interpretation.

---

## Table of Contents

1. [Trend Indicators](#1-trend-indicators)
2. [Momentum Oscillators](#2-momentum-oscillators)
3. [Volatility Indicators](#3-volatility-indicators)
4. [Volume Indicators](#4-volume-indicators)
5. [Ichimoku Cloud](#5-ichimoku-cloud)
6. [Implementation Notes](#6-implementation-notes)

---

## 1. Trend Indicators

### Exponential Moving Average (EMA)

EMA gives more weight to recent prices than a simple moving average.

```
EMA_t = price_t × k  +  EMA_{t-1} × (1 - k)
k = 2 / (period + 1)
```

The platform computes EMA-9, EMA-21, EMA-50, EMA-200. **Crossover signals:**
- EMA-9 crosses above EMA-21 → short-term bullish
- Price above EMA-200 → long-term uptrend

### MACD (Moving Average Convergence Divergence)

```
MACD_line    = EMA-12 - EMA-26
Signal_line  = EMA-9(MACD_line)
Histogram    = MACD_line - Signal_line
```

**Signal:** Histogram crosses zero → momentum shift. MACD line crosses signal line → entry signal.

### ADX (Average Directional Index)

ADX measures **trend strength** (not direction) on a 0–100 scale.

```
+DM = max(high - prev_high, 0)  if  high - prev_high > prev_low - low
-DM = max(prev_low - low, 0)    if  prev_low - low > high - prev_high

TR  = max(high - low, |high - prev_close|, |low - prev_close|)
ATR = EMA-14(TR)

+DI = 100 × EMA-14(+DM) / ATR
-DI = 100 × EMA-14(-DM) / ATR

DX  = 100 × |+DI - -DI| / (+DI + -DI)
ADX = EMA-14(DX)
```

**Usage in this platform:**
- Momentum strategy: skip entry if ADX < 20 (no trend)
- Mean reversion strategy: skip entry if ADX > 25 (trending, not ranging)

---

## 2. Momentum Oscillators

### RSI (Relative Strength Index)

```
RS   = avg_gain_14 / avg_loss_14      (Wilder's smoothing)
RSI  = 100 - 100 / (1 + RS)
```

Where avg_gain and avg_loss use exponential smoothing with `α = 1/14`.

**Levels:** RSI < 30 = oversold; RSI > 70 = overbought.

```python
# features/technical.py
delta = close.diff()
gain  = delta.clip(lower=0)
loss  = (-delta).clip(lower=0)
avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
rsi = 100 - (100 / (1 + avg_gain / avg_loss))
```

### Stochastic Oscillator

```
%K = 100 × (close - lowest_low_14) / (highest_high_14 - lowest_low_14)
%D = SMA-3(%K)
```

**Signal:** %K crosses %D above 80 (overbought) or below 20 (oversold).

### Rate of Change (ROC)

```
ROC_n = (close_t / close_{t-n} - 1) × 100
```

Simple n-period return as a percentage. The platform uses n=10 (2-week momentum).

### Williams %R

```
%R = -100 × (highest_high_14 - close) / (highest_high_14 - lowest_low_14)
```

Ranges from -100 to 0. Overbought: > -20; oversold: < -80. Note: same formula as %K, but scaled to [-100, 0] and inverted.

---

## 3. Volatility Indicators

### Bollinger Bands

```
Middle = SMA-20(close)
Upper  = Middle + 2 × σ_20
Lower  = Middle - 2 × σ_20
```

Where σ_20 is the rolling 20-day standard deviation of closes.

**Band width:** `(Upper - Lower) / Middle` — high bandwidth = volatile, low = quiet (Bollinger Squeeze).

**Z-score:** `(close - Middle) / σ_20` — used directly as the mean-reversion entry signal in `strategies/mean_reversion.py`.

### ATR (Average True Range)

```
TR  = max(high - low,  |high - prev_close|,  |low - prev_close|)
ATR = EMA-14(TR)
```

ATR measures *realised* volatility in price units. Used for:
- Stop-loss sizing: `stop = entry - 2 × ATR`
- Spread sizing in market making: `half_spread = 0.5 × ATR`

### Keltner Channels

```
Middle = EMA-20(close)
Upper  = Middle + 2 × ATR-10
Lower  = Middle - 2 × ATR-10
```

Similar to Bollinger Bands but uses ATR instead of standard deviation — smoother, less reactive to single-day spikes. Combined with Bollinger Bands: when BB is inside Keltner (Squeeze), a breakout is building.

### Historical Volatility (HV)

```
HV_n = σ(log(close_t / close_{t-1})) × sqrt(252)
```

Annualised standard deviation of log returns. The platform computes HV-20 (1-month) and HV-60 (3-month).

---

## 4. Volume Indicators

### VWAP (Volume-Weighted Average Price)

```
VWAP = Σ(typical_price × volume) / Σ(volume)
typical_price = (high + low + close) / 3
```

Accumulated intraday. Used by institutional traders as a benchmark — buying below VWAP is considered favourable. The platform computes a rolling daily VWAP reset.

```python
# features/technical.py
typical = (df["high"] + df["low"] + df["close"]) / 3
cum_tv   = (typical * df["volume"]).cumsum()
cum_vol  = df["volume"].cumsum()
vwap     = cum_tv / cum_vol
```

### OBV (On-Balance Volume)

```
OBV_t = OBV_{t-1} + volume_t   if close_t > close_{t-1}
OBV_t = OBV_{t-1} - volume_t   if close_t < close_{t-1}
OBV_t = OBV_{t-1}              if close_t = close_{t-1}
```

OBV is a running total that adds volume on up-days and subtracts on down-days. Divergence from price (price makes a new high but OBV doesn't) signals weakening trend.

### Volume Z-Score

```
vol_z = (volume_t - SMA-20(volume)) / std-20(volume)
```

Identifies unusual volume events. A spike > 2σ often precedes a large directional move.

### Chaikin Money Flow (CMF)

```
MFV = ((close - low) - (high - close)) / (high - low) × volume
CMF_20 = Σ_{20} MFV / Σ_{20} volume
```

CMF ranges from -1 to +1. Positive = accumulation (buying pressure); negative = distribution (selling). Unlike OBV, CMF incorporates the position of the close within the bar's range.

---

## 5. Ichimoku Cloud

The **Ichimoku Kinkō Hyō** ("one-glance equilibrium chart") packages five lines that together define support/resistance, trend, and momentum.

```
Tenkan-sen (Conversion)  = (highest_high_9  + lowest_low_9)  / 2
Kijun-sen  (Base)        = (highest_high_26 + lowest_low_26) / 2
Senkou A   (Leading A)   = (Tenkan + Kijun) / 2              [plotted 26 periods ahead]
Senkou B   (Leading B)   = (highest_high_52 + lowest_low_52) / 2 [plotted 26 periods ahead]
Chikou    (Lagging)      = close                             [plotted 26 periods back]
```

**Cloud (Kumo):** The area between Senkou A and B. Price above cloud = uptrend; below cloud = downtrend; inside cloud = consolidation.

**Signals:**
- Tenkan crosses above Kijun = bullish TK cross (strong if above cloud)
- Price crosses above cloud = strong trend confirmation
- Chikou above price from 26 periods ago = momentum confirmation

Ichimoku features are used as inputs to the LSTM and Transformer models in Sub-Tasks 4–5.

---

## 6. Implementation Notes

All indicators in `features/technical.py`:
- Accept a `pd.DataFrame` with columns `open`, `high`, `low`, `close`, `volume`
- Return a `pd.DataFrame` with the same index (no index alignment issues)
- Use pandas `.shift(1)` for lagged values — ensuring no look-ahead on the current bar
- Are implemented without `pandas-ta` (unavailable on Python 3.11 arm64) using pure NumPy/pandas

**Look-ahead safety:** Every indicator only uses `close_t` and earlier bars. The backtesting engine further enforces this by slicing the feature matrix to only include rows up to and including the current bar index.
