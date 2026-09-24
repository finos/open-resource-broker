"""GCP provider registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.registry import ProviderRegistry

from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.providers.gcp.cli.gcp_cli_spec import GCPCLISpec
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.factories import (
    GCPProviderInstanceProtocol,
    create_gcp_config,
    create_gcp_strategy,
    create_gcp_validator,
)


def register_gcp_provider(
    registry: Optional[ProviderRegistry] = None,
    logger: Optional[LoggingPort] = None,
    instance_name: Optional[str] = None,
) -> None:
    """Register GCP provider with the provider registry."""
    if registry is None:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

    if instance_name:
        registry.register_provider_instance(
            provider_type="gcp",
            instance_name=instance_name,
            # Keep wrapper unpacking at the registry boundary instead of inside
            # provider factories so GCP keeps one local contract.
            strategy_factory=lambda provider_instance_config: create_gcp_strategy(
                provider_instance_config.config,
                provider_name=provider_instance_config.name,
            ),
            config_factory=create_gcp_config,
            validator_factory=lambda provider_instance_config: create_gcp_validator(
                provider_instance_config.config
            ),
        )
    else:
        from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

        registry.register_provider(
            provider_type="gcp",
            strategy_factory=create_gcp_strategy,
            config_factory=create_gcp_config,
            validator_factory=create_gcp_validator,
            strategy_class=GCPProviderStrategy,
        )
    CLISpecRegistry.register("gcp", GCPCLISpec())
    if logger:
        logger.info("GCP provider registered successfully")


def register_gcp_provider_instance(
    provider_instance: GCPProviderInstanceProtocol,
    logger: Optional[LoggingPort] = None,
) -> bool:
    """Register a named GCP provider instance."""
    try:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        if not registry.is_provider_registered("gcp"):
            register_gcp_provider(registry=registry, logger=logger)
        registry.register_provider_instance(
            provider_type="gcp",
            instance_name=provider_instance.name,
            # Keep wrapper unpacking at the registry boundary instead of inside
            # provider factories so GCP keeps one local contract.
            strategy_factory=lambda provider_instance_config: create_gcp_strategy(
                provider_instance_config.config,
                provider_name=provider_instance_config.name,
            ),
            config_factory=create_gcp_config,
            validator_factory=lambda provider_instance_config: create_gcp_validator(
                provider_instance_config.config
            ),
        )
        return True
    except Exception as exc:
        if logger:
            logger.error("Failed to register GCP provider instance: %s", exc, exc_info=True)
        return False


def register_gcp_extensions(logger: Optional[LoggingPort] = None) -> None:
    """Register GCP template extensions."""
    TemplateExtensionRegistry.register_extension("gcp", GCPTemplateExtensionConfig)
    CLISpecRegistry.register("gcp", GCPCLISpec())
    if logger:
        logger.debug("GCP template extensions registered successfully")


def register_gcp_provider_settings() -> None:
    """Register GCPProviderConfig with the provider settings registry."""
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.providers.gcp.configuration.config import GCPProviderConfig

    ProviderSettingsRegistry.register_provider_settings("gcp", GCPProviderConfig)


def register_gcp_template_factory(
    factory: TemplateFactory, logger: Optional[LoggingPort] = None
) -> None:
    """Register GCP template class with the template factory."""
    from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate

    factory.register_provider_template_class("gcp", GCPTemplate)
    if logger:
        logger.info("GCP template class registered with factory")


def get_gcp_extension_defaults() -> dict[str, Any]:
    """Get default GCP template defaults."""
    return GCPTemplateExtensionConfig().to_template_defaults()


def initialize_gcp_provider(
    template_factory: Optional[TemplateFactory] = None,
    logger: Optional[LoggingPort] = None,
) -> None:
    """Initialize GCP's provider satellites during bootstrap."""
    from orb.providers.gcp.provider_plugin import GCPPlugin

    GCPPlugin().initialize_provider(template_factory=template_factory, logger=logger)


def register_gcp_services_with_di(container: Any) -> None:
    """Register GCP utility services and its template example generator."""
    from orb.providers.gcp.provider_plugin import GCPPlugin

    GCPPlugin().register_services_with_di(container)


def is_gcp_provider_registered() -> bool:
    """Return whether GCP extensions are registered."""
    return TemplateExtensionRegistry.has_extension("gcp")


register_gcp_extensions()
register_gcp_provider_settings()
