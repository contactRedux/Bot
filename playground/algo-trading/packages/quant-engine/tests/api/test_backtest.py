"""
tests/api/test_backtest.py — Tests for backtest endpoints.

These tests use the TestClient with a pre-populated AppState to avoid
requiring a real database, yfinance, or a full BacktestEngine run.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_result(run_id: str) -> dict:
    """A minimal BacktestResponse-shaped dict for pre-loading the cache."""
    return {
        "run_id": run_id,
        "status": "completed",
        "metrics": {
            "total_return_pct": 12.5,
            "cagr_pct": 11.0,
            "sharpe_ratio": 1.4,
            "sortino_ratio": 1.8,
            "calmar_ratio": 0.9,
            "max_drawdown_pct": -8.3,
            "annual_volatility_pct": 15.2,
            "n_trades": 42,
            "win_rate_pct": 55.0,
            "profit_factor": 1.6,
            "avg_trade_pnl": 280.0,
            "final_equity": 112_500.0,
            "start_date": "2023-01-01",
            "end_date": "2024-01-01",
        },
        "equity_curve": [
            {"timestamp": "2023-01-01T00:00:00", "equity": 100_000.0},
            {"timestamp": "2024-01-01T00:00:00", "equity": 112_500.0},
        ],
        "trade_log": [],
        "strategy_attribution": {"momentum": 8_000.0, "mean_reversion": 4_500.0},
        "tickers": ["AAPL", "MSFT"],
        "initial_capital": 100_000.0,
        "bar_interval": "1d",
        "halted": False,
        "halt_reason": "",
        "created_at": "2024-01-01T12:00:00",
        "error": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBacktestRun:
    def test_run_returns_202(self, client: TestClient):
        resp = client.post("/api/backtest/run", json={
            "tickers": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
            "strategies": ["all"],
            "initial_capital": 100_000.0,
        })
        assert resp.status_code == 202

    def test_run_returns_run_id(self, client: TestClient):
        resp = client.post("/api/backtest/run", json={
            "tickers": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
        })
        data = resp.json()
        assert "run_id" in data
        assert len(data["run_id"]) > 0

    def test_run_invalid_body_returns_422(self, client: TestClient):
        # Missing required fields (tickers, start_date, end_date)
        resp = client.post("/api/backtest/run", json={})
        assert resp.status_code == 422

    def test_run_empty_tickers_returns_422(self, client: TestClient):
        resp = client.post("/api/backtest/run", json={
            "tickers": [],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
        })
        assert resp.status_code == 422

    def test_run_negative_capital_returns_422(self, client: TestClient):
        resp = client.post("/api/backtest/run", json={
            "tickers": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
            "initial_capital": -1000.0,
        })
        assert resp.status_code == 422


class TestBacktestStatus:
    def test_known_run_id_returns_status(self, state, client: TestClient):
        run_id = "test001"
        state.backtest_status[run_id] = {
            "status": "running",
            "progress_pct": 25.0,
            "message": "In progress",
        }
        data = client.get(f"/api/backtest/{run_id}/status").json()
        assert data["run_id"] == run_id
        assert data["status"] == "running"
        assert data["progress_pct"] == 25.0

    def test_unknown_run_id_returns_not_found(self, client: TestClient):
        data = client.get("/api/backtest/nonexistent123/status").json()
        assert data["status"] == "not_found"

    def test_completed_run_status(self, state, client: TestClient):
        run_id = "done001"
        state.backtest_status[run_id] = {
            "status": "completed",
            "progress_pct": 100.0,
            "message": "Done",
        }
        data = client.get(f"/api/backtest/{run_id}/status").json()
        assert data["status"] == "completed"
        assert data["progress_pct"] == 100.0


class TestBacktestGet:
    def test_get_completed_result(self, state, client: TestClient):
        run_id = "abc123"
        state.backtest_results[run_id] = _fake_result(run_id)
        resp = client.get(f"/api/backtest/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run_id
        assert data["status"] == "completed"

    def test_get_metrics_present(self, state, client: TestClient):
        run_id = "xyz999"
        state.backtest_results[run_id] = _fake_result(run_id)
        data = client.get(f"/api/backtest/{run_id}").json()
        m = data["metrics"]
        assert m["sharpe_ratio"] == pytest.approx(1.4)
        assert m["n_trades"] == 42

    def test_get_unknown_id_returns_404(self, client: TestClient):
        resp = client.get("/api/backtest/doesnotexist")
        assert resp.status_code == 404

    def test_get_running_returns_202(self, state, client: TestClient):
        run_id = "running001"
        state.backtest_status[run_id] = {
            "status": "running",
            "progress_pct": 50.0,
            "message": "…",
        }
        resp = client.get(f"/api/backtest/{run_id}")
        assert resp.status_code == 202

    def test_equity_curve_shape(self, state, client: TestClient):
        run_id = "curve001"
        state.backtest_results[run_id] = _fake_result(run_id)
        data = client.get(f"/api/backtest/{run_id}").json()
        ec = data["equity_curve"]
        assert len(ec) == 2
        assert "timestamp" in ec[0]
        assert "equity" in ec[0]

    def test_strategy_attribution(self, state, client: TestClient):
        run_id = "attr001"
        state.backtest_results[run_id] = _fake_result(run_id)
        data = client.get(f"/api/backtest/{run_id}").json()
        attr = data["strategy_attribution"]
        assert "momentum" in attr
        assert attr["momentum"] == pytest.approx(8_000.0)


class TestBacktestList:
    def test_list_returns_200(self, client: TestClient):
        resp = client.get("/api/backtest/list")
        assert resp.status_code == 200

    def test_list_empty_initially(self, client: TestClient):
        data = client.get("/api/backtest/list").json()
        assert "runs" in data
        assert data["count"] == 0

    def test_list_contains_added_run(self, state, client: TestClient):
        run_id = "list001"
        state.backtest_results[run_id] = _fake_result(run_id)
        data = client.get("/api/backtest/list").json()
        assert data["count"] == 1
        assert data["runs"][0]["run_id"] == run_id

    def test_list_multiple_runs(self, state, client: TestClient):
        for i in range(3):
            rid = f"multi{i:03d}"
            state.backtest_results[rid] = _fake_result(rid)
        data = client.get("/api/backtest/list").json()
        assert data["count"] == 3


class TestBacktestDelete:
    def test_delete_existing_run(self, state, client: TestClient):
        run_id = "del001"
        state.backtest_results[run_id] = _fake_result(run_id)
        resp = client.delete(f"/api/backtest/{run_id}")
        assert resp.status_code == 204

    def test_deleted_run_not_in_list(self, state, client: TestClient):
        run_id = "del002"
        state.backtest_results[run_id] = _fake_result(run_id)
        client.delete(f"/api/backtest/{run_id}")
        data = client.get("/api/backtest/list").json()
        ids = [r["run_id"] for r in data["runs"]]
        assert run_id not in ids

    def test_delete_nonexistent_is_graceful(self, client: TestClient):
        resp = client.delete("/api/backtest/ghostrun")
        assert resp.status_code == 204  # idempotent
