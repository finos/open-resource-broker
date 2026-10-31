"""Application handlers and services resolve from a bootstrapped container.

The handlers receive their collaborators through constructor injection, so a
missing registration only shows up when a handler is first built. Resolving
each one from a real container catches that at test time.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterator

import pytest

from orb.application.commands.provider_handlers import (
    ExecuteProviderOperationHandler,
    RegisterProviderStrategyHandler,
    UpdateProviderHealthHandler,
)
from orb.application.commands.request_creation_handlers import (
    CreateMachineRequestHandler,
    CreateReturnRequestHandler,
)
from orb.application.commands.request_sync_handlers import (
    PopulateMachineIdsHandler,
    SyncRequestHandler,
)
from orb.application.commands.system_handlers import (
    RefreshTemplatesHandler,
    ReloadProviderConfigHandler,
    SetConfigurationHandler,
)
from orb.application.queries.bulk_handlers import (
    GetMultipleMachinesHandler,
    GetMultipleRequestsHandler,
    GetMultipleTemplatesHandler,
)
from orb.application.queries.machine_query_handlers import ListMachinesHandler
from orb.application.queries.request_query_handlers import (
    SyncAndGetRequestHandler,
    SyncAndListActiveRequestsHandler,
    SyncAndListReturnRequestsHandler,
)
from orb.application.queries.storage_handlers import ListStorageStrategiesHandler
from orb.application.queries.system_handlers import (
    GetConfigurationSectionHandler,
    GetProviderConfigHandler,
    GetSystemConfigHandler,
    GetSystemStatusHandler,
    ValidateMCPHandler,
    ValidateProviderConfigHandler,
    ValidateStorageHandler,
)
from orb.application.queries.template_query_handlers import (
    GetConfigurationHandler,
    GetTemplateHandler,
    ListTemplatesHandler,
    ValidateTemplateHandler,
)
from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.application.services.provider_validation_service import ProviderValidationService
from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.application.services.request_reconciler import RequestReconciler
from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base.ports import ContainerPort
from orb.infrastructure.di.container import DIContainer, create_container

_RESOLVED_FROM_CONTAINER = [
    ExecuteProviderOperationHandler,
    RegisterProviderStrategyHandler,
    UpdateProviderHealthHandler,
    CreateMachineRequestHandler,
    CreateReturnRequestHandler,
    PopulateMachineIdsHandler,
    SyncRequestHandler,
    RefreshTemplatesHandler,
    ReloadProviderConfigHandler,
    SetConfigurationHandler,
    GetMultipleMachinesHandler,
    GetMultipleRequestsHandler,
    GetMultipleTemplatesHandler,
    ListMachinesHandler,
    SyncAndGetRequestHandler,
    SyncAndListActiveRequestsHandler,
    SyncAndListReturnRequestsHandler,
    ListStorageStrategiesHandler,
    GetConfigurationSectionHandler,
    GetProviderConfigHandler,
    GetSystemConfigHandler,
    GetSystemStatusHandler,
    ValidateMCPHandler,
    ValidateProviderConfigHandler,
    ValidateStorageHandler,
    GetConfigurationHandler,
    GetTemplateHandler,
    ListTemplatesHandler,
    ValidateTemplateHandler,
    DeprovisioningOrchestrator,
    ProvisioningOrchestrationService,
    ProviderValidationService,
    RequestStatusService,
    RequestReconciler,
]


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
@pytest.mark.parametrize("target", _RESOLVED_FROM_CONTAINER, ids=lambda t: t.__name__)
def test_resolves_from_bootstrapped_container(
    bootstrapped_container: DIContainer, target: type
) -> None:
    assert bootstrapped_container.get(target) is not None


@pytest.mark.unit
@pytest.mark.parametrize("target", _RESOLVED_FROM_CONTAINER, ids=lambda t: t.__name__)
def test_constructor_takes_no_container(target: type) -> None:
    params = inspect.signature(target.__init__).parameters
    assert "container" not in params
    assert all(p.annotation is not ContainerPort for p in params.values())
