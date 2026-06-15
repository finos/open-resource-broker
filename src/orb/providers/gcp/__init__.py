"""GCP Provider implementation."""

from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.registration import (
    get_gcp_extension_defaults,
    initialize_gcp_provider,
    is_gcp_provider_registered,
    register_gcp_extensions,
    register_gcp_template_factory,
)
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

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
