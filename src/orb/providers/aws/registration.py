"""AWS Provider Registration - Register AWS provider with the provider registry."""

from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

# Use TYPE_CHECKING to avoid direct infrastructure import
if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.registry import ProviderRegistry

# Template extension imports for our new functionality
from orb.domain.template.extensions import TemplateExtensionRegistry
from orb.domain.template.factory import TemplateFactory
from orb.providers.aws.configuration.template_extension import AWSTemplateExtensionConfig


def create_aws_strategy(provider_config: Any) -> Any:
    """
    Create AWS provider strategy from configuration.

    Args:
        provider_config: Provider instance configuration

    Returns:
        Configured AWSProviderStrategy instance
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.aws.configuration.config import AWSProviderConfig
    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    try:
        # Handle AWSProviderConfig object directly
        if isinstance(provider_config, AWSProviderConfig):
            aws_config = provider_config
            provider_instance_config = None
            provider_name = None
        # Handle ProviderInstanceConfig object
        elif hasattr(provider_config, "config"):
            # ProviderInstanceConfig object
            config_data = provider_config.config
            provider_instance_config = provider_config
            provider_name = provider_config.name
            # Create AWS configuration
            aws_config = AWSProviderConfig(**config_data)
        else:
            # Raw config dict
            config_data = provider_config
            provider_instance_config = None
            provider_name = None
            # Create AWS configuration
            aws_config = AWSProviderConfig(**(config_data or {}))

        # Create a simple logger adapter for now
        # The DI container will inject the appropriate logger later if needed
        logger = LoggingAdapter()

        config_port = None
        try:
            from orb.domain.base.ports.configuration_port import ConfigurationPort
            from orb.infrastructure.di.container import get_container

            config_port = get_container().get(ConfigurationPort)
        except Exception as e:
            logger.debug("Could not get config port from DI container: %s", e)

        # Create AWS provider strategy
        strategy = AWSProviderStrategy(
            config=aws_config,
            logger=logger,
            provider_name=provider_name,
            provider_instance_config=provider_instance_config,
            config_port=config_port,
        )

        # Initialize the strategy
        if not strategy.initialize():
            raise RuntimeError("Failed to initialize AWS provider strategy")

        with suppress(Exception):
            from orb.domain.base.ports.health_check_port import HealthCheckPort
            from orb.infrastructure.di.container import get_container
            from orb.providers.aws.health import register_aws_health_checks

            if strategy.aws_client is not None:
                health_check = get_container().get(HealthCheckPort)
                register_aws_health_checks(health_check, strategy.aws_client)

        # Set provider name for identification
        if hasattr(strategy, "name") and provider_name:
            strategy.name = provider_name  # type: ignore[misc]

        return strategy

    except ImportError as e:
        raise ImportError(f"AWS provider strategy not available: {e!s}")
    except Exception as e:
        raise RuntimeError(f"Failed to create AWS strategy: {e!s}")


def create_aws_config(data: dict[str, Any]) -> Any:
    """
    Create AWS configuration from data dictionary.

    Args:
        data: Configuration data dictionary

    Returns:
        Configured AWSProviderConfig instance
    """
    try:
        from orb.providers.aws.configuration.config import AWSProviderConfig

        # AWSProviderConfig inherits from BaseSettings, so env vars are automatically loaded
        return AWSProviderConfig(**data)
    except ImportError as e:
        raise ImportError(f"AWS configuration not available: {e!s}")
    except Exception as e:
        raise RuntimeError(f"Failed to create AWS config: {e!s}")


def register_aws_provider_settings() -> None:
    """Register AWSProviderConfig with the provider settings registry."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.aws.configuration.config import AWSProviderConfig

        # Register AWSProviderConfig as the settings class for AWS providers
        ProviderSettingsRegistry.register_provider_settings("aws", AWSProviderConfig)

    except ImportError:
        # Registry not available, skip registration
        pass
    except Exception as e:
        raise RuntimeError(f"Failed to register AWS provider settings: {e!s}")


def create_aws_resolver() -> Any:
    """
    Create AWS template resolver.

    Returns:
        AWS template resolver instance
    """
    try:
        # Image resolution now handled by generic service
        # Return None to indicate no legacy resolver needed
        return None
    except ImportError:
        # AWS resolver not available, return None
        return None
    except Exception as e:
        # Re-raise with context - let caller handle logging
        raise RuntimeError(f"Failed to create AWS resolver: {e!s}")


def create_aws_validator(provider_config: Any = None) -> Any:
    """
    Create AWS template validator.

    Args:
        provider_config: AWSProviderConfig instance or raw config dict

    Returns:
        AWSValidationAdapter instance, or None if config unavailable
    """
    try:
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
        from orb.providers.aws.configuration.config import AWSProviderConfig
        from orb.providers.aws.infrastructure.adapters.aws_validation_adapter import (
            AWSValidationAdapter,
        )

        if provider_config is None:
            return None

        if isinstance(provider_config, AWSProviderConfig):
            aws_config = provider_config
        elif hasattr(provider_config, "config"):
            aws_config = AWSProviderConfig(**provider_config.config)
        elif isinstance(provider_config, dict):
            aws_config = AWSProviderConfig(**provider_config)
        else:
            return None

        return AWSValidationAdapter(config=aws_config, logger=LoggingAdapter())
    except Exception as e:
        raise RuntimeError(f"Failed to create AWS validator: {e!s}")


def register_aws_provider(
    registry: "Optional[ProviderRegistry]" = None,
    logger: "Optional[LoggingPort]" = None,
    instance_name: Optional[str] = None,
) -> None:
    """Register AWS provider with the provider registry.

    Args:
        registry: Provider registry instance (optional)
        logger: Logger port for logging (optional)
        instance_name: Optional instance name for multi-instance support
    """
    if registry is None:
        # Import here to avoid circular dependencies
        from orb.providers.registry import get_provider_registry

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
            logger.error("Failed to register AWS provider: %s", str(e))
        raise


def _register_aws_template_store(logger: "Optional[LoggingPort]" = None) -> None:
    """Register AWS template store - DISABLED: Template system consolidated.

    Template functionality has been consolidated into the integrated TemplateConfigurationManager.
    Provider-specific template logic is now handled by the scheduler strategy pattern.
    """
    if logger:
        logger.debug("AWS template store registration skipped - using integrated template system")
    # No-op: Template system has been consolidated


def _register_aws_template_adapter(logger: "Optional[LoggingPort]" = None) -> None:
    """Register AWS template adapter with the DI container."""
    try:
        from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
        from orb.infrastructure.di.container import get_container
        from orb.providers.aws.infrastructure.adapters.template_adapter import (
            AWSTemplateAdapter,
            create_aws_template_adapter,
        )

        container = get_container()

        # Register AWS template adapter factory
        def aws_template_adapter_factory(container_instance):
            """Create AWS template adapter."""
            from orb.domain.base.ports import ConfigurationPort, LoggingPort
            from orb.providers.aws.infrastructure.aws_client import AWSClient

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
            logger.warning("Failed to register AWS template adapter: %s", e, exc_info=True)


def register_aws_provider_instance(provider_instance, logger=None) -> bool:
    """Register AWS provider instance with Provider Registry."""
    try:
        if logger:
            logger.debug("Registering AWS provider instance: %s", provider_instance.name)

        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        # Register AWS as provider type if not already registered
        if not registry.is_provider_registered("aws"):
            registry.register_provider(
                provider_type="aws",
                strategy_factory=create_aws_strategy,
                config_factory=create_aws_config,
                resolver_factory=create_aws_resolver,
                validator_factory=create_aws_validator,
            )

        # Register the specific provider instance
        registry.register_provider_instance(
            provider_type="aws",
            instance_name=provider_instance.name,
            strategy_factory=create_aws_strategy,
            config_factory=create_aws_config,
            resolver_factory=create_aws_resolver,
            validator_factory=create_aws_validator,
        )

        if logger:
            logger.debug(
                "Successfully registered AWS provider instance: %s", provider_instance.name
            )
        return True

    except Exception as e:
        if logger:
            logger.error(
                "Failed to register AWS provider instance '%s': %s", provider_instance.name, str(e)
            )
        return False


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
            logger.error(error_msg, exc_info=True)
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
            from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

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
            logger.error(error_msg, exc_info=True)
        # Don't raise here - factory registration is optional


def get_aws_extension_defaults() -> dict:
    """Get default AWS extension configuration.

    Returns:
        Dictionary of default AWS extension values
    """
    default_config = AWSTemplateExtensionConfig()  # type: ignore[call-arg]
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
        # Register AWS provider settings
        register_aws_provider_settings()

        # Register AWS extensions
        register_aws_extensions(logger)

        # Register AWS template factory if provided
        if template_factory:
            register_aws_template_factory(template_factory, logger)

        # Register AWS CLI spec
        from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
        from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec

        CLISpecRegistry.register("aws", AWSCLISpec())

        if logger:
            logger.info("AWS provider initialization completed successfully")

    except Exception as e:
        error_msg = f"AWS provider initialization failed: {e}"
        if logger:
            logger.error(error_msg, exc_info=True)
        raise


def is_aws_provider_registered() -> bool:
    """Check if AWS provider is correctly registered.

    Returns:
        True if AWS extensions are registered
    """
    return TemplateExtensionRegistry.has_extension("aws")


def register_aws_services_with_di(container) -> None:
    """Register AWS utility services with DI container (not provider instances)."""
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    try:
        # Register AWS Template Adapter
        from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
        from orb.providers.aws.infrastructure.adapters.template_adapter import AWSTemplateAdapter

        def create_aws_template_adapter(c):
            from orb.infrastructure.template.configuration_manager import (
                TemplateConfigurationManager,
            )
            from orb.providers.aws.infrastructure.aws_client import AWSClient

            template_config_manager = c.get(TemplateConfigurationManager)
            aws_client = c.get(AWSClient)
            logger_port = c.get(LoggingPort)

            return AWSTemplateAdapter(template_config_manager, aws_client, logger_port)

        container.register_singleton(TemplateAdapterPort, create_aws_template_adapter)
        logger.debug("AWS Template Adapter registered with DI container")

        # Register TemplateExampleGeneratorPort backed by AWSHandlerFactory.
        # The factory is constructed with no AWS client because generate_example_templates
        # only calls handler classmethods — no live AWS connection is needed.
        from orb.domain.base.ports.template_example_generator_port import (
            TemplateExampleGeneratorPort,
        )
        from orb.providers.aws.adapters.template_example_generator_adapter import (
            AWSTemplateExampleGeneratorAdapter,
        )
        from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

        def create_template_example_generator(c):
            factory = AWSHandlerFactory(aws_client=None, logger=c.get(LoggingPort))  # type: ignore[arg-type]
            return AWSTemplateExampleGeneratorAdapter(aws_handler_factory=factory)

        container.register_singleton(
            TemplateExampleGeneratorPort, create_template_example_generator
        )
        logger.debug("TemplateExampleGeneratorPort registered with DI container")

        logger.debug("AWS utility services registered with DI container")

    except Exception as e:
        logger.warning(
            "Failed to register AWS utility services with DI container: %s", e, exc_info=True
        )


# Auto-register AWS extensions when module is imported
# This ensures basic functionality even if explicit initialization is missed

with suppress(Exception):
    register_aws_extensions()
    register_aws_provider_settings()
