"""GCP provider factories shared by registration and the plugin."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    from orb.providers.gcp.configuration.config import GCPProviderConfig
    from orb.providers.gcp.infrastructure.adapters.gcp_validation_adapter import (
        GCPValidationAdapter,
    )
    from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy


class GCPProviderInstanceProtocol(Protocol):
    """Named provider instance shape consumed by GCP registration."""

    name: str
    config: Mapping[str, Any]


def _provider_config_data(
    data: Mapping[str, Any] | GCPProviderInstanceProtocol,
) -> Mapping[str, Any]:
    """Return the raw provider config mapping from registry inputs."""
    if isinstance(data, Mapping):
        return data
    return data.config


def create_gcp_strategy(
    provider_config: Mapping[str, Any] | GCPProviderInstanceProtocol,
    *,
    provider_name: Optional[str] = None,
) -> GCPProviderStrategy:
    """Create a GCP provider strategy from raw provider config data.

    Provider factories consume only the provider's config mapping, while
    instance registration is responsible for unpacking ``provider_instance.config``.
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.gcp.configuration.config import GCPProviderConfig
    from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

    config_data = _provider_config_data(provider_config)
    gcp_config = GCPProviderConfig(**(config_data or {}))
    logger = LoggingAdapter()
    strategy = GCPProviderStrategy(
        config=gcp_config,
        logger=logger,
        provider_name=provider_name,
    )
    if not strategy.initialize():
        raise RuntimeError("Failed to initialize GCP provider strategy")
    return strategy


def create_gcp_config(data: Mapping[str, Any] | GCPProviderInstanceProtocol) -> GCPProviderConfig:
    """Create typed GCP config."""
    from orb.providers.gcp.configuration.config import GCPProviderConfig

    return GCPProviderConfig(**_provider_config_data(data))


def create_gcp_validator(
    provider_config: Mapping[str, Any] | GCPProviderConfig | None = None,
) -> GCPValidationAdapter | None:
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
