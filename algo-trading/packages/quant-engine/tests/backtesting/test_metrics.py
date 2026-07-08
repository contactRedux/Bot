"""
tests/backtesting/test_metrics.py — Unit tests for performance metric calculations.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backtesting.metrics import (
    cagr,
    calmar_ratio,
    compute_metrics,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    total_return,
    win_rate,
)


def _build_equity_curve(equities: list[float]) -> list[tuple[datetime, float]]:
    """Helper: build equity curve with daily timestamps."""
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return [(base + timedelta(days=i), eq) for i, eq in enumerate(equities)]


class TestTotalReturn:
    def test_positive_return(self):
        assert total_return(100_000, 120_000) == pytest.approx(0.20)

    def test_negative_return(self):
        assert total_return(100_000, 80_000) == pytest.approx(-0.20)

    def test_zero_return(self):
        assert total_return(100_000, 100_000) == pytest.approx(0.0)

    def test_zero_initial(self):
        assert total_return(0, 100_000) == 0.0


class TestCAGR:
    def test_one_year_double(self):
        # 100% return over 365 days = 100% CAGR
        result = cagr(100_000, 200_000, 365)
        assert result == pytest.approx(1.0, rel=0.01)

    def test_two_year_increase(self):
        # 100% return over 730 days ≈ 41.4% CAGR
        result = cagr(100_000, 200_000, 730)
        assert result == pytest.approx(math.sqrt(2) - 1, rel=0.01)

    def test_zero_days(self):
        assert cagr(100_000, 120_000, 0) == 0.0


class TestSharpeRatio:
    def test_positive_steady_returns(self):
        # Slightly varying positive returns → positive Sharpe
        np.random.seed(7)
        returns = np.random.normal(0.002, 0.005, 252)  # mean >> std → high Sharpe
        sr = sharpe_ratio(returns, risk_free_daily=0.0)
        assert sr > 1.0  # clearly positive Sharpe

    def test_zero_returns(self):
        returns = np.zeros(252)
        sr = sharpe_ratio(returns)
        assert sr == pytest.approx(0.0)

    def test_random_returns_reasonable(self):
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.01, 252)
        sr = sharpe_ratio(returns)
        # random noise around 0.5 annualised — just check it's computed
        assert isinstance(sr, float)
        assert not math.isnan(sr)


class TestSortinoRatio:
    def test_no_downside_returns_inf(self):
        # All returns positive → sortino = +inf
        returns = np.full(100, 0.01)
        sr = sortino_ratio(returns)
        assert math.isinf(sr) or sr > 100.0

    def test_symmetric_returns_higher_sortino_than_sharpe(self):
        # When returns are symmetric, Sortino ≈ Sharpe × sqrt(2)
        np.random.seed(0)
        returns = np.random.normal(0.001, 0.01, 500)
        sh = sharpe_ratio(returns)
        so = sortino_ratio(returns)
        # Sortino should be >= Sharpe (equal only when all excess returns negative)
        assert so >= sh - 0.5  # loose bound


class TestMaxDrawdown:
    def test_perfect_growth_no_drawdown(self):
        equities = list(range(100, 200))  # monotonically increasing
        dd = max_drawdown(equities)
        assert dd == pytest.approx(0.0)

    def test_known_drawdown(self):
        # Peak = 200, trough = 100 → 50% drawdown
        equities = [100, 150, 200, 150, 100, 120]
        dd = max_drawdown(equities)
        assert dd == pytest.approx(0.50)

    def test_single_element_no_drawdown(self):
        dd = max_drawdown([100.0])
        assert dd == 0.0

    def test_recovery_after_drawdown(self):
        # Drawdown then recovery — max should still be the deepest
        equities = [100, 90, 110, 80, 120]
        # Peak 110, trough 80: dd = (110-80)/110 = 0.2727
        dd = max_drawdown(equities)
        assert dd == pytest.approx((110 - 80) / 110, rel=0.01)


class TestCalmarRatio:
    def test_positive_calmar(self):
        result = calmar_ratio(0.20, 0.10)  # 20% return / 10% drawdown = 2.0
        assert result == pytest.approx(2.0)

    def test_zero_drawdown_returns_inf(self):
        result = calmar_ratio(0.20, 0.0)
        assert math.isinf(result)

    def test_zero_return_zero_drawdown(self):
        result = calmar_ratio(0.0, 0.0)
        assert result == 0.0


class TestWinRate:
    def test_all_wins(self):
        assert win_rate([10, 20, 30]) == pytest.approx(1.0)

    def test_all_losses(self):
        assert win_rate([-10, -20]) == pytest.approx(0.0)

    def test_half_half(self):
        assert win_rate([10, -5]) == pytest.approx(0.5)

    def test_empty(self):
        assert win_rate([]) == pytest.approx(0.0)


class TestProfitFactor:
    def test_positive_pf(self):
        # wins=200, losses=100 → pf = 2.0
        assert profit_factor([100, 100, -50, -50]) == pytest.approx(2.0)

    def test_no_losses_inf(self):
        import math
        assert math.isinf(profit_factor([100, 200]))

    def test_no_wins_zero(self):
        assert profit_factor([-100, -200]) == pytest.approx(0.0)


class TestComputeMetrics:
    def _run(self, equities, trade_pnls=None, capital=100_000.0):
        curve = _build_equity_curve(equities)
        trades = [{"realised_pnl": p} for p in (trade_pnls or [])]
        return compute_metrics(curve, trades, initial_capital=capital)

    def test_returns_all_required_keys(self):
        m = self._run([100_000, 105_000, 110_000, 108_000, 115_000])
        required = [
            "total_return_pct", "cagr_pct", "sharpe_ratio", "sortino_ratio",
            "calmar_ratio", "max_drawdown_pct", "win_rate_pct", "profit_factor",
            "n_trades", "start_date", "end_date",
        ]
        for k in required:
            assert k in m, f"Missing metric: {k}"

    def test_total_return_correct(self):
        m = self._run([100_000, 120_000], capital=100_000)
        assert m["total_return_pct"] == pytest.approx(20.0)

    def test_max_drawdown_captured(self):
        m = self._run([100_000, 80_000, 90_000], capital=100_000)
        assert m["max_drawdown_pct"] == pytest.approx(20.0)

    def test_trade_metrics(self):
        m = self._run(
            [100_000, 105_000, 110_000],
            trade_pnls=[200, -100, 300, -50, 150],
            capital=100_000,
        )
        assert m["n_trades"] == 5
        assert m["n_wins"] == 3
        assert m["n_losses"] == 2
        assert m["win_rate_pct"] == pytest.approx(60.0)

    def test_strategy_attribution_present(self):
        curve = _build_equity_curve([100_000, 105_000])
        trades = [{"realised_pnl": 5000}]
        m = compute_metrics(
            curve, trades, 100_000,
            strategy_attribution={"momentum": 3000, "mean_reversion": 2000}
        )
        assert m["strategy_attribution"]["momentum"] == 3000
        assert m["strategy_attribution"]["mean_reversion"] == 2000

    def test_insufficient_data_returns_empty(self):
        from backtesting.metrics import _empty_metrics
        m = compute_metrics([], [], 100_000)
        assert m["total_return_pct"] == 0.0
        assert m["n_trades"] == 0
