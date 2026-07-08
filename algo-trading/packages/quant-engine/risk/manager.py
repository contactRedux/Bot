"""
risk/manager.py — RiskManager: vets and scales orders before execution.

Position in the system
-----------------------
The RiskManager sits between the StrategyOrchestrator and the ExecutionBroker:

    StrategyOrchestrator.process_bar()
        → [list[Order]]  (weight-scaled, aggregated)
        → RiskManager.check_order(order, portfolio)
        → OrderDecision (APPROVE / SCALE_DOWN / REJECT)
        → ExecutionBroker.submit_order()

Every order must pass through ``check_order()`` before it reaches execution.
The risk manager never modifies orders in place — it returns a decision object
that may contain a modified (scaled-down) version of the order.

Decision types
--------------
APPROVE     — Order passes all checks.  Send as-is.
SCALE_DOWN  — Order violates a position or strategy limit.  A reduced
              ``adjusted_order`` is returned that brings the position within
              limits.  The caller should submit the adjusted order.
REJECT      — Order is entirely blocked (e.g. daily loss limit hit, dust
              order, or halt active).  Do not submit.

Checks performed (in order of precedence)
------------------------------------------
1. Master halt (DrawdownMonitor.is_halted)  → REJECT immediately.
2. Dust order (qty < min_order_quantity)    → REJECT.
3. Max daily loss                           → REJECT.
4. Per-asset position limit                 → SCALE_DOWN or REJECT.
5. Per-strategy allocation limit            → SCALE_DOWN or REJECT.
6. Correlation concentration                → SCALE_DOWN (optional).

Usage
-----
::

    from risk.manager import RiskManager
    from risk.limits import RiskLimits

    risk = RiskManager(
        limits=RiskLimits(max_position_pct=0.10, max_drawdown_pct=0.20),
        total_capital=100_000.0,
    )
    risk.set_monitor(monitor)   # attach DrawdownMonitor

    decision = risk.check_order(order, portfolio)
    if decision.approved:
        broker.submit(decision.order)   # use decision.order (may be scaled)
    else:
        logger.info("Order rejected: %s", decision.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from risk.limits import RiskLimits

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision type
# ---------------------------------------------------------------------------

class OrderDecisionType(str, Enum):
    APPROVE = "approve"
    SCALE_DOWN = "scale_down"
    REJECT = "reject"


@dataclass
class OrderDecision:
    """
    Result of ``RiskManager.check_order()``.

    Attributes
    ----------
    decision : OrderDecisionType
        APPROVE, SCALE_DOWN, or REJECT.
    order : Any
        The (possibly adjusted) order.  Always set — for REJECT it is the
        original order (for logging purposes).
    reason : str
        Human-readable explanation for any non-APPROVE decision.
    original_qty : float
        Quantity of the original order.
    adjusted_qty : float
        Quantity after scaling (same as original_qty for APPROVE/REJECT).
    check_name : str
        Which risk check triggered the decision.
    timestamp : datetime
    """

    decision: OrderDecisionType
    order: Any
    reason: str = ""
    original_qty: float = 0.0
    adjusted_qty: float = 0.0
    check_name: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def approved(self) -> bool:
        """True if order should be submitted (APPROVE or SCALE_DOWN with qty > 0)."""
        return self.decision in (OrderDecisionType.APPROVE, OrderDecisionType.SCALE_DOWN)

    @property
    def rejected(self) -> bool:
        return self.decision == OrderDecisionType.REJECT

    @property
    def scaled(self) -> bool:
        return self.decision == OrderDecisionType.SCALE_DOWN


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Centralised risk gate between strategies and execution.

    Parameters
    ----------
    limits : RiskLimits
        All risk constraint parameters.
    total_capital : float
        Current total portfolio value (updated via ``update_capital()``).
    """

    def __init__(
        self,
        limits: RiskLimits | None = None,
        total_capital: float = 100_000.0,
    ) -> None:
        self.limits = limits or RiskLimits()
        self.total_capital = float(total_capital)
        self._monitor: Optional[Any] = None          # DrawdownMonitor
        self._correlation_checker: Optional[Any] = None  # CorrelationChecker
        self._price_history: dict[str, list[float]] = {}  # for correlation
        # Audit log: list of non-APPROVE decisions
        self._audit_log: list[dict[str, Any]] = []

    # ── External state setters ────────────────────────────────────────────

    def set_monitor(self, monitor: Any) -> None:
        """Attach a DrawdownMonitor for halt detection."""
        self._monitor = monitor

    def set_correlation_checker(self, checker: Any) -> None:
        """Attach a CorrelationChecker for concentration detection."""
        self._correlation_checker = checker

    def update_capital(self, total_capital: float) -> None:
        """Called after every fill to keep position-limit math current."""
        self.total_capital = float(total_capital)

    def update_price_history(self, ticker: str, prices: list[float]) -> None:
        """Feed recent prices for correlation computation."""
        self._price_history[ticker] = list(prices)

    # ── Main interface ────────────────────────────────────────────────────

    def check_order(
        self,
        order: Any,
        portfolio: Any,
    ) -> OrderDecision:
        """
        Gate an order through all risk checks.

        Parameters
        ----------
        order : Order
            From ``strategies/base.py``.
        portfolio : Portfolio
            The current ``backtesting/portfolio.py`` Portfolio instance
            (or any object with ``position(ticker)``, ``total_equity``,
            and ``strategy_pnl_attribution()`` methods).

        Returns
        -------
        OrderDecision
            Call ``decision.approved`` to determine whether to proceed.
            If ``decision.scaled``, submit ``decision.order`` (scaled qty).
        """
        if not self.limits.enabled:
            return OrderDecision(
                decision=OrderDecisionType.APPROVE,
                order=order,
                original_qty=order.quantity,
                adjusted_qty=order.quantity,
                check_name="disabled",
            )

        original_qty = order.quantity

        # ── 1. Halt check ─────────────────────────────────────────────────
        if self._monitor is not None and self._monitor.is_halted:
            return self._reject(order, original_qty, "Trading halted by DrawdownMonitor", "halt")

        # ── 2. Dust order ─────────────────────────────────────────────────
        if order.quantity < self.limits.min_order_quantity:
            return self._reject(
                order, original_qty,
                f"Dust order: qty {order.quantity:.6f} < min {self.limits.min_order_quantity}",
                "dust_order",
            )

        # ── 3. Daily loss check ───────────────────────────────────────────
        if self._monitor is not None:
            daily_loss = self._monitor._last_alert.daily_loss_pct if self._monitor._last_alert else 0.0
            if daily_loss >= self.limits.max_daily_loss_pct:
                return self._reject(
                    order, original_qty,
                    f"Daily loss {daily_loss:.2%} ≥ limit {self.limits.max_daily_loss_pct:.2%}",
                    "daily_loss",
                )

        # ── 4. Per-asset position limit ───────────────────────────────────
        ticker = order.ticker
        side = order.side.value if hasattr(order.side, "value") else str(order.side)
        current_qty = portfolio.position(ticker)
        price = self._price_history.get(ticker, [])
        mark_price = float(price[-1]) if price else 1.0

        max_qty = (self.total_capital * self.limits.max_position_pct) / max(mark_price, 1e-8)

        if side == "buy":
            room = max(0.0, max_qty - max(0.0, current_qty))
        else:
            room = max(0.0, max_qty + min(0.0, current_qty))

        if room < self.limits.min_order_quantity:
            return self._reject(
                order, original_qty,
                f"Position limit: {ticker} at cap "
                f"(current={current_qty:.2f} max_qty={max_qty:.2f})",
                "position_limit",
            )

        if order.quantity > room:
            scaled_order = self._scale_order(order, room)
            logger.info(
                "RiskManager: SCALE_DOWN %s %s %.2f → %.2f (position_limit)",
                side, ticker, original_qty, room,
            )
            self._log_decision(OrderDecisionType.SCALE_DOWN, order, original_qty, room, "position_limit")
            return OrderDecision(
                decision=OrderDecisionType.SCALE_DOWN,
                order=scaled_order,
                reason=f"Scaled to position limit (room={room:.2f})",
                original_qty=original_qty,
                adjusted_qty=room,
                check_name="position_limit",
            )

        # ── 5. Per-strategy allocation limit ─────────────────────────────
        strategy_id = order.strategy_id
        strategy_market_value = self._strategy_market_value(strategy_id, portfolio)
        max_strategy_value = self.total_capital * self.limits.max_strategy_allocation

        if strategy_market_value >= max_strategy_value and side == "buy":
            return self._reject(
                order, original_qty,
                f"Strategy allocation: {strategy_id} at cap "
                f"({strategy_market_value:,.0f} ≥ {max_strategy_value:,.0f})",
                "strategy_allocation",
            )

        # ── 6. Correlation concentration (scale-down, not reject) ─────────
        if self._correlation_checker is not None and len(self._price_history) >= 2:
            corr_result = self._correlation_checker.check(self._price_history)
            scale = corr_result.scale_factor(ticker)
            if scale < 1.0 and side == "buy":
                new_qty = order.quantity * scale
                if new_qty < self.limits.min_order_quantity:
                    return self._reject(
                        order, original_qty,
                        f"Correlation concentration: scale={scale:.2f} would produce dust order",
                        "correlation",
                    )
                scaled_order = self._scale_order(order, new_qty)
                logger.info(
                    "RiskManager: SCALE_DOWN %s %s %.2f → %.2f (correlation scale=%.2f)",
                    side, ticker, original_qty, new_qty, scale,
                )
                self._log_decision(OrderDecisionType.SCALE_DOWN, order, original_qty, new_qty, "correlation")
                return OrderDecision(
                    decision=OrderDecisionType.SCALE_DOWN,
                    order=scaled_order,
                    reason=f"Correlation concentration scale={scale:.2f}",
                    original_qty=original_qty,
                    adjusted_qty=new_qty,
                    check_name="correlation",
                )

        # ── All checks passed ─────────────────────────────────────────────
        return OrderDecision(
            decision=OrderDecisionType.APPROVE,
            order=order,
            original_qty=original_qty,
            adjusted_qty=original_qty,
            check_name="",
        )

    # ── Audit log ─────────────────────────────────────────────────────────

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """All non-APPROVE decisions since last reset."""
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        self._audit_log.clear()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _reject(
        self, order: Any, original_qty: float, reason: str, check_name: str
    ) -> OrderDecision:
        logger.warning("RiskManager REJECT [%s]: %s", check_name, reason)
        self._log_decision(OrderDecisionType.REJECT, order, original_qty, 0.0, check_name)
        return OrderDecision(
            decision=OrderDecisionType.REJECT,
            order=order,
            reason=reason,
            original_qty=original_qty,
            adjusted_qty=0.0,
            check_name=check_name,
        )

    def _scale_order(self, order: Any, new_qty: float) -> Any:
        """Return a copy of the order with adjusted quantity."""
        from strategies.base import Order
        return Order(
            ticker=order.ticker,
            side=order.side,
            quantity=max(new_qty, self.limits.min_order_quantity),
            order_type=order.order_type,
            strategy_id=order.strategy_id,
            confidence=order.confidence,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            timestamp=order.timestamp,
            metadata={**order.metadata, "risk_scaled_from": round(order.quantity, 4)},
        )

    def _strategy_market_value(self, strategy_id: str, portfolio: Any) -> float:
        """Estimate open market value for a strategy (heuristic via PnL attribution)."""
        # We don't have per-strategy position breakdown — use total open MV
        # as a conservative proxy (always passes unless the whole portfolio is at cap)
        # A more precise implementation would require per-strategy position tracking.
        try:
            mv = sum(
                abs(portfolio.position(t)) * self._price_history.get(t, [1.0])[-1]
                for t in (portfolio.positions_snapshot() or {})
                if portfolio.position(t) != 0
            )
        except Exception:
            mv = 0.0
        return mv

    def _log_decision(
        self,
        decision: OrderDecisionType,
        order: Any,
        original_qty: float,
        adjusted_qty: float,
        check_name: str,
    ) -> None:
        self._audit_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": decision.value,
            "check_name": check_name,
            "ticker": order.ticker,
            "side": order.side.value if hasattr(order.side, "value") else str(order.side),
            "original_qty": round(original_qty, 6),
            "adjusted_qty": round(adjusted_qty, 6),
            "strategy_id": order.strategy_id,
        })
