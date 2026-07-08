"""
tests/execution/test_alpaca_status.py — Unit tests for Alpaca status mapping
(no live API calls required).
"""
from __future__ import annotations

import pytest

from execution.alpaca_broker import _map_alpaca_status
from execution.base import OrderStatus


class TestMapAlpacaStatus:
    def test_new_maps_to_pending(self):
        assert _map_alpaca_status("new") == OrderStatus.PENDING

    def test_partially_filled_maps_to_partial(self):
        assert _map_alpaca_status("partially_filled") == OrderStatus.PARTIAL

    def test_filled_maps_to_filled(self):
        assert _map_alpaca_status("filled") == OrderStatus.FILLED

    def test_canceled_maps_to_cancelled(self):
        assert _map_alpaca_status("canceled") == OrderStatus.CANCELLED

    def test_expired_maps_to_expired(self):
        assert _map_alpaca_status("expired") == OrderStatus.EXPIRED

    def test_done_for_day_maps_to_expired(self):
        assert _map_alpaca_status("done_for_day") == OrderStatus.EXPIRED

    def test_rejected_maps_to_rejected(self):
        assert _map_alpaca_status("rejected") == OrderStatus.REJECTED

    def test_accepted_maps_to_pending(self):
        assert _map_alpaca_status("accepted") == OrderStatus.PENDING

    def test_pending_new_maps_to_pending(self):
        assert _map_alpaca_status("pending_new") == OrderStatus.PENDING

    def test_pending_cancel_maps_to_pending(self):
        assert _map_alpaca_status("pending_cancel") == OrderStatus.PENDING

    def test_replaced_maps_to_cancelled(self):
        assert _map_alpaca_status("replaced") == OrderStatus.CANCELLED

    def test_uppercase_input_handled(self):
        assert _map_alpaca_status("FILLED") == OrderStatus.FILLED

    def test_unknown_defaults_to_pending(self):
        assert _map_alpaca_status("some_new_status") == OrderStatus.PENDING
