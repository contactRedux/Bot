"""
tests/models/test_walk_forward.py — Unit tests for WalkForwardCV and evaluate_signals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models.training.walk_forward import WalkForwardCV, evaluate_signals


class TestWalkForwardCV:
    def _make_data(self, n: int = 1500):
        rng = np.random.default_rng(0)
        X = rng.standard_normal((n, 10)).astype(np.float32)
        y = rng.standard_normal(n).astype(np.float32) * 0.02
        return X, y

    def test_expanding_fold_count(self):
        X, y = self._make_data(1500)
        wfcv = WalkForwardCV(n_splits=5, val_size=100, min_train_size=500)
        folds = list(wfcv.split(X, y))
        assert len(folds) == 5

    def test_rolling_fold_boundaries(self):
        X, y = self._make_data(2000)
        wfcv = WalkForwardCV(
            n_splits=4, val_size=100, min_train_size=500,
            rolling=True, train_size=500,
        )
        folds_data = list(wfcv.split(X, y))
        for X_tr, y_tr, X_v, y_v in folds_data:
            # Rolling: training window must be <= train_size
            assert len(X_tr) <= 500

    def test_no_temporal_leakage(self):
        """Validation data must always come AFTER training data."""
        X, y = self._make_data(1500)
        wfcv = WalkForwardCV(n_splits=4, val_size=100, min_train_size=400)
        folds = wfcv.get_folds(len(X))
        for fold in folds:
            # train ends before val starts (accounting for gap)
            assert fold.train_end <= fold.val_start

    def test_gap_respected(self):
        X, y = self._make_data(2000)
        gap = 5
        wfcv = WalkForwardCV(n_splits=3, val_size=100, min_train_size=500, gap=gap)
        folds = wfcv.get_folds(len(X))
        for fold in folds:
            assert fold.val_start - fold.train_end == gap

    def test_dataset_too_small_raises(self):
        X, y = self._make_data(100)
        wfcv = WalkForwardCV(n_splits=5, val_size=100, min_train_size=500)
        with pytest.raises(ValueError, match="too small"):
            wfcv.get_folds(100)

    def test_folds_are_non_overlapping(self):
        """No row should appear in both train and val in the same fold."""
        X, y = self._make_data(1500)
        wfcv = WalkForwardCV(n_splits=4, val_size=100, min_train_size=500)
        folds = wfcv.get_folds(len(X))
        for fold in folds:
            train_set = set(range(fold.train_start, fold.train_end))
            val_set = set(range(fold.val_start, fold.val_end))
            assert len(train_set & val_set) == 0

    def test_summary_returns_dataframe(self):
        X, y = self._make_data(1500)
        wfcv = WalkForwardCV(n_splits=3, val_size=100, min_train_size=500)
        df = wfcv.summary(n=len(X))
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3
        assert "fold" in df.columns

    def test_expanding_train_grows(self):
        """In expanding mode, training size should grow each fold."""
        X, y = self._make_data(2000)
        wfcv = WalkForwardCV(n_splits=5, val_size=100, min_train_size=500)
        folds = wfcv.get_folds(len(X))
        train_sizes = [f.train_end - f.train_start for f in folds]
        assert train_sizes == sorted(train_sizes), "Training sizes must be non-decreasing in expanding mode"


class TestEvaluateSignals:
    def test_perfect_direction(self):
        """If predictions perfectly predict direction, accuracy should be 1.0."""
        preds = np.array([1.0, -1.0, 1.0, -1.0, 1.0])
        acts  = np.array([0.01, -0.02, 0.005, -0.01, 0.02])
        metrics = evaluate_signals(preds, acts)
        assert metrics["direction_accuracy"] == pytest.approx(1.0)

    def test_zero_direction_accuracy(self):
        preds = np.array([1.0, -1.0, 1.0, -1.0])
        acts  = np.array([-0.01, 0.02, -0.005, 0.01])
        metrics = evaluate_signals(preds, acts)
        assert metrics["direction_accuracy"] == pytest.approx(0.0)

    def test_sharpe_positive_for_good_model(self):
        rng = np.random.default_rng(42)
        acts = rng.normal(0, 0.01, 252)
        # Perfect predictions
        preds = np.sign(acts) * 0.5
        metrics = evaluate_signals(preds, acts)
        assert metrics["sharpe_ratio"] > 0

    def test_rmse_zero_for_perfect(self):
        acts = np.array([0.1, -0.1, 0.0, 0.2])
        metrics = evaluate_signals(acts, acts)
        assert metrics["rmse"] == pytest.approx(0.0, abs=1e-8)

    def test_metrics_keys(self):
        preds = np.zeros(50)
        acts = np.random.default_rng(0).normal(0, 0.01, 50)
        metrics = evaluate_signals(preds, acts)
        assert "rmse" in metrics
        assert "direction_accuracy" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics

    def test_confidence_weighted_sharpe_present_when_confs_given(self):
        preds = np.array([0.5, -0.5, 0.5, -0.5])
        acts  = np.array([0.01, -0.01, 0.01, -0.01])
        confs = np.array([0.8, 0.9, 0.7, 0.85])
        metrics = evaluate_signals(preds, acts, confs)
        assert "conf_weighted_sharpe" in metrics

    def test_max_drawdown_non_negative(self):
        rng = np.random.default_rng(1)
        preds = rng.uniform(-1, 1, 100)
        acts  = rng.normal(0, 0.01, 100)
        metrics = evaluate_signals(preds, acts)
        assert metrics["max_drawdown"] >= 0.0
