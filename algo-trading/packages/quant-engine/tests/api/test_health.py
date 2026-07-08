"""
tests/api/test_health.py — Tests for the health and root endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_shape(self, client: TestClient):
        data = client.get("/health").json()
        for key in ("status", "version", "trading_mode", "broker_connected", "uptime_seconds", "timestamp"):
            assert key in data

    def test_health_status_ok_in_dev(self, client: TestClient):
        data = client.get("/health").json()
        # dev mode — broker is PaperBroker (always connected), so status should be ok
        assert data["status"] in ("ok", "degraded")

    def test_health_trading_mode(self, client: TestClient):
        data = client.get("/health").json()
        assert data["trading_mode"] == "dev"

    def test_health_uptime_positive(self, client: TestClient):
        data = client.get("/health").json()
        assert data["uptime_seconds"] >= 0.0


class TestRootEndpoint:
    def test_root_returns_200(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_root_has_docs_link(self, client: TestClient):
        data = client.get("/").json()
        assert "docs" in data
        assert data["docs"] == "/docs"

    def test_root_has_websocket_link(self, client: TestClient):
        data = client.get("/").json()
        assert "websocket" in data
        assert "/ws/feed" in data["websocket"]
