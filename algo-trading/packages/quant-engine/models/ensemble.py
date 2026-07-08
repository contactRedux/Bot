"""
models/ensemble.py — Ridge regression meta-learner over model outputs.

How ensemble learning works here
---------------------------------
Each base model (LSTM, Transformer, GP, LightGBM, PPO) produces a signal in
[-1, +1] for each bar.  The ensemble meta-learner trains a **Ridge regression**
on these model outputs during a held-out *validation period* (never the test
period — no future leakage).

Why Ridge regression?
---------------------
* **Regularization**: Ridge (L2) shrinks the weights toward zero, preventing
  any single model from dominating.  Useful when some models occasionally
  produce extreme signals.
* **Interpretability**: the learned weights tell us which models the ensemble
  trusts most — e.g. "LSTM gets weight 0.4, GP gets 0.2 in this regime".
* **Fast**: fits in milliseconds on a 5-column matrix of model outputs.

Walk-forward safety
--------------------
The ensemble is trained on validation-period signals *only*.  The validation
period is always *after* the training period of the base models but *before*
the test period.  This 3-way split is mandatory for time-series data.

Confidence blending
-------------------
The blended confidence is the *weighted average* of model confidences, using
the Ridge weights as the weighting scheme (softmax-normalized to sum to 1).

Usage
-----
::

    from models.ensemble import EnsembleModel

    ensemble = EnsembleModel(model_ids=["lstm_forecaster", "transformer_signal",
                                        "gaussian_process", "gradient_boosting",
                                        "rl_ppo_agent"])
    # val_signals shape: (n_val_bars, n_models)
    # val_confidences shape: (n_val_bars, n_models)
    # val_targets shape: (n_val_bars,)
    ensemble.train(val_signals, val_targets, val_confidences=val_confidences)

    # Predict from a current set of model outputs
    signals_now = np.array([0.3, 0.5, -0.1, 0.4, 0.2])
    confs_now   = np.array([0.8, 0.7,  0.9, 0.6, 0.5])
    out = ensemble.predict_from_outputs(signals_now, confs_now)
"""

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn not installed — EnsembleModel unavailable. pip install scikit-learn")

from models.base import BaseSignalModel, SignalOutput


class EnsembleModel(BaseSignalModel):
    """
    Ridge regression meta-learner that blends base model signals.

    Parameters
    ----------
    model_ids : list[str]
        Ordered list of base model identifiers.  The order must match the
        column order of the signal arrays passed to ``train()`` and
        ``predict_from_outputs()``.
    alpha : float
        Ridge regularization strength.  Higher = more shrinkage.
    fit_intercept : bool
        Whether to fit an intercept term.
    normalize_signals : bool
        If True, StandardScaler normalizes each model's signal column before
        fitting (handles different signal scales across models).
    """

    def __init__(
        self,
        model_ids: list[str],
        alpha: float = 1.0,
        fit_intercept: bool = True,
        normalize_signals: bool = True,
        # input_dim is not meaningful for ensemble — set to n_models
        input_dim: int = 0,
    ) -> None:
        n_models = len(model_ids)
        super().__init__(input_dim=n_models if input_dim == 0 else input_dim)
        if not _SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn is required. Install with: pip install scikit-learn")

        self.model_ids = list(model_ids)
        self.alpha = alpha
        self.fit_intercept = fit_intercept
        self.normalize_signals = normalize_signals

        self._ridge = Ridge(alpha=alpha, fit_intercept=fit_intercept)
        self._scaler = StandardScaler() if normalize_signals else None
        self._weights: np.ndarray | None = None  # coefficients after fitting

    @property
    def model_id(self) -> str:
        return "ensemble"

    # ── Training ─────────────────────────────────────────────────────────────

    def train(  # type: ignore[override]
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        val_confidences: pd.DataFrame | np.ndarray | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Fit Ridge meta-learner on validation-period model outputs.

        Parameters
        ----------
        X : array, shape (n_val, n_models)
            Base model signal outputs during the validation period.
            Columns must be in the same order as ``self.model_ids``.
        y : array, shape (n_val,)
            True next-period returns during the validation period.
        val_confidences : array, shape (n_val, n_models), optional
            Model confidence scores.  Stored but not used in Ridge fitting.
        """
        X_arr = self._to_2d_numpy(X)
        y_arr = self._to_numpy(y)

        if X_arr.shape[1] != len(self.model_ids):
            raise ValueError(
                f"Expected {len(self.model_ids)} model columns, got {X_arr.shape[1]}"
            )

        if self.normalize_signals:
            X_fit = self._scaler.fit_transform(X_arr)
        else:
            X_fit = X_arr

        self._ridge.fit(X_fit, y_arr)
        self._weights = self._ridge.coef_.copy()

        self._is_trained = True
        logger.info(
            "EnsembleModel trained: weights=%s",
            dict(zip(self.model_ids, self._weights.tolist())),
        )

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame | np.ndarray, **kwargs: Any) -> SignalOutput:
        """
        Predict from a matrix of base model signals.

        Parameters
        ----------
        X : array, shape (n, n_models)
            Base model signal outputs.  Uses the last row.
        confidences : array, shape (n_models,), optional
            Confidence for each base model at the latest bar.
        """
        self._assert_trained()
        X_arr = self._to_2d_numpy(X)
        signals_now = X_arr[-1]
        confidences = kwargs.get("confidences", np.ones(len(self.model_ids)) * 0.5)
        return self.predict_from_outputs(signals_now, np.asarray(confidences))

    def predict_from_outputs(
        self,
        signals: np.ndarray,
        confidences: np.ndarray,
    ) -> SignalOutput:
        """
        Blend signals and confidences from a single set of base model outputs.

        Parameters
        ----------
        signals : array, shape (n_models,)
            Signal from each base model for the current bar.
        confidences : array, shape (n_models,)
            Confidence from each base model.
        """
        self._assert_trained()
        signals = np.asarray(signals, dtype=np.float32)
        confidences = np.asarray(confidences, dtype=np.float32)

        if len(signals) != len(self.model_ids):
            raise ValueError(
                f"Expected {len(self.model_ids)} signals, got {len(signals)}"
            )

        x = signals[np.newaxis, :]  # (1, n_models)
        if self.normalize_signals:
            x = self._scaler.transform(x)

        raw = float(self._ridge.predict(x)[0])
        signal = float(np.tanh(raw))   # map to [-1, +1]

        # Confidence: weighted average of base model confidences, weighted
        # by softmax of |Ridge coefficients| (larger weight → more trusted)
        abs_weights = np.abs(self._weights)
        soft_weights = np.exp(abs_weights) / (np.sum(np.exp(abs_weights)) + 1e-8)
        confidence = float(np.dot(soft_weights, confidences))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        per_model = {
            mid: {"signal": float(s), "confidence": float(c), "weight": float(w)}
            for mid, s, c, w in zip(self.model_ids, signals, confidences, soft_weights)
        }
        return SignalOutput(
            signal=signal,
            confidence=confidence,
            model_id=self.model_id,
            metadata={
                "raw_prediction": raw,
                "ridge_weights": dict(zip(self.model_ids, self._weights.tolist())),
                "per_model": per_model,
            },
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "ridge.pkl", "wb") as f:
            pickle.dump(self._ridge, f)
        if self._scaler is not None:
            with open(path / "scaler.pkl", "wb") as f:
                pickle.dump(self._scaler, f)
        meta = {
            "model_ids": self.model_ids,
            "alpha": self.alpha,
            "fit_intercept": self.fit_intercept,
            "normalize_signals": self.normalize_signals,
            "weights": self._weights.tolist() if self._weights is not None else None,
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        logger.info("EnsembleModel saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "EnsembleModel":
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        model = cls(
            model_ids=meta["model_ids"],
            alpha=meta["alpha"],
            fit_intercept=meta["fit_intercept"],
            normalize_signals=meta["normalize_signals"],
        )
        with open(path / "ridge.pkl", "rb") as f:
            model._ridge = pickle.load(f)
        if meta["normalize_signals"] and (path / "scaler.pkl").exists():
            with open(path / "scaler.pkl", "rb") as f:
                model._scaler = pickle.load(f)
        if meta["weights"] is not None:
            model._weights = np.array(meta["weights"])
        model._is_trained = True
        return model

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_numpy(arr: Any) -> np.ndarray:
        if isinstance(arr, (pd.DataFrame, pd.Series)):
            return arr.to_numpy(dtype=np.float32)
        return np.asarray(arr, dtype=np.float32)

    @staticmethod
    def _to_2d_numpy(arr: Any) -> np.ndarray:
        if isinstance(arr, pd.DataFrame):
            return arr.to_numpy(dtype=np.float32)
        a = np.asarray(arr, dtype=np.float32)
        if a.ndim == 1:
            return a[np.newaxis, :]
        return a
