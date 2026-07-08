"""
tests/integration/test_portfolio_chart_flow.py — Integration tests for portfolio and chart flow.

These tests let the full lifespan run (module-scoped TestClient) and inject
data directly into the DataStore via write_bars so the price-history endpoint
returns real rows.

All tests use StaticPool on the in-memory DataStore so that writes and reads
share the same SQLite connection.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from api.deps import AppState
from api.main import app
from data.schemas import OHLCVBar
from data.store import DataStore
from execution.paper_broker import PaperBroker
from risk.limits import RiskLimits
from risk.manager import RiskManager
from risk.monitor import DrawdownMonitor

# ---------------------------------------------------------------------------
# Minimal stub orchestrator (mirrors tests/api/conftest.py)
# ---------------------------------------------------------------------------

class _StubStrategy:
    def __init__(self, sid: str) -> None:
        self.strategy_id = sid
        self._enabled = True
        self.allocation_weight = 0.1
        self.tickers = ["AAPL"]

    def generate_orders(self):
        return []


class _StubOrchestrator:
    def __init__(self) -> None:
        self._strategies = {
            "momentum": _StubStrategy("momentum"),
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def store_with_aapl():
    """In-memory DataStore pre-seeded with 10 AAPL daily bars (recent dates)."""
    store = DataStore(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Use recent dates so the price-history endpoint's lookback window covers them.
    now = datetime.now(UTC)
    bars = [
        OHLCVBar(
            ticker="AAPL",
            interval="1d",
            open=180.0 + i,
            high=183.0 + i,
            low=178.0 + i,
            close=181.0 + i,
            volume=float(5_000_000 + i * 100_000),
            # Spread bars over last 10 days, oldest first
            event_timestamp=now - timedelta(days=(9 - i)),
            fetch_timestamp=now,
            source="test",
        )
        for i in range(10)
    ]
    store.write_bars(bars)
    return store


@pytest.fixture(scope="module")
def chart_client(store_with_aapl: DataStore):
    """
    TestClient with a pre-populated DataStore injected before lifespan.

    We pre-set app.state.app_state before entering the TestClient context so
    the lifespan startup skips its own initialisation (the guard in main.py
    checks ``if not hasattr(app.state, "app_state")``).  This means the in-
    memory DataStore with the seeded AAPL bars is the one the endpoints see.
    """
    limits = RiskLimits(max_drawdown_pct=0.20, max_daily_loss_pct=0.02)
    monitor = DrawdownMonitor(limits=limits, initial_equity=100_000.0)
    risk_mgr = RiskManager(limits=limits, total_capital=100_000.0)
    risk_mgr.set_monitor(monitor)
    broker = PaperBroker(initial_cash=100_000.0)
    broker.update_prices({"AAPL": 185.0})

    state = AppState(
        broker=broker,
        monitor=monitor,
        risk_manager=risk_mgr,
        orchestrator=_StubOrchestrator(),
        data_store=store_with_aapl,
        equity_history=list(range(100_000, 100_050)),
        trading_mode="dev",
    )
    # Pre-inject state — the lifespan guard will skip its own init.
    app.state.app_state = state
    # Use raise_server_exceptions=False to avoid background task noise.
    client = TestClient(app, raise_server_exceptions=False)
    # Manually start (triggers lifespan startup which is a no-op here).
    client.__enter__()
    yield client
    client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Price-history tests
# ---------------------------------------------------------------------------

class TestPriceHistoryFlow:
    def test_price_history_returns_200(self, chart_client: TestClient):
        resp = chart_client.get("/api/portfolio/price-history?ticker=AAPL&interval=1d&limit=50")
        assert resp.status_code == 200

    def test_price_history_returns_exactly_10_points(self, chart_client: TestClient):
        data = chart_client.get(
            "/api/portfolio/price-history?ticker=AAPL&interval=1d&limit=50"
        ).json()
        assert data["count"] == 10
        assert len(data["points"]) == 10

    def test_price_history_points_have_required_fields(self, chart_client: TestClient):
        data = chart_client.get(
            "/api/portfolio/price-history?ticker=AAPL&interval=1d&limit=50"
        ).json()
        for pt in data["points"]:
            assert "time" in pt
            assert "close" in pt
            assert "open" in pt
            assert "high" in pt
            assert "low" in pt

    def test_price_history_limit_respected(self, chart_client: TestClient):
        data = chart_client.get(
            "/api/portfolio/price-history?ticker=AAPL&interval=1d&limit=5"
        ).json()
        assert data["count"] <= 5

    def test_price_history_empty_for_missing_ticker(self, chart_client: TestClient):
        data = chart_client.get(
            "/api/portfolio/price-history?ticker=ZZZZ&interval=1d&limit=50"
        ).json()
        assert data["count"] == 0
        assert data["points"] == []

    def test_price_history_ticker_and_interval_echoed(self, chart_client: TestClient):
        data = chart_client.get(
            "/api/portfolio/price-history?ticker=AAPL&interval=1d"
        ).json()
        assert data["ticker"] == "AAPL"
        assert data["interval"] == "1d"


# ---------------------------------------------------------------------------
# Portfolio endpoint tests
# ---------------------------------------------------------------------------

class TestPortfolioEndpointFlow:
    def test_portfolio_returns_200(self, chart_client: TestClient):
        resp = chart_client.get("/api/portfolio")
        assert resp.status_code == 200

    def test_portfolio_has_cash_and_positions(self, chart_client: TestClient):
        data = chart_client.get("/api/portfolio").json()
        assert "cash" in data
        assert "positions" in data

    def test_portfolio_history_returns_equity_list(self, chart_client: TestClient):
        data = chart_client.get("/api/portfolio/history").json()
        assert "equity_history" in data
        assert isinstance(data["equity_history"], list)

    def test_portfolio_trades_returns_trades_list(self, chart_client: TestClient):
        data = chart_client.get("/api/portfolio/trades").json()
        assert "trades" in data
        assert isinstance(data["trades"], list)


# ---------------------------------------------------------------------------
# Backtest round-trip test
# ---------------------------------------------------------------------------

class TestBacktestRoundTrip:
    def test_backtest_run_returns_202(self, chart_client: TestClient):
        payload = {
            "tickers": ["AAPL"],
            "strategies": ["momentum"],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
            "interval": "1d",
            "initial_capital": 100000.0,
        }
        resp = chart_client.post("/api/backtest/run", json=payload)
        assert resp.status_code == 202

    def test_backtest_run_returns_run_id(self, chart_client: TestClient):
        payload = {
            "tickers": ["AAPL"],
            "strategies": ["momentum"],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
            "interval": "1d",
            "initial_capital": 100000.0,
        }
        data = chart_client.post("/api/backtest/run", json=payload).json()
        assert "run_id" in data
        assert len(data["run_id"]) > 0

    def test_backtest_status_endpoint_returns_200(self, chart_client: TestClient):
        payload = {
            "tickers": ["AAPL"],
            "strategies": ["momentum"],
            "start_date": "2023-01-01",
            "end_date": "2023-03-01",
            "interval": "1d",
            "initial_capital": 100000.0,
        }
        run_data = chart_client.post("/api/backtest/run", json=payload).json()
        run_id = run_data["run_id"]

        # Poll status — background task should complete quickly for a 2-month window
        status_resp = None
        for _ in range(20):
            status_resp = chart_client.get(f"/api/backtest/{run_id}/status")
            assert status_resp.status_code == 200
            if status_resp.json()["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)

        assert status_resp is not None
        assert status_resp.json()["status"] in ("completed", "failed", "running")
