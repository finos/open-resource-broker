"""Azure Provider implementation."""

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from orb.providers.azure.registration import (
    get_azure_extension_defaults,
    initialize_azure_provider,
    is_azure_provider_registered,
    register_azure_extensions,
    register_azure_template_factory,
)

__all__: list[str] = [
    "AzureProviderConfig",
    "AzureTemplateExtensionConfig",
    "get_azure_extension_defaults",
    "initialize_azure_provider",
    "is_azure_provider_registered",
    "register_azure_extensions",
    "register_azure_template_factory",
]
