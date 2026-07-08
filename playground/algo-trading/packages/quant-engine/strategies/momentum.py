"""
strategies/momentum.py — Trend-following momentum strategy.

Strategy logic
--------------
Momentum is one of the most robust and well-documented phenomena in finance.
Jegadeesh & Titman (1993) showed that stocks with strong past 12-month returns
continue to outperform for 3–12 months.  We exploit this using ML models:

1. Call LSTM + Transformer models on the latest feature window.
2. The EnsembleModel blends their outputs into a single signal ∈ [-1, +1].
3. If |signal| > entry_threshold AND confidence > min_confidence:
   - signal > 0 → BUY (go long)
   - signal < 0 → SELL (go short / close long)
4. Scale position size by confidence: larger position for higher-conviction signals.

Cooldown mechanism
------------------
A cooldown prevents the strategy from churning after each signal.  After an
entry, the strategy waits ``cooldown_bars`` bars before re-evaluating.  This
reduces transaction costs and prevents whipsawing in choppy markets.

Stop-loss / take-profit
-----------------------
Exit rules are based on unrealised PnL relative to entry price:
    Stop-loss  : exit if price falls > stop_loss_pct below entry
    Take-profit: exit if price rises > take_profit_pct above entry

ADX filter
----------
ADX > 20 indicates a trending market (favourable for momentum).
We skip signals when ADX < 20 to avoid entering momentum trades in ranging markets.

Configuration (strategy_config.yaml)
-------------------------------------
    entry_threshold   : minimum |signal| to enter  (default 0.55)
    min_confidence    : minimum model confidence     (default 0.60)
    cooldown_bars     : bars between entries         (default 4)
    stop_loss_pct     : stop-loss fraction           (default 0.02)
    take_profit_pct   : take-profit fraction         (default 0.05)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    LSTM + Transformer ensemble momentum strategy.

    Parameters
    ----------
    config : dict
        From strategy_config.yaml ``momentum`` section.
    tickers : list[str]
        Universe of symbols to monitor.
    lstm_model : BaseSignalModel, optional
        Pre-trained LSTMForecaster.  If None, no momentum signal is produced.
    transformer_model : BaseSignalModel, optional
        Pre-trained TransformerSignalModel.  If None, only LSTM is used.
    ensemble_model : EnsembleModel, optional
        Meta-learner blending LSTM + Transformer outputs.
    base_position_size : float
        Base number of units per full-confidence signal.
    """

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        lstm_model: Any | None = None,
        transformer_model: Any | None = None,
        ensemble_model: Any | None = None,
        base_position_size: float = 100.0,
    ) -> None:
        super().__init__("momentum", config, tickers)
        self._lstm = lstm_model
        self._transformer = transformer_model
        self._ensemble = ensemble_model
        self.base_position_size = base_position_size

        self.entry_threshold: float = config.get("entry_threshold", 0.55)
        self.min_confidence: float = config.get("min_confidence", 0.60)
        self.cooldown_bars: int = int(config.get("cooldown_bars", 4))
        self.stop_loss_pct: float = config.get("stop_loss_pct", 0.02)
        self.take_profit_pct: float = config.get("take_profit_pct", 0.05)

        # Pending orders generated in on_bar, consumed in generate_orders
        self._pending_orders: list[Order] = []

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        """
        Process a new bar: query models, check entry/exit conditions.

        Decision flow per bar:
            1. Check stop-loss / take-profit on existing position.
            2. Decrement cooldown counter.
            3. If cooldown > 0 or no models available: skip.
            4. Apply ADX trend-strength filter (ADX column must be in features).
            5. Compute ensemble signal.
            6. If |signal| > threshold and confidence > min_confidence: emit order.
        """
        if not self._enabled:
            return

        state = self._state_for(ticker)
        close = float(bar.get("close", bar.iloc[0] if len(bar) == 1 else 0))

        # ── Step 1: Check exit conditions on open positions ───────────────────
        exit_orders = self._check_exits(ticker, close, state)
        self._pending_orders.extend(exit_orders)
        if exit_orders:
            return  # Don't enter on the same bar as an exit

        # ── Step 2: Cooldown ──────────────────────────────────────────────────
        if state.cooldown_bars_remaining > 0:
            state.cooldown_bars_remaining -= 1
            return

        # ── Step 3: Require at least one model ───────────────────────────────
        if self._lstm is None and self._transformer is None:
            return

        # ── Step 4: ADX trend filter ──────────────────────────────────────────
        adx = self._get_feature(features, "adx")
        if adx is not None and adx < 20.0:
            # Choppy market — momentum strategies perform poorly without trend
            logger.debug("Momentum [%s] skipping: ADX=%.1f < 20 (no trend)", ticker, adx)
            return

        # ── Step 5: Compute signal ────────────────────────────────────────────
        signal, confidence = self._compute_signal(features)
        state.last_signal = signal
        state.last_confidence = confidence

        # ── Step 6: Entry check ───────────────────────────────────────────────
        if abs(signal) < self.entry_threshold or confidence < self.min_confidence:
            return

        if not state.is_flat:
            # Already in position — only re-enter if signal has reversed
            if (signal > 0 and state.is_long) or (signal < 0 and state.is_short):
                return

        side = OrderSide.BUY if signal > 0 else OrderSide.SELL
        qty = self.base_position_size * confidence  # scale size by conviction

        order = self._make_order(ticker, side, qty, confidence,
                                 signal=round(signal, 4), adx=adx)
        self._pending_orders.append(order)

        # Update state
        state.position = qty if side == OrderSide.BUY else -qty
        state.entry_price = close
        state.bars_in_position = 0
        state.cooldown_bars_remaining = self.cooldown_bars

        logger.info(
            "Momentum [%s] %s qty=%.1f signal=%.3f conf=%.2f",
            ticker, side.value, qty, signal, confidence,
        )

    def generate_orders(self) -> list[Order]:
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compute_signal(self, features: pd.DataFrame) -> tuple[float, float]:
        """
        Run LSTM + Transformer models and blend outputs.

        Returns (signal, confidence) both in [0,1] / [-1,1] range.
        """
        X = features.to_numpy(dtype=np.float32)

        signals, confidences = [], []

        if self._lstm is not None and self._lstm.is_trained:
            out = self._lstm.predict(X)
            signals.append(out.signal)
            confidences.append(out.confidence)

        if self._transformer is not None and self._transformer.is_trained:
            out = self._transformer.predict(X)
            signals.append(out.signal)
            confidences.append(out.confidence)

        if not signals:
            return 0.0, 0.0

        # If ensemble is available and both models have predicted, use it
        if self._ensemble is not None and self._ensemble.is_trained and len(signals) >= 2:
            out = self._ensemble.predict_from_outputs(
                np.array(signals[:2]), np.array(confidences[:2])
            )
            return out.signal, out.confidence

        # Fallback: simple average
        return float(np.mean(signals)), float(np.mean(confidences))

    def _check_exits(
        self, ticker: str, close: float, state: TickerState
    ) -> list[Order]:
        """Emit exit orders if stop-loss or take-profit is triggered."""
        if state.is_flat or state.entry_price <= 0:
            return []

        pnl_pct = (close - state.entry_price) / state.entry_price
        if state.is_short:
            pnl_pct = -pnl_pct  # For short: profit when price falls

        orders = []
        reason = None

        if pnl_pct <= -self.stop_loss_pct:
            reason = "stop_loss"
        elif pnl_pct >= self.take_profit_pct:
            reason = "take_profit"

        if reason:
            side = OrderSide.SELL if state.is_long else OrderSide.BUY
            qty = abs(state.position)
            orders.append(self._make_order(ticker, side, qty, 1.0, reason=reason))
            state.position = 0.0
            state.entry_price = 0.0
            logger.info("Momentum [%s] exit: %s pnl_pct=%.3f%%", ticker, reason, pnl_pct * 100)

        return orders

    @staticmethod
    def _get_feature(features: pd.DataFrame, col: str) -> float | None:
        if col in features.columns and len(features) > 0:
            val = features[col].iloc[-1]
            return None if pd.isna(val) else float(val)
        return None
