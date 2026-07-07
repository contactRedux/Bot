"""
api package — FastAPI REST and WebSocket server.

Sub-modules
-----------
api.main         — FastAPI app with lifespan, CORS, and all routers mounted
api.schemas      — Pydantic request/response models for the API surface
api.routes.*     — REST endpoint routers (backtest, portfolio, signals, strategies)
api.ws.feed      — WebSocket /ws/feed endpoint for real-time event streaming
"""
