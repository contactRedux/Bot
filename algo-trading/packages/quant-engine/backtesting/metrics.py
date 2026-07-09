"""
backtesting/metrics.py — Performance metric calculations for backtests.

Metric reference
----------------

**Total Return**
    (final_equity - initial_equity) / initial_equity

**CAGR (Compound Annual Growth Rate)**
    (final_equity / initial_equity) ^ (365 / n_calendar_days) - 1
    Annualises total return accounting for the actual time elapsed.

**Sharpe Ratio** (annualised)
    mean(daily_returns) / std(daily_returns) × sqrt(252)
    The gold standard risk-adjusted return metric.  A Sharpe of 1.0 is
    acceptable; 2.0+ is excellent in practice.

**Sortino Ratio** (annualised)
    mean(daily_returns) / downside_std × sqrt(252)
    Like Sharpe but penalises only downside volatility.  Better for
    strategies with skewed returns (e.g. trend-following).

**Calmar Ratio**
    CAGR / |max_drawdown|
    How much annual return are you earning per unit of maximum pain?

**Max Drawdown**
    max of (peak - trough) / peak across the equity curve.
    The worst peak-to-trough decline.  This is the single most important
    risk metric — it determines how much capital you need to stomach live.

**Win Rate**
    fraction of trades with positive realised PnL.

**Profit Factor**
    sum(winning_trade_pnl) / |sum(losing_trade_pnl)|
    > 1.0 means you make more than you lose (gross).

**Average Hold Duration**
    Mean bars between entry and exit across all closed trades.

Usage
-----
::

    from backtesting.metrics import compute_metrics

    equity_curve = [(ts1, 100000), (ts2, 102000), ...]
    trade_log    = [{"realised_pnl": 500, "ticker": "AAPL", ...}, ...]
    strategy_attribution = {"momentum": 800, "mean_reversion": 200}

    metrics = compute_metrics(
        equity_curve=equity_curve,
        trade_log=trade_log,
        initial_capital=100_000.0,
        strategy_attribution=strategy_attribution,
    )
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_metrics(
    equity_curve: list[tuple[datetime, float]],
    trade_log: list[dict[str, Any]],
    initial_capital: float,
    strategy_attribution: dict[str, float] | None = None,
    risk_free_rate: float = 0.05,
    trading_days_per_year: int = 252,
) -> dict[str, Any]:
    """
    Compute the full suite of performance metrics.

    Parameters
    ----------
    equity_curve : list of (datetime, float)
        Portfolio equity at each snapshot.  Must be non-empty and in
        ascending timestamp order.
    trade_log : list of dict
        Each dict must have a ``"realised_pnl"`` key.  May be empty.
    initial_capital : float
    strategy_attribution : dict[str, float], optional
        Per-strategy realised PnL totals.
    risk_free_rate : float
        Annual risk-free rate for Sharpe/Sortino excess-return calculation.
        Default: 5% (approximately US 1-year T-bill rate).
    trading_days_per_year : int
        Used for annualisation.  252 for equities; 365 for crypto.

    Returns
    -------
    dict
        All metrics as a flat dictionary with rounded float values.
    """
    if len(equity_curve) < 2:
        return _empty_metrics()

    equities = np.array([eq for _, eq in equity_curve], dtype=np.float64)
    timestamps = [ts for ts, _ in equity_curve]

    final_equity = equities[-1]

    # ── Basic return metrics ───────────────────────────────────────────────
    total_return = _total_return(initial_capital, final_equity)
    # Use last minus first bar timestamp; strip the synthetic "now" seed point
    # so the duration always reflects the actual simulation range.
    sorted_ts = sorted(timestamps)
    n_calendar_days = max((sorted_ts[-1] - sorted_ts[0]).days, 1)
    cagr = _cagr(initial_capital, final_equity, n_calendar_days)

    # ── Daily returns ─────────────────────────────────────────────────────
    daily_returns = _daily_returns(equities, timestamps)

    # ── Risk metrics ──────────────────────────────────────────────────────
    rf_daily = (1 + risk_free_rate) ** (1 / trading_days_per_year) - 1
    sharpe = _sharpe(daily_returns, rf_daily, trading_days_per_year)
    sortino = _sortino(daily_returns, rf_daily, trading_days_per_year)
    max_dd, max_dd_start, max_dd_end = _max_drawdown(equities, timestamps)
    calmar = _calmar(cagr, max_dd)
    volatility = float(np.std(daily_returns) * math.sqrt(trading_days_per_year)) if len(daily_returns) > 1 else 0.0

    # ── Trade-level metrics ───────────────────────────────────────────────
    trade_pnls = [float(t.get("realised_pnl", 0.0)) for t in trade_log]
    win_rate = _win_rate(trade_pnls)
    profit_factor = _profit_factor(trade_pnls)
    avg_trade_pnl = float(np.mean(trade_pnls)) if trade_pnls else 0.0
    n_trades = len(trade_pnls)
    n_wins = sum(1 for p in trade_pnls if p > 0)
    n_losses = sum(1 for p in trade_pnls if p < 0)

    metrics: dict[str, Any] = {
        # Return metrics
        "total_return_pct": round(total_return * 100, 3),
        "cagr_pct": round(cagr * 100, 3),
        "final_equity": round(final_equity, 2),
        "initial_capital": round(initial_capital, 2),
        "total_pnl": round(final_equity - initial_capital, 2),
        # Risk-adjusted metrics
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "calmar_ratio": round(calmar, 4),
        "annual_volatility_pct": round(volatility * 100, 3),
        # Drawdown
        "max_drawdown_pct": round(max_dd * 100, 3),
        "max_drawdown_start": max_dd_start.isoformat() if max_dd_start else None,
        "max_drawdown_end": max_dd_end.isoformat() if max_dd_end else None,
        # Trade statistics
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate_pct": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 4),
        "avg_trade_pnl": round(avg_trade_pnl, 4),
        # Period
        "start_date": timestamps[0].isoformat() if timestamps else None,
        "end_date": timestamps[-1].isoformat() if timestamps else None,
        "n_calendar_days": n_calendar_days,
        "n_equity_snapshots": len(equity_curve),
    }

    # Per-strategy attribution
    if strategy_attribution:
        metrics["strategy_attribution"] = {
            sid: round(pnl, 2) for sid, pnl in strategy_attribution.items()
        }

    return metrics


# ---------------------------------------------------------------------------
# Individual metric functions (also importable independently)
# ---------------------------------------------------------------------------

def total_return(initial: float, final: float) -> float:
    """(final - initial) / initial."""
    return _total_return(initial, final)


def cagr(initial: float, final: float, n_calendar_days: int) -> float:
    """Compound Annual Growth Rate."""
    return _cagr(initial, final, n_calendar_days)


def sharpe_ratio(
    returns: list[float] | np.ndarray,
    risk_free_daily: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Annualised Sharpe ratio."""
    return _sharpe(np.asarray(returns, dtype=np.float64), risk_free_daily, periods_per_year)


def sortino_ratio(
    returns: list[float] | np.ndarray,
    risk_free_daily: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Annualised Sortino ratio (penalises downside vol only)."""
    return _sortino(np.asarray(returns, dtype=np.float64), risk_free_daily, periods_per_year)


def max_drawdown(equities: list[float] | np.ndarray) -> float:
    """Maximum peak-to-trough drawdown as a fraction (e.g. 0.20 = 20%)."""
    dd, _, _ = _max_drawdown(np.asarray(equities, dtype=np.float64), timestamps=None)
    return dd


def calmar_ratio(annual_return: float, max_dd: float) -> float:
    """Calmar = annual_return / |max_drawdown|."""
    return _calmar(annual_return, max_dd)


def win_rate(trade_pnls: list[float]) -> float:
    """Fraction of trades with positive PnL."""
    return _win_rate(trade_pnls)


def profit_factor(trade_pnls: list[float]) -> float:
    """Sum of wins / |sum of losses|.  Returns inf if no losing trades."""
    return _profit_factor(trade_pnls)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _total_return(initial: float, final: float) -> float:
    if initial <= 0:
        return 0.0
    return (final - initial) / initial


def _cagr(initial: float, final: float, n_calendar_days: int) -> float:
    if initial <= 0 or n_calendar_days <= 0:
        return 0.0
    years = n_calendar_days / 365.25
    if years <= 0:
        return 0.0
    ratio = final / initial
    if ratio <= 0:
        return -1.0
    return float(ratio ** (1.0 / years) - 1.0)


def _daily_returns(
    equities: np.ndarray, timestamps: list[datetime]
) -> np.ndarray:
    """
    Compute per-snapshot returns.

    We use percentage returns between consecutive equity snapshots.
    If the equity curve has multiple snapshots per day (e.g. intraday),
    we still compute bar-to-bar returns — the annualisation factor in Sharpe
    will account for the frequency.
    """
    if len(equities) < 2:
        return np.array([], dtype=np.float64)
    prev = equities[:-1]
    curr = equities[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.where(prev > 0, (curr - prev) / prev, 0.0)
    return rets.astype(np.float64)


def _sharpe(
    returns: np.ndarray, rf_daily: float, periods_per_year: int
) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf_daily
    mean_excess = float(np.mean(excess))
    std_excess = float(np.std(excess, ddof=1))
    if std_excess < 1e-12:
        return 0.0
    return float(mean_excess / std_excess * math.sqrt(periods_per_year))


def _sortino(
    returns: np.ndarray, rf_daily: float, periods_per_year: int
) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf_daily
    mean_excess = float(np.mean(excess))
    downside = excess[excess < 0]
    if len(downside) < 2:
        return float("inf") if mean_excess > 0 else 0.0
    downside_std = float(np.std(downside, ddof=1))
    if downside_std < 1e-12:
        return 0.0
    return float(mean_excess / downside_std * math.sqrt(periods_per_year))


def _max_drawdown(
    equities: np.ndarray,
    timestamps: list[datetime] | None,
) -> tuple[float, datetime | None, datetime | None]:
    """
    Compute maximum drawdown and the dates of peak and trough.

    Returns
    -------
    tuple of (max_drawdown_fraction, peak_date, trough_date)
    """
    if len(equities) < 2:
        return 0.0, None, None

    peak = equities[0]
    peak_idx = 0
    max_dd = 0.0
    dd_peak_idx = 0
    dd_trough_idx = 0

    for i in range(1, len(equities)):
        if equities[i] > peak:
            peak = equities[i]
            peak_idx = i
        dd = (peak - equities[i]) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            dd_peak_idx = peak_idx
            dd_trough_idx = i

    peak_ts = timestamps[dd_peak_idx] if timestamps and dd_peak_idx < len(timestamps) else None
    trough_ts = timestamps[dd_trough_idx] if timestamps and dd_trough_idx < len(timestamps) else None

    return float(max_dd), peak_ts, trough_ts


def _calmar(annual_return: float, max_dd: float) -> float:
    if max_dd <= 0:
        return float("inf") if annual_return > 0 else 0.0
    return float(annual_return / max_dd)


def _win_rate(trade_pnls: list[float]) -> float:
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)


def _profit_factor(trade_pnls: list[float]) -> float:
    wins = sum(p for p in trade_pnls if p > 0)
    losses = sum(abs(p) for p in trade_pnls if p < 0)
    if losses < 1e-9:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def _empty_metrics() -> dict[str, Any]:
    """Return a zeroed metrics dict when there is insufficient data."""
    return {
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "final_equity": 0.0,
        "initial_capital": 0.0,
        "total_pnl": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "annual_volatility_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_start": None,
        "max_drawdown_end": None,
        "n_trades": 0,
        "n_wins": 0,
        "n_losses": 0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "avg_trade_pnl": 0.0,
        "start_date": None,
        "end_date": None,
        "n_calendar_days": 0,
        "n_equity_snapshots": 0,
    }
