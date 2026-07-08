"""Azure Provider Registration.

Bootstrap helpers called at application startup to register the Azure
provider with the provider registry, template extension registry, and
DI container.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Optional, Protocol, TypeVar

from orb.config import PerformanceConfig
from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.infrastructure.adapters.azure_validation_adapter import (
        AzureValidationAdapter,
    )
    from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
        AzureNativeSpecService,
    )
    from orb.providers.azure.infrastructure.azure_client import (
        AzureClient,
        AzureClientRuntimeConfig,
    )
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
    from orb.providers.registry import ProviderRegistry

from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig

T = TypeVar("T")


class PerformanceConfigSource(Protocol):
    """Configuration source shape used to resolve shared performance settings."""

    def get_typed(self, config_type: type[T]) -> T:
        """Return a typed configuration object."""
        ...


def _resolve_performance_config(
    config_port: PerformanceConfigSource | None,
    logger: "LoggingPort",
) -> PerformanceConfig:
    """Resolve shared performance config, falling back to defaults."""
    if config_port is None:
        logger.debug("No shared config port available; using default Azure performance config")
        return PerformanceConfig.model_validate({})

    try:
        perf_config = config_port.get_typed(PerformanceConfig)
    except Exception as exc:
        logger.debug("Could not load performance config from shared config port: %s", exc)
        return PerformanceConfig.model_validate({})

    if not isinstance(perf_config, PerformanceConfig):
        logger.debug(
            "Ignoring unexpected performance config type from shared config port: %s",
            type(perf_config).__name__,
        )
        return PerformanceConfig.model_validate({})

    return perf_config


def _build_azure_client_runtime_config(
    azure_config: "AzureProviderConfig",
    logger: "LoggingPort",
    *,
    performance_config: PerformanceConfig | None = None,
    config_port: PerformanceConfigSource | None = None,
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
    provider_config: Mapping[str, Any],
    *,
    provider_instance_name: str,
    performance_config: PerformanceConfig | None = None,
    config_port: PerformanceConfigSource | None = None,
    azure_native_spec_service: Optional["AzureNativeSpecService"] = None,
) -> "AzureProviderStrategy":
    """Create an ``AzureProviderStrategy`` from raw provider config data.

    Azure/GCP are standardized at this boundary for now: provider factories
    consume only the provider's config mapping, while instance registration
    is responsible for unpacking ``provider_instance.config``.
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    try:
        azure_config = AzureProviderConfig(**provider_config)
        logger = LoggingAdapter()
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
            azure_native_spec_service=azure_native_spec_service,
        )

        return strategy
    except ImportError as exc:
        raise ImportError(f"Azure provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure strategy: {exc!s}")


def create_azure_config(data: ProviderInstanceConfig | Mapping[str, Any]) -> "AzureProviderConfig":
    """Create an ``AzureProviderConfig`` from registry config input."""
    try:
        from orb.providers.azure.configuration.config import AzureProviderConfig

        config_data = data.config if isinstance(data, ProviderInstanceConfig) else data
        return AzureProviderConfig(**config_data)
    except ImportError as exc:
        raise ImportError(f"Azure configuration not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure config: {exc!s}")


def create_azure_validator(
    provider_config: "AzureProviderConfig | Mapping[str, Any] | None" = None,
) -> Optional["AzureValidationAdapter"]:
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
        elif isinstance(provider_config, Mapping):
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
    config_port: PerformanceConfigSource | None = None,
) -> None:
    """Register a named Azure provider instance with the registry."""
    registry.register_provider_instance(
        provider_type="azure",
        instance_name=instance_name,
        # Keep wrapper unpacking at the registry boundary instead of inside
        # provider factories so Azure/GCP share one local contract.
        strategy_factory=lambda provider_instance_config: create_azure_strategy(
            provider_instance_config.config,
            provider_instance_name=instance_name,
            config_port=config_port,
        ),
        config_factory=create_azure_config,
        validator_factory=lambda provider_instance_config: create_azure_validator(
            provider_instance_config.config
        ),
    )


def register_azure_provider(
    registry: Optional["ProviderRegistry"] = None,
    logger: Optional["LoggingPort"] = None,
    instance_name: Optional[str] = None,
    config_port: PerformanceConfigSource | None = None,
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

        register_azure_auth_strategies(logger)
        if logger:
            logger.info("Azure provider registered successfully")
    except Exception as exc:
        if logger:
            logger.error("Failed to register Azure provider: %s", str(exc), exc_info=True)
        raise


def register_azure_provider_instance(
    provider_instance: Any,
    logger: Optional["LoggingPort"] = None,
    config_port: PerformanceConfigSource | None = None,
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
    from orb.providers.azure.infrastructure.azure_handler_factory import AzureHandlerFactory

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

    def azure_handler_factory_factory(container_instance: Any) -> Any:
        """Factory to create an Azure handler factory for the named provider instance."""
        from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
            AzureNativeSpecService,
        )
        from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager

        azure_client = container_instance.get(f"AzureClient_{instance_name}")
        logger_port = container_instance.get(LoggingPort)
        return AzureHandlerFactory(
            azure_client=azure_client,
            logger=logger_port,
            azure_native_spec_service=container_instance.get_optional(AzureNativeSpecService),
            azure_resource_manager=AzureResourceManager(
                azure_client=azure_client,
                config=azure_config,
                logger=logger_port,
            ),
        )

    container.register_factory(
        f"AzureHandlerFactory_{instance_name}",
        azure_handler_factory_factory,
    )


def _create_azure_strategy_with_di(
    container: Any, azure_config: "AzureProviderConfig", instance_name: str
) -> "AzureProviderStrategy":
    """Create Azure strategy using DI container."""
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
        AzureNativeSpecService,
    )
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    return AzureProviderStrategy(
        config=azure_config,
        logger=logger,
        provider_instance_name=instance_name,
        azure_client_resolver=lambda: container.get(f"AzureClient_{instance_name}"),
        azure_handler_factory_resolver=lambda: container.get(
            f"AzureHandlerFactory_{instance_name}"
        ),
        azure_native_spec_service=container.get_optional(AzureNativeSpecService),
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
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
        from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec

        CLISpecRegistry.register("azure", AzureCLISpec())
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to register Azure CLI spec: {exc!s}")


def register_azure_hostfactory_field_mapping() -> None:
    """Register Azure HostFactory field mapping."""
    try:
        from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
            FieldMappingRegistry,
        )
        from orb.providers.azure.scheduler.hostfactory_field_mapping import (
            AzureFieldMapping,
        )

        FieldMappingRegistry.register("azure", AzureFieldMapping())
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to register Azure HostFactory field mapping: {exc!s}")


def register_azure_auth_strategies(logger: Optional["LoggingPort"] = None) -> None:
    """Register Azure authentication strategies with the auth registry."""
    try:
        from orb.infrastructure.auth.registry import get_auth_registry
        from orb.providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        registry = get_auth_registry()
        if not registry.is_registered("azure"):
            registry.register_strategy("azure", AzureAuthStrategy)
            if logger:
                logger.debug("Azure auth strategy registered")
    except ImportError as exc:
        if logger:
            logger.warning("Azure auth strategy not available: %s", exc)
    except Exception as exc:
        if logger:
            logger.error("Failed to register Azure auth strategy: %s", exc, exc_info=True)
        raise


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
    return AzureTemplateExtensionConfig(
        priority="Regular",
        os_disk_type="Premium_LRS",
        os_disk_size_gb=None,
        admin_username="azureuser",
    ).to_template_defaults()


def initialize_azure_provider(
    template_factory: Optional[TemplateFactory] = None,
    logger: Optional["LoggingPort"] = None,
) -> None:
    """Initialize Azure provider components at application startup."""
    try:
        register_azure_extensions(logger)
        register_azure_provider_settings()
        register_azure_cli_spec()
        register_azure_hostfactory_field_mapping()
        register_azure_auth_strategies(logger)
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
        logger.warning(
            "Failed to register Azure services with DI container: %s", exc, exc_info=True
        )


# ------------------------------------------------------------------
# Auto-register extensions on import
# ------------------------------------------------------------------

try:
    register_azure_extensions()
    register_azure_provider_settings()
    register_azure_cli_spec()
    register_azure_hostfactory_field_mapping()
    register_azure_auth_strategies()
except Exception:
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "Failed to auto-register Azure extensions on import", exc_info=True
    )
