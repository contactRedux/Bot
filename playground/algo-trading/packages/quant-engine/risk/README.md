# Risk Management — Concepts & Reference Guide

This document explains every metric and concept used by the `risk/` package.
Each section links to the code that implements it.

---

## Table of Contents

1. [Value-at-Risk (VaR)](#value-at-risk-var)
2. [CVaR / Expected Shortfall](#cvar--expected-shortfall)
3. [Sharpe Ratio](#sharpe-ratio)
4. [Sortino Ratio](#sortino-ratio)
5. [Calmar Ratio](#calmar-ratio)
6. [Maximum Drawdown](#maximum-drawdown)
7. [Correlation Concentration](#correlation-concentration)
8. [Position Limits](#position-limits)
9. [Daily Loss Limit](#daily-loss-limit)
10. [Architectural Flow](#architectural-flow)

---

## Value-at-Risk (VaR)

**Implementation:** [`risk/var.py`](var.py)

**Question answered:** *"What is the maximum loss I should expect over one day, with X% confidence?"*

### Formula (Historical Simulation)

Given a rolling window of N daily portfolio returns `r_1, r_2, …, r_N` (sorted ascending):

```
VaR(95%, 1-day) = −percentile(returns, 5th) × portfolio_value
VaR(99%, 1-day) = −percentile(returns, 1st) × portfolio_value
```

### Why Historical Simulation?

The parametric (normal distribution) VaR assumes returns are normally distributed. They are not — equity returns have **fat tails**: crashes happen far more often than a normal distribution predicts (the 2008 financial crisis was a ~25σ event under a normal model).

Historical Simulation uses the *actual* empirical distribution, capturing fat tails automatically.

### Worked Example

Rolling 252-day return window. Sorted losses (worst → best):

```
Day rank | Return   | Cumulative %
---------|----------|-------------
1        | −4.2%    | 0.4%
2        | −3.8%    | 0.8%
...
5        | −2.9%    | 2.0%
...
13       | −1.5%    | 5.2%   ← VaR(95%) threshold
```

For a $100,000 portfolio:
- **VaR(95%)** ≈ $1,500 — with 95% confidence, you won't lose more than $1,500 today
- **VaR(99%)** ≈ $2,900 — with 99% confidence, you won't lose more than $2,900 today

---

## CVaR / Expected Shortfall

**Implementation:** [`risk/var.py`](var.py)

**Question answered:** *"Given I'm in the worst 5% of scenarios, how bad is it on average?"*

### Formula

```
CVaR(95%) = mean(all returns where return ≤ VaR(95%) return)
           × portfolio_value
```

### Why CVaR > VaR

VaR is a threshold — it tells you the *boundary* of the bad scenarios but nothing about their severity. CVaR tells you the *expected damage* beyond that boundary.

Example:
- Strategy A: always loses exactly $1,500 when it has a bad day
- Strategy B: usually fine, but occasionally loses $15,000

Both have the same VaR(95%) = $1,500. But CVaR(95%) distinguishes them:
- Strategy A CVaR = $1,500
- Strategy B CVaR = $15,000

Strategy B is far riskier. CVaR catches this. **CVaR is now required by Basel III for bank capital calculations.**

---

## Sharpe Ratio

**Implementation:** [`backtesting/metrics.py`](../backtesting/metrics.py)

**Question answered:** *"How much return am I earning per unit of total volatility?"*

### Formula

```
Sharpe = (mean(daily_returns) − rf_daily) / std(daily_returns) × √252
```

Where `rf_daily` = daily risk-free rate (e.g., 5% annual → 0.019% daily).

### Interpretation

| Sharpe | Meaning |
|--------|---------|
| < 0    | Strategy loses money on a risk-adjusted basis |
| 0–1    | Acceptable; barely compensated for risk |
| 1–2    | Good; actively managed funds typically target this |
| 2–3    | Excellent; top-tier hedge fund territory |
| > 3    | Exceptional or overfitted (verify in live trading) |

**Limitation:** Sharpe penalises upside volatility equally with downside. A strategy that sometimes has 5% up-days will look riskier than one that grinds out +0.1% every day.

---

## Sortino Ratio

**Implementation:** [`backtesting/metrics.py`](../backtesting/metrics.py)

**Question answered:** *"How much return am I earning per unit of **downside** volatility?"*

### Formula

```
Sortino = (mean(daily_returns) − rf_daily) / downside_std × √252

downside_std = std(returns[returns < 0])   # only negative returns
```

### Why Sortino > Sharpe for Trend-Following

Momentum strategies have asymmetric return distributions — many small losses and occasional large gains. Their Sharpe looks low (high variance from the gains) but their Sortino looks high (downside vol is small). Sortino gives them proper credit.

---

## Calmar Ratio

**Implementation:** [`backtesting/metrics.py`](../backtesting/metrics.py)

**Question answered:** *"How much annual return am I earning per unit of maximum historical pain?"*

### Formula

```
Calmar = CAGR / |max_drawdown|
```

### Interpretation

A Calmar of 1.0 means you earn one year of CAGR for every maximum drawdown you've experienced. A Calmar of 2.0 means you earn twice your max drawdown per year — an efficient use of pain tolerance.

Calmar is especially useful for trend-following strategies where drawdowns can be large but recovery is reliably fast.

---

## Maximum Drawdown

**Implementation:** [`backtesting/metrics.py`](../backtesting/metrics.py), [`risk/monitor.py`](monitor.py)

**Question answered:** *"What is the worst peak-to-trough decline in portfolio value I would have experienced?"*

### Formula

```
drawdown(t) = (peak(0..t) − equity(t)) / peak(0..t)

max_drawdown = max(drawdown(t)) for all t
```

### Why it Matters More than Volatility

A strategy can have zero volatility during a slow bleed — standard deviation misses this. Max drawdown directly answers: *"Could I have stomached this strategy?"*

For a retail trader with limited capital:
- A 20% drawdown on a $100,000 account = $20,000 real loss
- You need a 25% gain from the trough just to get back to flat
- Psychologically, most people sell at the bottom of the drawdown — eliminating all future gains

**Rule of thumb:** size positions so that a 3× max drawdown of your worst historical period still leaves you solvent.

---

## Correlation Concentration

**Implementation:** [`risk/correlation.py`](correlation.py)

**Question answered:** *"Are my positions actually diversified, or are they secretly one big correlated bet?"*

### Formula

```
ρ(A, B) = cov(r_A, r_B) / (σ_A × σ_B)     [Pearson correlation]
```

Computed over a rolling 60-day window of daily returns.

### The Hidden Risk

Consider a portfolio that holds:
- AAPL (25% of capital)
- MSFT (25% of capital)
- GOOGL (25% of capital)
- SPY ETF (25% of capital)

This appears to be a 4-way diversified portfolio. In reality:
- AAPL/MSFT/GOOGL all have ρ ≈ 0.80 with each other
- All three have ρ ≈ 0.85 with SPY

During a market crash, they all fall simultaneously. The portfolio behaves like a 100% single-stock position.

### How This System Handles It

When ρ(A, B) ≥ 0.70 threshold:
- Position scale factor = `max(0.25, 1.0 − (ρ − 0.70))`
- At ρ = 0.85: scale = `max(0.25, 1.0 − 0.15) = 0.85` (15% reduction)
- At ρ = 0.95: scale = `max(0.25, 1.0 − 0.25) = 0.75` (25% reduction)
- At ρ = 1.00: scale = 0.25 (75% reduction — near-duplicate position)

---

## Position Limits

**Implementation:** [`risk/manager.py`](manager.py), [`risk/limits.py`](limits.py)

### Max Position Per Asset (`max_position_pct`)

```
max_qty(ticker) = total_capital × max_position_pct / mark_price
```

Default: 10% — no single position may represent more than 10% of the portfolio.

**Why 10%?** Kelly Criterion analysis with typical Sharpe ratios of 0.5–1.5 suggests optimal position sizes of 5–15%. Capping at 10% is conservative enough to survive estimation errors while still allowing meaningful returns.

### Max Strategy Allocation (`max_strategy_allocation`)

```
max_strategy_value = total_capital × max_strategy_allocation
```

Default: 30% — no single strategy may control more than 30% of capital.

This prevents a rogue strategy (e.g., one trained in a different market regime) from dominating the portfolio when its signals are wrong.

---

## Daily Loss Limit

**Implementation:** [`risk/monitor.py`](monitor.py), [`risk/limits.py`](limits.py)

```
daily_loss = (day_open_equity − current_equity) / day_open_equity
```

If `daily_loss ≥ max_daily_loss_pct`, all new orders are rejected for the rest of the calendar day.

Default: 2% — a $100,000 portfolio halts if it loses more than $2,000 in one day.

**Why 2%?** A 2% daily loss cap limits the worst-case monthly loss (assuming independent days) to approximately:

```
max_monthly_loss ≈ 2% × sqrt(22 trading days) ≈ 9.4%
```

This is painful but survivable. Without a daily loss cap, a single bad day with leveraged positions can cause losses of 10–20% in hours.

---

## Architectural Flow

```
StrategyOrchestrator.process_bar(ticker, bar, features)
    │
    └─► list[Order]   (weight-scaled, aggregated, position-limited)
            │
            ▼
    RiskManager.check_order(order, portfolio)
    ├── [REJECT]     DrawdownMonitor halted → stop
    ├── [REJECT]     Dust order → drop
    ├── [REJECT]     Daily loss limit hit → drop for today
    ├── [SCALE_DOWN] Position limit → reduce qty
    ├── [SCALE_DOWN] Strategy allocation cap → reduce qty
    ├── [SCALE_DOWN] Correlation concentration → reduce qty
    └── [APPROVE]    All checks pass → forward
            │
            ▼
    ExecutionBroker.submit_order(decision.order)
    (SimulatedBroker in backtesting / AlpacaBroker / BinanceBroker in live)
```

The `DrawdownMonitor` runs in parallel, receiving every `portfolio.mark()` call:

```
portfolio.mark(prices, timestamp)
    └─► DrawdownMonitor.update(current_equity, timestamp)
            ├── Updates peak equity
            ├── Checks drawdown vs max_drawdown_pct
            ├── Checks daily loss vs max_daily_loss_pct
            └── Sets is_halted = True if either limit breached
                    └─► RiskManager.check_order() returns REJECT on next call
```
