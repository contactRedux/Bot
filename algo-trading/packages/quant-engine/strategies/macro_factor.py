"""
strategies/macro_factor.py — Macro regime meta-strategy.

Role in the system
-------------------
The macro factor strategy is a *meta-strategy*: it does not generate signals
based on individual asset price action.  Instead, it monitors the macro-economic
environment and:

1. **Scales all strategy allocation weights** based on the current regime.
   In a fear regime (high VIX), all strategies reduce their position sizes.
   In a normal regime, weights are unchanged.

2. **Generates earnings momentum signals** when a company reports a significant
   earnings surprise (actual EPS vs. consensus estimate > 2σ).

3. **Flags yield curve inversion** and reduces equity exposure.

The ``StrategyOrchestrator`` reads the regime from this strategy via
``get_regime_multiplier()`` before computing final order sizes.

Macro regime classification
-----------------------------
Three regimes based on VIX level and yield curve slope:

    RISK_ON   — VIX < vix_fear_threshold AND yield curve not inverted
               → all strategies at full allocation
    RISK_OFF  — VIX > vix_fear_threshold OR yield curve inverted
               → all strategies scaled by fear_reduction_factor (default 0.5)
    CRISIS    — VIX > 40 (extreme fear)
               → all strategies scaled by 0.25 (75% reduction)

Yield curve inversion
---------------------
When the 10Y − 2Y spread < 0, the yield curve is inverted.  This has
preceded every US recession since 1955 (with 6–18 month lag).  In the
short term, inversion typically signals:
    - Risk-off sentiment (sell equities, buy bonds/gold)
    - Reduced credit availability
    - Eventual rate cuts by the Fed

Earnings surprise
------------------
We compute:
    earnings_surprise_z = (EPS_actual − EPS_consensus) / rolling_std(EPS_surprise)

If |z| > earnings_surprise_z threshold, we emit a momentum signal in the
direction of the surprise:
    + surprise → BUY (post-earnings drift)
    − surprise → SELL (negative post-earnings drift)

Configuration (strategy_config.yaml)
-------------------------------------
    vix_fear_threshold           : VIX level defining fear regime (default 25)
    fear_reduction_factor        : allocation multiplier in fear (default 0.5)
    yield_curve_inversion_threshold : yield curve slope cutoff   (default 0.0)
    equity_reduction_on_inversion   : equity position reduction  (default 0.40)
    earnings_surprise_z          : surprise z-score threshold    (default 2.0)
    regime_update_interval_bars  : bars between regime updates   (default 4)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Order, OrderSide, OrderType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regime enum
# ---------------------------------------------------------------------------

class MacroRegime(str, Enum):
    RISK_ON  = "risk_on"
    RISK_OFF = "risk_off"
    CRISIS   = "crisis"


# ---------------------------------------------------------------------------
# MacroFactorStrategy
# ---------------------------------------------------------------------------

class MacroFactorStrategy(BaseStrategy):
    """
    Macro regime monitor and earnings momentum signal generator.

    Parameters
    ----------
    config : dict
        From strategy_config.yaml ``macro_factor`` section.
    tickers : list[str]
        Equities to watch for earnings surprises.
    """

    def __init__(
        self,
        config: dict[str, Any],
        tickers: list[str],
        base_position_size: float = 30.0,
    ) -> None:
        super().__init__("macro_factor", config, tickers)
        self.base_position_size = base_position_size

        self.vix_threshold: float = config.get("vix_fear_threshold", 25.0)
        self.fear_factor: float = config.get("fear_reduction_factor", 0.50)
        self.yield_inv_threshold: float = config.get("yield_curve_inversion_threshold", 0.0)
        self.equity_reduction: float = config.get("equity_reduction_on_inversion", 0.40)
        self.earnings_z_threshold: float = config.get("earnings_surprise_z", 2.0)
        self.regime_update_interval: int = int(config.get("regime_update_interval_bars", 4))

        # Current regime state
        self._regime: MacroRegime = MacroRegime.RISK_ON
        self._latest_vix: float = 15.0
        self._yield_curve_slope: float = 1.0  # 10Y − 2Y spread
        self._yield_curve_inverted: bool = False

        # Earnings surprise history per ticker (for rolling z-score)
        self._surprise_history: dict[str, list[float]] = {t: [] for t in tickers}

        self._pending_orders: list[Order] = []

    # ── Public regime interface ───────────────────────────────────────────────

    def get_regime(self) -> MacroRegime:
        """Return the current macro regime (read by orchestrator)."""
        return self._regime

    def get_regime_multiplier(self) -> float:
        """
        Return the allocation multiplier the orchestrator should apply to all strategies.

        Returns
        -------
        float
            1.0 in RISK_ON, fear_factor in RISK_OFF, 0.25 in CRISIS.
        """
        if self._regime == MacroRegime.CRISIS:
            return 0.25
        if self._regime == MacroRegime.RISK_OFF:
            return self.fear_factor
        return 1.0

    def get_equity_multiplier(self) -> float:
        """
        Return the equity-specific allocation multiplier.

        In a yield curve inversion, equity strategies are further reduced.
        """
        base = self.get_regime_multiplier()
        if self._yield_curve_inverted:
            base *= (1.0 - self.equity_reduction)
        return base

    # ── Event hooks ──────────────────────────────────────────────────────────

    def on_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> None:
        if not self._enabled:
            return

        # ── Regime update (not every bar — reduces computation overhead) ─────
        if self._bar_count % self.regime_update_interval == 0:
            self._update_regime(features)

        # ── Earnings surprise signal ──────────────────────────────────────────
        # Only emit if fundamentals were recently updated (via on_fundamental)
        state = self._state_for(ticker)
        if state.extra.get("new_earnings"):
            surprise_z = state.extra.pop("new_earnings")
            self._emit_earnings_signal(ticker, surprise_z, features)

        self._bar_count += 1

    def on_fundamental(self, ticker: str, snapshot: Any) -> None:
        """
        Called when a new FundamentalSnapshot arrives.
        Computes earnings surprise z-score and stashes it in state for on_bar.
        """
        if not self._enabled:
            return

        eps_actual = getattr(snapshot, "eps_reported", None)
        eps_consensus = getattr(snapshot, "eps_consensus", None)
        if eps_actual is None or eps_consensus is None:
            return

        surprise = float(eps_actual - eps_consensus)
        hist = self._surprise_history.setdefault(ticker, [])
        hist.append(surprise)
        if len(hist) > 20:
            hist.pop(0)

        if len(hist) < 3:
            return

        mu = np.mean(hist[:-1])
        std = np.std(hist[:-1]) + 1e-6
        z = float((surprise - mu) / std)

        if abs(z) > self.earnings_z_threshold:
            # Stash in state for emission in on_bar (where we have close price)
            self._state_for(ticker).extra["new_earnings"] = z

    def generate_orders(self) -> list[Order]:
        orders = list(self._pending_orders)
        self._pending_orders.clear()
        return orders

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _update_regime(self, features: pd.DataFrame) -> None:
        """
        Classify macro regime from VIX and yield curve features.
        Looks for macro feature columns added by features/macro.py.
        """
        # VIX
        for col in ("vix", "vix_level", "^vix"):
            val = self._get_feature(features, col)
            if val is not None:
                self._latest_vix = val
                break

        # Yield curve slope
        for col in ("yield_curve_slope", "yield_10y_2y", "yield_spread"):
            val = self._get_feature(features, col)
            if val is not None:
                self._yield_curve_slope = val
                self._yield_curve_inverted = val < self.yield_inv_threshold
                break

        old_regime = self._regime

        if self._latest_vix > 40.0:
            self._regime = MacroRegime.CRISIS
        elif self._latest_vix > self.vix_threshold or self._yield_curve_inverted:
            self._regime = MacroRegime.RISK_OFF
        else:
            self._regime = MacroRegime.RISK_ON

        if self._regime != old_regime:
            logger.info(
                "MacroFactor regime changed: %s → %s (VIX=%.1f yield_slope=%.3f)",
                old_regime.value, self._regime.value,
                self._latest_vix, self._yield_curve_slope,
            )

    def _emit_earnings_signal(
        self, ticker: str, surprise_z: float, features: pd.DataFrame
    ) -> None:
        """
        Emit a short-term momentum order based on earnings surprise.

        Post-earnings drift (PEAD): stocks that beat earnings tend to
        continue outperforming for 30–60 days.  This is one of the most
        well-documented anomalies in academic finance.
        """
        side = OrderSide.BUY if surprise_z > 0 else OrderSide.SELL
        confidence = min(1.0, abs(surprise_z) / (self.earnings_z_threshold * 2))
        qty = self.base_position_size * confidence

        self._pending_orders.append(self._make_order(
            ticker, side, qty, confidence,
            earnings_surprise_z=round(surprise_z, 3),
            regime=self._regime.value,
        ))
        logger.info(
            "MacroFactor [%s] earnings signal %s z=%.2f regime=%s",
            ticker, side.value, surprise_z, self._regime.value,
        )

    @staticmethod
    def _get_feature(features: pd.DataFrame, col: str) -> float | None:
        if col in features.columns and len(features) > 0:
            val = features[col].iloc[-1]
            return None if pd.isna(val) else float(val)
        return None
