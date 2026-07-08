"""
tests/execution/test_paper_broker.py — Unit tests for PaperBroker.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from execution.base import FillEvent, OrderStatus
from execution.paper_broker import PaperBroker
from strategies.base import Order, OrderSide, OrderType


TS = datetime(2023, 6, 1, tzinfo=timezone.utc)


def _order(
    ticker: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    qty: float = 10.0,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
    stop_price: float | None = None,
) -> Order:
    return Order(
        ticker=ticker,
        side=side,
        quantity=qty,
        order_type=order_type,
        strategy_id="test",
        limit_price=limit_price,
        stop_price=stop_price,
    )


def _broker(**kwargs) -> PaperBroker:
    defaults = {"initial_cash": 100_000.0, "commission_rate": 0.001, "seed": 42}
    defaults.update(kwargs)
    return PaperBroker(**defaults)


# ---------------------------------------------------------------------------
# Connection & price
# ---------------------------------------------------------------------------

class TestPaperBrokerConnection:
    def test_always_connected(self):
        broker = _broker()
        assert broker.is_connected

    def test_heartbeat_true(self):
        assert _broker().heartbeat()

    def test_no_price_rejects_order(self):
        broker = _broker()
        fill = broker.submit_order(_order())
        assert fill.status == OrderStatus.REJECTED

    def test_account_keys_present(self):
        broker = _broker()
        acct = broker.get_account()
        for key in ("cash", "portfolio_value", "buying_power", "broker"):
            assert key in acct
        assert acct["broker"] == "paper"


# ---------------------------------------------------------------------------
# Market order fills
# ---------------------------------------------------------------------------

class TestMarketOrders:
    def test_market_buy_fills_at_mark_price(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(qty=5.0))
        assert fill.status == OrderStatus.FILLED
        assert fill.filled_quantity == pytest.approx(5.0)
        assert fill.fill_price == pytest.approx(100.0)

    def test_market_sell_fills_at_mark_price(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(side=OrderSide.SELL, qty=5.0))
        assert fill.status == OrderStatus.FILLED
        assert fill.fill_price == pytest.approx(100.0)

    def test_commission_applied(self):
        broker = _broker(commission_rate=0.001, fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(qty=10.0))
        # commission = 100.0 × 10 × 0.001 = 1.0
        assert fill.commission == pytest.approx(1.0)

    def test_fill_is_fill_event(self):
        broker = _broker()
        broker.update_prices({"AAPL": 150.0})
        fill = broker.submit_order(_order())
        assert isinstance(fill, FillEvent)

    def test_fills_history_grows(self):
        broker = _broker()
        broker.update_prices({"AAPL": 100.0})
        broker.submit_order(_order(qty=1.0))
        broker.submit_order(_order(qty=2.0))
        assert len(broker.fills) == 2


# ---------------------------------------------------------------------------
# Slippage
# ---------------------------------------------------------------------------

class TestSlippage:
    def test_buy_fill_price_above_mark(self):
        """BUY slippage raises fill price above mid."""
        broker = _broker(fixed_slippage_pct=0.01, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(qty=1.0))
        assert fill.fill_price == pytest.approx(101.0, rel=1e-4)

    def test_sell_fill_price_below_mark(self):
        """SELL slippage lowers fill price below mid."""
        broker = _broker(fixed_slippage_pct=0.01, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(side=OrderSide.SELL, qty=1.0))
        assert fill.fill_price == pytest.approx(99.0, rel=1e-4)

    def test_zero_slippage_fills_at_exact_price(self):
        broker = _broker(fixed_slippage_pct=0.0, vol_impact_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 200.0})
        fill = broker.submit_order(_order(qty=1.0))
        assert fill.fill_price == pytest.approx(200.0)

    def test_random_slippage_seeded_reproducible(self):
        """Same seed → same slippage on successive runs."""
        broker_a = _broker(random_slippage_pct=0.005, seed=1)
        broker_b = _broker(random_slippage_pct=0.005, seed=1)
        broker_a.update_prices({"X": 50.0})
        broker_b.update_prices({"X": 50.0})
        fill_a = broker_a.submit_order(_order("X"))
        fill_b = broker_b.submit_order(_order("X"))
        assert fill_a.fill_price == pytest.approx(fill_b.fill_price)


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------

class TestLimitOrders:
    def test_limit_buy_fills_when_price_meets_limit(self):
        """If mark <= limit, fill immediately at limit price."""
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 95.0})  # mark < limit → immediate fill
        order = _order(order_type=OrderType.LIMIT, limit_price=100.0)
        fill = broker.submit_order(order)
        assert fill.status == OrderStatus.FILLED
        assert fill.fill_price == pytest.approx(100.0)

    def test_limit_buy_pends_when_price_above_limit(self):
        broker = _broker()
        broker.update_prices({"AAPL": 110.0})  # mark > limit → pending
        order = _order(order_type=OrderType.LIMIT, limit_price=100.0)
        fill = broker.submit_order(order)
        assert fill.status == OrderStatus.PENDING
        assert broker.pending_count == 1

    def test_pending_limit_fills_when_price_drops(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 110.0})
        order = _order(order_type=OrderType.LIMIT, limit_price=100.0)
        broker.submit_order(order)
        assert broker.pending_count == 1

        # Price drops to meet limit
        broker.update_prices({"AAPL": 98.0})
        fills = broker.check_pending_orders()
        assert len(fills) == 1
        assert fills[0].status == OrderStatus.FILLED
        assert broker.pending_count == 0

    def test_limit_sell_pends_when_price_below_limit(self):
        broker = _broker()
        broker.update_prices({"AAPL": 90.0})
        order = _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=100.0)
        fill = broker.submit_order(order)
        assert fill.status == OrderStatus.PENDING

    def test_limit_buy_missing_price_rejected(self):
        broker = _broker()
        order = Order(
            ticker="AAPL", side=OrderSide.BUY, quantity=1.0,
            order_type=OrderType.LIMIT, strategy_id="x",
            # limit_price intentionally not set
        )
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(order)
        assert fill.status == OrderStatus.REJECTED

    def test_cancel_pending_order(self):
        broker = _broker()
        broker.update_prices({"AAPL": 110.0})
        order = _order(order_type=OrderType.LIMIT, limit_price=100.0)
        fill = broker.submit_order(order)
        assert fill.status == OrderStatus.PENDING
        assert broker.cancel_order(fill.broker_order_id)
        assert broker.pending_count == 0


# ---------------------------------------------------------------------------
# FillEvent properties
# ---------------------------------------------------------------------------

class TestFillEventProperties:
    def test_net_value_buy_is_negative(self):
        """BUY → cash flows out → net_value negative."""
        broker = _broker(commission_rate=0.0, fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(qty=10.0))
        # cost = 100 × 10 = 1000; commission=0 → net_value = -1000
        assert fill.net_value == pytest.approx(-1000.0)

    def test_net_value_sell_is_positive(self):
        broker = _broker(commission_rate=0.0, fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(side=OrderSide.SELL, qty=10.0))
        assert fill.net_value == pytest.approx(1000.0)

    def test_is_filled_property(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(qty=1.0))
        assert fill.is_filled

    def test_to_dict_has_required_keys(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        fill = broker.submit_order(_order(qty=1.0))
        d = fill.to_dict()
        for key in ("ticker", "side", "status", "filled_quantity", "fill_price", "commission"):
            assert key in d

    def test_ticker_and_side_delegates_to_order(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"MSFT": 300.0})
        fill = broker.submit_order(_order(ticker="MSFT", side=OrderSide.SELL, qty=2.0))
        assert fill.ticker == "MSFT"
        assert fill.side == "sell"

    def test_strategy_id_propagated(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0})
        order = Order(ticker="AAPL", side=OrderSide.BUY, quantity=1.0,
                      strategy_id="momentum", order_type=OrderType.MARKET)
        fill = broker.submit_order(order)
        assert fill.strategy_id == "momentum"


# ---------------------------------------------------------------------------
# Multi-asset
# ---------------------------------------------------------------------------

class TestMultiAsset:
    def test_independent_fills_per_ticker(self):
        broker = _broker(fixed_slippage_pct=0.0, random_slippage_pct=0.0)
        broker.update_prices({"AAPL": 100.0, "MSFT": 300.0, "BTC-USD": 60_000.0})
        fills = [
            broker.submit_order(_order("AAPL", qty=5.0)),
            broker.submit_order(_order("MSFT", qty=2.0)),
            broker.submit_order(_order("BTC-USD", qty=0.1)),
        ]
        for f in fills:
            assert f.is_filled
        assert fills[0].fill_price == pytest.approx(100.0)
        assert fills[1].fill_price == pytest.approx(300.0)
        assert fills[2].fill_price == pytest.approx(60_000.0)
