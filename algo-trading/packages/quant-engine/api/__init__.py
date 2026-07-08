"""
api package — FastAPI REST and WebSocket server.

Sub-modules
-----------
api.main         — FastAPI app with lifespan, CORS, and all routers mounted
api.deps         — Dependency injection providers (get_app_state, get_monitor, …)
api.schemas      — Pydantic request/response models for the full API surface
api.routes.*     — REST endpoint routers:
                     backtest  — POST /run, GET /{id}, GET /list
                     portfolio — GET /portfolio, /history, /trades
                     risk      — GET /status, POST /resume, GET /var, /limits, /audit
                     signals   — GET /signals, /history
                     strategies— GET /strategies, PATCH /{id}, GET /{id}/signals
api.ws.feed      — WebSocket /ws/feed endpoint + ConnectionManager

Quick-start
-----------
::

    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs: http://localhost:8000/docs
"""
