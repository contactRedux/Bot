"""
risk/correlation.py — Correlation concentration detection.

Why correlation matters for risk
----------------------------------
Diversification only works when assets move independently.  Holding AAPL and
MSFT feels like two positions, but during a broad tech sell-off they fall in
unison — the diversification benefit disappears exactly when you need it most.

The correlation concentration check identifies pairs of held assets whose
returns are highly correlated.  When correlation exceeds a threshold, the risk
manager scales down orders in the correlated names to prevent the portfolio
from behaving like a single concentrated bet.

Measuring correlation
---------------------
We use Pearson correlation on rolling daily returns:

    ρ(A, B) = cov(r_A, r_B) / (σ_A × σ_B)

where ``r_X`` is the vector of daily returns for asset X over the last
``window`` bars.

A |ρ| > 0.70 threshold means the assets share 70% of their variance
(R² = ρ² = 49%), which is strong enough to be economically meaningful.

Concentration score
-------------------
The CorrelationResult reports:

1. The full pairwise correlation matrix (as a dict of dicts).
2. All pairs exceeding the threshold, sorted by |ρ| descending.
3. A per-asset *concentration score*: the maximum pairwise correlation
   this asset has with any other held asset.  Assets with high scores
   have their position sizes scaled down proportionally.

Usage
-----
::

    from risk.correlation import CorrelationChecker

    checker = CorrelationChecker(window=60, threshold=0.70)

    # price_history: dict[str, list[float]] — recent close prices per ticker
    result = checker.check(price_history)

    if result.has_concentration:
        for pair in result.concentrated_pairs:
            print(pair.ticker_a, pair.ticker_b, pair.correlation)

    # Per-asset scale factor in [0, 1] — multiply order quantity by this
    scale = result.scale_factor("AAPL")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class CorrelatedPair:
    """A pair of assets with above-threshold correlation."""
    ticker_a: str
    ticker_b: str
    correlation: float   # signed Pearson r ∈ [-1, 1]

    @property
    def abs_correlation(self) -> float:
        return abs(self.correlation)


@dataclass
class CorrelationResult:
    """
    Result of a correlation concentration check.

    Attributes
    ----------
    matrix : dict[str, dict[str, float]]
        Full pairwise correlation matrix.
    concentrated_pairs : list[CorrelatedPair]
        Pairs with |ρ| ≥ threshold, sorted by |ρ| descending.
    threshold : float
        The concentration threshold used.
    tickers : list[str]
        All assets included in the check.
    """

    matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    concentrated_pairs: list[CorrelatedPair] = field(default_factory=list)
    threshold: float = 0.70
    tickers: list[str] = field(default_factory=list)

    @property
    def has_concentration(self) -> bool:
        return len(self.concentrated_pairs) > 0

    def scale_factor(self, ticker: str) -> float:
        """
        Return a position scale-down factor in (0, 1] for a ticker.

        Logic:  if this asset's maximum pairwise correlation is ρ_max,
        the scale factor is ``(1 - (ρ_max - threshold))`` clamped to
        [0.25, 1.0].  Extreme correlation (ρ = 1.0) gives a 0.25 factor
        (75% position reduction); at threshold the factor is 1.0 (no reduction).
        """
        max_corr = 0.0
        for pair in self.concentrated_pairs:
            if ticker in (pair.ticker_a, pair.ticker_b):
                max_corr = max(max_corr, pair.abs_correlation)
        if max_corr <= self.threshold:
            return 1.0
        excess = max_corr - self.threshold
        return max(0.25, 1.0 - excess)

    def summary(self) -> str:
        if not self.has_concentration:
            return "No correlation concentration detected."
        lines = [f"Correlation concentration (threshold={self.threshold:.2f}):"]
        for p in self.concentrated_pairs:
            lines.append(f"  {p.ticker_a} ↔ {p.ticker_b}: ρ = {p.correlation:+.3f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CorrelationChecker
# ---------------------------------------------------------------------------

class CorrelationChecker:
    """
    Computes rolling pairwise correlations for held assets.

    Parameters
    ----------
    window : int
        Number of recent price observations to use for correlation
        estimation.  Default 60 (≈ 3 months of daily bars).
    threshold : float
        |ρ| threshold above which a pair is flagged as concentrated.
        Default 0.70.
    """

    def __init__(self, window: int = 60, threshold: float = 0.70) -> None:
        if window < 10:
            raise ValueError(f"Correlation window must be >= 10, got {window}")
        self.window = window
        self.threshold = threshold

    def check(
        self,
        price_history: dict[str, Sequence[float]],
    ) -> CorrelationResult:
        """
        Compute pairwise correlations for all provided assets.

        Parameters
        ----------
        price_history : dict[str, Sequence[float]]
            Maps ticker → list of recent close prices (newest last).
            Assets with fewer than 10 observations are skipped.

        Returns
        -------
        CorrelationResult
        """
        tickers = [t for t, prices in price_history.items() if len(prices) >= 10]

        if len(tickers) < 2:
            return CorrelationResult(tickers=tickers, threshold=self.threshold)

        # Build return matrix
        returns: dict[str, np.ndarray] = {}
        for ticker in tickers:
            prices = np.asarray(price_history[ticker][-self.window - 1:], dtype=np.float64)
            if len(prices) >= 2:
                with np.errstate(divide="ignore", invalid="ignore"):
                    r = np.diff(prices) / prices[:-1]
                    r = np.where(np.isfinite(r), r, 0.0)
                returns[ticker] = r

        valid_tickers = [t for t in tickers if t in returns]
        if len(valid_tickers) < 2:
            return CorrelationResult(tickers=valid_tickers, threshold=self.threshold)

        # Align lengths (take common minimum)
        min_len = min(len(r) for r in returns.values())
        if min_len < 5:
            return CorrelationResult(tickers=valid_tickers, threshold=self.threshold)

        aligned = {t: returns[t][-min_len:] for t in valid_tickers}

        # Compute Pearson correlations
        matrix: dict[str, dict[str, float]] = {t: {} for t in valid_tickers}
        concentrated_pairs: list[CorrelatedPair] = []

        for i, t_a in enumerate(valid_tickers):
            for t_b in valid_tickers[i:]:
                if t_a == t_b:
                    matrix[t_a][t_b] = 1.0
                    continue
                r_a = aligned[t_a]
                r_b = aligned[t_b]
                corr = _pearson(r_a, r_b)
                matrix[t_a][t_b] = round(corr, 4)
                matrix[t_b][t_a] = round(corr, 4)
                if abs(corr) >= self.threshold:
                    concentrated_pairs.append(
                        CorrelatedPair(ticker_a=t_a, ticker_b=t_b, correlation=corr)
                    )

        # Fill self-correlation
        for t in valid_tickers:
            matrix[t][t] = 1.0

        concentrated_pairs.sort(key=lambda p: p.abs_correlation, reverse=True)

        if concentrated_pairs:
            logger.warning(
                "CorrelationChecker: %d concentrated pair(s) detected (threshold=%.2f): %s",
                len(concentrated_pairs),
                self.threshold,
                [(p.ticker_a, p.ticker_b, f"{p.correlation:.3f}") for p in concentrated_pairs],
            )

        return CorrelationResult(
            matrix=matrix,
            concentrated_pairs=concentrated_pairs,
            threshold=self.threshold,
            tickers=valid_tickers,
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Numerically stable Pearson r between two equal-length arrays."""
    if len(a) < 3:
        return 0.0
    # Use np.corrcoef for correct, consistent Pearson r (sample-based)
    result = np.corrcoef(a, b)
    r = float(result[0, 1])
    if not np.isfinite(r):
        return 0.0
    return r
