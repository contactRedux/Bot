"""
backtesting/report.py — BacktestReport: serialisable backtest results container.

The BacktestReport is the final artefact of a backtest run.  It contains:

* **metrics**  — all quantitative performance metrics (dict).
* **equity_curve** — portfolio value at every simulation step.
* **trade_log** — every fill with timestamp, side, price, PnL.
* **strategy_attribution** — per-strategy realised PnL breakdown.
* **positions_at_close** — open positions at end of simulation.

Serialisation
-------------
``to_json()`` / ``to_dict()`` converts the report to a plain dict / JSON string
suitable for:

* Storing in a database (via the API layer in Sub-Task 9).
* Sending over a WebSocket to the dashboard.
* Diffing two report files to compare strategy variants.

``from_json()`` / ``from_dict()`` reconstitutes a report from stored data.

Usage
-----
::

    report = engine.run()
    print(report.summary())
    report.save("backtest_2024.json")

    # Load later
    r2 = BacktestReport.load("backtest_2024.json")
    assert r2.metrics["sharpe_ratio"] > 1.0
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class BacktestReport:
    """
    Serialisable container for all backtest results.

    Attributes
    ----------
    metrics : dict[str, Any]
        All computed metrics (total_return, CAGR, Sharpe, Sortino, Calmar,
        max_drawdown, win_rate, profit_factor, per-strategy attribution, …).
    equity_curve : list[dict]
        Each item: ``{"timestamp": ISO string, "equity": float}``.
    trade_log : list[dict]
        Each fill recorded during the simulation.
    strategy_attribution : dict[str, float]
        Per-strategy realised PnL totals.
    tickers : list[str]
        Tickers included in the backtest.
    bar_interval : str
        Bar duration (``"1d"``, ``"1h"``, etc.).
    initial_capital : float
    halted : bool
        True if the simulation was stopped early by a halt condition.
    halt_reason : str
        Human-readable reason for early halt.
    positions_at_close : dict[str, dict]
        Open positions remaining at end of simulation (typically zero
        for well-behaved strategies, but not forced).
    created_at : str
        ISO timestamp when this report was created.
    """

    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    trade_log: list[dict[str, Any]]
    strategy_attribution: dict[str, float] = field(default_factory=dict)
    tickers: list[str] = field(default_factory=list)
    bar_interval: str = "1d"
    initial_capital: float = 100_000.0
    halted: bool = False
    halt_reason: str = ""
    positions_at_close: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Accessors ─────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a human-readable single-line performance summary."""
        m = self.metrics
        lines = [
            "=" * 60,
            "  BACKTEST REPORT SUMMARY",
            "=" * 60,
            f"  Tickers       : {', '.join(self.tickers)}",
            f"  Interval      : {self.bar_interval}",
            f"  Period        : {m.get('start_date', 'N/A')} → {m.get('end_date', 'N/A')}",
            f"  Initial Cap   : ${self.initial_capital:,.0f}",
            f"  Final Equity  : ${m.get('final_equity', 0):,.2f}",
            "",
            "  ── Returns ──────────────────────────────",
            f"  Total Return  : {m.get('total_return_pct', 0):.2f}%",
            f"  CAGR          : {m.get('cagr_pct', 0):.2f}%",
            "",
            "  ── Risk ─────────────────────────────────",
            f"  Sharpe        : {m.get('sharpe_ratio', 0):.3f}",
            f"  Sortino       : {m.get('sortino_ratio', 0):.3f}",
            f"  Calmar        : {m.get('calmar_ratio', 0):.3f}",
            f"  Max Drawdown  : {m.get('max_drawdown_pct', 0):.2f}%",
            f"  Ann. Vol      : {m.get('annual_volatility_pct', 0):.2f}%",
            "",
            "  ── Trades ───────────────────────────────",
            f"  N Trades      : {m.get('n_trades', 0)}",
            f"  Win Rate      : {m.get('win_rate_pct', 0):.1f}%",
            f"  Profit Factor : {m.get('profit_factor', 0):.3f}",
            f"  Avg Trade PnL : ${m.get('avg_trade_pnl', 0):+.2f}",
        ]

        attr = m.get("strategy_attribution") or self.strategy_attribution
        if attr:
            lines += ["", "  ── Strategy Attribution ────────────────"]
            for sid, pnl in sorted(attr.items(), key=lambda x: -x[1]):
                lines.append(f"  {sid:<20}: ${pnl:+,.2f}")

        if self.halted:
            lines += ["", f"  ⚠  Simulation halted: {self.halt_reason}"]

        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict (JSON-serialisable)."""
        return {
            "metrics": self.metrics,
            "equity_curve": self.equity_curve,
            "trade_log": self.trade_log,
            "strategy_attribution": self.strategy_attribution,
            "tickers": self.tickers,
            "bar_interval": self.bar_interval,
            "initial_capital": self.initial_capital,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "positions_at_close": self.positions_at_close,
            "created_at": self.created_at,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save(self, path: str | Path) -> None:
        """Write the report as a JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BacktestReport":
        """Reconstruct a BacktestReport from a dict."""
        return cls(
            metrics=data.get("metrics", {}),
            equity_curve=data.get("equity_curve", []),
            trade_log=data.get("trade_log", []),
            strategy_attribution=data.get("strategy_attribution", {}),
            tickers=data.get("tickers", []),
            bar_interval=data.get("bar_interval", "1d"),
            initial_capital=data.get("initial_capital", 100_000.0),
            halted=data.get("halted", False),
            halt_reason=data.get("halt_reason", ""),
            positions_at_close=data.get("positions_at_close", {}),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "BacktestReport":
        """Reconstruct a BacktestReport from a JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def load(cls, path: str | Path) -> "BacktestReport":
        """Load a BacktestReport from a JSON file."""
        return cls.from_json(Path(path).read_text())

    # ── Comparison helpers ───────────────────────────────────────────────

    def compare(self, other: "BacktestReport") -> dict[str, Any]:
        """
        Compare key metrics between two reports.

        Returns a dict of metric → (self_value, other_value, delta).
        Useful for strategy parameter sensitivity analysis.
        """
        keys = [
            "total_return_pct", "cagr_pct", "sharpe_ratio", "sortino_ratio",
            "calmar_ratio", "max_drawdown_pct", "win_rate_pct", "profit_factor",
        ]
        result = {}
        for k in keys:
            v1 = self.metrics.get(k, 0.0)
            v2 = other.metrics.get(k, 0.0)
            result[k] = {"self": v1, "other": v2, "delta": round(float(v1) - float(v2), 4)}
        return result
