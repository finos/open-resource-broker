"""GCP Provider implementation."""

from typing import TYPE_CHECKING, Any

from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.registration import (
    get_gcp_extension_defaults,
    initialize_gcp_provider,
    is_gcp_provider_registered,
    register_gcp_extensions,
    register_gcp_template_factory,
)

if TYPE_CHECKING:
    from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy


def __getattr__(name: str) -> Any:
    # The strategy pulls in the cloud SDK, so it is imported on first access
    # rather than whenever the provider package is imported.
    if name == "GCPProviderStrategy":
        from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

        return GCPProviderStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = [
    "GCPProviderConfig",
    "GCPProviderStrategy",
    "GCPTemplateExtensionConfig",
    "get_gcp_extension_defaults",
    "initialize_gcp_provider",
    "is_gcp_provider_registered",
    "register_gcp_extensions",
    "register_gcp_template_factory",
]
