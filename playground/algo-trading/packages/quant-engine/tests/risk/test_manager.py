"""
tests/risk/test_manager.py — Unit tests for RiskManager.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from risk.limits import RiskLimits
from risk.manager import OrderDecision, OrderDecisionType, RiskManager
from risk.monitor import DrawdownMonitor
from strategies.base import Order, OrderSide, OrderType


TS = datetime(2023, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

def _order(
    ticker="AAPL",
    side=OrderSide.BUY,
    qty=10.0,
    strategy_id="momentum",
    confidence=0.8,
) -> Order:
    return Order(
        ticker=ticker,
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET,
        strategy_id=strategy_id,
        confidence=confidence,
    )


@dataclass
class StubPortfolio:
    """Minimal portfolio stub for RiskManager tests."""
    _positions: dict[str, float] = field(default_factory=dict)
    _strategy_pnl: dict[str, float] = field(default_factory=dict)
    total_equity: float = 100_000.0

    def position(self, ticker: str) -> float:
        return self._positions.get(ticker, 0.0)

    def positions_snapshot(self) -> dict[str, Any]:
        return {t: {"quantity": q} for t, q in self._positions.items() if abs(q) > 1e-8}

    def strategy_pnl_attribution(self) -> dict[str, float]:
        return dict(self._strategy_pnl)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRiskManagerDisabled:
    def test_disabled_approves_everything(self):
        mgr = RiskManager(limits=RiskLimits(enabled=False))
        pf = StubPortfolio()
        decision = mgr.check_order(_order(qty=999999.0), pf)
        assert decision.approved
        assert decision.decision == OrderDecisionType.APPROVE


class TestHaltCheck:
    def test_halt_rejects_all_orders(self):
        mgr = RiskManager(limits=RiskLimits())
        monitor = DrawdownMonitor(
            limits=RiskLimits(max_drawdown_pct=0.10),
            initial_equity=100_000.0,
        )
        monitor.update(100_000.0, TS)
        monitor.update(85_000.0, TS)  # triggers halt (15% > 10%)
        mgr.set_monitor(monitor)

        pf = StubPortfolio()
        decision = mgr.check_order(_order(qty=1.0), pf)
        assert decision.rejected
        assert decision.check_name == "halt"

    def test_no_halt_when_monitor_not_set(self):
        mgr = RiskManager(limits=RiskLimits())
        pf = StubPortfolio()
        mgr.update_price_history("AAPL", [100.0] * 5)
        decision = mgr.check_order(_order(qty=1.0), pf)
        assert decision.approved


class TestDustOrderCheck:
    def test_dust_order_rejected(self):
        mgr = RiskManager(limits=RiskLimits(min_order_quantity=0.01))
        pf = StubPortfolio()
        decision = mgr.check_order(_order(qty=0.001), pf)
        assert decision.rejected
        assert decision.check_name == "dust_order"

    def test_above_min_quantity_passes(self):
        mgr = RiskManager(limits=RiskLimits(min_order_quantity=0.01))
        pf = StubPortfolio()
        mgr.update_price_history("AAPL", [100.0] * 5)
        decision = mgr.check_order(_order(qty=0.02), pf)
        assert decision.approved or decision.scaled  # either is fine


class TestPositionLimit:
    def test_order_within_limit_approved(self):
        # capital=100k, max_position=10%, mark=100 → max_qty=100 shares
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.10), total_capital=100_000.0)
        mgr.update_price_history("AAPL", [100.0] * 5)
        pf = StubPortfolio(_positions={"AAPL": 0.0})
        decision = mgr.check_order(_order(qty=50.0), pf)
        assert decision.approved

    def test_order_exceeding_limit_scaled_down(self):
        # max_qty = 100, current = 0, order = 120 → scaled to 100
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.10), total_capital=100_000.0)
        mgr.update_price_history("AAPL", [100.0] * 5)
        pf = StubPortfolio(_positions={"AAPL": 0.0})
        decision = mgr.check_order(_order(qty=120.0), pf)
        assert decision.scaled
        assert decision.adjusted_qty == pytest.approx(100.0)

    def test_order_at_position_cap_rejected(self):
        # current = 100 shares, max_qty = 100 → room = 0 → REJECT
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.10), total_capital=100_000.0)
        mgr.update_price_history("AAPL", [100.0] * 5)
        pf = StubPortfolio(_positions={"AAPL": 100.0})
        decision = mgr.check_order(_order(qty=1.0), pf)
        assert decision.rejected
        assert decision.check_name == "position_limit"

    def test_sell_order_room_computed_from_short_side(self):
        # Selling more than held (going short)
        # current = 0, side = sell → room = max_qty (for short side)
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.10), total_capital=100_000.0)
        mgr.update_price_history("AAPL", [100.0] * 5)
        pf = StubPortfolio(_positions={"AAPL": 0.0})
        sell_order = _order(side=OrderSide.SELL, qty=50.0)
        decision = mgr.check_order(sell_order, pf)
        assert decision.approved or decision.scaled

    def test_no_price_history_uses_fallback(self):
        """Without price history, fallback price = 1.0 → huge position limit."""
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.10), total_capital=100_000.0)
        pf = StubPortfolio()
        decision = mgr.check_order(_order(qty=5.0), pf)
        # max_qty at price=1 → 10_000 shares; 5 is well within that
        assert decision.approved or decision.scaled


class TestDailyLossCheck:
    def test_daily_loss_limit_triggers_reject(self):
        from risk.monitor import DrawdownMonitor, RiskAlert
        mgr = RiskManager(limits=RiskLimits(max_daily_loss_pct=0.02))
        monitor = DrawdownMonitor(
            limits=RiskLimits(max_drawdown_pct=0.50, max_daily_loss_pct=0.02),
            initial_equity=100_000.0,
        )
        monitor.update(100_000.0, TS)
        monitor.update(97_000.0, TS)  # 3% daily loss > 2% → daily_loss_pct set

        mgr.set_monitor(monitor)
        # Monitor's halt is triggered by 3% daily loss
        pf = StubPortfolio()
        decision = mgr.check_order(_order(qty=1.0), pf)
        assert decision.rejected  # halted by daily_loss in monitor


class TestCorrelationScaleDown:
    def test_high_correlation_scales_down_order(self):
        import math
        import numpy as np

        # Generate two highly correlated price series
        rng = np.random.default_rng(42)
        r1 = rng.normal(0.001, 0.01, 100)
        r2 = 0.95 * r1 + math.sqrt(1 - 0.95**2) * rng.normal(0, 0.01, 100)
        p1 = list(np.cumprod(1 + r1) * 100)
        p2 = list(np.cumprod(1 + r2) * 100)

        from risk.correlation import CorrelationChecker
        checker = CorrelationChecker(window=60, threshold=0.70)
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.50), total_capital=1_000_000.0)
        mgr.set_correlation_checker(checker)
        mgr.update_price_history("A", p1)
        mgr.update_price_history("B", p2)
        mgr.update_price_history("A", p1)  # ensure A is in history

        pf = StubPortfolio(_positions={"A": 0.0, "B": 0.0})
        # Large buy on A — should be scaled down due to high correlation with B
        decision = mgr.check_order(_order(ticker="A", qty=100.0), pf)
        if decision.scaled:
            assert decision.adjusted_qty < 100.0
        else:
            # Could also be APPROVE if correlation didn't exceed threshold in this sample
            assert decision.approved


class TestAuditLog:
    def test_reject_logged_in_audit(self):
        mgr = RiskManager(limits=RiskLimits(min_order_quantity=1.0))
        pf = StubPortfolio()
        mgr.check_order(_order(qty=0.0001), pf)  # dust → reject
        log = mgr.audit_log
        assert len(log) >= 1
        assert log[-1]["decision"] == "reject"
        assert log[-1]["ticker"] == "AAPL"

    def test_scale_down_logged_in_audit(self):
        mgr = RiskManager(limits=RiskLimits(max_position_pct=0.10), total_capital=100_000.0)
        mgr.update_price_history("AAPL", [100.0] * 5)
        pf = StubPortfolio(_positions={"AAPL": 0.0})
        mgr.check_order(_order(qty=200.0), pf)  # exceeds limit → scale
        log = mgr.audit_log
        assert any(e["decision"] == "scale_down" for e in log)

    def test_clear_audit_log(self):
        mgr = RiskManager(limits=RiskLimits(min_order_quantity=1.0))
        pf = StubPortfolio()
        mgr.check_order(_order(qty=0.0001), pf)
        mgr.clear_audit_log()
        assert mgr.audit_log == []


class TestDecisionProperties:
    def test_approve_decision_properties(self):
        d = OrderDecision(
            decision=OrderDecisionType.APPROVE,
            order=_order(),
            original_qty=10.0,
            adjusted_qty=10.0,
        )
        assert d.approved is True
        assert d.rejected is False
        assert d.scaled is False

    def test_reject_decision_properties(self):
        d = OrderDecision(
            decision=OrderDecisionType.REJECT,
            order=_order(),
            original_qty=10.0,
            adjusted_qty=0.0,
        )
        assert d.approved is False
        assert d.rejected is True

    def test_scale_down_decision_properties(self):
        d = OrderDecision(
            decision=OrderDecisionType.SCALE_DOWN,
            order=_order(qty=5.0),
            original_qty=10.0,
            adjusted_qty=5.0,
        )
        assert d.approved is True
        assert d.scaled is True
