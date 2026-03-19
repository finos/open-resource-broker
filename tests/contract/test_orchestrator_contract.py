"""CLI=REST output contract tests.

Asserts that CLI handlers and REST endpoints return structurally identical
output for the same operation. Uses a real DI container with a mock provider
(no real AWS calls, no moto required).

Operations covered:
  1. list_machines   — handle_list_machines   vs GET /api/v1/machines/
  2. list_requests   — handle_list_requests   vs GET /api/v1/requests/
  3. get_request_status — handle_get_request_status vs GET /api/v1/requests/{id}/status
  4. list_templates  — handle_list_templates  vs GET /api/v1/templates/
"""

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("fastapi not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs: Any) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for CLI handler calls."""
    defaults: dict[str, Any] = {
        "all": False,
        "provider": None,
        "status": None,
        "request_id": None,
        "request_ids": [],
        "flag_request_ids": [],
        "machine_ids": [],
        "flag_machine_ids": [],
        "template_id": None,
        "input_data": None,
        "provider_api": None,
        "active_only": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _top_level_keys(result: Any) -> set[str]:
    """Return the top-level keys of a dict result, unwrapping tuple returns."""
    from orb.application.dto.interface_response import InterfaceResponse

    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, InterfaceResponse):
        result = result.data
    assert isinstance(result, dict), f"Expected dict, got {type(result)}: {result!r}"
    return set(result.keys())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_query_bus() -> MagicMock:
    bus = MagicMock()
    bus.execute = AsyncMock(return_value=[])
    return bus


def _make_machine_dto() -> Any:
    from orb.application.machine.dto import MachineDTO

    return MachineDTO(
        machine_id="machine-001",
        name="test-machine",
        status="running",
        instance_type="t3.medium",
        private_ip="10.0.0.1",
        result="succeed",
    )


def _make_request_dict() -> dict[str, Any]:
    from datetime import datetime, timezone

    from orb.application.request.dto import RequestDTO

    dto = RequestDTO(
        request_id="req-00000000-0000-0000-0000-000000000001",
        status="pending",
        requested_count=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        machine_references=[],
    )
    return dto.to_dict()


def _make_request_dto_obj() -> Any:
    from datetime import datetime, timezone

    from orb.application.request.dto import RequestDTO

    return RequestDTO(
        request_id="req-00000000-0000-0000-0000-000000000001",
        status="pending",
        requested_count=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        machine_references=[],
    )


def _make_template_dto() -> Any:
    from orb.infrastructure.template.dtos import TemplateDTO

    return TemplateDTO(
        template_id="tmpl-001",
        name="test",
        provider_api="EC2Fleet",
    )


@pytest.fixture
def mock_command_bus() -> MagicMock:
    bus = MagicMock()
    bus.execute = AsyncMock(return_value=None)
    return bus


def _make_scheduler_strategy(scheduler_type: str) -> Any:
    """Return a real scheduler strategy instance (no external deps)."""
    from unittest.mock import MagicMock

    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()

    if scheduler_type == "hostfactory":
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        return HostFactorySchedulerStrategy(logger=logger)
    else:
        from orb.infrastructure.scheduler.default.default_strategy import (
            DefaultSchedulerStrategy,
        )

        return DefaultSchedulerStrategy(logger=logger)


@pytest.fixture(params=["default", "hostfactory"])
def scheduler_type(request: pytest.FixtureRequest) -> str:
    return str(request.param)  # type: ignore[return-value]


@pytest.fixture
def rest_client_for(scheduler_type: str) -> Any:  # type: ignore[return]
    """FastAPI TestClient with DI container wired to a real scheduler strategy."""
    from orb.api.server import create_fastapi_app
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()

    strategy = _make_scheduler_strategy(scheduler_type)

    # Patch the DI container's get() so the REST layer gets our mock buses
    # and the real scheduler strategy.
    mock_qbus = MagicMock()
    mock_qbus.execute = AsyncMock(return_value=[])
    mock_cbus = MagicMock()
    mock_cbus.execute = AsyncMock(return_value=None)

    original_get = None

    def _patched_get(service_type: Any) -> Any:
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.infrastructure.di.buses import CommandBus, QueryBus

        if service_type is SchedulerPort:
            return strategy
        if service_type is QueryBus:
            return mock_qbus
        if service_type is CommandBus:
            return mock_cbus
        # Fall through to real container for everything else
        return original_get(service_type)  # type: ignore[misc]

    app = create_fastapi_app(None)
    container = get_container()
    original_get = container.get  # type: ignore[assignment]
    container.get = _patched_get  # type: ignore[method-assign]

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client  # type: ignore[misc]

    container.get = original_get  # type: ignore[method-assign]
    reset_container()


# ---------------------------------------------------------------------------
# Shared CLI call helper
# ---------------------------------------------------------------------------


async def _call_cli(
    handler_fn: Any,
    args: argparse.Namespace,
    strategy: Any,
    query_return: Any = None,
) -> dict[str, Any]:
    """Invoke a CLI handler with the DI container wired to *strategy*."""
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.buses import CommandBus, QueryBus
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()

    mock_qbus = MagicMock()
    mock_qbus.execute = AsyncMock(return_value=query_return if query_return is not None else [])
    mock_cbus = MagicMock()
    mock_cbus.execute = AsyncMock(return_value=None)

    container = get_container()
    original_get = container.get

    def _patched_get(service_type: Any) -> Any:
        if service_type is SchedulerPort:
            return strategy
        if service_type is QueryBus:
            return mock_qbus
        if service_type is CommandBus:
            return mock_cbus
        return original_get(service_type)

    container.get = _patched_get  # type: ignore[method-assign]
    try:
        result = await handler_fn(args)
    finally:
        container.get = original_get  # type: ignore[method-assign]
        reset_container()

    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. list_machines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_machines_cli_equals_rest_top_level_keys(scheduler_type: str) -> None:
    """CLI handle_list_machines and GET /api/v1/machines/ return the same top-level keys."""
    from orb.interface.machine_command_handlers import handle_list_machines

    strategy = _make_scheduler_strategy(scheduler_type)
    machine_dto = _make_machine_dto()

    # CLI call
    cli_result = await _call_cli(
        handle_list_machines, _make_args(), strategy, query_return=[machine_dto]
    )
    cli_keys = _top_level_keys(cli_result)

    # REST call
    from orb.api.server import create_fastapi_app
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()
    mock_qbus = MagicMock()
    mock_qbus.execute = AsyncMock(return_value=[machine_dto])

    app = create_fastapi_app(None)
    container = get_container()
    original_get = container.get

    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.buses import QueryBus

    def _patched(svc: Any) -> Any:
        if svc is SchedulerPort:
            return strategy
        if svc is QueryBus:
            return mock_qbus
        return original_get(svc)

    container.get = _patched  # type: ignore[method-assign]
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/machines/")
    finally:
        container.get = original_get  # type: ignore[method-assign]
        reset_container()

    assert response.status_code == 200, f"REST returned {response.status_code}: {response.text}"
    rest_keys = set(response.json().keys())

    assert cli_keys == rest_keys, (
        f"[{scheduler_type}] list_machines key mismatch:\n"
        f"  CLI keys:  {sorted(cli_keys)}\n"
        f"  REST keys: {sorted(rest_keys)}"
    )


# ---------------------------------------------------------------------------
# 2. list_requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requests_cli_equals_rest_top_level_keys(scheduler_type: str) -> None:
    """CLI handle_list_requests and GET /api/v1/requests/ return the same top-level keys."""
    from orb.interface.request_command_handlers import handle_list_requests

    strategy = _make_scheduler_strategy(scheduler_type)
    request_dict = _make_request_dict()

    cli_result = await _call_cli(
        handle_list_requests, _make_args(), strategy, query_return=[request_dict]
    )
    cli_keys = _top_level_keys(cli_result)

    from orb.api.server import create_fastapi_app
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()
    mock_qbus = MagicMock()
    mock_qbus.execute = AsyncMock(return_value=[request_dict])

    app = create_fastapi_app(None)
    container = get_container()
    original_get = container.get

    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.buses import QueryBus

    def _patched(svc: Any) -> Any:
        if svc is SchedulerPort:
            return strategy
        if svc is QueryBus:
            return mock_qbus
        return original_get(svc)

    container.get = _patched  # type: ignore[method-assign]
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/requests/")
    finally:
        container.get = original_get  # type: ignore[method-assign]
        reset_container()

    assert response.status_code == 200, f"REST returned {response.status_code}: {response.text}"
    rest_keys = set(response.json().keys())

    assert cli_keys == rest_keys, (
        f"[{scheduler_type}] list_requests key mismatch:\n"
        f"  CLI keys:  {sorted(cli_keys)}\n"
        f"  REST keys: {sorted(rest_keys)}"
    )


# ---------------------------------------------------------------------------
# 3. get_request_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_status_cli_equals_rest_top_level_keys(scheduler_type: str) -> None:
    """CLI handle_get_request_status and GET /api/v1/requests/{id}/status return same keys."""
    from orb.interface.request_command_handlers import handle_get_request_status

    req_id = "req-00000000-0000-0000-0000-000000000001"
    request_dto = _make_request_dto_obj()

    strategy = _make_scheduler_strategy(scheduler_type)

    # CLI call — wire query bus to return the DTO
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.buses import CommandBus, QueryBus
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()
    mock_qbus = MagicMock()
    mock_qbus.execute = AsyncMock(return_value=request_dto)
    mock_cbus = MagicMock()
    mock_cbus.execute = AsyncMock(return_value=None)

    container = get_container()
    original_get = container.get

    def _patched_cli(svc: Any) -> Any:
        if svc is SchedulerPort:
            return strategy
        if svc is QueryBus:
            return mock_qbus
        if svc is CommandBus:
            return mock_cbus
        return original_get(svc)

    container.get = _patched_cli  # type: ignore[method-assign]
    try:
        cli_result = await handle_get_request_status(_make_args(request_id=req_id))
    finally:
        container.get = original_get  # type: ignore[method-assign]
        reset_container()

    cli_keys = _top_level_keys(cli_result)

    # REST call — wire query bus symmetrically with the CLI path.
    from orb.api.server import create_fastapi_app
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()

    mock_qbus_rest = MagicMock()
    mock_qbus_rest.execute = AsyncMock(return_value=request_dto)

    app = create_fastapi_app(None)
    container2 = get_container()
    original_get2 = container2.get

    def _patched_rest(svc: Any) -> Any:
        if svc is SchedulerPort:
            return strategy
        if svc is QueryBus:
            return mock_qbus_rest
        return original_get2(svc)

    container2.get = _patched_rest  # type: ignore[method-assign]
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/api/v1/requests/{req_id}/status")
    finally:
        container2.get = original_get2  # type: ignore[method-assign]
        reset_container()

    assert response.status_code == 200, f"REST returned {response.status_code}: {response.text}"
    rest_keys = set(response.json().keys())

    assert cli_keys == rest_keys, (
        f"[{scheduler_type}] get_request_status key mismatch:\n"
        f"  CLI keys:  {sorted(cli_keys)}\n"
        f"  REST keys: {sorted(rest_keys)}"
    )


# ---------------------------------------------------------------------------
# 4. list_templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_cli_equals_rest_top_level_keys(scheduler_type: str) -> None:
    """CLI handle_list_templates and GET /api/v1/templates/ return the same top-level keys."""
    from orb.interface.template_command_handlers import handle_list_templates

    strategy = _make_scheduler_strategy(scheduler_type)
    template_dto = _make_template_dto()

    cli_result = await _call_cli(
        handle_list_templates, _make_args(), strategy, query_return=[template_dto]
    )
    cli_keys = _top_level_keys(cli_result)

    from orb.api.server import create_fastapi_app
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()
    mock_qbus = MagicMock()
    mock_qbus.execute = AsyncMock(return_value=[template_dto])

    app = create_fastapi_app(None)
    container = get_container()
    original_get = container.get

    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.buses import QueryBus

    def _patched(svc: Any) -> Any:
        if svc is SchedulerPort:
            return strategy
        if svc is QueryBus:
            return mock_qbus
        return original_get(svc)

    container.get = _patched  # type: ignore[method-assign]
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/templates/")
    finally:
        container.get = original_get  # type: ignore[method-assign]
        reset_container()

    assert response.status_code == 200, f"REST returned {response.status_code}: {response.text}"
    rest_keys = set(response.json().keys())

    assert cli_keys == rest_keys, (
        f"[{scheduler_type}] list_templates key mismatch:\n"
        f"  CLI keys:  {sorted(cli_keys)}\n"
        f"  REST keys: {sorted(rest_keys)}"
    )


# ---------------------------------------------------------------------------
# 5. Structural invariants — both interfaces return a dict (not a list/string)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_machines_cli_returns_dict(scheduler_type: str) -> None:
    """handle_list_machines always returns a dict, never a bare list."""
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.machine_command_handlers import handle_list_machines

    strategy = _make_scheduler_strategy(scheduler_type)
    result = await _call_cli(handle_list_machines, _make_args(), strategy)
    unwrapped = result[0] if isinstance(result, tuple) else result  # type: ignore[index]
    if isinstance(unwrapped, InterfaceResponse):
        unwrapped = unwrapped.data
    assert isinstance(unwrapped, dict), f"[{scheduler_type}] Expected dict, got {type(unwrapped)}"


@pytest.mark.asyncio
async def test_list_requests_cli_returns_dict(scheduler_type: str) -> None:
    """handle_list_requests always returns a dict, never a bare list."""
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.request_command_handlers import handle_list_requests

    strategy = _make_scheduler_strategy(scheduler_type)
    result = await _call_cli(handle_list_requests, _make_args(), strategy)
    unwrapped = result[0] if isinstance(result, tuple) else result  # type: ignore[index]
    if isinstance(unwrapped, InterfaceResponse):
        unwrapped = unwrapped.data
    assert isinstance(unwrapped, dict), f"[{scheduler_type}] Expected dict, got {type(unwrapped)}"


@pytest.mark.asyncio
async def test_list_templates_cli_returns_dict(scheduler_type: str) -> None:
    """handle_list_templates always returns a dict, never a bare list."""
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.template_command_handlers import handle_list_templates

    strategy = _make_scheduler_strategy(scheduler_type)
    result = await _call_cli(handle_list_templates, _make_args(), strategy)
    unwrapped = result[0] if isinstance(result, tuple) else result  # type: ignore[index]
    if isinstance(unwrapped, InterfaceResponse):
        unwrapped = unwrapped.data
    assert isinstance(unwrapped, dict), f"[{scheduler_type}] Expected dict, got {type(unwrapped)}"
