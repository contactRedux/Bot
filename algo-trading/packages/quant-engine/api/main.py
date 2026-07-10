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
from api.routes.ai_analyst import router as ai_analyst_router
from api.routes.analysis import router as analysis_router
from api.routes.backtest import router as backtest_router
from api.routes.news import router as news_router
from api.routes.optimize import router as optimize_router
from api.routes.portfolio import router as portfolio_router
from api.routes.risk import router as risk_router
from api.routes.signals import router as signals_router
from api.routes.strategies import router as strategies_router
from api.routes.trading import router as trading_router
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
    strategy_configs: dict = {}
    try:
        from pathlib import Path

        import yaml

        from config.settings import settings as app_settings
        from strategies.orchestrator import StrategyOrchestrator
        config_path = Path(app_settings.strategy_config_path)
        if config_path.exists():
            with open(config_path) as f:
                strategy_configs = yaml.safe_load(f) or {}

        # Build strategy instances from strategy_config.yaml
        _strategies: list = []
        _macro_strategy = None
        try:
            from strategies.kalman_trend import KalmanTrendStrategy
            from strategies.kelly_vol import KellyVolStrategy
            from strategies.macro_factor import MacroFactorStrategy
            from strategies.market_making import MarketMakingStrategy
            from strategies.mean_reversion import MeanReversionStrategy
            from strategies.momentum import MomentumStrategy
            from strategies.sentiment import SentimentStrategy
            from strategies.stat_arb import StatArbStrategy
            from strategies.vwap_reversion import VWAPReversionStrategy

            # StatArbStrategy takes pairs= not tickers=; handle it separately.
            stat_arb_cfg = strategy_configs.get("stat_arb", {})
            stat_arb_pairs_raw = stat_arb_cfg.get("default_pairs", [])
            stat_arb_pairs = [tuple(p) for p in stat_arb_pairs_raw if len(p) == 2]

            non_stat_arb_map = {
                "momentum":       (MomentumStrategy,      strategy_configs.get("momentum", {}),
                                   strategy_configs.get("momentum", {}).get("default_tickers", [])),
                "mean_reversion": (MeanReversionStrategy, strategy_configs.get("mean_reversion", {}),
                                   strategy_configs.get("mean_reversion", {}).get("default_tickers", [])),
                "market_making":  (MarketMakingStrategy,  strategy_configs.get("market_making", {}),
                                   strategy_configs.get("market_making", {}).get("default_tickers", [])),
                "sentiment":      (SentimentStrategy,     strategy_configs.get("sentiment", {}),
                                   strategy_configs.get("sentiment", {}).get("default_tickers", [])),
                "kelly_vol":      (KellyVolStrategy,      strategy_configs.get("kelly_vol", {}),
                                   strategy_configs.get("kelly_vol", {}).get("default_tickers", [])),
                "kalman_trend":   (KalmanTrendStrategy,   strategy_configs.get("kalman_trend", {}),
                                   strategy_configs.get("kalman_trend", {}).get("default_tickers", [])),
                "vwap_reversion": (VWAPReversionStrategy, strategy_configs.get("vwap_reversion", {}),
                                   strategy_configs.get("vwap_reversion", {}).get("default_tickers", [])),
            }
            macro_cfg = strategy_configs.get("macro_factor", {})
            if macro_cfg.get("enabled", True):
                macro_tickers = list({
                    t for cfg in strategy_configs.values()
                    if isinstance(cfg, dict)
                    for t in cfg.get("default_tickers", [])
                })
                _macro_strategy = MacroFactorStrategy(
                    config=macro_cfg, tickers=macro_tickers or ["SPY"]
                )

            for sid, (cls, cfg, tickers) in non_stat_arb_map.items():
                if cfg.get("enabled", True) and tickers:
                    try:
                        _strategies.append(cls(config=cfg, tickers=list(set(tickers))))
                        logger.info("Strategy loaded: %s (%d tickers)", sid, len(set(tickers)))
                    except Exception as sinit_exc:
                        logger.warning("Strategy %s failed to init: %s", sid, sinit_exc)

            # StatArbStrategy — requires pairs= keyword
            if stat_arb_cfg.get("enabled", True) and stat_arb_pairs:
                try:
                    _strategies.append(StatArbStrategy(config=stat_arb_cfg, pairs=stat_arb_pairs))
                    logger.info("Strategy loaded: stat_arb (%d pairs)", len(stat_arb_pairs))
                except Exception as sinit_exc:
                    logger.warning("Strategy stat_arb failed to init: %s", sinit_exc)
        except Exception as load_exc:
            logger.warning("Strategy loading failed: %s", load_exc)

        state.orchestrator = StrategyOrchestrator(
            strategies=_strategies,
            macro_strategy=_macro_strategy,
            config=strategy_configs.get("portfolio", {}),
        )
        logger.info("StrategyOrchestrator initialised with %d strategies", len(_strategies))
    except Exception as exc:
        logger.warning("StrategyOrchestrator init failed: %s", exc)

    # ── DataStore ─────────────────────────────────────────────────────────────
    try:
        from data.store import DataStore
        db_url = getattr(app_settings, "database_url", "sqlite:///./algo_trading.db")
        # Guard against invalid URLs in .env (e.g. placeholder values)
        if not db_url or not any(db_url.startswith(p) for p in ("sqlite", "postgresql", "mysql", "oracle", "mssql")):
            logger.warning("DATABASE_URL %r looks invalid — falling back to SQLite", db_url)
            db_url = "sqlite:///./algo_trading.db"
        state.data_store = DataStore(db_url)
        logger.info("DataStore initialised (url=%s)", db_url.split("@")[-1])
    except Exception as exc:
        logger.warning("DataStore init failed: %s — falling back to in-memory SQLite", exc)
        try:
            from data.store import DataStore
            state.data_store = DataStore("sqlite:///./algo_trading.db")
            logger.info("DataStore initialised with fallback SQLite")
        except Exception as exc2:
            logger.warning("DataStore fallback also failed: %s", exc2)

    # ── DataPipeline (news + bar polling) ─────────────────────────────────────
    pipeline = None
    try:
        from data.pipeline import DataPipeline
        all_equity: list[str] = []
        all_crypto: list[str] = []
        for cfg_val in (strategy_configs.get(k, {}) for k in
                        ("momentum", "mean_reversion", "market_making", "sentiment")):
            for t in cfg_val.get("default_tickers", []):
                if "-" in t and any(t.endswith(s) for s in ("-USD", "-USDT", "-BTC")):
                    all_crypto.append(t)
                else:
                    all_equity.append(t)
        equity_tickers = list(dict.fromkeys(all_equity)) or ["AAPL", "MSFT", "NVDA"]
        crypto_tickers = list(dict.fromkeys(all_crypto)) or ["BTC-USD", "ETH-USD"]
        pipeline = DataPipeline(
            store=state.data_store,
            equity_tickers=equity_tickers,
            crypto_tickers=crypto_tickers,
        )
        await pipeline.start()
        state._pipeline = pipeline  # type: ignore[attr-defined]
        logger.info(
            "DataPipeline started (equity=%s crypto=%s)",
            equity_tickers, crypto_tickers,
        )
    except Exception as exc:
        logger.warning("DataPipeline start failed: %s", exc)

    # ── Portfolio (live state shared by TradingEngine + REST endpoints) ────────
    try:
        from backtesting.portfolio import Portfolio as BtPortfolio
        live_portfolio = BtPortfolio(initial_capital=100_000.0)
        state.portfolio = live_portfolio
        logger.info("Live Portfolio initialised")
    except Exception as exc:
        logger.warning("Live Portfolio init failed: %s", exc)

    # ── TradingEngine ─────────────────────────────────────────────────────────
    trading_engine = None
    try:
        from execution.trading_engine import TradingEngine

        # Collect all unique tickers from loaded strategies
        engine_tickers: list[str] = []
        if state.orchestrator is not None:
            for s in getattr(state.orchestrator, "strategies", []):
                for t in getattr(s, "tickers", []):
                    if t not in engine_tickers:
                        engine_tickers.append(t)
        if not engine_tickers:
            engine_tickers = ["AAPL", "MSFT", "NVDA", "BTC-USD", "ETH-USD"]

        bar_interval = strategy_configs.get("portfolio", {}).get("default_bar_interval", "1d")

        trading_engine = TradingEngine(
            store=state.data_store,
            orchestrator=state.orchestrator,
            broker=state.broker,
            risk_manager=state.risk_manager,
            monitor=state.monitor,
            portfolio=state.portfolio,
            tickers=engine_tickers,
            bar_interval=bar_interval,
            trading_mode=state.trading_mode,
            initial_capital=100_000.0,
            app_state=state,
        )
        state.trading_engine = trading_engine

        # Auto-start the trading loop in paper and live modes
        if state.trading_mode in ("paper", "live"):
            await trading_engine.start()
            logger.info(
                "TradingEngine auto-started (mode=%s tickers=%s interval=%s)",
                state.trading_mode, engine_tickers, bar_interval,
            )
        else:
            logger.info(
                "TradingEngine ready (mode=dev — call POST /api/trading/start to begin)"
            )
    except Exception as exc:
        logger.warning("TradingEngine init failed: %s", exc)

    # ── Attach state to app ───────────────────────────────────────────────────
    # Only set app_state if it hasn't been pre-loaded by tests
    if not hasattr(app.state, "app_state"):
        app.state.app_state = state
    logger.info("quant-engine API ready on startup (mode=%s)", state.trading_mode)

    yield  # ── server is running ─────────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if trading_engine is not None:
        try:
            await trading_engine.stop()
            logger.info("TradingEngine stopped")
        except Exception as exc:
            logger.warning("TradingEngine stop error: %s", exc)
    if pipeline is not None:
        try:
            await pipeline.stop()
            logger.info("DataPipeline stopped")
        except Exception as exc:
            logger.warning("DataPipeline stop error: %s", exc)
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
app.include_router(analysis_router)
app.include_router(backtest_router)
app.include_router(news_router)
app.include_router(optimize_router)
app.include_router(portfolio_router)
app.include_router(risk_router)
app.include_router(signals_router)
app.include_router(strategies_router)
app.include_router(trading_router)
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
