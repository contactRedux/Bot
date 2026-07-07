"""
models/rl_agent.py — PPO Reinforcement Learning agent for adaptive execution.

Why RL for trading?
-------------------
Classical strategies apply fixed rules ("buy when RSI < 30").  An RL agent
learns a *policy* — a mapping from state to action — by experiencing the
consequences of its decisions through simulated trading.  Key advantages:

1. **Adaptive execution**: the agent can learn when to post bids vs. market
   orders, adapting to intraday volatility patterns.
2. **Multi-objective optimization**: the reward function directly encodes
   the strategy's goals (Sharpe ratio, drawdown penalty, turnover cost).
3. **Jane-Street style**: top quant firms (Jane Street, Two Sigma, Citadel)
   use RL agents specifically for market-making and execution optimization.

PPO (Proximal Policy Optimization)
------------------------------------
PPO is the standard algorithm for continuous-state, discrete-action trading:
* Stable training (clipped surrogate objective prevents large policy updates).
* Works well with sparse rewards (most bars have zero reward).
* Used by stable-baselines3 — battle-tested implementation.

Custom Gym Environment
----------------------
``TradingEnv`` wraps a historical price series in a Gymnasium interface:

    State:  feature_vector (60 dims) + [position, unrealized_pnl, cash_ratio]
    Actions: 0=strong_sell, 1=sell, 2=hold, 3=buy, 4=strong_buy,
             5=post_bid, 6=post_ask
    Reward: risk-adjusted PnL increment (Sharpe-like: ΔPnL / rolling_vol)

Note on BacktestEngine integration
------------------------------------
This module uses a simple ``TradingEnv`` with a price-replay loop.  When
Sub-Task 6 (backtesting engine) is complete, ``TradingEnv`` can be swapped
for the real engine by replacing ``_step_simulation()`` with a call to
``BacktestEngine.step()``.  The PPO agent's interface does not change.

Usage
-----
::

    from models.rl_agent import RLTradingAgent

    agent = RLTradingAgent(
        input_dim=60,
        prices=price_series,      # pd.Series of close prices
        features=feature_matrix,  # pd.DataFrame aligned to prices
    )
    agent.train(total_timesteps=100_000)
    out = agent.predict(current_features)
    print(out.signal, out.direction)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import gymnasium as gym
    from gymnasium import spaces
    _GYM_AVAILABLE = True
except ImportError:
    _GYM_AVAILABLE = False
    logger.warning("Gymnasium not installed — RLTradingAgent unavailable. pip install gymnasium")

try:
    from stable_baselines3 import PPO
    from stable_baselines3.common.env_util import make_vec_env
    _SB3_AVAILABLE = True
except ImportError:
    _SB3_AVAILABLE = False
    logger.warning("stable-baselines3 not installed — RLTradingAgent unavailable. pip install stable-baselines3")

from models.base import BaseSignalModel, SignalOutput


# ---------------------------------------------------------------------------
# Action constants
# ---------------------------------------------------------------------------

ACTIONS = {
    0: "strong_sell",   # Signal = -1.0
    1: "sell",          # Signal = -0.5
    2: "hold",          # Signal =  0.0
    3: "buy",           # Signal = +0.5
    4: "strong_buy",    # Signal = +1.0
    5: "post_bid",      # Signal = +0.3  (passive buy)
    6: "post_ask",      # Signal = -0.3  (passive sell)
}
_ACTION_TO_SIGNAL = {
    0: -1.0,
    1: -0.5,
    2:  0.0,
    3:  0.5,
    4:  1.0,
    5:  0.3,
    6: -0.3,
}
N_ACTIONS = len(ACTIONS)


# ---------------------------------------------------------------------------
# TradingEnv (Gymnasium environment)
# ---------------------------------------------------------------------------

if _GYM_AVAILABLE:
    class TradingEnv(gym.Env):
        """
        Simple price-replay Gymnasium environment for PPO training.

        The environment replays a historical price series bar by bar.
        At each step the agent receives a feature vector + position state
        and selects an action.

        State space
        -----------
        A flat vector of (feature_dim + 3) floats:
            [feature_0, ..., feature_N, position, unrealized_pnl_norm, cash_ratio]

        Action space
        -----------
        Discrete(7) — see ACTIONS dict above.

        Reward
        ------
        Sharpe-like incremental reward:

            reward = ΔPnL / (rolling_vol + ε) - λ * |ΔPosition|

        where:
          - ΔPnL is the step PnL from holding position × price change
          - rolling_vol is a 20-step rolling std of returns (risk normalization)
          - λ * |ΔPosition| is a transaction cost penalty

        Parameters
        ----------
        prices : np.ndarray
            Close prices, shape (n_bars,).
        features : np.ndarray
            Feature matrix, shape (n_bars, feature_dim).
        initial_cash : float
            Starting capital.
        transaction_cost : float
            Proportional cost per trade (e.g. 0.001 = 10 bps).
        max_position : float
            Maximum position size as fraction of capital.
        reward_vol_window : int
            Window for rolling volatility normalization.
        """

        metadata = {"render_modes": []}

        def __init__(
            self,
            prices: np.ndarray,
            features: np.ndarray,
            initial_cash: float = 10_000.0,
            transaction_cost: float = 0.001,
            max_position: float = 1.0,
            reward_vol_window: int = 20,
        ) -> None:
            super().__init__()
            assert len(prices) == len(features), "prices and features must have same length"
            assert len(prices) > reward_vol_window + 1, "Too few bars for this env"

            self.prices = prices.astype(np.float32)
            self.features = features.astype(np.float32)
            self.initial_cash = float(initial_cash)
            self.transaction_cost = float(transaction_cost)
            self.max_position = float(max_position)
            self.reward_vol_window = reward_vol_window

            feature_dim = features.shape[1]
            # Observation: features + [position_norm, unrealized_pnl_norm, cash_ratio]
            obs_dim = feature_dim + 3
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
            )
            self.action_space = spaces.Discrete(N_ACTIONS)

            self._n = len(prices)
            self._step_idx: int = 0
            self._position: float = 0.0       # shares held (fractional)
            self._cash: float = initial_cash
            self._entry_price: float = 0.0
            self._recent_returns: list[float] = []

        def reset(
            self,
            *,
            seed: int | None = None,
            options: dict | None = None,
        ) -> tuple[np.ndarray, dict]:
            super().reset(seed=seed)
            self._step_idx = self.reward_vol_window  # start after warm-up
            self._position = 0.0
            self._cash = self.initial_cash
            self._entry_price = self.prices[self._step_idx]
            self._recent_returns = []
            return self._get_obs(), {}

        def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
            price_now = self.prices[self._step_idx]
            price_prev = self.prices[self._step_idx - 1] if self._step_idx > 0 else price_now
            ret = (price_now - price_prev) / (price_prev + 1e-8)

            # Determine target position from action
            target_pos_frac = _ACTION_TO_SIGNAL[int(action)]
            max_shares = (self._cash / (price_now + 1e-8)) * self.max_position
            target_shares = target_pos_frac * max_shares

            # Trade execution
            delta = target_shares - self._position
            trade_value = abs(delta) * price_now
            trade_cost = trade_value * self.transaction_cost
            self._position = target_shares
            self._cash -= trade_cost

            # Step PnL: position × price_change − costs
            pnl = self._position * (price_now - price_prev) - trade_cost

            # Rolling volatility for Sharpe normalisation
            self._recent_returns.append(ret)
            if len(self._recent_returns) > self.reward_vol_window:
                self._recent_returns.pop(0)
            rolling_vol = float(np.std(self._recent_returns)) + 1e-6

            reward = float(pnl / rolling_vol) - 0.01 * abs(delta / (max_shares + 1e-6))

            self._step_idx += 1
            done = self._step_idx >= self._n - 1
            obs = self._get_obs()

            info = {
                "price": price_now,
                "position": self._position,
                "cash": self._cash,
                "pnl": pnl,
                "action_name": ACTIONS[int(action)],
            }
            return obs, reward, done, False, info

        def _get_obs(self) -> np.ndarray:
            features = self.features[self._step_idx]
            price = self.prices[self._step_idx]
            total_value = self._cash + self._position * price
            unrealized_pnl_norm = float(
                (self._position * (price - self._entry_price)) / (total_value + 1e-8)
            )
            cash_ratio = float(self._cash / (total_value + 1e-8))
            position_norm = float(
                self._position * price / (self.initial_cash + 1e-8)
            )
            extra = np.array(
                [position_norm, unrealized_pnl_norm, cash_ratio], dtype=np.float32
            )
            return np.concatenate([features, extra])

        def render(self) -> None:
            pass


# ---------------------------------------------------------------------------
# RLTradingAgent
# ---------------------------------------------------------------------------

class RLTradingAgent(BaseSignalModel):
    """
    PPO reinforcement learning agent for adaptive trading execution.

    Parameters
    ----------
    input_dim : int
        Number of input features (must match features array width).
    prices : pd.Series or np.ndarray
        Historical close prices for environment simulation.
    features : pd.DataFrame or np.ndarray
        Feature matrix aligned to prices.
    total_timesteps : int
        Default training timesteps for ``train()``.
    policy : str
        SB3 policy network type.  ``'MlpPolicy'`` is standard for flat features.
    n_steps : int
        PPO rollout length per environment.
    batch_size : int
        PPO mini-batch size.
    n_epochs : int
        PPO optimization epochs per rollout.
    learning_rate : float
        PPO learning rate.
    gamma : float
        Discount factor.
    device : str
        Torch device string.
    """

    def __init__(
        self,
        input_dim: int,
        prices: pd.Series | np.ndarray | None = None,
        features: pd.DataFrame | np.ndarray | None = None,
        total_timesteps: int = 100_000,
        policy: str = "MlpPolicy",
        n_steps: int = 2048,
        batch_size: int = 64,
        n_epochs: int = 10,
        learning_rate: float = 3e-4,
        gamma: float = 0.99,
        device: str = "cpu",
    ) -> None:
        super().__init__(input_dim)
        if not (_GYM_AVAILABLE and _SB3_AVAILABLE):
            raise ImportError(
                "gymnasium and stable-baselines3 are required. "
                "Install with: pip install gymnasium stable-baselines3"
            )

        self.total_timesteps = total_timesteps
        self.policy = policy
        self.n_steps = n_steps
        self.batch_size = batch_size
        self.n_epochs = n_epochs
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.device = device

        self._prices: np.ndarray | None = self._to_numpy(prices) if prices is not None else None
        self._features: np.ndarray | None = self._to_numpy(features) if features is not None else None
        self._ppo: "PPO | None" = None

    @property
    def model_id(self) -> str:
        return "rl_ppo_agent"

    # ── Training ─────────────────────────────────────────────────────────────

    def train(  # type: ignore[override]
        self,
        X: pd.DataFrame | np.ndarray | None = None,
        y: pd.Series | np.ndarray | None = None,
        prices: pd.Series | np.ndarray | None = None,
        features: pd.DataFrame | np.ndarray | None = None,
        total_timesteps: int | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Train the PPO agent on a historical price+feature environment.

        Parameters
        ----------
        X : ignored (RL doesn't use supervised labels)
        y : ignored
        prices : pd.Series or np.ndarray, optional
            Override self._prices.
        features : pd.DataFrame or np.ndarray, optional
            Override self._features.
        total_timesteps : int, optional
            Override self.total_timesteps.
        """
        prices_arr = self._to_numpy(prices) if prices is not None else self._prices
        features_arr = self._to_numpy(features) if features is not None else self._features
        ts = total_timesteps if total_timesteps is not None else self.total_timesteps

        if prices_arr is None or features_arr is None:
            raise ValueError(
                "prices and features must be provided either in __init__ or train()"
            )

        env = TradingEnv(prices=prices_arr, features=features_arr)

        self._ppo = PPO(
            policy=self.policy,
            env=env,
            n_steps=self.n_steps,
            batch_size=self.batch_size,
            n_epochs=self.n_epochs,
            learning_rate=self.learning_rate,
            gamma=self.gamma,
            device=self.device,
            verbose=0,
        )
        self._ppo.learn(total_timesteps=ts)
        self._is_trained = True
        logger.info("RLTradingAgent trained for %d timesteps", ts)

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame | np.ndarray, **kwargs: Any) -> SignalOutput:
        """
        Predict action from the latest feature row.

        Parameters
        ----------
        X : array, shape (n, input_dim) or (input_dim,)
            Feature vector for the current bar.
            Uses the last row.
        position : float, optional
            Current position fraction (0.0 if not provided).
        unrealized_pnl : float, optional
            Normalized unrealized PnL (0.0 if not provided).
        cash_ratio : float, optional
            Cash as fraction of portfolio (1.0 if not provided).
        """
        self._assert_trained()

        X_arr = self._to_numpy(X)
        if X_arr.ndim == 2:
            X_arr = X_arr[-1]  # take last row

        position = float(kwargs.get("position", 0.0))
        unrealized_pnl = float(kwargs.get("unrealized_pnl", 0.0))
        cash_ratio = float(kwargs.get("cash_ratio", 1.0))

        obs = np.concatenate([
            X_arr.astype(np.float32),
            np.array([position, unrealized_pnl, cash_ratio], dtype=np.float32),
        ])

        action, _ = self._ppo.predict(obs, deterministic=True)
        action = int(action)

        signal = float(_ACTION_TO_SIGNAL[action])
        # Confidence: 1.0 for strong actions, 0.5 for passive/mild, 0.3 for hold
        confidence_map = {0: 0.9, 1: 0.6, 2: 0.3, 3: 0.6, 4: 0.9, 5: 0.5, 6: 0.5}
        confidence = confidence_map[action]

        return SignalOutput(
            signal=signal,
            confidence=confidence,
            model_id=self.model_id,
            metadata={"action": action, "action_name": ACTIONS[action]},
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._ppo.save(str(path / "ppo_model"))
        meta = {
            "input_dim": self.input_dim,
            "policy": self.policy,
            "n_steps": self.n_steps,
            "batch_size": self.batch_size,
            "n_epochs": self.n_epochs,
            "learning_rate": self.learning_rate,
            "gamma": self.gamma,
            "device": self.device,
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        logger.info("RLTradingAgent saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "RLTradingAgent":
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        agent = cls(**meta)
        agent._ppo = PPO.load(str(path / "ppo_model"))
        agent._is_trained = True
        return agent

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_numpy(arr: Any) -> np.ndarray | None:
        if arr is None:
            return None
        if isinstance(arr, (pd.DataFrame, pd.Series)):
            return arr.to_numpy(dtype=np.float32)
        return np.asarray(arr, dtype=np.float32)
