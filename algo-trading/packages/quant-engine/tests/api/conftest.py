"""
tests/api/conftest.py — Shared fixtures for FastAPI test suite.

We use FastAPI's TestClient (synchronous HTTPX) so tests run without an event loop
and without requiring a real server.  AppState is patched with controlled stubs.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.deps import AppState
from api.main import app
from data.store import DataStore
from execution.paper_broker import PaperBroker
from risk.limits import RiskLimits
from risk.manager import RiskManager
from risk.monitor import DrawdownMonitor

# ---------------------------------------------------------------------------
# Minimal stub orchestrator
# ---------------------------------------------------------------------------

class _StubStrategy:
    def __init__(self, sid: str) -> None:
        self.strategy_id = sid
        self._enabled = True
        self.allocation_weight = 0.1
        self.tickers = ["AAPL", "MSFT"]

    def generate_orders(self):
        return []


class _StubOrchestrator:
    def __init__(self) -> None:
        self._strategies = {
            "momentum": _StubStrategy("momentum"),
            "mean_reversion": _StubStrategy("mean_reversion"),
        }


# ---------------------------------------------------------------------------
# AppState factory
# ---------------------------------------------------------------------------

def _make_state(
    *,
    halted: bool = False,
    equity_history: list[float] | None = None,
) -> AppState:
    limits = RiskLimits(max_drawdown_pct=0.20, max_daily_loss_pct=0.02)
    monitor = DrawdownMonitor(limits=limits, initial_equity=100_000.0)
    risk_mgr = RiskManager(limits=limits, total_capital=100_000.0)
    risk_mgr.set_monitor(monitor)

    broker = PaperBroker(initial_cash=100_000.0)
    broker.update_prices({"AAPL": 150.0, "MSFT": 300.0})

    # Use StaticPool so all sessions share the same in-memory SQLite connection.
    # Without this, each session/connection gets its own blank database.
    from sqlalchemy.pool import StaticPool
    store = DataStore(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    state = AppState(
        broker=broker,
        monitor=monitor,
        risk_manager=risk_mgr,
        orchestrator=_StubOrchestrator(),
        data_store=store,
        equity_history=equity_history or list(range(100_000, 100_300)),
        trading_mode="dev",
    )

    if halted:
        monitor._halted = True
        monitor._halt_reason = "Test halt"

    return state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state() -> AppState:
    """Clean AppState for each test."""
    return _make_state()


@pytest.fixture
def halted_state() -> AppState:
    """AppState with the monitor in a halted condition."""
    return _make_state(halted=True)


@pytest.fixture
def client(state: AppState) -> TestClient:
    """TestClient with clean AppState."""
    app.state.app_state = state
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def halted_client(halted_state: AppState) -> TestClient:
    """TestClient with halted AppState."""
    app.state.app_state = halted_state
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
