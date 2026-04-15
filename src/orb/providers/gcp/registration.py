"""GCP provider registration."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.registry import ProviderRegistry

from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
from orb.domain.template.extensions import TemplateExtensionRegistry
from orb.domain.template.factory import TemplateFactory
from orb.providers.gcp.cli.gcp_cli_spec import GCPCLISpec
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig


def create_gcp_strategy(
    provider_config: Mapping[str, Any],
    *,
    provider_name: Optional[str] = None,
) -> Any:
    """Create a GCP provider strategy from raw provider config data.

    Azure/GCP are standardized at this boundary for now: provider factories
    consume only the provider's config mapping, while instance registration
    is responsible for unpacking ``provider_instance.config``.
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.gcp.configuration.config import GCPProviderConfig
    from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

    gcp_config = GCPProviderConfig(**(provider_config or {}))
    logger = LoggingAdapter()
    strategy = GCPProviderStrategy(
        config=gcp_config,
        logger=logger,
        provider_name=provider_name,
    )
    if not strategy.initialize():
        raise RuntimeError("Failed to initialize GCP provider strategy")
    return strategy


def create_gcp_config(data: Mapping[str, Any]) -> Any:
    """Create typed GCP config."""
    from orb.providers.gcp.configuration.config import GCPProviderConfig

    return GCPProviderConfig(**data)


def create_gcp_validator(provider_config: "Mapping[str, Any] | GCPProviderConfig | None" = None) -> Any:
    """Create GCP template validator."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.gcp.configuration.config import GCPProviderConfig
    from orb.providers.gcp.infrastructure.adapters.gcp_validation_adapter import (
        GCPValidationAdapter,
    )

    if provider_config is None:
        return None

    if isinstance(provider_config, GCPProviderConfig):
        config = provider_config
    elif isinstance(provider_config, Mapping):
        config = GCPProviderConfig(**provider_config)
    else:
        return None
    return GCPValidationAdapter(config=config, logger=LoggingAdapter())


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
            # provider factories so Azure/GCP share one local contract.
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
    provider_instance: Any,
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
            # provider factories so Azure/GCP share one local contract.
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
    """Initialize GCP provider components."""
    register_gcp_extensions(logger)
    if template_factory:
        register_gcp_template_factory(template_factory, logger)


def is_gcp_provider_registered() -> bool:
    """Return whether GCP extensions are registered."""
    return TemplateExtensionRegistry.has_extension("gcp")


with suppress(Exception):
    register_gcp_extensions()
