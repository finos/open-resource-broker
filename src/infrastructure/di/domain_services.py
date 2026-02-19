"""Domain services registration for dependency injection container."""

from infrastructure.di.container import DIContainer
from domain.services.provider_selection_service import ProviderSelectionService
from domain.services.template_validation_domain_service import TemplateValidationDomainService
from domain.services.timestamp_service import TimestampService
from domain.services.filter_service import FilterService
from domain.services.generic_filter_service import GenericFilterService
from infrastructure.services.iso_timestamp_service import ISOTimestampService
from infrastructure.services.machine_filter_service import MachineFilterService
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort


def register_domain_services(container: DIContainer) -> None:
    """Register domain services in the DI container."""

    # Provider selection domain service - lazy initialization
    def create_provider_selection_service(c):
        config = c.get(ConfigurationPort)
        logger = c.get(LoggingPort)
        return ProviderSelectionService(config, logger)
    
    container.register_singleton(ProviderSelectionService, create_provider_selection_service)

    # Template validation domain service - lazy initialization
    def create_template_validation_service(c):
        config = c.get(ConfigurationPort)
        logger = c.get(LoggingPort)
        return TemplateValidationDomainService(config, logger)
    
    container.register_singleton(TemplateValidationDomainService, create_template_validation_service)

    # Timestamp service
    container.register_singleton(TimestampService, lambda c: ISOTimestampService())

    # Filter service
    container.register_singleton(FilterService, lambda c: MachineFilterService())

    # Generic filter service
    container.register_singleton(GenericFilterService, lambda c: GenericFilterService())
