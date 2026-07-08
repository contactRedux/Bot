"""
tests/backtesting/test_events.py — Unit tests for backtesting event types.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backtesting.events import (
    BarEvent,
    EventType,
    FillEvent,
    HaltEvent,
    OrderEvent,
    SignalEvent,
)


TS = datetime(2023, 6, 1, 9, 30, tzinfo=timezone.utc)


class TestBarEvent:
    def test_basic_construction(self):
        ev = BarEvent(
            timestamp=TS,
            ticker="AAPL",
            open=185.0,
            high=187.0,
            low=184.0,
            close=186.5,
            volume=5_000_000,
        )
        assert ev.ticker == "AAPL"
        assert ev.close == 186.5
        assert ev.event_type == EventType.BAR

    def test_to_series(self):
        ev = BarEvent(timestamp=TS, ticker="AAPL", open=1, high=2, low=0.5, close=1.5, volume=100)
        s = ev.to_series()
        assert set(s.keys()) == {"open", "high", "low", "close", "volume"}
        assert s["close"] == 1.5

    def test_sort_index_priority(self):
        """BarEvents at the same timestamp should sort before OrderEvents."""
        bar = BarEvent(timestamp=TS, ticker="X", open=1, high=1, low=1, close=1, volume=0)
        order = OrderEvent(timestamp=TS, ticker="X", side="buy", quantity=10, order_type="market")
        assert bar < order  # BarEvent priority = 0, OrderEvent priority = 2


class TestOrderEvent:
    def test_defaults(self):
        ev = OrderEvent(
            timestamp=TS,
            ticker="MSFT",
            side="sell",
            quantity=5.0,
            order_type="limit",
            limit_price=310.0,
        )
        assert ev.event_type == EventType.ORDER
        assert ev.limit_price == 310.0

    def test_metadata_stored(self):
        ev = OrderEvent(
            timestamp=TS,
            ticker="BTC-USD",
            side="buy",
            quantity=0.5,
            order_type="market",
            metadata={"source_strategies": ["momentum"]},
        )
        assert ev.metadata["source_strategies"] == ["momentum"]


class TestFillEvent:
    def test_net_cost_buy(self):
        fill = FillEvent(
            timestamp=TS,
            ticker="AAPL",
            side="buy",
            quantity=10.0,
            fill_price=186.5,
            commission=1.0,
        )
        # Net cost = 186.5 × 10 + 1.0 = 1866
        assert fill.net_cost == pytest.approx(1866.0)

    def test_net_cost_sell(self):
        fill = FillEvent(
            timestamp=TS,
            ticker="AAPL",
            side="sell",
            quantity=10.0,
            fill_price=190.0,
            commission=1.0,
        )
        # Net cost = -(190 × 10) + 1 = -1900 + 1 = -1899
        assert fill.net_cost == pytest.approx(-1899.0)

    def test_trade_value(self):
        fill = FillEvent(
            timestamp=TS, ticker="X", side="buy",
            quantity=5.0, fill_price=100.0, commission=0.5,
        )
        assert fill.trade_value == pytest.approx(500.0)


class TestHaltEvent:
    def test_construction(self):
        ev = HaltEvent(timestamp=TS, reason="max drawdown breached")
        assert ev.event_type == EventType.HALT
        assert "drawdown" in ev.reason


class TestEventOrdering:
    def test_chronological_sort(self):
        """Events should sort by timestamp then by priority."""
        ts1 = datetime(2023, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2023, 1, 2, tzinfo=timezone.utc)
        events = [
            OrderEvent(timestamp=ts2, ticker="X", side="buy", quantity=1, order_type="market"),
            BarEvent(timestamp=ts1, ticker="X", open=1, high=1, low=1, close=1, volume=0),
            FillEvent(timestamp=ts2, ticker="X", side="buy", quantity=1, fill_price=1.0),
            BarEvent(timestamp=ts2, ticker="X", open=1, high=1, low=1, close=1, volume=0),
        ]
        sorted_events = sorted(events)
        # ts1 BarEvent first
        assert sorted_events[0].timestamp == ts1
        assert sorted_events[0].event_type == EventType.BAR
        # ts2 BarEvent before ts2 OrderEvent before ts2 FillEvent
        ts2_events = [e for e in sorted_events if e.timestamp == ts2]
        assert ts2_events[0].event_type == EventType.BAR
        assert ts2_events[1].event_type == EventType.ORDER
        assert ts2_events[2].event_type == EventType.FILL
