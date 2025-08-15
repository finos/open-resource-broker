"""Provider service registrations for dependency injection."""

from application.services.provider_capability_service import (
    ProviderCapabilityService,
)
from application.services.provider_selection_service import ProviderSelectionService
from domain.base.ports import ConfigurationPort, LoggingPort
from infrastructure.di.container import DIContainer
from infrastructure.factories.provider_strategy_factory import (
    ProviderStrategyFactory,
)
from infrastructure.logging.logger import get_logger
from providers.base.strategy import ProviderContext


def register_provider_services(container: DIContainer) -> None:
    """Register provider-specific services."""

    # Register provider strategy factory
    container.register_factory(ProviderStrategyFactory, create_provider_strategy_factory)

    # Register ProviderContext with configuration-driven factory
    container.register_factory(ProviderContext, create_configured_provider_context)

    # Register SelectorFactory (keep existing)
    from providers.base.strategy import SelectorFactory

    container.register_singleton(SelectorFactory, lambda c: SelectorFactory())

    # NEW: Register provider selection services
    container.register_singleton(
        ProviderSelectionService,
        lambda c: ProviderSelectionService(
            config_manager=c.get(ConfigurationPort),
            logger=c.get(LoggingPort),
            provider_registry=None,  # Optional for now
        ),
    )

    container.register_singleton(
        ProviderCapabilityService,
        lambda c: ProviderCapabilityService(
            logger=c.get(LoggingPort), provider_registry=None
        ),  # Optional for now
    )

    # Register provider-specific services conditionally
    _register_provider_specific_services(container)


# Global flag to prevent duplicate provider registration
_providers_registered = False


def _register_providers() -> None:
    """Register providers based on configuration."""
    global _providers_registered

    if _providers_registered:
        return

    logger = get_logger(__name__)

    try:
        # Get configuration manager
        from config.manager import get_config_manager

        config_manager = get_config_manager()

        # Get provider configuration
        provider_config = config_manager.get_provider_config()

        if not provider_config:
            logger.warning("No provider configuration found - no providers will be registered")
            return

        # Validate configuration
        if not _validate_provider_config(provider_config):
            logger.error(
                "Provider configuration validation failed - no providers will be registered"
            )
            return

        # Get active providers from configuration
        active_providers = provider_config.get_active_providers()

        if not active_providers:
            logger.warning("No active providers found in configuration")
            return

        logger.debug(f"Found {len(active_providers)} active provider(s) in configuration")

        # Register each active provider
        registered_count = 0
        registered_names = []
        for provider_instance in active_providers:
            if provider_instance.enabled:
                if _register_provider_instance(provider_instance):
                    registered_count += 1
                    registered_names.append(f"{provider_instance.name}({provider_instance.type})")
            else:
                logger.debug(
                    f"Provider instance '{ provider_instance.name}' is disabled - skipping"
                )

        if registered_count > 0:
            # Group by provider type for better logging
            type_groups = {}
            for provider_instance in active_providers:
                if provider_instance.enabled:
                    provider_type = provider_instance.type
                    if provider_type not in type_groups:
                        type_groups[provider_type] = []
                    type_groups[provider_type].append(provider_instance.name)

            # Create summary message
            type_summaries = []
            for provider_type, instance_names in type_groups.items():
                type_summaries.append(
                    f"{len(instance_names)} {provider_type} provider(s): {', '.join(instance_names)}"
                )

            logger.info(
                f"Registered {registered_count} provider instances - {'; '.join(type_summaries)}"
            )
        else:
            logger.warning("No provider instances were successfully registered")
        _providers_registered = True

    except Exception as e:
        logger.error(f"Failed to register providers from configuration: {str(e)}")
        logger.info("No providers registered due to configuration errors")


def _register_providers_with_di_context(container: DIContainer) -> None:
    """Register providers with full DI container context available."""
    global _providers_registered

    if _providers_registered:
        return

    _providers_registered = True

    logger = container.get(LoggingPort)

    try:
        # Get configuration manager from DI container
        config_manager = container.get(ConfigurationPort)

        # Get provider configuration
        provider_config = config_manager.get_provider_config()

        if not provider_config:
            logger.warning("No provider configuration found - no providers will be registered")
            return

        # Validate configuration
        if not _validate_provider_config(provider_config):
            logger.error(
                "Provider configuration validation failed - no providers will be registered"
            )
            return

        # Get active providers from configuration
        active_providers = provider_config.get_active_providers()

        if not active_providers:
            logger.warning("No active providers found in configuration")
            return

        logger.info(f"Found {len(active_providers)} active provider(s) in configuration")

        # Register each active provider with DI context
        registered_count = 0
        for provider_instance in active_providers:
            if provider_instance.enabled:
                if _register_provider_instance_with_di(provider_instance, container):
                    registered_count += 1
            else:
                logger.info(f"Provider instance '{ provider_instance.name}' is disabled - skipping")

        logger.info(f"Successfully registered {registered_count} provider instance(s)")
        _providers_registered = True

    except Exception as e:
        logger.error(f"Failed to register providers from configuration: {str(e)}")
        logger.info("No providers registered due to configuration errors")


def _register_provider_instance_with_di(provider_instance, container: DIContainer) -> bool:
    """Register a single provider instance using DI container context."""
    logger = container.get(LoggingPort)

    try:
        provider_type = provider_instance.type.lower()

        if provider_type == "aws":
            return _register_aws_provider_with_di(provider_instance, container)
        else:
            logger.warning(f"Unknown provider type: {provider_type}")
            return False

    except Exception as e:
        logger.error(f"Failed to register provider instance '{provider_instance.name}': {str(e)}")
        return False


def _register_aws_provider_with_di(provider_instance, container: DIContainer) -> bool:
    """Register AWS provider instance using DI container context."""
    logger = container.get(LoggingPort)

    try:
        from providers.aws.registration import register_aws_provider_with_di

        return register_aws_provider_with_di(provider_instance, container)
    except Exception as e:
        logger.error(f"Failed to register AWS provider '{provider_instance.name}': {str(e)}")
        return False


def _validate_provider_config(provider_config) -> bool:
    """Validate provider configuration."""
    logger = get_logger(__name__)

    try:
        # Check if providers list exists
        if not hasattr(provider_config, "providers") or not provider_config.providers:
            logger.error("Provider configuration must have at least one provider instance")
            return False

        # Validate each provider instance
        for provider_instance in provider_config.providers:
            if not hasattr(provider_instance, "name") or not provider_instance.name:
                logger.error("Provider instance must have a name")
                return False

            if not hasattr(provider_instance, "type") or not provider_instance.type:
                logger.error(f"Provider instance '{provider_instance.name}' must have a type")
                return False

            # Check for supported provider types
            supported_types = ["aws"]  # Add more as they're implemented
            if provider_instance.type not in supported_types:
                logger.warning(
                    f"Provider type '{ provider_instance.type}' is not supported (supported: {supported_types})"
                )

        return True

    except Exception as e:
        logger.error(f"Provider configuration validation error: {str(e)}")
        return False


def _register_provider_instance(provider_instance) -> bool:
    """Register a specific provider instance based on its type."""
    logger = get_logger(__name__)

    try:
        logger.debug(
            f"Registering provider instance: { provider_instance.name} (type: { provider_instance.type})"
        )

        if provider_instance.type == "aws":
            from infrastructure.registry.provider_registry import (
                get_provider_registry,
            )
            from providers.aws.registration import register_aws_provider

            # Get provider registry
            registry = get_provider_registry()

            # Register AWS provider instance with unique name
            register_aws_provider(registry=registry, instance_name=provider_instance.name)
            logger.debug(
                f"AWS provider instance '{ provider_instance.name}' registered successfully"
            )
            return True
        else:
            logger.warning(
                f"Unknown provider type: { provider_instance.type} for instance: { provider_instance.name}"
            )
            return False

    except ImportError as e:
        logger.warning(f"Provider type '{provider_instance.type}' not available: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Failed to register provider instance '{provider_instance.name}': {str(e)}")
        return False


def create_provider_strategy_factory(container: DIContainer) -> ProviderStrategyFactory:
    """Create provider strategy factory."""
    return ProviderStrategyFactory(
        logger=container.get(LoggingPort), config=container.get(ConfigurationPort)
    )


def create_configured_provider_context(container: DIContainer) -> ProviderContext:
    """Create provider context using configuration-driven factory with lazy loading support."""
    try:
        logger = container.get(LoggingPort)
        config_manager = container.get(ConfigurationPort)

        # Check if lazy loading is enabled
        if container.is_lazy_loading_enabled():
            return _create_lazy_provider_context(container, logger, config_manager)
        else:
            return _create_eager_provider_context(container, logger, config_manager)

    except Exception as e:
        logger = container.get(LoggingPort)
        logger.error(f"Failed to create configured provider context, using fallback: {e}")
        # Create minimal provider context as fallback
        return ProviderContext(logger)


def _create_lazy_provider_context(
    container: DIContainer, logger: LoggingPort, config_manager: ConfigurationPort
) -> ProviderContext:
    """Create provider context with immediate provider registration (lazy loading fixed)."""
    logger.info("Creating provider context with lazy loading enabled")

    # Create provider context
    provider_context = ProviderContext(logger)

    try:
        # IMMEDIATE LOADING instead of broken lazy loading
        logger.info("Loading providers immediately (lazy loading was broken)")

        # Get provider configuration
        provider_config = config_manager.get_provider_config()
        if not provider_config or not provider_config.providers:
            logger.warning("No provider configuration found - creating empty provider context")
            return provider_context

        # Register each active provider immediately
        registered_count = 0
        for provider_instance in provider_config.providers:
            if provider_instance.enabled and _register_provider_to_context(
                provider_instance, provider_context, container
            ):
                registered_count += 1
                logger.info(
                    f"Registered provider: { provider_instance.name} (type: { provider_instance.type})"
                )

        if registered_count > 0:
            logger.info(f"Successfully registered {registered_count} provider(s)")
            # Initialize the context after loading providers
            provider_context.initialize()
        else:
            logger.warning("No providers were successfully registered")

    except Exception as e:
        logger.error(f"Failed to register providers: {e}")

    return provider_context


def _create_eager_provider_context(
    container: DIContainer, logger: LoggingPort, config_manager: ConfigurationPort
) -> ProviderContext:
    """Create provider context with immediate provider registration (fallback mode)."""
    logger.info("Creating provider context with eager loading (fallback mode)")

    # Register providers first (now that DI container is ready)
    _register_providers()

    # Try to get provider config
    try:
        provider_config = config_manager.get_provider_config()
        if provider_config and provider_config.providers:
            # Use configuration-driven approach
            from providers.base.strategy import create_provider_context

            return create_provider_context(logger=logger)
    except (AttributeError, Exception) as e:
        logger.warning(f"Failed to create provider context: {e}")

    # Fallback to basic provider context
    from providers.base.strategy import create_provider_context

    return create_provider_context(logger)


def _register_provider_to_context(
    provider_instance, provider_context: ProviderContext, container: DIContainer
) -> bool:
    """Register a single provider instance to the provider context."""
    logger = container.get(LoggingPort)

    try:
        provider_type = provider_instance.type.lower()

        if provider_type == "aws":
            return _register_aws_provider_to_context(provider_instance, provider_context, container)
        else:
            logger.warning(f"Unknown provider type: {provider_type}")
            return False

    except Exception as e:
        logger.error(
            f"Failed to register provider instance '{ provider_instance.name}' to context: { str(e)}"
        )
        return False


def _register_aws_provider_to_context(
    provider_instance, provider_context: ProviderContext, container: DIContainer
) -> bool:
    """Register AWS provider instance to the provider context."""
    logger = container.get(LoggingPort)

    try:
        # Create AWS provider configuration for this instance
        from providers.aws.configuration.config import AWSProviderConfig
        from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        aws_config = AWSProviderConfig(
            region=provider_instance.config.get("region", "us-east-1"),
            profile=provider_instance.config.get("profile", "default"),
        )

        # Create AWS provider strategy with correct parameters
        aws_strategy = AWSProviderStrategy(config=aws_config, logger=logger)

        # Register strategy with provider context
        provider_context.register_strategy(aws_strategy, provider_instance.name)

        logger.debug(f"Registered AWS provider '{provider_instance.name}' to context")
        return True

    except Exception as e:
        logger.error(
            f"Failed to register AWS provider '{ provider_instance.name}' to context: { str(e)}"
        )
        return False


def _register_provider_specific_services(container: DIContainer) -> None:
    """Register provider-specific services conditionally."""
    logger = get_logger(__name__)

    # Register AWS services if available - delegate to AWS provider
    try:
        import importlib.util

        # Check if AWS provider is available
        if importlib.util.find_spec("src.providers.aws"):
            from providers.aws.registration import register_aws_services_with_di

            register_aws_services_with_di(container)
        else:
            logger.debug("AWS provider not available, skipping AWS service registration")
    except ImportError:
        logger.debug("AWS provider not available, skipping AWS service registration")
    except Exception as e:
        logger.warning(f"Error registering AWS services: {str(e)}")


def _register_aws_services(container: DIContainer) -> None:
    """Register AWS-specific services."""
    logger = get_logger(__name__)

    try:
        # Import AWS-specific classes with individual error handling
        try:
            from providers.aws.infrastructure.aws_client import AWSClient
        except Exception as e:
            logger.debug(f"Failed to import AWSClient: {e}")
            raise

        try:
            from providers.aws.infrastructure.aws_handler_factory import (
                AWSHandlerFactory,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSHandlerFactory: {e}")
            raise

        try:
            from providers.aws.utilities.aws_operations import AWSOperations
        except Exception as e:
            logger.debug(f"Failed to import AWSOperations: {e}")
            raise

        try:
            from providers.aws.infrastructure.handlers.spot_fleet_handler import (
                SpotFleetHandler,
            )
        except Exception as e:
            logger.debug(f"Failed to import SpotFleetHandler: {e}")
            raise

        try:
            from providers.aws.infrastructure.adapters.template_adapter import (
                AWSTemplateAdapter,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSTemplateAdapter: {e}")
            raise

        try:
            from providers.aws.infrastructure.adapters.machine_adapter import (
                AWSMachineAdapter,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSMachineAdapter: {e}")
            raise

        try:
            from providers.aws.infrastructure.adapters.provisioning_adapter import (
                AWSProvisioningAdapter,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSProvisioningAdapter: {e}")
            raise

        try:
            from providers.aws.infrastructure.adapters.request_adapter import (
                AWSRequestAdapter,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSRequestAdapter: {e}")
            raise

        try:
            from providers.aws.infrastructure.adapters.resource_manager_adapter import (
                AWSResourceManagerAdapter,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSResourceManagerAdapter: {e}")
            raise

        try:
            from providers.aws.strategy.aws_provider_adapter import (
                AWSProviderAdapter,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSProviderAdapter: {e}")
            raise

        try:
            from providers.aws.strategy.aws_provider_strategy import (
                AWSProviderStrategy,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSProviderStrategy: {e}")
            raise

        try:
            from providers.aws.managers.aws_instance_manager import (
                AWSInstanceManager,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSInstanceManager: {e}")
            raise

        try:
            from providers.aws.managers.aws_resource_manager import (
                AWSResourceManagerImpl,
            )
        except Exception as e:
            logger.debug(f"Failed to import AWSResourceManagerImpl: {e}")
            raise

        try:
            from infrastructure.adapters.ports.cloud_resource_manager_port import (
                CloudResourceManagerPort,
            )
            from infrastructure.adapters.ports.request_adapter_port import (
                RequestAdapterPort,
            )
            from infrastructure.adapters.ports.resource_provisioning_port import (
                ResourceProvisioningPort,
            )
        except Exception as e:
            logger.debug(f"Failed to import infrastructure ports: {e}")
            raise

        # Register AWS client factory (not singleton - each provider gets its own)
        container.register_factory(AWSClient, lambda c: _create_aws_client(c))

        # Register AWS operations utility
        container.register_singleton(AWSOperations)

        # Register AWS handler factory
        container.register_singleton(AWSHandlerFactory)

        # Register AWS handler implementations
        container.register_singleton(SpotFleetHandler)

        # Register AWS adapter implementations using @injectable decorator
        container.register_singleton(AWSTemplateAdapter)
        container.register_singleton(AWSMachineAdapter)
        container.register_singleton(AWSProvisioningAdapter)
        container.register_singleton(AWSRequestAdapter)
        container.register_singleton(AWSResourceManagerAdapter)

        # Register AWS provider strategy and adapter using @injectable decorator
        container.register_singleton(AWSProviderAdapter)
        container.register_singleton(AWSProviderStrategy)

        # Register AWS manager implementations using @injectable decorator
        container.register_singleton(AWSInstanceManager)
        container.register_singleton(AWSResourceManagerImpl)

        # Register port implementations
        container.register_factory(
            ResourceProvisioningPort, lambda c: c.get(AWSProvisioningAdapter)
        )

        container.register_factory(
            CloudResourceManagerPort, lambda c: c.get(AWSResourceManagerAdapter)
        )

        container.register_factory(RequestAdapterPort, lambda c: c.get(AWSRequestAdapter))

        logger.info("AWS services registered successfully")
    except ImportError as e:
        logger.warning(f"Failed to import AWS classes: {str(e)}")
    except Exception as e:
        logger.warning(f"Failed to register AWS services: {str(e)}")


def _create_aws_client(container: DIContainer):
    """Create AWS client from the currently selected provider."""
    logger = container.get(LoggingPort)

    try:
        # Get the provider context to find the currently selected provider
        provider_context = container.get(ProviderContext)

        # Get the current strategy
        current_strategy_type = provider_context.current_strategy_type
        if current_strategy_type and current_strategy_type in provider_context._strategies:
            current_strategy = provider_context._strategies[current_strategy_type]

            # If it's an AWS strategy, get its AWS client
            if hasattr(current_strategy, "aws_client") and current_strategy.aws_client:
                logger.debug(
                    f"Using AWS client from selected provider strategy: {current_strategy_type}"
                )
                return current_strategy.aws_client

        logger.debug("No selected AWS provider strategy found, creating fallback AWS client")

    except Exception as e:
        logger.debug(f"Could not get AWS client from provider context: {e}")

    # Fallback: create AWS client with generic configuration
    config = container.get(ConfigurationPort)
    from providers.aws.infrastructure.aws_client import AWSClient

    return AWSClient(config=config, logger=logger)
