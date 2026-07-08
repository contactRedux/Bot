"""
backtesting/portfolio.py — Portfolio: tracks positions, cash, and PnL.

Accounting model
----------------
The Portfolio tracks four quantities for each asset:

1. **quantity** — signed shares held (+ long, - short).
2. **avg_cost** — average cost basis per unit (weighted average of fills).
3. **realised_pnl** — cash profit/loss from completed round-trips.
4. **unrealised_pnl** — mark-to-market profit/loss of open positions.

Per-strategy attribution
------------------------
Each FillEvent carries a ``strategy_id`` that generated it.  The Portfolio
tracks realised PnL per strategy so the BacktestReport can show which strategy
contributed most to the overall return.

Cash management
---------------
Cash starts at ``initial_capital``.  On a BUY fill:

    cash -= fill_price × quantity + commission

On a SELL fill:

    cash += fill_price × quantity - commission

Short positions are supported: selling more than you own creates a negative
position with margin requirements not modelled here (assumes unlimited margin
for backtesting purposes).

Equity curve
------------
After each fill the portfolio records ``(timestamp, total_equity)`` where:

    total_equity = cash + Σ (position × mark_price)

The ``mark()`` method must be called after each bar (or fill) to update
unrealised PnL with the latest prices.

Trade log
---------
Every fill is appended to ``trade_log`` as a dict — used by BacktestReport.

Usage
-----
::

    from backtesting.portfolio import Portfolio
    from backtesting.events import FillEvent

    pf = Portfolio(initial_capital=100_000.0)
    pf.on_fill(fill_event)
    pf.mark({"AAPL": 186.50})
    print(pf.total_equity)   # cash + unrealised positions
    print(pf.realised_pnl)   # sum of all realised round-trips
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backtesting.events import FillEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position container
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """
    Tracks a single open position in one asset.

    Attributes
    ----------
    ticker : str
    quantity : float        Signed: positive = long, negative = short.
    avg_cost : float        Average cost basis per unit.
    realised_pnl : float    Cumulative realised PnL from closed portions.
    unrealised_pnl : float  Current mark-to-market PnL.
    """

    ticker: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0

    @property
    def is_flat(self) -> bool:
        return abs(self.quantity) < 1e-8

    @property
    def market_value(self) -> float:
        """Current market value using last mark price (set externally)."""
        return self.quantity * self._mark_price

    # Mark price is set by Portfolio.mark(); use 0 until set
    _mark_price: float = field(default=0.0, repr=False)


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

class Portfolio:
    """
    Tracks portfolio-level cash, positions, PnL, and equity over time.

    Parameters
    ----------
    initial_capital : float
        Starting cash in base currency.
    """

    def __init__(self, initial_capital: float = 100_000.0) -> None:
        self.initial_capital = float(initial_capital)
        self.cash: float = float(initial_capital)

        # Per-asset positions
        self._positions: dict[str, Position] = {}

        # Per-strategy realised PnL attribution
        self._strategy_pnl: dict[str, float] = defaultdict(float)

        # Equity curve: list of (timestamp, equity_value)
        self.equity_curve: list[tuple[datetime, float]] = []

        # Trade log: list of fill dicts for reporting
        self.trade_log: list[dict[str, Any]] = []

        # Latest prices for marking positions to market
        self._mark_prices: dict[str, float] = {}

        # Total commission paid
        self.total_commission: float = 0.0

        # Record initial equity
        self.equity_curve.append((datetime.now(timezone.utc), self.initial_capital))

    # ── Core event handler ─────────────────────────────────────────────────

    def on_fill(self, fill: FillEvent) -> None:
        """
        Update positions and cash for a fill event.

        Parameters
        ----------
        fill : FillEvent
            Confirmed fill from the SimulatedBroker.
        """
        ticker = fill.ticker
        pos = self._positions.setdefault(ticker, Position(ticker=ticker))

        realised = 0.0

        if fill.side == "buy":
            if pos.quantity >= 0:
                # Adding to a long position: update average cost
                total_cost = pos.avg_cost * pos.quantity + fill.fill_price * fill.quantity
                pos.quantity += fill.quantity
                pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0.0
            else:
                # Covering a short position
                covered = min(fill.quantity, abs(pos.quantity))
                realised = covered * (pos.avg_cost - fill.fill_price)  # profit if entry > fill
                pos.realised_pnl += realised

                pos.quantity += fill.quantity
                if pos.quantity > 1e-8:
                    # Flipped to long
                    pos.avg_cost = fill.fill_price
                elif pos.is_flat:
                    pos.avg_cost = 0.0

            self.cash -= fill.fill_price * fill.quantity + fill.commission

        else:  # sell
            if pos.quantity <= 0:
                # Adding to a short position
                total_cost = pos.avg_cost * abs(pos.quantity) + fill.fill_price * fill.quantity
                pos.quantity -= fill.quantity
                pos.avg_cost = total_cost / abs(pos.quantity) if abs(pos.quantity) > 0 else 0.0
            else:
                # Reducing / closing a long position
                closed = min(fill.quantity, pos.quantity)
                realised = closed * (fill.fill_price - pos.avg_cost)  # profit if fill > entry
                pos.realised_pnl += realised

                pos.quantity -= fill.quantity
                if pos.quantity < -1e-8:
                    # Flipped to short
                    pos.avg_cost = fill.fill_price
                elif pos.is_flat:
                    pos.avg_cost = 0.0

            self.cash += fill.fill_price * fill.quantity - fill.commission

        self.total_commission += fill.commission

        # Per-strategy attribution
        if realised != 0.0:
            self._strategy_pnl[fill.strategy_id] += realised

        # Trade log entry
        self.trade_log.append({
            "timestamp": fill.timestamp.isoformat(),
            "ticker": ticker,
            "side": fill.side,
            "quantity": round(fill.quantity, 6),
            "fill_price": round(fill.fill_price, 4),
            "commission": round(fill.commission, 4),
            "slippage": round(fill.slippage, 4),
            "realised_pnl": round(realised, 4),
            "strategy_id": fill.strategy_id,
            "order_id": fill.order_id,
        })

        logger.debug(
            "Fill: %s %s %.2f @ %.4f | cash=%.2f realised=%.2f",
            fill.side, ticker, fill.quantity, fill.fill_price, self.cash, realised,
        )

    # ── Marking to market ─────────────────────────────────────────────────

    def mark(
        self,
        prices: dict[str, float],
        timestamp: datetime | None = None,
    ) -> float:
        """
        Update unrealised PnL for all open positions and append to equity curve.

        Parameters
        ----------
        prices : dict[str, float]
            Map of ticker → current market price.
        timestamp : datetime, optional
            Simulation time; defaults to now.

        Returns
        -------
        float
            Current total equity.
        """
        self._mark_prices.update(prices)
        ts = timestamp or datetime.now(timezone.utc)

        for ticker, pos in self._positions.items():
            price = self._mark_prices.get(ticker, pos.avg_cost)
            pos._mark_price = price
            if not pos.is_flat:
                if pos.quantity > 0:
                    pos.unrealised_pnl = pos.quantity * (price - pos.avg_cost)
                else:
                    pos.unrealised_pnl = abs(pos.quantity) * (pos.avg_cost - price)
            else:
                pos.unrealised_pnl = 0.0

        equity = self.total_equity
        self.equity_curve.append((ts, equity))
        return equity

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def total_equity(self) -> float:
        """Cash + market value of all open positions."""
        mv = sum(
            self._mark_prices.get(t, p.avg_cost) * p.quantity
            for t, p in self._positions.items()
        )
        return self.cash + mv

    @property
    def realised_pnl(self) -> float:
        """Total realised PnL across all positions."""
        return sum(p.realised_pnl for p in self._positions.values())

    @property
    def unrealised_pnl(self) -> float:
        """Total unrealised PnL across all open positions."""
        return sum(p.unrealised_pnl for p in self._positions.values())

    @property
    def total_pnl(self) -> float:
        """realised_pnl + unrealised_pnl."""
        return self.realised_pnl + self.unrealised_pnl

    # ── Accessors ─────────────────────────────────────────────────────────

    def position(self, ticker: str) -> float:
        """Return signed position quantity for a ticker (0 if flat)."""
        pos = self._positions.get(ticker)
        return pos.quantity if pos is not None else 0.0

    def avg_cost(self, ticker: str) -> float:
        """Return average cost basis for a ticker (0 if flat)."""
        pos = self._positions.get(ticker)
        return pos.avg_cost if pos is not None else 0.0

    def positions_snapshot(self) -> dict[str, dict[str, float]]:
        """
        Return a snapshot of all non-flat positions.

        Returns
        -------
        dict[str, dict]
            Maps ticker → {quantity, avg_cost, unrealised_pnl, realised_pnl}.
        """
        return {
            t: {
                "quantity": round(p.quantity, 6),
                "avg_cost": round(p.avg_cost, 4),
                "unrealised_pnl": round(p.unrealised_pnl, 4),
                "realised_pnl": round(p.realised_pnl, 4),
                "market_value": round(
                    self._mark_prices.get(t, p.avg_cost) * p.quantity, 2
                ),
            }
            for t, p in self._positions.items()
            if not p.is_flat
        }

    def strategy_pnl_attribution(self) -> dict[str, float]:
        """Return per-strategy realised PnL (rounded to 2 dp)."""
        return {sid: round(pnl, 2) for sid, pnl in self._strategy_pnl.items()}

    def equity_series(self) -> list[dict[str, Any]]:
        """Return equity curve as list of {timestamp, equity} dicts."""
        return [
            {"timestamp": ts.isoformat(), "equity": round(eq, 2)}
            for ts, eq in self.equity_curve
        ]

    # ── Reset ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset to initial state (call at start of each backtest run)."""
        self.cash = self.initial_capital
        self._positions.clear()
        self._strategy_pnl.clear()
        self.equity_curve.clear()
        self.trade_log.clear()
        self._mark_prices.clear()
        self.total_commission = 0.0
        self.equity_curve.append((datetime.now(timezone.utc), self.initial_capital))
