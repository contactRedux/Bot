"""
models/training/walk_forward.py — Walk-forward cross-validation for time-series models.

Walk-forward cross-validation
------------------------------
Standard k-fold cross-validation shuffles data randomly — this creates future
leakage in time-series problems.  Walk-forward CV maintains chronological order:

    Training window: [t₀ ... t_train]
    Validation window: [t_train+1 ... t_train+val_size]
    Test window: [t_train+val_size+1 ... t_end]  (hold-out, never seen during training)

Each "fold" expands or rolls the training window forward in time:

    Fold 1: Train [0:200]   Val [200:250]
    Fold 2: Train [0:250]   Val [250:300]
    Fold 3: Train [0:300]   Val [300:350]
    ...

This is called "expanding window" CV.  Alternatively, a "rolling window" keeps
the training window at a fixed size (removes old data as new data arrives).

Why this matters
----------------
Financial data is non-stationary — relationships that held in 2018 may not hold
in 2023.  Walk-forward CV measures how well your model generalises *forward in
time*, which is exactly the task you face in live trading.

Never use sklearn's train_test_split(shuffle=True) for time-series models.

Usage
-----
::

    from models.training.walk_forward import WalkForwardCV, train_model_walk_forward
    from models.lstm_forecaster import LSTMForecaster

    # Low-level: iterate folds
    wfcv = WalkForwardCV(n_splits=5, val_size=252, min_train_size=252)
    for fold, (X_train, y_train, X_val, y_val) in enumerate(wfcv.split(X, y)):
        model = LSTMForecaster(input_dim=X.shape[1])
        model.train(X_train, y_train, X_val=X_val, y_val=y_val)
        metrics = evaluate_signals(model, X_val, y_val)
        print(f"Fold {fold}: {metrics}")

    # High-level: full walk-forward training pipeline
    results = train_model_walk_forward(
        model_class=LSTMForecaster,
        model_kwargs=dict(input_dim=60, seq_len=30),
        X=X_full,
        y=y_full,
        n_splits=5,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generator, Iterator, Type

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WalkForwardCV
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardFold:
    """Container for a single walk-forward fold."""
    fold_index: int
    train_start: int   # inclusive row index
    train_end: int     # exclusive
    val_start: int
    val_end: int       # exclusive


class WalkForwardCV:
    """
    Walk-forward cross-validator for time-series data.

    Two modes are supported:

    * **Expanding window** (default): training window grows each fold.
    * **Rolling window**: training window slides forward at fixed size.

    Parameters
    ----------
    n_splits : int
        Number of folds.
    val_size : int
        Number of rows in each validation window.
    min_train_size : int
        Minimum number of training rows before the first fold.
    gap : int
        Number of rows between the end of training and start of validation.
        Set > 0 to avoid leakage when features use future data (e.g. if your
        target is return over the next 5 bars, set gap=5).
    rolling : bool
        If True, use a rolling (fixed-size) training window instead of expanding.
    train_size : int | None
        Fixed training window size for rolling mode.  Ignored in expanding mode.
    """

    def __init__(
        self,
        n_splits: int = 5,
        val_size: int = 252,
        min_train_size: int = 252,
        gap: int = 0,
        rolling: bool = False,
        train_size: int | None = None,
    ) -> None:
        self.n_splits = n_splits
        self.val_size = val_size
        self.min_train_size = min_train_size
        self.gap = gap
        self.rolling = rolling
        self.train_size = train_size

    def split(
        self,
        X: np.ndarray | pd.DataFrame,
        y: np.ndarray | pd.Series | None = None,
    ) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """
        Iterate over walk-forward folds.

        Yields
        ------
        tuple of (X_train, y_train, X_val, y_val)
            All as numpy arrays.  Chronological order is preserved.
        """
        X_arr = X.to_numpy() if isinstance(X, pd.DataFrame) else np.asarray(X)
        y_arr = y.to_numpy() if isinstance(y, (pd.Series, pd.DataFrame)) else np.asarray(y)

        n = len(X_arr)
        folds = list(self.get_folds(n))

        for fold in folds:
            X_train = X_arr[fold.train_start : fold.train_end]
            y_train = y_arr[fold.train_start : fold.train_end]
            X_val = X_arr[fold.val_start : fold.val_end]
            y_val = y_arr[fold.val_start : fold.val_end]
            yield X_train, y_train, X_val, y_val

    def get_folds(self, n: int) -> list[WalkForwardFold]:
        """
        Compute fold boundaries for a dataset of length n.

        Returns a list of WalkForwardFold objects.
        """
        # Total data needed: min_train_size + gap + n_splits * val_size
        total_needed = self.min_train_size + self.gap + self.n_splits * self.val_size
        if n < total_needed:
            raise ValueError(
                f"Dataset too small ({n} rows) for walk-forward CV. "
                f"Need >= {total_needed}. "
                f"Reduce n_splits ({self.n_splits}), val_size ({self.val_size}), "
                f"or min_train_size ({self.min_train_size})."
            )

        folds = []
        # First validation window starts after min_train_size + gap
        first_val_start = self.min_train_size + self.gap

        for i in range(self.n_splits):
            val_start = first_val_start + i * self.val_size
            val_end = val_start + self.val_size

            if val_end > n:
                break  # Not enough data for this fold

            train_end = val_start - self.gap

            if self.rolling and self.train_size is not None:
                train_start = max(0, train_end - self.train_size)
            else:
                train_start = 0

            folds.append(WalkForwardFold(
                fold_index=i,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
            ))

        return folds

    def summary(self, n: int, index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
        """
        Return a DataFrame summarizing the fold splits.

        Parameters
        ----------
        n : int
            Dataset length.
        index : DatetimeIndex, optional
            If provided, includes date labels for each fold.
        """
        folds = self.get_folds(n)
        rows = []
        for fold in folds:
            row: dict[str, Any] = {
                "fold": fold.fold_index,
                "train_start_idx": fold.train_start,
                "train_end_idx": fold.train_end,
                "val_start_idx": fold.val_start,
                "val_end_idx": fold.val_end,
                "train_size": fold.train_end - fold.train_start,
                "val_size": fold.val_end - fold.val_start,
            }
            if index is not None and len(index) > fold.val_end - 1:
                row["train_start_date"] = str(index[fold.train_start].date())
                row["train_end_date"] = str(index[fold.train_end - 1].date())
                row["val_start_date"] = str(index[fold.val_start].date())
                row["val_end_date"] = str(index[fold.val_end - 1].date())
            rows.append(row)
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Signal evaluation utilities
# ---------------------------------------------------------------------------

def evaluate_signals(
    predictions: np.ndarray,
    actuals: np.ndarray,
    confidences: np.ndarray | None = None,
) -> dict[str, float]:
    """
    Compute evaluation metrics for model signal predictions.

    Parameters
    ----------
    predictions : array (n,)
        Predicted signals in [-1, +1].
    actuals : array (n,)
        Actual next-period log returns.
    confidences : array (n,), optional
        Model confidence scores.  Used for confidence-weighted Sharpe.

    Returns
    -------
    dict
        Metrics: ``rmse``, ``direction_accuracy``, ``sharpe_ratio``,
        ``max_drawdown``, optionally ``conf_weighted_sharpe``.
    """
    preds = np.asarray(predictions, dtype=np.float64)
    acts = np.asarray(actuals, dtype=np.float64)
    n = len(preds)

    # RMSE
    rmse = float(np.sqrt(np.mean((preds - acts) ** 2)))

    # Direction accuracy: did the predicted sign match the actual sign?
    direction_hits = np.sign(preds) == np.sign(acts)
    direction_accuracy = float(np.mean(direction_hits))

    # Strategy returns: trade in direction of prediction (scaled by signal magnitude)
    strategy_returns = preds * acts

    # Sharpe ratio (annualized, assuming daily bars)
    daily_mean = np.mean(strategy_returns)
    daily_std = np.std(strategy_returns) + 1e-8
    sharpe = float(daily_mean / daily_std * np.sqrt(252))

    # Max drawdown
    cum_returns = np.cumprod(1 + np.clip(strategy_returns, -0.5, 0.5))
    running_max = np.maximum.accumulate(cum_returns)
    drawdowns = (running_max - cum_returns) / (running_max + 1e-8)
    max_drawdown = float(np.max(drawdowns))

    metrics = {
        "rmse": rmse,
        "direction_accuracy": direction_accuracy,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "n_samples": n,
    }

    # Confidence-weighted Sharpe
    if confidences is not None:
        confs = np.asarray(confidences, dtype=np.float64)
        confs = np.clip(confs, 1e-4, 1.0)
        w_returns = confs * preds * acts
        w_sharpe = float(np.mean(w_returns) / (np.std(w_returns) + 1e-8) * np.sqrt(252))
        metrics["conf_weighted_sharpe"] = w_sharpe

    return metrics


# ---------------------------------------------------------------------------
# High-level walk-forward training pipeline
# ---------------------------------------------------------------------------

from models.base import BaseSignalModel  # noqa: E402  (import after class definitions)


def train_model_walk_forward(
    model_class: Type[BaseSignalModel],
    model_kwargs: dict[str, Any],
    X: np.ndarray | pd.DataFrame,
    y: np.ndarray | pd.Series,
    n_splits: int = 5,
    val_size: int = 252,
    min_train_size: int = 252,
    gap: int = 0,
    rolling: bool = False,
    train_size: int | None = None,
) -> list[dict[str, Any]]:
    """
    Run a full walk-forward training and evaluation pipeline.

    For each fold:
    1. Instantiate a fresh model from ``model_class(**model_kwargs)``.
    2. Train on the training split.
    3. Predict on the validation split.
    4. Evaluate signals.

    Parameters
    ----------
    model_class : type
        A BaseSignalModel subclass.
    model_kwargs : dict
        Keyword arguments passed to model_class.__init__.
    X : array (n, feature_dim)
        Feature matrix in chronological order.
    y : array (n,)
        Target returns in chronological order.
    n_splits, val_size, min_train_size, gap, rolling, train_size
        Passed through to WalkForwardCV.

    Returns
    -------
    list[dict]
        One dict per fold with keys: ``fold``, ``metrics``, ``model``,
        ``val_indices``.
    """
    wfcv = WalkForwardCV(
        n_splits=n_splits,
        val_size=val_size,
        min_train_size=min_train_size,
        gap=gap,
        rolling=rolling,
        train_size=train_size,
    )
    folds = wfcv.get_folds(len(X) if hasattr(X, "__len__") else X.shape[0])
    X_arr = X.to_numpy() if isinstance(X, pd.DataFrame) else np.asarray(X)
    y_arr = y.to_numpy() if isinstance(y, (pd.Series, pd.DataFrame)) else np.asarray(y)

    results = []
    for fold in folds:
        logger.info(
            "Walk-forward fold %d/%d: train [%d:%d] val [%d:%d]",
            fold.fold_index + 1, len(folds),
            fold.train_start, fold.train_end,
            fold.val_start, fold.val_end,
        )

        X_train = X_arr[fold.train_start : fold.train_end]
        y_train = y_arr[fold.train_start : fold.train_end]
        X_val   = X_arr[fold.val_start   : fold.val_end]
        y_val   = y_arr[fold.val_start   : fold.val_end]

        model = model_class(**model_kwargs)
        model.train(X_train, y_train, X_val=X_val, y_val=y_val)

        # Predict on validation set (one bar at a time for sequence models,
        # or bulk for tabular models)
        val_signals = []
        val_confidences = []
        for i in range(len(X_val)):
            # Pass full context up to bar i
            context_start = max(0, i - getattr(model, "seq_len", 1) + 1)
            out = model.predict(X_val[context_start : i + 1])
            val_signals.append(out.signal)
            val_confidences.append(out.confidence)

        metrics = evaluate_signals(
            np.array(val_signals),
            y_val,
            np.array(val_confidences),
        )

        logger.info("Fold %d metrics: %s", fold.fold_index + 1, metrics)

        results.append({
            "fold": fold.fold_index,
            "train_start": fold.train_start,
            "train_end": fold.train_end,
            "val_start": fold.val_start,
            "val_end": fold.val_end,
            "metrics": metrics,
            "model": model,
            "val_signals": np.array(val_signals),
            "val_confidences": np.array(val_confidences),
            "val_targets": y_val,
        })

    return results
