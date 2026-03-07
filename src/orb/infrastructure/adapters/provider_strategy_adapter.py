"""Provider strategy adapter implementing domain provider strategy resolver port."""

from typing import Optional

from orb.domain.base.ports.provider_strategy_resolver_port import ProviderStrategyResolverPort
from orb.infrastructure.services.provider_strategy_resolver import ProviderStrategyResolver


class ProviderStrategyResolverAdapter(ProviderStrategyResolverPort):
    """Adapter for provider strategy resolution."""

    def __init__(self, resolver: ProviderStrategyResolver) -> None:
        """Initialize with strategy resolver.

        Args:
            resolver: Underlying strategy resolver implementation
        """
        self._resolver = resolver

    def resolve_strategy_identifier(
        self, provider_type: str, provider_name: Optional[str] = None
    ) -> Optional[str]:
        """Resolve strategy identifier using registry lookup."""
        return self._resolver.resolve_strategy_identifier(provider_type, provider_name)

    def get_available_strategies(self) -> list[str]:
        """Get all available strategy identifiers."""
        return self._resolver.get_available_strategies()

    def validate_strategy_exists(self, strategy_identifier: str) -> bool:
        """Check if strategy exists."""
        return self._resolver.validate_strategy_exists(strategy_identifier)
