"""
tests/api/test_risk.py — Tests for risk management endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestRiskStatus:
    def test_status_returns_200(self, client: TestClient):
        resp = client.get("/api/risk/status")
        assert resp.status_code == 200

    def test_status_has_required_fields(self, client: TestClient):
        data = client.get("/api/risk/status").json()
        for key in (
            "halted", "halt_reason", "peak_equity",
            "current_drawdown_pct", "daily_loss_pct",
            "var_95", "var_99", "cvar_95", "cvar_99",
            "correlation_pairs",
        ):
            assert key in data, f"Missing key: {key}"

    def test_not_halted_initially(self, client: TestClient):
        data = client.get("/api/risk/status").json()
        assert data["halted"] is False

    def test_halted_state_reported(self, halted_client: TestClient):
        data = halted_client.get("/api/risk/status").json()
        assert data["halted"] is True
        assert data["halt_reason"] == "Test halt"

    def test_var_values_are_non_negative(self, client: TestClient):
        data = client.get("/api/risk/status").json()
        assert data["var_95"] >= 0.0
        assert data["var_99"] >= 0.0
        assert data["cvar_95"] >= 0.0
        assert data["cvar_99"] >= 0.0


class TestRiskResume:
    def test_resume_when_not_halted_returns_false(self, client: TestClient):
        resp = client.post("/api/risk/resume", json={"new_equity": None})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_resume_when_halted_succeeds(self, halted_client: TestClient):
        resp = halted_client.post("/api/risk/resume", json={"new_equity": 95000.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "cleared" in data["message"].lower()

    def test_resume_clears_halt_flag(self, halted_client: TestClient):
        halted_client.post("/api/risk/resume", json={"new_equity": None})
        status = halted_client.get("/api/risk/status").json()
        assert status["halted"] is False

    def test_resume_no_new_equity(self, halted_client: TestClient):
        resp = halted_client.post("/api/risk/resume", json={})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestRiskVar:
    def test_var_endpoint_returns_200(self, client: TestClient):
        resp = client.get("/api/risk/var")
        assert resp.status_code == 200

    def test_var_available_with_sufficient_history(self, client: TestClient):
        # fixture has 300 equity points — enough for VaR
        data = client.get("/api/risk/var").json()
        assert data["available"] is True

    def test_var_unavailable_without_history(self, state, client: TestClient):
        # Temporarily clear equity history
        original = list(state.equity_history)
        state.equity_history.clear()
        data = client.get("/api/risk/var").json()
        assert data["available"] is False
        state.equity_history.extend(original)


class TestRiskLimits:
    def test_limits_endpoint_returns_200(self, client: TestClient):
        resp = client.get("/api/risk/limits")
        assert resp.status_code == 200

    def test_limits_has_key_fields(self, client: TestClient):
        data = client.get("/api/risk/limits").json()
        assert "max_position_pct" in data
        assert "max_drawdown_pct" in data
        assert "enabled" in data

    def test_limits_default_values(self, client: TestClient):
        data = client.get("/api/risk/limits").json()
        assert data["max_position_pct"] == pytest.approx(0.10)
        assert data["max_drawdown_pct"] == pytest.approx(0.20)


class TestRiskAudit:
    def test_audit_returns_200(self, client: TestClient):
        resp = client.get("/api/risk/audit")
        assert resp.status_code == 200

    def test_audit_has_entries_key(self, client: TestClient):
        data = client.get("/api/risk/audit").json()
        assert "entries" in data
        assert "count" in data

    def test_audit_limit_param(self, client: TestClient):
        data = client.get("/api/risk/audit?limit=10").json()
        assert len(data["entries"]) <= 10
