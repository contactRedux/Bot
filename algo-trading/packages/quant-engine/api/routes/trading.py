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

POST /api/trading/order
    Submit a manual paper trade (BUY or SELL) through the PaperBroker.
    Body: { "ticker": "AAPL", "side": "buy"|"sell", "quantity": 10.0,
            "order_type": "market"|"limit", "limit_price": null }

These endpoints are operator-protected in production (require the OIDC
operator role).  In dev mode (no OIDC_ISSUER_URL set) they are open.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

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


class ManualOrderRequest(BaseModel):
    ticker: str
    side: str = Field(..., pattern="^(buy|sell)$")
    quantity: float = Field(..., gt=0)
    order_type: str = Field(default="market", pattern="^(market|limit)$")
    limit_price: float | None = None


@router.post("/order")
async def manual_order(
    request: Request,
    body: ManualOrderRequest,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> dict:
    """
    Submit a manual paper trade through the broker.

    In paper/dev mode this goes straight to the PaperBroker — no real money
    changes hands.  The broker needs a mark price for the ticker; if none is
    loaded yet, the order will be rejected with status ``rejected``.
    """
    from datetime import datetime, timezone
    from strategies.base import Order, OrderSide, OrderType

    broker = getattr(state, "broker", None)
    if broker is None:
        raise HTTPException(status_code=503, detail="Broker not initialised.")

    # If the broker has no price yet for this ticker, try to seed it from
    # the DataStore (latest bar close).
    ticker = body.ticker.upper().strip()
    if not hasattr(broker, "_prices") or broker._prices.get(ticker, 0.0) <= 0.0:
        try:
            from datetime import UTC, timedelta
            store = state.data_store
            if store is not None:
                bars = store.read_bars(
                    ticker=ticker,
                    interval="1d",
                    start=datetime.now(UTC) - timedelta(days=7),
                    end=datetime.now(UTC),
                )
                if not bars:
                    # fallback: on-demand yfinance fetch
                    import asyncio
                    from data.feeds.yfinance_feed import YFinanceFeed
                    yf = YFinanceFeed()
                    loop = asyncio.get_event_loop()
                    bars = await loop.run_in_executor(
                        None, yf.fetch_bars, ticker, "1d",
                        datetime.now(UTC) - timedelta(days=7), datetime.now(UTC),
                    )
                if bars:
                    broker.update_prices({ticker: bars[-1].close})
        except Exception as exc:
            logger.warning("manual_order.price_seed_failed ticker=%s error=%s", ticker, exc)

    order = Order(
        ticker=ticker,
        side=OrderSide(body.side),
        quantity=body.quantity,
        order_type=OrderType(body.order_type),
        limit_price=body.limit_price,
        strategy_id="manual",
        confidence=1.0,
        timestamp=datetime.now(timezone.utc),
    )

    try:
        fill = broker.submit_order(order)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Order failed: {exc}") from exc

    # Feed the fill into the portfolio if available.
    # backtesting.portfolio.Portfolio.on_fill() expects backtesting.events.FillEvent
    # (which has .quantity), not execution.base.FillEvent (which has .filled_quantity).
    portfolio = getattr(state, "portfolio", None)
    if portfolio is not None and hasattr(portfolio, "on_fill") and fill.is_filled:
        try:
            from datetime import datetime, timezone
            from backtesting.events import FillEvent as BtFillEvent
            bt_fill = BtFillEvent(
                timestamp=fill.timestamp,
                ticker=fill.ticker,
                side=fill.side,
                quantity=fill.filled_quantity,
                fill_price=fill.fill_price,
                commission=fill.commission,
                strategy_id=fill.strategy_id,
            )
            portfolio.on_fill(bt_fill)
        except Exception as exc:
            logger.warning("manual_order.portfolio_on_fill_error error=%s", exc)

    logger.warning(
        "AUDIT manual_order ticker=%s side=%s qty=%s status=%s fill_price=%s client=%s",
        ticker, body.side, body.quantity, fill.status.value,
        fill.fill_price, request.client.host if request.client else "unknown",
    )

    return {
        "success": fill.is_filled,
        "status": fill.status.value,
        "ticker": ticker,
        "side": body.side,
        "quantity": fill.filled_quantity,
        "fill_price": fill.fill_price,
        "commission": fill.commission,
        "broker_order_id": fill.broker_order_id,
    }
