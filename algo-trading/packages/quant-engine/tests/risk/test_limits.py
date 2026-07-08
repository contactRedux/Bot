"""
tests/risk/test_limits.py — Unit tests for RiskLimits configuration.
"""
from __future__ import annotations

import pytest
from risk.limits import RiskLimits


class TestRiskLimitsDefaults:
    def test_default_values(self):
        lim = RiskLimits()
        assert lim.max_position_pct == 0.10
        assert lim.max_strategy_allocation == 0.30
        assert lim.max_drawdown_pct == 0.20
        assert lim.max_daily_loss_pct == 0.02
        assert lim.max_correlation_concentration == 0.70
        assert lim.enabled is True

    def test_custom_values(self):
        lim = RiskLimits(max_position_pct=0.05, max_drawdown_pct=0.15)
        assert lim.max_position_pct == 0.05
        assert lim.max_drawdown_pct == 0.15

    def test_from_dict(self):
        d = {"max_position_pct": 0.08, "max_drawdown_pct": 0.25, "unknown_key": 999}
        lim = RiskLimits.from_dict(d)
        assert lim.max_position_pct == 0.08
        assert lim.max_drawdown_pct == 0.25

    def test_to_dict_roundtrip(self):
        lim = RiskLimits(max_position_pct=0.12)
        d = lim.to_dict()
        lim2 = RiskLimits.from_dict(d)
        assert lim2.max_position_pct == 0.12

    def test_disabled_flag(self):
        lim = RiskLimits(enabled=False)
        assert lim.enabled is False


class TestRiskLimitsValidation:
    def test_position_pct_zero_raises(self):
        with pytest.raises(AssertionError):
            RiskLimits(max_position_pct=0.0)

    def test_position_pct_over_one_raises(self):
        with pytest.raises(AssertionError):
            RiskLimits(max_position_pct=1.5)

    def test_drawdown_zero_raises(self):
        with pytest.raises(AssertionError):
            RiskLimits(max_drawdown_pct=0.0)

    def test_var_window_too_small_raises(self):
        with pytest.raises(AssertionError):
            RiskLimits(var_window_days=10)

    def test_var_confidence_one_raises(self):
        with pytest.raises(AssertionError):
            RiskLimits(var_confidence_level=1.0)
