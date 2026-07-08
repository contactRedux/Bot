"""
models/base.py — BaseSignalModel interface and SignalOutput dataclass.

Every model in the ``models`` package implements ``BaseSignalModel``.  This
enforces a uniform interface so the ensemble layer, registry, and strategy
orchestrator can treat all models identically.

SignalOutput
------------
The shared prediction container returned by every model's ``predict()`` call:

* ``signal``     — float in [-1, +1].  -1 = full short, +1 = full long.
* ``confidence`` — float in [0, 1].    1 = maximum confidence.
* ``model_id``   — str identifier matching the model's registry key.
* ``timestamp``  — UTC datetime of the prediction.

BaseSignalModel
---------------
Abstract class with four required methods:

* ``train(X, y)``          — Fit the model on a training set.
* ``predict(X) -> SignalOutput``   — Generate a single prediction for the
                                     latest row (or batch) of features.
* ``save(path)``           — Serialize all state to *path*.
* ``load(path)``           — Restore state from *path* (classmethod).

The ``model_id`` property must return a stable, lowercase snake_case string
(e.g. ``"lstm_forecaster"``) used as the registry key and embedded in every
``SignalOutput``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# SignalOutput
# ---------------------------------------------------------------------------

@dataclass
class SignalOutput:
    """
    Unified prediction container returned by every BaseSignalModel.

    Attributes
    ----------
    signal : float
        Directional signal in [-1, +1].
        Positive values indicate bullish conviction; negative bearish.
        The magnitude encodes *how* bullish/bearish (e.g. 0.3 = mild long,
        1.0 = max long).
    confidence : float
        Model confidence in [0, 1].  Used by the position sizer to scale
        order size.  For GP models this is derived from predictive variance;
        for neural models it is typically a calibrated output probability.
    model_id : str
        Stable identifier of the model that produced this signal.
        Matches the key in ModelRegistry.
    timestamp : datetime
        UTC wall-clock time at which the prediction was generated.  Must be
        >= the latest event_timestamp in the feature row used for prediction
        (never in the future relative to the feature data).
    metadata : dict
        Optional extra fields — e.g. SHAP values, attention weights, GP
        variance, feature importances.  Kept outside the core fields so
        callers can ignore it.
    """

    signal: float
    confidence: float
    model_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Clamp to valid ranges — models should produce valid values, but
        # clamp rather than raise to avoid crashing in production.
        self.signal = float(np.clip(self.signal, -1.0, 1.0))
        self.confidence = float(np.clip(self.confidence, 0.0, 1.0))

    @property
    def direction(self) -> str:
        """Human-readable direction: 'long', 'short', or 'neutral'."""
        if self.signal > 0.05:
            return "long"
        if self.signal < -0.05:
            return "short"
        return "neutral"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (useful for API responses and logging)."""
        return {
            "signal": self.signal,
            "confidence": self.confidence,
            "model_id": self.model_id,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction,
            **self.metadata,
        }


# ---------------------------------------------------------------------------
# BaseSignalModel
# ---------------------------------------------------------------------------

class BaseSignalModel(abc.ABC):
    """
    Abstract base class for all signal-generating models.

    Subclasses must implement ``model_id``, ``train``, ``predict``,
    ``save``, and ``load``.

    Parameters
    ----------
    input_dim : int
        Number of input features.  Should match ``len(FeaturePipeline.feature_names())``.
    """

    def __init__(self, input_dim: int) -> None:
        self.input_dim = input_dim
        self._is_trained: bool = False

    # ── Required interface ────────────────────────────────────────────────────

    @property
    @abc.abstractmethod
    def model_id(self) -> str:
        """Stable lowercase snake_case identifier (e.g. ``'lstm_forecaster'``)."""

    @abc.abstractmethod
    def train(
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        **kwargs: Any,
    ) -> None:
        """
        Fit the model on labelled training data.

        Parameters
        ----------
        X : array-like, shape (n_samples, input_dim)
            Feature matrix.  Must be chronologically ordered.
        y : array-like, shape (n_samples,)
            Target labels / returns.  Convention: next-period log return
            clipped to [-1, +1].
        """

    @abc.abstractmethod
    def predict(
        self,
        X: pd.DataFrame | np.ndarray,
    ) -> SignalOutput:
        """
        Generate a signal for the latest feature row(s).

        Parameters
        ----------
        X : array-like
            Feature matrix.  For sequence models pass the full lookback window;
            for tabular models pass a single row or recent batch.

        Returns
        -------
        SignalOutput
        """

    @abc.abstractmethod
    def save(self, path: str | Path) -> None:
        """Persist model weights and metadata to *path*."""

    @classmethod
    @abc.abstractmethod
    def load(cls, path: str | Path) -> "BaseSignalModel":
        """Restore a previously saved model from *path*."""

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def _assert_trained(self) -> None:
        if not self._is_trained:
            raise RuntimeError(
                f"{self.__class__.__name__} has not been trained yet. "
                "Call train() before predict()."
            )
