"""
api/routes/optimize.py — Hyperparameter optimization and walk-forward endpoints.

Endpoints
---------
POST /api/optimize/run
    Run Bayesian hyperparameter optimization (Optuna TPE) for a single strategy
    on a date range.  Returns best params, all trial values, and elapsed time.

POST /api/backtest/walkforward
    Run walk-forward out-of-sample validation: n_splits × OOS folds, each with
    a fresh orchestrator.  Returns per-fold metrics and aggregate statistics.

GET  /api/optimize/spaces
    List available parameter spaces (one per strategy type).

Both endpoints run in a BackgroundTasks thread and return a run_id immediately.
Poll /api/optimize/{run_id}/status and /api/backtest/walkforward/{run_id}/status.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import AppState, get_app_state, require_operator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimize"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class OptimizeRequest(BaseModel):
    strategy: str = Field(
        ...,
        description="Strategy to tune: momentum | mean_reversion | kelly_vol | kalman_trend | vwap_reversion",
    )
    tickers: list[str] = Field(default=["AAPL", "MSFT", "NVDA"])
    start_date: str = Field(default="2022-01-01")
    end_date: str = Field(default="2024-12-31")
    interval: str = Field(default="1d")
    n_trials: int = Field(default=40, ge=5, le=200)
    objective: str = Field(
        default="sharpe",
        description="Objective metric: sharpe | sortino | calmar | total_return",
    )
    initial_capital: float = Field(default=100_000.0, gt=0)


class WalkForwardRequest(BaseModel):
    tickers: list[str] = Field(default=["AAPL", "MSFT", "NVDA"])
    strategies: list[str] = Field(default=["momentum", "mean_reversion"])
    interval: str = Field(default="1d")
    start_date: str = Field(default="2020-01-01")
    end_date: str = Field(default="2024-12-31")
    n_splits: int = Field(default=4, ge=2, le=10)
    oos_size_days: int = Field(default=252, ge=63, le=756)
    min_train_days: int = Field(default=365, ge=180)
    initial_capital: float = Field(default=100_000.0, gt=0)


# ---------------------------------------------------------------------------
# Parameter space names
# ---------------------------------------------------------------------------

_PARAM_SPACES = {
    "momentum":       "momentum_space",
    "mean_reversion": "mean_reversion_space",
    "kelly_vol":      "kelly_vol_space",
    "kalman_trend":   "kalman_trend_space",
    "vwap_reversion": "vwap_reversion_space",
}

_STRATEGY_CLASSES = {
    "momentum":       "strategies.momentum.MomentumStrategy",
    "mean_reversion": "strategies.mean_reversion.MeanReversionStrategy",
    "kelly_vol":      "strategies.kelly_vol.KellyVolStrategy",
    "kalman_trend":   "strategies.kalman_trend.KalmanTrendStrategy",
    "vwap_reversion": "strategies.vwap_reversion.VWAPReversionStrategy",
}


# ---------------------------------------------------------------------------
# Optimization background worker
# ---------------------------------------------------------------------------

def _run_optimize(req: OptimizeRequest, run_id: str, state: AppState) -> None:
    state.backtest_status[run_id] = {
        "status": "running", "progress_pct": 0.0,
        "message": f"Optimizing {req.strategy} ({req.n_trials} trials)…",
    }
    try:
        from backtesting.optimizer import StrategyOptimizer
        from data.feeds.yfinance_feed import YFinanceFeed
        from strategies.orchestrator import StrategyOrchestrator

        # Fetch bars
        feed = YFinanceFeed()
        from datetime import datetime as _dt
        start = _dt.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=__import__("datetime").timezone.utc)
        end   = _dt.strptime(req.end_date,   "%Y-%m-%d").replace(tzinfo=__import__("datetime").timezone.utc)
        bars: dict = {}
        for t in req.tickers:
            b = feed.fetch_bars(t, req.interval, start, end)
            if b:
                bars[t] = b

        if not bars:
            _intraday_limits = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "1h": 730, "90m": 60}
            days_limit = _intraday_limits.get(req.interval)
            if days_limit is not None and (end - start).days > days_limit:
                raise ValueError(
                    f"yfinance only supports '{req.interval}' data for the past {days_limit} days. "
                    f"Use interval '1d' for multi-year optimisations, or shorten the date range."
                )
            raise ValueError("No bars fetched — check tickers and date range")

        # Resolve strategy class
        space_name = _PARAM_SPACES.get(req.strategy)
        cls_path   = _STRATEGY_CLASSES.get(req.strategy)
        if not space_name or not cls_path:
            raise ValueError(f"Unknown strategy: {req.strategy!r}. Choose from: {list(_PARAM_SPACES)}")

        mod_name, cls_name = cls_path.rsplit(".", 1)
        import importlib
        mod = importlib.import_module(mod_name)
        StrategyCls = getattr(mod, cls_name)

        space_fn = getattr(StrategyOptimizer, space_name)

        def strategy_factory(trial):
            cfg = space_fn(trial)
            cfg["enabled"] = True
            return StrategyCls(config=cfg, tickers=req.tickers)

        def orchestrator_factory(strategies):
            return StrategyOrchestrator(strategies=strategies, config={})

        optimizer = StrategyOptimizer(
            bars=bars,
            strategy_factory=strategy_factory,
            orchestrator_factory=orchestrator_factory,
            n_trials=req.n_trials,
            objective=req.objective,
            initial_capital=req.initial_capital,
            bar_interval=req.interval,
            study_name=f"{req.strategy}_{run_id}",
        )
        result = optimizer.run()

        state.backtest_results[run_id] = {
            "run_id": run_id,
            "type": "optimization",
            "strategy": req.strategy,
            "status": "completed",
            **result.to_dict(),
        }
        state.backtest_status[run_id] = {
            "status": "completed", "progress_pct": 100.0,
            "message": f"Done — best {req.objective}={result.best_value:.4f}",
        }
    except Exception as exc:
        logger.error("Optimize failed [%s]: %s\n%s", run_id, exc, traceback.format_exc())
        state.backtest_status[run_id] = {
            "status": "failed", "progress_pct": 0.0, "message": str(exc),
        }


# ---------------------------------------------------------------------------
# Walk-forward background worker
# ---------------------------------------------------------------------------

def _run_walkforward(req: WalkForwardRequest, run_id: str, state: AppState) -> None:
    state.backtest_status[run_id] = {
        "status": "running", "progress_pct": 0.0,
        "message": f"Walk-forward {req.n_splits} folds…",
    }
    try:
        from backtesting.walkforward import WalkForwardBacktest
        from data.feeds.yfinance_feed import YFinanceFeed
        from strategies.orchestrator import StrategyOrchestrator
        import importlib, yaml
        from pathlib import Path
        from config.settings import settings as app_settings
        from datetime import datetime as _dt, timezone as _tz

        config_path = Path(app_settings.strategy_config_path)
        strategy_configs: dict = {}
        if config_path.exists():
            with open(config_path) as f:
                strategy_configs = yaml.safe_load(f) or {}

        feed = YFinanceFeed()
        start = _dt.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=_tz.utc)
        end   = _dt.strptime(req.end_date,   "%Y-%m-%d").replace(tzinfo=_tz.utc)
        bars: dict = {}
        for t in req.tickers:
            b = feed.fetch_bars(t, req.interval, start, end)
            if b:
                bars[t] = b

        if not bars:
            _intraday_limits = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "1h": 730, "90m": 60}
            days_limit = _intraday_limits.get(req.interval)
            if days_limit is not None and (end - start).days > days_limit:
                raise ValueError(
                    f"yfinance only supports '{req.interval}' data for the past {days_limit} days. "
                    f"Use interval '1d' for multi-year walk-forward tests, or shorten the date range."
                )
            raise ValueError("No bars fetched — check tickers and date range")

        requested = set(req.strategies)

        def orchestrator_factory():
            strats = []
            for sid in requested:
                cls_path = _STRATEGY_CLASSES.get(sid)
                if not cls_path:
                    continue
                mod_name, cls_name = cls_path.rsplit(".", 1)
                StrategyCls = getattr(importlib.import_module(mod_name), cls_name)
                cfg = dict(strategy_configs.get(sid, {}))
                cfg["enabled"] = True
                tickers = cfg.pop("default_tickers", req.tickers)
                strats.append(StrategyCls(config=cfg, tickers=tickers))
            return StrategyOrchestrator(strategies=strats, config={})

        wfb = WalkForwardBacktest(
            bars=bars,
            orchestrator_factory=orchestrator_factory,
            n_splits=req.n_splits,
            oos_size_days=req.oos_size_days,
            min_train_days=req.min_train_days,
            initial_capital=req.initial_capital,
        )
        results = wfb.run()
        wf_dict = results.to_dict()
        wf_dict.update({"run_id": run_id, "type": "walkforward", "status": "completed"})
        state.backtest_results[run_id] = wf_dict
        state.backtest_status[run_id] = {
            "status": "completed", "progress_pct": 100.0,
            "message": f"Done — {results.n_folds if hasattr(results, 'n_folds') else req.n_splits} folds completed",
        }
    except Exception as exc:
        logger.error("WalkForward failed [%s]: %s\n%s", run_id, exc, traceback.format_exc())
        state.backtest_status[run_id] = {
            "status": "failed", "progress_pct": 0.0, "message": str(exc),
        }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/optimize/run", status_code=202)
async def run_optimize(
    body: OptimizeRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> dict:
    """Trigger Bayesian hyperparameter optimization for a strategy."""
    run_id = str(uuid.uuid4())[:12]
    logger.warning(
        "AUDIT optimize_run strategy=%s tickers=%s trials=%d client=%s",
        body.strategy, body.tickers, body.n_trials,
        request.client.host if request.client else "unknown",
    )
    background_tasks.add_task(_run_optimize, body, run_id, state)
    return {"run_id": run_id, "status": "running", "message": "Optimization queued"}


@router.post("/api/backtest/walkforward", status_code=202)
async def run_walkforward(
    body: WalkForwardRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    state: AppState = Depends(get_app_state),
    _: None = Depends(require_operator),
) -> dict:
    """Trigger walk-forward out-of-sample validation."""
    run_id = str(uuid.uuid4())[:12]
    logger.warning(
        "AUDIT walkforward_run tickers=%s strategies=%s splits=%d client=%s",
        body.tickers, body.strategies, body.n_splits,
        request.client.host if request.client else "unknown",
    )
    background_tasks.add_task(_run_walkforward, body, run_id, state)
    return {"run_id": run_id, "status": "running", "message": "Walk-forward queued"}


@router.get("/api/optimize/spaces")
async def list_param_spaces() -> dict:
    """List all available parameter spaces and the strategies they cover."""
    return {
        "spaces": [
            {"strategy": s, "space": sp, "description": desc}
            for (s, sp), desc in zip(
                _PARAM_SPACES.items(),
                [
                    "LSTM+Transformer momentum — entry/cooldown/stop thresholds",
                    "Bollinger Band z-score — lookback/z-thresholds/ATR",
                    "Fractional Kelly + vol targeting — vol target/kelly fraction/lookback",
                    "1D Kalman filter trend — observation noise/process noise/thresholds",
                    "VWAP mean reversion — VWAP window/band widths/ATR+volume filters",
                ],
            )
        ]
    }


@router.get("/api/optimize/{run_id}/status")
async def get_optimize_status(
    run_id: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """Poll the status of a running optimization or walk-forward job."""
    s = state.backtest_status.get(run_id)
    if s is None:
        raise HTTPException(404, detail=f"No job with run_id={run_id!r}")
    return {"run_id": run_id, **s}


@router.get("/api/optimize/{run_id}")
async def get_optimize_result(
    run_id: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """Retrieve the completed result of an optimization or walk-forward job."""
    result = state.backtest_results.get(run_id)
    if result is None:
        s = state.backtest_status.get(run_id)
        if s and s["status"] == "running":
            raise HTTPException(202, detail="Still running — poll /status first")
        raise HTTPException(404, detail=f"No result for run_id={run_id!r}")
    return result
