"""
api/routes/portfolio.py — Live portfolio state endpoints.

Endpoints
---------
GET  /api/portfolio
    Current portfolio state: cash, total equity, open positions
    with unrealised PnL and mark-to-market values.

GET  /api/portfolio/history
    Equity curve history collected since server start.

GET  /api/portfolio/trades
    Recent trade log (fills) from the live broker.

GET  /api/portfolio/price-history
    OHLCV bars for a ticker from the DataStore, formatted for the PriceChart.
    Query parameters:
        ticker   — symbol (required)
        interval — bar interval, default "1d"
        limit    — max bars to return, default 365
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import AppState, get_app_state
from api.schemas import PortfolioResponse, PositionItem, PriceHistoryPoint, PriceHistoryResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=PortfolioResponse)
async def get_portfolio(
    state: AppState = Depends(get_app_state),
) -> PortfolioResponse:
    """
    Return the current live portfolio state.

    In dev/paper mode the portfolio reflects the PaperBroker's simulated fills.
    In live mode it reflects actual brokerage positions.
    """
    pf = state.portfolio
    if pf is None:
        # Return an empty portfolio response if no portfolio is initialised
        return PortfolioResponse(
            cash=0.0,
            total_equity=0.0,
            total_market_value=0.0,
            total_unrealised_pnl=0.0,
            total_realised_pnl=0.0,
            positions=[],
            last_updated=datetime.now(UTC).isoformat(),
        )

    positions: list[PositionItem] = []
    snapshot = pf.positions_snapshot() if hasattr(pf, "positions_snapshot") else {}
    for ticker, pos_data in (snapshot or {}).items():
        qty = float(pos_data.get("quantity", 0.0))
        if abs(qty) < 1e-8:
            continue
        avg_cost = float(pos_data.get("avg_cost", 0.0))
        mark = float(pos_data.get("mark_price", 0.0))
        market_val = qty * mark
        cost_basis = qty * avg_cost
        unreal_pnl = market_val - cost_basis
        unreal_pct = (unreal_pnl / abs(cost_basis) * 100.0) if abs(cost_basis) > 1e-8 else 0.0
        positions.append(PositionItem(
            ticker=ticker,
            quantity=round(qty, 6),
            avg_cost=round(avg_cost, 4),
            mark_price=round(mark, 4),
            market_value=round(market_val, 2),
            unrealised_pnl=round(unreal_pnl, 2),
            unrealised_pnl_pct=round(unreal_pct, 2),
        ))

    cash = float(getattr(pf, "cash", 0.0))
    total_mv = sum(p.market_value for p in positions)
    total_unreal = sum(p.unrealised_pnl for p in positions)
    total_real = float(getattr(pf, "realised_pnl", 0.0))

    return PortfolioResponse(
        cash=round(cash, 2),
        total_equity=round(cash + total_mv, 2),
        total_market_value=round(total_mv, 2),
        total_unrealised_pnl=round(total_unreal, 2),
        total_realised_pnl=round(total_real, 2),
        positions=positions,
        last_updated=datetime.now(UTC).isoformat(),
    )


@router.get("/history")
async def get_portfolio_history(
    limit: int = 500,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Return the recent equity curve history.

    ``limit`` controls how many most-recent data points are returned.
    """
    history = state.equity_history[-limit:] if state.equity_history else []
    return {
        "equity_history": history,
        "count": len(history),
    }


@router.get("/trades")
async def get_trades(
    limit: int = 100,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Return recent fills from the broker.

    In dev/paper mode, reads from PaperBroker.fills.
    In live mode, reads from the broker's fill history.
    """
    broker = state.broker
    fills: list[dict] = []
    if broker is not None and hasattr(broker, "fills"):
        for fill in broker.fills[-limit:]:
            fills.append(fill.to_dict() if hasattr(fill, "to_dict") else vars(fill))
    return {"trades": fills, "count": len(fills)}


@router.get("/price-history", response_model=PriceHistoryResponse)
async def get_price_history(
    ticker: str = Query(..., description="Ticker symbol, e.g. AAPL or BTC-USD"),
    interval: str = Query("1d", description="Bar interval: 1d, 1h, 15m, etc."),
    limit: int = Query(365, ge=1, le=2000, description="Maximum number of bars to return"),
    state: AppState = Depends(get_app_state),
) -> PriceHistoryResponse:
    """
    Return OHLCV bars for *ticker* from the DataStore formatted for the
    PriceChart component (time + close + optional OHLCV fields).

    If no data is found in the DataStore (e.g. the ticker is not in the
    pipeline's watched universe), falls back to a live yfinance fetch so that
    arbitrary tickers like AMD, MU, SNDK also display data.
    """
    if not ticker or not ticker.strip():
        raise HTTPException(status_code=422, detail="ticker must not be empty")

    store = state.data_store
    if store is None:
        return PriceHistoryResponse(ticker=ticker, interval=interval, points=[], count=0)

    end = datetime.now(UTC)
    # Estimate lookback: map interval to approximate trading bars per calendar day,
    # then multiply by limit + a 50% headroom for weekends/holidays.
    # Daily bars: ~1.0 trading day per calendar day (1.5× headroom covers weekends).
    # Intraday bars: scale by trading minutes per day (390 min).
    _interval_bars_per_day: dict[str, float] = {
        "1m": 390.0, "5m": 78.0, "15m": 26.0,
        "30m": 13.0, "1h": 6.5, "4h": 1.625, "1d": 1.0,
        "1w": 1 / 5, "1mo": 1 / 21,
    }
    bars_per_day = _interval_bars_per_day.get(interval, 1.0)
    # calendar days needed = (limit / bars_per_day) * 1.5 headroom, minimum 7
    lookback_days = max(7, int(limit / bars_per_day * 1.5))
    start = end - timedelta(days=lookback_days)

    try:
        bars = store.read_bars(ticker=ticker, interval=interval, start=start, end=end)
    except Exception:
        logger.exception("price_history.read_bars_failed ticker=%s interval=%s", ticker, interval)
        return PriceHistoryResponse(ticker=ticker, interval=interval, points=[], count=0)

    # If no bars in DB, fall back to a live yfinance fetch so that tickers
    # outside the pipeline's watched universe (e.g. AMD, MU, SNDK) still work.
    if not bars:
        try:
            import asyncio
            from data.feeds.yfinance_feed import YFinanceFeed
            yf = YFinanceFeed()
            loop = asyncio.get_event_loop()
            bars = await loop.run_in_executor(
                None, yf.fetch_bars, ticker, interval, start, end
            )
            # Persist for future requests so the next call is instant
            if bars and store is not None:
                store.write_bars(bars)
        except Exception:
            logger.exception("price_history.yfinance_fallback_failed ticker=%s", ticker)

    # Return only the most recent ``limit`` bars
    bars = bars[-limit:]

    points = [
        PriceHistoryPoint(
            time=b.event_timestamp.strftime("%Y-%m-%d")
            if interval in ("1d", "1w", "1mo")
            else b.event_timestamp.strftime("%Y-%m-%dT%H:%M"),
            close=round(b.close, 4),
            open=round(b.open, 4),
            high=round(b.high, 4),
            low=round(b.low, 4),
            volume=round(b.volume, 2) if b.volume else None,
        )
        for b in bars
    ]

    return PriceHistoryResponse(
        ticker=ticker,
        interval=interval,
        points=points,
        count=len(points),
    )
