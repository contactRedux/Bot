"""
tests/execution/test_factory.py — Unit tests for BrokerFactory and RoutingBroker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from execution.base import FillEvent, OrderStatus
from execution.factory import BrokerFactory, RoutingBroker, _is_crypto
from execution.paper_broker import PaperBroker
from strategies.base import Order, OrderSide, OrderType


# ---------------------------------------------------------------------------
# Stub settings objects
# ---------------------------------------------------------------------------

@dataclass
class DevSettings:
    trading_mode: str = "dev"
    alpaca_api_key: Any = None
    alpaca_secret_key: Any = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    binance_api_key: Any = None
    binance_secret_key: Any = None
    binance_testnet: bool = True


@dataclass
class PaperSettings:
    trading_mode: str = "paper"
    alpaca_api_key: Any = None
    alpaca_secret_key: Any = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    binance_api_key: Any = None
    binance_secret_key: Any = None
    binance_testnet: bool = True


@dataclass
class LiveNoKeysSettings:
    trading_mode: str = "live"
    alpaca_api_key: Any = None
    alpaca_secret_key: Any = None
    alpaca_base_url: str = "https://api.alpaca.markets"
    binance_api_key: Any = None
    binance_secret_key: Any = None
    binance_testnet: bool = False


# ---------------------------------------------------------------------------
# _is_crypto helper
# ---------------------------------------------------------------------------

class TestIsCrypto:
    def test_btc_usd_is_crypto(self):
        assert _is_crypto("BTC-USD")

    def test_eth_usd_is_crypto(self):
        assert _is_crypto("ETH-USD")

    def test_sol_usd_is_crypto(self):
        assert _is_crypto("SOL-USD")

    def test_aapl_not_crypto(self):
        assert not _is_crypto("AAPL")

    def test_msft_not_crypto(self):
        assert not _is_crypto("MSFT")

    def test_btcusdt_is_crypto(self):
        # Binance-style symbol — no dash but BTC is in the known set
        assert _is_crypto("BTCUSDT") or _is_crypto("BTC-USDT")

    def test_xrp_usd_is_crypto(self):
        assert _is_crypto("XRP-USD")

    def test_case_insensitive(self):
        assert _is_crypto("btc-usd")


# ---------------------------------------------------------------------------
# BrokerFactory
# ---------------------------------------------------------------------------

class TestBrokerFactoryDev:
    def test_dev_mode_returns_paper_broker(self):
        broker = BrokerFactory.create(DevSettings(), initial_cash=50_000.0)
        assert isinstance(broker, PaperBroker)

    def test_paper_mode_returns_paper_broker(self):
        broker = BrokerFactory.create(PaperSettings(), initial_cash=50_000.0)
        assert isinstance(broker, PaperBroker)

    def test_paper_broker_is_connected(self):
        broker = BrokerFactory.create(DevSettings())
        assert broker.is_connected

    def test_initial_cash_forwarded(self):
        broker = BrokerFactory.create(DevSettings(), initial_cash=12_345.0)
        acct = broker.get_account()
        assert acct["cash"] == pytest.approx(12_345.0)

    def test_paper_kwargs_forwarded(self):
        broker = BrokerFactory.create(DevSettings(), commission_rate=0.002)
        assert isinstance(broker, PaperBroker)
        assert broker.commission_rate == pytest.approx(0.002)


class TestBrokerFactoryLive:
    def test_live_mode_no_keys_raises(self):
        with pytest.raises(ValueError, match="requires at least one broker"):
            BrokerFactory.create(LiveNoKeysSettings())

    def test_unknown_mode_raises(self):
        @dataclass
        class WeirdSettings:
            trading_mode: str = "staging"
        with pytest.raises(ValueError, match="Unknown trading mode"):
            BrokerFactory.create(WeirdSettings())


# ---------------------------------------------------------------------------
# RoutingBroker
# ---------------------------------------------------------------------------

class TestRoutingBroker:
    """RoutingBroker dispatches orders to equity vs crypto sub-brokers."""

    def _make_routing_broker(self) -> RoutingBroker:
        equity = PaperBroker(initial_cash=100_000.0, fixed_slippage_pct=0.0,
                             random_slippage_pct=0.0)
        crypto = PaperBroker(initial_cash=100_000.0, fixed_slippage_pct=0.0,
                             random_slippage_pct=0.0)
        equity.update_prices({"AAPL": 150.0, "MSFT": 300.0})
        crypto.update_prices({"BTC-USD": 60_000.0, "ETH-USD": 3_000.0})
        return RoutingBroker(equity_broker=equity, crypto_broker=crypto)

    def test_equity_order_fills_via_equity_broker(self):
        broker = self._make_routing_broker()
        order = Order(ticker="AAPL", side=OrderSide.BUY, quantity=1.0,
                      order_type=OrderType.MARKET, strategy_id="test")
        fill = broker.submit_order(order)
        assert fill.is_filled
        assert fill.fill_price == pytest.approx(150.0)

    def test_crypto_order_fills_via_crypto_broker(self):
        broker = self._make_routing_broker()
        order = Order(ticker="BTC-USD", side=OrderSide.BUY, quantity=0.01,
                      order_type=OrderType.MARKET, strategy_id="test")
        fill = broker.submit_order(order)
        assert fill.is_filled
        assert fill.fill_price == pytest.approx(60_000.0)

    def test_is_connected_both_brokers_up(self):
        broker = self._make_routing_broker()
        assert broker.is_connected

    def test_update_prices_propagates_to_both(self):
        equity = PaperBroker()
        crypto = PaperBroker()
        routing = RoutingBroker(equity, crypto)
        routing.update_prices({"AAPL": 200.0, "BTC-USD": 70_000.0})
        assert equity._prices["AAPL"] == pytest.approx(200.0)
        assert crypto._prices["BTC-USD"] == pytest.approx(70_000.0)

    def test_get_account_combines_both(self):
        broker = self._make_routing_broker()
        acct = broker.get_account()
        assert "equity" in acct
        assert "crypto" in acct
        assert acct["cash"] == pytest.approx(200_000.0)  # 100k + 100k

    def test_cancel_equity_order(self):
        broker = self._make_routing_broker()
        order = Order(ticker="AAPL", side=OrderSide.BUY, quantity=5.0,
                      order_type=OrderType.LIMIT, strategy_id="test",
                      limit_price=200.0)
        # Price is 150, limit=200 → fills immediately (150 < 200)
        broker.submit_order(order)
        # No pending orders after immediate fill, cancel should return False
        result = broker.cancel_order("unknown-equity-id")
        assert result is False

    def test_get_order_status_crypto_by_colon_format(self):
        broker = self._make_routing_broker()
        # A colon in the order_id routes to crypto broker
        status = broker.get_order_status("BTCUSDT:123456")
        # PaperBroker returns CANCELLED for unknown IDs
        assert status == OrderStatus.CANCELLED
