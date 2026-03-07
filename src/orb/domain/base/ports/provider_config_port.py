"""Focused port for provider configuration reads.

ISP-compliant interface: clients that only need provider config
should depend on this rather than the full ConfigurationPort.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class ProviderConfigPort(ABC):
    """Focused port for reading provider configuration.

    Extracted from ConfigurationPort to satisfy ISP - provisioning
    and provider components only need these two methods.
    """

    @abstractmethod
    def get_provider_config(self) -> Optional[Any]:
        """Get provider configuration root."""

    @abstractmethod
    def get_provider_instance_config(self, provider_name: str) -> Any:
        """Get configuration for a specific provider instance."""
