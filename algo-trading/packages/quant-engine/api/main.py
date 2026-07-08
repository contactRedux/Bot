"""
api/main.py — FastAPI application entry point.

Application structure
---------------------
The FastAPI app is created here and all routers are mounted on it.

Startup / shutdown
------------------
The ``lifespan`` async context manager (FastAPI 0.93+ style) handles:

1. **Startup** — initialise all shared components (broker, monitor, risk
   manager, orchestrator, portfolio) and attach them to ``app.state``.
2. **Shutdown** — graceful cleanup (flush pending fills, close DB connections).

CORS
----
All origins are allowed in dev mode so the Vite dashboard on port 5173
can reach the API on port 8000.  In production, restrict origins to your
deployed dashboard URL.

OpenAPI docs
------------
Interactive docs are available at:
    http://localhost:8000/docs    (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
    http://localhost:8000/openapi.json

Starting the server
-------------------
::

    uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

or via the Makefile:

    make api
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.deps import AppState
from api.routes.backtest import router as backtest_router
from api.routes.portfolio import router as portfolio_router
from api.routes.risk import router as risk_router
from api.routes.signals import router as signals_router
from api.routes.strategies import router as strategies_router
from api.schemas import HealthResponse
from api.ws.feed import router as ws_router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup and shutdown lifecycle for the FastAPI app.

    This is where shared state (broker, monitor, risk manager, orchestrator)
    is initialised and attached to ``app.state.app_state``.

    Components are initialised with safe defaults — if the settings are
    insufficient for a full live setup (e.g. missing API keys in dev mode),
    the server starts anyway with reduced functionality rather than crashing.
    """
    logger.info("quant-engine API starting up…")

    state = AppState()

    # ── Settings ─────────────────────────────────────────────────────────────
    try:
        from config.settings import settings as app_settings
        state.trading_mode = str(app_settings.trading_mode.value
                                 if hasattr(app_settings.trading_mode, "value")
                                 else app_settings.trading_mode)
        state.version = "0.1.0"
    except Exception as exc:
        logger.warning("Could not load settings: %s", exc)

    # ── Risk limits + monitor ─────────────────────────────────────────────────
    try:
        from risk.limits import RiskLimits
        from risk.monitor import DrawdownMonitor
        limits = RiskLimits()
        state.monitor = DrawdownMonitor(limits=limits, initial_equity=100_000.0)
        logger.info("DrawdownMonitor initialised")
    except Exception as exc:
        logger.warning("DrawdownMonitor init failed: %s", exc)

    # ── Risk manager ──────────────────────────────────────────────────────────
    try:
        from risk.manager import RiskManager
        state.risk_manager = RiskManager(limits=limits, total_capital=100_000.0)
        if state.monitor is not None:
            state.risk_manager.set_monitor(state.monitor)
        logger.info("RiskManager initialised")
    except Exception as exc:
        logger.warning("RiskManager init failed: %s", exc)

    # ── Broker (PaperBroker by default in dev) ────────────────────────────────
    try:
        from execution.factory import BrokerFactory
        state.broker = BrokerFactory.create(app_settings, initial_cash=100_000.0)
        logger.info("Broker initialised (%s mode)", state.trading_mode)
    except Exception as exc:
        logger.warning("Broker init failed: %s", exc)

    # ── StrategyOrchestrator ──────────────────────────────────────────────────
    try:
        from pathlib import Path

        import yaml

        from config.settings import settings as app_settings
        from strategies.orchestrator import StrategyOrchestrator
        config_path = Path(app_settings.strategy_config_path)
        strategy_configs: dict = {}
        if config_path.exists():
            with open(config_path) as f:
                strategy_configs = yaml.safe_load(f) or {}
        state.orchestrator = StrategyOrchestrator(
            strategies=[],
            config=strategy_configs,
        )
        logger.info("StrategyOrchestrator initialised")
    except Exception as exc:
        logger.warning("StrategyOrchestrator init failed: %s", exc)

    # ── DataStore ─────────────────────────────────────────────────────────────
    try:
        from data.store import DataStore
        db_url = getattr(app_settings, "database_url", "sqlite:///./algo_trading.db")
        state.data_store = DataStore(db_url)
        logger.info("DataStore initialised (url=%s)", db_url.split("@")[-1])
    except Exception as exc:
        logger.warning("DataStore init failed: %s", exc)

    # ── Attach state to app ───────────────────────────────────────────────────
    # Only set app_state if it hasn't been pre-loaded by tests
    if not hasattr(app.state, "app_state"):
        app.state.app_state = state
    logger.info("quant-engine API ready on startup (mode=%s)", state.trading_mode)

    yield  # ── server is running ─────────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    # Clear app_state so the next TestClient invocation starts fresh
    if hasattr(app.state, "app_state"):
        del app.state.app_state
    logger.info("quant-engine API shutting down…")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="quant-engine API",
    description=(
        "REST + WebSocket API for the algorithmic trading platform.\n\n"
        "**Modes:** `dev` (no live connections) | `paper` (live data, simulated fills) | "
        "`live` (real execution)\n\n"
        "All endpoints are documented below.  The WebSocket feed is at `/ws/feed`."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# In production, replace "*" with your deployed dashboard domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(backtest_router)
app.include_router(portfolio_router)
app.include_router(risk_router)
app.include_router(signals_router)
app.include_router(strategies_router)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Meta endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health(request: Request) -> HealthResponse:
    """
    Health check endpoint.

    Returns ``200 OK`` when the server is running.
    Returns ``503`` only if the server failed to start (which wouldn't reach here).
    """
    state: AppState = request.app.state.app_state  # type: ignore[attr-defined]
    broker_ok = False
    if state.broker is not None:
        try:
            broker_ok = state.broker.is_connected
        except Exception:
            broker_ok = False

    _status: Literal["ok", "degraded"] = (
        "ok" if broker_ok or state.trading_mode == "dev" else "degraded"
    )
    return HealthResponse(
        status=_status,
        version=state.version,
        trading_mode=state.trading_mode,
        broker_connected=broker_ok,
        uptime_seconds=round(time.time() - state.started_at, 1),
        timestamp=datetime.now(UTC).isoformat(),
    )


@app.get("/", tags=["meta"])
async def root() -> dict:
    """Root endpoint — returns API info and links to docs."""
    return {
        "name": "quant-engine API",
        "version": "0.1.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
        "websocket": "/ws/feed",
    }
