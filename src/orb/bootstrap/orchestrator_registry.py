from __future__ import annotations

from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.di.container import DIContainer


def register_orchestrators(container: DIContainer) -> None:
    """Register all orchestrators with the DI container."""
    from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
    from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
    from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
    from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
    from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
    from orb.application.services.orchestration.get_provider_config import (
        GetProviderConfigOrchestrator,
    )
    from orb.application.services.orchestration.get_provider_health import (
        GetProviderHealthOrchestrator,
    )
    from orb.application.services.orchestration.get_provider_metrics import (
        GetProviderMetricsOrchestrator,
    )
    from orb.application.services.orchestration.get_request_status import (
        GetRequestStatusOrchestrator,
    )
    from orb.application.services.orchestration.get_scheduler_config import (
        GetSchedulerConfigOrchestrator,
    )
    from orb.application.services.orchestration.get_storage_config import (
        GetStorageConfigOrchestrator,
    )
    from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
    from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
    from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
    from orb.application.services.orchestration.list_return_requests import (
        ListReturnRequestsOrchestrator,
    )
    from orb.application.services.orchestration.list_scheduler_strategies import (
        ListSchedulerStrategiesOrchestrator,
    )
    from orb.application.services.orchestration.list_storage_strategies import (
        ListStorageStrategiesOrchestrator,
    )
    from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
    from orb.application.services.orchestration.refresh_templates import (
        RefreshTemplatesOrchestrator,
    )
    from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
    from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator
    from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
    from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
    from orb.application.services.orchestration.validate_template import (
        ValidateTemplateOrchestrator,
    )
    from orb.application.services.orchestration.watch_request_status import (
        WatchRequestStatusOrchestrator,
    )

    if not container.is_registered(AcquireMachinesOrchestrator):
        container.register_singleton(
            AcquireMachinesOrchestrator,
            lambda c: AcquireMachinesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetRequestStatusOrchestrator):
        container.register_singleton(
            GetRequestStatusOrchestrator,
            lambda c: GetRequestStatusOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListRequestsOrchestrator):
        container.register_singleton(
            ListRequestsOrchestrator,
            lambda c: ListRequestsOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ReturnMachinesOrchestrator):
        container.register_singleton(
            ReturnMachinesOrchestrator,
            lambda c: ReturnMachinesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(CancelRequestOrchestrator):
        container.register_singleton(
            CancelRequestOrchestrator,
            lambda c: CancelRequestOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListMachinesOrchestrator):
        container.register_singleton(
            ListMachinesOrchestrator,
            lambda c: ListMachinesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetMachineOrchestrator):
        container.register_singleton(
            GetMachineOrchestrator,
            lambda c: GetMachineOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListTemplatesOrchestrator):
        container.register_singleton(
            ListTemplatesOrchestrator,
            lambda c: ListTemplatesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListReturnRequestsOrchestrator):
        container.register_singleton(
            ListReturnRequestsOrchestrator,
            lambda c: ListReturnRequestsOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetTemplateOrchestrator):
        container.register_singleton(
            GetTemplateOrchestrator,
            lambda c: GetTemplateOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(CreateTemplateOrchestrator):
        container.register_singleton(
            CreateTemplateOrchestrator,
            lambda c: CreateTemplateOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(UpdateTemplateOrchestrator):
        container.register_singleton(
            UpdateTemplateOrchestrator,
            lambda c: UpdateTemplateOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(DeleteTemplateOrchestrator):
        container.register_singleton(
            DeleteTemplateOrchestrator,
            lambda c: DeleteTemplateOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ValidateTemplateOrchestrator):
        container.register_singleton(
            ValidateTemplateOrchestrator,
            lambda c: ValidateTemplateOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(RefreshTemplatesOrchestrator):
        container.register_singleton(
            RefreshTemplatesOrchestrator,
            lambda c: RefreshTemplatesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(StopMachinesOrchestrator):
        container.register_singleton(
            StopMachinesOrchestrator,
            lambda c: StopMachinesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(StartMachinesOrchestrator):
        container.register_singleton(
            StartMachinesOrchestrator,
            lambda c: StartMachinesOrchestrator(
                command_bus=c.get(CommandBus),
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetProviderHealthOrchestrator):
        container.register_singleton(
            GetProviderHealthOrchestrator,
            lambda c: GetProviderHealthOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListProvidersOrchestrator):
        container.register_singleton(
            ListProvidersOrchestrator,
            lambda c: ListProvidersOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetProviderConfigOrchestrator):
        container.register_singleton(
            GetProviderConfigOrchestrator,
            lambda c: GetProviderConfigOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetProviderMetricsOrchestrator):
        container.register_singleton(
            GetProviderMetricsOrchestrator,
            lambda c: GetProviderMetricsOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListSchedulerStrategiesOrchestrator):
        container.register_singleton(
            ListSchedulerStrategiesOrchestrator,
            lambda c: ListSchedulerStrategiesOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetSchedulerConfigOrchestrator):
        container.register_singleton(
            GetSchedulerConfigOrchestrator,
            lambda c: GetSchedulerConfigOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(ListStorageStrategiesOrchestrator):
        container.register_singleton(
            ListStorageStrategiesOrchestrator,
            lambda c: ListStorageStrategiesOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(GetStorageConfigOrchestrator):
        container.register_singleton(
            GetStorageConfigOrchestrator,
            lambda c: GetStorageConfigOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
    if not container.is_registered(WatchRequestStatusOrchestrator):
        container.register_singleton(
            WatchRequestStatusOrchestrator,
            lambda c: WatchRequestStatusOrchestrator(
                query_bus=c.get(QueryBus),
                logger=c.get(LoggingPort),
            ),
        )
