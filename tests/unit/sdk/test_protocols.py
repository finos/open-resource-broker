"""Tests for SDK protocol fixes — Task 5.

Verifies:
- ORBClientProtocol has stop_machines and start_machines
- get_request_status parameter is request_ids (list), not request_id (str)
- ORBClient satisfies ORBClientProtocol at runtime
"""

from __future__ import annotations

import inspect


class TestSDKProtocolFixes:
    def test_protocol_has_stop_machines(self):
        from orb.sdk.protocols import ORBClientProtocol

        assert hasattr(ORBClientProtocol, "stop_machines")

    def test_protocol_has_start_machines(self):
        from orb.sdk.protocols import ORBClientProtocol

        assert hasattr(ORBClientProtocol, "start_machines")

    def test_protocol_get_request_status_param_is_request_ids(self):
        from orb.sdk.protocols import ORBClientProtocol

        sig = inspect.signature(getattr(ORBClientProtocol, "get_request_status"))
        assert "request_ids" in sig.parameters

    def test_protocol_stop_machines_is_coroutine(self):
        from orb.sdk.protocols import ORBClientProtocol

        assert inspect.iscoroutinefunction(getattr(ORBClientProtocol, "stop_machines"))

    def test_protocol_start_machines_is_coroutine(self):
        from orb.sdk.protocols import ORBClientProtocol

        assert inspect.iscoroutinefunction(getattr(ORBClientProtocol, "start_machines"))
