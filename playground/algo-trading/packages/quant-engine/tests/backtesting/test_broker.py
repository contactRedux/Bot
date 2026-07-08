"""
tests/backtesting/test_broker.py — Unit tests for SimulatedBroker.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backtesting.broker import (
    FixedPercentageSlippage,
    HalfSpreadSlippage,
    SimulatedBroker,
)
from backtesting.events import BarEvent, OrderEvent


TS = datetime(2023, 6, 1, 9, 30, tzinfo=timezone.utc)

BAR = BarEvent(
    timestamp=TS,
    ticker="AAPL",
    open=185.0,
    high=190.0,
    low=183.0,
    close=186.5,
    volume=5_000_000,
)


def _order(side="buy", order_type="market", quantity=10.0, limit_price=None, stop_price=None):
    return OrderEvent(
        timestamp=TS,
        ticker="AAPL",
        side=side,
        quantity=quantity,
        order_type=order_type,
        strategy_id="test",
        limit_price=limit_price,
        stop_price=stop_price,
    )


class TestSlippageModels:
    def test_fixed_percentage_buy(self):
        model = FixedPercentageSlippage(pct=0.001)
        price = model.apply("buy", close=100.0, high=101.0, low=99.0)
        assert price == pytest.approx(100.1)

    def test_fixed_percentage_sell(self):
        model = FixedPercentageSlippage(pct=0.001)
        price = model.apply("sell", close=100.0, high=101.0, low=99.0)
        assert price == pytest.approx(99.9)

    def test_half_spread_buy(self):
        model = HalfSpreadSlippage(half_spread=0.05)
        price = model.apply("buy", close=100.0, high=100.5, low=99.5)
        assert price == pytest.approx(100.05)

    def test_half_spread_sell(self):
        model = HalfSpreadSlippage(half_spread=0.05)
        price = model.apply("sell", close=100.0, high=100.5, low=99.5)
        assert price == pytest.approx(99.95)


class TestMarketOrders:
    def test_buy_market_fill_price_with_slippage(self):
        broker = SimulatedBroker(
            slippage_model=FixedPercentageSlippage(pct=0.001),
            commission_per_share=0.0,
            min_commission=0.0,
        )
        fills = broker.process_order(_order(side="buy"), BAR)
        assert len(fills) == 1
        fill = fills[0]
        assert fill.fill_price == pytest.approx(BAR.close * 1.001)
        assert fill.side == "buy"
        assert fill.quantity == 10.0

    def test_sell_market_fill_price(self):
        broker = SimulatedBroker(
            slippage_model=FixedPercentageSlippage(pct=0.001),
            commission_per_share=0.0,
            min_commission=0.0,
        )
        fills = broker.process_order(_order(side="sell"), BAR)
        assert len(fills) == 1
        assert fills[0].fill_price == pytest.approx(BAR.close * (1 - 0.001))

    def test_commission_min_applied(self):
        broker = SimulatedBroker(commission_per_share=0.001, min_commission=1.0)
        # quantity=1, commission_per_share × 1 = 0.001 < min_commission=1.0
        fills = broker.process_order(_order(quantity=1.0), BAR)
        assert fills[0].commission == pytest.approx(1.0)

    def test_commission_per_share_applied(self):
        broker = SimulatedBroker(commission_per_share=0.005, min_commission=1.0)
        # quantity=300 → 300 × 0.005 = 1.5 > 1.0
        fills = broker.process_order(_order(quantity=300.0), BAR)
        assert fills[0].commission == pytest.approx(1.5)


class TestLimitOrders:
    def test_buy_limit_fills_when_price_touches(self):
        # BAR.low = 183.0; limit = 184.0 → bar.low (183) <= limit (184) → fill
        broker = SimulatedBroker(min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="buy", order_type="limit", limit_price=184.0), BAR)
        assert len(fills) == 1

    def test_buy_limit_queued_when_price_not_reached(self):
        # limit = 180.0 < bar.low = 183.0 → NOT filled; bar.low never went to 180
        broker = SimulatedBroker(limit_order_ttl_bars=1, min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="buy", order_type="limit", limit_price=180.0), BAR)
        assert fills == []
        # Should be pending
        assert len(broker._pending.get("AAPL", [])) == 1

    def test_sell_limit_fills_when_high_exceeds_limit(self):
        # BAR.high = 190.0; limit = 189.0 → fill
        broker = SimulatedBroker(min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="sell", order_type="limit", limit_price=189.0), BAR)
        assert len(fills) == 1

    def test_sell_limit_queued_when_not_reached(self):
        # limit = 195.0 > bar.high = 190.0 → NOT filled
        broker = SimulatedBroker(limit_order_ttl_bars=1, min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="sell", order_type="limit", limit_price=195.0), BAR)
        assert fills == []

    def test_limit_order_expires(self):
        broker = SimulatedBroker(limit_order_ttl_bars=0, min_commission=0.0, commission_per_share=0.0)
        # Queue an order with ttl=0 (day order)
        broker.process_order(_order(side="buy", order_type="limit", limit_price=180.0), BAR)
        # Process next bar — should expire (ttl=0 means expire immediately if not filled)
        next_bar = BarEvent(
            timestamp=TS, ticker="AAPL",
            open=186.0, high=188.0, low=185.0, close=187.0, volume=1e6,
        )
        fills = broker.process_bar("AAPL", next_bar)
        assert fills == []
        assert broker._pending.get("AAPL", []) == []


class TestStopOrders:
    def test_buy_stop_triggers_when_high_crosses(self):
        # bar.high = 190 > stop = 188 → fill
        broker = SimulatedBroker(min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="buy", order_type="stop", stop_price=188.0), BAR)
        assert len(fills) == 1

    def test_sell_stop_triggers_when_low_crosses(self):
        # bar.low = 183 < stop = 184 → fill
        broker = SimulatedBroker(min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="sell", order_type="stop", stop_price=184.0), BAR)
        assert len(fills) == 1

    def test_stop_not_triggered(self):
        # bar.high = 190; stop = 195 → not triggered
        broker = SimulatedBroker(limit_order_ttl_bars=0, min_commission=0.0, commission_per_share=0.0)
        fills = broker.process_order(_order(side="buy", order_type="stop", stop_price=195.0), BAR)
        assert fills == []


class TestReset:
    def test_reset_clears_pending(self):
        broker = SimulatedBroker(limit_order_ttl_bars=5, min_commission=0.0, commission_per_share=0.0)
        broker.process_order(_order(side="buy", order_type="limit", limit_price=180.0), BAR)
        broker.reset()
        assert broker._pending == {}
