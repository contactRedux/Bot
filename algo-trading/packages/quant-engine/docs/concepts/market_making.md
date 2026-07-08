# Market Making

> **Code links:** [`strategies/market_making.py`](../../strategies/market_making.py) · [`models/rl_agent.py`](../../models/rl_agent.py)

---

## Table of Contents

1. [What Is a Market Maker?](#1-what-is-a-market-maker)
2. [The Bid-Ask Spread](#2-the-bid-ask-spread)
3. [Inventory Risk](#3-inventory-risk)
4. [The Avellaneda-Stoikov Model](#4-the-avellaneda-stoikov-model)
5. [How the RL Agent Approximates AS Quoting](#5-how-the-rl-agent-approximates-as-quoting)
6. [Implementation in This Platform](#6-implementation-in-this-platform)

---

## 1. What Is a Market Maker?

A **market maker** simultaneously posts a **bid** (price to buy) and an **ask** (price to sell) for a security, earning the spread between them. The business model is volume × spread: if a stock's bid/ask is $99.98 / $100.02 and 10,000 shares trade through you per day, you earn roughly $400/day on that security.

Market makers provide liquidity — without them, buyers and sellers would have to wait for a counterparty. In exchange for this service, exchanges grant market makers rebates and priority access.

The central challenge is **adverse selection**: if someone is aggressively trading against your quote, they probably know something you don't (an information asymmetry). A naive market maker posts tight quotes and bleeds to informed traders.

---

## 2. The Bid-Ask Spread

The spread has three economic components:

1. **Order-processing cost** — fees, infrastructure, regulatory cost. Largely fixed.
2. **Inventory cost** — the risk of holding unwanted position while waiting for a counterparty.
3. **Adverse selection cost** — the expected loss from trading against a better-informed participant.

```
spread = 2 × (order_processing + inventory_risk + adverse_selection)
```

In practice, a market maker quotes:
```
bid = mid_price - δ_bid
ask = mid_price + δ_ask
```

Where `δ_bid` and `δ_ask` are the **half-spreads**. They need not be symmetric: if the maker is long inventory, they tighten the ask (more willing to sell) and widen the bid (less willing to buy more).

---

## 3. Inventory Risk

Inventory is the core operational risk for a market maker. If a maker fills 5,000 buy orders but only 2,000 sell orders in a session, they hold a net long position of 3,000 shares. If the price falls overnight, they suffer a mark-to-market loss independent of their spread earnings.

**Inventory management goals:**
1. Keep net inventory close to zero (flat book)
2. When inventory deviates, skew quotes to attract the offsetting flow
3. Set hard inventory limits beyond which you stop quoting

The **inventory skew** adjustment: if current inventory `q > 0` (long), widen the bid and tighten the ask to encourage selling:

```
δ_ask_adjusted = δ_ask - γ · q
δ_bid_adjusted = δ_bid + γ · q
```

Where `γ` is the inventory-skew sensitivity parameter.

---

## 4. The Avellaneda-Stoikov Model

The **Avellaneda-Stoikov (2008)** stochastic control model is the theoretical foundation of modern algorithmic market making. It solves for the *optimal* bid and ask quotes that maximise expected terminal wealth while controlling inventory risk.

**Assumptions:**
- Mid-price follows arithmetic Brownian motion: `dS = σ dW`
- Order arrivals follow a Poisson process with intensity `λ(δ) = A·e^{-κδ}` (higher spread → fewer fills)
- The market maker maximises: `E[W_T - γ·q_T²·S_T]`

**Optimal reservation price (indifference price):**

```
r(t, q) = S - q · γ · σ² · (T - t)
```

This is the mid-price adjusted for inventory. A long position (`q > 0`) lowers the reservation price — the maker is willing to sell at a discount to flatten the book.

**Optimal half-spread:**

```
δ* = γ · σ² · (T - t) / 2  +  (1/κ) · ln(1 + γ/κ)
```

Where:
- `γ` — risk aversion coefficient
- `σ` — volatility of the mid-price
- `T - t` — time remaining in the trading session
- `κ` — order-arrival sensitivity to spread

Key insights from the AS model:
- Spread is **proportional to volatility** (`σ²`): quote wider in volatile markets
- Spread **widens as T approaches** (less time to flatten inventory)
- Quotes are **asymmetric** based on current inventory `q`

In the platform, the ATR (Average True Range) serves as the volatility proxy for spread sizing — the ATR-proportional fallback in `market_making.py` directly implements the AS insight that spread ∝ σ.

---

## 5. How the RL Agent Approximates AS Quoting

The AS model is derived analytically, but it requires estimates of `γ`, `κ`, and `σ` that are hard to calibrate in real markets. The PPO reinforcement learning agent in `models/rl_agent.py` learns a quoting policy empirically.

**State space (observation):**
- Current inventory `q`
- Spread z-score (current spread vs rolling mean)
- Recent mid-price momentum
- Time-of-day (to capture intraday volatility patterns)
- Realised volatility (σ proxy)

**Action space (7 discrete actions):**
```
0: widen both sides by 2 ticks
1: widen both sides by 1 tick
2: keep spread constant
3: tighten both sides by 1 tick
4: tighten both sides by 2 ticks
5: skew ask tighter (encourage selling — destock inventory)
6: skew bid tighter (encourage buying — restock inventory)
```

**Reward function:**
```
reward = spread_earned - λ·|inventory_change|² - penalty_if_inventory_limit_breached
```

The RL agent learns to approximate the AS reservation price adjustment through the inventory-skew actions (5 and 6), and the AS spread formula through the volatility-dependent widening actions (0–4).

The advantage over the analytical model: the agent adapts to non-Gaussian order flow, intraday patterns, and regime changes that the AS Poisson arrival model ignores.

---

## 6. Implementation in This Platform

```python
# strategies/market_making.py — core quoting logic (simplified)

def on_bar(self, bar, features):
    signal = self.rl_agent.predict(features)   # PPO action 0-6
    mid    = bar.close

    # ATR-proportional base spread (AS insight: spread ∝ volatility)
    half_spread = features["atr"] * self.config.atr_spread_multiplier

    # Inventory skew: long inventory → tighter ask, wider bid
    skew = self.inventory * self.config.inventory_skew_factor
    ask  = mid + half_spread - skew
    bid  = mid - half_spread - skew

    # Post LIMIT orders on both sides
    orders = [
        Order(ticker, SELL, qty, order_type=LIMIT, limit_price=ask),
        Order(ticker, BUY,  qty, order_type=LIMIT, limit_price=bid),
    ]
    return orders
```

**Key config parameters** (in `config/strategy_config.yaml`):

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `atr_spread_multiplier` | 0.5 | Half-spread as fraction of ATR |
| `inventory_skew_factor` | 0.1 | Quote skew per unit of inventory |
| `max_inventory` | 500 | Hard limit; stop quoting if breached |
| `min_spread_bps` | 5 | Floor on spread regardless of ATR |

**Limitations in this implementation:**
- Fills at mid ± half-spread (no order book simulation); real fills depend on queue position
- RL agent is trained in price-replay mode, not against a simulated adversarial order flow
- Single-asset; real market making requires correlated-asset inventory netting
