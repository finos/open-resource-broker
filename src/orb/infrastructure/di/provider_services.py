"""Provider service registrations for dependency injection."""

from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.logging.logger import get_logger


def register_provider_services(container: DIContainer) -> None:
    """Register provider application services and utilities."""

    # Register enhanced application services
    _register_application_services(container)

    # Register provider-specific utility services
    _register_provider_utility_services(container)


def _register_application_services(container: DIContainer) -> None:
    """Register enhanced application services with proper dependencies."""
    from orb.application.services.machine_sync_service import MachineSyncService
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.ports.logging_port import LoggingPort
    from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
    from orb.domain.services.template_validation_domain_service import (
        TemplateValidationDomainService,
    )
    from orb.infrastructure.di.buses import CommandBus
    from orb.providers.registry import get_provider_registry

    def _create_provider_registry(c):
        from orb.domain.base.ports.configuration_port import ConfigurationPort

        registry = get_provider_registry()
        registry._config_port = c.get(ConfigurationPort)
        return registry

    container.register_singleton(ProviderRegistryPort, _create_provider_registry)

    def create_provider_registry_service(c):
        registry = c.get(ProviderRegistryPort)
        validation_service = c.get(TemplateValidationDomainService)
        logger = c.get(LoggingPort)
        return ProviderRegistryService(registry, validation_service, logger)

    container.register_singleton(ProviderRegistryService, create_provider_registry_service)

    # Machine sync service - lazy initialization
    def create_machine_sync_service(c):
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.domain.base import UnitOfWorkFactory
        from orb.domain.base.ports.configuration_port import ConfigurationPort

        command_bus = c.get(CommandBus)
        uow_factory = c.get(UnitOfWorkFactory)
        config_port = c.get(ConfigurationPort)
        logger = c.get(LoggingPort)
        provider_registry_service = c.get(ProviderRegistryService)
        return MachineSyncService(
            command_bus, uow_factory, config_port, logger, provider_registry_service
        )

    container.register_singleton(MachineSyncService, create_machine_sync_service)


def _register_provider_utility_services(container: DIContainer) -> None:
    """Register provider-specific utility services only (not provider instances)."""
    logger = get_logger(__name__)

    # Register AWS utility services if available
    try:
        import importlib.util

        # Check if AWS provider is available
        if importlib.util.find_spec("orb.providers.aws"):
            try:
                from orb.providers.aws.registration import register_aws_services_with_di

                register_aws_services_with_di(container)
                logger.debug("AWS utility services registered with DI")
            except Exception as e:
                logger.warning("Failed to register AWS utility services: %s", str(e))

        else:
            logger.debug("AWS provider not available, skipping AWS utility service registration")
    except ImportError:
        logger.debug("AWS provider not available, skipping AWS utility service registration")
    except Exception as e:
        logger.warning("Failed to register AWS utility services: %s", str(e))
