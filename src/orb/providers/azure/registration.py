"""Azure Provider Registration.

Bootstrap helpers called at application startup to register the Azure
provider with the provider registry, template extension registry, and
DI container.
"""

from typing import TYPE_CHECKING, Any, Optional

from orb.config import PerformanceConfig

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.infrastructure.adapters.azure_validation_adapter import (
        AzureValidationAdapter,
    )
    from orb.providers.azure.infrastructure.azure_client import (
        AzureClient,
        AzureClientRuntimeConfig,
    )
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
    from orb.providers.registry import ProviderRegistry

from orb.domain.template.extensions import TemplateExtensionRegistry
from orb.domain.template.factory import TemplateFactory
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig


def _resolve_performance_config(
    config_port: Any,  # ConfigurationPort.get_typed() is called with non-standard args
    logger: "LoggingPort",
) -> PerformanceConfig:
    """Resolve shared performance config, falling back to defaults."""
    if config_port is None:
        logger.debug("No shared config port available; using default Azure performance config")
        return PerformanceConfig()

    try:
        perf_config = config_port.get_typed(PerformanceConfig)
    except Exception as exc:
        logger.debug("Could not load performance config from shared config port: %s", exc)
        return PerformanceConfig()

    if not isinstance(perf_config, PerformanceConfig):
        logger.debug(
            "Ignoring unexpected performance config type from shared config port: %s",
            type(perf_config).__name__,
        )
        return PerformanceConfig()

    return perf_config


def _build_azure_client_runtime_config(
    azure_config: "AzureProviderConfig",
    logger: "LoggingPort",
    *,
    performance_config: PerformanceConfig | None = None,
    config_port: Optional["ConfigurationPort"] = None,
) -> "AzureClientRuntimeConfig":
    """Assemble the explicit runtime config owned by Azure infrastructure."""
    from orb.providers.azure.infrastructure.azure_client import AzureClientRuntimeConfig

    resolved = performance_config or _resolve_performance_config(config_port, logger)
    return AzureClientRuntimeConfig(
        azure_config=azure_config,
        performance_config=resolved,
    )


def _create_azure_client(
    runtime_config: "AzureClientRuntimeConfig",
    logger: "LoggingPort",
) -> "AzureClient":
    """Construct an ``AzureClient`` from explicit runtime config."""
    from orb.providers.azure.infrastructure.azure_client import AzureClient

    return AzureClient(runtime_config=runtime_config, logger=logger)


# ------------------------------------------------------------------
# Factory functions
# ------------------------------------------------------------------


def create_azure_strategy(
    provider_config: Any,
    *,
    provider_instance_name: str,
    performance_config: PerformanceConfig | None = None,
    config_port: Optional["ConfigurationPort"] = None,
) -> "AzureProviderStrategy":
    """Create an ``AzureProviderStrategy`` from provider configuration."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.services.cyclecloud_request_context_service import (
        create_cyclecloud_request_lookup,
    )
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    try:
        config_data = (
            provider_config.config
            if hasattr(provider_config, "config")
            else provider_config
        )
        azure_config = AzureProviderConfig(**config_data)
        logger = LoggingAdapter()
        cyclecloud_request_lookup = None
        try:
            from orb.domain.base import UnitOfWorkFactory
            from orb.infrastructure.di.container import get_container

            cyclecloud_request_lookup = create_cyclecloud_request_lookup(
                get_container().get(UnitOfWorkFactory)
            )
        except Exception as exc:
            logger.debug("Could not get unit of work factory from DI container: %s", exc)
        runtime_config = _build_azure_client_runtime_config(
            azure_config,
            logger,
            performance_config=performance_config,
            config_port=config_port,
        )
        strategy = AzureProviderStrategy(
            config=azure_config,
            logger=logger,
            provider_instance_name=provider_instance_name,
            azure_client_resolver=lambda: _create_azure_client(runtime_config, logger),
            cyclecloud_request_lookup=cyclecloud_request_lookup,
        )

        return strategy
    except ImportError as exc:
        raise ImportError(f"Azure provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure strategy: {exc!s}")


def create_azure_config(data: dict[str, Any]) -> "AzureProviderConfig":
    """Create an ``AzureProviderConfig`` from a data dict."""
    try:
        from orb.providers.azure.configuration.config import AzureProviderConfig

        config_data = data.config if hasattr(data, "config") else data
        return AzureProviderConfig(**config_data)
    except ImportError as exc:
        raise ImportError(f"Azure configuration not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure config: {exc!s}")


def create_azure_validator(provider_config: Any = None) -> Optional["AzureValidationAdapter"]:
    """Create an Azure template validator."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.infrastructure.adapters.azure_validation_adapter import (
        AzureValidationAdapter,
    )

    if provider_config is None:
        return None

    try:
        if isinstance(provider_config, AzureProviderConfig):
            azure_config = provider_config
        elif hasattr(provider_config, "config"):
            azure_config = AzureProviderConfig(**provider_config.config)
        elif isinstance(provider_config, dict):
            azure_config = AzureProviderConfig(**provider_config)
        else:
            return None

        return AzureValidationAdapter(config=azure_config, logger=LoggingAdapter())
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure validator: {exc!s}")

# ------------------------------------------------------------------
# Provider registration
# ------------------------------------------------------------------


def _register_named_azure_provider_instance(
    registry: "ProviderRegistry",
    instance_name: str,
    *,
    config_port: Optional["ConfigurationPort"] = None,
) -> None:
    """Register a named Azure provider instance with the registry."""
    registry.register_provider_instance(
        provider_type="azure",
        instance_name=instance_name,
        strategy_factory=lambda provider_config: create_azure_strategy(
            provider_config,
            provider_instance_name=instance_name,
            config_port=config_port,
        ),
        config_factory=create_azure_config,
        validator_factory=create_azure_validator,
    )


def register_azure_provider(
    registry: Optional["ProviderRegistry"] = None,
    logger: Optional["LoggingPort"] = None,
    instance_name: Optional[str] = None,
    config_port: Optional["ConfigurationPort"] = None,
) -> None:
    """Register Azure provider with the provider registry."""
    if registry is None:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

    try:
        if instance_name:
            _register_named_azure_provider_instance(
                registry,
                instance_name,
                config_port=config_port,
            )
        else:
            registry.register_provider(
                provider_type="azure",
                strategy_factory=lambda provider_config: create_azure_strategy(
                    provider_config,
                    provider_instance_name="azure-default",
                    config_port=config_port,
                ),
                config_factory=create_azure_config,
                validator_factory=create_azure_validator,
            )

        if logger:
            logger.info("Azure provider registered successfully")
    except Exception as exc:
        if logger:
            logger.error("Failed to register Azure provider: %s", str(exc), exc_info=True)
        raise


def register_azure_provider_instance(
    provider_instance: Any,
    logger: Optional["LoggingPort"] = None,
    config_port: Optional["ConfigurationPort"] = None,
) -> bool:
    """Register an Azure provider instance using the canonical registry contract."""
    try:
        if logger:
            logger.debug("Registering Azure provider instance: %s", provider_instance.name)

        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        if not registry.is_provider_registered("azure"):
            register_azure_provider(
                registry=registry,
                logger=logger,
                config_port=config_port,
            )

        _register_named_azure_provider_instance(
            registry,
            provider_instance.name,
            config_port=config_port,
        )

        if logger:
            logger.debug(
                "Successfully registered Azure provider instance: %s", provider_instance.name
            )
        return True
    except Exception as exc:
        if logger:
            logger.error(
                "Failed to register Azure provider instance '%s': %s",
                provider_instance.name,
                str(exc),
                exc_info=True,
            )
        return False


# ------------------------------------------------------------------
# DI registration
# ------------------------------------------------------------------


def register_azure_provider_with_di(provider_instance: Any, container: Any) -> bool:
    """Register Azure provider instance using DI container context."""
    from orb.domain.base.ports import LoggingPort
    from orb.domain.base.ports.configuration_port import ConfigurationPort

    logger = container.get(LoggingPort)

    try:
        logger.debug("Registering Azure provider instance: %s", provider_instance.name)

        azure_config = create_azure_config(provider_instance.config)

        _register_azure_components_with_di(container, azure_config, provider_instance.name)

        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        def azure_strategy_factory(_config: Any) -> Any:
            """Factory to create Azure strategy using DI container."""
            return _create_azure_strategy_with_di(container, azure_config, provider_instance.name)

        if not registry.is_provider_registered("azure"):
            register_azure_provider(
                registry=registry,
                logger=logger,
                config_port=container.get(ConfigurationPort),
            )

        registry.register_provider_instance(
            provider_type="azure",
            instance_name=provider_instance.name,
            strategy_factory=azure_strategy_factory,
            config_factory=lambda _data: azure_config,
        )

        logger.debug("Successfully registered Azure provider instance: %s", provider_instance.name)
        return True

    except Exception as exc:
        logger.error(
            "Failed to register Azure provider instance '%s': %s",
            provider_instance.name,
            str(exc),
            exc_info=True,
        )
        return False


def _register_azure_components_with_di(
    container: Any, azure_config: "AzureProviderConfig", instance_name: str
) -> None:
    """Register Azure components with DI container for a specific instance."""
    from orb.domain.base.ports import LoggingPort

    def azure_client_factory(container_instance: Any) -> Any:
        """Factory to create an Azure client with the correct config and logger."""
        from orb.domain.base.ports.configuration_port import ConfigurationPort

        logger_port = container_instance.get(LoggingPort)
        runtime_config = _build_azure_client_runtime_config(
            azure_config,
            logger_port,
            config_port=container_instance.get(ConfigurationPort),
        )
        client = _create_azure_client(runtime_config, logger_port)
        logger_port.info(
            "Azure client initialized for %s: region=%s",
            instance_name,
            azure_config.region,
        )
        return client

    container.register_factory(f"AzureClient_{instance_name}", azure_client_factory)


def _create_azure_strategy_with_di(
    container: Any, azure_config: "AzureProviderConfig", instance_name: str
) -> "AzureProviderStrategy":
    """Create Azure strategy using DI container."""
    from orb.domain.base import UnitOfWorkFactory
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)
    azure_client = container.get(f"AzureClient_{instance_name}")

    from orb.providers.azure.services.cyclecloud_request_context_service import (
        create_cyclecloud_request_lookup,
    )
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    return AzureProviderStrategy(
        config=azure_config,
        logger=logger,
        provider_instance_name=instance_name,
        azure_client_resolver=lambda: azure_client,
        cyclecloud_request_lookup=create_cyclecloud_request_lookup(
            container.get(UnitOfWorkFactory)
        ),
    )


# ------------------------------------------------------------------
# Template extensions
# ------------------------------------------------------------------


def register_azure_extensions(logger: Optional["LoggingPort"] = None) -> None:
    """Register Azure template extensions with the global registry."""
    try:
        TemplateExtensionRegistry.register_extension("azure", AzureTemplateExtensionConfig)
        if logger:
            logger.debug("Azure template extensions registered successfully")
    except Exception as exc:
        error_msg = f"Failed to register Azure template extensions: {exc}"
        if logger:
            logger.error(error_msg)
        raise


def register_azure_provider_settings() -> None:
    """Register AzureProviderConfig with the provider settings registry."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.azure.configuration.config import AzureProviderConfig

        ProviderSettingsRegistry.register_provider_settings("azure", AzureProviderConfig)
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to register Azure provider settings: {exc!s}")


def register_azure_cli_spec() -> None:
    """Register Azure CLI spec for provider add/update commands."""
    try:
        from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
        from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec

        CLISpecRegistry.register("azure", AzureCLISpec())
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to register Azure CLI spec: {exc!s}")


def register_azure_template_factory(
    factory: TemplateFactory, logger: Optional["LoggingPort"] = None
) -> None:
    """Register AzureTemplate with the template factory."""
    try:
        from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate

        factory.register_provider_template_class("azure", AzureTemplate)
        if logger:
            logger.info("Azure template class registered with factory")
    except ImportError:
        if logger:
            logger.debug("Azure template class not available, using core template")
    except Exception as exc:
        if logger:
            logger.error("Failed to register Azure template factory: %s", exc, exc_info=True)


def get_azure_extension_defaults() -> dict[str, Any]:
    """Get default Azure extension configuration."""
    return AzureTemplateExtensionConfig().to_template_defaults()


def initialize_azure_provider(
    template_factory: Optional[TemplateFactory] = None,
    logger: Optional["LoggingPort"] = None,
) -> None:
    """Initialize Azure provider components at application startup."""
    try:
        register_azure_extensions(logger)
        register_azure_provider_settings()
        register_azure_cli_spec()
        if template_factory:
            register_azure_template_factory(template_factory, logger)
        if logger:
            logger.info("Azure provider initialization completed successfully")
    except Exception as exc:
        if logger:
            logger.error("Azure provider initialization failed: %s", exc, exc_info=True)
        raise


def is_azure_provider_registered() -> bool:
    """Check if Azure provider extensions are registered."""
    return TemplateExtensionRegistry.has_extension("azure")


def register_azure_services_with_di(container) -> None:
    """Register Azure services with the DI container."""
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    try:
        from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
            AzureNativeSpecService,
        )

        if not container.is_registered(AzureNativeSpecService):

            def create_azure_native_spec_service(c):
                """Factory to create AzureNativeSpecService with dependencies from DI container."""
                from orb.application.services.native_spec_service import NativeSpecService
                from orb.domain.base.ports.configuration_port import ConfigurationPort

                return AzureNativeSpecService(
                    native_spec_service=c.get(NativeSpecService),
                    config_port=c.get(ConfigurationPort),
                )

            container.register_factory(AzureNativeSpecService, create_azure_native_spec_service)
            logger.debug("Azure Native Spec Service registered with DI container")
    except Exception as exc:
        logger.warning("Failed to register Azure services with DI container: %s", exc, exc_info=True)


# ------------------------------------------------------------------
# Auto-register extensions on import
# ------------------------------------------------------------------

try:
    register_azure_extensions()
    register_azure_provider_settings()
    register_azure_cli_spec()
except Exception:
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "Failed to auto-register Azure extensions on import", exc_info=True
    )
