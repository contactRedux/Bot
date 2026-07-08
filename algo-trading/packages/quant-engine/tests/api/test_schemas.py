"""
tests/api/test_schemas.py — Unit tests for Pydantic schemas (no HTTP needed).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import (
    BacktestRequest,
    BacktestResponse,
    EquityCurvePoint,
    PortfolioResponse,
    PositionItem,
    ResumeRequest,
    RiskStatusResponse,
    SignalItem,
    StrategyInfo,
    StrategyToggleRequest,
    WSEvent,
)


class TestBacktestRequest:
    def test_valid_request(self):
        req = BacktestRequest(
            tickers=["AAPL", "MSFT"],
            start_date="2023-01-01",
            end_date="2024-01-01",
        )
        assert req.initial_capital == 100_000.0
        assert req.interval == "1d"

    def test_defaults_applied(self):
        req = BacktestRequest(
            tickers=["BTC-USD"],
            start_date="2023-01-01",
            end_date="2023-12-31",
        )
        assert req.strategies == ["all"]

    def test_empty_tickers_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRequest(tickers=[], start_date="2023-01-01", end_date="2024-01-01")

    def test_negative_capital_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRequest(
                tickers=["AAPL"],
                start_date="2023-01-01",
                end_date="2024-01-01",
                initial_capital=-1000.0,
            )


class TestSignalItem:
    def test_valid_signal(self):
        s = SignalItem(
            ticker="AAPL",
            strategy_id="momentum",
            signal=0.75,
            confidence=0.9,
            timestamp="2024-01-01T00:00:00Z",
        )
        assert s.signal == 0.75

    def test_signal_serialises_to_dict(self):
        s = SignalItem(
            ticker="BTC-USD", strategy_id="sentiment",
            signal=-0.3, confidence=0.6,
            timestamp="2024-01-01T00:00:00Z",
        )
        d = s.model_dump()
        assert d["ticker"] == "BTC-USD"
        assert d["signal"] == -0.3


class TestRiskStatusResponse:
    def test_valid_response(self):
        r = RiskStatusResponse(
            halted=False,
            halt_reason="",
            peak_equity=100_000.0,
            current_drawdown_pct=2.5,
            daily_loss_pct=0.8,
            max_drawdown_pct_limit=20.0,
            max_daily_loss_pct_limit=2.0,
            var_95=1_200.0,
            var_99=2_100.0,
            cvar_95=1_600.0,
            cvar_99=2_800.0,
            correlation_pairs=[],
        )
        assert r.halted is False


class TestWSEvent:
    def test_valid_event(self):
        ev = WSEvent(
            event_type="signal",
            payload={"ticker": "AAPL", "signal": 0.5},
        )
        assert ev.event_type == "signal"
        assert "ticker" in ev.payload

    def test_invalid_event_type_rejected(self):
        with pytest.raises(ValidationError):
            WSEvent(event_type="unknown_type", payload={})

    def test_timestamp_auto_set(self):
        ev = WSEvent(event_type="heartbeat", payload={})
        assert ev.timestamp is not None
        assert len(ev.timestamp) > 0


class TestStrategyToggle:
    def test_valid_toggle(self):
        t = StrategyToggleRequest(enabled=False)
        assert t.enabled is False

    def test_missing_enabled_rejected(self):
        with pytest.raises(ValidationError):
            StrategyToggleRequest()


class TestResumeRequest:
    def test_new_equity_optional(self):
        r = ResumeRequest()
        assert r.new_equity is None

    def test_new_equity_set(self):
        r = ResumeRequest(new_equity=95_000.0)
        assert r.new_equity == pytest.approx(95_000.0)


class TestEquityCurvePoint:
    def test_valid_point(self):
        p = EquityCurvePoint(timestamp="2024-01-01T00:00:00", equity=105_000.0)
        assert p.equity == 105_000.0
