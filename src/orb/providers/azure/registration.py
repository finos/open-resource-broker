"""Azure Provider Registration.

Bootstrap helpers called at application startup to register the Azure
provider with the provider registry, template extension registry, and
DI container.
"""

from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.registry import ProviderRegistry

from orb.domain.template.extensions import TemplateExtensionRegistry
from orb.domain.template.factory import TemplateFactory
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig


class AzureInstanceConfigPort:
    """Minimal config port that wraps an already-resolved
    ``AzureProviderConfig``.

    ``AzureClient`` calls ``get_typed(ConfigClass)`` to resolve its
    config.  This shim ensures the client always receives the correct
    per-instance config rather than the global ``ConfigurationManager``
    (which does not carry Azure provider config).

    This deliberately does NOT implement ``ConfigurationPort`` — the
    client only needs ``get_typed(type) -> Any``, not the full 30+
    method interface.
    """

    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg

    def get_typed(self, config_type: type) -> Any:
        """Return the wrapped config if the requested type matches."""
        from orb.providers.azure.configuration.config import AzureProviderConfig

        if config_type == AzureProviderConfig:
            return self._cfg
        return None


def _create_azure_client(azure_config: Any, logger: Any) -> Any:
    """Construct an ``AzureClient`` with the per-instance config shim."""
    from orb.providers.azure.infrastructure.azure_client import AzureClient

    return AzureClient(config=AzureInstanceConfigPort(azure_config), logger=logger)


# ------------------------------------------------------------------
# Factory functions
# ------------------------------------------------------------------


def create_azure_strategy(provider_config: Any, *, provider_instance_name: str) -> Any:
    """Create an ``AzureProviderStrategy`` from provider configuration."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.azure.configuration.config import AzureProviderConfig
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    try:
        config_data = (
            provider_config.config
            if hasattr(provider_config, "config")
            else provider_config
        )
        azure_config = AzureProviderConfig(**config_data)
        logger = LoggingAdapter()
        strategy = AzureProviderStrategy(
            config=azure_config,
            logger=logger,
            provider_instance_name=provider_instance_name,
            azure_client_resolver=lambda: _create_azure_client(azure_config, logger),
        )

        return strategy
    except ImportError as exc:
        raise ImportError(f"Azure provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure strategy: {exc!s}")


def create_azure_config(data: dict[str, Any]) -> Any:
    """Create an ``AzureProviderConfig`` from a data dict."""
    try:
        from orb.providers.azure.configuration.config import AzureProviderConfig

        config_data = data.config if hasattr(data, "config") else data
        return AzureProviderConfig(**config_data)
    except ImportError as exc:
        raise ImportError(f"Azure configuration not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Azure config: {exc!s}")

# ------------------------------------------------------------------
# Provider registration
# ------------------------------------------------------------------


def _register_named_azure_provider_instance(registry: "ProviderRegistry", instance_name: str) -> None:
    """Register a named Azure provider instance with the registry."""
    registry.register_provider_instance(
        provider_type="azure",
        instance_name=instance_name,
        strategy_factory=lambda provider_config: create_azure_strategy(
            provider_config,
            provider_instance_name=instance_name,
        ),
        config_factory=create_azure_config,
    )


def register_azure_provider(
    registry: Optional["ProviderRegistry"] = None,
    logger: Optional["LoggingPort"] = None,
    instance_name: Optional[str] = None,
) -> None:
    """Register Azure provider with the provider registry."""
    if registry is None:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

    try:
        if instance_name:
            _register_named_azure_provider_instance(registry, instance_name)
        else:
            registry.register_provider(
                provider_type="azure",
                strategy_factory=lambda provider_config: create_azure_strategy(
                    provider_config,
                    provider_instance_name="azure-default",
                ),
                config_factory=create_azure_config,
            )

        if logger:
            logger.info("Azure provider registered successfully")
    except Exception as exc:
        if logger:
            logger.error("Failed to register Azure provider: %s", str(exc))
        raise


def register_azure_provider_instance(provider_instance: Any, logger: Optional["LoggingPort"] = None) -> bool:
    """Register an Azure provider instance using the canonical registry contract."""
    try:
        if logger:
            logger.debug("Registering Azure provider instance: %s", provider_instance.name)

        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        if not registry.is_provider_registered("azure"):
            register_azure_provider(registry=registry, logger=logger)

        _register_named_azure_provider_instance(registry, provider_instance.name)

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
            )
        return False


# ------------------------------------------------------------------
# DI registration
# ------------------------------------------------------------------


def register_azure_provider_with_di(provider_instance: Any, container: Any) -> bool:
    """Register Azure provider instance using DI container context."""
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    try:
        logger.debug("Registering Azure provider instance: %s", provider_instance.name)

        azure_config = create_azure_config(provider_instance.config)

        _register_azure_components_with_di(container, azure_config, provider_instance.name)

        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        def azure_strategy_factory(_config: Any) -> Any:
            return _create_azure_strategy_with_di(container, azure_config, provider_instance.name)

        if not registry.is_provider_registered("azure"):
            register_azure_provider(registry=registry, logger=logger)

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
        )
        return False


def _register_azure_components_with_di(
    container: Any, azure_config: Any, instance_name: str
) -> None:
    """Register Azure components with DI container for a specific instance."""
    from orb.domain.base.ports import LoggingPort
    from orb.providers.azure.infrastructure.azure_client import AzureClient

    def azure_client_factory(container_instance: Any) -> Any:
        logger_port = container_instance.get(LoggingPort)
        client = _create_azure_client(azure_config, logger_port)
        logger_port.info(
            "Azure client initialized for %s: region=%s",
            instance_name,
            azure_config.region,
        )
        return client

    container.register_factory(f"AzureClient_{instance_name}", azure_client_factory)


def _create_azure_strategy_with_di(
    container: Any, azure_config: Any, instance_name: str
) -> Any:
    """Create Azure strategy using DI container."""
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)
    azure_client = container.get(f"AzureClient_{instance_name}")

    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    return AzureProviderStrategy(
        config=azure_config,
        logger=logger,
        provider_instance_name=instance_name,
        azure_client_resolver=lambda: azure_client,
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
            logger.error("Failed to register Azure template factory: %s", exc)


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
        if template_factory:
            register_azure_template_factory(template_factory, logger)
        if logger:
            logger.info("Azure provider initialization completed successfully")
    except Exception as exc:
        if logger:
            logger.error("Azure provider initialization failed: %s", exc)
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
                from orb.application.services.native_spec_service import NativeSpecService
                from orb.domain.base.ports.configuration_port import ConfigurationPort

                return AzureNativeSpecService(
                    native_spec_service=c.get(NativeSpecService),
                    config_port=c.get(ConfigurationPort),
                )

            container.register_factory(AzureNativeSpecService, create_azure_native_spec_service)
            logger.debug("Azure Native Spec Service registered with DI container")
    except Exception as exc:
        logger.warning("Failed to register Azure services with DI container: %s", exc)


# ------------------------------------------------------------------
# Auto-register extensions on import
# ------------------------------------------------------------------

with suppress(Exception):
    register_azure_extensions()
