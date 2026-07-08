"""
tests/execution/test_base.py — Unit tests for FillEvent and OrderStatus.
"""
from __future__ import annotations

import pytest

from execution.base import FillEvent, OrderStatus
from strategies.base import Order, OrderSide, OrderType


def _order(ticker="AAPL", side=OrderSide.BUY, qty=10.0) -> Order:
    return Order(ticker=ticker, side=side, quantity=qty,
                 order_type=OrderType.MARKET, strategy_id="test")


class TestOrderStatus:
    def test_all_status_values_defined(self):
        statuses = {s.value for s in OrderStatus}
        for expected in ("pending", "filled", "partial", "cancelled", "rejected", "expired"):
            assert expected in statuses


class TestFillEvent:
    def test_is_filled_true_for_filled(self):
        fill = FillEvent(
            order=_order(), status=OrderStatus.FILLED,
            filled_quantity=10.0, fill_price=100.0,
        )
        assert fill.is_filled

    def test_is_filled_false_for_rejected(self):
        fill = FillEvent(
            order=_order(), status=OrderStatus.REJECTED,
            filled_quantity=0.0, fill_price=0.0,
        )
        assert not fill.is_filled

    def test_net_value_buy(self):
        # BUY 10 @ $100, commission $1 → net_value = -(1000 + 1) = -1001
        fill = FillEvent(
            order=_order(side=OrderSide.BUY, qty=10.0),
            status=OrderStatus.FILLED,
            filled_quantity=10.0,
            fill_price=100.0,
            commission=1.0,
        )
        assert fill.net_value == pytest.approx(-1001.0)

    def test_net_value_sell(self):
        # SELL 10 @ $100, commission $1 → net_value = 1000 - 1 = 999
        fill = FillEvent(
            order=_order(side=OrderSide.SELL, qty=10.0),
            status=OrderStatus.FILLED,
            filled_quantity=10.0,
            fill_price=100.0,
            commission=1.0,
        )
        assert fill.net_value == pytest.approx(999.0)

    def test_ticker_delegates_to_order(self):
        fill = FillEvent(
            order=_order(ticker="NVDA"),
            status=OrderStatus.FILLED,
            filled_quantity=5.0,
            fill_price=500.0,
        )
        assert fill.ticker == "NVDA"

    def test_side_delegates_to_order(self):
        fill = FillEvent(
            order=_order(side=OrderSide.SELL),
            status=OrderStatus.FILLED,
            filled_quantity=1.0,
            fill_price=100.0,
        )
        assert fill.side == "sell"

    def test_to_dict_complete(self):
        fill = FillEvent(
            order=_order(side=OrderSide.BUY, qty=2.0),
            status=OrderStatus.FILLED,
            filled_quantity=2.0,
            fill_price=150.0,
            commission=0.3,
            slippage=0.05,
            broker_order_id="abc123",
        )
        d = fill.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["side"] == "buy"
        assert d["status"] == "filled"
        assert d["filled_quantity"] == pytest.approx(2.0)
        assert d["fill_price"] == pytest.approx(150.0)
        assert d["commission"] == pytest.approx(0.3)
        assert d["broker_order_id"] == "abc123"

    def test_strategy_id_delegates_to_order(self):
        order = Order(ticker="AAPL", side=OrderSide.BUY, quantity=1.0,
                      order_type=OrderType.MARKET, strategy_id="stat_arb")
        fill = FillEvent(order=order, status=OrderStatus.FILLED,
                         filled_quantity=1.0, fill_price=100.0)
        assert fill.strategy_id == "stat_arb"
