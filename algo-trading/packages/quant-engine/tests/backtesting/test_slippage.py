"""
tests/backtesting/test_slippage.py — Tests for the slippage models in SimulatedBroker.

Phase 7: verifies that:
 - FixedPercentageSlippage applies a constant bps cost regardless of order size.
 - SqrtImpactSlippage increases monotonically with order quantity.
 - The SimulatedBroker.calc_sqrt_slippage() helper returns the correct fraction.
"""
from __future__ import annotations

import math
from datetime import UTC

import pytest

from backtesting.broker import (
    FixedPercentageSlippage,
    HalfSpreadSlippage,
    SimulatedBroker,
    SqrtImpactSlippage,
)


class TestFixedPercentageSlippage:
    def test_buy_adds_slippage(self):
        model = FixedPercentageSlippage(pct=0.001)
        fill = model.apply("buy", close=100.0, high=101.0, low=99.0)
        assert fill == pytest.approx(100.1)

    def test_sell_subtracts_slippage(self):
        model = FixedPercentageSlippage(pct=0.001)
        fill = model.apply("sell", close=100.0, high=101.0, low=99.0)
        assert fill == pytest.approx(99.9)

    def test_zero_slippage_fills_at_close(self):
        model = FixedPercentageSlippage(pct=0.0)
        assert model.apply("buy", 100.0, 101.0, 99.0) == 100.0
        assert model.apply("sell", 100.0, 101.0, 99.0) == 100.0


class TestHalfSpreadSlippage:
    def test_buy_adds_half_spread(self):
        model = HalfSpreadSlippage(half_spread=0.50)
        assert model.apply("buy", 100.0, 101.0, 99.0) == pytest.approx(100.50)

    def test_sell_subtracts_half_spread(self):
        model = HalfSpreadSlippage(half_spread=0.50)
        assert model.apply("sell", 100.0, 101.0, 99.0) == pytest.approx(99.50)


class TestSqrtImpactSlippage:
    def test_slippage_increases_with_order_size(self):
        """Larger orders should incur more slippage."""
        model = SqrtImpactSlippage(impact_coeff=0.1, fixed_slippage_pct=0.0005)
        model.avg_daily_volume = 1_000_000.0

        model.order_quantity = 1_000.0
        slip_small = model._calc_slippage_frac()

        model.order_quantity = 100_000.0
        slip_large = model._calc_slippage_frac()

        assert slip_large > slip_small

    def test_slippage_zero_adv_falls_back_to_fixed(self):
        model = SqrtImpactSlippage(impact_coeff=0.1, fixed_slippage_pct=0.0005)
        model.avg_daily_volume = 0.0
        model.order_quantity = 1000.0
        assert model._calc_slippage_frac() == pytest.approx(0.0005)

    def test_slippage_formula(self):
        """Verify slip = coeff × sqrt(qty / adv)."""
        model = SqrtImpactSlippage(impact_coeff=0.2, fixed_slippage_pct=0.0)
        model.avg_daily_volume = 100_000.0
        model.order_quantity = 10_000.0
        expected = 0.2 * math.sqrt(10_000.0 / 100_000.0)
        assert model._calc_slippage_frac() == pytest.approx(expected)

    def test_apply_buy_increases_price(self):
        model = SqrtImpactSlippage(impact_coeff=0.1)
        model.avg_daily_volume = 1_000_000.0
        model.order_quantity = 50_000.0
        fill = model.apply("buy", 100.0, 101.0, 99.0)
        assert fill > 100.0

    def test_apply_sell_decreases_price(self):
        model = SqrtImpactSlippage(impact_coeff=0.1)
        model.avg_daily_volume = 1_000_000.0
        model.order_quantity = 50_000.0
        fill = model.apply("sell", 100.0, 101.0, 99.0)
        assert fill < 100.0


class TestCalcSqrtSlippage:
    def test_helper_uses_model_coeff_when_sqrt_model(self):
        model = SqrtImpactSlippage(impact_coeff=0.2, fixed_slippage_pct=0.0001)
        broker = SimulatedBroker(slippage_model=model)
        slip = broker.calc_sqrt_slippage(qty=10_000.0, avg_daily_vol=100_000.0)
        expected = 0.2 * math.sqrt(10_000.0 / 100_000.0)
        assert slip == pytest.approx(expected)

    def test_helper_uses_default_coeff_when_fixed_model(self):
        broker = SimulatedBroker()  # uses FixedPercentageSlippage
        slip = broker.calc_sqrt_slippage(qty=10_000.0, avg_daily_vol=100_000.0)
        expected = 0.1 * math.sqrt(10_000.0 / 100_000.0)
        assert slip == pytest.approx(expected)

    def test_helper_falls_back_to_fixed_when_adv_zero(self):
        broker = SimulatedBroker()
        slip = broker.calc_sqrt_slippage(qty=10_000.0, avg_daily_vol=0.0)
        assert slip == pytest.approx(0.0005)

    def test_slippage_in_broker_fill_with_sqrt_model(self):
        """Integration: broker with SqrtImpact produces larger slippage for larger order."""
        from datetime import datetime

        from backtesting.events import BarEvent, OrderEvent

        model = SqrtImpactSlippage(impact_coeff=0.5, fixed_slippage_pct=0.0)
        broker = SimulatedBroker(
            slippage_model=model,
            min_commission=0.0,
            commission_per_share=0.0,
        )

        def make_bar(ts):
            return BarEvent(
                timestamp=ts,
                ticker="AAPL",
                open=100.0, high=105.0, low=95.0, close=100.0,
                volume=1_000_000.0,
            )

        ts = datetime(2024, 1, 10, tzinfo=UTC)

        # Small order
        small_order = OrderEvent(
            timestamp=ts, ticker="AAPL", side="buy", quantity=1_000.0, order_type="market"
        )
        fills_small = broker.process_order(small_order, make_bar(ts))

        # Large order (new broker instance to reset ADV)
        broker2 = SimulatedBroker(
            slippage_model=SqrtImpactSlippage(impact_coeff=0.5, fixed_slippage_pct=0.0),
            min_commission=0.0,
            commission_per_share=0.0,
        )
        large_order = OrderEvent(
            timestamp=ts, ticker="AAPL", side="buy", quantity=100_000.0, order_type="market"
        )
        fills_large = broker2.process_order(large_order, make_bar(ts))

        assert fills_large[0].fill_price > fills_small[0].fill_price
