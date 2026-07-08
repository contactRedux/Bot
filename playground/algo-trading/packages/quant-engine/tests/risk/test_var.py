"""
tests/risk/test_var.py — Unit tests for Historical VaR and CVaR.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from risk.var import HistoricalVaR, VaRResult, _compute_var_cvar


def _equity_with_crash(n=300, crash_pct=0.05, n_crashes=5, seed=42) -> list[float]:
    """Generate an equity curve with occasional large drawdowns."""
    rng = np.random.default_rng(seed)
    equity = [100_000.0]
    for i in range(n - 1):
        if i % (n // n_crashes) == 0:
            ret = -crash_pct
        else:
            ret = float(rng.normal(0.001, 0.008))
        equity.append(equity[-1] * (1 + ret))
    return equity


class TestHistoricalVaRBasic:
    def test_window_too_small_raises(self):
        with pytest.raises(ValueError):
            HistoricalVaR(window=5)

    def test_returns_var_result(self):
        equity = _equity_with_crash(n=300)
        hvar = HistoricalVaR(window=252)
        result = hvar.compute(equity)
        assert isinstance(result, VaRResult)

    def test_var_99_geq_var_95(self):
        """99% VaR should always be >= 95% VaR (deeper tail)."""
        equity = _equity_with_crash(n=300)
        hvar = HistoricalVaR(window=252)
        result = hvar.compute(equity)
        assert result.var_99 >= result.var_95

    def test_cvar_99_geq_var_99(self):
        """CVaR should be >= VaR at the same confidence level."""
        equity = _equity_with_crash(n=300)
        hvar = HistoricalVaR(window=252)
        result = hvar.compute(equity)
        assert result.cvar_99 >= result.var_99

    def test_cvar_95_geq_var_95(self):
        equity = _equity_with_crash(n=300)
        hvar = HistoricalVaR(window=252)
        result = hvar.compute(equity)
        assert result.cvar_95 >= result.var_95

    def test_var_positive_for_risky_portfolio(self):
        """A portfolio with losses should have positive VaR."""
        equity = _equity_with_crash(n=260, crash_pct=0.03)
        hvar = HistoricalVaR(window=252)
        result = hvar.compute(equity)
        assert result.var_95 > 0.0

    def test_window_used_capped_at_actual_data(self):
        equity = list(range(100_000, 100_200))  # 200 values
        hvar = HistoricalVaR(window=252)        # window > data length
        result = hvar.compute(equity)
        assert result.window_used <= 199         # 199 returns from 200 prices


class TestVaRMath:
    def test_known_returns_var_95(self):
        """Hand-compute: 100 returns, 5th percentile at index 4 (sorted)."""
        returns = np.linspace(-0.10, 0.10, 100)  # -10% to +10%, 100 values
        # 5th percentile of linspace(-0.1, 0.1, 100) ≈ -0.10 + 5/100 × 0.20 = -0.09
        result = _compute_var_cvar(returns, portfolio_value=100_000.0)
        # VaR_95 should be positive (it's a loss magnitude)
        assert result.var_95 > 0
        # VaR_95 should be less than VaR_99 (less severe)
        assert result.var_95 <= result.var_99

    def test_all_positive_returns_zero_var(self):
        """If all returns are positive, there are no losses → VaR = 0."""
        returns = np.full(100, 0.001)  # all +0.1%
        result = _compute_var_cvar(returns, portfolio_value=100_000.0)
        assert result.var_95 == pytest.approx(0.0)
        assert result.var_99 == pytest.approx(0.0)

    def test_empty_returns_returns_zero(self):
        result = _compute_var_cvar(np.array([]), portfolio_value=100_000.0)
        assert result.var_95 == 0.0


class TestVaRResult:
    def test_pct_properties(self):
        r = VaRResult(var_95=1_000.0, var_99=2_000.0, current_value=100_000.0)
        assert r.var_95_pct == pytest.approx(0.01)
        assert r.var_99_pct == pytest.approx(0.02)

    def test_zero_portfolio_value_no_crash(self):
        r = VaRResult(var_95=1_000.0, current_value=0.0)
        assert r.var_95_pct == 0.0

    def test_to_dict_has_all_keys(self):
        equity = _equity_with_crash(n=260)
        hvar = HistoricalVaR(window=252)
        result = hvar.compute(equity)
        d = result.to_dict()
        for k in ["var_95", "var_99", "cvar_95", "cvar_99", "var_95_pct", "var_99_pct"]:
            assert k in d


class TestFromReturns:
    def test_from_returns_consistent_with_from_equity(self):
        equity = _equity_with_crash(n=260)
        arr = np.asarray(equity, dtype=float)
        returns = list((arr[1:] - arr[:-1]) / arr[:-1])
        hvar = HistoricalVaR(window=252)
        r1 = hvar.compute(equity)
        r2 = hvar.compute_from_returns(returns, current_value=equity[-1])
        # Should be very close (minor floating point differences possible)
        assert abs(r1.var_95 - r2.var_95) < 1.0  # within $1
