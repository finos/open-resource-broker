"""A broken orchestrator module must fail server registration, not vanish silently."""

import sys

import pytest

from orb.bootstrap.server_services import _register_orchestrators
from orb.infrastructure.di.container import DIContainer


def test_registration_raises_when_an_orchestrator_module_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "orb.application.services.orchestration.list_machines", None)

    with pytest.raises(ImportError):
        _register_orchestrators(DIContainer())


def test_registration_succeeds_with_all_orchestrators_importable():
    container = DIContainer()

    _register_orchestrators(container)

    from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator

    assert container.is_registered(ListMachinesOrchestrator)
