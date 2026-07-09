"""
api/schemas.py — Pydantic request/response models for the API surface.

Design philosophy
-----------------
Every API endpoint has a typed request model (for POST bodies) and a typed
response model.  Using Pydantic models gives us:

1. **Automatic validation** — FastAPI validates incoming JSON against the
   schema before your route function is ever called.  Bad requests get a
   422 Unprocessable Entity with a clear error message.

2. **OpenAPI docs** — FastAPI generates the /docs and /redoc pages entirely
   from these models.  No manual Swagger YAML needed.

3. **Type safety** — all route functions return typed dicts/models, so mypy
   can catch shape mismatches at development time.

Naming convention
-----------------
Request bodies:   ``*Request``  (e.g. ``BacktestRequest``)
Response payloads:``*Response`` (e.g. ``BacktestResponse``)
Nested items:     ``*Item``     (e.g. ``TradeItem``, ``EquityCurvePoint``)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------

class EquityCurvePoint(BaseModel):
    timestamp: str
    equity: float


class TradeItem(BaseModel):
    timestamp: str
    ticker: str
    side: str
    quantity: float
    fill_price: float
    commission: float
    realised_pnl: float
    strategy_id: str


class StrategyAttributionItem(BaseModel):
    strategy_id: str
    realised_pnl: float
    pct_contribution: float


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    """
    Request body for ``POST /api/backtest/run``.

    All date fields accept ISO 8601 strings (``YYYY-MM-DD``).
    """
    tickers: list[str] = Field(
        min_length=1,
        description="Ticker symbols to include (e.g. ['AAPL', 'BTC-USD'])",
        examples=[["AAPL", "MSFT"]],
    )
    start_date: str = Field(
        description="Backtest start date (YYYY-MM-DD)",
        examples=["2023-01-01"],
    )
    end_date: str = Field(
        description="Backtest end date (YYYY-MM-DD)",
        examples=["2024-01-01"],
    )
    strategies: list[str] = Field(
        default=["all"],
        description="Strategy IDs to activate, or ['all'] for every enabled strategy",
        examples=[["momentum", "mean_reversion"]],
    )
    initial_capital: float = Field(
        default=100_000.0,
        gt=0,
        description="Starting capital in USD",
    )
    interval: str = Field(
        default="1d",
        description="Bar interval (1d, 1h, 15m)",
    )


class MetricsSummary(BaseModel):
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    annual_volatility_pct: float
    n_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_trade_pnl: float
    final_equity: float
    start_date: str | None = None
    end_date: str | None = None


class BacktestResponse(BaseModel):
    run_id: str
    status: Literal["completed", "running", "failed"]
    metrics: dict[str, Any]
    equity_curve: list[EquityCurvePoint]
    trade_log: list[dict[str, Any]]
    strategy_attribution: dict[str, float]
    tickers: list[str]
    initial_capital: float
    bar_interval: str
    halted: bool
    halt_reason: str
    created_at: str
    error: str | None = None


class BacktestStatusResponse(BaseModel):
    run_id: str
    status: Literal["completed", "running", "failed", "not_found"]
    progress_pct: float = 0.0
    message: str = ""


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

class PriceHistoryPoint(BaseModel):
    """One close-price data point returned by ``GET /api/portfolio/price-history``."""

    time: str    # ISO date string, e.g. "2024-01-15"
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None


class PriceHistoryResponse(BaseModel):
    ticker: str
    interval: str
    points: list[PriceHistoryPoint]
    count: int


class PositionItem(BaseModel):
    ticker: str
    quantity: float
    avg_cost: float
    mark_price: float
    market_value: float
    unrealised_pnl: float
    unrealised_pnl_pct: float


class PortfolioResponse(BaseModel):
    cash: float
    total_equity: float
    total_market_value: float
    total_unrealised_pnl: float
    total_realised_pnl: float
    positions: list[PositionItem]
    last_updated: str


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class SignalItem(BaseModel):
    ticker: str
    strategy_id: str
    signal: float          # [-1, +1]
    confidence: float      # [0, 1]
    timestamp: str


class SignalsResponse(BaseModel):
    signals: list[SignalItem]
    count: int


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class RiskStatusResponse(BaseModel):
    halted: bool
    halt_reason: str
    peak_equity: float
    current_drawdown_pct: float
    daily_loss_pct: float
    max_drawdown_pct_limit: float
    max_daily_loss_pct_limit: float
    var_95: float
    var_99: float
    cvar_95: float
    cvar_99: float
    correlation_pairs: list[dict[str, Any]]


class ResumeRequest(BaseModel):
    """Body for ``POST /api/risk/resume``."""
    new_equity: float | None = Field(
        default=None,
        description="If provided, resets peak equity to this value after clearing the halt.",
    )


class ResumeResponse(BaseModel):
    success: bool
    message: str


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

class StrategyInfo(BaseModel):
    strategy_id: str
    display_name: str
    description: str
    enabled: bool
    allocation_weight: float
    tickers: list[str]


class StrategiesResponse(BaseModel):
    strategies: list[StrategyInfo]
    total: int


class StrategyToggleRequest(BaseModel):
    enabled: bool


class StrategyToggleResponse(BaseModel):
    strategy_id: str
    enabled: bool
    message: str


# ---------------------------------------------------------------------------
# Health / meta
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    trading_mode: str
    broker_connected: bool
    uptime_seconds: float
    timestamp: str


# ---------------------------------------------------------------------------
# WebSocket event envelope
# ---------------------------------------------------------------------------

class WSEvent(BaseModel):
    """
    Envelope for all WebSocket messages pushed to the dashboard.

    ``event_type`` is used by the dashboard to route the payload to the
    correct Zustand store slice.
    """
    event_type: Literal[
        "bar",
        "signal",
        "fill",
        "risk_alert",
        "portfolio_update",
        "heartbeat",
        "backtest_progress",
        "news",
        "trading_status",
    ]
    payload: dict[str, Any]
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
