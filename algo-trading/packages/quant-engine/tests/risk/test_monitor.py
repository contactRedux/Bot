"""
tests/risk/test_monitor.py — Unit tests for DrawdownMonitor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from risk.limits import RiskLimits
from risk.monitor import DrawdownMonitor, RiskAlert


BASE = datetime(2023, 1, 2, tzinfo=timezone.utc)   # a Monday (trading day)
DAY2 = datetime(2023, 1, 3, tzinfo=timezone.utc)


def _monitor(max_dd=0.20, max_daily=0.02, initial=100_000.0) -> DrawdownMonitor:
    return DrawdownMonitor(
        limits=RiskLimits(max_drawdown_pct=max_dd, max_daily_loss_pct=max_daily),
        initial_equity=initial,
    )


class TestDrawdownMonitorNormal:
    def test_initial_no_halt(self):
        m = _monitor()
        assert m.is_halted is False

    def test_small_loss_no_halt(self):
        m = _monitor(max_dd=0.20)
        alert = m.update(99_000.0, BASE)   # 1% drop
        assert alert.is_ok
        assert not m.is_halted

    def test_peak_tracks_new_high(self):
        m = _monitor()
        m.update(110_000.0, BASE)
        assert m.peak_equity == pytest.approx(110_000.0)

    def test_drawdown_computed_correctly(self):
        m = _monitor(max_dd=0.30)
        m.update(110_000.0, BASE)
        alert = m.update(99_000.0, BASE)
        # drawdown = (110k - 99k) / 110k ≈ 10%
        assert alert.drawdown_pct == pytest.approx((110_000 - 99_000) / 110_000, rel=1e-3)


class TestDrawdownHalt:
    def test_halt_on_drawdown_breach(self):
        m = _monitor(max_dd=0.10)
        m.update(100_000.0, BASE)  # peak = 100k
        alert = m.update(89_000.0, BASE)  # 11% drawdown > 10% limit
        assert alert.halt_triggered
        assert m.is_halted
        assert alert.alert_type == "drawdown"

    def test_halt_reason_informative(self):
        m = _monitor(max_dd=0.10)
        m.update(100_000.0, BASE)
        alert = m.update(88_000.0, BASE)
        assert "drawdown" in alert.reason.lower()

    def test_subsequent_updates_still_halted(self):
        m = _monitor(max_dd=0.10)
        m.update(100_000.0, BASE)
        m.update(85_000.0, BASE)   # triggers halt
        alert = m.update(95_000.0, BASE)  # "recovery" — still halted
        assert alert.halt_triggered
        assert alert.alert_type == "already_halted"

    def test_no_halt_at_exact_threshold(self):
        """Halt only triggers when drawdown STRICTLY >= threshold."""
        m = _monitor(max_dd=0.20)
        m.update(100_000.0, BASE)
        # exactly 20% drawdown → halt
        alert = m.update(80_000.0, BASE)
        assert alert.halt_triggered

    def test_just_below_threshold_no_halt(self):
        # 19.99% drawdown must not trigger halt; also need daily_loss_pct set
        # high enough that daily loss check doesn't trip first on this intraday move
        m = DrawdownMonitor(
            limits=RiskLimits(max_drawdown_pct=0.20, max_daily_loss_pct=0.30),
            initial_equity=100_000.0,
        )
        m.update(100_000.0, BASE)
        # 19.99% drawdown: (100k - 80_010) / 100k = 19.99% < 20% → no halt
        # daily loss = same 19.99% < 30% daily limit → also no halt
        alert = m.update(80_010.0, BASE)
        assert not alert.halt_triggered


class TestDailyLossHalt:
    def test_halt_on_daily_loss_breach(self):
        m = _monitor(max_daily=0.02)
        m.update(100_000.0, BASE)   # sets day_open = 100k
        # 2.5% daily loss → breach
        alert = m.update(97_400.0, BASE)
        assert alert.halt_triggered
        assert alert.alert_type == "daily_loss"

    def test_daily_reset_on_new_day(self):
        """After a daily loss, the limit resets on the next calendar day."""
        m = _monitor(max_daily=0.02, max_dd=0.50)
        m.update(100_000.0, BASE)
        # trigger daily loss halt on DAY 1
        m.update(97_000.0, BASE)
        assert m.is_halted
        # Clear the halt manually and update with a new day
        m.reset_halt(new_equity=97_000.0)
        # New day — daily loss resets
        alert = m.update(97_500.0, DAY2)   # gain vs yesterday — no breach
        assert not alert.halt_triggered


class TestResetHalt:
    def test_reset_halt_clears_flag(self):
        m = _monitor(max_dd=0.10)
        m.update(100_000.0, BASE)
        m.update(85_000.0, BASE)
        assert m.is_halted
        m.reset_halt(new_equity=85_000.0)
        assert not m.is_halted

    def test_reset_halt_updates_peak(self):
        m = _monitor(max_dd=0.10)
        m.update(100_000.0, BASE)
        m.update(85_000.0, BASE)
        m.reset_halt(new_equity=85_000.0)
        assert m.peak_equity == pytest.approx(85_000.0)


class TestFullReset:
    def test_full_reset_clears_state(self):
        m = _monitor()
        m.update(90_000.0, BASE)
        m.reset(initial_equity=50_000.0)
        assert m.peak_equity == pytest.approx(50_000.0)
        assert not m.is_halted
        assert m._last_alert is None


class TestStatusDict:
    def test_status_keys(self):
        m = _monitor()
        m.update(98_000.0, BASE)
        s = m.status()
        for k in ["halted", "peak_equity", "current_drawdown_pct", "daily_loss_pct"]:
            assert k in s
