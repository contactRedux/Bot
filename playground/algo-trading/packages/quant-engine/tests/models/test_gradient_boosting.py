"""
tests/models/test_gradient_boosting.py — Isolated LightGBM tests.

This file is intentionally kept separate from test_models.py.
LightGBM's libomp conflicts with PyTorch's bundled OpenMP on macOS arm64
when both are loaded in the same process.  Running these tests first (before
any torch import) avoids the segfault.

pytest collects files alphabetically: test_gradient_boosting.py runs before
test_models.py (which imports torch), so these tests execute first in a
PyTorch-free environment.
"""

from __future__ import annotations

# Import LightGBM at module load time — before any torch import in this session
import tempfile

import numpy as np
import pytest


def _lgb_available() -> bool:
    try:
        import lightgbm  # noqa: F401
        return True
    except ImportError:
        return False


def _make_data(n: int = 300, n_features: int = 20) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    X = rng.standard_normal((n, n_features)).astype(np.float32)
    y = np.clip(rng.normal(0, 0.01, n), -0.1, 0.1).astype(np.float32)
    return X, y


@pytest.mark.skipif(not _lgb_available(), reason="LightGBM not installed")
class TestGradientBoostingModel:
    INPUT_DIM = 20

    def _make_model(self):
        from models.gradient_boosting import GradientBoostingModel
        return GradientBoostingModel(
            input_dim=self.INPUT_DIM,
            n_estimators=50,
            learning_rate=0.1,
        )

    def test_predict_before_train_raises(self):
        from models.gradient_boosting import GradientBoostingModel
        m = GradientBoostingModel(input_dim=self.INPUT_DIM)
        with pytest.raises(RuntimeError, match="not been trained"):
            m.predict(np.zeros(self.INPUT_DIM))

    def test_train_and_predict(self):
        from models.base import SignalOutput
        X, y = _make_data(300, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:240], y[:240], X_val=X[240:], y_val=y[240:])
        out = m.predict(X[-1:])
        assert isinstance(out, SignalOutput)
        assert -1.0 <= out.signal <= 1.0
        assert 0.0 <= out.confidence <= 1.0
        assert out.model_id == "gradient_boosting"

    def test_feature_importance_keys(self):
        X, y = _make_data(300, self.INPUT_DIM)
        feature_names = [f"feat_{i}" for i in range(self.INPUT_DIM)]
        from models.gradient_boosting import GradientBoostingModel
        m = GradientBoostingModel(
            input_dim=self.INPUT_DIM,
            feature_names=feature_names,
            n_estimators=50,
        )
        m.train(X[:240], y[:240])
        importance = m.feature_importance()
        assert len(importance) == self.INPUT_DIM
        assert all(k in importance for k in feature_names)

    def test_save_load_roundtrip(self):
        from models.gradient_boosting import GradientBoostingModel
        X, y = _make_data(300, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:240], y[:240])
        out1 = m.predict(X[-1:])

        with tempfile.TemporaryDirectory() as tmp:
            m.save(tmp)
            m2 = GradientBoostingModel.load(tmp)

        out2 = m2.predict(X[-1:])
        assert abs(out1.signal - out2.signal) < 1e-5

    def test_train_without_val(self):
        """Train without explicit X_val — should use internal 80/20 split."""
        X, y = _make_data(300, self.INPUT_DIM)
        m = self._make_model()
        m.train(X, y)  # no X_val — uses last 20% internally
        assert m.is_trained

    def test_signal_in_range(self):
        """Output signal must always be in [-1, +1]."""
        X, y = _make_data(300, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:240], y[:240])
        # Test many rows
        for i in range(0, 60, 10):
            out = m.predict(X[i : i + 1])
            assert -1.0 <= out.signal <= 1.0, f"signal={out.signal} at row {i}"
