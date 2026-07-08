"""
tests/strategies/test_orchestrator.py — Tests for StrategyOrchestrator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.base import Order, OrderSide, OrderType
from strategies.orchestrator import StrategyOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(close: float = 150.0) -> pd.Series:
    return pd.Series({"open": close, "high": close * 1.005,
                      "low": close * 0.995, "close": close, "volume": 1e6})


def _make_features(n: int = 50) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    data = {
        "close": 100 + np.cumsum(rng.normal(0, 0.5, n)),
        "adx": rng.uniform(15, 35, n),
        "bb_pct_b": rng.uniform(0.1, 0.9, n),
        "atr": rng.uniform(0.5, 2.0, n),
        "vix": rng.uniform(12, 20, n),
        "yield_curve_slope": rng.uniform(0.3, 1.5, n),
    }
    return pd.DataFrame(data)


def _make_order(ticker="AAPL", side=OrderSide.BUY, qty=100.0,
                strategy_id="s1", confidence=0.8,
                order_type=OrderType.MARKET) -> Order:
    return Order(ticker=ticker, side=side, quantity=qty,
                 strategy_id=strategy_id, confidence=confidence,
                 order_type=order_type)


class _FixedOutputStrategy:
    """A minimal strategy that always emits a pre-configured list of orders."""

    def __init__(self, strategy_id, orders, allocation_weight=0.5):
        self.strategy_id = strategy_id
        self.tickers = ["AAPL", "MSFT", "BTC-USD"]
        self._orders = list(orders)
        self._is_enabled = True
        self.config = {"allocation_weight": allocation_weight, "enabled": True}

    def on_bar(self, ticker, bar, features):
        pass

    def on_news(self, ticker, article):
        pass

    def on_fundamental(self, ticker, snapshot):
        pass

    def generate_orders(self):
        o = list(self._orders)
        return o  # always return same orders

    def reset(self):
        pass

    @property
    def is_enabled(self):
        return self._is_enabled

    @property
    def allocation_weight(self):
        return self.config["allocation_weight"]


# ---------------------------------------------------------------------------
# Weight computation
# ---------------------------------------------------------------------------

class TestWeightComputation:
    def test_weights_sum_to_one(self):
        s1 = _FixedOutputStrategy("s1", [], allocation_weight=0.30)
        s2 = _FixedOutputStrategy("s2", [], allocation_weight=0.20)
        s3 = _FixedOutputStrategy("s3", [], allocation_weight=0.50)
        orch = StrategyOrchestrator([s1, s2, s3], total_capital=100_000.0)
        total = sum(orch._weights.values())
        assert total == pytest.approx(1.0)

    def test_equal_weights_when_equal_config(self):
        s1 = _FixedOutputStrategy("s1", [], allocation_weight=0.25)
        s2 = _FixedOutputStrategy("s2", [], allocation_weight=0.25)
        orch = StrategyOrchestrator([s1, s2], total_capital=100_000.0)
        assert orch._weights["s1"] == pytest.approx(0.5)
        assert orch._weights["s2"] == pytest.approx(0.5)

    def test_weights_scale_order_quantity(self):
        """A strategy with weight 0.5 should have its order qty halved."""
        s1 = _FixedOutputStrategy("s1", [_make_order(qty=100.0, strategy_id="s1")],
                                   allocation_weight=0.5)
        s2 = _FixedOutputStrategy("s2", [], allocation_weight=0.5)
        orch = StrategyOrchestrator([s1, s2], total_capital=10_000_000.0)
        scaled = orch._apply_weights([_make_order(qty=100.0, strategy_id="s1")])
        # weight of s1 = 0.5 → scaled qty = 100 * 0.5 = 50
        assert len(scaled) == 1
        assert scaled[0].quantity == pytest.approx(50.0, rel=0.01)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_same_direction_orders_averaged(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0)
        orders = [
            _make_order("AAPL", OrderSide.BUY, 60.0, "s1"),
            _make_order("AAPL", OrderSide.BUY, 40.0, "s2"),
        ]
        result = orch._aggregate(orders)
        assert len(result) == 1
        assert result[0].side == OrderSide.BUY
        assert result[0].quantity == pytest.approx(100.0)

    def test_opposite_orders_net_out(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0)
        orders = [
            _make_order("AAPL", OrderSide.BUY, 60.0, "s1"),
            _make_order("AAPL", OrderSide.SELL, 60.0, "s2"),
        ]
        result = orch._aggregate(orders)
        assert len(result) == 0  # they cancel

    def test_partial_net_emits_net_order(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0)
        orders = [
            _make_order("AAPL", OrderSide.BUY, 80.0, "s1"),
            _make_order("AAPL", OrderSide.SELL, 30.0, "s2"),
        ]
        result = orch._aggregate(orders)
        assert len(result) == 1
        assert result[0].side == OrderSide.BUY
        assert result[0].quantity == pytest.approx(50.0)

    def test_different_tickers_not_merged(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0)
        orders = [
            _make_order("AAPL", OrderSide.BUY, 50.0, "s1"),
            _make_order("MSFT", OrderSide.BUY, 50.0, "s1"),
        ]
        result = orch._aggregate(orders)
        assert len(result) == 2
        tickers = {o.ticker for o in result}
        assert tickers == {"AAPL", "MSFT"}

    def test_confidence_is_max_of_group(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0)
        orders = [
            _make_order("AAPL", OrderSide.BUY, 50.0, "s1", confidence=0.6),
            _make_order("AAPL", OrderSide.BUY, 50.0, "s2", confidence=0.9),
        ]
        result = orch._aggregate(orders)
        assert result[0].confidence == pytest.approx(0.9)

    def test_limit_orders_kept_separate_by_price(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0)
        orders = [
            Order("AAPL", OrderSide.BUY, 10.0, OrderType.LIMIT, "s1", 0.7,
                  limit_price=149.50),
            Order("AAPL", OrderSide.BUY, 10.0, OrderType.LIMIT, "s2", 0.7,
                  limit_price=150.00),
        ]
        result = orch._aggregate(orders)
        assert len(result) == 2  # different prices → not merged


# ---------------------------------------------------------------------------
# Position limits
# ---------------------------------------------------------------------------

class TestPositionLimits:
    def test_order_within_limit_passes(self):
        orch = StrategyOrchestrator([], total_capital=100_000.0,
                                    config={"max_position_pct": 0.10})
        orders = [_make_order("AAPL", OrderSide.BUY, 10.0)]  # $150 × 10 = $1500 < $10000
        result = orch._enforce_limits(orders, current_price=150.0)
        assert len(result) == 1
        assert result[0].quantity == pytest.approx(10.0)

    def test_order_exceeding_limit_scaled_down(self):
        orch = StrategyOrchestrator([], total_capital=10_000.0,
                                    config={"max_position_pct": 0.10})
        # Max qty = 10_000 * 0.10 / 150 = 6.67
        orders = [_make_order("AAPL", OrderSide.BUY, 100.0)]  # way too large
        result = orch._enforce_limits(orders, current_price=150.0)
        assert len(result) == 1
        max_qty = 10_000.0 * 0.10 / 150.0
        assert result[0].quantity <= max_qty + 0.01

    def test_existing_position_reduces_room(self):
        orch = StrategyOrchestrator([], total_capital=10_000.0,
                                    config={"max_position_pct": 0.10})
        orch._positions["AAPL"] = 5.0  # already own 5 shares @ $150 = $750
        # Max allowed = 6.67; room = 6.67 - 5 = 1.67
        orders = [_make_order("AAPL", OrderSide.BUY, 50.0)]
        result = orch._enforce_limits(orders, current_price=150.0)
        assert len(result) == 1
        assert result[0].quantity < 3.0  # significantly less than requested

    def test_order_with_no_room_dropped(self):
        orch = StrategyOrchestrator([], total_capital=10_000.0,
                                    config={"max_position_pct": 0.10})
        # Position fills the entire limit
        max_qty = 10_000.0 * 0.10 / 150.0
        orch._positions["AAPL"] = max_qty
        orders = [_make_order("AAPL", OrderSide.BUY, 10.0)]
        result = orch._enforce_limits(orders, current_price=150.0)
        assert len(result) == 0  # no room left


# ---------------------------------------------------------------------------
# Macro regime integration
# ---------------------------------------------------------------------------

class TestMacroRegimeIntegration:
    def test_fear_regime_reduces_order_quantity(self):
        """In RISK_OFF regime, orders should be scaled by fear_reduction_factor."""
        from strategies.macro_factor import MacroFactorStrategy, MacroRegime
        cfg = {
            "enabled": True, "allocation_weight": 0.12,
            "vix_fear_threshold": 25.0, "fear_reduction_factor": 0.50,
            "yield_curve_inversion_threshold": 0.0, "equity_reduction_on_inversion": 0.40,
            "earnings_surprise_z": 2.0, "regime_update_interval_bars": 1,
        }
        macro = MacroFactorStrategy(cfg, ["AAPL"])
        macro._regime = MacroRegime.RISK_OFF  # Force fear regime

        s1 = _FixedOutputStrategy("s1", [_make_order(qty=100.0, strategy_id="s1")],
                                   allocation_weight=1.0)
        orch = StrategyOrchestrator([s1], macro_strategy=macro, total_capital=10_000_000.0)

        # regime_multiplier = 0.5 → 100 * 1.0 (weight) * 0.5 (fear) = 50
        scaled = orch._apply_weights([_make_order(qty=100.0, strategy_id="s1")])
        assert len(scaled) == 1
        assert scaled[0].quantity == pytest.approx(50.0, rel=0.01)

    def test_crisis_regime_severe_reduction(self):
        from strategies.macro_factor import MacroFactorStrategy, MacroRegime
        cfg = {
            "enabled": True, "allocation_weight": 0.12,
            "vix_fear_threshold": 25.0, "fear_reduction_factor": 0.50,
            "yield_curve_inversion_threshold": 0.0, "equity_reduction_on_inversion": 0.40,
            "earnings_surprise_z": 2.0, "regime_update_interval_bars": 1,
        }
        macro = MacroFactorStrategy(cfg, ["AAPL"])
        macro._regime = MacroRegime.CRISIS

        s1 = _FixedOutputStrategy("s1", [], allocation_weight=1.0)
        orch = StrategyOrchestrator([s1], macro_strategy=macro, total_capital=10_000_000.0)

        assert orch.regime_multiplier == pytest.approx(0.25)

    def test_risk_on_regime_no_reduction(self):
        from strategies.macro_factor import MacroFactorStrategy, MacroRegime
        cfg = {
            "enabled": True, "allocation_weight": 0.12,
            "vix_fear_threshold": 25.0, "fear_reduction_factor": 0.50,
            "yield_curve_inversion_threshold": 0.0, "equity_reduction_on_inversion": 0.40,
            "earnings_surprise_z": 2.0, "regime_update_interval_bars": 1,
        }
        macro = MacroFactorStrategy(cfg, ["AAPL"])
        macro._regime = MacroRegime.RISK_ON

        s1 = _FixedOutputStrategy("s1", [], allocation_weight=1.0)
        orch = StrategyOrchestrator([s1], macro_strategy=macro, total_capital=10_000_000.0)
        assert orch.regime_multiplier == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Full process_bar integration
# ---------------------------------------------------------------------------

class TestProcessBar:
    def test_process_bar_returns_list(self):
        s1 = _FixedOutputStrategy("s1", [], allocation_weight=1.0)
        orch = StrategyOrchestrator([s1], total_capital=100_000.0)
        result = orch.process_bar("AAPL", _make_bar(), _make_features())
        assert isinstance(result, list)

    def test_process_bar_calls_on_bar(self):
        called = []

        class Tracker(_FixedOutputStrategy):
            def on_bar(self, ticker, bar, features):
                called.append(ticker)

        s = Tracker("s1", [])
        orch = StrategyOrchestrator([s], total_capital=100_000.0)
        orch.process_bar("AAPL", _make_bar(), _make_features())
        assert "AAPL" in called

    def test_position_update_affects_limits(self):
        orch = StrategyOrchestrator([], total_capital=10_000.0,
                                    config={"max_position_pct": 0.10})
        orch.update_position("AAPL", 5.0)
        assert orch._positions["AAPL"] == pytest.approx(5.0)

    def test_reset_clears_all(self):
        s1 = _FixedOutputStrategy("s1", [])
        orch = StrategyOrchestrator([s1], total_capital=100_000.0)
        orch._positions["AAPL"] = 50.0
        orch._prices["AAPL"] = 150.0
        orch.reset()
        assert "AAPL" not in orch._positions
        assert "AAPL" not in orch._prices

    def test_multi_strategy_orders_aggregated(self):
        """Two strategies buying the same ticker → merged into one order."""
        s1 = _FixedOutputStrategy("s1", [_make_order("AAPL", OrderSide.BUY, 50.0, "s1")],
                                   allocation_weight=0.5)
        s2 = _FixedOutputStrategy("s2", [_make_order("AAPL", OrderSide.BUY, 50.0, "s2")],
                                   allocation_weight=0.5)
        orch = StrategyOrchestrator([s1, s2], total_capital=100_000_000.0)
        result = orch.process_bar("AAPL", _make_bar(), _make_features())
        # Both buy → merged to one order with combined (net) quantity
        aapl_orders = [o for o in result if o.ticker == "AAPL"]
        assert len(aapl_orders) == 1
        assert aapl_orders[0].side == OrderSide.BUY

    def test_conflicting_signals_cancel(self):
        """One strategy buys, another sells → they should net to zero."""
        s1 = _FixedOutputStrategy("s1", [_make_order("AAPL", OrderSide.BUY, 50.0, "s1")],
                                   allocation_weight=0.5)
        s2 = _FixedOutputStrategy("s2", [_make_order("AAPL", OrderSide.SELL, 50.0, "s2")],
                                   allocation_weight=0.5)
        orch = StrategyOrchestrator([s1, s2], total_capital=100_000_000.0)
        result = orch.process_bar("AAPL", _make_bar(), _make_features())
        aapl_orders = [o for o in result if o.ticker == "AAPL"]
        # Net = 25 - 25 = 0 → no order emitted
        assert len(aapl_orders) == 0
