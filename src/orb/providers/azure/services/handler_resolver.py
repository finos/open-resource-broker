"""Handler resolution contract for Azure orchestration services."""

from typing import Optional, Protocol

from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler


class AzureHandlerResolver(Protocol):
    """Resolve the handler responsible for an Azure provider API."""

    def resolve_handler(
        self,
        provider_api: AzureProviderApi,
        *,
        allow_vmss_uniform_fallback: bool = False,
    ) -> Optional[AzureHandler]:
        """Return the configured handler, or ``None`` when none is available."""
        ...
