"""OCI provider registration."""

from __future__ import annotations

from typing import Any

from orb.providers.oci.factories import create_oci_config, create_oci_strategy

__all__ = ["create_oci_config", "create_oci_strategy"]


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
