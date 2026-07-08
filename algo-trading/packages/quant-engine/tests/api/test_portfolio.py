"""
tests/api/test_portfolio.py — Tests for portfolio endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestPortfolioEndpoint:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/portfolio")
        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient):
        data = client.get("/api/portfolio").json()
        for key in ("cash", "total_equity", "total_market_value",
                    "total_unrealised_pnl", "total_realised_pnl",
                    "positions", "last_updated"):
            assert key in data

    def test_positions_is_list(self, client: TestClient):
        data = client.get("/api/portfolio").json()
        assert isinstance(data["positions"], list)

    def test_empty_portfolio_no_crash(self, state, client: TestClient):
        # No portfolio set → returns empty portfolio
        original = state.portfolio
        state.portfolio = None
        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cash"] == 0.0
        state.portfolio = original


class TestPortfolioHistory:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/portfolio/history")
        assert resp.status_code == 200

    def test_has_equity_history(self, client: TestClient):
        data = client.get("/api/portfolio/history").json()
        assert "equity_history" in data
        assert "count" in data

    def test_limit_param_respected(self, client: TestClient):
        data = client.get("/api/portfolio/history?limit=10").json()
        assert data["count"] <= 10

    def test_empty_history_returns_empty_list(self, state, client: TestClient):
        original = list(state.equity_history)
        state.equity_history.clear()
        data = client.get("/api/portfolio/history").json()
        assert data["equity_history"] == []
        state.equity_history.extend(original)


class TestPortfolioTrades:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/api/portfolio/trades")
        assert resp.status_code == 200

    def test_has_trades_key(self, client: TestClient):
        data = client.get("/api/portfolio/trades").json()
        assert "trades" in data
        assert "count" in data

    def test_no_broker_returns_empty(self, state, client: TestClient):
        original = state.broker
        state.broker = None
        data = client.get("/api/portfolio/trades").json()
        assert data["trades"] == []
        state.broker = original

    def test_fills_appear_after_order(self, state, client: TestClient):
        state.broker.update_prices({"AAPL": 150.0})
        from strategies.base import Order, OrderSide, OrderType
        order = Order(ticker="AAPL", side=OrderSide.BUY, quantity=2.0,
                      order_type=OrderType.MARKET, strategy_id="test")
        state.broker.submit_order(order)
        data = client.get("/api/portfolio/trades").json()
        assert data["count"] >= 1
