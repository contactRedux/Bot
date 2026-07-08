"""
risk/monitor.py — DrawdownMonitor: real-time drawdown and daily-loss circuit breaker.

Why you need a drawdown monitor
---------------------------------
A strategy that works in backtesting can still blow up in live trading if:

1. **Market regime shift** — the relationship your model learned no longer holds.
2. **Fat-tail event** — a correlated crash hits all positions simultaneously.
3. **Implementation bug** — a sign error silently trades the wrong direction.

A DrawdownMonitor acts as an automatic circuit breaker that halts trading
before a recoverable loss becomes an unrecoverable one.

Two circuit breakers
--------------------
1. **Peak-to-trough drawdown** (``max_drawdown_pct``):
   Tracks the highest equity value seen (the "peak") and halts when the
   current equity has fallen more than ``max_drawdown_pct`` from that peak.

       drawdown = (peak - current) / peak

   This is the most important safeguard — it catches slow bleeding as well
   as sudden crashes.

2. **Daily loss limit** (``max_daily_loss_pct``):
   Resets at market open each day.  Halts trading if the portfolio has lost
   more than ``max_daily_loss_pct`` of the day-open equity in a single day.
   Catches acute intraday crashes (e.g. a bad earnings release on a heavy
   position) before they compound.

Recovery
--------
After a halt, trading does not automatically resume.  The monitor exposes
``reset_halt()`` for manual review and restart.  In live trading this should
require a human action or a dedicated "resume" endpoint in the API layer
(Sub-Task 9).

Usage
-----
::

    from risk.monitor import DrawdownMonitor
    from risk.limits import RiskLimits

    monitor = DrawdownMonitor(limits=RiskLimits(max_drawdown_pct=0.20))

    # Called after every bar mark:
    alert = monitor.update(current_equity=95_000.0, timestamp=bar_timestamp)
    if alert.halt_triggered:
        logger.critical("HALT: %s", alert.reason)
        # stop submitting orders
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from risk.limits import RiskLimits

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alert container
# ---------------------------------------------------------------------------

@dataclass
class RiskAlert:
    """
    Emitted by DrawdownMonitor when a circuit breaker trips.

    Attributes
    ----------
    halt_triggered : bool       True if trading should stop.
    reason : str                Human-readable explanation.
    alert_type : str            ``"drawdown"`` or ``"daily_loss"``.
    current_equity : float      Portfolio value at time of alert.
    peak_equity : float         Portfolio peak value (for drawdown alerts).
    drawdown_pct : float        Current drawdown as a fraction.
    daily_loss_pct : float      Today's PnL as a fraction of day-open equity.
    timestamp : datetime
    """

    halt_triggered: bool = False
    reason: str = ""
    alert_type: str = ""
    current_equity: float = 0.0
    peak_equity: float = 0.0
    drawdown_pct: float = 0.0
    daily_loss_pct: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_ok(self) -> bool:
        return not self.halt_triggered


# ---------------------------------------------------------------------------
# DrawdownMonitor
# ---------------------------------------------------------------------------

class DrawdownMonitor:
    """
    Tracks peak-to-trough drawdown and daily loss against configured limits.

    Parameters
    ----------
    limits : RiskLimits
        Provides ``max_drawdown_pct`` and ``max_daily_loss_pct``.
    initial_equity : float
        Starting portfolio value (sets the initial peak and day-open value).
    """

    def __init__(
        self,
        limits: RiskLimits,
        initial_equity: float = 100_000.0,
    ) -> None:
        self.limits = limits
        self._peak: float = initial_equity
        self._day_open: float = initial_equity
        self._current_day: Optional[date] = None
        self._halted: bool = False
        self._halt_reason: str = ""
        self._last_alert: Optional[RiskAlert] = None

    # ── Main update interface ─────────────────────────────────────────────

    def update(
        self,
        current_equity: float,
        timestamp: datetime | None = None,
    ) -> RiskAlert:
        """
        Update monitor state with the latest portfolio equity.

        Call this after every bar's ``portfolio.mark()`` in the engine.

        Parameters
        ----------
        current_equity : float
            Latest total portfolio value (cash + open positions).
        timestamp : datetime, optional
            Simulation / wall-clock time.  Used for daily reset detection.
            Defaults to ``datetime.now(UTC)``.

        Returns
        -------
        RiskAlert
            ``alert.is_ok`` is True when no limit is breached.
            ``alert.halt_triggered`` is True when trading should stop.
        """
        ts = timestamp or datetime.now(timezone.utc)
        today = ts.date()

        # ── Daily reset ─────────────────────────────────────────────────
        if self._current_day != today:
            self._day_open = current_equity
            self._current_day = today
            logger.debug("DrawdownMonitor: new trading day %s, day_open=%.2f", today, current_equity)

        # ── Update peak ──────────────────────────────────────────────────
        if current_equity > self._peak:
            self._peak = current_equity

        # ── Compute metrics ───────────────────────────────────────────────
        drawdown = (self._peak - current_equity) / self._peak if self._peak > 0 else 0.0
        daily_loss = (self._day_open - current_equity) / self._day_open if self._day_open > 0 else 0.0

        alert = RiskAlert(
            current_equity=current_equity,
            peak_equity=self._peak,
            drawdown_pct=round(drawdown, 6),
            daily_loss_pct=round(daily_loss, 6),
            timestamp=ts,
        )

        # Already halted — propagate without re-checking
        if self._halted:
            alert.halt_triggered = True
            alert.reason = self._halt_reason
            alert.alert_type = "already_halted"
            return alert

        # ── Check drawdown limit ──────────────────────────────────────────
        if drawdown >= self.limits.max_drawdown_pct:
            reason = (
                f"Portfolio drawdown {drawdown:.2%} exceeded max "
                f"{self.limits.max_drawdown_pct:.2%} "
                f"(peak={self._peak:,.2f}, current={current_equity:,.2f})"
            )
            logger.critical("RISK HALT [drawdown]: %s", reason)
            self._halted = True
            self._halt_reason = reason
            alert.halt_triggered = True
            alert.reason = reason
            alert.alert_type = "drawdown"
            self._last_alert = alert
            return alert

        # ── Check daily loss limit ────────────────────────────────────────
        if daily_loss >= self.limits.max_daily_loss_pct:
            reason = (
                f"Daily loss {daily_loss:.2%} exceeded max "
                f"{self.limits.max_daily_loss_pct:.2%} "
                f"(day_open={self._day_open:,.2f}, current={current_equity:,.2f})"
            )
            logger.critical("RISK HALT [daily_loss]: %s", reason)
            self._halted = True
            self._halt_reason = reason
            alert.halt_triggered = True
            alert.reason = reason
            alert.alert_type = "daily_loss"
            self._last_alert = alert
            return alert

        self._last_alert = alert
        return alert

    # ── State accessors ───────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        """True if trading is currently halted by this monitor."""
        return self._halted

    @property
    def current_drawdown(self) -> float:
        """Latest peak-to-trough drawdown fraction (0.0 if never updated)."""
        return self._last_alert.drawdown_pct if self._last_alert else 0.0

    @property
    def peak_equity(self) -> float:
        return self._peak

    def status(self) -> dict:
        """Return a status dict for the API / dashboard."""
        alert = self._last_alert
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "peak_equity": round(self._peak, 2),
            "current_drawdown_pct": round((alert.drawdown_pct * 100) if alert else 0.0, 3),
            "daily_loss_pct": round((alert.daily_loss_pct * 100) if alert else 0.0, 3),
            "max_drawdown_pct_limit": round(self.limits.max_drawdown_pct * 100, 1),
            "max_daily_loss_pct_limit": round(self.limits.max_daily_loss_pct * 100, 1),
        }

    # ── Manual control ────────────────────────────────────────────────────

    def reset_halt(self, new_equity: float | None = None) -> None:
        """
        Manually clear the halt flag after human review.

        Parameters
        ----------
        new_equity : float, optional
            If provided, resets the peak and day-open to this value
            (use current portfolio value after partial liquidation).
        """
        self._halted = False
        self._halt_reason = ""
        if new_equity is not None:
            self._peak = new_equity
            self._day_open = new_equity
        logger.info("DrawdownMonitor: halt cleared (new equity reference=%.2f)", new_equity or self._peak)

    def reset(self, initial_equity: float | None = None) -> None:
        """Full reset — call at start of each backtest run."""
        equity = initial_equity or self._peak
        self._peak = equity
        self._day_open = equity
        self._current_day = None
        self._halted = False
        self._halt_reason = ""
        self._last_alert = None
