"""
strategies/mean_reversion.py — Bollinger Band mean reversion strategy.

Strategy logic
--------------
Mean reversion exploits the statistical principle that asset prices tend to
revert to their historical mean.  This works best in *ranging* (non-trending)
markets where there's no persistent directional momentum.

Implementation:
1. Compute a rolling z-score: z = (price − SMA(n)) / std(n)
2. When z > +entry_z (overbought): SHORT — expect price to fall back
3. When z < −entry_z (oversold):  LONG  — expect price to rise back
4. Exit when |z| < exit_z (price has reverted to near the mean)
5. Stop-loss at ATR-based distance from entry to cap downside

Why z-score over raw Bollinger Bands?
--------------------------------------
Bollinger Bands express the same information but are price-level dependent.
The z-score normalises across assets and volatility regimes — a z-score of 2.0
has the same statistical meaning for AAPL trading at $180 and BTC trading at $50,000.

ADX anti-filter
---------------
ADX < 20 indicates a ranging market — this is *favourable* for mean reversion.
We skip mean-reversion trades when ADX > 25 (trending market) because in a
strong trend, "overbought" can stay overbought for a long time.

ATR stop-loss
--------------
Rather than a fixed percentage stop, we use ATR (Average True Range) to
scale the stop distance to the asset's current volatility:
    stop_distance = ATR × stop_atr_multiplier

A high-volatility day means a wider stop (doesn't get shaken out by noise);
a low-volatility day means a tighter stop (less capital at risk per trade).

Configuration (strategy_config.yaml)
-------------------------------------
    lookback_bars       : rolling window for z-score (default 20)
    entry_z_score       : z threshold to enter       (default 2.0)
    exit_z_score        : z threshold to exit        (default 0.3)
    stop_atr_multiplier : ATR multiplier for stop    (default 2.0)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """
    Bollinger Band / z-score mean reversion strategy.

    Parameters
    ----------
    config : dict
        From strategy_config.yaml ``mean_reversion`` section.
    tickers : list[str]
        Universe of symbols to monitor.
    base_position_size : float
        Base number of units per signal.
    """

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        base_position_size: float = 100.0,
    ) -> None:
        super().__init__("mean_reversion", config, tickers)
        self.lookback: int = int(config.get("lookback_bars", 20))
        self.entry_z: float = config.get("entry_z_score", 2.0)
        self.exit_z: float = config.get("exit_z_score", 0.3)
        self.stop_atr_mult: float = config.get("stop_atr_multiplier", 2.0)
        self.base_position_size = base_position_size

        self._pending_orders: list[Order] = []

        # Per-ticker rolling price buffer for z-score (fallback when features lack bb_zscore)
        self._price_buffers: dict[str, list[float]] = {t: [] for t in tickers}

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        if not self._enabled:
            return

        state = self._state_for(ticker)
        close = float(bar.get("close", 0.0))

        # Maintain rolling price buffer
        buf = self._price_buffers.setdefault(ticker, [])
        buf.append(close)
        if len(buf) > self.lookback * 3:
            buf.pop(0)

        # ── Compute z-score ───────────────────────────────────────────────────
        z = self._compute_zscore(ticker, close, features)
        if z is None:
            return  # Not enough history yet

        # ATR for stop placement
        atr = self._get_feature(features, "atr") or (close * 0.01)

        # ADX filter: skip if strongly trending (mean reversion underperforms in trends)
        adx = self._get_feature(features, "adx")
        if adx is not None and adx > 25.0:
            logger.debug("MeanReversion [%s] skipping: ADX=%.1f > 25 (trending)", ticker, adx)
            # Still check exits on existing position
            self._check_exits(ticker, close, z, state)
            return

        # ── Exit logic ────────────────────────────────────────────────────────
        if self._check_exits(ticker, close, z, state):
            return  # Position closed; don't immediately re-enter

        # ── Entry logic ───────────────────────────────────────────────────────
        if not state.is_flat:
            return  # Already in a position; wait for exit

        if z > self.entry_z:
            # Price is overbought: sell short, expect reversion to mean
            side = OrderSide.SELL
            stop = close + atr * self.stop_atr_mult  # stop above entry for short
            confidence = min(1.0, (z - self.entry_z) / self.entry_z + 0.5)
            qty = self.base_position_size * confidence
            order = self._make_order(ticker, side, qty, confidence, order_type=OrderType.MARKET,
                                     stop_price=stop, zscore=round(z, 3))
            self._pending_orders.append(order)
            state.position = -qty
            state.entry_price = close
            state.extra["stop_price"] = stop
            logger.info("MeanReversion [%s] SHORT z=%.2f atr=%.4f stop=%.4f", ticker, z, atr, stop)

        elif z < -self.entry_z:
            # Price is oversold: buy, expect reversion to mean
            side = OrderSide.BUY
            stop = close - atr * self.stop_atr_mult  # stop below entry for long
            confidence = min(1.0, (abs(z) - self.entry_z) / self.entry_z + 0.5)
            qty = self.base_position_size * confidence
            order = self._make_order(ticker, side, qty, confidence, order_type=OrderType.MARKET,
                                     stop_price=stop, zscore=round(z, 3))
            self._pending_orders.append(order)
            state.position = qty
            state.entry_price = close
            state.extra["stop_price"] = stop
            logger.info("MeanReversion [%s] LONG z=%.2f atr=%.4f stop=%.4f", ticker, z, atr, stop)

    def generate_orders(self) -> list[Order]:
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_zscore(
        self, ticker: str, close: float, features: pd.DataFrame
    ) -> float | None:
        """
        Compute current z-score from features or fallback rolling buffer.

        Prefers the ``bb_zscore`` column computed by the feature pipeline
        (Bollinger Band z-score).  Falls back to a local rolling computation.
        """
        # Try feature pipeline column first (most accurate)
        for col in ("bb_pct_b", "bb_zscore"):
            val = self._get_feature(features, col)
            if val is not None:
                # bb_pct_b ranges [0, 1] where 0.5 = midline
                # Convert to z-score: z = (pct_b - 0.5) * 4  (≈ maps [0,1] to [-2,+2])
                if col == "bb_pct_b":
                    return (val - 0.5) * 4.0
                return val

        # Fallback: compute from rolling buffer
        buf = self._price_buffers.get(ticker, [])
        if len(buf) < self.lookback:
            return None
        window = buf[-self.lookback:]
        mu = np.mean(window)
        sigma = np.std(window) + 1e-8
        return (close - mu) / sigma

    def _check_exits(
        self, ticker: str, close: float, z: float, state: TickerState
    ) -> bool:
        """
        Check stop-loss and z-score exit conditions.
        Returns True if the position was closed.
        """
        if state.is_flat:
            return False

        stop = state.extra.get("stop_price")
        exit_triggered = False
        reason = None

        # Z-score reversion exit
        if state.is_long and abs(z) < self.exit_z:
            reason = "reversion_exit"
        elif state.is_short and abs(z) < self.exit_z:
            reason = "reversion_exit"

        # Stop-loss exit
        if stop is not None:
            if state.is_long and close <= stop:
                reason = "stop_loss"
            elif state.is_short and close >= stop:
                reason = "stop_loss"

        if reason:
            exit_side = OrderSide.SELL if state.is_long else OrderSide.BUY
            qty = abs(state.position)
            self._pending_orders.append(
                self._make_order(ticker, exit_side, qty, 1.0, reason=reason)
            )
            state.position = 0.0
            state.entry_price = 0.0
            state.extra.pop("stop_price", None)
            exit_triggered = True
            logger.info("MeanReversion [%s] exit: %s z=%.2f", ticker, reason, z)

        return exit_triggered

    @staticmethod
    def _get_feature(features: pd.DataFrame, col: str) -> float | None:
        if col in features.columns and len(features) > 0:
            val = features[col].iloc[-1]
            return None if pd.isna(val) else float(val)
        return None
