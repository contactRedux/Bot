"""
tests/strategies/test_base.py — Tests for Order, TickerState, and BaseStrategy.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from strategies.base import BaseStrategy, Order, OrderSide, OrderType, TickerState
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Order tests
# ---------------------------------------------------------------------------

class TestOrder:
    def test_basic_creation(self):
        o = Order(ticker="AAPL", side=OrderSide.BUY, quantity=100.0, strategy_id="test")
        assert o.ticker == "AAPL"
        assert o.side == OrderSide.BUY
        assert o.quantity == 100.0

    def test_zero_quantity_raises(self):
        with pytest.raises(ValueError, match="positive"):
            Order(ticker="AAPL", side=OrderSide.BUY, quantity=0.0)

    def test_negative_quantity_raises(self):
        with pytest.raises(ValueError, match="positive"):
            Order(ticker="AAPL", side=OrderSide.BUY, quantity=-10.0)

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            Order(ticker="AAPL", side=OrderSide.BUY, quantity=10.0, confidence=1.5)

    def test_to_dict_contains_all_keys(self):
        o = Order(ticker="MSFT", side=OrderSide.SELL, quantity=50.0, confidence=0.7)
        d = o.to_dict()
        for key in ("ticker", "side", "quantity", "order_type", "strategy_id",
                    "confidence", "timestamp"):
            assert key in d

    def test_limit_order_has_price(self):
        o = Order(ticker="AAPL", side=OrderSide.BUY, quantity=10.0,
                  order_type=OrderType.LIMIT, limit_price=150.0)
        assert o.limit_price == 150.0

    def test_default_order_type_is_market(self):
        o = Order(ticker="AAPL", side=OrderSide.BUY, quantity=10.0)
        assert o.order_type == OrderType.MARKET

    def test_timestamp_defaults_to_utc(self):
        o = Order(ticker="AAPL", side=OrderSide.BUY, quantity=10.0)
        assert o.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# TickerState tests
# ---------------------------------------------------------------------------

class TestTickerState:
    def test_initial_state_is_flat(self):
        s = TickerState()
        assert s.is_flat
        assert not s.is_long
        assert not s.is_short

    def test_long_position(self):
        s = TickerState(position=100.0)
        assert s.is_long
        assert not s.is_short
        assert not s.is_flat

    def test_short_position(self):
        s = TickerState(position=-50.0)
        assert s.is_short
        assert not s.is_long
        assert not s.is_flat

    def test_extra_dict_accessible(self):
        s = TickerState()
        s.extra["stop_price"] = 140.0
        assert s.extra["stop_price"] == 140.0


# ---------------------------------------------------------------------------
# BaseStrategy (via concrete subclass)
# ---------------------------------------------------------------------------

class _MinimalStrategy(BaseStrategy):
    def __init__(self, config, tickers):
        super().__init__("minimal", config, tickers)
        self._orders = []

    def on_bar(self, ticker, bar, features):
        self._orders.append(
            self._make_order(ticker, OrderSide.BUY, 10.0, 0.5)
        )

    def generate_orders(self):
        o = list(self._orders)
        self._orders.clear()
        return o


def _make_bar(close: float = 150.0) -> pd.Series:
    return pd.Series({"open": close, "high": close, "low": close, "close": close, "volume": 1e6})

def _make_features(n: int = 50, n_cols: int = 10) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(rng.standard_normal((n, n_cols)),
                        columns=[f"f{i}" for i in range(n_cols)])


class TestBaseStrategy:
    def test_strategy_id_set(self):
        s = _MinimalStrategy({}, ["AAPL"])
        assert s.strategy_id == "minimal"

    def test_on_bar_generates_order(self):
        s = _MinimalStrategy({"enabled": True}, ["AAPL"])
        s.on_bar("AAPL", _make_bar(), _make_features())
        orders = s.generate_orders()
        assert len(orders) == 1
        assert orders[0].strategy_id == "minimal"

    def test_disabled_strategy(self):
        s = _MinimalStrategy({"enabled": False}, ["AAPL"])
        assert not s.is_enabled

    def test_allocation_weight_defaults(self):
        s = _MinimalStrategy({}, ["AAPL"])
        assert s.allocation_weight == pytest.approx(0.1)

    def test_allocation_weight_from_config(self):
        s = _MinimalStrategy({"allocation_weight": 0.25}, ["AAPL"])
        assert s.allocation_weight == pytest.approx(0.25)

    def test_reset_clears_state(self):
        s = _MinimalStrategy({}, ["AAPL"])
        s._state["AAPL"].position = 100.0
        s.reset()
        assert s._state["AAPL"].is_flat

    def test_state_for_unknown_ticker(self):
        s = _MinimalStrategy({}, ["AAPL"])
        state = s._state_for("TSLA")  # not in tickers — creates lazily
        assert isinstance(state, TickerState)
        assert state.is_flat
