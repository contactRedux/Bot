"""
tests/api/test_signals.py — Tests for signals endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _add_signals(state, n: int = 5) -> None:
    """Populate state.latest_signals with synthetic signal dicts."""
    from datetime import datetime, timezone
    for i in range(n):
        state.latest_signals.append({
            "ticker": "AAPL" if i % 2 == 0 else "MSFT",
            "strategy_id": "momentum" if i % 3 == 0 else "mean_reversion",
            "signal": round(0.5 + i * 0.05, 2),
            "confidence": round(0.6 + i * 0.02, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


class TestSignalsEndpoint:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/signals")
        assert resp.status_code == 200

    def test_empty_signals(self, client: TestClient):
        data = client.get("/api/signals").json()
        assert data["count"] == 0
        assert data["signals"] == []

    def test_populated_signals(self, state, client: TestClient):
        _add_signals(state, 5)
        data = client.get("/api/signals").json()
        assert data["count"] == 5
        assert len(data["signals"]) == 5

    def test_signal_item_has_required_fields(self, state, client: TestClient):
        _add_signals(state, 1)
        data = client.get("/api/signals").json()
        signal = data["signals"][0]
        for key in ("ticker", "strategy_id", "signal", "confidence", "timestamp"):
            assert key in signal


class TestSignalHistory:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/signals/history")
        assert resp.status_code == 200

    def test_limit_filters_results(self, state, client: TestClient):
        _add_signals(state, 20)
        data = client.get("/api/signals/history?limit=5").json()
        assert data["count"] <= 5

    def test_ticker_filter(self, state, client: TestClient):
        _add_signals(state, 10)
        data = client.get("/api/signals/history?ticker=AAPL").json()
        for sig in data["signals"]:
            assert sig["ticker"] == "AAPL"

    def test_strategy_filter(self, state, client: TestClient):
        _add_signals(state, 10)
        data = client.get("/api/signals/history?strategy_id=momentum").json()
        for sig in data["signals"]:
            assert sig["strategy_id"] == "momentum"


class TestSignalsClear:
    def test_clear_returns_204(self, state, client: TestClient):
        _add_signals(state, 5)
        resp = client.post("/api/signals/clear")
        assert resp.status_code == 204

    def test_signals_empty_after_clear(self, state, client: TestClient):
        _add_signals(state, 5)
        client.post("/api/signals/clear")
        data = client.get("/api/signals").json()
        assert data["count"] == 0
