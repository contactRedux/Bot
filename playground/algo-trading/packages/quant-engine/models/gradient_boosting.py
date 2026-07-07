"""
models/gradient_boosting.py — LightGBM gradient boosting with SHAP interpretability.

Why LightGBM?
-------------
Neural models (LSTM, Transformer, GP) are powerful but act as black boxes.
LightGBM provides a "glass box" counterpart — it:

1. Trains 10–100× faster than neural models on tabular features.
2. Produces SHAP values: for each prediction, we know *which features drove it*.
   This is essential for debugging ("why did the model short AAPL here?").
3. Handles missing values natively (no imputation needed).
4. Provides a strong baseline — many quant shops use GBDT as their primary model.

Signal generation
-----------------
We train a **regression** GBDT targeting the next-period log return.  The raw
prediction is clipped to [-1, +1] via tanh.  Confidence is derived from the
prediction's z-score relative to the training distribution (high-magnitude
predictions in the tails of the training distribution → higher confidence).

SHAP values
-----------
After each ``predict()`` call, ``SignalOutput.metadata['shap_values']`` contains
a dict mapping feature_name → SHAP contribution.  This allows strategy-level
logging of "which features drove this trade".

Usage
-----
::

    from models.gradient_boosting import GradientBoostingModel

    model = GradientBoostingModel(input_dim=60, feature_names=feature_names)
    model.train(X_train, y_train)
    out = model.predict(X_latest)
    print(out.signal, out.confidence)
    print(out.metadata['shap_values'])  # top features
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
    import lightgbm as lgb
    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False
    logger.warning("LightGBM not installed — GradientBoostingModel unavailable. pip install lightgbm")

try:
    import shap
    _SHAP_AVAILABLE = True
except ImportError:
    _SHAP_AVAILABLE = False
    logger.warning("SHAP not installed — SHAP explanations disabled. pip install shap")

from models.base import BaseSignalModel, SignalOutput


class GradientBoostingModel(BaseSignalModel):
    """
    LightGBM regressor with SHAP feature importance.

    Parameters
    ----------
    input_dim : int
        Number of input features.
    feature_names : list[str], optional
        Feature names for SHAP attribution.  If None, uses ``f0, f1, ...``.
    n_estimators : int
        Number of boosting rounds.
    learning_rate : float
        Step size shrinkage.
    num_leaves : int
        Maximum number of leaves per tree (controls model complexity).
    min_child_samples : int
        Minimum samples in a leaf (prevents overfitting on small splits).
    subsample : float
        Fraction of rows sampled per tree (row sub-sampling).
    colsample_bytree : float
        Fraction of features sampled per tree.
    reg_alpha : float
        L1 regularization.
    reg_lambda : float
        L2 regularization.
    early_stopping_rounds : int
        Stop if val metric doesn't improve for this many rounds.
    verbose : int
        LightGBM verbosity (-1 = silent).
    """

    def __init__(
        self,
        input_dim: int,
        feature_names: list[str] | None = None,
        n_estimators: int = 300,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        min_child_samples: int = 20,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        reg_alpha: float = 0.1,
        reg_lambda: float = 1.0,
        early_stopping_rounds: int = 30,
        verbose: int = -1,
    ) -> None:
        super().__init__(input_dim)
        if not _LGB_AVAILABLE:
            raise ImportError("lightgbm is required. Install with: pip install lightgbm")

        self.feature_names = feature_names or [f"f{i}" for i in range(input_dim)]
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.reg_alpha = reg_alpha
        self.reg_lambda = reg_lambda
        self.early_stopping_rounds = early_stopping_rounds
        self.verbose = verbose

        self._model: "lgb.Booster | None" = None
        self._explainer: "shap.TreeExplainer | None" = None
        self._train_pred_std: float = 1.0  # std of train predictions for confidence

    @property
    def model_id(self) -> str:
        return "gradient_boosting"

    # ── Training ─────────────────────────────────────────────────────────────

    def train(  # type: ignore[override]
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        X_val: pd.DataFrame | np.ndarray | None = None,
        y_val: pd.Series | np.ndarray | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Train LightGBM with walk-forward validation split.

        Walk-forward integrity: X must be in chronological order.
        If X_val is not provided, uses the last 20% of X as validation.
        Validation data is never from before any training data (no shuffle).
        """
        X_arr = self._to_numpy(X)
        y_arr = self._to_numpy(y)

        if X_val is not None and y_val is not None:
            X_val_arr = self._to_numpy(X_val)
            y_val_arr = self._to_numpy(y_val)
        else:
            # Walk-forward split: last 20% of training data as validation
            split = int(len(X_arr) * 0.8)
            X_val_arr = X_arr[split:]
            y_val_arr = y_arr[split:]
            X_arr = X_arr[:split]
            y_arr = y_arr[:split]

        dtrain = lgb.Dataset(
            X_arr, label=y_arr, feature_name=self.feature_names, free_raw_data=False
        )
        dval = lgb.Dataset(
            X_val_arr, label=y_val_arr, feature_name=self.feature_names, free_raw_data=False,
            reference=dtrain,
        )

        params = {
            "objective": "regression",
            "metric": "rmse",
            "num_leaves": self.num_leaves,
            "learning_rate": self.learning_rate,
            "min_child_samples": self.min_child_samples,
            "subsample": self.subsample,
            "colsample_bytree": self.colsample_bytree,
            "reg_alpha": self.reg_alpha,
            "reg_lambda": self.reg_lambda,
            "verbose": self.verbose,
            "n_jobs": -1,
        }
        callbacks = [lgb.early_stopping(self.early_stopping_rounds, verbose=False)]
        if self.verbose >= 0:
            callbacks.append(lgb.log_evaluation(period=50))

        self._model = lgb.train(
            params,
            dtrain,
            num_boost_round=self.n_estimators,
            valid_sets=[dval],
            callbacks=callbacks,
        )

        # Calibrate confidence: std of predictions on training set
        train_preds = self._model.predict(X_arr)
        self._train_pred_std = float(np.std(train_preds)) + 1e-6

        # Build SHAP explainer
        if _SHAP_AVAILABLE:
            self._explainer = shap.TreeExplainer(self._model)
            logger.debug("GradientBoosting: SHAP explainer built")

        self._is_trained = True
        logger.info(
            "GradientBoosting trained: %d trees, val_rmse=%.6f",
            self._model.num_trees(),
            # Best iteration validation score (last recorded metric)
            list(self._model.best_score.get("valid_0", {}).values())[0]
            if self._model.best_score else float("nan"),
        )

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame | np.ndarray, **kwargs: Any) -> SignalOutput:
        """
        Predict signal and compute SHAP values for the latest feature row.

        Parameters
        ----------
        X : array, shape (n, input_dim)
            Feature matrix. Uses the last row.
        """
        self._assert_trained()
        X_arr = self._to_numpy(X)
        if X_arr.ndim == 1:
            X_arr = X_arr[np.newaxis, :]
        row = X_arr[[-1]]  # latest bar

        raw = float(self._model.predict(row)[0])

        # Map raw prediction to [-1, +1] via tanh
        signal = float(np.tanh(raw))

        # Confidence: z-score of the raw prediction relative to training distribution
        z = abs(raw) / self._train_pred_std
        # sigmoid(z - 1): confident when z > 1 (prediction is stronger than avg)
        confidence = float(1.0 / (1.0 + np.exp(-(z - 1.0))))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        # SHAP values for interpretability
        metadata: dict[str, Any] = {"raw_prediction": raw}
        if _SHAP_AVAILABLE and self._explainer is not None:
            try:
                sv = self._explainer.shap_values(row)
                if isinstance(sv, list):
                    sv = sv[0]
                shap_dict = {
                    name: float(val)
                    for name, val in zip(self.feature_names, sv[0])
                }
                # Keep only top-10 by magnitude for conciseness
                top10 = dict(
                    sorted(shap_dict.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
                )
                metadata["shap_values"] = top10
            except Exception as exc:
                logger.debug("SHAP computation failed: %s", exc)

        return SignalOutput(
            signal=signal,
            confidence=confidence,
            model_id=self.model_id,
            metadata=metadata,
        )

    # ── Feature importance ────────────────────────────────────────────────────

    def feature_importance(self, importance_type: str = "gain") -> dict[str, float]:
        """
        Return LightGBM feature importances.

        Parameters
        ----------
        importance_type : str
            ``'gain'`` (total gain, preferred) or ``'split'`` (count of splits).
        """
        self._assert_trained()
        importances = self._model.feature_importance(importance_type=importance_type)
        return dict(zip(self.feature_names, importances.tolist()))

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path / "model.lgb"))
        meta = {
            "input_dim": self.input_dim,
            "feature_names": self.feature_names,
            "train_pred_std": self._train_pred_std,
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        logger.info("GradientBoostingModel saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "GradientBoostingModel":
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        model = cls(
            input_dim=meta["input_dim"],
            feature_names=meta["feature_names"],
        )
        model._model = lgb.Booster(model_file=str(path / "model.lgb"))
        model._train_pred_std = meta.get("train_pred_std", 1.0)
        if _SHAP_AVAILABLE:
            model._explainer = shap.TreeExplainer(model._model)
        model._is_trained = True
        return model

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_numpy(arr: Any) -> np.ndarray:
        if isinstance(arr, (pd.DataFrame, pd.Series)):
            return arr.to_numpy(dtype=np.float32)
        return np.asarray(arr, dtype=np.float32)
