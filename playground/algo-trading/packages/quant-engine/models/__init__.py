"""
models package — ML/DL signal models.

Sub-modules
-----------
models.base              — BaseSignalModel interface + SignalOutput dataclass
models.lstm_forecaster   — PyTorch LSTM sequence model
models.transformer_signal — Transformer encoder signal model
models.gaussian_process  — GPyTorch GP for uncertainty-aware predictions
models.gradient_boosting — LightGBM tabular model with SHAP interpretability
models.rl_agent          — PPO reinforcement learning agent (stable-baselines3)
models.ensemble          — Meta-learner combining all model outputs
models.registry          — ModelRegistry for artifact save/load
models.training          — Walk-forward CV and training utilities
"""

from models.base import BaseSignalModel, SignalOutput

__all__ = ["BaseSignalModel", "SignalOutput"]
