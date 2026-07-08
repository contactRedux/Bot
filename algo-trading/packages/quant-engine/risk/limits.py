"""
risk/limits.py — RiskLimits: configuration dataclass for all risk constraints.

Design philosophy
-----------------
Every hard constraint the risk system can enforce lives here as a single
typed dataclass.  This makes limits explicit, diffable in version control,
and loadable from YAML/JSON without touching any risk logic.

The limits are checked in order of severity in ``RiskManager.check_order()``:

1. ``max_position_pct``      — per-asset position cap (most common breach)
2. ``max_strategy_allocation``— per-strategy capital cap
3. ``max_daily_loss_pct``    — intraday circuit-breaker
4. ``max_drawdown_pct``      — peak-to-trough halt threshold
5. ``max_correlation_concentration`` — concentration guard

Defaults
--------
The defaults below are deliberately conservative — suitable for a retail
account where capital preservation matters more than maximising returns.

Usage
-----
::

    from risk.limits import RiskLimits

    # Default conservative limits
    limits = RiskLimits()

    # Aggressive intraday limits
    limits = RiskLimits(
        max_position_pct=0.20,
        max_strategy_allocation=0.40,
        max_daily_loss_pct=0.03,
        max_drawdown_pct=0.20,
    )

    # Load from a dict (e.g. from YAML)
    limits = RiskLimits.from_dict(config["risk"])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskLimits:
    """
    Configuration for all risk constraints enforced by RiskManager.

    All percentage fields are fractions, not percentages
    (e.g. 0.10 = 10%, NOT 10).

    Attributes
    ----------
    max_position_pct : float
        Maximum single-asset position as a fraction of total portfolio value.
        e.g. 0.10 → no single position may exceed 10% of capital.
        Default: 0.10 (10%).

    max_strategy_allocation : float
        Maximum fraction of capital allocated to any single strategy's
        open positions combined.  Prevents one strategy from dominating.
        Default: 0.30 (30%).

    max_drawdown_pct : float
        Portfolio-level halt threshold.  If peak-to-trough drawdown exceeds
        this fraction, trading is halted until manual review.
        Default: 0.20 (20%).

    max_daily_loss_pct : float
        Maximum allowed portfolio loss in a single calendar day.  If the
        day's PnL drops below ``-initial_equity × max_daily_loss_pct``,
        all new orders are rejected for the rest of the day.
        Default: 0.02 (2%).

    max_correlation_concentration : float
        Maximum pairwise Pearson correlation between any two held assets
        before the concentration flag is raised.  Does not block orders
        outright — it scales down position sizes for correlated pairs.
        Default: 0.70 (correlation ≥ 0.70 triggers concentration warning).

    min_order_quantity : float
        Minimum order quantity to prevent dust orders from cluttering the
        trade log.  Orders below this threshold are rejected.
        Default: 1e-4 units.

    max_open_orders : int
        Maximum number of simultaneous open (pending limit) orders.
        Prevents runaway order accumulation during illiquid periods.
        Default: 50.

    var_confidence_level : float
        Confidence level for VaR/CVaR computation (e.g. 0.95 → 95% VaR).
        Default: 0.95 (5th percentile loss).

    var_window_days : int
        Rolling window length for historical VaR simulation.
        Default: 252 (one trading year).

    enabled : bool
        Master kill switch.  If False, RiskManager approves all orders
        without checking any limits (use only in controlled test scenarios).
        Default: True.
    """

    # ── Position limits ───────────────────────────────────────────────────────
    max_position_pct: float = 0.10
    max_strategy_allocation: float = 0.30

    # ── Loss / drawdown limits ────────────────────────────────────────────────
    max_drawdown_pct: float = 0.20
    max_daily_loss_pct: float = 0.02

    # ── Correlation concentration ─────────────────────────────────────────────
    max_correlation_concentration: float = 0.70

    # ── Order hygiene ─────────────────────────────────────────────────────────
    min_order_quantity: float = 1e-4
    max_open_orders: int = 50

    # ── VaR parameters ────────────────────────────────────────────────────────
    var_confidence_level: float = 0.95
    var_window_days: int = 252

    # ── Master switch ─────────────────────────────────────────────────────────
    enabled: bool = True

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Basic sanity checks on limit values."""
        assert 0 < self.max_position_pct <= 1.0, \
            f"max_position_pct must be in (0, 1], got {self.max_position_pct}"
        assert 0 < self.max_strategy_allocation <= 1.0, \
            f"max_strategy_allocation must be in (0, 1], got {self.max_strategy_allocation}"
        assert 0 < self.max_drawdown_pct <= 1.0, \
            f"max_drawdown_pct must be in (0, 1], got {self.max_drawdown_pct}"
        assert 0 < self.max_daily_loss_pct <= 1.0, \
            f"max_daily_loss_pct must be in (0, 1], got {self.max_daily_loss_pct}"
        assert 0 < self.max_correlation_concentration <= 1.0, \
            f"max_correlation_concentration must be in (0, 1], got {self.max_correlation_concentration}"
        assert 0 < self.var_confidence_level < 1.0, \
            f"var_confidence_level must be in (0, 1), got {self.var_confidence_level}"
        assert self.var_window_days >= 30, \
            f"var_window_days must be >= 30, got {self.var_window_days}"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RiskLimits":
        """Construct from a plain dict (e.g. loaded from YAML)."""
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        import dataclasses
        return dataclasses.asdict(self)
