"""Provider service registrations for dependency injection."""

from typing import TYPE_CHECKING

# Keep these imports for helper functions
from infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from infrastructure.di.container import DIContainer


def register_provider_services(container: "DIContainer") -> None:
    """Register provider application services and utilities only."""
    
    # Lazy imports to avoid import cascade
    from application.services.provider_capability_service import ProviderCapabilityService
    from application.services.provider_selection_service import ProviderSelectionService
    from domain.base.ports import ConfigurationPort
    from domain.base.ports.logging_port import LoggingPort
    from infrastructure.logging.logger import get_logger
    from providers.registry import ProviderRegistry

    # Register provider application services (NOT provider instances)
    container.register_singleton(
        ProviderSelectionService,
        lambda c: ProviderSelectionService(
            config_manager=c.get(ConfigurationPort),
            logger=c.get(LoggingPort),
            provider_registry=c.get(ProviderRegistry),
        ),
    )

    container.register_singleton(
        ProviderCapabilityService,
        lambda c: ProviderCapabilityService(
            logger=c.get(LoggingPort), 
            config_manager=c.get(ConfigurationPort),
            provider_registry=c.get(ProviderRegistry)
        ),
    )

    # Register provider-specific utility services only
    _register_provider_utility_services(container)


def _register_provider_utility_services(container: "DIContainer") -> None:
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


