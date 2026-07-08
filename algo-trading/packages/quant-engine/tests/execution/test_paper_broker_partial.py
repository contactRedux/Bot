"""
tests/execution/test_paper_broker_partial.py — Tests for PaperBroker partial fill mode.

Phase 7: verifies that:
 - Default mode gives instant full fills (backward compat).
 - partial_fill_mode=True caps fills at simulated_bar_volume × rate.
 - Partial fill returns OrderStatus.PARTIAL.
 - Full fill when order fits within volume cap returns OrderStatus.FILLED.
"""
from __future__ import annotations

import pytest

from execution.base import OrderStatus
from execution.paper_broker import PaperBroker
from strategies.base import Order, OrderSide, OrderType


def _make_order(
    ticker: str = "AAPL",
    qty: float = 100.0,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
) -> Order:
    return Order(
        ticker=ticker,
        quantity=qty,
        side=side,
        order_type=order_type,
        strategy_id="test",
    )


@pytest.fixture
def paper_broker() -> PaperBroker:
    broker = PaperBroker(initial_cash=100_000.0)
    broker.update_prices({"AAPL": 150.0})
    return broker


@pytest.fixture
def partial_broker() -> PaperBroker:
    broker = PaperBroker(
        initial_cash=100_000.0,
        partial_fill_mode=True,
        volume_participation_rate=0.05,
        simulated_bar_volume=1_000_000.0,
    )
    broker.update_prices({"AAPL": 150.0})
    return broker


class TestDefaultModeFullFills:
    def test_market_order_full_fill(self, paper_broker: PaperBroker):
        """Default mode always gives full fill."""
        fill = paper_broker.submit_order(_make_order(qty=500.0))
        assert fill.status == OrderStatus.FILLED
        assert fill.filled_quantity == pytest.approx(500.0)

    def test_large_order_still_full_fill_in_default_mode(self, paper_broker: PaperBroker):
        fill = paper_broker.submit_order(_make_order(qty=1_000_000.0))
        assert fill.status == OrderStatus.FILLED
        assert fill.filled_quantity == pytest.approx(1_000_000.0)


class TestPartialFillMode:
    def test_partial_fill_returns_partial_status(self, partial_broker: PaperBroker):
        """
        With rate=0.05 and simulated_bar_volume=1_000_000, max_fillable=50_000.
        Order of 200_000 → PARTIAL fill of 50_000.
        """
        fill = partial_broker.submit_order(_make_order(qty=200_000.0))
        assert fill.status == OrderStatus.PARTIAL
        assert fill.filled_quantity == pytest.approx(50_000.0)

    def test_partial_fill_remaining_qty_in_metadata(self, partial_broker: PaperBroker):
        fill = partial_broker.submit_order(_make_order(qty=200_000.0))
        assert "remaining_qty" in fill.metadata
        assert fill.metadata["remaining_qty"] == pytest.approx(150_000.0)

    def test_full_fill_within_volume_cap_returns_filled(self, partial_broker: PaperBroker):
        """Order small enough to fit within volume cap → FILLED."""
        fill = partial_broker.submit_order(_make_order(qty=10_000.0))
        assert fill.status == OrderStatus.FILLED
        assert fill.filled_quantity == pytest.approx(10_000.0)

    def test_partial_fill_recorded_in_fills(self, partial_broker: PaperBroker):
        partial_broker.submit_order(_make_order(qty=200_000.0))
        assert len(partial_broker.fills) == 1
        assert partial_broker.fills[0].status == OrderStatus.PARTIAL

    def test_partial_fill_cash_reduced_correctly(self, partial_broker: PaperBroker):
        initial_cash = partial_broker._cash
        fill = partial_broker.submit_order(_make_order(qty=200_000.0, side=OrderSide.BUY))
        # Cash should decrease by fill value + commission, not full order value
        cash_spent = initial_cash - partial_broker._cash
        expected_cost = fill.fill_price * fill.filled_quantity
        # Allow for commission on top
        expected_with_comm = expected_cost * (1 + partial_broker.commission_rate)
        assert cash_spent == pytest.approx(expected_with_comm, rel=1e-4)

    def test_sell_partial_fill(self, partial_broker: PaperBroker):
        fill = partial_broker.submit_order(_make_order(qty=200_000.0, side=OrderSide.SELL))
        assert fill.status == OrderStatus.PARTIAL
        assert fill.filled_quantity == pytest.approx(50_000.0)

    def test_limit_order_not_affected_by_partial_mode(self, partial_broker: PaperBroker):
        """Limit orders always use the standard path — partial mode only applies to market."""
        order = _make_order(qty=200_000.0, order_type=OrderType.LIMIT)
        object.__setattr__(order, "limit_price", 140.0)  # below mark price → pending
        fill = partial_broker.submit_order(order)
        # Should be PENDING (not PARTIAL) since limit not met at 150.0
        assert fill.status == OrderStatus.PENDING
