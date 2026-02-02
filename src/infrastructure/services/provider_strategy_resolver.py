"""Provider Strategy Resolver - Registry-based strategy identifier resolution."""

from typing import Optional

from providers.registry import get_provider_registry


class ProviderStrategyResolver:
    """Resolves provider strategy identifiers using the registry pattern."""

    def __init__(self):
        self._registry = get_provider_registry()

    def resolve_strategy_identifier(
        self, 
        provider_type: str, 
        provider_name: Optional[str] = None
    ) -> Optional[str]:
        """
        Resolve strategy identifier using registry lookup.
        
        Args:
            provider_type: Provider type (e.g., 'aws')
            provider_name: Provider instance name (e.g., 'aws_default_us-east-1')
            
        Returns:
            Registered strategy identifier or None if not found
        """
        # Try exact instance match first
        if provider_name and self._registry.is_provider_instance_registered(provider_name):
            return provider_name
        
        # Try provider type
        if self._registry.is_provider_registered(provider_type):
            return provider_type
            
        return None

    def get_available_strategies(self) -> list[str]:
        """Get all available strategy identifiers from registry."""
        types = self._registry.get_registered_providers()
        instances = self._registry.get_registered_provider_instances()
        return types + instances

    def validate_strategy_exists(self, strategy_identifier: str) -> bool:
        """Check if strategy exists in registry."""
        return (self._registry.is_provider_registered(strategy_identifier) or 
                self._registry.is_provider_instance_registered(strategy_identifier))
