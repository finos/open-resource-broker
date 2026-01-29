"""Provider Strategy Resolver Port - Domain interface for strategy resolution."""

from abc import ABC, abstractmethod
from typing import Optional

from domain.base.value_objects import ProviderType


class ProviderStrategyResolverPort(ABC):
    """Domain port for resolving provider strategy identifiers."""

    @abstractmethod
    def resolve_strategy_identifier(
        self, 
        provider_type: str, 
        provider_name: Optional[str] = None
    ) -> Optional[str]:
        """
        Resolve the correct strategy identifier for a provider.
        
        Args:
            provider_type: The provider type (e.g., 'aws')
            provider_name: The provider instance name (e.g., 'aws_default_us-east-1')
            
        Returns:
            Strategy identifier if found, None otherwise
        """
        pass

    @abstractmethod
    def get_available_strategies(self) -> list[str]:
        """Get list of all available strategy identifiers."""
        pass

    @abstractmethod
    def validate_strategy_exists(self, strategy_identifier: str) -> bool:
        """Check if a strategy identifier exists in the registry."""
        pass
