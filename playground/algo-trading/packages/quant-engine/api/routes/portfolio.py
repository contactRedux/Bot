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
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from api.deps import AppState, get_app_state
from api.schemas import PortfolioResponse, PositionItem

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
            last_updated=datetime.now(timezone.utc).isoformat(),
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
        last_updated=datetime.now(timezone.utc).isoformat(),
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
