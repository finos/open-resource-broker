"""Registry parity and real dispatch through ``bind`` for the migrated handler classes."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.ports.scheduler_port import SchedulerPort
from orb.cli import registry
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import DomainException
from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.error.exception_handler import get_exception_handler
from orb.interface.response_formatting_service import ResponseFormattingService

# Snapshot of every (resource, action) the CLI registers.
EXPECTED_KEYS = {
    ("config", "get"),
    ("config", "reload"),
    ("config", "set"),
    ("config", "show"),
    ("config", "validate"),
    ("infrastructure", "discover"),
    ("infrastructure", "show"),
    ("infrastructure", "validate"),
    ("init", "*"),
    ("machines", "get-bulk"),
    ("machines", "list"),
    ("machines", "request"),
    ("machines", "return"),
    ("machines", "show"),
    ("machines", "start"),
    ("machines", "status"),
    ("machines", "stop"),
    ("machines", "terminate"),
    ("mcp", "serve"),
    ("mcp", "validate"),
    ("providers", "add"),
    ("providers", "exec"),
    ("providers", "get"),
    ("providers", "get-default"),
    ("providers", "health"),
    ("providers", "list"),
    ("providers", "metrics"),
    ("providers", "remove"),
    ("providers", "select"),
    ("providers", "set-default"),
    ("providers", "show"),
    ("providers", "update"),
    ("requests", "cancel"),
    ("requests", "confirm-terminated"),
    ("requests", "get-bulk"),
    ("requests", "list"),
    ("requests", "list-returns"),
    ("requests", "show"),
    ("requests", "status"),
    ("requests", "watch"),
    ("scheduler", "list"),
    ("scheduler", "show"),
    ("scheduler", "validate"),
    ("server", "logs"),
    ("server", "reload"),
    ("server", "restart"),
    ("server", "start"),
    ("server", "status"),
    ("server", "stop"),
    ("server", "ui-export"),
    ("storage", "health"),
    ("storage", "list"),
    ("storage", "metrics"),
    ("storage", "migrate"),
    ("storage", "show"),
    ("storage", "test"),
    ("storage", "validate"),
    ("system", "health"),
    ("system", "metrics"),
    ("system", "reload"),
    ("system", "status"),
    ("templates", "create"),
    ("templates", "delete"),
    ("templates", "generate"),
    ("templates", "get-bulk"),
    ("templates", "list"),
    ("templates", "refresh"),
    ("templates", "show"),
    ("templates", "update"),
    ("templates", "validate"),
}

# (resource, action) -> (handler class name, method) for every bound entry.
BOUND = {
    ("config", "get"): ("ConfigHandlers", "get_configuration"),
    ("config", "set"): ("ConfigHandlers", "set_configuration"),
    ("config", "show"): ("ConfigHandlers", "get_system_config"),
    ("config", "validate"): ("ConfigHandlers", "validate_provider_config"),
    ("machines", "get-bulk"): ("MachineHandlers", "get_multiple_machines"),
    ("machines", "list"): ("MachineHandlers", "list_machines"),
    ("machines", "request"): ("RequestWriteHandlers", "request_machines"),
    ("machines", "return"): ("RequestWriteHandlers", "request_return_machines"),
    ("machines", "show"): ("MachineHandlers", "get_machine"),
    ("machines", "start"): ("MachineHandlers", "start_machines"),
    ("machines", "status"): ("MachineHandlers", "get_machine_status"),
    ("machines", "stop"): ("MachineHandlers", "stop_machines"),
    ("machines", "terminate"): ("RequestWriteHandlers", "request_return_machines"),
    ("requests", "cancel"): ("RequestWriteHandlers", "cancel_request"),
    ("requests", "confirm-terminated"): ("RequestWriteHandlers", "confirm_terminated"),
    ("requests", "get-bulk"): ("RequestReadHandlers", "get_multiple_requests"),
    ("requests", "list"): ("RequestReadHandlers", "list_requests"),
    ("requests", "list-returns"): ("RequestReadHandlers", "get_return_requests"),
    ("requests", "show"): ("RequestReadHandlers", "get_request_status"),
    ("requests", "status"): ("RequestReadHandlers", "get_request_status"),
    ("requests", "watch"): ("RequestReadHandlers", "watch_request_status"),
    ("scheduler", "list"): ("SchedulerHandlers", "list_scheduler_strategies"),
    ("scheduler", "show"): ("SchedulerHandlers", "show_scheduler_config"),
    ("scheduler", "validate"): ("SchedulerHandlers", "validate_scheduler_config"),
    ("storage", "health"): ("StorageHandlers", "storage_health"),
    ("storage", "list"): ("StorageHandlers", "list_storage_strategies"),
    ("storage", "metrics"): ("StorageHandlers", "storage_metrics"),
    ("storage", "migrate"): ("StorageHandlers", "storage_migrate"),
    ("storage", "show"): ("StorageHandlers", "show_storage_config"),
    ("storage", "test"): ("StorageHandlers", "test_storage"),
    ("storage", "validate"): ("StorageHandlers", "validate_storage_config"),
    ("templates", "create"): ("TemplateWriteHandlers", "create_template"),
    ("templates", "delete"): ("TemplateWriteHandlers", "delete_template"),
    ("templates", "generate"): ("TemplatesGenerateHandler", "handle"),
    ("templates", "get-bulk"): ("TemplateReadHandlers", "get_multiple_templates"),
    ("templates", "list"): ("TemplateReadHandlers", "list_templates"),
    ("templates", "refresh"): ("TemplateWriteHandlers", "refresh_templates"),
    ("templates", "show"): ("TemplateReadHandlers", "get_template"),
    ("templates", "update"): ("TemplateWriteHandlers", "update_template"),
    ("templates", "validate"): ("TemplateReadHandlers", "validate_template"),
}


@pytest.fixture
def populated_registry() -> Iterator[None]:
    registry._REGISTRY.clear()
    registry.build_registry()
    yield
    registry._REGISTRY.clear()


@pytest.mark.unit
@pytest.mark.cli
def test_registry_keeps_every_command(populated_registry: None) -> None:
    assert set(registry._REGISTRY) == EXPECTED_KEYS


@pytest.mark.unit
@pytest.mark.cli
def test_build_registry_is_idempotent(populated_registry: None) -> None:
    first = dict(registry._REGISTRY)

    registry.build_registry()

    assert registry._REGISTRY == first


@pytest.mark.unit
@pytest.mark.cli
def test_each_bound_entry_dispatches_to_its_class_and_method(populated_registry: None) -> None:
    actual = {
        key: (handler.target[0].__name__, handler.target[1])  # type: ignore[attr-defined]
        for key, handler in registry._REGISTRY.items()
        if hasattr(handler, "target")
    }

    assert actual == BOUND


# ---------------------------------------------------------------------------
# Real dispatch: a container with fake ports, real wiring, no handler mocks.
# ---------------------------------------------------------------------------

ORCHESTRATOR_MODULES = {
    "AcquireMachinesOrchestrator": "acquire_machines",
    "CancelRequestOrchestrator": "cancel_request",
    "ConfirmTerminatedOrchestrator": "confirm_terminated",
    "CreateTemplateOrchestrator": "create_template",
    "DeleteTemplateOrchestrator": "delete_template",
    "GetMachineOrchestrator": "get_machine",
    "GetRequestStatusOrchestrator": "get_request_status",
    "GetSchedulerConfigOrchestrator": "get_scheduler_config",
    "GetStorageConfigOrchestrator": "get_storage_config",
    "GetTemplateOrchestrator": "get_template",
    "ListMachinesOrchestrator": "list_machines",
    "ListReclaimedMachinesOrchestrator": "list_reclaimed_machines",
    "ListRequestsOrchestrator": "list_requests",
    "ListReturnRequestsOrchestrator": "list_return_requests",
    "ListSchedulerStrategiesOrchestrator": "list_scheduler_strategies",
    "ListStorageStrategiesOrchestrator": "list_storage_strategies",
    "ListTemplatesOrchestrator": "list_templates",
    "RefreshTemplatesOrchestrator": "refresh_templates",
    "ReturnMachinesOrchestrator": "return_machines",
    "StartMachinesOrchestrator": "start_machines",
    "StopMachinesOrchestrator": "stop_machines",
    "UpdateTemplateOrchestrator": "update_template",
    "ValidateTemplateOrchestrator": "validate_template",
    "WatchRequestStatusOrchestrator": "watch_request_status",
}


def _orchestrator_classes() -> dict[str, type]:
    import importlib

    return {
        name: getattr(
            importlib.import_module(f"orb.application.services.orchestration.{module}"), name
        )
        for name, module in ORCHESTRATOR_MODULES.items()
    }


def _formatter() -> MagicMock:
    """A formatter whose renderers all return an empty, successful response."""
    from orb.application.dto.interface_response import InterfaceResponse

    formatter = MagicMock(spec=ResponseFormattingService)
    for name in dir(ResponseFormattingService):
        if name.startswith("format_"):
            getattr(formatter, name).return_value = InterfaceResponse(data={}, exit_code=0)
    formatter.status_poll_exit_code.return_value = 0
    return formatter


@pytest.fixture
def fake_container(populated_registry: None) -> Iterator[tuple[DIContainer, dict[str, AsyncMock]]]:
    container = DIContainer()
    orchestrators: dict[str, AsyncMock] = {}
    for name, cls in _orchestrator_classes().items():
        fake = AsyncMock(spec=cls)
        orchestrators[name] = fake
        container.register_instance(cls, fake)
    for port in (QueryBusPort, CommandBusPort):
        container.register_instance(port, AsyncMock(spec=port))
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.is_provider_reclaim_poll.return_value = False
    container.register_instance(SchedulerPort, scheduler)
    container.register_instance(UnitOfWorkFactory, MagicMock(spec=UnitOfWorkFactory))
    container.register_instance(ConsolePort, MagicMock(spec=ConsolePort))
    container.register_instance(ResponseFormattingService, _formatter())
    with patch("orb.infrastructure.di.container.get_container", return_value=container):
        yield container, orchestrators


def _args(**kwargs: Any) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


# (resource, action, args, the one orchestrator that must run)
DISPATCHES = [
    ("requests", "show", {"request_id": "req-1"}, "GetRequestStatusOrchestrator"),
    ("requests", "list", {}, "ListRequestsOrchestrator"),
    ("requests", "list-returns", {}, "ListReturnRequestsOrchestrator"),
    ("requests", "cancel", {"request_id": "req-1"}, "CancelRequestOrchestrator"),
    (
        "machines",
        "request",
        {"template_id": "tpl", "machine_count": 2},
        "AcquireMachinesOrchestrator",
    ),
    ("machines", "return", {"machine_ids": ["m-1"]}, "ReturnMachinesOrchestrator"),
    ("machines", "list", {}, "ListMachinesOrchestrator"),
    ("machines", "show", {"machine_id": "m-1"}, "GetMachineOrchestrator"),
    ("machines", "stop", {"machine_ids": ["m-1"], "force": True}, "StopMachinesOrchestrator"),
    ("machines", "start", {"machine_ids": ["m-1"]}, "StartMachinesOrchestrator"),
    ("templates", "list", {}, "ListTemplatesOrchestrator"),
    ("templates", "show", {"template_id": "tpl"}, "GetTemplateOrchestrator"),
    ("templates", "validate", {"template_id": "tpl"}, "ValidateTemplateOrchestrator"),
    ("templates", "delete", {"template_id": "tpl", "force": True}, "DeleteTemplateOrchestrator"),
    ("templates", "refresh", {}, "RefreshTemplatesOrchestrator"),
    ("scheduler", "list", {}, "ListSchedulerStrategiesOrchestrator"),
    ("scheduler", "show", {}, "GetSchedulerConfigOrchestrator"),
    ("storage", "list", {}, "ListStorageStrategiesOrchestrator"),
    ("storage", "show", {}, "GetStorageConfigOrchestrator"),
]


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource", "action", "namespace", "expected"),
    DISPATCHES,
    ids=[f"{r}-{a}" for r, a, _, _ in DISPATCHES],
)
async def test_command_runs_through_its_own_orchestrator(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
    resource: str,
    action: str,
    namespace: dict[str, Any],
    expected: str,
) -> None:
    _, orchestrators = fake_container
    handler = registry.lookup(resource, action)
    assert handler is not None

    await handler(_args(**namespace))

    awaited = {name for name, fake in orchestrators.items() if fake.execute.await_count}
    assert awaited == {expected}


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_a_provider_reclaim_poll_runs_through_the_reclaimed_orchestrator(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    container, orchestrators = fake_container
    container.get(SchedulerPort).is_provider_reclaim_poll.return_value = True  # type: ignore[attr-defined]
    handler = registry.lookup("requests", "list-returns")
    assert handler is not None

    await handler(_args(input_data={"machines": [{"name": "m-1"}]}))

    awaited = {name for name, fake in orchestrators.items() if fake.execute.await_count}
    assert awaited == {"ListReclaimedMachinesOrchestrator"}


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_query_bus_commands_use_the_bus(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    container, _ = fake_container
    bus = container.get(QueryBusPort)
    bus.execute.return_value = {"status": "healthy"}  # type: ignore[attr-defined]

    for resource, action, namespace in [
        ("scheduler", "validate", {}),
        ("storage", "health", {}),
        ("storage", "metrics", {}),
        ("storage", "test", {}),
        ("storage", "validate", {}),
        ("config", "show", {}),
        ("config", "get", {"key": "provider.default"}),
        ("config", "validate", {}),
    ]:
        handler = registry.lookup(resource, action)
        assert handler is not None
        bus.execute.reset_mock()  # type: ignore[attr-defined]
        await handler(_args(**namespace))
        bus.execute.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_config_set_uses_the_command_bus(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    container, _ = fake_container
    bus = container.get(CommandBusPort)
    bus.execute.return_value = {"success": True}  # type: ignore[attr-defined]
    handler = registry.lookup("config", "set")
    assert handler is not None

    await handler(_args(key="a.b", value="1", persist=False))

    bus.execute.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_dispatching_twice_registers_each_group_once_per_container(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    container, _ = fake_container
    registered: list[type] = []
    original = container.register_singleton
    container.register_singleton = lambda cls, factory=None: (  # type: ignore[method-assign]
        registered.append(cls),
        original(cls, factory),
    )[1]
    handler = registry.lookup("requests", "list")
    assert handler is not None

    await handler(_args())
    await handler(_args())

    assert sorted(c.__name__ for c in registered) == [
        "RequestReadHandlers",
        "RequestWriteHandlers",
    ]


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_a_new_container_gets_its_own_registrations(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    container, orchestrators = fake_container
    handler = registry.lookup("requests", "list")
    assert handler is not None
    await handler(_args())
    first = container.get(registry.lookup("requests", "list").target[0])  # type: ignore[union-attr]

    fresh = DIContainer()
    for name, cls in _orchestrator_classes().items():
        fresh.register_instance(cls, orchestrators[name])
    fresh.register_instance(QueryBusPort, AsyncMock(spec=QueryBusPort))
    fresh.register_instance(SchedulerPort, MagicMock(spec=SchedulerPort))
    fresh.register_instance(ResponseFormattingService, MagicMock(spec=ResponseFormattingService))
    with patch("orb.infrastructure.di.container.get_container", return_value=fresh):
        await handler(_args())

    assert fresh.is_registered(type(first))


# ---------------------------------------------------------------------------
# Failure paths through bind
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_a_failing_method_is_handled_once_with_the_same_context(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    _, orchestrators = fake_container
    orchestrators["ListRequestsOrchestrator"].execute.side_effect = DomainException("boom")
    handler = registry.lookup("requests", "list")
    assert handler is not None
    contexts: list[Any] = []

    def record(self: Any, exc: Exception, context: Any, **kwargs: Any) -> Exception:
        contexts.append(context)
        return exc

    with patch.object(type(get_exception_handler()), "handle", record):
        with pytest.raises(DomainException, match="boom"):
            await handler(_args())

    assert [(c.operation, c.layer) for c in contexts] == [("list_requests", "interface")]


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_a_failing_collaborator_is_handled_once_before_the_method_runs(
    fake_container: tuple[DIContainer, dict[str, AsyncMock]],
) -> None:
    container, orchestrators = fake_container

    def broken(_: Any) -> Any:
        raise DomainException("collaborator failed")

    list_requests = _orchestrator_classes()["ListRequestsOrchestrator"]
    container.unregister(list_requests)
    container.register_singleton(list_requests, broken)
    handler = registry.lookup("requests", "list")
    assert handler is not None
    contexts: list[Any] = []

    def record(self: Any, exc: Exception, context: Any, **kwargs: Any) -> Exception:
        contexts.append(context)
        return exc

    with patch.object(type(get_exception_handler()), "handle", record):
        with pytest.raises(Exception, match="collaborator failed"):
            await handler(_args())

    assert [(c.operation, c.layer) for c in contexts] == [
        ("RequestReadHandlers.list_requests", "interface")
    ]
    assert not any(fake.execute.await_count for fake in orchestrators.values())
