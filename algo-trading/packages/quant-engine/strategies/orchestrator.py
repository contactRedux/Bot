"""
strategies/orchestrator.py — StrategyOrchestrator: aggregates all strategy signals.

Role in the system
-------------------
The orchestrator sits between the strategies and execution/risk layers.  It:

1. **Receives events** (bars, news, fundamentals) from the data pipeline and
   fans them out to all registered strategies.

2. **Collects orders** from every strategy after each event cycle.

3. **Applies allocation weights** from ``strategy_config.yaml`` to scale
   each strategy's order quantities to its risk budget.

4. **Applies the macro regime multiplier** from MacroFactorStrategy to all
   strategies (reduces risk in fear / crisis regimes).

5. **Aggregates overlapping signals** on the same ticker by averaging
   quantities from strategies with the same directional intent.

6. **Enforces portfolio-level position limits** by capping any single
   position at ``max_position_pct`` of total capital.

7. **Emits a de-duplicated final order list** ready for the risk manager.

Signal aggregation rules
-------------------------
When multiple strategies want to trade the same ticker:

* **Same direction (BUY + BUY)**: average the quantities, keeping the
  higher confidence.  This represents consensus.

* **Opposite directions (BUY + SELL)**: the signals cancel.  We only
  emit an order if the net quantity exceeds a minimum threshold.
  This prevents small conflicting signals from generating noise trades.

* **Market orders + Limit orders**: market orders take priority.  If any
  strategy emits a MARKET order, the aggregated order is MARKET.

Allocation weights
------------------
Weights from ``strategy_config.yaml`` are normalised to sum to 1:

    weight_momentum = 0.20 / (0.20 + 0.18 + ... )

Each strategy's order quantity is scaled by:
    scaled_qty = raw_qty × weight_normalised × regime_multiplier × capital_fraction

Position limit enforcement
---------------------------
Portfolio-level constraint:
    max_position_qty = capital × max_position_pct / current_price

Any order exceeding this is scaled down to the remaining room before
passing to execution.

Usage
-----
::

    from strategies.orchestrator import StrategyOrchestrator
    from strategies.momentum import MomentumStrategy
    from strategies.mean_reversion import MeanReversionStrategy

    orchestrator = StrategyOrchestrator(
        strategies=[MomentumStrategy(cfg, tickers), MeanReversionStrategy(cfg, tickers)],
        macro_strategy=MacroFactorStrategy(cfg, tickers),
        total_capital=100_000.0,
        config=portfolio_config,
    )

    # Process one bar:
    orders = orchestrator.process_bar(ticker, bar, features)
    # Orders are now ready for the risk manager / execution broker.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType
from strategies.macro_factor import MacroFactorStrategy, MacroRegime

logger = logging.getLogger(__name__)

# Minimum net quantity (as fraction of base_position_size) to emit an order.
# Prevents dust orders from conflicting signal pairs.
_MIN_ORDER_QTY = 1e-4


class StrategyOrchestrator:
    """
    Aggregates signals from all strategies into a final order list.

    Parameters
    ----------
    strategies : list[BaseStrategy]
        All strategy instances.  Include every enabled strategy.
    macro_strategy : MacroFactorStrategy, optional
        If provided, its regime multiplier is applied to all order quantities.
    total_capital : float
        Total portfolio value in base currency (used for position sizing).
    config : dict
        The ``portfolio`` section of strategy_config.yaml.
    """

    def __init__(
        self,
        strategies: list[BaseStrategy],
        macro_strategy: MacroFactorStrategy | None = None,
        total_capital: float = 100_000.0,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.strategies = list(strategies)
        # Dict view required by /api/strategies routes: strategy_id → strategy instance
        self._strategies: dict[str, BaseStrategy] = {
            s.strategy_id: s for s in self.strategies
        }
        self.macro_strategy = macro_strategy
        self.total_capital = total_capital
        self.config = config or {}

        self.max_position_pct: float = self.config.get("max_position_pct", 0.10)

        # Normalised allocation weights: strategy_id → weight
        self._weights: dict[str, float] = self._compute_weights()

        # Simulated positions for position-limit enforcement
        self._positions: dict[str, float] = {}  # ticker → current quantity (signed)
        self._prices: dict[str, float] = {}      # ticker → latest price

        # All strategies + macro strategy for event dispatch
        self._all_strategies: list[BaseStrategy] = list(self.strategies)
        if self.macro_strategy is not None:
            self._all_strategies.append(self.macro_strategy)

    # ── Main interface ────────────────────────────────────────────────────────

    def process_bar(
        self,
        ticker: str,
        bar: pd.Series,
        features: pd.DataFrame,
    ) -> list[Order]:
        """
        Dispatch a new bar to all strategies and collect aggregated orders.

        Parameters
        ----------
        ticker : str
            Asset symbol.
        bar : pd.Series
            Current OHLCV bar with keys: open, high, low, close, volume.
        features : pd.DataFrame
            Full feature matrix up to this bar (last row = current).

        Returns
        -------
        list[Order]
            Aggregated, weight-scaled, de-duplicated orders.
        """
        close = float(bar.get("close", 0.0))
        if close > 0:
            self._prices[ticker] = close

        # 1. Dispatch bar to all strategies
        for strategy in self._all_strategies:
            if ticker in strategy.tickers:
                strategy.on_bar(ticker, bar, features)

        # 2. Collect raw orders
        raw_orders: list[Order] = []
        for strategy in self.strategies:
            orders = strategy.generate_orders()
            raw_orders.extend(orders)

        if self.macro_strategy is not None:
            self.macro_strategy.generate_orders()  # consume (may emit earnings orders)

        # 3. Apply weights and macro multiplier
        scaled_orders = self._apply_weights(raw_orders)

        # 4. Aggregate overlapping orders
        aggregated = self._aggregate(scaled_orders)

        # 5. Enforce position limits
        final = self._enforce_limits(aggregated, close if close > 0 else None)

        if final:
            logger.debug(
                "Orchestrator [%s] %d raw → %d aggregated → %d final orders",
                ticker, len(raw_orders), len(aggregated), len(final),
            )

        return final

    def process_news(self, ticker: str, article: Any) -> list[Order]:
        """Dispatch a news article to all strategies and collect orders."""
        for strategy in self._all_strategies:
            if ticker in strategy.tickers:
                strategy.on_news(ticker, article)

        raw_orders: list[Order] = []
        for strategy in self.strategies:
            raw_orders.extend(strategy.generate_orders())

        scaled = self._apply_weights(raw_orders)
        aggregated = self._aggregate(scaled)
        return self._enforce_limits(aggregated, self._prices.get(ticker))

    def process_fundamental(self, ticker: str, snapshot: Any) -> list[Order]:
        """Dispatch a fundamental update to all strategies."""
        for strategy in self._all_strategies:
            if ticker in strategy.tickers:
                strategy.on_fundamental(ticker, snapshot)

        raw_orders: list[Order] = []
        for strategy in self.strategies:
            raw_orders.extend(strategy.generate_orders())

        scaled = self._apply_weights(raw_orders)
        aggregated = self._aggregate(scaled)
        return self._enforce_limits(aggregated, self._prices.get(ticker))

    # ── State management ─────────────────────────────────────────────────────

    def update_position(self, ticker: str, quantity_delta: float) -> None:
        """
        Called by the execution layer after a fill to update the orchestrator's
        view of current positions (used for position-limit enforcement).
        """
        self._positions[ticker] = self._positions.get(ticker, 0.0) + quantity_delta

    def update_capital(self, total_capital: float) -> None:
        """Update total portfolio value (called after each fill cycle)."""
        self.total_capital = total_capital

    def reset(self) -> None:
        """Reset all strategy states and position tracking (for new backtest run)."""
        for s in self._all_strategies:
            s.reset()
        self._positions.clear()
        self._prices.clear()

    # ── Macro regime interface ────────────────────────────────────────────────

    @property
    def current_regime(self) -> MacroRegime:
        if self.macro_strategy is not None:
            return self.macro_strategy.get_regime()
        return MacroRegime.RISK_ON

    @property
    def regime_multiplier(self) -> float:
        if self.macro_strategy is not None:
            return self.macro_strategy.get_regime_multiplier()
        return 1.0

    # ── Internal pipeline steps ───────────────────────────────────────────────

    def _apply_weights(self, orders: list[Order]) -> list[Order]:
        """
        Scale each order's quantity by:
            allocation_weight × normalised × regime_multiplier

        The weight is normalised so all enabled strategy weights sum to 1.
        """
        regime_mult = self.regime_multiplier
        result = []
        for order in orders:
            w = self._weights.get(order.strategy_id, 1.0)
            new_qty = order.quantity * w * regime_mult
            if new_qty < _MIN_ORDER_QTY:
                continue
            # Create modified copy
            new_order = Order(
                ticker=order.ticker,
                side=order.side,
                quantity=new_qty,
                order_type=order.order_type,
                strategy_id=order.strategy_id,
                confidence=order.confidence,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                timestamp=order.timestamp,
                metadata={**order.metadata, "allocation_weight": round(w, 4),
                           "regime_multiplier": round(regime_mult, 4)},
            )
            result.append(new_order)
        return result

    def _aggregate(self, orders: list[Order]) -> list[Order]:
        """
        Merge orders for the same (ticker, order_type) using averaging rules:

        * Same direction: average quantities, take max confidence.
        * Opposite directions: net the quantities; if net is negligible, skip.
        * Limit orders: keep separate by limit_price bucket.
        """
        # Group by (ticker, order_type, limit_price)
        groups: dict[tuple, list[Order]] = defaultdict(list)
        for o in orders:
            # Round limit price to 4 decimal places to group nearby quotes
            lp = round(o.limit_price or 0.0, 4)
            key = (o.ticker, o.order_type, lp)
            groups[key].append(o)

        result: list[Order] = []
        for (ticker, order_type, lp), grp in groups.items():
            buys = [o for o in grp if o.side == OrderSide.BUY]
            sells = [o for o in grp if o.side == OrderSide.SELL]

            buy_qty = sum(o.quantity for o in buys)
            sell_qty = sum(o.quantity for o in sells)
            net = buy_qty - sell_qty

            if abs(net) < _MIN_ORDER_QTY:
                continue  # Signals cancel out

            side = OrderSide.BUY if net > 0 else OrderSide.SELL
            qty = abs(net)
            all_orders = buys if net > 0 else sells
            confidence = max((o.confidence for o in all_orders), default=0.5)
            strategies = list({o.strategy_id for o in grp})

            merged = Order(
                ticker=ticker,
                side=side,
                quantity=qty,
                order_type=order_type,
                strategy_id="+".join(sorted(strategies)),
                confidence=confidence,
                limit_price=lp if order_type != OrderType.MARKET else None,
                metadata={"source_strategies": strategies, "raw_buy_qty": round(buy_qty, 4),
                           "raw_sell_qty": round(sell_qty, 4)},
            )
            result.append(merged)

        return result

    def _enforce_limits(
        self, orders: list[Order], current_price: float | None
    ) -> list[Order]:
        """
        Scale down any order that would push a position beyond max_position_pct.
        """
        result = []
        for order in orders:
            ticker = order.ticker
            price = current_price or self._prices.get(ticker, 1.0)
            if price <= 0:
                price = 1.0

            max_qty = (self.total_capital * self.max_position_pct) / price
            current_qty = self._positions.get(ticker, 0.0)

            if order.side == OrderSide.BUY:
                room = max_qty - max(0.0, current_qty)
            else:  # SELL
                room = max_qty + min(0.0, current_qty)  # room for short side

            room = max(0.0, room)
            allowed_qty = min(order.quantity, room)

            if allowed_qty < _MIN_ORDER_QTY:
                logger.debug(
                    "Orchestrator position limit: skip %s %s (room=%.2f max=%.2f)",
                    order.side.value, ticker, room, max_qty,
                )
                continue

            if allowed_qty < order.quantity:
                logger.info(
                    "Orchestrator position limit: scale %s %s %.2f → %.2f",
                    order.side.value, ticker, order.quantity, allowed_qty,
                )
                order = Order(
                    ticker=order.ticker,
                    side=order.side,
                    quantity=allowed_qty,
                    order_type=order.order_type,
                    strategy_id=order.strategy_id,
                    confidence=order.confidence,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    timestamp=order.timestamp,
                    metadata={**order.metadata, "scaled_from": round(order.quantity, 4)},
                )

            result.append(order)

        return result

    def _compute_weights(self) -> dict[str, float]:
        """
        Compute normalised allocation weights for all strategies.

        Weights are read from config's ``allocation_weight`` field and normalised
        to sum to 1.0 across all enabled strategies.
        """
        raw: dict[str, float] = {}
        for s in self.strategies:
            raw[s.strategy_id] = s.allocation_weight if s.is_enabled else 0.0

        total = sum(raw.values()) or 1.0
        normalised = {sid: w / total for sid, w in raw.items()}
        logger.debug("Orchestrator weights: %s", normalised)
        return normalised
