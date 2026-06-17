"""Azure provider defaults loader."""

from __future__ import annotations

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort
from orb.providers.azure.registration import get_azure_extension_defaults


class AzureDefaultsLoader:
    """Loads Azure provider defaults from the provider-owned template extension."""

    def load_defaults(self) -> dict:
        """Return Azure provider defaults contributed by the Azure provider."""
        return {
            "provider": {
                "provider_defaults": {
                    "azure": {
                        "template_defaults": get_azure_extension_defaults(),
                    }
                }
            }
        }


assert isinstance(AzureDefaultsLoader(), ProviderDefaultsLoaderPort)
