"""
backtesting/optimizer.py — Bayesian Hyperparameter Optimizer for strategy parameters.

Overview
--------
Grid search evaluates every point on a fixed parameter lattice, which scales
exponentially with the number of dimensions.  Bayesian optimisation instead
builds a *surrogate probabilistic model* of the objective surface and uses it
to choose the next trial intelligently — trading off exploration (regions with
high uncertainty) and exploitation (regions known to perform well).

TPE Sampler
-----------
Optuna's default sampler is **Tree-structured Parzen Estimator (TPE)**.  It
fits two kernel-density estimators — one over the "good" trials (those whose
objective value is in the top quantile) and one over the rest — then proposes
parameter values where good/bad density ratio is highest.  In practice this
finds near-optimal regions in tens of trials rather than hundreds, making it
far more efficient than grid or random search for high-dimensional strategy
parameter spaces.

A ``seed=42`` is passed to ``TPESampler`` so that results are reproducible
across runs with the same ``n_trials`` and ``strategy_factory``.

Usage
-----
::

    import optuna
    from backtesting.optimizer import StrategyOptimizer
    from strategies.momentum import MomentumStrategy
    from orchestrator import StrategyOrchestrator

    def make_strategy(trial):
        params = StrategyOptimizer.momentum_space(trial)
        return MomentumStrategy(**params)

    def make_orchestrator(strategies):
        return StrategyOrchestrator(strategies)

    result = StrategyOptimizer(
        bars={"AAPL": bars_aapl, "MSFT": bars_msft},
        strategy_factory=make_strategy,
        orchestrator_factory=make_orchestrator,
        n_trials=100,
        objective="sharpe",
    ).run()

    print(result.best_params)
    print(result.best_value)

Objective metrics
-----------------
``"sharpe"``
    Annualised Sharpe ratio — risk-adjusted return divided by return
    volatility.  The industry default.  A value ≥ 1.0 is considered acceptable
    live; ≥ 2.0 is excellent.

``"sortino"``
    Like Sharpe but penalises only downside volatility.  Prefer this for
    strategies with positively-skewed payoffs (e.g. trend-following).

``"calmar"``
    CAGR / |max_drawdown|.  Useful when drawdown is the dominant risk
    constraint, e.g. managed futures or low-frequency strategies.

``"total_return"``
    Raw percentage return over the period.  Simple but ignores risk entirely;
    use only when the backtest window is very short or risk is controlled
    separately.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

from backtesting.engine import BacktestEngine

_log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OptimizationResult
# ---------------------------------------------------------------------------

_VALID_OBJECTIVES = frozenset({"sharpe", "calmar", "sortino", "total_return"})

# Metric key in the BacktestReport.metrics dict for each objective name
_OBJECTIVE_METRIC_KEY: dict[str, str] = {
    "sharpe":       "sharpe_ratio",
    "sortino":      "sortino_ratio",
    "calmar":       "calmar_ratio",
    "total_return": "total_return_pct",
}

# Sentinel returned to Optuna when a trial fails so the trial is pruned /
# marked failed rather than crashing the whole study.
_FAILED_TRIAL_SENTINEL = -999.0


@dataclass
class OptimizationResult:
    """
    Container for the results of a completed hyperparameter optimisation run.

    Attributes
    ----------
    best_params : dict
        Parameter dict from the best trial.
    best_value : float
        Objective value achieved by the best trial.
    objective : str
        The metric that was optimised (e.g. ``"sharpe"``).
    n_trials : int
        Number of trials that were run (including failed ones).
    all_trials : list[dict]
        One dict per trial with keys ``trial_number``, ``params``,
        ``value`` (float or None), and ``state`` (string).
    study_name : str
        Optuna study name.
    elapsed_seconds : float
        Wall-clock time of the full ``study.optimize()`` call.
    """

    best_params: dict
    best_value: float
    objective: str
    n_trials: int
    all_trials: list[dict]
    study_name: str
    elapsed_seconds: float

    def to_dict(self) -> dict:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "best_params":      self.best_params,
            "best_value":       self.best_value,
            "objective":        self.objective,
            "n_trials":         self.n_trials,
            "all_trials":       self.all_trials,
            "study_name":       self.study_name,
            "elapsed_seconds":  round(self.elapsed_seconds, 3),
        }


# ---------------------------------------------------------------------------
# StrategyOptimizer
# ---------------------------------------------------------------------------

class StrategyOptimizer:
    """
    Bayesian hyperparameter optimiser for strategy parameters using Optuna.

    Each trial:
    1. Calls ``strategy_factory(trial)`` to build a strategy with
       Optuna-suggested parameters.
    2. Wraps it in an orchestrator via ``orchestrator_factory([strategy])``.
    3. Runs a full backtest with ``BacktestEngine``.
    4. Returns the requested objective metric to Optuna.

    Parameters
    ----------
    bars : dict[str, list]
        Historical OHLCV bars per ticker.
    strategy_factory : callable
        ``(trial: optuna.Trial) -> BaseStrategy``
        Responsible for calling ``trial.suggest_*`` to define the search space
        and returning a configured strategy instance.
    orchestrator_factory : callable
        ``(strategies: list) -> StrategyOrchestrator``
        Wraps the strategy list in an orchestrator.
    n_trials : int
        Maximum number of Optuna trials.  Default 50.
    n_jobs : int
        Parallel jobs for ``study.optimize()``.  Default 1.
    initial_capital : float
        Starting portfolio cash.
    bar_interval : str
        Bar frequency string passed to ``BacktestEngine`` (e.g. ``"1d"``).
    objective : str
        One of ``"sharpe"``, ``"sortino"``, ``"calmar"``, ``"total_return"``.
    direction : str
        ``"maximize"`` (default) or ``"minimize"``.
    timeout_seconds : int | None
        Optional wall-clock timeout passed to ``study.optimize()``.
    study_name : str | None
        Optuna study name.  Auto-generated if None.
    """

    def __init__(
        self,
        bars: dict,
        strategy_factory: Callable,
        orchestrator_factory: Callable,
        n_trials: int = 50,
        n_jobs: int = 1,
        initial_capital: float = 100_000.0,
        bar_interval: str = "1d",
        objective: str = "sharpe",
        direction: str = "maximize",
        timeout_seconds: int | None = None,
        study_name: str | None = None,
    ) -> None:
        if objective not in _VALID_OBJECTIVES:
            raise ValueError(
                f"Invalid objective {objective!r}. "
                f"Must be one of {sorted(_VALID_OBJECTIVES)}."
            )

        self.bars = bars
        self.strategy_factory = strategy_factory
        self.orchestrator_factory = orchestrator_factory
        self.n_trials = n_trials
        self.n_jobs = n_jobs
        self.initial_capital = initial_capital
        self.bar_interval = bar_interval
        self.objective = objective
        self.direction = direction
        self.timeout_seconds = timeout_seconds
        self.study_name = study_name or f"optimizer_{objective}_{int(time.time())}"

        # Set after run() completes
        self._study: Any = None

    # ── Public API ────────────────────────────────────────────────────────

    def run(self) -> OptimizationResult:
        """
        Execute the optimisation study.

        Returns
        -------
        OptimizationResult
            Full results including best params, best value, and per-trial data.
        """
        import optuna  # lazy import — not required at instantiation time

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        _log.info(
            "optimizer.run.start",
            study_name=self.study_name,
            n_trials=self.n_trials,
            objective=self.objective,
            direction=self.direction,
        )

        sampler = optuna.samplers.TPESampler(seed=42)
        study = optuna.create_study(
            study_name=self.study_name,
            direction=self.direction,
            sampler=sampler,
        )
        self._study = study

        metric_key = _OBJECTIVE_METRIC_KEY[self.objective]

        def _objective(trial: "optuna.Trial") -> float:
            try:
                strategy = self.strategy_factory(trial)
                orchestrator = self.orchestrator_factory([strategy])
                engine = BacktestEngine(
                    bars=self.bars,
                    orchestrator=orchestrator,
                    initial_capital=self.initial_capital,
                    bar_interval=self.bar_interval,
                )
                report = engine.run()
                value = float(report.metrics.get(metric_key, 0.0))
                return value
            except Exception:
                logging.getLogger(__name__).debug(
                    "Trial %d raised an exception; returning sentinel value.",
                    trial.number,
                    exc_info=True,
                )
                return _FAILED_TRIAL_SENTINEL

        t0 = time.perf_counter()
        study.optimize(
            _objective,
            n_trials=self.n_trials,
            n_jobs=self.n_jobs,
            timeout=self.timeout_seconds,
        )
        elapsed = time.perf_counter() - t0

        all_trials = [
            {
                "trial_number": t.number,
                "params":       dict(t.params),
                "value":        t.value,
                "state":        str(t.state),
            }
            for t in study.trials
        ]

        best_value: float
        best_params: dict
        try:
            best_value = float(study.best_value)
            best_params = dict(study.best_params)
        except ValueError:
            # No successful trial — study.best_value raises
            best_value = _FAILED_TRIAL_SENTINEL
            best_params = {}

        _log.info(
            "optimizer.run.finish",
            study_name=self.study_name,
            n_trials_completed=len(study.trials),
            best_value=best_value,
            objective=self.objective,
            elapsed_seconds=round(elapsed, 3),
        )

        return OptimizationResult(
            best_params=best_params,
            best_value=best_value,
            objective=self.objective,
            n_trials=len(study.trials),
            all_trials=all_trials,
            study_name=self.study_name,
            elapsed_seconds=elapsed,
        )

    def best_params(self) -> dict:
        """
        Return the best parameters found so far.

        Returns an empty dict if ``run()`` has not been called yet or if no
        trial completed successfully.
        """
        if self._study is None:
            return {}
        try:
            return dict(self._study.best_params)
        except ValueError:
            return {}

    # ── Predefined parameter spaces ───────────────────────────────────────

    @staticmethod
    def momentum_space(trial: Any) -> dict:
        """Return a config dict with Optuna-suggested values for MomentumStrategy."""
        return {
            "entry_threshold":  trial.suggest_float("entry_threshold", 0.45, 0.75),
            "min_confidence":   trial.suggest_float("min_confidence", 0.45, 0.80),
            "cooldown_bars":    trial.suggest_int("cooldown_bars", 1, 8),
            "lookback_bars":    trial.suggest_int("lookback_bars", 10, 60),
            "stop_loss_pct":    trial.suggest_float("stop_loss_pct", 0.01, 0.05),
            "take_profit_pct":  trial.suggest_float("take_profit_pct", 0.02, 0.10),
        }

    @staticmethod
    def mean_reversion_space(trial: Any) -> dict:
        """Return a config dict with Optuna-suggested values for MeanReversionStrategy."""
        return {
            "lookback_bars":        trial.suggest_int("lookback_bars", 10, 40),
            "entry_z_score":        trial.suggest_float("entry_z_score", 1.5, 3.0),
            "exit_z_score":         trial.suggest_float("exit_z_score", 0.1, 0.8),
            "stop_atr_multiplier":  trial.suggest_float("stop_atr_multiplier", 1.0, 4.0),
            "bollinger_std":        trial.suggest_float("bollinger_std", 1.5, 2.5),
        }

    @staticmethod
    def kelly_vol_space(trial: Any) -> dict:
        """Return a config dict with Optuna-suggested values for KellyVolStrategy."""
        return {
            "vol_target_pct":   trial.suggest_float("vol_target_pct", 0.08, 0.25),
            "lookback_bars":    trial.suggest_int("lookback_bars", 20, 120),
            "kelly_fraction":   trial.suggest_float("kelly_fraction", 0.1, 0.5),
            "min_edge_pct":     trial.suggest_float("min_edge_pct", 0.001, 0.01),
            "rebalance_bars":   trial.suggest_int("rebalance_bars", 1, 10),
        }

    @staticmethod
    def kalman_trend_space(trial: Any) -> dict:
        """Return a config dict with Optuna-suggested values for KalmanTrendStrategy."""
        return {
            "observation_noise": trial.suggest_float("observation_noise", 0.1, 5.0, log=True),
            "process_noise":     trial.suggest_float("process_noise", 0.001, 0.5, log=True),
            "entry_threshold":   trial.suggest_float("entry_threshold", 0.5, 3.0),
            "exit_threshold":    trial.suggest_float("exit_threshold", 0.1, 1.0),
        }

    @staticmethod
    def vwap_reversion_space(trial: Any) -> dict:
        """Return a config dict with Optuna-suggested values for VWAPReversionStrategy."""
        return {
            "vwap_window":      trial.suggest_int("vwap_window", 10, 40),
            "entry_band_pct":   trial.suggest_float("entry_band_pct", 0.002, 0.015),
            "exit_band_pct":    trial.suggest_float("exit_band_pct", 0.0005, 0.005),
            "atr_window":       trial.suggest_int("atr_window", 7, 21),
            "max_atr_pct":      trial.suggest_float("max_atr_pct", 0.01, 0.05),
        }
