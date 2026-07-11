"""
api/ws/feed.py — WebSocket /ws/feed endpoint for real-time event streaming.

Protocol
--------
The dashboard connects once via WebSocket at ``ws://localhost:8000/ws/feed``.
The server pushes JSON-serialised ``WSEvent`` envelopes as events occur:

    {"event_type": "bar",              "payload": {...}, "timestamp": "..."}
    {"event_type": "signal",           "payload": {...}, "timestamp": "..."}
    {"event_type": "fill",             "payload": {...}, "timestamp": "..."}
    {"event_type": "risk_alert",       "payload": {...}, "timestamp": "..."}
    {"event_type": "portfolio_update", "payload": {...}, "timestamp": "..."}
    {"event_type": "heartbeat",        "payload": {},    "timestamp": "..."}
    {"event_type": "backtest_progress","payload": {...}, "timestamp": "..."}

Heartbeat
---------
The server sends a ``heartbeat`` event every 15 seconds to keep the
connection alive through proxies and load balancers that close idle
WebSocket connections.

Connection manager
------------------
``ConnectionManager`` maintains the set of all active WebSocket connections.
The ``broadcast()`` coroutine pushes a message to every connected client.
The engine and routes call ``manager.broadcast()`` whenever a noteworthy
event occurs.

Usage in routes / engine
------------------------
::

    from api.ws.feed import manager

    await manager.broadcast({
        "event_type": "signal",
        "payload": {"ticker": "AAPL", "signal": 0.82, "strategy_id": "momentum"},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 15


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """
    Manages the pool of active WebSocket connections.

    Thread-safe for concurrent asyncio tasks; NOT thread-safe across
    OS threads.  All calls must originate from the asyncio event loop.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info("WS client connected (total=%d)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info("WS client disconnected (total=%d)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Push ``message`` (a dict) to all connected clients."""
        if not self._connections:
            return
        text = json.dumps(message, default=str)
        dead: set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)

    async def send_to(self, ws: WebSocket, message: dict[str, Any]) -> None:
        """Push ``message`` to a single client."""
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception:
            self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Singleton — import this in routes and the engine
manager = ConnectionManager()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/feed")
async def websocket_feed(ws: WebSocket) -> None:
    """
    Real-time event feed.

    The client connects once; the server pushes events as they occur.
    The server also sends a heartbeat every 15 s to keep the connection alive.
    """
    await manager.connect(ws)

    # Send an initial welcome message with current timestamp
    await manager.send_to(ws, {
        "event_type": "heartbeat",
        "payload": {"message": "Connected to quant-engine feed"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Start a heartbeat task for this connection
    heartbeat_task = asyncio.ensure_future(_heartbeat(ws))

    try:
        while True:
            # Keep connection alive by waiting for client messages
            # (the dashboard doesn't send anything meaningful — this just
            #  processes ping/pong and clean close frames)
            data = await ws.receive_text()
            # Handle explicit client ping
            if data.strip().lower() in ("ping", '{"type":"ping"}'):
                await manager.send_to(ws, {
                    "event_type": "heartbeat",
                    "payload": {"pong": True},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        manager.disconnect(ws)
    finally:
        heartbeat_task.cancel()


async def _heartbeat(ws: WebSocket) -> None:
    """Send a heartbeat message every ``_HEARTBEAT_INTERVAL`` seconds."""
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            await manager.send_to(ws, {
                "event_type": "heartbeat",
                "payload": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Convenience broadcast helpers (called from engine / other routes)
# ---------------------------------------------------------------------------

async def broadcast_signal(
    ticker: str,
    strategy_id: str,
    signal: float,
    confidence: float,
) -> None:
    """Broadcast a strategy signal to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "signal",
        "payload": {
            "ticker": ticker,
            "strategy_id": strategy_id,
            "signal": round(signal, 4),
            "confidence": round(confidence, 4),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_fill(fill_dict: dict[str, Any]) -> None:
    """Broadcast a fill event to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "fill",
        "payload": fill_dict,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_risk_alert(alert_dict: dict[str, Any]) -> None:
    """Broadcast a risk alert to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "risk_alert",
        "payload": alert_dict,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_portfolio_update(equity: float, cash: float) -> None:
    """Broadcast a portfolio equity update to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "portfolio_update",
        "payload": {"equity": equity, "cash": cash},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_backtest_progress(
    run_id: str,
    progress_pct: float,
    message: str = "",
) -> None:
    """Broadcast backtest progress to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "backtest_progress",
        "payload": {
            "run_id": run_id,
            "progress_pct": round(progress_pct, 1),
            "message": message,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_news(article_dict: dict[str, Any]) -> None:
    """Broadcast a news article to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "news",
        "payload": article_dict,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_trading_status(running: bool, mode: str) -> None:
    """Broadcast trading engine state change to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "trading_status",
        "payload": {"running": running, "trading_mode": mode},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_engine_tick(
    ticker: str,
    close: float,
    orders: int,
    equity: float,
    bar_ts: str,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    """Broadcast a single engine tick event to all WebSocket clients."""
    await manager.broadcast({
        "event_type": "engine_tick",
        "payload": {
            "ticker": ticker,
            "close": round(close, 4),
            "orders": orders,
            "equity": round(equity, 2),
            "bar_ts": bar_ts,
            "skipped": skipped,
            "skip_reason": skip_reason,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
