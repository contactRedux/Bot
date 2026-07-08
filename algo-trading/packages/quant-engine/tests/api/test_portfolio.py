"""
tests/api/test_portfolio.py — Tests for portfolio endpoints.
"""
from __future__ import annotations

from datetime import UTC

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


# ---------------------------------------------------------------------------
# Tests for the new /api/portfolio/price-history endpoint
# ---------------------------------------------------------------------------

class TestPriceHistory:
    """Tests for GET /api/portfolio/price-history"""

    def test_returns_200_empty_store(self, client: TestClient):
        """Endpoint must return 200 even when the DataStore has no bars."""
        resp = client.get("/api/portfolio/price-history?ticker=AAPL")
        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient):
        data = client.get("/api/portfolio/price-history?ticker=AAPL").json()
        assert "ticker" in data
        assert "interval" in data
        assert "points" in data
        assert "count" in data

    def test_empty_store_returns_empty_points(self, client: TestClient):
        data = client.get("/api/portfolio/price-history?ticker=AAPL").json()
        assert data["points"] == []
        assert data["count"] == 0

    def test_ticker_echoed_in_response(self, client: TestClient):
        data = client.get("/api/portfolio/price-history?ticker=MSFT").json()
        assert data["ticker"] == "MSFT"

    def test_default_interval_is_1d(self, client: TestClient):
        data = client.get("/api/portfolio/price-history?ticker=AAPL").json()
        assert data["interval"] == "1d"

    def test_custom_interval_echoed(self, client: TestClient):
        data = client.get("/api/portfolio/price-history?ticker=AAPL&interval=1h").json()
        assert data["interval"] == "1h"

    def test_missing_ticker_returns_422(self, client: TestClient):
        resp = client.get("/api/portfolio/price-history")
        assert resp.status_code == 422

    def test_limit_param_accepted(self, client: TestClient):
        resp = client.get("/api/portfolio/price-history?ticker=AAPL&limit=50")
        assert resp.status_code == 200

    def test_limit_too_large_returns_422(self, client: TestClient):
        resp = client.get("/api/portfolio/price-history?ticker=AAPL&limit=9999")
        assert resp.status_code == 422

    def test_limit_zero_returns_422(self, client: TestClient):
        resp = client.get("/api/portfolio/price-history?ticker=AAPL&limit=0")
        assert resp.status_code == 422

    def test_with_real_bars_in_store(self, state, client: TestClient):
        """Seed the in-memory DataStore and verify bars are returned."""
        from datetime import datetime, timedelta

        from data.schemas import OHLCVBar

        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        bars = [
            OHLCVBar(
                ticker="AAPL",
                interval="1d",
                open=148.0 + i,
                high=152.0 + i,
                low=147.0 + i,
                close=150.0 + i,
                volume=1_000_000.0,
                event_timestamp=now - timedelta(days=4 - i),
                fetch_timestamp=now - timedelta(days=4 - i),
                source="yfinance",
            )
            for i in range(5)
        ]
        state.data_store.write_bars(bars)

        data = client.get("/api/portfolio/price-history?ticker=AAPL&interval=1d").json()
        assert data["count"] == 5
        # Verify the first and last closes
        assert data["points"][0]["close"] == 150.0
        assert data["points"][4]["close"] == 154.0
        # Time field is ISO date string
        assert len(data["points"][0]["time"]) == 10  # "YYYY-MM-DD"

    def test_bars_point_shape(self, state, client: TestClient):
        """Each point must have time, close, and optional OHLCV fields."""
        from datetime import datetime, timedelta

        from data.schemas import OHLCVBar

        ts = (
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=1)
        )
        bar = OHLCVBar(
            ticker="TSLA",
            interval="1d",
            open=200.0,
            high=210.0,
            low=195.0,
            close=205.0,
            volume=500_000.0,
            event_timestamp=ts,
            fetch_timestamp=ts,
            source="yfinance",
        )
        state.data_store.write_bars([bar])

        data = client.get("/api/portfolio/price-history?ticker=TSLA&interval=1d").json()
        assert data["count"] == 1
        pt = data["points"][0]
        assert pt["close"] == 205.0
        assert pt["open"] == 200.0
        assert pt["high"] == 210.0
        assert pt["low"] == 195.0
        assert pt["time"] == ts.strftime("%Y-%m-%d")

    def test_limit_trims_results(self, state, client: TestClient):
        """limit param must cap the number of returned points."""
        from datetime import datetime, timedelta

        from data.schemas import OHLCVBar

        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        bars = [
            OHLCVBar(
                ticker="GOOG",
                interval="1d",
                open=100.0,
                high=105.0,
                low=98.0,
                close=102.0,
                volume=200_000.0,
                event_timestamp=now - timedelta(days=9 - i),
                fetch_timestamp=now - timedelta(days=9 - i),
                source="yfinance",
            )
            for i in range(10)
        ]
        state.data_store.write_bars(bars)

        data = client.get("/api/portfolio/price-history?ticker=GOOG&interval=1d&limit=3").json()
        assert data["count"] == 3

    def test_no_data_store_returns_empty(self, state, client: TestClient):
        """If data_store is None, endpoint must return empty points (not 500)."""
        original = state.data_store
        state.data_store = None
        data = client.get("/api/portfolio/price-history?ticker=AAPL").json()
        assert data["points"] == []
        assert data["count"] == 0
        state.data_store = original
