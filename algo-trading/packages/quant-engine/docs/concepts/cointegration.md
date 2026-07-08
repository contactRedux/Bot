# Cointegration and Statistical Arbitrage

> **Code links:** [`strategies/stat_arb.py`](../../strategies/stat_arb.py) · [`features/statistical.py`](../../features/statistical.py)

---

## Table of Contents

1. [Correlation vs Cointegration](#1-correlation-vs-cointegration)
2. [The Engle-Granger Test](#2-the-engle-granger-test)
3. [The Ornstein-Uhlenbeck Process](#3-the-ornstein-uhlenbeck-process)
4. [Half-Life Calculation](#4-half-life-calculation)
5. [Pairs Trading: Entry and Exit Rules](#5-pairs-trading-entry-and-exit-rules)
6. [Worked Example](#6-worked-example)
7. [Pitfalls and Regime Breaks](#7-pitfalls-and-regime-breaks)

---

## 1. Correlation vs Cointegration

Two price series can be **highly correlated** without being **cointegrated**, and the difference matters enormously for trading.

**Correlation** measures whether two series *move together in the short run* — their percentage changes tend to have the same sign. AAPL and MSFT have a Pearson correlation of ~0.85 on daily returns. But if you take their price levels and subtract one from the other, the spread will wander without bound over time.

**Cointegration** is a stronger statement: two non-stationary series (e.g. random walks) are cointegrated if a *linear combination* of them is stationary. Stationary means the spread has a constant mean and variance — it cannot drift to infinity.

```
X_t ~ I(1),  Y_t ~ I(1)
Cointegrated ⟺  ∃ β such that  Z_t = Y_t - β·X_t  ~  I(0)
```

Where `I(1)` means "integrated of order 1" (a random walk), and `I(0)` means stationary (mean-reverting). The scalar `β` is the **hedge ratio**.

**Intuition:** If Shell and BP are cointegrated, there is a long-run economic relationship (both track oil prices) that prevents the spread from diverging permanently. Short-term deviations are noise; the spread will revert. That reversion is the source of profit for a stat arb strategy.

---

## 2. The Engle-Granger Test

The Engle-Granger (1987) two-step procedure tests for cointegration between two series.

**Step 1 — OLS regression:**

```
Y_t = α + β·X_t + ε_t
```

Fit this regression to get the hedge ratio `β` and the residual series `ε_t`.

**Step 2 — ADF test on residuals:**

Test whether `ε_t` is stationary using the Augmented Dickey-Fuller (ADF) test. The null hypothesis is a unit root (non-stationary). Reject the null (p < 0.05) → cointegrated.

```python
# From features/statistical.py
from statsmodels.tsa.stattools import coint
score, pvalue, critical_values = coint(series_x, series_y)
if pvalue < 0.05:
    print("Cointegrated at 5% significance")
```

**Limitations:**
- Assumes a single cointegrating vector (works for pairs; use Johansen for baskets)
- The hedge ratio is estimated in-sample and can shift over time (regime breaks)
- Requires the series to both be I(1) — test this with ADF before running Engle-Granger

**Johansen test** (also in `features/statistical.py`) handles multiple cointegrating vectors and is preferred when trading baskets of 3+ assets.

---

## 3. The Ornstein-Uhlenbeck Process

Once we confirm cointegration, we model the spread `Z_t = Y_t - β·X_t` as an **Ornstein-Uhlenbeck (OU) process**:

```
dZ_t = κ(μ - Z_t) dt + σ dW_t
```

Where:
- `κ` (kappa) — mean-reversion speed. Higher = faster reversion.
- `μ` — long-run mean of the spread (theoretical equilibrium)
- `σ` — volatility of the spread
- `dW_t` — standard Brownian motion increment

In discrete time (daily bars):

```
Z_{t+1} - Z_t = a + b·Z_t + ε_t
```

Where:
- `b = e^{-κΔt} - 1` (negative, since the spread is pulled back toward μ)
- `a = μ(1 - e^{-κΔt})`

Estimate `a` and `b` by regressing `ΔZ_t` on `Z_t` using OLS — this is the discrete OU calibration in `features/statistical.py`.

---

## 4. Half-Life Calculation

The **half-life** is how long it takes for the spread to revert halfway to its mean. It is the single most useful summary statistic for a pairs trade.

```
half_life = -ln(2) / ln(1 + b)   ≈   ln(2) / κ
```

In practice (from `features/statistical.py`):

```python
import numpy as np

def ou_half_life(spread: pd.Series) -> float:
    delta = spread.diff().dropna()
    lag   = spread.shift(1).dropna()
    # Align
    delta, lag = delta.align(lag, join="inner")
    b = np.polyfit(lag, delta, 1)[0]        # slope of ΔZ ~ b·Z
    if b >= 0:
        return float("nan")                  # not mean-reverting
    return -np.log(2) / np.log(1 + b)
```

**Practical thresholds used in `stat_arb.py`:**
- Half-life < 5 days → too noisy / likely spurious
- Half-life > 60 days → mean reversion too slow to be tradeable before the relationship breaks
- Sweet spot: 5–30 days

---

## 5. Pairs Trading: Entry and Exit Rules

With a calibrated OU spread, entry/exit signals are driven by the **z-score** of the spread:

```
z_t = (Z_t - μ_hat) / σ_hat
```

Where `μ_hat` and `σ_hat` are the rolling mean and standard deviation of the spread over a lookback window.

| Signal | Condition | Action |
|--------|-----------|--------|
| Short spread | `z_t > +entry_threshold` (e.g. +2.0) | Sell Y, Buy X (β units) |
| Long spread | `z_t < -entry_threshold` | Buy Y, Sell X (β units) |
| Close short | `z_t < +exit_threshold` (e.g. +0.5) | Cover position |
| Close long | `z_t > -exit_threshold` | Cover position |
| Stop-loss | `\|z_t\| > stop_threshold` (e.g. 4.0) | Emergency close — regime may have broken |

Both legs are entered **simultaneously** to be delta-neutral. The position size of X is scaled by `β` so the dollar value of both legs is equal (dollar-neutral).

```python
# From strategies/stat_arb.py (simplified)
if z_score > entry_z and not in_position:
    orders.append(Order(ticker=Y, side=SELL, qty=base_qty))
    orders.append(Order(ticker=X, side=BUY,  qty=base_qty * hedge_ratio))
```

---

## 6. Worked Example

Suppose we are trading the Shell (SHEL) / BP (BP) spread.

```
OLS regression:  BP_t = 0.32 + 1.14 · SHEL_t + ε_t
ADF p-value on ε_t: 0.021  → cointegrated at 5%
OU calibration: κ = 0.052/day, μ = 0.0, σ = 0.85
Half-life: ln(2) / 0.052 ≈ 13.3 days
```

On day T, the spread z-score is +2.4 → **short spread signal**:
- Sell 100 shares of BP at $35.20 = $3,520
- Buy 100 × 1.14 = 114 shares of SHEL at $30.80 = $3,511 (approximately dollar-neutral)

Eight days later the z-score reverts to +0.3 → **close signal**:
- Cover BP short: buy 100 shares
- Close SHEL long: sell 114 shares

If BP fell $1.20 and SHEL rose $0.80, P&L ≈ $120 + $91 = $211 minus transaction costs.

---

## 7. Pitfalls and Regime Breaks

1. **Spurious cointegration** — with enough pairs and a fixed lookback, some will appear cointegrated by chance. Use out-of-sample validation and require p < 0.01.
2. **Regime breaks** — the cointegrating relationship can vanish (e.g., BP's Deepwater Horizon divergence). The OU stop-loss (`|z| > 4`) is the circuit breaker.
3. **Look-ahead bias** — all hedge ratio estimation and z-score normalisation must use only data available *before* the bar being traded. `features/statistical.py` enforces expanding-window estimation.
4. **Transaction costs** — pairs trades have 4 legs total (open 2, close 2). Commissions and slippage eat into the thin mean-reversion edge. Only trade spreads with half-life > 5 days.
5. **Crowded trades** — when every quant fund runs the same pairs, the spread z-score temporarily blows out during crowded unwind events (e.g., August 2007 quant quake).
