"""
api/routes/signals.py — Latest strategy signals and signal history.

Endpoints
---------
GET  /api/signals
    Return the most recent signal from each strategy for each ticker.
    Useful for the dashboard's signal table.

GET  /api/signals/history
    Return the last N signals in reverse-chronological order.

POST /api/signals/clear
    Clear the cached signal history (dev/testing utility).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.deps import AppState, get_app_state
from api.schemas import SignalItem, SignalsResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("", response_model=SignalsResponse)
async def get_latest_signals(
    state: AppState = Depends(get_app_state),
) -> SignalsResponse:
    """
    Return all cached signals, most recent first.

    The signal cache is populated by the live engine's ``on_signal`` hook
    or by the WebSocket feed broadcaster during backtesting.
    """
    signals = [SignalItem(**s) for s in state.latest_signals]
    return SignalsResponse(signals=signals, count=len(signals))


@router.get("/history")
async def get_signal_history(
    limit: int = 200,
    ticker: str | None = None,
    strategy_id: str | None = None,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Return the last ``limit`` signals with optional filters.

    Query parameters
    ----------------
    limit        : int  — max results to return (default 200)
    ticker       : str  — filter to a specific ticker
    strategy_id  : str  — filter to a specific strategy
    """
    signals = list(state.latest_signals)
    if ticker:
        signals = [s for s in signals if s.get("ticker") == ticker]
    if strategy_id:
        signals = [s for s in signals if s.get("strategy_id") == strategy_id]
    signals = signals[-limit:]
    return {"signals": signals, "count": len(signals)}


@router.post("/clear", status_code=204)
async def clear_signals(
    state: AppState = Depends(get_app_state),
) -> None:
    """Clear the signal cache. Useful during testing or after a strategy reset."""
    state.latest_signals.clear()
