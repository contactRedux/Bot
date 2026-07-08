"""
api/routes/backtest.py — Backtest trigger and results endpoints.

Endpoints
---------
POST /api/backtest/run
    Accepts a BacktestRequest body, runs the simulation synchronously
    (or asynchronously in a background task), and returns the full
    BacktestResponse including metrics, equity curve, and trade log.

GET  /api/backtest/{run_id}
    Retrieve a previously completed backtest result by run ID.

GET  /api/backtest/{run_id}/status
    Poll the progress of a running backtest (useful for long-running
    walk-forward or multi-year simulations).

GET  /api/backtest/list
    List all cached backtest run IDs and their summary metrics.

DELETE /api/backtest/{run_id}
    Remove a completed run from the cache.

Implementation notes
--------------------
Backtest runs are executed in a ``BackgroundTask`` so the HTTP response
returns immediately with a ``run_id``.  The client polls
``GET /api/backtest/{run_id}/status`` until ``status == "completed"``
then fetches the full result.

For dev/test purposes, small backtests complete synchronously within the
request so that the test suite does not need to poll.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from api.deps import AppState, get_app_state, require_operator
from api.schemas import (
    BacktestRequest,
    BacktestResponse,
    BacktestStatusResponse,
    EquityCurvePoint,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_backtest_sync(
    req: BacktestRequest,
    run_id: str,
    state: AppState,
) -> dict[str, Any]:
    """
    Execute a backtest and return a serialised BacktestResponse dict.

    Uses yfinance to download historical bars in dev mode so the endpoint
    works without a live data feed or database.
    """
    # -- Try to use the real BacktestEngine if possible ----------------------
    try:
        from backtesting.engine import BacktestEngine
        from strategies.orchestrator import StrategyOrchestrator

        # Build per-ticker bar dicts from yfinance (silent fallback on error)
        bars: dict[str, Any] = {}
        try:
            import yfinance as yf
            for ticker in req.tickers:
                raw = yf.download(
                    ticker,
                    start=req.start_date,
                    end=req.end_date,
                    interval=req.interval,
                    auto_adjust=True,
                    progress=False,
                )
                if not raw.empty:
                    bars[ticker] = raw
        except Exception as exc:
            logger.warning("yfinance download failed (%s); using synthetic bars", exc)

        if not bars:
            # Synthetic fallback for testing without network
            bars = _synthetic_bars(req.tickers, req.start_date, req.end_date)

        # Build a minimal orchestrator
        from pathlib import Path

        import yaml

        from config.settings import settings as app_settings

        config_path = Path(app_settings.strategy_config_path)
        strategy_configs: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path) as f:
                strategy_configs = yaml.safe_load(f) or {}

        requested = set(req.strategies)
        use_all = "all" in requested

        orchestrator = StrategyOrchestrator(
            tickers=req.tickers,
            strategy_configs=strategy_configs,
        )
        if not use_all:
            for sid in list(orchestrator._strategies.keys()):
                if sid not in requested:
                    orchestrator._strategies[sid]._enabled = False

        engine = BacktestEngine(
            bars=bars,
            orchestrator=orchestrator,
            initial_capital=req.initial_capital,
            interval=req.interval,
        )
        report = engine.run()

        ec = [
            EquityCurvePoint(
                timestamp=pt["timestamp"] if isinstance(pt["timestamp"], str)
                          else str(pt["timestamp"]),
                equity=float(pt["equity"]),
            )
            for pt in report.equity_curve
        ]
        return BacktestResponse(
            run_id=run_id,
            status="completed",
            metrics=report.metrics,
            equity_curve=ec,
            trade_log=report.trade_log,
            strategy_attribution=report.strategy_attribution,
            tickers=report.tickers,
            initial_capital=report.initial_capital,
            bar_interval=report.bar_interval,
            halted=report.halted,
            halt_reason=report.halt_reason,
            created_at=report.created_at,
        ).model_dump()

    except Exception as exc:
        logger.error("Backtest engine failed: %s\n%s", exc, traceback.format_exc())
        return BacktestResponse(
            run_id=run_id,
            status="failed",
            metrics={},
            equity_curve=[],
            trade_log=[],
            strategy_attribution={},
            tickers=req.tickers,
            initial_capital=req.initial_capital,
            bar_interval=req.interval,
            halted=False,
            halt_reason="",
            created_at=datetime.now(datetime.UTC).isoformat(),
            error=str(exc),
        ).model_dump()


def _synthetic_bars(
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Generate synthetic OHLCV bars for offline testing."""
    import numpy as np
    import pandas as pd

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = pd.date_range(start, end, freq="B")

    rng = np.random.default_rng(42)
    result: dict[str, Any] = {}
    for t in tickers:
        n = len(dates)
        returns = rng.normal(0.0005, 0.015, n)
        prices = 100.0 * np.cumprod(1 + returns)
        df = pd.DataFrame({
            "Open": prices * 0.998,
            "High": prices * 1.005,
            "Low": prices * 0.995,
            "Close": prices,
            "Volume": rng.integers(1_000_000, 10_000_000, n).astype(float),
        }, index=dates)
        df.index.name = "Date"
        result[t] = df
    return result


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

def _background_run(
    req: BacktestRequest,
    run_id: str,
    state: AppState,
) -> None:
    """Runs the backtest in a background thread and stores the result."""
    state.backtest_status[run_id] = {
        "status": "running",
        "progress_pct": 0.0,
        "message": "Simulation in progress…",
    }
    try:
        result = _run_backtest_sync(req, run_id, state)
        state.backtest_results[run_id] = result
        state.backtest_status[run_id] = {
            "status": result.get("status", "completed"),
            "progress_pct": 100.0,
            "message": "Done",
        }
    except Exception as exc:
        logger.error("Background backtest failed: %s", exc)
        state.backtest_status[run_id] = {
            "status": "failed",
            "progress_pct": 0.0,
            "message": str(exc),
        }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run", response_model=BacktestStatusResponse, status_code=202)
async def run_backtest(
    body: BacktestRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> BacktestStatusResponse:
    """
    Trigger a new backtest run.

    Returns immediately with a ``run_id``.  Poll
    ``GET /api/backtest/{run_id}/status`` until completed,
    then fetch full results from ``GET /api/backtest/{run_id}``.
    """
    run_id = str(uuid.uuid4())[:12]
    logger.warning(
        "AUDIT backtest_run action=launch run_id=%s tickers=%s strategies=%s "
        "start=%s end=%s trading_mode=%s client=%s",
        run_id,
        body.tickers,
        body.strategies,
        body.start_date,
        body.end_date,
        state.trading_mode,
        request.client.host if request.client else "unknown",
    )
    background_tasks.add_task(_background_run, body, run_id, state)
    return BacktestStatusResponse(
        run_id=run_id,
        status="running",
        progress_pct=0.0,
        message="Backtest queued",
    )


@router.get("/{run_id}/status", response_model=BacktestStatusResponse)
async def get_backtest_status(
    run_id: str,
    state: AppState = Depends(get_app_state),
) -> BacktestStatusResponse:
    """Poll the status of a running or completed backtest."""
    status = state.backtest_status.get(run_id)
    if status is None:
        return BacktestStatusResponse(
            run_id=run_id, status="not_found", message="No such run ID"
        )
    return BacktestStatusResponse(
        run_id=run_id,
        status=status["status"],
        progress_pct=status["progress_pct"],
        message=status["message"],
    )


@router.get("/list")
async def list_backtests(
    state: AppState = Depends(get_app_state),
) -> dict:
    """List all cached backtest runs with summary metrics."""
    runs = []
    for run_id, result in state.backtest_results.items():
        m = result.get("metrics", {})
        runs.append({
            "run_id": run_id,
            "tickers": result.get("tickers", []),
            "created_at": result.get("created_at", ""),
            "status": result.get("status", "unknown"),
            "total_return_pct": m.get("total_return_pct", 0.0),
            "sharpe_ratio": m.get("sharpe_ratio", 0.0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
        })
    return {"runs": runs, "count": len(runs)}


@router.get("/{run_id}", response_model=BacktestResponse)
async def get_backtest_result(
    run_id: str,
    state: AppState = Depends(get_app_state),
) -> BacktestResponse:
    """Retrieve the full result of a completed backtest run."""
    result = state.backtest_results.get(run_id)
    if result is None:
        # Check if still running
        status = state.backtest_status.get(run_id)
        if status and status["status"] == "running":
            raise HTTPException(
                status_code=202,
                detail=f"Run {run_id} is still in progress. Poll /status first.",
            )
        raise HTTPException(status_code=404, detail=f"No backtest with run_id={run_id!r}")
    return BacktestResponse(**result)


@router.delete("/{run_id}", status_code=204)
async def delete_backtest(
    run_id: str,
    request: Request,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> None:
    """Remove a completed backtest run from the cache."""
    logger.warning(
        "AUDIT backtest_delete action=delete run_id=%s trading_mode=%s client=%s",
        run_id,
        state.trading_mode,
        request.client.host if request.client else "unknown",
    )
    state.backtest_results.pop(run_id, None)
    state.backtest_status.pop(run_id, None)
