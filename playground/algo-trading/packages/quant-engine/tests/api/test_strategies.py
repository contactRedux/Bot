"""
tests/api/test_strategies.py — Tests for strategy management endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestListStrategies:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient):
        data = client.get("/api/strategies").json()
        assert "strategies" in data
        assert "total" in data

    def test_two_strategies_in_stub(self, client: TestClient):
        data = client.get("/api/strategies").json()
        assert data["total"] == 2

    def test_strategy_item_has_fields(self, client: TestClient):
        data = client.get("/api/strategies").json()
        s = data["strategies"][0]
        for key in ("strategy_id", "display_name", "enabled", "allocation_weight", "tickers"):
            assert key in s

    def test_no_orchestrator_returns_empty(self, state, client: TestClient):
        original = state.orchestrator
        state.orchestrator = None
        data = client.get("/api/strategies").json()
        assert data["total"] == 0
        state.orchestrator = original


class TestGetStrategy:
    def test_known_strategy_returns_200(self, client: TestClient):
        resp = client.get("/api/strategies/momentum")
        assert resp.status_code == 200

    def test_unknown_strategy_returns_404(self, client: TestClient):
        resp = client.get("/api/strategies/nonexistent_strategy")
        assert resp.status_code == 404

    def test_strategy_details_correct(self, client: TestClient):
        data = client.get("/api/strategies/momentum").json()
        assert data["strategy_id"] == "momentum"
        assert data["enabled"] is True


class TestToggleStrategy:
    def test_disable_strategy(self, client: TestClient):
        resp = client.patch("/api/strategies/momentum", json={"enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["strategy_id"] == "momentum"

    def test_enable_strategy(self, client: TestClient):
        client.patch("/api/strategies/momentum", json={"enabled": False})
        resp = client.patch("/api/strategies/momentum", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_toggle_reflected_in_get(self, client: TestClient):
        client.patch("/api/strategies/mean_reversion", json={"enabled": False})
        data = client.get("/api/strategies/mean_reversion").json()
        assert data["enabled"] is False
        # Restore
        client.patch("/api/strategies/mean_reversion", json={"enabled": True})

    def test_toggle_unknown_strategy_returns_404(self, client: TestClient):
        resp = client.patch("/api/strategies/ghost_strategy", json={"enabled": True})
        assert resp.status_code == 404

    def test_toggle_requires_enabled_field(self, client: TestClient):
        resp = client.patch("/api/strategies/momentum", json={})
        assert resp.status_code == 422  # validation error


class TestStrategySignals:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/strategies/momentum/signals")
        assert resp.status_code == 200

    def test_empty_signals_initially(self, client: TestClient):
        data = client.get("/api/strategies/momentum/signals").json()
        assert data["signals"] == []
        assert data["count"] == 0

    def test_signals_filtered_by_strategy(self, state, client: TestClient):
        from datetime import datetime, timezone
        state.latest_signals.extend([
            {"ticker": "AAPL", "strategy_id": "momentum", "signal": 0.8,
             "confidence": 0.9, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"ticker": "MSFT", "strategy_id": "mean_reversion", "signal": -0.3,
             "confidence": 0.7, "timestamp": datetime.now(timezone.utc).isoformat()},
        ])
        data = client.get("/api/strategies/momentum/signals").json()
        assert data["count"] == 1
        assert data["signals"][0]["strategy_id"] == "momentum"
