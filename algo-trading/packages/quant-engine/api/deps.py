"""
api/deps.py — FastAPI dependency injection providers.

FastAPI's ``Depends()`` system lets us inject shared state (broker, monitor,
orchestrator, etc.) into route functions without making them global variables.

AppState
--------
``AppState`` is a simple container attached to ``app.state`` during startup
(in ``api/main.py``'s lifespan context manager).  All route dependencies pull
from this single source of truth.

Why not globals?
----------------
Using ``app.state`` rather than module-level globals makes it easy to swap
out components in tests — just override ``app.state.monitor`` with a mock
before the test runs, and all routes that call ``get_monitor()`` will
receive the mock automatically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request


# ---------------------------------------------------------------------------
# AppState — attached to app.state at startup
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """
    Container for all shared application components.

    Populated by the lifespan context manager in ``api/main.py``.
    Routes access this via the ``get_*`` dependency functions below.
    """
    # Core components (set during lifespan startup)
    broker: Any = None             # ExecutionBroker
    monitor: Any = None            # DrawdownMonitor
    risk_manager: Any = None       # RiskManager
    orchestrator: Any = None       # StrategyOrchestrator
    portfolio: Any = None          # Portfolio (live state)

    # Backtest run cache: run_id → BacktestResponse dict
    backtest_results: dict[str, dict] = field(default_factory=dict)
    # In-progress run tracking: run_id → {"status", "progress_pct", "message"}
    backtest_status: dict[str, dict] = field(default_factory=dict)

    # Latest signals cache: strategy_id → list[SignalItem dict]
    latest_signals: list[dict] = field(default_factory=list)

    # Latest equity curve snapshot for VaR computation
    equity_history: list[float] = field(default_factory=list)

    # Start time for uptime calculation
    started_at: float = field(default_factory=time.time)

    # App version (injected from pyproject or env)
    version: str = "0.1.0"

    # Trading mode string (dev / paper / live)
    trading_mode: str = "dev"


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------

def get_app_state(request: Request) -> AppState:
    """Return the AppState attached to the running FastAPI app."""
    return request.app.state.app_state  # type: ignore[attr-defined]


def get_monitor(request: Request) -> Any:
    """
    Return the DrawdownMonitor.

    Raises 503 if the monitor was not initialised (startup failed or
    called before lifespan startup completed).
    """
    state: AppState = request.app.state.app_state
    if state.monitor is None:
        raise HTTPException(
            status_code=503,
            detail="DrawdownMonitor not initialised. Is the server still starting up?",
        )
    return state.monitor


def get_risk_manager(request: Request) -> Any:
    """Return the RiskManager, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.risk_manager is None:
        raise HTTPException(
            status_code=503,
            detail="RiskManager not initialised.",
        )
    return state.risk_manager


def get_broker(request: Request) -> Any:
    """Return the ExecutionBroker, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.broker is None:
        raise HTTPException(
            status_code=503,
            detail="ExecutionBroker not initialised.",
        )
    return state.broker


def get_orchestrator(request: Request) -> Any:
    """Return the StrategyOrchestrator, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="StrategyOrchestrator not initialised.",
        )
    return state.orchestrator


def get_portfolio(request: Request) -> Any:
    """Return the live Portfolio, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.portfolio is None:
        raise HTTPException(
            status_code=503,
            detail="Portfolio not initialised.",
        )
    return state.portfolio
