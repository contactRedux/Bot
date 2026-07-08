# Risk Metrics Reference

> **Code links:** [`risk/var.py`](../../risk/var.py) · [`risk/monitor.py`](../../risk/monitor.py) · [`risk/manager.py`](../../risk/manager.py) · [`backtesting/metrics.py`](../../backtesting/metrics.py)

---

## Table of Contents

1. [Value at Risk (VaR)](#1-value-at-risk-var)
2. [CVaR / Expected Shortfall](#2-cvar--expected-shortfall)
3. [Sharpe Ratio](#3-sharpe-ratio)
4. [Sortino Ratio](#4-sortino-ratio)
5. [Calmar Ratio](#5-calmar-ratio)
6. [Maximum Drawdown](#6-maximum-drawdown)
7. [Ulcer Index](#7-ulcer-index)
8. [How They Work Together in This Platform](#8-how-they-work-together-in-this-platform)

---

## 1. Value at Risk (VaR)

**VaR at confidence level α** answers: *"What is the worst loss I should expect to NOT exceed with probability α, over a given horizon?"*

```
VaR_α = -quantile(returns, 1 - α)
```

For a 95% VaR: sort the last 252 daily returns; VaR is the negative of the 5th-percentile return.

**Historical Simulation** (used in `risk/var.py`):

```python
def historical_var(returns: np.ndarray, confidence: float = 0.95) -> float:
    sorted_returns = np.sort(returns)
    index = int((1 - confidence) * len(sorted_returns))
    return -sorted_returns[index]   # positive number = loss
```

**Example:** If the 5th percentile of your daily returns is -1.2%, then your 95% VaR is $12,000 on a $1M portfolio. On any given day, you should expect to lose more than this only 5% of the time (≈12 trading days per year).

**Limitations of VaR:**
1. Tells you nothing about the severity of losses *beyond* the threshold
2. Historical simulation assumes the future resembles the past (fat tails, regime breaks ignored)
3. Not sub-additive — VaR of a portfolio can exceed the sum of individual VaRs (see CVaR)

---

## 2. CVaR / Expected Shortfall

**Conditional VaR (CVaR)**, also called **Expected Shortfall (ES)**, fixes VaR's blindspot: it is the *average loss* given that you are already in the worst `(1-α)` of outcomes.

```
CVaR_α = E[ -R  |  -R > VaR_α ]
       = (1 / (1-α)) · ∫_{-∞}^{-VaR_α} (-r) · f(r) dr
```

In discrete form:

```python
def historical_cvar(returns: np.ndarray, confidence: float = 0.95) -> float:
    var = historical_var(returns, confidence)
    tail_losses = -returns[returns < -var]
    return float(np.mean(tail_losses)) if len(tail_losses) > 0 else var
```

**Example (continuing above):** The worst 5% of days average a loss of -2.1%. CVaR = $21,000 on a $1M portfolio. This is a more honest risk measure than VaR's $12,000.

**Why CVaR is strictly better than VaR:**
- Sub-additive: CVaR(A + B) ≤ CVaR(A) + CVaR(B) — diversification always helps
- Coherent risk measure (satisfies all four axioms of Artzner et al., 1999)
- Directly captures tail risk — what happens *after* the bad threshold is crossed

The platform computes both VaR and CVaR at 95% and 99% confidence levels, available via `GET /api/risk/status`.

---

## 3. Sharpe Ratio

The **Sharpe ratio** is the de-facto standard for risk-adjusted return:

```
Sharpe = (R_p - R_f) / σ_p
```

Where:
- `R_p` — annualised portfolio return
- `R_f` — risk-free rate (typically 0% in near-zero rate environments, or current T-bill rate)
- `σ_p` — annualised standard deviation of portfolio returns

To annualise from daily returns:

```python
daily_sharpe   = mean(daily_returns) / std(daily_returns)
annual_sharpe  = daily_sharpe * sqrt(252)
```

**Rule of thumb interpretation:**
| Sharpe | Interpretation |
|--------|---------------|
| < 0 | Strategy loses money on risk-adjusted basis |
| 0 – 0.5 | Poor |
| 0.5 – 1.0 | Acceptable |
| 1.0 – 2.0 | Good |
| > 2.0 | Excellent (likely overfitted if sustained) |

**Limitations:** Sharpe treats upside and downside volatility equally — a strategy that has large *positive* surprises will be penalised. This is addressed by the Sortino ratio.

---

## 4. Sortino Ratio

The **Sortino ratio** replaces σ_p (total volatility) with **downside deviation** — only volatility of negative returns counts:

```
Sortino = (R_p - R_f) / σ_downside

σ_downside = sqrt( mean( min(r_t - R_target, 0)² ) ) × sqrt(252)
```

Where `R_target` is often 0 (you only care about returns below zero).

```python
excess   = daily_returns - 0.0               # target = 0
downside = excess.clip(upper=0)
sortino  = mean(excess) / std(downside) * sqrt(252)
```

A Sortino > Sharpe indicates the strategy's volatility is predominantly *positive* — a good sign. If Sortino ≈ Sharpe, the return distribution is roughly symmetric.

---

## 5. Calmar Ratio

The **Calmar ratio** measures return relative to the worst drawdown experienced:

```
Calmar = Annualised Return / |Max Drawdown|
```

It asks: *"How much return am I getting per unit of the worst pain I've had to endure?"*

```python
calmar = cagr_pct / abs(max_drawdown_pct)
```

**Why it matters:** Sharpe penalises all volatility equally. Calmar specifically penalises the worst consecutive loss streak — which is the risk a human investor actually feels most acutely.

Target: Calmar > 1.0 in backtests (you earn more than the worst drawdown per year).

---

## 6. Maximum Drawdown

**Maximum drawdown (MDD)** is the largest peak-to-trough decline in portfolio value over the entire history:

```
MDD = max_{t in [0,T]} [ max_{s <= t} V_s  -  V_t ]
              ─────────────────────────────────────
                      max_{s <= t} V_s
```

In code:

```python
def max_drawdown(equity_curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (peak - equity_curve) / peak
    return float(drawdown.max())
```

`backtesting/metrics.py` also returns the **peak date** and **trough date**, so you can identify which market event caused the worst drawdown.

**DrawdownMonitor** in `risk/monitor.py` tracks this in real time and triggers a halt when current drawdown exceeds `max_drawdown_pct` in `RiskLimits`.

---

## 7. Ulcer Index

The **Ulcer Index** captures the depth *and duration* of drawdowns, not just the worst single point:

```
Ulcer = sqrt( (1/N) · Σ D_t² )

where D_t = (peak_t - V_t) / peak_t × 100  (drawdown in %)
```

```python
def ulcer_index(equity_curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity_curve)
    pct_drawdown = (peak - equity_curve) / peak * 100
    return float(np.sqrt(np.mean(pct_drawdown ** 2)))
```

Ulcer Index penalises strategies that spend a long time underwater — a 5% drawdown for 50 days scores worse than a 15% drawdown for 1 day. The **Martin Ratio** (Sharpe/Ulcer) is increasingly preferred over Calmar for this reason.

---

## 8. How They Work Together in This Platform

```
         ┌─────────────────────────────────────────────┐
         │  Live trading loop                          │
         │                                             │
         │  Every bar:                                 │
         │    DrawdownMonitor.update(equity)           │
         │      → if drawdown > limit: HALT            │
         │                                             │
         │  Every order:                               │
         │    RiskManager.check_order(order, portfolio)│
         │      → check position limits               │
         │      → check daily loss limit              │
         │      → check correlation concentration     │
         │      → APPROVE / SCALE_DOWN / REJECT        │
         │                                             │
         │  Every day:                                 │
         │    VaR/CVaR recomputed (252-day window)     │
         │    Exposed via GET /api/risk/status          │
         └─────────────────────────────────────────────┘
```

Backtesting metrics (Sharpe, Sortino, Calmar, MDD) are computed post-run by `backtesting/metrics.py` and stored in `BacktestReport`. They serve as strategy selection criteria during walk-forward validation.

Live risk metrics (VaR, CVaR, current drawdown, daily loss) are computed continuously by the `risk/` package and exposed through the API for the dashboard's `RiskPanel` component.
