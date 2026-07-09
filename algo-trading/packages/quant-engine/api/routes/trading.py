"""
api/routes/trading.py — TradingEngine control endpoints.

Endpoints
---------
GET  /api/trading/status
    Return the current TradingEngine state: running flag, tickers, loop count,
    last processed timestamps, and a portfolio snapshot.

POST /api/trading/start
    Start the trading loop if it is not already running.

POST /api/trading/stop
    Stop the trading loop gracefully.

POST /api/trading/tickers
    Replace the active ticker universe at runtime (restarts the engine).

These endpoints are operator-protected in production (require the OIDC
operator role).  In dev mode (no OIDC_ISSUER_URL set) they are open.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from api.deps import AppState, get_app_state, require_operator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trading", tags=["trading"])


def _get_engine(state: AppState) -> object:
    """Return the TradingEngine from AppState or raise 503."""
    engine = getattr(state, "trading_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="TradingEngine not initialised.")
    return engine


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_trading_status(
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return current TradingEngine state."""
    engine = _get_engine(state)
    return engine.status


@router.post("/start")
async def start_trading(
    request: Request,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> dict:
    """
    Start the live trading loop.

    No-op (returns success) if the engine is already running.
    """
    engine = _get_engine(state)
    if engine.is_running:
        return {"success": True, "message": "TradingEngine is already running."}

    await engine.start()
    logger.warning(
        "AUDIT trading_start trading_mode=%s tickers=%s client=%s",
        engine.trading_mode,
        engine.tickers,
        request.client.host if request.client else "unknown",
    )
    return {"success": True, "message": f"TradingEngine started ({engine.trading_mode} mode)."}


@router.post("/stop")
async def stop_trading(
    request: Request,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> dict:
    """
    Stop the live trading loop gracefully.

    Outstanding orders are NOT cancelled (the broker retains pending orders).
    Call ``POST /api/trading/start`` to resume.
    """
    engine = _get_engine(state)
    if not engine.is_running:
        return {"success": True, "message": "TradingEngine is not running."}

    await engine.stop()
    logger.warning(
        "AUDIT trading_stop loops_completed=%d client=%s",
        engine.loop_count,
        request.client.host if request.client else "unknown",
    )
    return {
        "success": True,
        "message": f"TradingEngine stopped after {engine.loop_count} loop(s).",
    }


@router.post("/tickers")
async def update_tickers(
    request: Request,
    tickers: list[str] = Body(..., embed=True, description="New ticker universe"),
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> dict:
    """
    Replace the active ticker universe.

    Stops the engine (if running), updates the ticker list, and restarts it.
    """
    if not tickers:
        raise HTTPException(status_code=422, detail="tickers list must not be empty.")

    engine = _get_engine(state)
    was_running = engine.is_running

    if was_running:
        await engine.stop()

    engine.tickers = list(tickers)

    if was_running:
        await engine.start()

    logger.warning(
        "AUDIT trading_tickers_updated tickers=%s client=%s",
        tickers,
        request.client.host if request.client else "unknown",
    )
    return {
        "success": True,
        "tickers": engine.tickers,
        "message": f"Tickers updated. Engine {'restarted' if was_running else 'ready (not running)'}.",
    }
