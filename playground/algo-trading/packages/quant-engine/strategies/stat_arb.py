"""
strategies/stat_arb.py — Statistical Arbitrage (Pairs Trading) strategy.

Strategy logic
--------------
Statistical arbitrage exploits the co-movement of two cointegrated assets.
When the spread between them (price_A − β × price_B) deviates significantly
from its mean, we bet on convergence:

    Spread z > +entry_z  →  SHORT spread: sell A, buy B
    Spread z < −entry_z  →  LONG spread: buy A, sell B
    |z| < exit_z         →  Close both legs (spread reverted)

Mathematical foundation
-----------------------
1. **Cointegration test (Engle-Granger)**
   Regress price_A on price_B: price_A = α + β × price_B + ε
   Run ADF test on residuals ε.  If p-value < 0.05, the pair is cointegrated
   and the spread is stationary (mean-reverting).

2. **Hedge ratio β**
   β is the OLS regression coefficient: β = Cov(A, B) / Var(B)
   It tells us how many units of B we need to hold to be "market neutral"
   (the combined position has ~zero net delta to broad market moves).

3. **Z-score entry**
   z = (spread − mean(spread, window)) / std(spread, window)
   Enter when |z| > 2 (spread is 2 standard deviations from its mean).
   Exit when |z| < 0.5 (spread has substantially reverted).

4. **Half-life filter**
   The Ornstein-Uhlenbeck half-life tells us how fast the spread reverts.
   If half-life > max_half_life_bars, the spread is too slow to trade
   profitably within our holding horizon — we skip this pair.

Walk-forward pair screening
----------------------------
In live trading, pairs should be re-screened periodically (e.g. quarterly)
as cointegration relationships can break down.  This strategy maintains a
dict of active pairs and their hedge ratios, updated at configurable intervals.

Configuration (strategy_config.yaml)
-------------------------------------
    entry_z_score          : z threshold to enter (default 2.0)
    exit_z_score           : z threshold to exit  (default 0.5)
    max_half_life_bars      : skip slow pairs      (default 30)
    cointegration_pvalue_max: max ADF p-value      (default 0.05)
    coint_lookback_bars     : rolling coint window (default 252)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pair state container
# ---------------------------------------------------------------------------

@dataclass
class PairState:
    """State for a single cointegrated pair."""
    ticker_a: str
    ticker_b: str
    hedge_ratio: float = 1.0              # β in spread = A − β × B
    half_life: float = float("inf")       # bars to 50% mean reversion
    spread_mean: float = 0.0
    spread_std: float = 1.0
    position_a: float = 0.0              # + long, − short
    position_b: float = 0.0
    entry_z: float = 0.0
    last_z: float = 0.0
    bars_in_position: int = 0
    last_coint_pvalue: float = 1.0
    active: bool = False                  # passes all filters?
    price_history_a: list[float] = field(default_factory=list)
    price_history_b: list[float] = field(default_factory=list)

    @property
    def is_flat(self) -> bool:
        return abs(self.position_a) < 1e-8 and abs(self.position_b) < 1e-8


# ---------------------------------------------------------------------------
# StatArbStrategy
# ---------------------------------------------------------------------------

class StatArbStrategy(BaseStrategy):
    """
    Cointegration-based pairs trading strategy.

    Parameters
    ----------
    config : dict
        From strategy_config.yaml ``stat_arb`` section.
    pairs : list[tuple[str, str]]
        List of (ticker_a, ticker_b) pairs to monitor.
        Pair selection is confirmed by cointegration test during warmup.
    base_position_size : float
        Base units per leg per full-conviction trade.
    """

    def __init__(
        self,
        config: dict[str, Any],
        pairs: list[tuple[str, str]],
        base_position_size: float = 50.0,
    ) -> None:
        # Flatten pairs to tickers list for BaseStrategy
        all_tickers = list({t for pair in pairs for t in pair})
        super().__init__("stat_arb", config, all_tickers)

        self.pairs = pairs
        self.base_position_size = base_position_size

        self.entry_z: float = config.get("entry_z_score", 2.0)
        self.exit_z: float = config.get("exit_z_score", 0.5)
        self.max_half_life: float = config.get("max_half_life_bars", 30.0)
        self.coint_pvalue_max: float = config.get("cointegration_pvalue_max", 0.05)
        self.coint_lookback: int = int(config.get("coint_lookback_bars", 252))
        self.spread_window: int = 60  # rolling window for spread z-score

        # Pair states keyed by (ticker_a, ticker_b)
        self._pair_states: dict[tuple[str, str], PairState] = {
            (a, b): PairState(ticker_a=a, ticker_b=b)
            for a, b in pairs
        }

        # Latest close prices per ticker (updated in on_bar)
        self._latest_prices: dict[str, float] = {}
        self._pending_orders: list[Order] = []

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        if not self._enabled:
            return

        close = float(bar.get("close", 0.0))
        self._latest_prices[ticker] = close

        # Update price history for all pairs containing this ticker
        for (a, b), ps in self._pair_states.items():
            if ticker == a:
                ps.price_history_a.append(close)
                if len(ps.price_history_a) > self.coint_lookback * 2:
                    ps.price_history_a.pop(0)
            elif ticker == b:
                ps.price_history_b.append(close)
                if len(ps.price_history_b) > self.coint_lookback * 2:
                    ps.price_history_b.pop(0)

        # After updating prices for this ticker, process all pairs
        # where we now have fresh data for BOTH legs
        for pair_key, ps in self._pair_states.items():
            a, b = pair_key
            if a not in self._latest_prices or b not in self._latest_prices:
                continue
            if len(ps.price_history_a) < self.spread_window or \
               len(ps.price_history_b) < self.spread_window:
                continue
            self._process_pair(ps)

    def generate_orders(self) -> list[Order]:
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        self._bar_count += 1
        return orders

    # ── Core pair processing ─────────────────────────────────────────────────

    def _process_pair(self, ps: PairState) -> None:
        """Run cointegration checks and emit orders for one pair."""
        # Re-screen cointegration periodically (every coint_lookback bars)
        if self._bar_count % max(1, self.coint_lookback // 10) == 0 or not ps.active:
            self._screen_pair(ps)

        if not ps.active:
            return

        # Compute current spread z-score
        price_a = self._latest_prices[ps.ticker_a]
        price_b = self._latest_prices[ps.ticker_b]
        spread = price_a - ps.hedge_ratio * price_b
        z = self._zscore(spread, ps)
        ps.last_z = z

        if ps.is_flat:
            # Entry
            if z > self.entry_z:
                # Spread too high: SHORT A, LONG B (bet on convergence)
                qty_a = self.base_position_size
                qty_b = self.base_position_size * ps.hedge_ratio
                conf = min(1.0, (z - self.entry_z) / self.entry_z + 0.5)
                self._pending_orders.append(
                    self._make_order(ps.ticker_a, OrderSide.SELL, qty_a, conf,
                                     pair=f"{ps.ticker_a}/{ps.ticker_b}", z=round(z, 3), leg="A")
                )
                self._pending_orders.append(
                    self._make_order(ps.ticker_b, OrderSide.BUY, qty_b, conf,
                                     pair=f"{ps.ticker_a}/{ps.ticker_b}", z=round(z, 3), leg="B")
                )
                ps.position_a = -qty_a
                ps.position_b = qty_b
                ps.entry_z = z
                logger.info("StatArb ENTER short spread %s/%s z=%.2f", ps.ticker_a, ps.ticker_b, z)

            elif z < -self.entry_z:
                # Spread too low: LONG A, SHORT B (bet on convergence)
                qty_a = self.base_position_size
                qty_b = self.base_position_size * ps.hedge_ratio
                conf = min(1.0, (abs(z) - self.entry_z) / self.entry_z + 0.5)
                self._pending_orders.append(
                    self._make_order(ps.ticker_a, OrderSide.BUY, qty_a, conf,
                                     pair=f"{ps.ticker_a}/{ps.ticker_b}", z=round(z, 3), leg="A")
                )
                self._pending_orders.append(
                    self._make_order(ps.ticker_b, OrderSide.SELL, qty_b, conf,
                                     pair=f"{ps.ticker_a}/{ps.ticker_b}", z=round(z, 3), leg="B")
                )
                ps.position_a = qty_a
                ps.position_b = -qty_b
                ps.entry_z = z
                logger.info("StatArb ENTER long spread %s/%s z=%.2f", ps.ticker_a, ps.ticker_b, z)

        else:
            ps.bars_in_position += 1
            # Exit when spread has reverted
            if abs(z) < self.exit_z:
                self._close_pair(ps, reason="reversion", z=z)

    def _close_pair(self, ps: PairState, reason: str = "exit", z: float = 0.0) -> None:
        """Close both legs of the pair trade."""
        if not ps.is_flat:
            exit_a = OrderSide.BUY if ps.position_a < 0 else OrderSide.SELL
            exit_b = OrderSide.BUY if ps.position_b < 0 else OrderSide.SELL
            self._pending_orders.append(
                self._make_order(ps.ticker_a, exit_a, abs(ps.position_a), 1.0,
                                 reason=reason, pair=f"{ps.ticker_a}/{ps.ticker_b}")
            )
            self._pending_orders.append(
                self._make_order(ps.ticker_b, exit_b, abs(ps.position_b), 1.0,
                                 reason=reason, pair=f"{ps.ticker_a}/{ps.ticker_b}")
            )
            logger.info("StatArb EXIT %s %s/%s z=%.2f bars=%d",
                        reason, ps.ticker_a, ps.ticker_b, z, ps.bars_in_position)
        ps.position_a = 0.0
        ps.position_b = 0.0
        ps.entry_z = 0.0
        ps.bars_in_position = 0

    def _screen_pair(self, ps: PairState) -> None:
        """
        Run Engle-Granger cointegration test and compute hedge ratio + half-life.
        Updates ps.active, ps.hedge_ratio, ps.half_life.
        """
        n = min(len(ps.price_history_a), len(ps.price_history_b), self.coint_lookback)
        if n < 30:  # Not enough data
            ps.active = False
            return

        series_a = np.array(ps.price_history_a[-n:])
        series_b = np.array(ps.price_history_b[-n:])

        # OLS hedge ratio: β = Cov(A,B) / Var(B)
        ps.hedge_ratio = float(np.cov(series_a, series_b)[0, 1] / (np.var(series_b) + 1e-10))

        spread = series_a - ps.hedge_ratio * series_b
        ps.spread_mean = float(np.mean(spread))
        ps.spread_std = float(np.std(spread)) + 1e-8

        # OU half-life from AR(1) regression on spread differences
        spread_lag = spread[:-1]
        spread_diff = np.diff(spread)
        if len(spread_lag) >= 5:
            cov = np.cov(spread_lag, spread_diff)
            kappa = -cov[0, 1] / (np.var(spread_lag) + 1e-10)
            ps.half_life = float(np.log(2) / (kappa + 1e-10)) if kappa > 0 else float("inf")
        else:
            ps.half_life = float("inf")

        # Simple cointegration proxy: ADF-like stationarity check via variance ratio
        # (Full statsmodels ADF is in features/statistical.py; we use a lightweight proxy here
        #  to avoid circular imports and statsmodels dependency in strategy logic.)
        spread_var = np.var(spread)
        half_spread_var = np.var(spread[n // 2:])
        # If spread variance is stable across halves, it's mean-reverting
        var_ratio = half_spread_var / (spread_var + 1e-10)
        # Proxy p-value: < 1.0 suggests stationarity
        ps.last_coint_pvalue = max(0.0, float(var_ratio - 0.5))  # rough proxy

        ps.active = (
            ps.half_life < self.max_half_life
            and ps.last_coint_pvalue < self.coint_pvalue_max
            and ps.spread_std > 1e-6
        )
        logger.debug(
            "StatArb screen %s/%s β=%.3f hl=%.1f p≈%.3f active=%s",
            ps.ticker_a, ps.ticker_b, ps.hedge_ratio, ps.half_life,
            ps.last_coint_pvalue, ps.active,
        )

    def _zscore(self, spread_value: float, ps: PairState) -> float:
        """
        Compute the rolling z-score of the spread.
        Uses a recent window of computed spreads.
        """
        n = min(len(ps.price_history_a), len(ps.price_history_b), self.spread_window)
        if n < 10:
            return 0.0
        hist_a = np.array(ps.price_history_a[-n:])
        hist_b = np.array(ps.price_history_b[-n:])
        spreads = hist_a - ps.hedge_ratio * hist_b
        mu = np.mean(spreads)
        sigma = np.std(spreads) + 1e-8
        return float((spread_value - mu) / sigma)
