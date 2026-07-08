"""
api/routes/strategies.py — Strategy management endpoints.

Endpoints
---------
GET  /api/strategies
    List all registered strategies with their current enabled state,
    allocation weight, and ticker list.

GET  /api/strategies/{strategy_id}
    Details for a single strategy.

PATCH /api/strategies/{strategy_id}
    Enable or disable a strategy at runtime without restarting.

GET  /api/strategies/{strategy_id}/signals
    Return the most recent signals produced by this strategy.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import AppState, get_app_state
from api.schemas import (
    StrategiesResponse,
    StrategyInfo,
    StrategyToggleRequest,
    StrategyToggleResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategies", tags=["strategies"])

# Human-readable display names for known strategy IDs
_DISPLAY_NAMES: dict[str, str] = {
    "momentum":       "Momentum (LSTM + Transformer)",
    "mean_reversion": "Mean Reversion (Bollinger / Z-score)",
    "stat_arb":       "Statistical Arbitrage (Cointegration)",
    "market_making":  "Market Making (PPO RL agent)",
    "sentiment":      "News Sentiment (FinBERT)",
    "macro_factor":   "Macro Factor (VIX + Yield Curve)",
}

_DESCRIPTIONS: dict[str, str] = {
    "momentum":       "Trend-following using LSTM + Transformer signal ensemble.",
    "mean_reversion": "Trades reversion to mean via Bollinger Bands and z-score.",
    "stat_arb":       "Exploits cointegrated spread between correlated pairs.",
    "market_making":  "Provides liquidity via PPO RL-optimised bid-ask quotes.",
    "sentiment":      "Scores news headlines with FinBERT to drive event-driven trades.",
    "macro_factor":   "Uses VIX, yield curve, and earnings data for regime signals.",
}


def _orchestrator_to_strategy_list(orchestrator: object) -> list[StrategyInfo]:
    """Convert the orchestrator's strategy registry to StrategyInfo list."""
    strategies: list[StrategyInfo] = []
    registry = getattr(orchestrator, "_strategies", {})
    for sid, strat in registry.items():
        strategies.append(StrategyInfo(
            strategy_id=sid,
            display_name=_DISPLAY_NAMES.get(sid, sid),
            description=_DESCRIPTIONS.get(sid, ""),
            enabled=getattr(strat, "_enabled", True),
            allocation_weight=float(getattr(strat, "allocation_weight", 0.1)),
            tickers=list(getattr(strat, "tickers", [])),
        ))
    return strategies


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=StrategiesResponse)
async def list_strategies(
    state: AppState = Depends(get_app_state),
) -> StrategiesResponse:
    """List all registered strategies and their current state."""
    if state.orchestrator is None:
        return StrategiesResponse(strategies=[], total=0)
    strategies = _orchestrator_to_strategy_list(state.orchestrator)
    return StrategiesResponse(strategies=strategies, total=len(strategies))


@router.get("/{strategy_id}", response_model=StrategyInfo)
async def get_strategy(
    strategy_id: str,
    state: AppState = Depends(get_app_state),
) -> StrategyInfo:
    """Return details for a single strategy."""
    if state.orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialised.")
    registry = getattr(state.orchestrator, "_strategies", {})
    strat = registry.get(strategy_id)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id!r} not found.")
    return StrategyInfo(
        strategy_id=strategy_id,
        display_name=_DISPLAY_NAMES.get(strategy_id, strategy_id),
        description=_DESCRIPTIONS.get(strategy_id, ""),
        enabled=getattr(strat, "_enabled", True),
        allocation_weight=float(getattr(strat, "allocation_weight", 0.1)),
        tickers=list(getattr(strat, "tickers", [])),
    )


@router.patch("/{strategy_id}", response_model=StrategyToggleResponse)
async def toggle_strategy(
    strategy_id: str,
    body: StrategyToggleRequest,
    state: AppState = Depends(get_app_state),
) -> StrategyToggleResponse:
    """
    Enable or disable a strategy at runtime.

    This does NOT restart the server — the change takes effect on the
    next bar processed by the StrategyOrchestrator.
    """
    if state.orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialised.")
    registry = getattr(state.orchestrator, "_strategies", {})
    strat = registry.get(strategy_id)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id!r} not found.")

    strat._enabled = body.enabled
    action = "enabled" if body.enabled else "disabled"
    logger.info("Strategy %s %s via API", strategy_id, action)
    return StrategyToggleResponse(
        strategy_id=strategy_id,
        enabled=body.enabled,
        message=f"Strategy '{strategy_id}' has been {action}.",
    )


@router.get("/{strategy_id}/signals")
async def get_strategy_signals(
    strategy_id: str,
    limit: int = 50,
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return the most recent signals produced by a specific strategy."""
    signals = [
        s for s in state.latest_signals
        if s.get("strategy_id") == strategy_id
    ][-limit:]
    return {"strategy_id": strategy_id, "signals": signals, "count": len(signals)}
