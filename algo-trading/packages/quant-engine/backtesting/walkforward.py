"""
backtesting/walkforward.py — Walk-forward out-of-sample validation for backtests.

What is walk-forward validation?
----------------------------------
Standard backtesting risks over-fitting: you tune parameters on the same data
you evaluate on.  Walk-forward validation addresses this by repeatedly:

1. Training models on a historical window (in-sample).
2. Running the backtest on the *next* window (out-of-sample) without
   re-training or looking ahead.
3. Advancing both windows forward and repeating.

This mirrors real-world conditions: you train on past data, deploy, and the
market moves forward.

Integration with WalkForwardCV
-------------------------------
This module wraps ``models/training/walk_forward.py``'s ``WalkForwardCV`` (which
operates on NumPy arrays) and extends it to operate on OHLCV bar lists and
``BacktestEngine`` instances.

Fold structure
--------------
For an expanding-window walk-forward with ``n_splits=4``:

    Fold 0:  Train [2018-01-01 .. 2020-12-31]   OOS [2021-01-01 .. 2021-12-31]
    Fold 1:  Train [2018-01-01 .. 2021-12-31]   OOS [2022-01-01 .. 2022-12-31]
    Fold 2:  Train [2018-01-01 .. 2022-12-31]   OOS [2023-01-01 .. 2023-12-31]
    Fold 3:  Train [2018-01-01 .. 2023-12-31]   OOS [2024-01-01 .. 2024-12-31]

Usage
-----
::

    from backtesting.walkforward import WalkForwardBacktest

    wfb = WalkForwardBacktest(
        bars=bars_dict,             # dict[str, list[OHLCVBar]]
        orchestrator_factory=make_orchestrator,   # callable() → StrategyOrchestrator
        train_callback=train_models,              # callable(bars_train_dict) → None
        n_splits=4,
        oos_size_days=252,
        initial_capital=100_000.0,
    )
    results = wfb.run()
    for fold_result in results.folds:
        print(fold_result.fold_index, fold_result.report.metrics["sharpe_ratio"])
    print(results.aggregate_metrics())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backtesting.engine import BacktestEngine
from backtesting.broker import SimulatedBroker
from backtesting.metrics import compute_metrics
from backtesting.report import BacktestReport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardFoldResult:
    """Result for a single walk-forward fold."""

    fold_index: int
    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime
    report: BacktestReport

    def summary_row(self) -> dict[str, Any]:
        """One-row dict for tabular display."""
        m = self.report.metrics
        return {
            "fold": self.fold_index,
            "train_start": self.train_start.date().isoformat(),
            "train_end": self.train_end.date().isoformat(),
            "oos_start": self.oos_start.date().isoformat(),
            "oos_end": self.oos_end.date().isoformat(),
            "total_return_pct": m.get("total_return_pct"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "n_trades": m.get("n_trades"),
            "win_rate_pct": m.get("win_rate_pct"),
        }


@dataclass
class WalkForwardResults:
    """Aggregated results from all walk-forward folds."""

    folds: list[WalkForwardFoldResult] = field(default_factory=list)
    initial_capital: float = 100_000.0

    def aggregate_metrics(self) -> dict[str, Any]:
        """
        Aggregate fold-level metrics into summary statistics.

        Returns mean and std of key metrics across all OOS folds.
        """
        if not self.folds:
            return {}

        keys = [
            "total_return_pct", "cagr_pct", "sharpe_ratio", "sortino_ratio",
            "max_drawdown_pct", "win_rate_pct", "profit_factor",
        ]
        agg: dict[str, Any] = {}
        for k in keys:
            vals = [f.report.metrics.get(k, 0.0) or 0.0 for f in self.folds]
            if vals:
                import numpy as np
                agg[k] = {
                    "mean": round(float(np.mean(vals)), 4),
                    "std": round(float(np.std(vals)), 4),
                    "min": round(float(np.min(vals)), 4),
                    "max": round(float(np.max(vals)), 4),
                }

        # Concatenated equity curve (stitch folds end-to-end)
        all_pnl = [
            f.report.metrics.get("total_pnl", 0.0) or 0.0 for f in self.folds
        ]
        agg["total_oos_pnl"] = round(sum(all_pnl), 2)
        agg["n_folds"] = len(self.folds)
        return agg

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": [f.report.to_dict() | {"fold_index": f.fold_index,
                                              "oos_start": f.oos_start.isoformat(),
                                              "oos_end": f.oos_end.isoformat()}
                      for f in self.folds],
            "aggregate_metrics": self.aggregate_metrics(),
            "n_folds": len(self.folds),
        }


# ---------------------------------------------------------------------------
# WalkForwardBacktest
# ---------------------------------------------------------------------------

class WalkForwardBacktest:
    """
    Walk-forward out-of-sample backtesting framework.

    Parameters
    ----------
    bars : dict[str, list[OHLCVBar]]
        All historical bars per ticker (training + OOS combined).
    orchestrator_factory : callable
        Called for each fold with ``orchestrator_factory()`` → fresh
        ``StrategyOrchestrator`` instance.  The factory is called at the start
        of each OOS fold so each fold gets a clean strategy state.
    train_callback : callable, optional
        Called before each OOS fold with the in-sample bars:
        ``train_callback(bars_train: dict[str, list[OHLCVBar]]) -> None``.
        Use this to re-train ML models on the training window.
    n_splits : int
        Number of OOS folds.
    oos_size_days : int
        Number of calendar days in each out-of-sample period.
    min_train_days : int
        Minimum calendar days required for the first training window.
    initial_capital : float
    broker_factory : callable, optional
        ``broker_factory()`` → ``SimulatedBroker``.  Defaults to standard broker.
    feature_pipeline_factory : callable, optional
        ``feature_pipeline_factory()`` → ``FeaturePipeline`` (or None).
    """

    def __init__(
        self,
        bars: "dict[str, list[Any]]",
        orchestrator_factory: Callable[[], "Any"],
        train_callback: Callable[["dict[str, list[Any]]"], None] | None = None,
        n_splits: int = 4,
        oos_size_days: int = 252,
        min_train_days: int = 365,
        initial_capital: float = 100_000.0,
        broker_factory: Callable[[], SimulatedBroker] | None = None,
        feature_pipeline_factory: Callable[[], "Any"] | None = None,
    ) -> None:
        self.bars = bars
        self.orchestrator_factory = orchestrator_factory
        self.train_callback = train_callback
        self.n_splits = n_splits
        self.oos_size_days = oos_size_days
        self.min_train_days = min_train_days
        self.initial_capital = initial_capital
        self.broker_factory = broker_factory
        self.feature_pipeline_factory = feature_pipeline_factory

    # ── Main entry point ──────────────────────────────────────────────────

    def run(self) -> WalkForwardResults:
        """
        Execute all walk-forward folds and return aggregated results.

        Returns
        -------
        WalkForwardResults
        """
        folds = self._compute_folds()
        if not folds:
            logger.warning("No walk-forward folds could be computed — not enough data")
            return WalkForwardResults(initial_capital=self.initial_capital)

        results = WalkForwardResults(initial_capital=self.initial_capital)

        for fold_idx, (train_start, train_end, oos_start, oos_end) in enumerate(folds):
            logger.info(
                "Walk-forward fold %d/%d: train=[%s, %s] OOS=[%s, %s]",
                fold_idx + 1, len(folds),
                train_start.date(), train_end.date(),
                oos_start.date(), oos_end.date(),
            )

            # Slice bars into train and OOS windows
            train_bars = self._slice_bars(self.bars, train_start, train_end)
            oos_bars = self._slice_bars(self.bars, oos_start, oos_end)

            # (Optional) retrain models on training window
            if self.train_callback is not None:
                logger.info("Running train_callback for fold %d", fold_idx)
                self.train_callback(train_bars)

            # Run OOS backtest with fresh orchestrator and broker
            orchestrator = self.orchestrator_factory()
            broker = self.broker_factory() if self.broker_factory else SimulatedBroker()
            feature_pipeline = (
                self.feature_pipeline_factory() if self.feature_pipeline_factory else None
            )

            engine = BacktestEngine(
                bars=oos_bars,
                orchestrator=orchestrator,
                feature_pipeline=feature_pipeline,
                initial_capital=self.initial_capital,
                broker=broker,
            )
            report = engine.run()

            results.folds.append(WalkForwardFoldResult(
                fold_index=fold_idx,
                train_start=train_start,
                train_end=train_end,
                oos_start=oos_start,
                oos_end=oos_end,
                report=report,
            ))
            logger.info(
                "Fold %d complete: OOS Sharpe=%.3f Return=%.2f%% MaxDD=%.2f%%",
                fold_idx,
                report.metrics.get("sharpe_ratio", 0),
                report.metrics.get("total_return_pct", 0),
                report.metrics.get("max_drawdown_pct", 0),
            )

        return results

    # ── Internal helpers ──────────────────────────────────────────────────

    def _get_date_range(self) -> tuple[datetime, datetime]:
        """Find the overall start and end dates across all tickers."""
        all_timestamps: list[datetime] = []
        for ticker_bars in self.bars.values():
            for b in ticker_bars:
                all_timestamps.append(b.event_timestamp)
        if not all_timestamps:
            raise ValueError("No bars provided — cannot compute walk-forward folds")
        # Make tz-aware if naive
        start = min(all_timestamps)
        end = max(all_timestamps)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return start, end

    def _compute_folds(
        self,
    ) -> list[tuple[datetime, datetime, datetime, datetime]]:
        """
        Compute fold date boundaries.

        Returns list of (train_start, train_end, oos_start, oos_end).
        """
        data_start, data_end = self._get_date_range()
        total_days = (data_end - data_start).days
        required_days = self.min_train_days + self.n_splits * self.oos_size_days

        if total_days < required_days:
            logger.warning(
                "Insufficient data for %d folds × %d OOS days + %d train days. "
                "Have %d days total.  Reducing n_splits.",
                self.n_splits, self.oos_size_days, self.min_train_days, total_days,
            )

        folds = []
        # First OOS window starts after min_train_days
        first_oos_start = data_start + timedelta(days=self.min_train_days)

        for i in range(self.n_splits):
            oos_start = first_oos_start + timedelta(days=i * self.oos_size_days)
            oos_end = oos_start + timedelta(days=self.oos_size_days)
            if oos_end > data_end:
                break

            train_start = data_start
            train_end = oos_start - timedelta(days=1)

            folds.append((train_start, train_end, oos_start, oos_end))

        return folds

    @staticmethod
    def _slice_bars(
        bars: "dict[str, list[Any]]",
        start: datetime,
        end: datetime,
    ) -> "dict[str, list[Any]]":
        """Filter bars to the [start, end] date window."""
        # Ensure tz-aware comparison
        start_naive = start.replace(tzinfo=None)
        end_naive = end.replace(tzinfo=None)

        sliced: dict[str, list] = {}
        for ticker, ticker_bars in bars.items():
            filtered = []
            for b in ticker_bars:
                ts = b.event_timestamp.replace(tzinfo=None) if b.event_timestamp.tzinfo else b.event_timestamp
                if start_naive <= ts <= end_naive:
                    filtered.append(b)
            if filtered:
                sliced[ticker] = filtered
        return sliced
