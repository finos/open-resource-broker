"""AWS Provider Registration - Register AWS provider with the provider registry."""

from contextlib import suppress
from typing import TYPE_CHECKING, Any, Dict, Optional

# Use TYPE_CHECKING to avoid direct infrastructure import
if TYPE_CHECKING:
    from src.domain.base.ports import LoggingPort
    from src.infrastructure.registry.provider_registry import ProviderRegistry

# Template extension imports for our new functionality
from src.domain.template.extensions import TemplateExtensionRegistry
from src.domain.template.factory import TemplateFactory
from src.providers.aws.configuration.template_extension import (
    AWSTemplateExtensionConfig,
)


def create_aws_strategy(provider_config: Any) -> Any:
    """
    Create AWS provider strategy from configuration.

    Args:
        provider_config: Provider instance configuration

    Returns:
        Configured AWSProviderStrategy instance
    """
    from src.infrastructure.adapters.logging_adapter import LoggingAdapter
    from src.providers.aws.configuration.config import AWSProviderConfig
    from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    try:
        # Handle both ProviderInstanceConfig object and raw dict
        if hasattr(provider_config, "config"):
            # ProviderInstanceConfig object
            config_data = provider_config.config
        else:
            # Raw config dict
            config_data = provider_config

        # Create AWS configuration
        aws_config = AWSProviderConfig(**config_data)

        # Create a simple logger adapter for now
        # The DI container will inject the proper logger later if needed
        logger = LoggingAdapter()

        # Create AWS provider strategy
        strategy = AWSProviderStrategy(aws_config, logger)

        # Set provider name for identification
        if hasattr(strategy, "name"):
            strategy.name = provider_config.name

        return strategy

    except ImportError as e:
        raise ImportError(f"AWS provider strategy not available: {str(e)}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to create AWS strategy: {str(e)}") from e


def create_aws_config(data: Dict[str, Any]) -> Any:
    """
    Create AWS configuration from data dictionary.

    Args:
        data: Configuration data dictionary

    Returns:
        Configured AWSProviderConfig instance
    """
    try:
        from src.providers.aws.configuration.config import AWSProviderConfig

        return AWSProviderConfig(**data)
    except ImportError as e:
        raise ImportError(f"AWS configuration not available: {str(e)}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to create AWS config: {str(e)}") from e


def create_aws_resolver() -> Any:
    """
    Create AWS template resolver.

    Returns:
        AWS template resolver instance
    """
    try:
        from src.providers.aws.infrastructure.template.caching_ami_resolver import (
            CachingAMIResolver,
        )

        return CachingAMIResolver()
    except ImportError:
        # AWS resolver not available, return None
        return None
    except Exception as e:
        # Re-raise with context - let caller handle logging
        raise RuntimeError(f"Failed to create AWS resolver: {str(e)}") from e


def create_aws_validator() -> Any:
    """
    Create AWS template validator.

    Returns:
        AWS template validator instance
    """
    try:
        # AWS doesn't have a specific validator yet, return None
        return None
    except Exception as e:
        # Re-raise with context - let caller handle logging
        raise RuntimeError(f"Failed to create AWS validator: {str(e)}") from e


def register_aws_provider(
    registry: "ProviderRegistry" = None,
    logger: "LoggingPort" = None,
    instance_name: str = None,
) -> None:
    """Register AWS provider with the provider registry.

    Args:
        registry: Provider registry instance (optional)
        logger: Logger port for logging (optional)
        instance_name: Optional instance name for multi-instance support
    """
    if registry is None:
        # Import here to avoid circular dependencies
        from src.infrastructure.registry.provider_registry import get_provider_registry

        registry = get_provider_registry()

    try:
        if instance_name:
            # Register as named instance
            registry.register_provider_instance(
                provider_type="aws",
                instance_name=instance_name,
                strategy_factory=create_aws_strategy,
                config_factory=create_aws_config,
                resolver_factory=create_aws_resolver,
                validator_factory=create_aws_validator,
            )
        else:
            # Register as provider type (backward compatibility)
            registry.register_provider(
                provider_type="aws",
                strategy_factory=create_aws_strategy,
                config_factory=create_aws_config,
                resolver_factory=create_aws_resolver,
                validator_factory=create_aws_validator,
            )

        # Register AWS template store
        # _register_aws_template_store(logger)

        # Register AWS template adapter (following adapter/port pattern)
        # _register_aws_template_adapter(logger)

        if logger:
            logger.info("AWS provider registered successfully")

    except Exception as e:
        if logger:
            logger.error(f"Failed to register AWS provider: {str(e)}")
        raise


def _register_aws_template_store(logger: "LoggingPort" = None) -> None:
    """Register AWS template store - DISABLED: Template system consolidated.

    Template functionality has been consolidated into the integrated TemplateConfigurationManager.
    Provider-specific template logic is now handled by the scheduler strategy pattern.
    """
    if logger:
        logger.debug("AWS template store registration skipped - using integrated template system")
    # No-op: Template system has been consolidated


def _register_aws_template_adapter(logger: "LoggingPort" = None) -> None:
    """Register AWS template adapter with the DI container."""
    try:
        from src.domain.base.ports.template_adapter_port import TemplateAdapterPort
        from src.infrastructure.di.container import get_container

        from .infrastructure.adapters.template_adapter import (
            AWSTemplateAdapter,
            create_aws_template_adapter,
        )

        container = get_container()

        # Register AWS template adapter factory
        def aws_template_adapter_factory(container_instance):
            """Create AWS template adapter."""
            from src.domain.base.ports import ConfigurationPort, LoggingPort
            from src.providers.aws.infrastructure.aws_client import AWSClient

            aws_client = container_instance.get(AWSClient)
            logger_port = container_instance.get(LoggingPort)
            config_port = container_instance.get(ConfigurationPort)

            return create_aws_template_adapter(aws_client, logger_port, config_port)

        # Register the adapter with DI container
        container.register_singleton(AWSTemplateAdapter, aws_template_adapter_factory)
        container.register_singleton(TemplateAdapterPort, aws_template_adapter_factory)

        if logger:
            logger.info("AWS template adapter registered successfully")

    except Exception as e:
        if logger:
            logger.warning(f"Failed to register AWS template adapter: {e}")


def register_aws_provider_with_di(provider_instance, container) -> bool:
    """Register AWS provider instance using DI container context."""
    from src.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    try:
        logger.debug(f"Registering AWS provider instance: {provider_instance.name}")

        # Create AWS provider configuration
        aws_config = create_aws_config(provider_instance.config)

        # Register AWS components with DI container
        _register_aws_components_with_di(container, aws_config, provider_instance.name)

        # Register provider strategy with registry
        from src.infrastructure.registry.provider_registry import get_provider_registry

        registry = get_provider_registry()

        # Create provider strategy factory using DI container
        def aws_strategy_factory():
            """Factory function to create AWS strategy with DI container."""
            return _create_aws_strategy_with_di(container, aws_config, provider_instance.name)

        # Register the specific provider instance (no generic type registration)
        registry.register_provider_instance(
            provider_type="aws",
            instance_name=provider_instance.name,
            strategy_factory=aws_strategy_factory,
            config_factory=lambda: aws_config,
        )

        logger.debug(f"Successfully registered AWS provider instance: {provider_instance.name}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to register AWS provider instance '{provider_instance.name}': {str(e)}"
        )
        return False


def _register_aws_components_with_di(container, aws_config, instance_name: str) -> None:
    """Register AWS components with DI container for specific instance."""
    from src.domain.base.ports import LoggingPort
    from src.providers.aws.infrastructure.aws_client import AWSClient

    # Register AWS client for this instance with instance-specific configuration
    def aws_client_factory(container_instance):
        """Factory function to create AWS client with instance-specific configuration."""
        logger_port = container_instance.get(LoggingPort)

        # Create a configuration port that provides the instance-specific AWS config
        class AWSInstanceConfigPort:
            """Configuration port that provides instance-specific AWS configuration."""

            def __init__(self, aws_config):
                """Initialize with AWS configuration."""
                self._aws_config = aws_config

            def get_typed(self, config_type):
                """Return the instance-specific AWS config."""
                from src.providers.aws.configuration.config import AWSProviderConfig

                if config_type == AWSProviderConfig:
                    return self._aws_config
                return None

            def get(self, key, default=None):
                """Get configuration value."""
                return getattr(self._aws_config, key, default)

        config_port = AWSInstanceConfigPort(aws_config)

        # Create AWS client with instance-specific config
        aws_client = AWSClient(config=config_port, logger=logger_port)

        # Log the client creation for this specific instance
        logger_port.info(f"AWS client initialized for {instance_name}: region={aws_config.region}")

        return aws_client

    # Register with instance-specific key
    container.register_factory(f"AWSClient_{instance_name}", aws_client_factory)


def _create_aws_strategy_with_di(container, aws_config, instance_name: str):
    """Create AWS strategy using DI container."""
    from src.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    # Get AWS client for this instance
    aws_client = container.get(f"AWSClient_{instance_name}")

    # Create and return AWS strategy
    from src.providers.aws.strategy import AWSProviderStrategy

    return AWSProviderStrategy(aws_client=aws_client, config=aws_config, logger=logger)


def register_aws_extensions(logger: Optional["LoggingPort"] = None) -> None:
    """Register AWS template extensions with the global registry.

    This function should be called during application startup to ensure
    AWS extensions are available for template processing.

    Args:
        logger: Optional logger for registration messages
    """
    try:
        # Register AWS template extension configuration
        TemplateExtensionRegistry.register_extension("aws", AWSTemplateExtensionConfig)

        if logger:
            logger.debug("AWS template extensions registered successfully")
        # Remove print statement - should use structured logging

    except Exception as e:
        error_msg = f"Failed to register AWS template extensions: {e}"
        if logger:
            logger.error(error_msg)
        raise


def register_aws_template_factory(
    factory: TemplateFactory, logger: Optional["LoggingPort"] = None
) -> None:
    """Register AWS template class with the template factory.

    Args:
        factory: Template factory to register AWS template with
        logger: Optional logger for registration messages
    """
    try:
        # Try to import and register AWS template class
        try:
            from src.providers.aws.domain.template.aggregate import AWSTemplate

            factory.register_provider_template_class("aws", AWSTemplate)

            if logger:
                logger.info("AWS template class registered with factory")

        except ImportError:
            # AWS template class doesn't exist yet, that's okay
            if logger:
                logger.debug("AWS template class not available, using core template")

    except Exception as e:
        error_msg = f"Failed to register AWS template factory: {e}"
        if logger:
            logger.error(error_msg)
        # Don't raise here - factory registration is optional


def get_aws_extension_defaults() -> dict:
    """Get default AWS extension configuration.

    Returns:
        Dictionary of default AWS extension values
    """
    default_config = AWSTemplateExtensionConfig()
    return default_config.to_template_defaults()


def initialize_aws_provider(
    template_factory: Optional[TemplateFactory] = None,
    logger: Optional["LoggingPort"] = None,
) -> None:
    """Initialize AWS provider components.

    This is the main initialization function that should be called during
    application startup to set up all AWS provider components.

    Args:
        template_factory: Optional template factory to register AWS components with
        logger: Optional logger for initialization messages
    """
    try:
        # Register AWS extensions
        register_aws_extensions(logger)

        # Register AWS template factory if provided
        if template_factory:
            register_aws_template_factory(template_factory, logger)

        if logger:
            logger.info("AWS provider initialization completed successfully")

    except Exception as e:
        error_msg = f"AWS provider initialization failed: {e}"
        if logger:
            logger.error(error_msg)
        raise


def is_aws_provider_registered() -> bool:
    """Check if AWS provider is correctly registered.

    Returns:
        True if AWS extensions are registered
    """
    return TemplateExtensionRegistry.has_extension("aws")


def register_aws_services_with_di(container) -> None:
    """Register AWS services with DI container."""
    from src.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    try:
        # Register AWS-specific services that need to be available globally
        from src.domain.base.ports.template_resolver_port import TemplateResolverPort
        from src.providers.aws.infrastructure.launch_template.manager import (
            AWSLaunchTemplateManager,
        )
        from src.providers.aws.infrastructure.template.caching_ami_resolver import (
            CachingAMIResolver,
        )

        # Register AMI resolver if not already registered
        if not container.is_registered(CachingAMIResolver):
            container.register_singleton(CachingAMIResolver)
            container.register_singleton(TemplateResolverPort, lambda c: c.get(CachingAMIResolver))
            logger.debug("AWS AMI resolver registered with DI container")

        # Register AWS Launch Template Manager if not already registered
        if not container.is_registered(AWSLaunchTemplateManager):
            container.register_singleton(AWSLaunchTemplateManager)
            logger.debug("AWS Launch Template Manager registered with DI container")

        logger.debug("AWS services registered with DI container")

    except Exception as e:
        logger.warning(f"Failed to register AWS services with DI container: {e}")


# Auto-register AWS extensions when module is imported
# This ensures basic functionality even if explicit initialization is missed

with suppress(Exception):
    register_aws_extensions()
