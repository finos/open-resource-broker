"""Azure provider configuration."""

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from orb.providers.azure.configuration.validator import validate_azure_config, validate_azure_template

__all__: list[str] = [
    "AzureProviderConfig",
    "AzureTemplateExtensionConfig",
    "validate_azure_config",
    "validate_azure_template",
]

