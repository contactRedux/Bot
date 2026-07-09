"""
strategies/kalman_trend.py — Kalman Filter Trend Following strategy.

Mathematical foundation
-----------------------
We model the unobserved "true" price as a 1-D random walk (Harvey 1989):

    State equation : x_t  = x_{t-1} + w_t,    w_t ~ N(0, Q)
    Obs. equation  : z_t  = x_t     + v_t,    v_t ~ N(0, R)

where
  x_t  — latent true price (state)
  P_t  — state-error variance (how uncertain we are about x_t)
  Q    — process noise variance  (``process_noise``)   controls filter agility
  R    — observation noise variance (``observation_noise``) controls smoothing

Kalman recursion (1-D scalar form)
------------------------------------
Predict:
    x_pred = x_{t-1}
    P_pred = P_{t-1} + Q

Update:
    innovation = z_t - x_pred         # prediction error
    S          = P_pred + R            # innovation variance
    K          = P_pred / S            # Kalman gain  (0 < K < 1)
    x_t        = x_pred + K * innovation
    P_t        = (1 - K) * P_pred

Signal
------
The normalised innovation is:

    signal = innovation / sqrt(S)

It measures how far the current price has moved relative to what the filter
predicted, scaled by the filter's current uncertainty.  Crucially:

  • It adapts to volatility — a 2-point move in a quiet regime produces a
    larger signal than the same move during high-volatility.
  • It is statistically interpretable — under Gaussian noise it is
    approximately N(0,1) when no structural shift is occurring.

Entry rule:
  signal > +entry_threshold  → price surging above filter prediction → BUY
  signal < −entry_threshold  → price falling below filter prediction → SELL

Exit rule:
  |signal| < exit_threshold  → filter has caught up; trend exhausted → CLOSE

References
----------
Harvey, A.C. (1989). Forecasting, Structural Time Series Models and the
    Kalman Filter. Cambridge University Press.
Welch, G. & Bishop, G. (2006). An Introduction to the Kalman Filter.
    UNC-Chapel Hill, TR 95-041.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np  # noqa: F401  (required by contract; available for subclasses / future use)
import pandas as pd

from strategies.base import (  # noqa: F401  (OrderType, TickerState required by contract)
    BaseStrategy,
    Order,
    OrderSide,
    OrderType,
    TickerState,
)

logger = logging.getLogger(__name__)


class KalmanTrendStrategy(BaseStrategy):
    """
    Kalman Filter trend-following strategy.

    Uses a 1-D Kalman filter to track each ticker's latent "true" price.
    The normalised prediction innovation drives entry and exit decisions,
    giving a volatility-adaptive trend signal without lagging indicators.

    Parameters
    ----------
    config : dict
        Strategy parameters.  Recognised keys:

        observation_noise    (R)  float, default 1.0
            Measurement noise variance.  Higher → smoother filter, slower
            reaction to genuine price moves.
        process_noise        (Q)  float, default 0.01
            Process / transition noise variance.  Higher → filter tracks
            price more aggressively (but noisier signal).
        initial_variance     (P₀) float, default 1.0
            Starting state-error variance.  The filter self-corrects quickly
            so the choice matters only for the first few bars.
        entry_threshold           float, default 1.5
            Enter when |normalised innovation| exceeds this level.
        exit_threshold            float, default 0.3
            Exit when |normalised innovation| drops below this level.
        min_bars                  int,   default 20
            Warmup bars before any signal is generated.
        position_size_scale       float, default 1.0
            Multiplier applied to base_position_size.

    tickers : list[str]
        Universe of symbols to monitor.
    base_position_size : float
        Base number of units (shares / coins) for a full-strength signal.
    """

    strategy_id = "kalman_trend"

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        base_position_size: float = 100.0,
    ) -> None:
        super().__init__(self.strategy_id, config, tickers)

        # ── Kalman filter hyperparameters ─────────────────────────────────────
        self._R: float = float(config.get("observation_noise", 1.0))
        self._Q: float = float(config.get("process_noise", 0.01))
        self._P0: float = float(config.get("initial_variance", 1.0))

        # ── Signal thresholds ─────────────────────────────────────────────────
        self.entry_threshold: float = float(config.get("entry_threshold", 1.5))
        self.exit_threshold: float = float(config.get("exit_threshold", 0.3))
        self.min_bars: int = int(config.get("min_bars", 20))
        self.position_size_scale: float = float(config.get("position_size_scale", 1.0))
        self.base_position_size: float = base_position_size

        # ── Per-ticker Kalman filter state ────────────────────────────────────
        # x             : current filter state (price estimate)
        # P             : current state-error variance
        # price_history : list of observed closes (for diagnostics / min_bars gate)
        # bars_seen     : total bars processed for this ticker
        self._kf_state: dict[str, dict[str, Any]] = {
            t: {
                "x": None,       # float | None — None until first bar
                "P": self._P0,
                "price_history": [],
                "bars_seen": 0,
            }
            for t in tickers
        }

        # Pending orders populated in on_bar, consumed in generate_orders
        self._pending_orders: list[Order] = []

    # ── Event hooks ───────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        """
        Process one OHLCV bar: run the Kalman update then evaluate entry/exit.

        Decision flow
        -------------
        1. Initialise filter state on the very first bar (x = close).
        2. Run predict → update to get normalised innovation.
        3. Check exit conditions on any open position.
        4. After ``min_bars`` warmup, check entry conditions.
        5. Append any new orders to ``self._pending_orders``.
        """
        if not self._enabled:
            return

        close = float(bar.get("close", 0.0))
        if close <= 0.0:
            return

        # Ensure per-ticker KF state exists (handles tickers added after init)
        if ticker not in self._kf_state:
            self._kf_state[ticker] = {
                "x": None,
                "P": self._P0,
                "price_history": [],
                "bars_seen": 0,
            }

        kf = self._kf_state[ticker]
        state = self._state_for(ticker)

        # ── Initialise on first observation ───────────────────────────────────
        if kf["x"] is None:
            kf["x"] = close
            kf["P"] = self._P0
            kf["price_history"].append(close)
            kf["bars_seen"] += 1
            return  # Need at least one prior to compute an innovation

        # ── Kalman predict step ───────────────────────────────────────────────
        x_pred: float = kf["x"]
        p_pred: float = kf["P"] + self._Q     # P_pred in Harvey (1989) notation

        # ── Kalman update step ────────────────────────────────────────────────
        innovation: float = close - x_pred    # prediction error  (ν)
        s_innov: float = p_pred + self._R     # S — innovation variance
        k_gain: float = p_pred / s_innov      # K — Kalman gain
        x_new: float = x_pred + k_gain * innovation   # posterior mean
        p_new: float = (1.0 - k_gain) * p_pred        # posterior variance

        # Persist updated state
        kf["x"] = x_new
        kf["P"] = p_new
        kf["price_history"].append(close)
        kf["bars_seen"] += 1

        # ── Normalised innovation ─────────────────────────────────────────────
        # signal ~ N(0,1) in a quiet market; large |signal| flags a structural move
        norm_signal: float = innovation / math.sqrt(s_innov)

        logger.debug(
            "KalmanTrend [%s] close=%.4f x=%.4f P=%.6f K=%.4f inno=%.4f sig=%.3f",
            ticker, close, x_new, p_new, k_gain, innovation, norm_signal,
        )

        # ── Warmup gate ───────────────────────────────────────────────────────
        if kf["bars_seen"] < self.min_bars:
            return

        # ── Exit check (always evaluated regardless of entry gate) ────────────
        if not state.is_flat:
            if abs(norm_signal) < self.exit_threshold:
                exit_side = OrderSide.SELL if state.is_long else OrderSide.BUY
                qty = abs(state.position)
                order = self._make_order(
                    ticker, exit_side, qty, 1.0,
                    reason="kalman_exit",
                    norm_signal=round(norm_signal, 4),
                    x_filter=round(x_new, 4),
                )
                self._pending_orders.append(order)
                state.position = 0.0
                state.entry_price = 0.0
                logger.info(
                    "KalmanTrend [%s] EXIT %s |signal|=%.3f < %.3f",
                    ticker, exit_side.value, abs(norm_signal), self.exit_threshold,
                )
            return  # Position open: wait for exit, don't layer in

        # ── Entry check ───────────────────────────────────────────────────────
        if norm_signal > self.entry_threshold:
            # Price trending strongly upward relative to filter → BUY
            confidence = self._signal_to_confidence(norm_signal)
            qty = self.base_position_size * self.position_size_scale * confidence
            order = self._make_order(
                ticker, OrderSide.BUY, qty, confidence,
                norm_signal=round(norm_signal, 4),
                x_filter=round(x_new, 4),
                innovation=round(innovation, 4),
            )
            self._pending_orders.append(order)
            state.position = qty
            state.entry_price = close
            state.bars_in_position = 0
            logger.info(
                "KalmanTrend [%s] BUY qty=%.2f signal=%.3f conf=%.2f x=%.4f",
                ticker, qty, norm_signal, confidence, x_new,
            )

        elif norm_signal < -self.entry_threshold:
            # Price trending strongly downward relative to filter → SELL SHORT
            confidence = self._signal_to_confidence(norm_signal)
            qty = self.base_position_size * self.position_size_scale * confidence
            order = self._make_order(
                ticker, OrderSide.SELL, qty, confidence,
                norm_signal=round(norm_signal, 4),
                x_filter=round(x_new, 4),
                innovation=round(innovation, 4),
            )
            self._pending_orders.append(order)
            state.position = -qty
            state.entry_price = close
            state.bars_in_position = 0
            logger.info(
                "KalmanTrend [%s] SELL qty=%.2f signal=%.3f conf=%.2f x=%.4f",
                ticker, qty, norm_signal, confidence, x_new,
            )

    def generate_orders(self) -> list[Order]:
        """
        Return and clear all orders queued during the current bar cycle.

        Called once per bar by the StrategyOrchestrator after all
        ``on_bar`` / ``on_news`` / ``on_fundamental`` hooks have run.

        Returns
        -------
        list[Order]
            May be empty if no signal was triggered this bar.
        """
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _signal_to_confidence(self, norm_signal: float) -> float:
        """
        Map a normalised innovation value to a confidence in [0, 1].

        Uses a soft-clamp: confidence starts at 0.5 at the entry threshold
        and saturates toward 1.0 as |signal| → 3 × entry_threshold.

        Parameters
        ----------
        norm_signal : float
            Normalised Kalman innovation (can be negative).

        Returns
        -------
        float
            Confidence value in [0.5, 1.0].
        """
        excess = abs(norm_signal) - self.entry_threshold  # ≥ 0 when called
        scale = self.entry_threshold * 2.0                # width of the ramp
        return min(1.0, 0.5 + 0.5 * (excess / scale))

    def reset(self) -> None:
        """Reset all per-ticker state (call at the start of each backtest run)."""
        super().reset()
        self._kf_state = {
            t: {
                "x": None,
                "P": self._P0,
                "price_history": [],
                "bars_seen": 0,
            }
            for t in self.tickers
        }
        self._pending_orders.clear()
