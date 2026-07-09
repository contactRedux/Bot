"""
strategies/kelly_vol.py — Kelly Criterion + Volatility Targeting strategy.

Mathematical Foundation
========================

Volatility Targeting (Moreira & Muir 2017)
-------------------------------------------
The core observation is that **realised volatility is negatively correlated
with future returns** on a short horizon.  High-vol periods tend to produce
lower Sharpe ratios, so scaling down when volatility is elevated and scaling
up when it is suppressed delivers better risk-adjusted performance than a
static weight.

    σ_t  =  rolling_std(log_returns, lookback) × √252       (annualised)

    vol_target_qty  =  (vol_target_pct / σ_t) × base_position_size

    → clipped at max_leverage × base_position_size

This is the foundation of *risk-parity* and *volatility-targeting* funds
(AQR, Bridgewater, etc.).

Kelly Criterion (Edward O. Thorp — Beat the Dealer / Beat the Market)
----------------------------------------------------------------------
Given a repeated binary bet with:
    p  = probability of a win
    q  = 1 − p  (probability of a loss)
    W  = average gain on a win  (positive fraction)
    L  = average loss on a loss (positive fraction, i.e. |avg_loss|)

The Kelly fraction of capital to bet is:

    f* = p/L − q/W           (continuous-returns form of Kelly)

Because full-Kelly is highly volatile (drawdowns of 50%+ are common),
**fractional Kelly** is standard practice in quantitative finance:

    f  = f* × kelly_fraction          (default: 0.25 = quarter-Kelly)

The quantity is then:

    kelly_qty  =  max(f, 0) × base_position_size

Combined sizing
---------------
Both signals are computed independently; the **minimum** is used:

    qty  =  min(vol_target_qty, kelly_qty)

Taking the minimum is conservative — we only commit capital when BOTH
the volatility regime and the historical edge agree.

Entry / Exit
------------
- Enter long when:
    (a) position is currently flat
    (b) kelly f* > min_edge_pct  (positive expected value)
    (c) momentum is positive     (last close > rolling mean over lookback)
    (d) at least lookback_bars of price history are available
    (e) rebalance_bars has elapsed since the last rebalance

- Exit long when EITHER:
    (a) kelly f* turns negative  (edge has disappeared)
    (b) current σ_t > 2 × (vol_target_pct / 1.0 × annualised)  (vol spike)

- Rebalance gate of rebalance_bars reduces turnover costs.

References
----------
- Moreira, A. & Muir, T. (2017). "Volatility-Managed Portfolios."
  Journal of Finance 72(4): 1611–1644.
- Kelly, J. L. (1956). "A New Interpretation of Information Rate."
  Bell System Technical Journal 35(4): 917–926.
- Thorp, E. O. (1962). Beat the Dealer. Random House.
- Thorp, E. O. (1967). Beat the Market. Random House.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState

logger = logging.getLogger(__name__)

# Annualisation factor: √252 trading days
_ANNUALISE = np.sqrt(252.0)

# Minimum standard deviation guard — prevents division by near-zero vol
_MIN_SIGMA = 1e-6


class KellyVolStrategy(BaseStrategy):
    """
    Kelly Criterion + Volatility Targeting position-sizing strategy.

    Combines two well-researched sizing frameworks:

    1. **Volatility targeting** (Moreira & Muir 2017): scale position size
       inversely with realised volatility so that risk contribution per bar
       stays constant.

    2. **Fractional Kelly** (Thorp 1956/1967): size bets proportionally to
       the estimated edge (p/L − q/W) and apply a conservative fraction
       (default 0.25) to guard against estimation error.

    The final quantity is the minimum of both estimates, acting as a
    dual-confirmation gate before capital is deployed.

    Parameters
    ----------
    config : dict
        Strategy parameters (see module-level docstring for keys).
    tickers : list[str]
        Universe of symbols to monitor.
    base_position_size : float
        Base number of units per signal.  Actual sizes are scaled
        multiplicatively around this anchor.
    """

    strategy_id = "kelly_vol"

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        base_position_size: float = 100.0,
    ) -> None:
        super().__init__("kelly_vol", config, tickers)
        self.base_position_size: float = base_position_size

        # ── Config parameters with defaults ──────────────────────────────────
        self.vol_target_pct: float = float(config.get("vol_target_pct", 0.15))
        self.lookback_bars: int = int(config.get("lookback_bars", 60))
        self.kelly_fraction: float = float(config.get("kelly_fraction", 0.25))
        self.min_edge_pct: float = float(config.get("min_edge_pct", 0.002))
        self.max_leverage: float = float(config.get("max_leverage", 2.0))
        self.rebalance_bars: int = int(config.get("rebalance_bars", 5))

        # ── Per-ticker price history buffers (max 2× lookback) ───────────────
        # Using deque with maxlen avoids manual pop(0) on every bar.
        _buf_size = self.lookback_bars * 2
        self._price_buffers: dict[str, deque[float]] = {
            t: deque(maxlen=_buf_size) for t in tickers
        }

        # ── Per-ticker bar counter since last rebalance ───────────────────────
        # Stored in TickerState.extra["bars_since_rebalance"] to keep all
        # per-ticker mutable state in one place (consistent with other strategies).

        # ── Pending orders produced in on_bar, consumed in generate_orders ───
        self._pending_orders: list[Order] = []

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        """
        Process a new OHLCV bar for *ticker*.

        Steps
        -----
        1.  Append close price to the rolling buffer.
        2.  Skip if insufficient history (< lookback_bars).
        3.  Compute realised vol σ_t and vol-targeting quantity.
        4.  Compute Kelly f* and Kelly quantity.
        5.  Apply entry / exit / rebalance logic.
        6.  Append any orders to self._pending_orders.
        """
        if not self._enabled:
            return

        state = self._state_for(ticker)
        close = float(bar.get("close", 0.0))
        if close <= 0.0:
            return

        # ── Step 1: maintain rolling price buffer ─────────────────────────────
        buf = self._price_buffers.setdefault(ticker, deque(maxlen=self.lookback_bars * 2))
        buf.append(close)

        # ── Step 2: require minimum history ───────────────────────────────────
        if len(buf) < self.lookback_bars + 1:
            # +1 because log-returns need one extra price observation
            return

        # ── Step 3: volatility estimate ───────────────────────────────────────
        prices = np.asarray(buf, dtype=np.float64)
        log_rets = np.diff(np.log(prices[-(self.lookback_bars + 1):]))
        sigma = float(np.std(log_rets, ddof=1)) * _ANNUALISE
        if sigma < _MIN_SIGMA:
            sigma = _MIN_SIGMA

        vol_target_qty = np.clip(
            (self.vol_target_pct / sigma) * self.base_position_size,
            0.0,
            self.max_leverage * self.base_position_size,
        )

        # ── Step 4: Kelly estimate ────────────────────────────────────────────
        f_star, kelly_qty = self._kelly_size(log_rets)

        # ── Step 5: combined quantity ─────────────────────────────────────────
        qty = float(min(vol_target_qty, kelly_qty))

        # Stash diagnostics in state.extra for logging / debugging
        state.extra["sigma"] = round(sigma, 6)
        state.extra["f_star"] = round(f_star, 6)
        state.extra["vol_target_qty"] = round(float(vol_target_qty), 4)
        state.extra["kelly_qty"] = round(float(kelly_qty), 4)
        state.last_signal = float(f_star)

        # ── Step 6: rebalance gate ────────────────────────────────────────────
        bars_since = int(state.extra.get("bars_since_rebalance", self.rebalance_bars))
        if bars_since < self.rebalance_bars:
            state.extra["bars_since_rebalance"] = bars_since + 1
            # Still check exits even during the rebalance cooldown
            self._check_exits(ticker, close, f_star, sigma, state)
            return
        # Reset counter (will be set to 0 after a rebalance decision)

        # ── Exit check ────────────────────────────────────────────────────────
        if self._check_exits(ticker, close, f_star, sigma, state):
            state.extra["bars_since_rebalance"] = 0
            return

        # ── Entry check ───────────────────────────────────────────────────────
        if not state.is_flat:
            # Already long — no additional entry without a prior exit
            return

        # Require positive Kelly edge above minimum threshold
        if f_star < self.min_edge_pct:
            return

        # Momentum filter: last close must be above rolling mean (simple trend check)
        window_prices = prices[-self.lookback_bars:]
        if close <= float(np.mean(window_prices)):
            return

        # Sufficient quantity guard (> 1e-4 units)
        if qty < 1e-4:
            return

        # Confidence: normalised Kelly f* capped at 1.0
        confidence = float(np.clip(f_star / max(self.min_edge_pct * 10, 0.02), 0.0, 1.0))

        order = self._make_order(
            ticker,
            OrderSide.BUY,
            qty,
            confidence,
            order_type=OrderType.MARKET,
            sigma=round(sigma, 5),
            f_star=round(f_star, 5),
            vol_target_qty=round(float(vol_target_qty), 3),
            kelly_qty=round(float(kelly_qty), 3),
        )
        self._pending_orders.append(order)

        state.position = qty
        state.entry_price = close
        state.bars_in_position = 0
        state.last_confidence = confidence
        state.extra["bars_since_rebalance"] = 0

        logger.info(
            "KellyVol [%s] BUY qty=%.2f σ=%.4f f*=%.4f vol_qty=%.2f kelly_qty=%.2f conf=%.2f",
            ticker, qty, sigma, f_star, float(vol_target_qty), float(kelly_qty), confidence,
        )

    def generate_orders(self) -> list[Order]:
        """
        Return all pending orders accumulated since the last call.

        Called once per bar cycle by the orchestrator after all ``on_bar``
        hooks have been invoked.  Clears the pending list and increments
        the global bar counter.
        """
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Private helpers ───────────────────────────────────────────────────────

    def _kelly_size(self, log_rets: np.ndarray) -> tuple[float, float]:
        """
        Estimate the Kelly fraction f* and corresponding quantity from a
        window of log-returns.

        Parameters
        ----------
        log_rets : np.ndarray
            Array of per-bar log-returns over the lookback window.

        Returns
        -------
        f_star : float
            Raw Kelly fraction (can be negative → no edge).
        kelly_qty : float
            Fractional Kelly quantity ≥ 0.0 in units of base_position_size.

        Notes
        -----
        Win probability ``p`` is the empirical fraction of positive returns.
        Average win ``W`` and average loss ``L`` are computed over those
        subsets respectively.  The formula is the **continuous-returns**
        Kelly formula (Thorp 1967):

            f* = p / L  −  (1 − p) / W
        """
        pos_mask = log_rets > 0.0
        neg_mask = log_rets < 0.0

        n_pos = int(np.sum(pos_mask))
        n_neg = int(np.sum(neg_mask))
        n_total = len(log_rets)

        if n_total == 0 or n_pos == 0 or n_neg == 0:
            # Cannot estimate all components — no edge claim
            return 0.0, 0.0

        p = n_pos / n_total
        q = 1.0 - p

        W = float(np.mean(log_rets[pos_mask]))    # avg win  (positive)
        L = float(np.mean(-log_rets[neg_mask]))   # avg loss (positive, flip sign)

        if W < _MIN_SIGMA or L < _MIN_SIGMA:
            return 0.0, 0.0

        # Classic Kelly formula in return-space
        f_star = (p / L) - (q / W)

        # Fractional Kelly — apply shrinkage to guard against estimation noise
        f = f_star * self.kelly_fraction

        kelly_qty = float(np.clip(f * self.base_position_size, 0.0,
                                  self.max_leverage * self.base_position_size))
        return float(f_star), kelly_qty

    def _check_exits(
        self,
        ticker: str,
        close: float,
        f_star: float,
        sigma: float,
        state: TickerState,
    ) -> bool:
        """
        Check whether the current position should be closed.

        Exit conditions:
            1. Kelly edge has turned negative (f* < 0).
            2. Volatility spike: σ_t > 2 × vol_target_pct  (double the target).

        Parameters
        ----------
        ticker  : str
        close   : float  Current close price.
        f_star  : float  Current raw Kelly fraction.
        sigma   : float  Current annualised realised volatility.
        state   : TickerState

        Returns
        -------
        bool
            True if an exit order was emitted.
        """
        if state.is_flat:
            return False

        reason: str | None = None

        # Condition 1: edge has evaporated
        if f_star < 0.0:
            reason = "kelly_edge_negative"

        # Condition 2: vol spike — σ > 2× vol target
        vol_spike_threshold = 2.0 * self.vol_target_pct
        if sigma > vol_spike_threshold:
            reason = "vol_spike"

        if reason is None:
            return False

        exit_side = OrderSide.SELL if state.is_long else OrderSide.BUY
        qty = abs(state.position)

        self._pending_orders.append(
            self._make_order(
                ticker,
                exit_side,
                qty,
                1.0,
                order_type=OrderType.MARKET,
                reason=reason,
                sigma=round(sigma, 5),
                f_star=round(f_star, 5),
            )
        )

        logger.info(
            "KellyVol [%s] %s reason=%s σ=%.4f f*=%.4f",
            ticker, exit_side.value, reason, sigma, f_star,
        )

        state.position = 0.0
        state.entry_price = 0.0
        state.last_confidence = 0.0
        state.extra.pop("bars_since_rebalance", None)
        return True

    @staticmethod
    def _get_feature(features: pd.DataFrame, col: str) -> float | None:
        """Return the last value of *col* from features, or None if missing/NaN."""
        if col in features.columns and len(features) > 0:
            val = features[col].iloc[-1]
            return None if pd.isna(val) else float(val)
        return None
