"""Provider Strategy Resolver - Registry-based strategy identifier resolution."""

from typing import Optional

from providers.base.strategy.provider_context import ProviderContext


class ProviderStrategyResolver:
    """Resolves provider strategy identifiers using the registry pattern."""

    def __init__(self, provider_context: ProviderContext):
        self._context = provider_context

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
        # Try exact match first
        if provider_name:
            candidate = f"{provider_type}-{provider_name}"
            if candidate in self._context.available_strategies:
                return candidate
        
        # Try with 'default' fallback
        default_candidate = f"{provider_type}-default"
        if default_candidate in self._context.available_strategies:
            return default_candidate
            
        # Try provider type only
        if provider_type in self._context.available_strategies:
            return provider_type
            
        return None

    def get_available_strategies(self) -> list[str]:
        """Get all available strategy identifiers from registry."""
        return self._context.available_strategies

    def validate_strategy_exists(self, strategy_identifier: str) -> bool:
        """Check if strategy exists in registry."""
        return strategy_identifier in self._context.available_strategies
