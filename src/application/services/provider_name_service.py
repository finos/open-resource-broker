"""Provider name generation and parsing service."""

from typing import Any, Dict

from domain.base.utils import extract_provider_type
from providers.registry import ProviderRegistry


class ProviderNameService:
    """Service for generating and parsing provider names."""

    def __init__(self, provider_registry: ProviderRegistry):
        self._provider_registry = provider_registry

    def generate_provider_name(self, provider_type: str, config: Dict[str, Any]) -> str:
        """Generate provider name using provider-specific strategy."""
        strategy = self._provider_registry.get_strategy(provider_type)
        return strategy.generate_provider_name(config)  # type: ignore[union-attr]

    def parse_provider_name(self, provider_name: str) -> Dict[str, str]:
        """Parse provider name using appropriate strategy."""
        provider_type = self._extract_provider_type(provider_name)
        strategy = self._provider_registry.get_strategy(provider_type)
        return strategy.parse_provider_name(provider_name)  # type: ignore[union-attr]

    def get_provider_name_pattern(self, provider_type: str) -> str:
        """Get naming pattern for provider type."""
        strategy = self._provider_registry.get_strategy(provider_type)
        return strategy.get_provider_name_pattern()  # type: ignore[union-attr]

    def _extract_provider_type(self, provider_name: str) -> str:
        """Extract provider type from provider name."""
        return extract_provider_type(provider_name)
