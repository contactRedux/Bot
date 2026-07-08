"""
tests/backtesting/test_engine.py — Integration tests for BacktestEngine.

These tests use minimal stub strategies and pre-built bar data to verify the
engine's simulation loop, order routing, portfolio accounting, and report
generation without requiring a live DataStore or real ML models.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backtesting.broker import SimulatedBroker, FixedPercentageSlippage
from backtesting.engine import BacktestEngine
from backtesting.events import BarEvent
from backtesting.portfolio import Portfolio
from backtesting.report import BacktestReport
from strategies.base import Order, OrderSide, OrderType


# ---------------------------------------------------------------------------
# Helpers — stub classes
# ---------------------------------------------------------------------------

def _make_bars(
    ticker: str,
    n: int = 30,
    start_price: float = 100.0,
    drift: float = 0.001,
) -> list[Any]:
    """
    Build a list of mock OHLCVBar-like objects.

    We use a simple dataclass rather than importing OHLCVBar to avoid
    DataStore dependencies in these unit tests.
    """
    from dataclasses import dataclass

    @dataclass
    class MockBar:
        ticker: str
        open: float
        high: float
        low: float
        close: float
        volume: float
        event_timestamp: datetime
        interval: str = "1d"
        source: str = "test"
        adjusted: bool = False

    bars = []
    base = datetime(2023, 1, 1, tzinfo=timezone.utc)
    price = start_price
    for i in range(n):
        ts = base + timedelta(days=i)
        price = price * (1 + drift)
        bars.append(MockBar(
            ticker=ticker,
            open=price * 0.99,
            high=price * 1.01,
            low=price * 0.98,
            close=price,
            volume=1_000_000.0,
            event_timestamp=ts,
        ))
    return bars


class StubOrchestrator:
    """
    Stub orchestrator that emits configurable orders for testing.

    Attributes
    ----------
    orders_to_emit : list[Order]
        Orders returned on every process_bar call.
    process_bar_calls : list
        Records of all process_bar calls for assertion.
    """

    def __init__(self, orders_to_emit: list[Order] | None = None) -> None:
        self.orders_to_emit = list(orders_to_emit or [])
        self.process_bar_calls: list[dict] = []
        self._positions: dict[str, float] = {}
        self.total_capital = 100_000.0

    def process_bar(self, ticker: str, bar: pd.Series, features: pd.DataFrame) -> list[Order]:
        self.process_bar_calls.append({"ticker": ticker, "close": bar.get("close")})
        return list(self.orders_to_emit)

    def update_position(self, ticker: str, delta: float) -> None:
        self._positions[ticker] = self._positions.get(ticker, 0.0) + delta

    def update_capital(self, capital: float) -> None:
        self.total_capital = capital

    def reset(self) -> None:
        self.process_bar_calls.clear()
        self._positions.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBacktestEngineBasic:
    def test_run_returns_report(self):
        bars = {"AAPL": _make_bars("AAPL", n=20)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator, initial_capital=50_000.0)
        report = engine.run()
        assert isinstance(report, BacktestReport)

    def test_all_bars_processed(self):
        n = 30
        bars = {"AAPL": _make_bars("AAPL", n=n)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        engine.run()
        assert len(orchestrator.process_bar_calls) == n

    def test_bars_processed_in_time_order(self):
        bars = {"AAPL": _make_bars("AAPL", n=10)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        engine.run()
        timestamps = [c["close"] for c in orchestrator.process_bar_calls]
        # Close prices should be monotonically increasing (drift=0.001)
        assert timestamps == sorted(timestamps)

    def test_multi_ticker_both_processed(self):
        bars = {"AAPL": _make_bars("AAPL", n=10), "MSFT": _make_bars("MSFT", n=10)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        engine.run()
        tickers_seen = {c["ticker"] for c in orchestrator.process_bar_calls}
        assert "AAPL" in tickers_seen
        assert "MSFT" in tickers_seen


class TestOrderFillFlow:
    def _make_buy_order(self, ticker="AAPL", qty=5.0):
        return Order(
            ticker=ticker,
            side=OrderSide.BUY,
            quantity=qty,
            order_type=OrderType.MARKET,
            strategy_id="test_strat",
            confidence=0.8,
        )

    def test_market_buy_order_creates_fill(self):
        bars = {"AAPL": _make_bars("AAPL", n=5, start_price=100.0)}
        orchestrator = StubOrchestrator(orders_to_emit=[self._make_buy_order(qty=5.0)])
        broker = SimulatedBroker(
            slippage_model=FixedPercentageSlippage(pct=0.0),
            commission_per_share=0.0,
            min_commission=0.0,
        )
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator, broker=broker, initial_capital=100_000.0)
        engine.run()
        assert len(engine.portfolio.trade_log) > 0

    def test_buy_reduces_cash(self):
        bars = {"AAPL": _make_bars("AAPL", n=3, start_price=100.0, drift=0.0)}
        # Emit one buy on every bar
        orchestrator = StubOrchestrator(orders_to_emit=[self._make_buy_order(qty=1.0)])
        broker = SimulatedBroker(
            slippage_model=FixedPercentageSlippage(pct=0.0),
            commission_per_share=0.0,
            min_commission=0.0,
        )
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator, broker=broker, initial_capital=100_000.0)
        engine.run()
        assert engine.portfolio.cash < 100_000.0

    def test_equity_curve_populated(self):
        bars = {"AAPL": _make_bars("AAPL", n=10)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        engine.run()
        # Should have initial + N marks
        assert len(engine.portfolio.equity_curve) > 1


class TestReportContent:
    def test_report_has_all_metric_keys(self):
        bars = {"AAPL": _make_bars("AAPL", n=30)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        report = engine.run()
        m = report.metrics
        required = ["total_return_pct", "sharpe_ratio", "max_drawdown_pct", "n_trades"]
        for k in required:
            assert k in m

    def test_report_tickers_correct(self):
        bars = {"AAPL": _make_bars("AAPL", n=10), "MSFT": _make_bars("MSFT", n=10)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        report = engine.run()
        assert set(report.tickers) == {"AAPL", "MSFT"}

    def test_report_serialisation_round_trip(self):
        bars = {"AAPL": _make_bars("AAPL", n=20)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        report = engine.run()
        json_str = report.to_json()
        restored = BacktestReport.from_json(json_str)
        assert restored.metrics["total_return_pct"] == report.metrics["total_return_pct"]
        assert restored.tickers == report.tickers

    def test_report_summary_string(self):
        bars = {"AAPL": _make_bars("AAPL", n=20)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        report = engine.run()
        summary = report.summary()
        assert "BACKTEST REPORT SUMMARY" in summary
        assert "Sharpe" in summary


class TestHaltOnDrawdown:
    def test_halt_stops_simulation_early(self):
        """
        Halt on drawdown requires the portfolio to hold a position so that
        price changes affect portfolio equity.  We inject a buy order on every
        bar via the stub orchestrator, then crash the price on bar 2.
        """
        from dataclasses import dataclass

        @dataclass
        class MockBar:
            ticker: str
            open: float
            high: float
            low: float
            close: float
            volume: float
            event_timestamp: datetime
            interval: str = "1d"
            source: str = "test"
            adjusted: bool = False

        base = datetime(2023, 1, 1, tzinfo=timezone.utc)
        ticker = "CRASH"
        bars_list = []
        for i in range(50):
            price = 100.0 if i == 0 else 50.0  # instant 50% crash on bar 2
            bars_list.append(MockBar(
                ticker=ticker,
                open=price, high=price + 1, low=price - 1, close=price, volume=1e6,
                event_timestamp=base + timedelta(days=i),
            ))

        # Orchestrator that buys on bar 0, holds thereafter
        buy_order = Order(
            ticker=ticker,
            side=OrderSide.BUY,
            quantity=500.0,   # Buy $50k worth at $100
            order_type=OrderType.MARKET,
            strategy_id="test",
            confidence=1.0,
        )

        class BuyOnFirstBarOrchestrator(StubOrchestrator):
            def __init__(self):
                super().__init__()
                self._bought = False

            def process_bar(self, ticker, bar, features):
                self.process_bar_calls.append({"ticker": ticker, "close": bar.get("close")})
                if not self._bought:
                    self._bought = True
                    return [buy_order]
                return []

        orchestrator = BuyOnFirstBarOrchestrator()
        broker = SimulatedBroker(
            slippage_model=FixedPercentageSlippage(pct=0.0),
            commission_per_share=0.0,
            min_commission=0.0,
        )
        engine = BacktestEngine(
            bars={ticker: bars_list},
            orchestrator=orchestrator,
            broker=broker,
            initial_capital=100_000.0,
            halt_on_drawdown=0.30,  # halt if portfolio drops 30%
        )
        report = engine.run()
        # After buying 500 shares @ $100, price crashes to $50:
        # equity = 50_000 cash + 500×50 = 75_000 → 25% drawdown from 100k.
        # Actually peak after mark is ~100k, trough is 75k → 25% drawdown.
        # To trigger 30% halt we need equity to drop to 70k.
        # Since 25% < 30% threshold, simulation completes.
        # Let's adjust: 800 shares @ 100 = 80_000 cost, 20_000 cash;
        # after crash equity = 20_000 + 800×50 = 60_000 → 40% drawdown > 30%.
        # Re-run with bigger buy.
        buy_order_big = Order(
            ticker=ticker, side=OrderSide.BUY, quantity=800.0,
            order_type=OrderType.MARKET, strategy_id="test", confidence=1.0,
        )

        class BuyBigOrchestrator(StubOrchestrator):
            def __init__(self):
                super().__init__()
                self._bought = False

            def process_bar(self, ticker, bar, features):
                self.process_bar_calls.append({"ticker": ticker, "close": bar.get("close")})
                if not self._bought:
                    self._bought = True
                    return [buy_order_big]
                return []

        orch2 = BuyBigOrchestrator()
        engine2 = BacktestEngine(
            bars={ticker: bars_list},
            orchestrator=orch2,
            broker=broker,
            initial_capital=100_000.0,
            halt_on_drawdown=0.30,
        )
        report2 = engine2.run()
        # 800 shares bought @ 100 = $80k, cash = $20k
        # After crash to $50: equity = $20k + 800×$50 = $60k
        # Peak was $100k, drawdown = 40% > 30% threshold → halted
        assert report2.halted is True
        assert len(orch2.process_bar_calls) < 50


class TestStepMode:
    def test_reset_returns_obs(self):
        bars = {"AAPL": _make_bars("AAPL", n=10)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        obs = engine.reset()
        assert isinstance(obs, np.ndarray)
        assert len(obs) == 3  # [equity_ratio, cash_ratio, pnl_norm]

    def test_step_advances_simulation(self):
        bars = {"AAPL": _make_bars("AAPL", n=10)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        engine.reset()
        obs, reward, done, info = engine.step()
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(done, bool)

    def test_step_done_after_all_bars(self):
        bars = {"AAPL": _make_bars("AAPL", n=5)}
        orchestrator = StubOrchestrator()
        engine = BacktestEngine(bars=bars, orchestrator=orchestrator)
        engine.reset()
        done = False
        steps = 0
        while not done:
            _, _, done, _ = engine.step()
            steps += 1
            if steps > 100:
                break
        assert done is True
        assert steps <= 5
