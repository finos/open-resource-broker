"""Domain services registration for dependency injection container."""

from application.services.asg_metadata_service import ASGMetadataService
from application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from application.services.machine_grouping_service import MachineGroupingService
from application.services.provider_validation_service import ProviderValidationService
from domain.base import UnitOfWorkFactory
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.container_port import ContainerPort
from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.provider_selection_port import ProviderSelectionPort
from domain.services.filter_service import FilterService
from domain.services.generic_filter_service import GenericFilterService
from domain.services.template_validation_domain_service import TemplateValidationDomainService
from domain.services.timestamp_service import TimestampService
from infrastructure.di.container import DIContainer
from infrastructure.services.iso_timestamp_service import ISOTimestampService
from infrastructure.services.machine_filter_service import MachineFilterService


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
        from infrastructure.di.buses import QueryBus

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
        return ProviderValidationService(
            container=c.get(ContainerPort),
            logger=c.get(LoggingPort),
            provider_selection_port=c.get(ProviderSelectionPort),
        )

    container.register_singleton(ProviderValidationService, create_provider_validation_service)

    # ASG metadata service (SRP refactoring)
    def create_asg_metadata_service(c):
        return ASGMetadataService(
            uow_factory=c.get(UnitOfWorkFactory),
            container=c.get(ContainerPort),
            logger=c.get(LoggingPort),
        )

    container.register_singleton(ASGMetadataService, create_asg_metadata_service)
