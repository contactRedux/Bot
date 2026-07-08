"""
tests/backtesting/test_partial_fills.py — Tests for partial fill logic in SimulatedBroker.

Phase 7: verifies that market orders are capped by the volume participation rate and
that the unfilled remainder is re-queued as a pending order for the next bar.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backtesting.broker import SimulatedBroker
from backtesting.events import BarEvent, OrderEvent


def _bar(
    ticker: str = "AAPL",
    close: float = 100.0,
    volume: float = 1_000_000.0,
    ts: datetime | None = None,
) -> BarEvent:
    ts = ts or datetime(2024, 1, 10, tzinfo=UTC)
    return BarEvent(
        timestamp=ts,
        ticker=ticker,
        open=99.0,
        high=101.0,
        low=98.0,
        close=close,
        volume=volume,
    )


def _order(
    ticker: str = "AAPL",
    qty: float = 100.0,
    side: str = "buy",
    order_type: str = "market",
) -> OrderEvent:
    return OrderEvent(
        timestamp=datetime(2024, 1, 10, tzinfo=UTC),
        ticker=ticker,
        side=side,
        quantity=qty,
        order_type=order_type,
    )


class TestPartialFillsDisabled:
    def test_full_fill_when_volume_rate_zero(self):
        """Default broker (rate=0) fills full quantity in one bar."""
        broker = SimulatedBroker(volume_participation_rate=0.0)
        bar = _bar(volume=100_000.0)
        order = _order(qty=500.0)
        fills = broker.process_order(order, bar)
        assert len(fills) == 1
        assert fills[0].quantity == 500.0

    def test_full_fill_regardless_of_volume(self):
        """Even with tiny volume, rate=0 gives a full fill."""
        broker = SimulatedBroker(volume_participation_rate=0.0)
        bar = _bar(volume=1.0)
        fills = broker.process_order(_order(qty=1_000_000.0), bar)
        assert fills[0].quantity == 1_000_000.0


class TestPartialFillsEnabled:
    def test_partial_fill_when_order_exceeds_bar_volume_cap(self):
        """
        With volume_participation_rate=0.05 and bar.volume=1_000_000,
        max fillable = 50_000.  An order for 200_000 should yield a
        50_000-share fill and queue 150_000 as pending.
        """
        broker = SimulatedBroker(
            volume_participation_rate=0.05,
            min_fill_pct=0.05,
            min_commission=0.0,
            commission_per_share=0.0,
        )
        bar = _bar(volume=1_000_000.0)
        order = _order(qty=200_000.0)

        fills = broker.process_order(order, bar)

        assert len(fills) == 1
        assert fills[0].quantity == pytest.approx(50_000.0)
        # Remainder should be queued as pending market order
        assert len(broker._pending.get("AAPL", [])) == 1
        pending_order = broker._pending["AAPL"][0].order
        assert pending_order.quantity == pytest.approx(150_000.0)
        assert pending_order.order_type == "market"

    def test_full_fill_when_order_below_volume_cap(self):
        """A small order fills completely even when partial fill mode is on."""
        broker = SimulatedBroker(
            volume_participation_rate=0.05,
            min_commission=0.0,
            commission_per_share=0.0,
        )
        bar = _bar(volume=1_000_000.0)  # cap = 50_000
        fills = broker.process_order(_order(qty=10_000.0), bar)
        assert len(fills) == 1
        assert fills[0].quantity == pytest.approx(10_000.0)
        assert len(broker._pending.get("AAPL", [])) == 0

    def test_remainder_fills_on_next_bar(self):
        """The pending partial fill is executed on the next process_bar() call."""
        broker = SimulatedBroker(
            volume_participation_rate=0.05,
            min_fill_pct=0.01,
            min_commission=0.0,
            commission_per_share=0.0,
        )
        bar1 = _bar(volume=100_000.0, ts=datetime(2024, 1, 10, tzinfo=UTC))
        order = _order(qty=10_000.0)  # cap = 5_000; remainder = 5_000
        fills1 = broker.process_order(order, bar1)
        assert len(fills1) == 1
        assert fills1[0].quantity == pytest.approx(5_000.0)

        bar2 = _bar(volume=1_000_000.0, ts=datetime(2024, 1, 11, tzinfo=UTC))
        fills2 = broker.process_bar("AAPL", bar2)
        assert len(fills2) >= 1  # remainder was filled on bar2
        total_filled = fills1[0].quantity + sum(f.quantity for f in fills2)
        assert total_filled >= 5_000.0 - 1e-6

    def test_dust_remainder_not_queued(self):
        """
        A remainder smaller than min_fill_pct × original_qty is silently dropped.
        """
        broker = SimulatedBroker(
            volume_participation_rate=0.05,
            min_fill_pct=0.5,   # 50% — only re-queue if >= half original
            min_commission=0.0,
            commission_per_share=0.0,
        )
        bar = _bar(volume=1_000_000.0)  # cap = 50_000
        # Order of 51_000: fills 50_000, remainder 1_000 < 50% * 51_000 → dropped
        fills = broker.process_order(_order(qty=51_000.0), bar)
        assert len(fills) == 1
        assert fills[0].quantity == pytest.approx(50_000.0)
        # Nothing pending (dust discarded)
        assert len(broker._pending.get("AAPL", [])) == 0


class TestLimitOrderFillLogic:
    def test_buy_limit_fills_only_when_bar_low_touches(self):
        """BUY limit fills if bar.low <= limit_price."""
        broker = SimulatedBroker()
        bar = _bar(close=100.0)
        bar.low = 98.0   # bar range 98–101
        order = OrderEvent(
            timestamp=bar.timestamp,
            ticker="AAPL",
            side="buy",
            quantity=10.0,
            order_type="limit",
            limit_price=99.0,  # inside bar range → should fill
        )
        fills = broker.process_order(order, bar)
        assert len(fills) == 1
        assert fills[0].fill_price <= 99.0 + 1.0  # at or near limit

    def test_buy_limit_does_not_fill_when_low_above_limit(self):
        """BUY limit does NOT fill when bar.low > limit_price."""
        broker = SimulatedBroker()
        bar = _bar(close=105.0)
        bar.low = 103.0   # lowest price is 103, above limit of 99
        order = OrderEvent(
            timestamp=bar.timestamp,
            ticker="AAPL",
            side="buy",
            quantity=10.0,
            order_type="limit",
            limit_price=99.0,
        )
        fills = broker.process_order(order, bar)
        assert len(fills) == 0

    def test_sell_limit_fills_when_bar_high_crosses(self):
        """SELL limit fills if bar.high >= limit_price."""
        broker = SimulatedBroker()
        bar = _bar(close=100.0)
        bar.high = 105.0
        order = OrderEvent(
            timestamp=bar.timestamp,
            ticker="AAPL",
            side="sell",
            quantity=10.0,
            order_type="limit",
            limit_price=103.0,
        )
        fills = broker.process_order(order, bar)
        assert len(fills) == 1

    def test_sell_limit_does_not_fill_when_high_below_limit(self):
        """SELL limit does NOT fill when bar.high < limit_price."""
        broker = SimulatedBroker()
        bar = _bar(close=100.0)
        bar.high = 101.0
        order = OrderEvent(
            timestamp=bar.timestamp,
            ticker="AAPL",
            side="sell",
            quantity=10.0,
            order_type="limit",
            limit_price=110.0,
        )
        fills = broker.process_order(order, bar)
        assert len(fills) == 0
