"""Provider service registrations for dependency injection."""

from infrastructure.di.container import DIContainer
from infrastructure.logging.logger import get_logger


def register_provider_services(container: DIContainer) -> None:
    """Register provider application services and utilities only."""

    # Provider services moved to Provider Registry
    # No longer registering ProviderSelectionService or ProviderCapabilityService
    
    # Register provider-specific utility services only
    _register_provider_utility_services(container)


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


