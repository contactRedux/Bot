# Gaussian Processes for Trading Signals

> **Code links:** [`models/gaussian_process.py`](../../models/gaussian_process.py)

---

## Table of Contents

1. [What Is a Gaussian Process?](#1-what-is-a-gaussian-process)
2. [Kernel Functions](#2-kernel-functions)
3. [GP Prior and Posterior](#3-gp-prior-and-posterior)
4. [Type-II Maximum Likelihood (Hyperparameter Learning)](#4-type-ii-maximum-likelihood-hyperparameter-learning)
5. [Uncertainty Quantification for Position Sizing](#5-uncertainty-quantification-for-position-sizing)
6. [Implementation with GPyTorch](#6-implementation-with-gpytorch)
7. [Limitations in High-Dimensional Financial Data](#7-limitations-in-high-dimensional-financial-data)

---

## 1. What Is a Gaussian Process?

A **Gaussian Process (GP)** is a probability distribution over *functions*. Rather than learning a single function `f(x)`, a GP maintains a distribution over all functions consistent with the observed data.

Formally, a GP is defined by its **mean function** `m(x)` and **covariance (kernel) function** `k(x, x')`:

```
f(x) ~ GP( m(x),  k(x, x') )
```

The key property: any finite collection of function values `[f(x_1), ..., f(x_n)]` follows a **multivariate Gaussian distribution**:

```
[f(x_1), ..., f(x_n)]^T ~ N( [m(x_1),...,m(x_n)],  K )

where K_{ij} = k(x_i, x_j)
```

**Why this is useful for trading:** The GP doesn't just predict the next return — it also gives you a *calibrated uncertainty estimate* around that prediction. High uncertainty → small position. Low uncertainty → full conviction.

---

## 2. Kernel Functions

The kernel `k(x, x')` encodes our beliefs about the *smoothness* and *structure* of the function.

### RBF (Radial Basis Function / Squared Exponential) Kernel

```
k_RBF(x, x') = σ_f² · exp( -||x - x'||² / (2ℓ²) )
```

- `σ_f²` — signal variance (output scale)
- `ℓ` — length scale: how quickly the function changes with input distance

The RBF kernel produces infinitely differentiable functions — very smooth. It assumes the function's influence decays exponentially with input distance.

### Scale Kernel

```
k(x, x') = σ_f² · k_base(x, x')
```

A multiplicative constant that scales the overall output variance.

### RBF × Scale Kernel (used in `models/gaussian_process.py`)

Combining RBF with a scale kernel gives both learnable amplitude and length scale — the two key hyperparameters in `models/gaussian_process.py`.

### Other kernels (not implemented but worth knowing):
- **Matérn** — less smooth than RBF; better for non-differentiable financial time series
- **Periodic** — for seasonal patterns
- **Linear** — encodes linear trends; equivalent to Bayesian linear regression

---

## 3. GP Prior and Posterior

**Prior:** Before seeing data, the GP prior encodes beliefs about plausible functions via the kernel.

**Posterior:** After observing training data `D = {(x_i, y_i)}`, the posterior is obtained by conditioning the prior on the data using Bayes' rule. For Gaussian noise `y = f(x) + ε, ε ~ N(0, σ_n²)`:

**Posterior mean (predictive mean):**

```
μ*(x*) = m(x*) + K(x*, X) · [K(X,X) + σ_n² I]^{-1} · (y - m(X))
```

**Posterior variance (predictive uncertainty):**

```
σ²*(x*) = k(x*, x*) - K(x*, X) · [K(X,X) + σ_n² I]^{-1} · K(X, x*)
```

Where:
- `x*` — test input (the feature vector for the next bar)
- `X` — training inputs
- `K(x*, X)` — vector of covariances between test point and training points

The posterior variance `σ²*(x*)` **decreases when the test point is similar to training points** — the model is more certain when it has seen similar market conditions before.

---

## 4. Type-II Maximum Likelihood (Hyperparameter Learning)

The kernel hyperparameters (`σ_f²`, `ℓ`, `σ_n²`) are learned by maximising the **log marginal likelihood** of the training data:

```
log p(y | X, θ) = -½ y^T [K + σ_n² I]^{-1} y
                  -½ log|K + σ_n² I|
                  - N/2 log(2π)
```

The first term rewards good data fit; the second is a complexity penalty (Occam's razor). This avoids overfitting automatically.

In `models/gaussian_process.py`, this is done via gradient descent using GPyTorch:

```python
# models/gaussian_process.py
optimizer = torch.optim.Adam(model.parameters(), lr=0.1)
mll = gpytorch.mlls.ExactMarginalLogLikelihood(likelihood, model)

for i in range(100):
    optimizer.zero_grad()
    output = model(train_x)
    loss = -mll(output, train_y)    # negative because we maximise
    loss.backward()
    optimizer.step()
```

---

## 5. Uncertainty Quantification for Position Sizing

The GP's posterior variance directly controls position size in the ensemble:

```python
# models/gaussian_process.py
mean, variance = predict(test_features)

# confidence = how certain we are about this prediction
confidence = 1.0 / (1.0 + variance)   # in (0, 1)

return SignalOutput(
    signal=float(torch.tanh(mean)),    # squash to [-1, +1]
    confidence=float(confidence),
    metadata={"gp_variance": float(variance)},
)
```

**Intuition:** `confidence = 1 / (1 + σ²)`:
- `σ² → 0` (very certain): confidence → 1.0 → full weight in ensemble
- `σ² → ∞` (very uncertain): confidence → 0.0 → GP signal down-weighted

The **ensemble meta-learner** (`models/ensemble.py`) uses these per-model confidence scores as input features, learning to weight the GP more in low-volatility regimes where it is calibrated well, and less in high-variance crisis periods.

---

## 6. Implementation with GPyTorch

GPyTorch uses PyTorch's GPU acceleration and autograd for scalable GP inference.

```python
class ReturnGP(gpytorch.models.ExactGP):
    def __init__(self, train_x, train_y, likelihood):
        super().__init__(train_x, train_y, likelihood)
        self.mean_module  = gpytorch.means.ConstantMean()
        self.covar_module = gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel()
        )

    def forward(self, x):
        mean  = self.mean_module(x)
        covar = self.covar_module(x)
        return gpytorch.distributions.MultivariateNormal(mean, covar)
```

**Computational complexity:** Exact GP inference requires inverting the `N × N` kernel matrix — O(N³). For N > 2,000 training points this is slow. The platform limits GP training to the most recent 500 bars (≈2 trading years of daily data). For larger datasets, GPyTorch supports approximate inference via inducing points.

---

## 7. Limitations in High-Dimensional Financial Data

1. **Dimensionality:** GPs scale poorly with input dimension `d`. The RBF kernel's single length scale `ℓ` assumes all dimensions are equally relevant — far from true for 40 technical indicators. Automatic Relevance Determination (ARD) kernel (separate `ℓ_d` per dimension) partially addresses this but adds more hyperparameters.

2. **Non-stationarity:** Financial markets are non-stationary — regimes change. The RBF kernel assumes stationarity (covariance depends only on distance, not position in time). The walk-forward training window in `models/training/walk_forward.py` partially compensates.

3. **Computational cost:** 500 training points, 40 features, 100 hyperparameter optimisation steps takes ~2 seconds on CPU. In a live pipeline, retrain nightly rather than on every bar.

4. **Calibration:** GP uncertainty estimates are only well-calibrated when the kernel matches the true data-generating process. Always validate calibration on held-out data: the 90% confidence interval should contain ≈90% of actual outcomes.
