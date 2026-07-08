"""
api/routes/risk.py — Risk status, halt management, and VaR endpoints.

Endpoints
---------
GET  /api/risk/status
    Current risk status: drawdown, daily loss, VaR, CVaR, halt flag,
    and any detected correlation pairs.

POST /api/risk/resume
    Manually clear the trading halt set by the DrawdownMonitor.
    Requires operator confirmation (body: {"new_equity": float | null}).

GET  /api/risk/var
    Latest VaR/CVaR computation from the equity history.

GET  /api/risk/limits
    Return the currently configured RiskLimits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import AppState, get_app_state
from api.schemas import (
    ResumeRequest,
    ResumeResponse,
    RiskStatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/risk", tags=["risk"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status", response_model=RiskStatusResponse)
async def get_risk_status(
    state: AppState = Depends(get_app_state),
) -> RiskStatusResponse:
    """
    Return a full risk snapshot including:

    - Halt flag + reason
    - Current drawdown vs limit
    - Daily loss vs limit
    - Latest VaR/CVaR (95% and 99%)
    - Active correlation concentration pairs
    """
    monitor = state.monitor
    risk_mgr = state.risk_manager

    # DrawdownMonitor state
    monitor_status: dict[str, Any] = {}
    if monitor is not None and hasattr(monitor, "status"):
        monitor_status = monitor.status()

    halted = monitor_status.get("halted", False)
    halt_reason = monitor_status.get("halt_reason", "")
    peak_equity = monitor_status.get("peak_equity", 0.0)
    current_dd = monitor_status.get("current_drawdown_pct", 0.0)
    daily_loss = monitor_status.get("daily_loss_pct", 0.0)
    max_dd_limit = monitor_status.get("max_drawdown_pct_limit", 20.0)
    max_dl_limit = monitor_status.get("max_daily_loss_pct_limit", 2.0)

    # VaR from equity history
    var_95 = var_99 = cvar_95 = cvar_99 = 0.0
    if state.equity_history and len(state.equity_history) >= 32:
        try:
            from risk.var import HistoricalVaR
            hvar = HistoricalVaR(window=min(252, len(state.equity_history) - 1))
            result = hvar.compute(state.equity_history)
            var_95 = result.var_95
            var_99 = result.var_99
            cvar_95 = result.cvar_95
            cvar_99 = result.cvar_99
        except Exception as exc:
            logger.warning("VaR computation failed: %s", exc)

    # Correlation pairs from risk manager
    corr_pairs: list[dict[str, Any]] = []
    if risk_mgr is not None and hasattr(risk_mgr, "_correlation_checker"):
        checker = risk_mgr._correlation_checker
        prices = getattr(risk_mgr, "_price_history", {})
        if checker is not None and len(prices) >= 2:
            try:
                cr = checker.check(prices)
                corr_pairs = [
                    {
                        "ticker_a": p.ticker_a,
                        "ticker_b": p.ticker_b,
                        "correlation": round(p.correlation, 4),
                    }
                    for p in cr.concentrated_pairs
                ]
            except Exception as exc:
                logger.warning("Correlation check failed: %s", exc)

    return RiskStatusResponse(
        halted=halted,
        halt_reason=halt_reason,
        peak_equity=round(peak_equity, 2),
        current_drawdown_pct=round(current_dd, 3),
        daily_loss_pct=round(daily_loss, 3),
        max_drawdown_pct_limit=round(max_dd_limit, 1),
        max_daily_loss_pct_limit=round(max_dl_limit, 1),
        var_95=round(var_95, 2),
        var_99=round(var_99, 2),
        cvar_95=round(cvar_95, 2),
        cvar_99=round(cvar_99, 2),
        correlation_pairs=corr_pairs,
    )


@router.post("/resume", response_model=ResumeResponse)
async def resume_trading(
    body: ResumeRequest,
    state: AppState = Depends(get_app_state),
) -> ResumeResponse:
    """
    Manually clear a trading halt.

    This endpoint is the human-in-the-loop control for re-enabling
    trading after the DrawdownMonitor or daily-loss circuit-breaker
    has halted the system.

    **Use with caution in live mode.**
    """
    monitor = state.monitor
    if monitor is None:
        raise HTTPException(status_code=503, detail="DrawdownMonitor not initialised.")

    if not monitor.is_halted:
        return ResumeResponse(success=False, message="System is not currently halted.")

    monitor.reset_halt(new_equity=body.new_equity)
    logger.warning(
        "Trading halt CLEARED via API (new_equity=%s)", body.new_equity
    )
    return ResumeResponse(
        success=True,
        message=(
            f"Halt cleared. Peak equity reset to "
            f"{body.new_equity if body.new_equity is not None else 'previous peak'}."
        ),
    )


@router.get("/var")
async def get_var(
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return the latest VaR/CVaR computation from the equity history."""
    if not state.equity_history or len(state.equity_history) < 32:
        return {
            "available": False,
            "reason": "Insufficient equity history (need >= 32 data points)",
            "var_95": 0.0,
            "var_99": 0.0,
            "cvar_95": 0.0,
            "cvar_99": 0.0,
        }
    try:
        from risk.var import HistoricalVaR
        hvar = HistoricalVaR(window=min(252, len(state.equity_history) - 1))
        r = hvar.compute(state.equity_history)
        return {"available": True, **r.to_dict()}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


@router.get("/limits")
async def get_risk_limits(
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return the current RiskLimits configuration."""
    rm = state.risk_manager
    if rm is None:
        raise HTTPException(status_code=503, detail="RiskManager not initialised.")
    limits = rm.limits
    return limits.to_dict() if hasattr(limits, "to_dict") else {}


@router.get("/audit")
async def get_audit_log(
    limit: int = 50,
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return the most recent non-APPROVE risk decisions."""
    rm = state.risk_manager
    if rm is None:
        return {"entries": [], "count": 0}
    log = getattr(rm, "audit_log", [])
    entries = log[-limit:]
    return {"entries": entries, "count": len(entries)}
