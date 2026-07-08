"""
tests/backtesting/test_portfolio.py — Unit tests for Portfolio accounting.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backtesting.events import FillEvent
from backtesting.portfolio import Portfolio


TS = datetime(2023, 6, 1, tzinfo=timezone.utc)
TS2 = datetime(2023, 6, 2, tzinfo=timezone.utc)


def _fill(side="buy", qty=10.0, price=100.0, commission=1.0, strategy="momentum", ticker="AAPL"):
    return FillEvent(
        timestamp=TS,
        ticker=ticker,
        side=side,
        quantity=qty,
        fill_price=price,
        commission=commission,
        strategy_id=strategy,
        order_id="oid1",
    )


class TestPortfolioInitialState:
    def test_initial_cash(self):
        pf = Portfolio(initial_capital=50_000.0)
        assert pf.cash == pytest.approx(50_000.0)
        assert pf.total_equity == pytest.approx(50_000.0)
        assert pf.realised_pnl == pytest.approx(0.0)

    def test_initial_equity_curve(self):
        pf = Portfolio(initial_capital=100_000.0)
        assert len(pf.equity_curve) == 1
        assert pf.equity_curve[0][1] == pytest.approx(100_000.0)


class TestBuyFills:
    def test_cash_decremented_on_buy(self):
        pf = Portfolio(initial_capital=10_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=1.0))
        # cash = 10000 - (100 × 10) - 1 = 8999
        assert pf.cash == pytest.approx(8999.0)

    def test_position_set_correctly(self):
        pf = Portfolio(initial_capital=10_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        assert pf.position("AAPL") == pytest.approx(10.0)

    def test_avg_cost_set(self):
        pf = Portfolio(initial_capital=10_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        assert pf.avg_cost("AAPL") == pytest.approx(100.0)

    def test_avg_cost_average_on_add(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.on_fill(_fill(side="buy", qty=10, price=110.0, commission=0.0))
        # avg_cost = (10×100 + 10×110) / 20 = 105
        assert pf.avg_cost("AAPL") == pytest.approx(105.0)
        assert pf.position("AAPL") == pytest.approx(20.0)


class TestSellFills:
    def test_realised_pnl_on_profitable_sell(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.on_fill(_fill(side="sell", qty=10, price=120.0, commission=0.0))
        # Profit: (120 - 100) × 10 = 200
        assert pf.realised_pnl == pytest.approx(200.0)

    def test_realised_pnl_on_losing_sell(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.on_fill(_fill(side="sell", qty=10, price=90.0, commission=0.0))
        assert pf.realised_pnl == pytest.approx(-100.0)

    def test_position_flat_after_full_close(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.on_fill(_fill(side="sell", qty=10, price=105.0, commission=0.0))
        assert pf.position("AAPL") == pytest.approx(0.0)

    def test_partial_close(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=20, price=100.0, commission=0.0))
        pf.on_fill(_fill(side="sell", qty=5, price=120.0, commission=0.0))
        # 5 shares closed at $20 profit = $100
        assert pf.realised_pnl == pytest.approx(100.0)
        assert pf.position("AAPL") == pytest.approx(15.0)


class TestShortPosition:
    def test_open_short(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="sell", qty=10, price=100.0, commission=0.0))
        assert pf.position("AAPL") == pytest.approx(-10.0)
        assert pf.avg_cost("AAPL") == pytest.approx(100.0)

    def test_cover_short_profit(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="sell", qty=10, price=100.0, commission=0.0))
        # Cover at lower price = profit
        pf.on_fill(_fill(side="buy", qty=10, price=90.0, commission=0.0))
        # Profit = (100 - 90) × 10 = 100
        assert pf.realised_pnl == pytest.approx(100.0)
        assert pf.position("AAPL") == pytest.approx(0.0)


class TestMarkToMarket:
    def test_unrealised_pnl_mark(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.mark({"AAPL": 110.0}, timestamp=TS2)
        assert pf.unrealised_pnl == pytest.approx(100.0)  # 10 × (110 - 100)

    def test_equity_curve_appended(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        # Cash after buy: 100_000 - 1000 = 99_000 (no commission in this fill)
        # Actually commission=1.0 by default in _fill helper
        initial_count = len(pf.equity_curve)
        pf.mark({"AAPL": 110.0})
        assert len(pf.equity_curve) == initial_count + 1

    def test_total_equity_equals_cash_plus_mv(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.mark({"AAPL": 110.0})
        expected = pf.cash + 10 * 110.0
        assert pf.total_equity == pytest.approx(expected)


class TestStrategyAttribution:
    def test_pnl_attributed_to_correct_strategy(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0, strategy="momentum"))
        pf.on_fill(_fill(side="sell", qty=10, price=120.0, commission=0.0, strategy="momentum"))
        attr = pf.strategy_pnl_attribution()
        assert "momentum" in attr
        assert attr["momentum"] == pytest.approx(200.0)

    def test_multiple_strategy_attribution(self):
        pf = Portfolio(initial_capital=200_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0, strategy="strat_a"))
        pf.on_fill(_fill(side="sell", qty=10, price=110.0, commission=0.0, strategy="strat_a"))
        pf.on_fill(_fill(side="buy", qty=5, price=200.0, commission=0.0, strategy="strat_b", ticker="MSFT"))
        pf.on_fill(_fill(side="sell", qty=5, price=190.0, commission=0.0, strategy="strat_b", ticker="MSFT"))
        attr = pf.strategy_pnl_attribution()
        assert attr["strat_a"] == pytest.approx(100.0)
        assert attr["strat_b"] == pytest.approx(-50.0)


class TestPositionsSnapshot:
    def test_non_flat_positions_only(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.mark({"AAPL": 100.0})
        snap = pf.positions_snapshot()
        assert "AAPL" in snap

    def test_flat_positions_excluded(self):
        pf = Portfolio(initial_capital=100_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0, commission=0.0))
        pf.on_fill(_fill(side="sell", qty=10, price=100.0, commission=0.0))
        pf.mark({"AAPL": 100.0})
        snap = pf.positions_snapshot()
        # Position is flat so should not appear (or have ~0 qty)
        if "AAPL" in snap:
            assert abs(snap["AAPL"]["quantity"]) < 1e-6


class TestReset:
    def test_reset_restores_initial_state(self):
        pf = Portfolio(initial_capital=50_000.0)
        pf.on_fill(_fill(side="buy", qty=10, price=100.0))
        pf.reset()
        assert pf.cash == pytest.approx(50_000.0)
        assert pf.position("AAPL") == 0.0
        assert pf.trade_log == []
        assert pf.equity_curve[-1][1] == pytest.approx(50_000.0)
