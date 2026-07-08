# Macro Regimes and Factor Models

> **Code links:** [`features/macro.py`](../../features/macro.py) · [`strategies/macro_factor.py`](../../strategies/macro_factor.py)

---

## Table of Contents

1. [VIX as a Fear Gauge](#1-vix-as-a-fear-gauge)
2. [Yield Curve as a Recession Predictor](#2-yield-curve-as-a-recession-predictor)
3. [USD Momentum](#3-usd-momentum)
4. [Regime Classification: RISK_ON / RISK_OFF / CRISIS](#4-regime-classification-risk_on--risk_off--crisis)
5. [Earnings Surprise Drift (PEAD)](#5-earnings-surprise-drift-pead)
6. [How Macro Interacts with Strategy Weights](#6-how-macro-interacts-with-strategy-weights)

---

## 1. VIX as a Fear Gauge

The **CBOE Volatility Index (VIX)** measures the market's *expected* 30-day volatility of the S&P 500, derived from option prices. It is often called the "fear gauge" because it spikes when investors are anxious and buy protective puts.

**VIX is computed from option prices:**

```
VIX² = (2/T) · Σ [ΔK_i / K_i² · e^{rT} · Q(K_i)] - (1/T) · [F/K_0 - 1]²
```

Where:
- `K_i` — option strike prices
- `Q(K_i)` — mid-price of puts (below F) and calls (above F)
- `F` — forward price of the S&P 500
- `T` — time to expiration (normalised to 30 days)

**Practical interpretation:**

| VIX Level | Regime | Market Mood |
|-----------|--------|-------------|
| < 15 | RISK_ON | Complacency / bull market |
| 15–25 | Neutral | Normal uncertainty |
| 25–35 | RISK_OFF | Elevated fear, caution warranted |
| > 35 | CRISIS | Panic, historical: GFC, COVID crash |

The platform's `features/macro.py` fetches VIX from Yahoo Finance (`^VIX`) and uses the level as a primary regime input.

---

## 2. Yield Curve as a Recession Predictor

The **yield curve** plots Treasury yields across maturities (3-month, 2-year, 10-year, 30-year). In a healthy economy, long-term rates are higher than short-term rates (upward slope) — investors demand more compensation for locking money up longer.

**Inversion:** When the 2-year yield exceeds the 10-year yield (`10Y - 2Y < 0`), the curve has **inverted**. This has preceded every US recession in the past 50 years with a lead time of 6–18 months.

**Mechanism:** An inverted curve signals that markets expect the Fed to cut rates in the future (because the economy will weaken). Banks, which borrow short and lend long, see compressed margins and tighten credit.

```python
# features/macro.py
yield_curve_slope = rate_10y - rate_2y   # positive = normal, negative = inverted
```

**Platform usage:**
- `yield_curve_slope < 0` → increases probability of RISK_OFF / CRISIS regime
- Combined with VIX: deep inversion + high VIX = CRISIS signal

The platform fetches Treasury yields from Alpha Vantage (FRED data endpoint). Bloomberg provides higher-frequency intraday rate data when available.

---

## 3. USD Momentum

The **US Dollar Index (DXY)** measures the dollar against a basket of 6 currencies (EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%).

**USD strength matters for:**
- **Equities:** Strong dollar → headwinds for S&P 500 multinationals (revenue denominated in foreign currencies loses value when converted back to USD)
- **Commodities:** Commodities are USD-denominated. Strong dollar → lower commodity prices in USD terms
- **Emerging markets:** Dollar-denominated debt becomes more expensive to service → capital outflows

```python
# features/macro.py
usd_momentum = (dxy_t / dxy_t_20) - 1.0   # 20-day return of DXY
```

**Platform usage:**
- Strong rising dollar (`usd_momentum > threshold`) → reduces equity allocation
- Weak falling dollar → supportive for risk assets

---

## 4. Regime Classification: RISK_ON / RISK_OFF / CRISIS

The three macro signals (VIX, yield curve slope, USD momentum) are combined into a discrete **regime** used to scale all strategy outputs:

```python
# features/macro.py
def classify_regime(vix: float, yield_slope: float, usd_mom: float) -> MacroRegime:
    if vix > 35 or yield_slope < -0.5:
        return MacroRegime.CRISIS
    elif vix > 22 or (yield_slope < 0 and usd_mom > 0.02):
        return MacroRegime.RISK_OFF
    else:
        return MacroRegime.RISK_ON
```

| Regime | VaR Multiplier | Equity Allocation Multiplier |
|--------|---------------|------------------------------|
| RISK_ON | 1.0 | 1.0 |
| RISK_OFF | 0.6 | 0.7 |
| CRISIS | 0.2 | 0.3 |

These multipliers are applied by `strategies/orchestrator.py` to every strategy's final order quantities before execution:

```python
multiplier = macro_strategy.get_regime_multiplier(current_regime)
order.qty  = int(order.qty * multiplier)
```

**Rationale:** In a crisis regime, strategies that worked in RISK_ON (momentum, stat arb) often fail catastrophically. The regime multiplier acts as a systematic risk-off switch — reducing all position sizes rather than relying on individual strategies to detect the regime shift themselves.

---

## 5. Earnings Surprise Drift (PEAD)

**Post-Earnings Announcement Drift (PEAD)** is one of the most robust anomalies in academic finance: stocks that beat earnings estimates continue to drift upward for weeks after the announcement; stocks that miss continue downward.

**Standardised Unexpected Earnings (SUE):**

```
SUE = (Actual_EPS - Estimated_EPS) / std(historical_surprises)
```

```python
# features/fundamental.py
sue = (actual_eps - consensus_eps) / rolling_std_surprise
```

**Signal strength:**
- `SUE > 2.0` → strong positive PEAD signal (buy)
- `SUE < -2.0` → strong negative PEAD signal (sell)
- Hold window: typically 2–4 weeks post-announcement

`strategies/macro_factor.py` generates PEAD orders when `|SUE| > threshold`, sized proportionally to SUE magnitude and the current regime multiplier. In CRISIS regime, PEAD signals are suppressed (market overrides all fundamental signals).

---

## 6. How Macro Interacts with Strategy Weights

The full signal aggregation pipeline in `strategies/orchestrator.py`:

```
Individual strategy signals
  (momentum, mean_reversion, stat_arb, market_making, sentiment, macro_factor)
             ↓
  YAML weight normalisation (sum to 1.0)
             ↓
  Macro regime multiplier applied to ALL strategies
  (RISK_ON: ×1.0,  RISK_OFF: ×0.7,  CRISIS: ×0.3)
             ↓
  Same-direction signals: averaged and merged
  Opposite-direction signals: netted (partially cancel)
             ↓
  Portfolio position cap enforcement
  (no single position > max_position_pct × total equity)
             ↓
  Final orders → RiskManager → ExecutionBroker
```

The macro regime is intentionally a **portfolio-level override**, not a per-strategy parameter. This ensures that even if the momentum strategy is generating strong long signals, the system-wide response to a CRISIS regime is to reduce size across the board. An individual strategy should not be able to override macro risk management.
