"""
tests/models/test_models.py — Integration tests for all BaseSignalModel implementations.

These tests use synthetic data and do not require GPU, real market data,
or pre-trained model weights.  They verify:

1. Each model trains without crashing on synthetic feature data.
2. ``predict()`` returns a valid SignalOutput (signal ∈ [-1,+1], confidence ∈ [0,1]).
3. ``save()`` / ``load()`` round-trips preserve model_id and produce consistent predictions.
4. Calling ``predict()`` before ``train()`` raises RuntimeError.

ML dependencies (torch, gpytorch, lightgbm, gymnasium, stable-baselines3) are
imported lazily — each test is skipped if the required library is not installed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from models.base import SignalOutput

# ── Synthetic data helpers ────────────────────────────────────────────────────

def _make_data(
    n: int = 400,
    n_features: int = 20,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate synthetic OHLCV-like feature matrix and return labels."""
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, n_features)).astype(np.float32)
    # Target: noisy log returns clipped to [-0.1, 0.1]
    y = np.clip(rng.normal(0, 0.01, n), -0.1, 0.1).astype(np.float32)
    return X, y


def _assert_valid_signal(out: SignalOutput) -> None:
    assert isinstance(out, SignalOutput), f"Expected SignalOutput, got {type(out)}"
    assert -1.0 <= out.signal <= 1.0, f"signal={out.signal} out of range"
    assert 0.0 <= out.confidence <= 1.0, f"confidence={out.confidence} out of range"
    assert isinstance(out.model_id, str) and len(out.model_id) > 0


# ── LSTM Forecaster ───────────────────────────────────────────────────────────

def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _torch_available(), reason="PyTorch not installed")
class TestLSTMForecaster:
    INPUT_DIM = 20
    SEQ_LEN = 10

    def _make_model(self):
        from models.lstm_forecaster import LSTMForecaster
        return LSTMForecaster(
            input_dim=self.INPUT_DIM,
            seq_len=self.SEQ_LEN,
            hidden_dim=32,
            num_layers=1,
            dropout=0.0,
            max_epochs=3,
            batch_size=32,
        )

    def test_predict_before_train_raises(self):
        from models.lstm_forecaster import LSTMForecaster
        m = LSTMForecaster(input_dim=self.INPUT_DIM, seq_len=self.SEQ_LEN)
        with pytest.raises(RuntimeError, match="not been trained"):
            m.predict(np.zeros((self.SEQ_LEN, self.INPUT_DIM)))

    def test_train_and_predict(self):
        X, y = _make_data(200, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:160], y[:160], X_val=X[160:], y_val=y[160:])
        out = m.predict(X[-self.SEQ_LEN:])
        _assert_valid_signal(out)
        assert out.model_id == "lstm_forecaster"

    def test_predict_short_input_padded(self):
        X, y = _make_data(200, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:160], y[:160])
        # Pass fewer bars than seq_len — should pad
        out = m.predict(X[:3])
        _assert_valid_signal(out)

    def test_save_load_roundtrip(self):
        X, y = _make_data(200, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:160], y[:160])
        out1 = m.predict(X[-self.SEQ_LEN:])

        from models.lstm_forecaster import LSTMForecaster
        with tempfile.TemporaryDirectory() as tmp:
            m.save(tmp)
            m2 = LSTMForecaster.load(tmp)
        out2 = m2.predict(X[-self.SEQ_LEN:])

        assert m2.model_id == "lstm_forecaster"
        # Predictions should be identical after round-trip
        assert abs(out1.signal - out2.signal) < 1e-4


# ── Transformer Signal Model ──────────────────────────────────────────────────

@pytest.mark.skipif(not _torch_available(), reason="PyTorch not installed")
class TestTransformerSignalModel:
    INPUT_DIM = 20
    SEQ_LEN = 10

    def _make_model(self):
        from models.transformer_signal import TransformerSignalModel
        return TransformerSignalModel(
            input_dim=self.INPUT_DIM,
            seq_len=self.SEQ_LEN,
            d_model=16,
            nhead=4,
            num_layers=1,
            dropout=0.0,
            max_epochs=3,
            batch_size=32,
        )

    def test_predict_before_train_raises(self):
        from models.transformer_signal import TransformerSignalModel
        m = TransformerSignalModel(input_dim=self.INPUT_DIM, seq_len=self.SEQ_LEN, d_model=16, nhead=4)
        with pytest.raises(RuntimeError):
            m.predict(np.zeros((self.SEQ_LEN, self.INPUT_DIM)))

    def test_train_and_predict(self):
        X, y = _make_data(200, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:160], y[:160], X_val=X[160:], y_val=y[160:])
        out = m.predict(X[-self.SEQ_LEN:])
        _assert_valid_signal(out)
        assert out.model_id == "transformer_signal"

    def test_save_load_roundtrip(self):
        from models.transformer_signal import TransformerSignalModel
        X, y = _make_data(200, self.INPUT_DIM)
        m = self._make_model()
        m.train(X[:160], y[:160])
        out1 = m.predict(X[-self.SEQ_LEN:])

        with tempfile.TemporaryDirectory() as tmp:
            m.save(tmp)
            m2 = TransformerSignalModel.load(tmp)

        out2 = m2.predict(X[-self.SEQ_LEN:])
        assert abs(out1.signal - out2.signal) < 1e-4

    def test_invalid_nhead_raises(self):
        from models.transformer_signal import TransformerSignalModel
        with pytest.raises(ValueError, match="divisible"):
            TransformerSignalModel(input_dim=10, d_model=10, nhead=3)


# ── Gaussian Process Model ────────────────────────────────────────────────────

def _gpytorch_available() -> bool:
    try:
        import gpytorch  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _gpytorch_available(), reason="GPyTorch not installed")
class TestGaussianProcessModel:
    INPUT_DIM = 10

    def test_train_and_predict(self):
        from models.gaussian_process import GaussianProcessModel
        X, y = _make_data(150, self.INPUT_DIM)
        m = GaussianProcessModel(input_dim=self.INPUT_DIM, n_train_iters=10, max_train_samples=100)
        m.train(X[:100], y[:100])
        out = m.predict(X[-1])
        _assert_valid_signal(out)
        assert out.model_id == "gaussian_process"

    def test_variance_in_metadata(self):
        from models.gaussian_process import GaussianProcessModel
        X, y = _make_data(150, self.INPUT_DIM)
        m = GaussianProcessModel(input_dim=self.INPUT_DIM, n_train_iters=10, max_train_samples=100)
        m.train(X[:100], y[:100])
        out = m.predict(X[-1])
        assert "variance" in out.metadata
        assert out.metadata["variance"] >= 0.0

    def test_confidence_from_variance(self):
        """Higher variance should correspond to lower confidence."""
        from models.gaussian_process import GaussianProcessModel
        X, y = _make_data(200, self.INPUT_DIM)
        m = GaussianProcessModel(input_dim=self.INPUT_DIM, n_train_iters=20, max_train_samples=100)
        m.train(X[:100], y[:100])
        # Predict on training data (low variance) vs. far-OOD data (high variance)
        out_near = m.predict(X[0])  # in-distribution
        out_far = m.predict(np.full((1, self.INPUT_DIM), 100.0, dtype=np.float32))  # far OOD
        # OOD should have lower confidence (higher variance)
        assert out_far.confidence <= out_near.confidence + 0.1  # allow small tolerance

    def test_save_load_roundtrip(self):
        from models.gaussian_process import GaussianProcessModel
        X, y = _make_data(150, self.INPUT_DIM)
        m = GaussianProcessModel(input_dim=self.INPUT_DIM, n_train_iters=5, max_train_samples=100)
        m.train(X[:100], y[:100])
        out1 = m.predict(X[-1])

        with tempfile.TemporaryDirectory() as tmp:
            m.save(tmp)
            m2 = GaussianProcessModel.load(tmp)

        out2 = m2.predict(X[-1])
        assert abs(out1.signal - out2.signal) < 0.05  # GP can have small numerical diffs


# ── Ensemble Model ────────────────────────────────────────────────────────────

class TestEnsembleModel:
    """EnsembleModel only requires scikit-learn, so always runs."""

    MODEL_IDS = ["m1", "m2", "m3"]

    def _make_model(self):
        from models.ensemble import EnsembleModel
        return EnsembleModel(model_ids=self.MODEL_IDS, alpha=1.0)

    def test_train_and_predict(self):
        rng = np.random.default_rng(0)
        n = 200
        signals = rng.uniform(-1, 1, (n, 3)).astype(np.float32)
        targets = rng.normal(0, 0.01, n).astype(np.float32)
        confs = rng.uniform(0.5, 1.0, 3).astype(np.float32)

        m = self._make_model()
        m.train(signals, targets)
        out = m.predict(signals[-5:], confidences=confs)
        _assert_valid_signal(out)
        assert out.model_id == "ensemble"

    def test_predict_from_outputs(self):
        rng = np.random.default_rng(1)
        signals_train = rng.uniform(-1, 1, (100, 3)).astype(np.float32)
        targets_train = rng.normal(0, 0.01, 100).astype(np.float32)

        m = self._make_model()
        m.train(signals_train, targets_train)

        out = m.predict_from_outputs(
            signals=np.array([0.3, -0.1, 0.5]),
            confidences=np.array([0.8, 0.7, 0.9]),
        )
        _assert_valid_signal(out)
        # Metadata should include per-model breakdown
        assert "per_model" in out.metadata
        assert "m1" in out.metadata["per_model"]

    def test_wrong_column_count_raises(self):
        from models.ensemble import EnsembleModel
        m = EnsembleModel(model_ids=["a", "b", "c"])
        rng = np.random.default_rng(0)
        m.train(rng.uniform(-1, 1, (100, 3)), rng.normal(0, 0.01, 100))
        with pytest.raises(ValueError, match="Expected 3"):
            m.predict_from_outputs(
                signals=np.array([0.1, 0.2]),   # only 2, should be 3
                confidences=np.array([0.8, 0.7]),
            )

    def test_save_load_roundtrip(self):
        from models.ensemble import EnsembleModel
        rng = np.random.default_rng(2)
        signals_train = rng.uniform(-1, 1, (100, 3)).astype(np.float32)
        targets_train = rng.normal(0, 0.01, 100).astype(np.float32)

        m = self._make_model()
        m.train(signals_train, targets_train)
        out1 = m.predict_from_outputs(np.array([0.2, -0.3, 0.4]), np.array([0.7, 0.8, 0.6]))

        with tempfile.TemporaryDirectory() as tmp:
            m.save(tmp)
            m2 = EnsembleModel.load(tmp)

        out2 = m2.predict_from_outputs(np.array([0.2, -0.3, 0.4]), np.array([0.7, 0.8, 0.6]))
        assert abs(out1.signal - out2.signal) < 1e-5


# ── ModelRegistry ─────────────────────────────────────────────────────────────

class TestModelRegistry:
    def test_save_and_load_latest(self):
        from models.registry import ModelRegistry
        from tests.models.test_base import _DummyModel

        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(artifacts_dir=tmp)
            m = _DummyModel(input_dim=5)
            m.train(None, None)

            v = registry.save(m, metrics={"val_sharpe": 1.2})
            assert v == 1

            loaded = registry.load_latest("dummy", _DummyModel)
            assert loaded.model_id == "dummy"
            assert loaded.is_trained

    def test_version_increments(self):
        from models.registry import ModelRegistry
        from tests.models.test_base import _DummyModel

        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(artifacts_dir=tmp)
            m = _DummyModel(input_dim=5)
            m.train(None, None)

            v1 = registry.save(m)
            v2 = registry.save(m)
            assert v1 == 1
            assert v2 == 2

    def test_list_versions(self):
        from models.registry import ModelRegistry
        from tests.models.test_base import _DummyModel

        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(artifacts_dir=tmp)
            m = _DummyModel(input_dim=5)
            m.train(None, None)
            registry.save(m, metrics={"val_sharpe": 0.5})
            registry.save(m, metrics={"val_sharpe": 1.5})

            versions = registry.list_versions("dummy")
            assert len(versions) == 2

    def test_best_version(self):
        from models.registry import ModelRegistry
        from tests.models.test_base import _DummyModel

        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(artifacts_dir=tmp)
            m = _DummyModel(input_dim=5)
            m.train(None, None)
            registry.save(m, metrics={"val_sharpe": 0.5})
            registry.save(m, metrics={"val_sharpe": 1.5})

            best = registry.best_version("dummy", metric="val_sharpe", higher_is_better=True)
            assert best["metrics"]["val_sharpe"] == pytest.approx(1.5)

    def test_load_unknown_model_raises(self):
        from models.registry import ModelRegistry
        from tests.models.test_base import _DummyModel

        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(artifacts_dir=tmp)
            with pytest.raises(KeyError):
                registry.load_latest("nonexistent", _DummyModel)

    def test_tag_version(self):
        from models.registry import ModelRegistry
        from tests.models.test_base import _DummyModel

        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(artifacts_dir=tmp)
            m = _DummyModel(input_dim=5)
            m.train(None, None)
            registry.save(m)
            registry.tag_version("dummy", version=1, tag="production")
            versions = registry.list_versions("dummy")
            assert versions[0]["tag"] == "production"
