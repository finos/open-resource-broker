"""GCP provider defaults loader."""

from __future__ import annotations

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort
from orb.providers.gcp.capabilities import get_supported_api_capabilities
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig


def build_gcp_defaults() -> dict:
    """Return GCP template defaults and handler capabilities.

    Kept free of the provider strategy so reading defaults does not import the
    Google Cloud SDK.
    """
    defaults = GCPTemplateExtensionConfig().to_template_defaults()
    handlers = {
        api_name: {
            "handler_class": capabilities["handler_class"],
            "supported_fleet_types": capabilities["supported_fleet_types"],
            "supports_spot": capabilities["supports_spot"],
            "supports_ondemand": capabilities["supports_on_demand"],
            "max_instances": capabilities["max_instances"],
        }
        for api_name, capabilities in get_supported_api_capabilities().items()
    }
    return {
        "provider": {
            "provider_defaults": {
                "gcp": {
                    "handlers": handlers,
                    "template_defaults": defaults,
                }
            },
        }
    }


class GCPDefaultsLoader:
    """Load defaults exposed by the GCP provider."""

    def load_defaults(self) -> dict:
        """Return GCP provider defaults."""
        return build_gcp_defaults()


assert isinstance(  # noqa: S101  # module-level protocol-conformance smoke check, also enforced statically by pyright
    GCPDefaultsLoader(), ProviderDefaultsLoaderPort
)
