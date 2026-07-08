"""Azure provider defaults loader."""

from __future__ import annotations

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort
from orb.providers.azure.capabilities import get_supported_api_capabilities
from orb.providers.azure.registration import get_azure_extension_defaults


_HANDLER_CLASSES = {
    "VMSS": "VMSSHandler",
    "VMSSUniform": "VMSSHandler",
    "SingleVM": "SingleVMHandler",
    "CycleCloud": "CycleCloudHandler",
}


class AzureDefaultsLoader:
    """Loads Azure provider defaults from the provider-owned template extension."""

    def load_defaults(self) -> dict:
        """Return Azure provider defaults contributed by the Azure provider."""
        handlers = {
            api: {"handler_class": _HANDLER_CLASSES[api], **capabilities}
            for api, capabilities in get_supported_api_capabilities().items()
        }
        return {
            "provider": {
                "provider_defaults": {
                    "azure": {
                        "handlers": handlers,
                        "template_defaults": get_azure_extension_defaults(),
                    }
                }
            }
        }


assert isinstance(AzureDefaultsLoader(), ProviderDefaultsLoaderPort)
