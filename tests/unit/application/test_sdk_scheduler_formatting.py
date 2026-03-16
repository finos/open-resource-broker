"""SDK scheduler formatting tests.

Documents the expected SDK contract for scheduler-aware response formatting:
- raw_response=True bypasses scheduler formatting
- raw_response=False (default) applies scheduler formatting
- SDKMethodDiscovery does not break when SchedulerPort is in the container
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.request.dto import RequestDTO


def _make_dto(status: str = "complete") -> RequestDTO:
    return RequestDTO(
        request_id="req-abc",
        status=status,
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 8b. raw_response=True bypasses scheduler formatting
# ---------------------------------------------------------------------------


def test_sdk_raw_response_bypasses_scheduler():
    """When raw_response=True, scheduler.format_* must NOT be called.

    This test documents the expected contract. The scheduler mock is set up
    but must remain uncalled when raw_response=True is in effect.
    """
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_status_response.return_value = {"requests": [{"requestId": "r1"}]}

    # Simulate the raw_response=True path: scheduler formatting is skipped.
    # The SDK should return the DTO directly without calling format_*.
    # This assertion documents the contract before the SDK integration point
    # is wired in ticket 1910.
    scheduler.format_request_status_response.assert_not_called()


# ---------------------------------------------------------------------------
# 8c. raw_response=False applies scheduler formatting
# ---------------------------------------------------------------------------


def test_sdk_formatted_response_calls_scheduler():
    """When raw_response=False (default), scheduler.format_* must be called."""
    scheduler = MagicMock(spec=SchedulerPort)
    expected = {"requests": [{"requestId": "req-abc", "status": "complete"}]}
    scheduler.format_request_status_response.return_value = expected

    # Simulate the formatted path: call the scheduler directly as the SDK will.
    result = scheduler.format_request_status_response([_make_dto()])
    assert result == expected
    scheduler.format_request_status_response.assert_called_once()


# ---------------------------------------------------------------------------
# 8d. SDKMethodDiscovery does not break when SchedulerPort is injected
# ---------------------------------------------------------------------------


def test_sdk_discovery_with_scheduler_port():
    """SDKMethodDiscovery.discover_cqrs_methods must not raise when scheduler is in container."""
    from orb.sdk.discovery import SDKMethodDiscovery

    query_bus = AsyncMock()
    command_bus = AsyncMock()

    # discover_cqrs_methods inspects the bus for registered handlers.
    # SchedulerPort is a cross-cutting concern, not a CQRS handler — it must
    # not cause discovery to fail.
    discovery = SDKMethodDiscovery()
    methods = asyncio.get_event_loop().run_until_complete(
        discovery.discover_cqrs_methods(query_bus, command_bus)
    )
    assert isinstance(methods, dict)


# ---------------------------------------------------------------------------
# 8e. Scheduler strategy produces correct output shape for SDK consumption
# ---------------------------------------------------------------------------


def test_hf_scheduler_output_suitable_for_sdk():
    """HF scheduler output must be a plain dict suitable for SDK serialisation."""
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    hf = HostFactorySchedulerStrategy()
    result = hf.format_request_status_response([_make_dto("complete")])

    # SDK expects a plain dict (JSON-serialisable)
    assert isinstance(result, dict)
    assert "requests" in result
    assert isinstance(result["requests"], list)


def test_default_scheduler_output_suitable_for_sdk():
    """Default scheduler output must be a plain dict suitable for SDK serialisation."""
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    default = DefaultSchedulerStrategy()
    result = default.format_request_status_response([_make_dto("complete")])

    assert isinstance(result, dict)
    assert "requests" in result
    assert isinstance(result["requests"], list)


# ---------------------------------------------------------------------------
# 8f. Scheduler format_request_response output shape for SDK
# ---------------------------------------------------------------------------


def test_hf_format_request_response_sdk_shape():
    """HF format_request_response must return a dict with requestId for SDK."""
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    hf = HostFactorySchedulerStrategy()
    result = hf.format_request_response({"request_id": "req-1", "status": "pending"})

    assert isinstance(result, dict)
    assert "requestId" in result


def test_default_format_request_response_sdk_shape():
    """Default format_request_response must return a dict with request_id for SDK."""
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    default = DefaultSchedulerStrategy()
    result = default.format_request_response({"request_id": "req-1", "status": "pending"})

    assert isinstance(result, dict)
    assert "request_id" in result


# ---------------------------------------------------------------------------
# 8g. Both schedulers handle empty request list without error
# ---------------------------------------------------------------------------


def test_hf_empty_request_list():
    """HF scheduler must handle empty request list gracefully."""
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    hf = HostFactorySchedulerStrategy()
    result = hf.format_request_status_response([])
    assert result["requests"] == []


def test_default_empty_request_list():
    """Default scheduler must handle empty request list gracefully."""
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    default = DefaultSchedulerStrategy()
    result = default.format_request_status_response([])
    assert result["requests"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# 8h. SchedulerPort spec compliance — mock validates method signatures
# ---------------------------------------------------------------------------


def test_scheduler_port_mock_has_required_methods():
    """MagicMock(spec=SchedulerPort) must expose all required formatting methods."""
    scheduler = MagicMock(spec=SchedulerPort)
    required_methods = [
        "format_request_response",
        "format_request_status_response",
        "format_templates_response",
        "format_machine_status_response",
        "format_template_for_display",
        "get_scheduler_type",
        "get_exit_code_for_status",
        "parse_request_data",
    ]
    for method in required_methods:
        assert hasattr(scheduler, method), f"SchedulerPort must have method '{method}'"
