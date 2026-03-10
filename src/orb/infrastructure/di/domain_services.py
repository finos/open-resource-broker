"""Domain services registration for dependency injection container."""

from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.application.services.machine_grouping_service import MachineGroupingService
from orb.application.services.provider_validation_service import ProviderValidationService
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.configuration_service import DomainConfigurationService
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.container_port import ContainerPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort
from orb.domain.constants import PROVIDER_TYPE_AWS
from orb.domain.services.filter_service import FilterService
from orb.domain.services.generic_filter_service import GenericFilterService
from orb.domain.services.template_validation_domain_service import TemplateValidationDomainService
from orb.domain.services.timestamp_service import TimestampService
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.services.iso_timestamp_service import ISOTimestampService
from orb.infrastructure.services.machine_filter_service import MachineFilterService


def register_domain_services(container: DIContainer) -> None:
    """Register domain services in the DI container."""

    # Template validation domain service - lazy initialization
    def create_template_validation_service(c):
        service = TemplateValidationDomainService()
        # Inject dependencies after creation
        config = c.get(ConfigurationPort)
        logger = c.get(LoggingPort)
        service.inject_dependencies(config, logger)
        return service

    container.register_singleton(
        TemplateValidationDomainService, create_template_validation_service
    )

    # Timestamp service
    container.register_singleton(TimestampService, lambda c: ISOTimestampService())

    # Filter service
    container.register_singleton(FilterService, lambda c: MachineFilterService())

    # Generic filter service
    container.register_singleton(GenericFilterService, lambda c: GenericFilterService())

    # Machine grouping service (SRP refactoring)
    def create_machine_grouping_service(c):
        return MachineGroupingService(
            uow_factory=c.get(UnitOfWorkFactory),
            logger=c.get(LoggingPort),
        )

    container.register_singleton(MachineGroupingService, create_machine_grouping_service)

    # Deprovisioning orchestrator (SRP refactoring)
    def create_deprovisioning_orchestrator(c):
        from orb.infrastructure.di.buses import QueryBus

        return DeprovisioningOrchestrator(
            uow_factory=c.get(UnitOfWorkFactory),
            logger=c.get(LoggingPort),
            container=c.get(ContainerPort),
            query_bus=c.get(QueryBus),
            provider_selection_port=c.get(ProviderSelectionPort),
        )

    container.register_singleton(DeprovisioningOrchestrator, create_deprovisioning_orchestrator)

    # Provider validation service (SRP refactoring)
    def create_provider_validation_service(c):
        from orb.providers.registry.provider_registry import get_provider_registry

        validator = None
        try:
            validator = get_provider_registry().create_validator(PROVIDER_TYPE_AWS)
        except Exception as e:
            c.get(LoggingPort).debug("Could not create AWS provider validator: %s", e)
        return ProviderValidationService(
            container=c.get(ContainerPort),
            logger=c.get(LoggingPort),
            provider_selection_port=c.get(ProviderSelectionPort),
            validator=validator,
        )

    container.register_singleton(ProviderValidationService, create_provider_validation_service)

    # Domain configuration service — translates raw config dicts into typed domain values
    container.register_singleton(
        DomainConfigurationService,
        lambda c: DomainConfigurationService(c.get(ConfigurationPort)),
    )
