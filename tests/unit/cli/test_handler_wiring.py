"""CLI handler classes are wired through the container and receive no container."""

from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from orb.cli import handler_wiring
from orb.infrastructure.di.container import DIContainer, create_container
from orb.interface.catalog import OPERATION_CATALOG

# Each group registrar and the handler classes it must make resolvable.
GROUPS: dict[Callable[[Any], None], list[tuple[str, str]]] = {
    handler_wiring.register_request_handlers: [
        ("orb.interface.request_command_handlers", "RequestWriteHandlers"),
        ("orb.interface.request_query_handlers", "RequestReadHandlers"),
    ],
    handler_wiring.register_machine_handlers: [
        ("orb.interface.machine_command_handlers", "MachineHandlers"),
    ],
    handler_wiring.register_template_handlers: [
        ("orb.interface.template_command_handlers", "TemplateReadHandlers"),
        ("orb.interface.template_command_handlers", "TemplateWriteHandlers"),
        ("orb.interface.templates_generate_handler", "TemplatesGenerateHandler"),
    ],
    handler_wiring.register_config_handlers: [
        ("orb.interface.config_command_handlers", "ConfigHandlers"),
    ],
    handler_wiring.register_scheduler_handlers: [
        ("orb.interface.scheduler_command_handlers", "SchedulerHandlers"),
    ],
    handler_wiring.register_storage_handlers: [
        ("orb.interface.storage_command_handlers", "StorageHandlers"),
    ],
}

HANDLER_CLASSES = [
    getattr(importlib.import_module(module), name)
    for classes in GROUPS.values()
    for module, name in classes
]

# (class, attribute holding the orchestrator, operation catalog key it must match)
ORCHESTRATOR_PARAMETERS = [
    ("RequestWriteHandlers", "_acquire_orchestrator", "request_machines"),
    ("RequestWriteHandlers", "_return_orchestrator", "return_machines"),
    ("RequestWriteHandlers", "_cancel_orchestrator", "cancel_request"),
    ("RequestWriteHandlers", "_confirm_orchestrator", "confirm_terminated"),
    ("RequestReadHandlers", "_status_orchestrator", "get_request_status"),
    ("RequestReadHandlers", "_list_requests_orchestrator", "list_requests"),
    ("RequestReadHandlers", "_list_return_requests_orchestrator", "list_return_requests"),
    ("RequestReadHandlers", "_reclaimed_orchestrator", "list_reclaimed_machines"),
    ("MachineHandlers", "_list_orchestrator", "list_machines"),
    ("MachineHandlers", "_get_orchestrator", "get_machine"),
    ("MachineHandlers", "_stop_orchestrator", "stop_machines"),
    ("MachineHandlers", "_start_orchestrator", "start_machines"),
    ("TemplateReadHandlers", "_list_orchestrator", "list_templates"),
    ("TemplateReadHandlers", "_get_orchestrator", "get_template"),
    ("TemplateReadHandlers", "_validate_orchestrator", "validate_template"),
    ("TemplateWriteHandlers", "_create_orchestrator", "create_template"),
    ("TemplateWriteHandlers", "_update_orchestrator", "update_template"),
    ("TemplateWriteHandlers", "_delete_orchestrator", "delete_template"),
    ("TemplateWriteHandlers", "_refresh_orchestrator", "refresh_templates"),
]


class _Requested:
    """Stands in for a resolved service and remembers which type was asked for."""

    def __init__(self, requested: type) -> None:
        self.requested = requested


class _RecordingContainer:
    """Container double that records registrations and every type it is asked for."""

    def __init__(self) -> None:
        self.factories: dict[type, Any] = {}
        self.resolved: list[type] = []
        self.registrations: list[type] = []
        self._instances: dict[type, Any] = {}

    def is_registered(self, cls: type) -> bool:
        return cls in self.factories

    def register_singleton(self, cls: type, factory: Any) -> None:
        self.registrations.append(cls)
        self.factories[cls] = factory

    def get(self, cls: type) -> Any:
        if cls in self.factories:
            if cls not in self._instances:
                self._instances[cls] = self.factories[cls](self)
            return self._instances[cls]
        self.resolved.append(cls)
        return _Requested(cls)


@pytest.fixture(scope="module")
def bootstrapped_container(tmp_path_factory: pytest.TempPathFactory) -> Iterator[DIContainer]:
    importlib.import_module("orb.bootstrap")  # registers the container factory
    config_dir = tmp_path_factory.mktemp("orb-config")
    mp = pytest.MonkeyPatch()
    mp.setenv("ORB_CONFIG_DIR", str(config_dir))
    mp.setenv("ORB_ROOT_DIR", str(config_dir))
    try:
        yield create_container()
    finally:
        mp.undo()


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize("registrar", list(GROUPS), ids=lambda r: r.__name__)
def test_every_handler_resolves_from_a_bootstrapped_container(
    bootstrapped_container: DIContainer, registrar: Callable[[Any], None]
) -> None:
    registrar(bootstrapped_container)

    for module, name in GROUPS[registrar]:
        handler_cls = getattr(importlib.import_module(module), name)
        assert isinstance(bootstrapped_container.get(handler_cls), handler_cls)


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize("registrar", list(GROUPS), ids=lambda r: r.__name__)
def test_a_registrar_run_twice_registers_each_class_once(registrar: Callable[[Any], None]) -> None:
    container = _RecordingContainer()

    registrar(container)
    registrar(container)

    expected = [getattr(importlib.import_module(m), n) for m, n in GROUPS[registrar]]
    assert sorted(c.__name__ for c in container.registrations) == sorted(
        c.__name__ for c in expected
    )


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize("handler_cls", HANDLER_CLASSES, ids=lambda c: c.__name__)
def test_constructors_take_no_container(handler_cls: type) -> None:
    for name, param in inspect.signature(handler_cls.__init__).parameters.items():
        assert not name.lower().endswith("container"), f"{handler_cls.__name__}.{name}"
        assert "ContainerPort" not in str(param.annotation), f"{handler_cls.__name__}.{name}"


@pytest.mark.unit
@pytest.mark.cli
def test_lazy_providers_are_not_called_while_a_handler_is_built() -> None:
    container = _RecordingContainer()
    handler_wiring.register_storage_handlers(container)
    handler_wiring.register_template_handlers(container)
    storage = next(c for c in container.factories if c.__name__ == "StorageHandlers")
    generate = next(c for c in container.factories if c.__name__ == "TemplatesGenerateHandler")

    container.get(storage)
    container.get(generate)

    resolved = {c.__name__ for c in container.resolved}
    assert resolved.isdisjoint(
        {"QueryBusPort", "ConfigurationManager", "TemplateGenerationService", "ConsolePort"}
    )


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize(("class_name", "attribute", "key"), ORCHESTRATOR_PARAMETERS)
def test_orchestrator_parameters_match_the_operation_catalog(
    class_name: str, attribute: str, key: str
) -> None:
    container = _RecordingContainer()
    for registrar in GROUPS:
        registrar(container)
    handler_cls = next(c for c in container.factories if c.__name__ == class_name)

    handler = container.get(handler_cls)

    assert getattr(handler, attribute).requested is OPERATION_CATALOG[key].orchestrator


@pytest.mark.unit
@pytest.mark.cli
def test_core_bootstrap_does_not_import_cli_wiring_or_mcp() -> None:
    probe = (
        "import json, os, sys, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "os.environ['ORB_CONFIG_DIR'] = d\n"
        "os.environ['ORB_ROOT_DIR'] = d\n"
        "import orb.bootstrap\n"
        "from orb.infrastructure.di.container import create_container\n"
        "create_container()\n"
        "print(json.dumps(sorted(m for m in sys.modules if m == 'mcp' or "
        "m.startswith(('mcp.', 'orb.interface.mcp', 'orb.cli.handler_wiring')))))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []


@pytest.mark.unit
@pytest.mark.cli
def test_the_generate_early_path_loads_only_the_template_group() -> None:
    probe = (
        "import json, sys\n"
        "from orb.cli.handler_wiring import register_template_handlers\n"
        "from orb.cli.registry import bind\n"
        "from orb.interface.templates_generate_handler import TemplatesGenerateHandler\n"
        "bind(TemplatesGenerateHandler, 'handle', group=register_template_handlers)\n"
        "from orb.infrastructure.di.container import DIContainer\n"
        "c = DIContainer()\n"
        "register_template_handlers(c)\n"
        "other = ('request_command_handlers', 'request_query_handlers', 'machine_command_handlers',"
        " 'config_command_handlers', 'scheduler_command_handlers', 'storage_command_handlers',"
        " 'system_command_handlers', 'init_command_handler', 'server_command_handlers')\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('orb.interface.') and "
        "m.rsplit('.', 1)[-1] in other or m == 'mcp')))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
