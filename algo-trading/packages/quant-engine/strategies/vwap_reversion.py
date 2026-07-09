"""
strategies/vwap_reversion.py — VWAP Mean Reversion Strategy.

Mathematical Foundation
-----------------------
**VWAP (Volume-Weighted Average Price)** is the true average transaction price
over a rolling window of N bars.  Every institutional algorithm benchmarks
execution against VWAP — it is the fair-value anchor for intraday price action.

    VWAP_t = Σ(close_i · volume_i, i = t−N … t)
             ─────────────────────────────────────
                 Σ(volume_i, i = t−N … t)

**Deviation from VWAP**:

    deviation_t = (close_t − VWAP_t) / VWAP_t

Deviations from VWAP are transient rather than persistent: when price drifts
above VWAP, institutional sellers who track VWAP begin selling aggressively,
pushing price back toward the benchmark, and vice versa.  This creates a
reliable mean-reversion attractor — pure microstructure arbitrage.

**ATR (Average True Range)** — volatility guard:

    TR_t  = max(high_t − low_t, |high_t − close_{t−1}|, |low_t − close_{t−1}|)
    ATR_t = EWM(TR, span = atr_window)

When ATR/close > max_atr_pct the bid/ask spread widens and slippage can exceed
the expected reversion profit, so we skip new entries.

**Volume confirmation** — conviction guard:

Only trade when current bar volume exceeds the rolling mean volume over
volume_window bars.  Low-volume deviations are artefacts of illiquidity, not
genuine mispricing.

Signal Summary
--------------
    deviation < −entry_band_pct  →  BUY  (price below VWAP, expect upward reversion)
    deviation > +entry_band_pct  →  SELL (price above VWAP, expect downward reversion)
    long  open & deviation > −exit_band_pct  →  SELL to close
    short open & deviation < +exit_band_pct  →  BUY  to cover

Confidence Score
----------------
    confidence = clip(|deviation| / entry_band_pct, 0.0, 1.0)

The further price has strayed from VWAP relative to the entry threshold,
the higher the conviction in the reversion trade.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState  # noqa: F401

logger = logging.getLogger(__name__)

# Maximum history to keep per ticker (2× the largest window used)
_HISTORY_MULTIPLIER = 2


class VWAPReversionStrategy(BaseStrategy):
    """
    VWAP Mean Reversion strategy.

    Enters when price deviates from the rolling VWAP by more than
    ``entry_band_pct``, and exits when it reverts within ``exit_band_pct``.
    Optional ATR and volume filters guard against noisy, low-conviction signals.

    Parameters
    ----------
    config : dict
        Strategy parameters (see module docstring for all keys and defaults).
    tickers : list[str]
        Universe of symbols to monitor.
    base_position_size : float
        Base number of units per signal before confidence scaling.
    """

    strategy_id = "vwap_reversion"

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        base_position_size: float = 100.0,
    ) -> None:
        super().__init__(self.strategy_id, config, tickers)

        # ── Config parameters with defaults ──────────────────────────────────
        self.vwap_window: int = int(config.get("vwap_window", 20))
        self.entry_band_pct: float = float(config.get("entry_band_pct", 0.005))
        self.exit_band_pct: float = float(config.get("exit_band_pct", 0.001))
        self.atr_filter: bool = bool(config.get("atr_filter", True))
        self.atr_window: int = int(config.get("atr_window", 14))
        self.max_atr_pct: float = float(config.get("max_atr_pct", 0.03))
        self.volume_filter: bool = bool(config.get("volume_filter", True))
        self.volume_window: int = int(config.get("volume_window", 20))
        self.min_bars: int = int(config.get("min_bars", 25))
        self.base_position_size: float = base_position_size

        # ── Per-ticker OHLCV history buffers ──────────────────────────────────
        # Each entry is a dict of lists: close, volume, high, low.
        # Capped at _HISTORY_MULTIPLIER * max(vwap_window, atr_window, volume_window).
        self._max_buf: int = _HISTORY_MULTIPLIER * max(
            self.vwap_window, self.atr_window, self.volume_window
        )
        self._ticker_data: dict[str, dict[str, list[float]]] = {
            t: {"close": [], "volume": [], "high": [], "low": []}
            for t in tickers
        }

        # Orders staged in on_bar, flushed in generate_orders
        self._pending_orders: list[Order] = []

    # ── Event hook ────────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        """
        Ingest a new OHLCV bar, update history, and compute the VWAP signal.

        Parameters
        ----------
        ticker : str
            Symbol for this bar.
        bar : pd.Series
            Current bar with keys: open, high, low, close, volume.
        features : pd.DataFrame
            Full feature matrix up to and including this bar (unused internally;
            all indicators are computed from raw OHLCV buffers).
        """
        if not self._enabled:
            return

        close: float = float(bar.get("close", 0.0))
        high: float = float(bar.get("high", close))
        low: float = float(bar.get("low", close))
        volume: float = float(bar.get("volume", 0.0))

        # ── Update per-ticker buffers ─────────────────────────────────────────
        buf = self._ticker_data.setdefault(
            ticker, {"close": [], "volume": [], "high": [], "low": []}
        )
        buf["close"].append(close)
        buf["volume"].append(volume)
        buf["high"].append(high)
        buf["low"].append(low)

        # Trim to maximum buffer size
        if len(buf["close"]) > self._max_buf:
            for key in buf:
                buf[key] = buf[key][-self._max_buf:]

        # ── Warmup guard ──────────────────────────────────────────────────────
        if len(buf["close"]) < self.min_bars:
            return

        state: TickerState = self._state_for(ticker)

        # ── Compute VWAP ──────────────────────────────────────────────────────
        vwap: float | None = self._compute_vwap(buf)
        if vwap is None or vwap == 0.0:
            return

        deviation: float = (close - vwap) / vwap

        # ── ATR filter ────────────────────────────────────────────────────────
        if self.atr_filter:
            atr: float | None = self._compute_atr(buf)
            if atr is not None and close > 0.0 and (atr / close) > self.max_atr_pct:
                logger.debug(
                    "VWAPReversion [%s] skipping: ATR/price=%.4f > %.4f (too noisy)",
                    ticker, atr / close, self.max_atr_pct,
                )
                # Still honour exit logic on existing positions
                self._check_exits(ticker, deviation, state)
                return

        # ── Volume filter ─────────────────────────────────────────────────────
        if self.volume_filter:
            mean_vol: float | None = self._compute_mean_volume(buf)
            if mean_vol is not None and volume < mean_vol:
                logger.debug(
                    "VWAPReversion [%s] skipping: volume=%.0f < mean=%.0f (low conviction)",
                    ticker, volume, mean_vol,
                )
                self._check_exits(ticker, deviation, state)
                return

        # ── Exit logic ────────────────────────────────────────────────────────
        if self._check_exits(ticker, deviation, state):
            return  # Position closed; do not immediately re-enter same bar

        # ── Entry logic ───────────────────────────────────────────────────────
        if not state.is_flat:
            return  # Already in a position; wait for exit signal

        confidence: float = min(1.0, abs(deviation) / self.entry_band_pct)
        qty: float = self.base_position_size * confidence

        if deviation < -self.entry_band_pct:
            # Price is below VWAP → expect upward reversion → BUY
            order = self._make_order(
                ticker, OrderSide.BUY, qty, confidence,
                vwap=round(vwap, 6), deviation=round(deviation, 6),
            )
            self._pending_orders.append(order)
            state.position = qty
            state.entry_price = close
            logger.info(
                "VWAPReversion [%s] BUY  vwap=%.4f dev=%.4f conf=%.3f qty=%.2f",
                ticker, vwap, deviation, confidence, qty,
            )

        elif deviation > self.entry_band_pct:
            # Price is above VWAP → expect downward reversion → SELL short
            order = self._make_order(
                ticker, OrderSide.SELL, qty, confidence,
                vwap=round(vwap, 6), deviation=round(deviation, 6),
            )
            self._pending_orders.append(order)
            state.position = -qty
            state.entry_price = close
            logger.info(
                "VWAPReversion [%s] SELL vwap=%.4f dev=%.4f conf=%.3f qty=%.2f",
                ticker, vwap, deviation, confidence, qty,
            )

    # ── Core interface ────────────────────────────────────────────────────────

    def generate_orders(self) -> list[Order]:
        """
        Return all orders staged during the current bar and increment bar counter.

        Returns
        -------
        list[Order]
            May be empty if no signals fired this bar.
        """
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Private helpers ───────────────────────────────────────────────────────

    def _compute_vwap(self, buf: dict[str, list[float]]) -> float | None:
        """
        Compute rolling VWAP over the last ``vwap_window`` bars.

        VWAP = Σ(close_i · volume_i) / Σ(volume_i)

        Returns None if the volume sum is zero (prevents division by zero).
        """
        closes = np.asarray(buf["close"][-self.vwap_window:], dtype=float)
        volumes = np.asarray(buf["volume"][-self.vwap_window:], dtype=float)
        total_volume: float = volumes.sum()
        if total_volume == 0.0:
            return None
        return float((closes * volumes).sum() / total_volume)

    def _compute_atr(self, buf: dict[str, list[float]]) -> float | None:
        """
        Compute ATR using an exponentially weighted mean of True Range.

        TR_t = max(high − low, |high − prev_close|, |low − prev_close|)
        ATR  = EWM(TR, span = atr_window).iloc[-1]

        Returns None when fewer than 2 bars are available.
        """
        n: int = min(len(buf["close"]), self._max_buf)
        if n < 2:
            return None

        highs = np.asarray(buf["high"][-n:], dtype=float)
        lows = np.asarray(buf["low"][-n:], dtype=float)
        closes = np.asarray(buf["close"][-n:], dtype=float)

        prev_closes = closes[:-1]
        highs_ = highs[1:]
        lows_ = lows[1:]

        tr = np.maximum(
            highs_ - lows_,
            np.maximum(np.abs(highs_ - prev_closes), np.abs(lows_ - prev_closes)),
        )

        # Exponentially weighted mean (pandas for numerical accuracy)
        atr_series = pd.Series(tr).ewm(span=self.atr_window, adjust=False).mean()
        return float(atr_series.iloc[-1])

    def _compute_mean_volume(self, buf: dict[str, list[float]]) -> float | None:
        """
        Return the simple rolling mean of volume over ``volume_window`` bars,
        excluding the current (most recent) bar so we compare current vs history.

        Returns None if there are fewer than 2 volume observations.
        """
        vols = buf["volume"]
        # Need at least (volume_window + 1) entries: window history + current bar
        if len(vols) < 2:
            return None
        history = np.asarray(vols[-self.volume_window - 1:-1], dtype=float)
        if len(history) == 0:
            return None
        return float(history.mean())

    def _check_exits(
        self, ticker: str, deviation: float, state: TickerState
    ) -> bool:
        """
        Evaluate exit conditions for an open position.

        Exit rules:
        - Long  position and deviation > −exit_band_pct → price has reverted; SELL to close.
        - Short position and deviation < +exit_band_pct → price has reverted; BUY  to cover.

        Parameters
        ----------
        ticker : str
            Symbol being evaluated.
        deviation : float
            Current (close − VWAP) / VWAP.
        state : TickerState
            Mutable per-ticker state.

        Returns
        -------
        bool
            True if a closing order was staged, False otherwise.
        """
        if state.is_flat:
            return False

        reason: str | None = None

        if state.is_long and deviation > -self.exit_band_pct:
            reason = "reversion_exit_long"
        elif state.is_short and deviation < self.exit_band_pct:
            reason = "reversion_exit_short"

        if reason is None:
            return False

        exit_side = OrderSide.SELL if state.is_long else OrderSide.BUY
        qty = abs(state.position)
        self._pending_orders.append(
            self._make_order(ticker, exit_side, qty, 1.0, reason=reason,
                             deviation=round(deviation, 6))
        )
        logger.info(
            "VWAPReversion [%s] EXIT %s dev=%.4f reason=%s",
            ticker, exit_side.value.upper(), deviation, reason,
        )
        state.position = 0.0
        state.entry_price = 0.0
        return True
