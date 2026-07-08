"""
risk/var.py — Historical Value-at-Risk (VaR) and CVaR (Expected Shortfall).

What is VaR?
------------
Value-at-Risk answers: "What is the maximum loss I should expect over the next
N days, with X% confidence?"

    VaR(95%, 1-day) = $5,000

means: "There is only a 5% chance of losing more than $5,000 in one day."

Equivalently, the worst 5% of days in the historical sample had losses
exceeding $5,000.

Historical Simulation Method
-----------------------------
Rather than assuming returns follow a normal distribution (parametric VaR),
Historical Simulation uses the actual empirical distribution of past returns:

    1. Collect the last N daily portfolio returns (default N = 252 days).
    2. Sort them ascending (worst first).
    3. VaR(95%) = the return at the 5th percentile × current portfolio value.
    4. VaR(99%) = the return at the 1st percentile × current portfolio value.

This approach captures fat tails and non-normality automatically — a critical
advantage for equity portfolios which regularly experience extreme moves far
beyond what a normal distribution would predict (e.g. March 2020, March 2009).

What is CVaR (Conditional VaR / Expected Shortfall)?
-----------------------------------------------------
CVaR answers: "Given that I'm in the worst X% of scenarios, how bad is it
on average?"

    CVaR(95%, 1-day) = mean loss across all days where loss > VaR(95%)

CVaR is strictly more informative than VaR because it measures the *expected
magnitude* of tail losses, not just the threshold.  It is now preferred by
regulators (Basel III uses Expected Shortfall rather than VaR).

Intuition: if a wall collapse hurts 5% of buildings, VaR tells you how tall
the wall was.  CVaR tells you how deep the rubble is, on average.

Usage
-----
::

    from risk.var import HistoricalVaR

    # From an equity curve (list of portfolio values)
    equity = [100_000, 99_500, 101_200, ...]
    hvar = HistoricalVaR(window=252)
    result = hvar.compute(equity)

    print(f"VaR 95%: ${result.var_95:,.0f}")
    print(f"VaR 99%: ${result.var_99:,.0f}")
    print(f"CVaR 95%: ${result.cvar_95:,.0f}")
    print(f"CVaR 99%: ${result.cvar_99:,.0f}")
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class VaRResult:
    """
    Result of a VaR/CVaR computation.

    All dollar values are positive — they represent the magnitude of the
    expected loss (not a signed return).

    Attributes
    ----------
    var_95 : float      Dollar VaR at 95% confidence (5th percentile loss).
    var_99 : float      Dollar VaR at 99% confidence (1st percentile loss).
    cvar_95 : float     Conditional VaR (Expected Shortfall) at 95%.
    cvar_99 : float     Conditional VaR at 99%.
    window_used : int   Actual number of return observations used.
    current_value : float   Portfolio value at time of computation.
    daily_return_std : float  Standard deviation of daily returns in window.
    """

    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    cvar_99: float = 0.0
    window_used: int = 0
    current_value: float = 0.0
    daily_return_std: float = 0.0

    @property
    def var_95_pct(self) -> float:
        """VaR 95% as a fraction of portfolio value."""
        return self.var_95 / self.current_value if self.current_value > 0 else 0.0

    @property
    def var_99_pct(self) -> float:
        """VaR 99% as a fraction of portfolio value."""
        return self.var_99 / self.current_value if self.current_value > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "var_95": round(self.var_95, 2),
            "var_99": round(self.var_99, 2),
            "cvar_95": round(self.cvar_95, 2),
            "cvar_99": round(self.cvar_99, 2),
            "var_95_pct": round(self.var_95_pct * 100, 4),
            "var_99_pct": round(self.var_99_pct * 100, 4),
            "window_used": self.window_used,
            "current_value": round(self.current_value, 2),
            "daily_return_std": round(self.daily_return_std, 6),
        }


# ---------------------------------------------------------------------------
# HistoricalVaR
# ---------------------------------------------------------------------------

class HistoricalVaR:
    """
    Historical Simulation VaR and CVaR calculator.

    Parameters
    ----------
    window : int
        Rolling window of daily returns to use.  Default 252 (one trading
        year).  Shorter windows react faster to recent regime changes;
        longer windows provide more stable estimates.
    """

    def __init__(self, window: int = 252) -> None:
        if window < 30:
            raise ValueError(f"VaR window must be >= 30 observations, got {window}")
        self.window = window

    def compute(
        self,
        equity_values: Sequence[float],
        current_value: float | None = None,
    ) -> VaRResult:
        """
        Compute VaR and CVaR from a sequence of portfolio equity values.

        Parameters
        ----------
        equity_values : sequence of float
            Portfolio equity at each time step (daily snapshots).
            Must have at least 31 values (30 returns + 1).
        current_value : float, optional
            Current portfolio value for converting return-VaR to dollar-VaR.
            Defaults to the last value in equity_values.

        Returns
        -------
        VaRResult
            Dollar-VaR and CVaR at 95% and 99% confidence levels.
        """
        arr = np.asarray(equity_values, dtype=np.float64)
        if len(arr) < 2:
            return VaRResult()

        # Take the most recent `window` values
        tail = arr[-min(self.window + 1, len(arr)):]
        prev = tail[:-1]
        curr = tail[1:]

        with np.errstate(divide="ignore", invalid="ignore"):
            returns = np.where(prev > 0, (curr - prev) / prev, 0.0)

        portfolio_value = float(current_value if current_value is not None else arr[-1])

        return _compute_var_cvar(returns, portfolio_value)

    def compute_from_returns(
        self,
        returns: Sequence[float],
        current_value: float,
    ) -> VaRResult:
        """
        Compute VaR/CVaR directly from a sequence of daily returns.

        Parameters
        ----------
        returns : sequence of float
            Daily portfolio returns (fractions, e.g. -0.02 = 2% loss).
        current_value : float
            Current portfolio value.
        """
        arr = np.asarray(returns[-self.window:], dtype=np.float64)
        return _compute_var_cvar(arr, current_value)


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------

def _compute_var_cvar(returns: np.ndarray, portfolio_value: float) -> VaRResult:
    """
    Core computation: given a returns array and portfolio value,
    compute VaR and CVaR at 95% and 99%.
    """
    if len(returns) < 5:
        return VaRResult(current_value=portfolio_value)

    sorted_returns = np.sort(returns)  # ascending: worst first is index 0
    n = len(sorted_returns)

    # VaR: percentile loss (5th and 1st percentile → 95% and 99% confidence)
    var_95_ret = float(np.percentile(sorted_returns, 5))
    var_99_ret = float(np.percentile(sorted_returns, 1))

    # Convert to dollar loss (positive = loss magnitude)
    var_95 = max(0.0, -var_95_ret * portfolio_value)
    var_99 = max(0.0, -var_99_ret * portfolio_value)

    # CVaR: mean of returns worse than the VaR threshold
    tail_95 = sorted_returns[sorted_returns <= var_95_ret]
    tail_99 = sorted_returns[sorted_returns <= var_99_ret]

    cvar_95_ret = float(np.mean(tail_95)) if len(tail_95) > 0 else var_95_ret
    cvar_99_ret = float(np.mean(tail_99)) if len(tail_99) > 0 else var_99_ret

    cvar_95 = max(0.0, -cvar_95_ret * portfolio_value)
    cvar_99 = max(0.0, -cvar_99_ret * portfolio_value)

    return VaRResult(
        var_95=round(var_95, 4),
        var_99=round(var_99, 4),
        cvar_95=round(cvar_95, 4),
        cvar_99=round(cvar_99, 4),
        window_used=n,
        current_value=portfolio_value,
        daily_return_std=round(float(np.std(returns, ddof=1)) if n > 1 else 0.0, 6),
    )
