"""Provider service registrations for dependency injection."""

from infrastructure.di.container import DIContainer
from infrastructure.logging.logger import get_logger


def register_provider_services(container: DIContainer) -> None:
    """Register provider application services and utilities."""

    # Register enhanced application services
    _register_application_services(container)

    # Register provider-specific utility services
    _register_provider_utility_services(container)


def _register_application_services(container: DIContainer) -> None:
    """Register enhanced application services with proper dependencies."""
    from application.services.provider_registry_service import ProviderRegistryService
    from application.services.machine_sync_service import MachineSyncService
    from infrastructure.di.buses import CommandBus
    from domain.services.provider_selection_service import ProviderSelectionService
    from domain.services.template_validation_domain_service import TemplateValidationDomainService
    from domain.base.ports.logging_port import LoggingPort
    from providers.registry import get_provider_registry

    # Enhanced provider registry service
    container.register_singleton(
        ProviderRegistryService,
        lambda c: ProviderRegistryService(
            registry=get_provider_registry(),
            validation_service=c.get(TemplateValidationDomainService),
            selection_service=c.get(ProviderSelectionService),
            logger=c.get(LoggingPort),
        ),
    )

    # Machine sync service
    container.register_singleton(
        MachineSyncService,
        lambda c: MachineSyncService(
            command_bus=c.get(CommandBus),
            container=c,
            logger=c.get(LoggingPort),
            provider_registry_service=c.get(ProviderRegistryService),
        ),
    )


def _register_provider_utility_services(container: DIContainer) -> None:
    """Register provider-specific utility services only (not provider instances)."""
    logger = get_logger(__name__)

    # Register AWS utility services if available
    try:
        import importlib.util

        # Check if AWS provider is available
        if importlib.util.find_spec("src.providers.aws"):
            try:
                from providers.aws.registration import register_aws_services_with_di

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
