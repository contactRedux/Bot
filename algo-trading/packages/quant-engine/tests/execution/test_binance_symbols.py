"""
tests/execution/test_binance_symbols.py — Unit tests for Binance ticker normalisation
and status mapping helpers (no live API calls required).
"""
from __future__ import annotations

import pytest

from execution.binance_broker import _map_binance_status, _to_binance_symbol
from execution.base import OrderStatus


class TestToBinanceSymbol:
    def test_btc_usd_becomes_btcusdt(self):
        assert _to_binance_symbol("BTC-USD") == "BTCUSDT"

    def test_eth_usd_becomes_ethusdt(self):
        assert _to_binance_symbol("ETH-USD") == "ETHUSDT"

    def test_sol_usd_becomes_solusdt(self):
        assert _to_binance_symbol("SOL-USD") == "SOLUSDT"

    def test_eth_btc_becomes_ethbtc(self):
        assert _to_binance_symbol("ETH-BTC") == "ETHBTC"

    def test_usdc_treated_as_usdt(self):
        # USD and USDC both normalise to USDT on Binance
        assert _to_binance_symbol("BTC-USDC") == "BTCUSDT"

    def test_passthrough_if_no_dash(self):
        assert _to_binance_symbol("BTCUSDT") == "BTCUSDT"

    def test_lowercase_input_uppercased(self):
        assert _to_binance_symbol("btc-usd") == "BTCUSDT"

    def test_xrp_usd(self):
        assert _to_binance_symbol("XRP-USD") == "XRPUSDT"


class TestMapBinanceStatus:
    def test_new_maps_to_pending(self):
        assert _map_binance_status("NEW") == OrderStatus.PENDING

    def test_filled_maps_to_filled(self):
        assert _map_binance_status("FILLED") == OrderStatus.FILLED

    def test_partially_filled_maps_to_partial(self):
        assert _map_binance_status("PARTIALLY_FILLED") == OrderStatus.PARTIAL

    def test_canceled_maps_to_cancelled(self):
        assert _map_binance_status("CANCELED") == OrderStatus.CANCELLED

    def test_rejected_maps_to_rejected(self):
        assert _map_binance_status("REJECTED") == OrderStatus.REJECTED

    def test_expired_maps_to_expired(self):
        assert _map_binance_status("EXPIRED") == OrderStatus.EXPIRED

    def test_lowercase_input_handled(self):
        assert _map_binance_status("filled") == OrderStatus.FILLED

    def test_unknown_status_defaults_to_pending(self):
        assert _map_binance_status("SOMETHING_WEIRD") == OrderStatus.PENDING
