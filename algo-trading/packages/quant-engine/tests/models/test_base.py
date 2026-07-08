"""
tests/models/test_base.py — Unit tests for SignalOutput and BaseSignalModel.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from models.base import BaseSignalModel, SignalOutput


# ---------------------------------------------------------------------------
# SignalOutput tests
# ---------------------------------------------------------------------------

class TestSignalOutput:
    def test_signal_clamped_above(self):
        out = SignalOutput(signal=2.5, confidence=0.5, model_id="test")
        assert out.signal == pytest.approx(1.0)

    def test_signal_clamped_below(self):
        out = SignalOutput(signal=-3.0, confidence=0.5, model_id="test")
        assert out.signal == pytest.approx(-1.0)

    def test_confidence_clamped_above(self):
        out = SignalOutput(signal=0.3, confidence=1.5, model_id="test")
        assert out.confidence == pytest.approx(1.0)

    def test_confidence_clamped_below(self):
        out = SignalOutput(signal=0.3, confidence=-0.1, model_id="test")
        assert out.confidence == pytest.approx(0.0)

    def test_direction_long(self):
        out = SignalOutput(signal=0.5, confidence=0.8, model_id="test")
        assert out.direction == "long"

    def test_direction_short(self):
        out = SignalOutput(signal=-0.5, confidence=0.8, model_id="test")
        assert out.direction == "short"

    def test_direction_neutral(self):
        out = SignalOutput(signal=0.02, confidence=0.3, model_id="test")
        assert out.direction == "neutral"

    def test_to_dict_keys(self):
        out = SignalOutput(signal=0.3, confidence=0.7, model_id="m1")
        d = out.to_dict()
        assert "signal" in d
        assert "confidence" in d
        assert "model_id" in d
        assert "timestamp" in d
        assert "direction" in d

    def test_timestamp_defaults_to_utc(self):
        out = SignalOutput(signal=0.0, confidence=0.5, model_id="m")
        assert out.timestamp.tzinfo is not None

    def test_metadata_stored(self):
        out = SignalOutput(signal=0.1, confidence=0.9, model_id="m", metadata={"shap": 0.5})
        assert out.metadata["shap"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# BaseSignalModel tests
# ---------------------------------------------------------------------------

class _DummyModel(BaseSignalModel):
    """Minimal concrete implementation for testing the abstract class."""

    @property
    def model_id(self) -> str:
        return "dummy"

    def train(self, X, y, **kwargs):
        self._is_trained = True

    def predict(self, X, **kwargs) -> SignalOutput:
        self._assert_trained()
        return SignalOutput(signal=0.5, confidence=0.8, model_id=self.model_id)

    def save(self, path):
        pass

    @classmethod
    def load(cls, path) -> "_DummyModel":
        m = cls(input_dim=5)
        m._is_trained = True
        return m


class TestBaseSignalModel:
    def test_not_trained_raises(self):
        m = _DummyModel(input_dim=5)
        with pytest.raises(RuntimeError, match="not been trained"):
            m.predict(np.zeros(5))

    def test_is_trained_after_train(self):
        m = _DummyModel(input_dim=5)
        assert not m.is_trained
        m.train(np.zeros((10, 5)), np.zeros(10))
        assert m.is_trained

    def test_predict_returns_signal_output(self):
        m = _DummyModel(input_dim=5)
        m.train(None, None)
        out = m.predict(np.zeros(5))
        assert isinstance(out, SignalOutput)
        assert -1.0 <= out.signal <= 1.0
        assert 0.0 <= out.confidence <= 1.0
