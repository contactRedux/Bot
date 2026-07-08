"""
tests/integration/test_api_app_state.py — Integration tests using the real lifespan.

These tests use TestClient(app) as a full context manager so the lifespan runs
completely (DataStore init, broker init, etc.).  They do NOT pre-set
app.state.app_state — they let the lifespan do it.

Note: these tests create a real SQLite file (algo_trading.db) in the
packages/quant-engine/ directory which is .gitignored.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="module")
def integration_client():
    """TestClient that lets the full lifespan run (no pre-injected AppState)."""
    # Ensure no leftover state from unit-test suite
    if hasattr(app.state, "app_state"):
        del app.state.app_state
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


class TestHealthEndpoint:
    def test_health_returns_200(self, integration_client: TestClient):
        resp = integration_client.get("/health")
        assert resp.status_code == 200

    def test_health_has_required_fields(self, integration_client: TestClient):
        data = integration_client.get("/health").json()
        assert "status" in data
        assert "trading_mode" in data
        assert "broker_connected" in data

    def test_health_status_is_ok_or_degraded(self, integration_client: TestClient):
        data = integration_client.get("/health").json()
        assert data["status"] in ("ok", "degraded")

    def test_health_version_present(self, integration_client: TestClient):
        data = integration_client.get("/health").json()
        assert "version" in data
        assert data["version"] == "0.1.0"


class TestRootEndpoint:
    def test_root_returns_200(self, integration_client: TestClient):
        resp = integration_client.get("/")
        assert resp.status_code == 200

    def test_root_has_name(self, integration_client: TestClient):
        data = integration_client.get("/").json()
        assert data["name"] == "quant-engine API"

    def test_root_has_docs_links(self, integration_client: TestClient):
        data = integration_client.get("/").json()
        assert "docs" in data
        assert "health" in data
        assert "websocket" in data


class TestStrategiesEndpoint:
    def test_strategies_returns_200(self, integration_client: TestClient):
        resp = integration_client.get("/api/strategies")
        assert resp.status_code == 200

    def test_strategies_response_has_list(self, integration_client: TestClient):
        """Strategy list is present (may be empty when no strategy_config.yaml is found)."""
        data = integration_client.get("/api/strategies").json()
        assert "strategies" in data
        assert isinstance(data["strategies"], list)

    def test_strategies_total_field_present(self, integration_client: TestClient):
        data = integration_client.get("/api/strategies").json()
        assert "total" in data
        assert data["total"] == len(data["strategies"])


class TestRiskStatus:
    def test_risk_status_returns_200(self, integration_client: TestClient):
        resp = integration_client.get("/api/risk/status")
        assert resp.status_code == 200

    def test_risk_status_shape(self, integration_client: TestClient):
        data = integration_client.get("/api/risk/status").json()
        assert "halted" in data
        assert "var_95" in data
        assert "current_drawdown_pct" in data


class TestPortfolioEndpoint:
    def test_portfolio_returns_200(self, integration_client: TestClient):
        resp = integration_client.get("/api/portfolio")
        assert resp.status_code == 200

    def test_portfolio_has_cash_and_positions(self, integration_client: TestClient):
        data = integration_client.get("/api/portfolio").json()
        assert "cash" in data
        assert "positions" in data


class TestWebSocketHeartbeat:
    def test_websocket_delivers_heartbeat(self, integration_client: TestClient):
        """Connect to /ws/feed and verify the first message is a heartbeat."""
        with integration_client.websocket_connect("/ws/feed") as ws:
            raw = ws.receive_text()
            msg = json.loads(raw)
            assert msg["event_type"] == "heartbeat"
