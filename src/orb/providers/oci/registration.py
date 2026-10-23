"""OCI provider registration."""

from __future__ import annotations

from typing import Any


def _extract_oci_provider_settings(provider_config: Any) -> dict[str, Any]:
    """Unwrap provider instance entries to the inner OCI settings dict."""
    if provider_config is None:
        return {}
    if hasattr(provider_config, "config"):
        provider_config = provider_config.config
    if hasattr(provider_config, "model_dump"):
        provider_config = provider_config.model_dump()
    elif hasattr(provider_config, "dict"):
        provider_config = provider_config.dict()
    if isinstance(provider_config, dict):
        nested = provider_config.get("config")
        if isinstance(nested, dict):
            return nested
        if nested is not None and hasattr(nested, "model_dump"):
            return nested.model_dump()
        if nested is not None and hasattr(nested, "dict"):
            return nested.dict()
        return provider_config
    return {}


def create_oci_strategy(provider_config: Any) -> Any:
    """Create OCI provider strategy from configuration."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.oci.configuration.config import OCIProviderConfig
    from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

    try:
        if isinstance(provider_config, OCIProviderConfig):
            oci_config = provider_config
            provider_instance_config = None
            provider_name = None
        elif hasattr(provider_config, "config"):
            provider_instance_config = provider_config
            # getattr: config wrappers expose settings but may omit an instance name.
            provider_name = getattr(provider_config, "name", None)
            oci_config = OCIProviderConfig(**_extract_oci_provider_settings(provider_config))
        else:
            provider_instance_config = (
                provider_config if isinstance(provider_config, dict) else None
            )
            provider_name = (
                provider_config.get("name") if isinstance(provider_config, dict) else None
            )
            oci_config = OCIProviderConfig(**_extract_oci_provider_settings(provider_config))

        logger = LoggingAdapter()
        strategy = OCIProviderStrategy(
            config=oci_config,
            logger=logger,
            provider_name=provider_name,
            provider_instance_config=provider_instance_config,
        )

        if not strategy.initialize():
            raise RuntimeError("Failed to initialize OCI provider strategy")

        return strategy
    except ImportError as exc:
        raise ImportError(f"OCI provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create OCI strategy: {exc!s}")


def create_oci_config(data: Any) -> Any:
    """Create OCI configuration from data dictionary."""
    from orb.providers.oci.configuration.config import OCIProviderConfig

    try:
        if isinstance(data, OCIProviderConfig):
            return data
        if isinstance(data, dict) or hasattr(data, "config"):
            return OCIProviderConfig(**_extract_oci_provider_settings(data))
        return OCIProviderConfig()
    except Exception as exc:
        raise RuntimeError(f"Failed to create OCI config: {exc!s}")


def register_oci_provider(registry=None, logger=None, instance_name=None) -> None:
    """Register OCI using the shared plugin lifecycle."""
    from orb.providers.oci.provider_plugin import OCIPlugin

    plugin = OCIPlugin()
    plugin.register_provider(registry, logger, instance_name)


def initialize_oci_provider(template_factory=None, logger=None) -> None:
    """Populate all provider satellites through the canonical plugin."""
    from orb.providers.oci.provider_plugin import OCIPlugin

    OCIPlugin().initialize_provider(template_factory, logger)


def register_oci_services_with_di(container) -> None:
    """Register OCI example generation without replacing other providers."""
    from orb.providers.oci.provider_plugin import OCIPlugin

    OCIPlugin().register_services_with_di(container)


def register_oci_template_factory(template_factory, logger=None) -> None:
    """Register the concrete template on each newly constructed factory."""
    from orb.providers.oci.domain.template import OCITemplate

    template_factory.register_provider_template_class("oci", OCITemplate)


def register_oci_provider_instance(provider_instance: Any, logger: Any | None = None) -> bool:
    """Register a named OCI instance using the common plugin factories."""
    from orb.providers.oci.provider_plugin import OCIPlugin
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    plugin = OCIPlugin()
    if not registry.is_provider_registered("oci"):
        plugin.register_provider(registry, logger)
    plugin.register_provider(registry, logger, provider_instance.name)
    return True
