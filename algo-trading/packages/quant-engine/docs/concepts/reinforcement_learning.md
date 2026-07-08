# Reinforcement Learning for Trading

> **Code links:** [`models/rl_agent.py`](../../models/rl_agent.py) · [`backtesting/engine.py`](../../backtesting/engine.py)

---

## Table of Contents

1. [Markov Decision Processes](#1-markov-decision-processes)
2. [The Bellman Equation](#2-the-bellman-equation)
3. [Policy Gradient Methods](#3-policy-gradient-methods)
4. [Proximal Policy Optimization (PPO)](#4-proximal-policy-optimization-ppo)
5. [Reward Shaping for Trading](#5-reward-shaping-for-trading)
6. [The TradingEnv Gymnasium Wrapper](#6-the-tradingenv-gymnasium-wrapper)
7. [Why RL Instead of Supervised Learning?](#7-why-rl-instead-of-supervised-learning)

---

## 1. Markov Decision Processes

A **Markov Decision Process (MDP)** is the mathematical framework for sequential decision-making under uncertainty. It consists of:

- **State space S** — all possible observations of the environment (e.g., price, inventory, features)
- **Action space A** — all decisions the agent can make (e.g., buy/sell/hold, quote widths)
- **Transition function P(s'|s, a)** — probability of moving to state `s'` after taking action `a` in state `s`
- **Reward function R(s, a, s')** — scalar feedback signal
- **Discount factor γ ∈ [0, 1)** — how much future rewards are worth relative to immediate ones

The **Markov property**: `P(s_{t+1}|s_t, a_t, s_{t-1}, a_{t-1}, ...) = P(s_{t+1}|s_t, a_t)` — the future depends only on the current state, not the full history. In trading, this is an approximation: we engineer the state to include enough lookback (e.g., rolling features) to satisfy it approximately.

The agent's goal: find a **policy** `π(a|s)` (a distribution over actions given state) that maximises expected discounted cumulative reward:

```
J(π) = E_π [ Σ_{t=0}^{∞} γ^t · R(s_t, a_t) ]
```

---

## 2. The Bellman Equation

The **value function** `V^π(s)` is the expected return from state `s` when following policy `π`:

```
V^π(s) = E_π [ R(s, a) + γ · V^π(s') ]
```

The **optimal value function** `V*(s)` satisfies the **Bellman optimality equation**:

```
V*(s) = max_a [ R(s, a) + γ · Σ_{s'} P(s'|s,a) · V*(s') ]
```

The **action-value function** (Q-function) is more directly useful:

```
Q*(s, a) = R(s, a) + γ · Σ_{s'} P(s'|s,a) · max_{a'} Q*(s', a')
```

Given `Q*`, the optimal policy is: `π*(s) = argmax_a Q*(s, a)`.

In deep RL, a neural network approximates `Q(s, a; θ)` (DQN) or the policy `π(a|s; θ)` directly (policy gradient). The Bellman equation provides the training target.

---

## 3. Policy Gradient Methods

**Value-based methods** (DQN) work well for discrete action spaces but struggle with continuous actions. **Policy gradient** methods directly parameterise the policy as a neural network `π_θ(a|s)` and optimise `θ` by gradient ascent on `J(π_θ)`.

**REINFORCE gradient** (Monte Carlo policy gradient):

```
∇_θ J(π_θ) = E_π [ ∇_θ log π_θ(a|s) · G_t ]
```

Where `G_t = Σ_{k=0}^{T-t} γ^k · R_{t+k}` is the return from time `t`.

**Actor-Critic** reduces variance by subtracting a baseline (the critic's value estimate):

```
∇_θ J ≈ ∇_θ log π_θ(a|s) · A(s, a)
```

Where the **advantage** `A(s, a) = Q(s, a) - V(s)` tells you how much better action `a` is than average.

---

## 4. Proximal Policy Optimization (PPO)

PPO (Schulman et al., 2017) is the algorithm used in `models/rl_agent.py`. It is an on-policy actor-critic method with a clipped objective that prevents destructively large policy updates.

**Objective:**

```
L^CLIP(θ) = E_t [ min( r_t(θ) · A_t,  clip(r_t(θ), 1-ε, 1+ε) · A_t ) ]
```

Where:
- `r_t(θ) = π_θ(a_t|s_t) / π_θ_old(a_t|s_t)` — probability ratio (new vs old policy)
- `ε` — clip parameter (default 0.2) — limits how much the policy can change in one update
- `A_t` — advantage estimate

**Why the clip?** Without it, the policy might take a single large gradient step that overshoots and collapses performance. The clip enforces a "trust region" — you can only move the policy a bounded amount per update.

**Full PPO loss:**

```
L(θ) = L^CLIP(θ) - c₁ · L^VF(θ) + c₂ · S[π_θ](s_t)
```

Where:
- `L^VF` — value function loss (mean-squared error)
- `S[π_θ]` — entropy bonus (encourages exploration)
- `c₁, c₂` — coefficients (default 0.5, 0.01)

**Training loop (stable-baselines3):**
1. Collect `n_steps` of experience with current policy
2. Compute advantages using GAE (Generalised Advantage Estimation)
3. Run `n_epochs` of mini-batch gradient updates with the clipped objective
4. Repeat

```python
# models/rl_agent.py
from stable_baselines3 import PPO

model = PPO(
    "MlpPolicy",
    env=TradingEnv(bars=train_bars, features=train_features),
    learning_rate=3e-4,
    n_steps=2048,
    batch_size=64,
    n_epochs=10,
    gamma=0.99,
    clip_range=0.2,
    verbose=0,
)
model.learn(total_timesteps=100_000)
```

---

## 5. Reward Shaping for Trading

The reward function is the single most important design decision in trading RL. Common choices:

| Reward | Formula | Problem |
|--------|---------|---------|
| Raw PnL | `ΔPortfolio` | High variance; encourages gambling |
| Sharpe increment | `r_t / σ_returns` | More stable; penalises volatility |
| Log return | `log(P_t / P_{t-1})` | Numerically stable; ignores risk |
| **Sharpe-like (used here)** | `ΔPnL / rolling_std(ΔPnL)` | Best balance of return and risk |

The platform's `TradingEnv` reward:

```python
# models/rl_agent.py
pnl_change = current_portfolio_value - prev_portfolio_value
self._pnl_history.append(pnl_change)

if len(self._pnl_history) >= 10:
    mu  = np.mean(self._pnl_history)
    std = np.std(self._pnl_history) + 1e-8
    reward = mu / std          # Sharpe-like increment
else:
    reward = pnl_change / (abs(pnl_change) + 1e-8)   # sign-only early on
```

**Inventory penalty** (for market-making mode):

```python
reward -= self.inventory_penalty * abs(self.inventory)
```

This mimics the AS model's quadratic inventory cost, pushing the agent toward flat positions.

---

## 6. The TradingEnv Gymnasium Wrapper

`TradingEnv` in `models/rl_agent.py` wraps historical bar data as a Gymnasium environment.

```
Observation:  [price_features (N), position, cash_ratio, time_of_day]
Action:       Discrete(7)
   0 = strong sell   (-1.0 × base_qty)
   1 = moderate sell (-0.5 × base_qty)
   2 = light sell    (-0.25 × base_qty)
   3 = hold          (0)
   4 = light buy     (+0.25 × base_qty)
   5 = moderate buy  (+0.5 × base_qty)
   6 = strong buy    (+1.0 × base_qty)
Episode:      One full backtest period (reset starts a new period)
Terminal:     End of data, OR drawdown > max_drawdown_pct
```

Two modes (selected at construction time):

1. **Price-replay mode** (default) — steps through pre-loaded bars; fast, suitable for training
2. **Engine mode** — delegates to `BacktestEngine.step()` for full order simulation including fills, commissions, and slippage

---

## 7. Why RL Instead of Supervised Learning?

Supervised learning for trading predicts the *next return* and acts on that prediction. The problem: the prediction target (`next return`) is independent of the *actions taken*. A supervised model doesn't account for the fact that your trades move prices, and it is optimised to minimise prediction error, not to maximise risk-adjusted PnL.

RL directly optimises the objective you care about — cumulative risk-adjusted returns — accounting for the sequential nature of decisions. The agent learns:
- *When* to trade (not just direction)
- *How much* to trade (position sizing)
- *When to stay flat* (the hardest lesson for any model)

The trade-off: RL requires far more data and compute, is sensitive to reward design, and can overfit to the training period. This platform uses RL only for market-making (where the action-feedback loop is tight) and augments supervised signal models (LSTM, Transformer) with RL for position sizing in momentum/mean-reversion strategies.
