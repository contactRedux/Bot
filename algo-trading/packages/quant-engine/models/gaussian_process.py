"""
models/gaussian_process.py — GPyTorch Gaussian Process regression model.

Why a GP for trading signals?
-------------------------------
Gaussian Processes are unique among the model types here because they provide
*calibrated uncertainty estimates*.  A GP doesn't just predict a signal — it
predicts a distribution over possible signals (mean + variance).  This lets us:

1. Set ``confidence = 1 / (1 + variance)`` — automatically reduce position size
   in regimes where the model is uncertain (e.g. during earnings releases,
   market stress, or in feature regions not seen in training).
2. Use the GP as a natural "when not to trade" detector: very high variance
   predictions are filtered out by the risk layer.

Architecture
------------
* Kernel: RBF (Squared Exponential) × Scale kernel.  Captures smooth,
  stationary relationships between feature vectors and returns.
* Likelihood: Gaussian (additive noise).
* Inference: Exact GP (works well for up to ~5000 training points; for larger
  datasets switch to ApproximateGP with inducing points).
* Optimization: Adam on marginal log-likelihood (type-II MLE).

Confidence mapping
------------------
GP predictive variance σ² grows with distance from training data.  We map:

    confidence = 1 / (1 + σ²)

When σ² ≈ 0 (near a training point), confidence → 1.
When σ² → ∞ (far from training), confidence → 0.

Usage
-----
::

    from models.gaussian_process import GaussianProcessModel

    model = GaussianProcessModel(input_dim=60, n_train_iters=100)
    model.train(X_train, y_train)
    out = model.predict(X_latest_row)
    print(out.signal, out.confidence)
    print(out.metadata["variance"])  # raw GP variance
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import torch
    import gpytorch
    from gpytorch.models import ExactGP
    from gpytorch.likelihoods import GaussianLikelihood
    from gpytorch.means import ConstantMean
    from gpytorch.kernels import RBFKernel, ScaleKernel
    from gpytorch.distributions import MultivariateNormal
    from gpytorch.mlls import ExactMarginalLogLikelihood
    _GPYTORCH_AVAILABLE = True
except ImportError:
    _GPYTORCH_AVAILABLE = False
    logger.warning("GPyTorch not installed — GaussianProcessModel unavailable. pip install gpytorch")

from models.base import BaseSignalModel, SignalOutput


# ---------------------------------------------------------------------------
# GP model definition (only when gpytorch is available)
# ---------------------------------------------------------------------------

if _GPYTORCH_AVAILABLE:
    class _ExactGPModel(ExactGP):
        """
        Single-output GP with RBF × Scale kernel and constant mean.

        The RBF kernel: k(x, x') = σ² * exp(-‖x−x'‖² / 2l²)
        captures the intuition that similar feature vectors should produce
        similar returns.  The lengthscale l is learned from data.
        """

        def __init__(
            self,
            train_x: "torch.Tensor",
            train_y: "torch.Tensor",
            likelihood: "GaussianLikelihood",
        ) -> None:
            super().__init__(train_x, train_y, likelihood)
            self.mean_module = ConstantMean()
            self.covar_module = ScaleKernel(RBFKernel())

        def forward(self, x: "torch.Tensor") -> "MultivariateNormal":
            mean_x = self.mean_module(x)
            covar_x = self.covar_module(x)
            return MultivariateNormal(mean_x, covar_x)


# ---------------------------------------------------------------------------
# GaussianProcessModel
# ---------------------------------------------------------------------------

class GaussianProcessModel(BaseSignalModel):
    """
    GPyTorch Gaussian Process regression for uncertainty-aware signal generation.

    Parameters
    ----------
    input_dim : int
        Number of input features.
    n_train_iters : int
        Number of Adam iterations for hyperparameter optimization.
    lr : float
        Adam learning rate for GP hyperparameters.
    max_train_samples : int
        Cap on training set size (GP is O(n³)).  If more data is supplied,
        the most recent ``max_train_samples`` rows are used.
    noise_prior : float
        Initial noise variance for the Gaussian likelihood.
    device : str or None
        Device string. Auto-detected if None.
    """

    def __init__(
        self,
        input_dim: int,
        n_train_iters: int = 100,
        lr: float = 0.1,
        max_train_samples: int = 2000,
        noise_prior: float = 0.1,
        device: str | None = None,
    ) -> None:
        super().__init__(input_dim)
        if not _GPYTORCH_AVAILABLE:
            raise ImportError("gpytorch is required. Install with: pip install gpytorch")

        self.n_train_iters = n_train_iters
        self.lr = lr
        self.max_train_samples = max_train_samples
        self.noise_prior = noise_prior

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)

        # These are set during train()
        self._gp: "_ExactGPModel | None" = None
        self._likelihood: "GaussianLikelihood | None" = None

    @property
    def model_id(self) -> str:
        return "gaussian_process"

    # ── Training ─────────────────────────────────────────────────────────────

    def train(
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        **kwargs: Any,
    ) -> None:
        """
        Optimize GP hyperparameters (kernel lengthscale, noise) via type-II MLE.

        For GP tractability, uses only the most recent ``max_train_samples`` rows.
        This is appropriate for walk-forward training where older data is less
        informative about the current market regime.
        """
        X_arr = self._to_numpy(X)
        y_arr = self._to_numpy(y)

        # Cap training size
        if len(X_arr) > self.max_train_samples:
            X_arr = X_arr[-self.max_train_samples:]
            y_arr = y_arr[-self.max_train_samples:]

        train_x = torch.tensor(X_arr, dtype=torch.float32, device=self.device)
        train_y = torch.tensor(y_arr, dtype=torch.float32, device=self.device)

        likelihood = GaussianLikelihood().to(self.device)
        likelihood.noise = torch.tensor(self.noise_prior, device=self.device)
        gp = _ExactGPModel(train_x, train_y, likelihood).to(self.device)

        gp.train()
        likelihood.train()

        optimizer = torch.optim.Adam(gp.parameters(), lr=self.lr)
        mll = ExactMarginalLogLikelihood(likelihood, gp)

        for i in range(self.n_train_iters):
            optimizer.zero_grad()
            output = gp(train_x)
            loss = -mll(output, train_y)
            loss.backward()
            optimizer.step()
            if (i + 1) % 20 == 0:
                logger.debug("GP iter %d/%d loss=%.4f", i + 1, self.n_train_iters, loss.item())

        gp.eval()
        likelihood.eval()

        self._gp = gp
        self._likelihood = likelihood
        self._is_trained = True

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame | np.ndarray, **kwargs: Any) -> SignalOutput:
        """
        Predict with GP posterior.

        Parameters
        ----------
        X : array, shape (n, input_dim) or (input_dim,)
            Feature vector(s).  Uses only the last row for the signal.
        """
        self._assert_trained()
        X_arr = self._to_numpy(X)
        if X_arr.ndim == 1:
            X_arr = X_arr[np.newaxis, :]
        # Use last row
        x = torch.tensor(X_arr[[-1]], dtype=torch.float32, device=self.device)

        with torch.no_grad(), gpytorch.settings.fast_pred_var():
            pred = self._likelihood(self._gp(x))

        mean = pred.mean.item()
        variance = pred.variance.item()

        # Map mean to [-1, 1] via tanh (raw GP mean is an unbounded regression target)
        signal = float(np.tanh(mean))
        # Confidence: inversely proportional to variance
        confidence = float(1.0 / (1.0 + variance))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        return SignalOutput(
            signal=signal,
            confidence=confidence,
            model_id=self.model_id,
            metadata={"gp_mean": mean, "variance": variance},
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Save GP state dict and hyperparameters."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self._gp.state_dict(), path / "gp_state.pt")
        torch.save(self._likelihood.state_dict(), path / "likelihood_state.pt")
        # Save training data (needed to restore ExactGP)
        torch.save(
            {
                "train_x": self._gp.train_inputs[0],
                "train_y": self._gp.train_targets,
            },
            path / "train_data.pt",
        )
        meta = {
            "input_dim": self.input_dim,
            "n_train_iters": self.n_train_iters,
            "lr": self.lr,
            "max_train_samples": self.max_train_samples,
            "noise_prior": self.noise_prior,
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        logger.info("GaussianProcessModel saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "GaussianProcessModel":
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        model = cls(**meta)

        train_data = torch.load(path / "train_data.pt", map_location=model.device, weights_only=True)
        likelihood = GaussianLikelihood().to(model.device)
        gp = _ExactGPModel(train_data["train_x"], train_data["train_y"], likelihood).to(model.device)

        gp.load_state_dict(torch.load(path / "gp_state.pt", map_location=model.device, weights_only=True))
        likelihood.load_state_dict(torch.load(path / "likelihood_state.pt", map_location=model.device, weights_only=True))

        gp.eval()
        likelihood.eval()

        model._gp = gp
        model._likelihood = likelihood
        model._is_trained = True
        return model

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_numpy(arr: Any) -> np.ndarray:
        if isinstance(arr, (pd.DataFrame, pd.Series)):
            return arr.to_numpy(dtype=np.float32)
        return np.asarray(arr, dtype=np.float32)
