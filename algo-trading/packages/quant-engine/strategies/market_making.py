"""
strategies/market_making.py — RL-driven market making strategy.

Strategy logic
--------------
Market making earns the bid-ask spread by simultaneously posting a limit bid
(buy) below mid-price and a limit ask (sell) above mid-price.  The profit
per round-trip is the spread captured minus any adverse selection cost.

The core challenge: **inventory risk**.  If the price moves against our
accumulated inventory (e.g. we've been buying in a falling market), we lose
money.  The strategy manages this via:

1. **RL quote adjustment**: The PPO agent determines the optimal half-spread
   offset (wider in volatile markets, tighter in quiet ones).

2. **Inventory skew**: When we have a long inventory (bought more than sold),
   we shade our quotes lower — quote a tighter ask (to sell) and wider bid
   (less eager to buy more).  This reduces directional risk.

Avellaneda-Stoikov framework
------------------------------
The classical A-S model gives the optimal quotes as:
    bid = mid − (γσ²(T−t) + (1/γ)ln(1 + γ/k))
    ask = mid + (γσ²(T−t) + (1/γ)ln(1 + γ/k))

where γ is risk aversion, σ² is volatility, k is order arrival rate.
Our RL agent learns to approximate this policy from experience.

Requoting
---------
Limit orders that haven't been filled are cancelled and reposted every
``requote_interval_bars`` bars to keep quotes competitive.

Configuration (strategy_config.yaml)
-------------------------------------
    base_half_spread      : default quote offset as % of price (default 0.0005)
    max_inventory         : units to hold before skewing quotes  (default 100)
    inventory_skew_factor : skew per unit of inventory            (default 0.0001)
    requote_interval_bars : how often to refresh quotes           (default 1)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState

logger = logging.getLogger(__name__)


class MarketMakingStrategy(BaseStrategy):
    """
    RL PPO-driven market making strategy.

    Posts limit bids and asks around the current mid-price, adjusting quote
    offsets based on the RL agent's output and inventory position.

    Parameters
    ----------
    config : dict
        From strategy_config.yaml ``market_making`` section.
    tickers : list[str]
        Universe of symbols to quote.
    rl_agent : RLTradingAgent, optional
        Pre-trained PPO agent.  If None, uses rule-based fallback.
    base_position_size : float
        Default quote size in units.
    """

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        rl_agent: Any | None = None,
        base_position_size: float = 10.0,
    ) -> None:
        super().__init__("market_making", config, tickers)
        self._rl_agent = rl_agent
        self.base_position_size = base_position_size

        self.base_half_spread: float = config.get("base_half_spread", 0.0005)
        self.max_inventory: float = config.get("max_inventory", 100.0)
        self.skew_factor: float = config.get("inventory_skew_factor", 0.0001)
        self.requote_interval: int = int(config.get("requote_interval_bars", 1))

        self._pending_orders: list[Order] = []
        self._bar_counts: dict[str, int] = {t: 0 for t in tickers}

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        if not self._enabled:
            return

        state = self._state_for(ticker)
        close = float(bar.get("close", 0.0))
        if close <= 0:
            return

        self._bar_counts[ticker] = self._bar_counts.get(ticker, 0) + 1

        # Only requote on the configured interval
        if self._bar_counts[ticker] % self.requote_interval != 0:
            return

        # ── Compute half-spread from RL agent or fallback ─────────────────────
        half_spread = self._compute_half_spread(features, close, state)

        # ── Inventory skew ────────────────────────────────────────────────────
        # When long inventory, shade ask lower (reduce) and bid lower (less eager to buy more)
        inventory = state.position
        skew = np.clip(inventory / (self.max_inventory + 1e-8), -1.0, 1.0) * self.skew_factor * close
        bid_offset = half_spread + skew   # long inventory → wider bid (less aggressive)
        ask_offset = half_spread - skew   # long inventory → tighter ask (more eager to sell)
        bid_offset = max(bid_offset, close * 0.0001)
        ask_offset = max(ask_offset, close * 0.0001)

        bid_price = close - bid_offset
        ask_price = close + ask_offset

        # Determine quote sizes based on remaining inventory capacity
        inv_pct = abs(inventory) / (self.max_inventory + 1e-8)
        # Reduce bid size as we accumulate long inventory (to slow accumulation)
        bid_qty = self.base_position_size * max(0.1, 1.0 - max(0, inventory / self.max_inventory))
        ask_qty = self.base_position_size * max(0.1, 1.0 + min(0, inventory / self.max_inventory))

        confidence = float(np.clip(1.0 - inv_pct * 0.5, 0.3, 1.0))

        # Post bid
        self._pending_orders.append(self._make_order(
            ticker, OrderSide.BUY, bid_qty, confidence,
            order_type=OrderType.LIMIT, limit_price=round(bid_price, 4),
            quote_type="bid", half_spread=round(half_spread, 6), inventory=inventory,
        ))
        # Post ask
        self._pending_orders.append(self._make_order(
            ticker, OrderSide.SELL, ask_qty, confidence,
            order_type=OrderType.LIMIT, limit_price=round(ask_price, 4),
            quote_type="ask", half_spread=round(half_spread, 6), inventory=inventory,
        ))

        logger.debug(
            "MarketMaking [%s] bid=%.4f ask=%.4f spread=%.4f inv=%.1f",
            ticker, bid_price, ask_price, ask_price - bid_price, inventory,
        )

    def generate_orders(self) -> list[Order]:
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_half_spread(
        self, features: pd.DataFrame, close: float, state: TickerState
    ) -> float:
        """
        Use RL agent to determine half-spread, or fall back to ATR-based rule.

        The RL agent is optional — the rule-based fallback scales the spread
        proportionally to recent volatility (ATR / price).
        """
        if self._rl_agent is not None and self._rl_agent.is_trained:
            try:
                X = features.to_numpy(dtype=np.float32)
                out = self._rl_agent.predict(
                    X,
                    position=float(state.position / (self.max_inventory + 1e-8)),
                    unrealized_pnl=0.0,
                    cash_ratio=0.5,
                )
                # Map RL signal [-1,1] to half-spread adjustment factor [0.5, 2.0]
                # Strong buy signal (-1) → narrow spread (eager to fill)
                # Strong sell signal (+1) → wide spread (cautious)
                adj_factor = 1.0 + out.signal * 0.5
                return self.base_half_spread * close * adj_factor
            except Exception as exc:
                logger.debug("RL agent predict failed: %s — using fallback", exc)

        # Fallback: ATR-proportional spread
        atr = self._get_feature(features, "atr")
        if atr is not None and close > 0:
            # Typically ATR/price ≈ 0.5–2% for liquid stocks
            # We target half-spread ≈ ATR/price * 0.1 (10% of daily range)
            return max(self.base_half_spread * close, atr * 0.1)

        return self.base_half_spread * close

    @staticmethod
    def _get_feature(features: pd.DataFrame, col: str) -> float | None:
        if col in features.columns and len(features) > 0:
            val = features[col].iloc[-1]
            return None if pd.isna(val) else float(val)
        return None
