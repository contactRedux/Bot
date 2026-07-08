"""
tests/api/test_ws_feed.py — Tests for the WebSocket ConnectionManager and broadcast helpers.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.ws.feed import ConnectionManager, broadcast_signal


class TestConnectionManager:
    def test_initial_connection_count_zero(self):
        mgr = ConnectionManager()
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_does_not_crash_when_no_connections(self):
        mgr = ConnectionManager()
        # Should not raise even with no connected clients
        await mgr.broadcast({"event_type": "heartbeat", "payload": {}})

    def test_disconnect_removes_connection(self):
        mgr = ConnectionManager()

        class FakeWS:
            async def accept(self): pass
            async def send_text(self, _): pass

        fw = FakeWS()
        mgr._connections.add(fw)
        assert mgr.connection_count == 1
        mgr.disconnect(fw)
        assert mgr.connection_count == 0

    def test_disconnect_unknown_does_not_raise(self):
        mgr = ConnectionManager()

        class FakeWS:
            async def accept(self): pass

        mgr.disconnect(FakeWS())  # Not in set — should be a no-op

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connection(self):
        mgr = ConnectionManager()

        class DeadWS:
            async def accept(self): pass
            async def send_text(self, _):
                raise RuntimeError("Connection dead")

        dws = DeadWS()
        mgr._connections.add(dws)
        await mgr.broadcast({"event_type": "test", "payload": {}})
        # Dead connection should be removed
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_sends_json(self):
        mgr = ConnectionManager()
        received: list[str] = []

        class FakeWS:
            async def accept(self): pass
            async def send_text(self, text): received.append(text)

        fw = FakeWS()
        mgr._connections.add(fw)
        await mgr.broadcast({"event_type": "signal", "payload": {"ticker": "AAPL"}})
        assert len(received) == 1
        parsed = json.loads(received[0])
        assert parsed["event_type"] == "signal"
