"""Domain port for provider name generation and parsing."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class ProviderNamePort(ABC):
    """Port for provider name generation and parsing operations."""

    @abstractmethod
    def generate_provider_name(self, provider_type: str, config: Dict[str, Any]) -> str:
        """Generate provider name using provider-specific strategy."""

    @abstractmethod
    def parse_provider_name(self, provider_name: str) -> Dict[str, str]:
        """Parse provider name back to its components."""

    @abstractmethod
    def get_provider_name_pattern(self, provider_type: str) -> str:
        """Get naming pattern for provider type."""
