"""Provider Strategy Pattern - Base strategy interfaces and implementations.

This package implements the Strategy pattern for provider operations,
enabling runtime selection and switching of provider strategies while
maintaining clean separation of concerns and SOLID principles compliance.

Key Components:
- ProviderStrategy: Abstract base class for all provider strategies
- ProviderContext: Context for managing and executing strategies
- ProviderSelector: Algorithms for selecting optimal strategies
- CompositeProviderStrategy: Multi-provider composition and orchestration
- FallbackProviderStrategy: Resilience and failover capabilities
- LoadBalancingProviderStrategy: Performance optimization and load distribution
- Value Objects: Operation, Result, Capabilities, Health status

Usage Example:
    from providers.base.strategy import (
        ProviderContext,
        ProviderOperation,
        ProviderOperationType,
        SelectorFactory,
        SelectionPolicy,
        CompositeProviderStrategy,
        FallbackProviderStrategy,
        LoadBalancingProviderStrategy
    )

    # Create context with strategy selection
    context = ProviderContext()
    selector = SelectorFactory.create_selector(SelectionPolicy.PERFORMANCE_BASED)

    # Register strategies
    context.register_strategy(aws_strategy)
    context.register_strategy(provider1_strategy)

    # Or use advanced strategies
    composite = CompositeProviderStrategy([aws_strategy, provider1_strategy])
    fallback = FallbackProviderStrategy(aws_strategy, [provider1_strategy])
    load_balancer = LoadBalancingProviderStrategy([aws_strategy, provider1_strategy])

    # Execute operations
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={'count': 5, 'template_id': 'web-server'}
    )

    result = await context.execute_operation(operation)
"""

# Advanced strategy patterns
from .base_provider_strategy import BaseProviderStrategy
from .composite_strategy import (
    AggregationPolicy,
    CompositeProviderStrategy,
    CompositionConfig,
    CompositionMode,
    StrategyExecutionResult,
)
from .fallback_strategy import (
    CircuitBreakerState,
    CircuitState,
    FallbackConfig,
    FallbackMode,
    FallbackProviderStrategy,
)
from .load_balancing_strategy import (
    HealthCheckMode,
    LoadBalancingAlgorithm,
    LoadBalancingConfig,
    LoadBalancingProviderStrategy,
    StrategyStats,
)

# Strategy context and management
from .provider_context import ProviderContext, StrategyMetrics

# Strategy selection algorithms
from .provider_selector import (
    FirstAvailableSelector,
    PerformanceBasedSelector,
    ProviderSelector,
    RandomSelector,
    RoundRobinSelector,
    SelectionCriteria,
    SelectionPolicy,
    SelectionResult,
    SelectorFactory,
)

# Core strategy pattern interfaces
from .provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)

# Public API exports
__all__: list[str] = [
    # Core interfaces
    "BaseProviderStrategy",
    "ProviderStrategy",
    "ProviderOperation",
    "ProviderResult",
    "ProviderCapabilities",
    "ProviderHealthStatus",
    "ProviderOperationType",
    # Context management
    "ProviderContext",
    "StrategyMetrics",
    # Selection algorithms
    "ProviderSelector",
    "SelectionPolicy",
    "SelectionCriteria",
    "SelectionResult",
    "SelectorFactory",
    "FirstAvailableSelector",
    "RoundRobinSelector",
    "PerformanceBasedSelector",
    "RandomSelector",
    # Advanced strategies
    "CompositeProviderStrategy",
    "CompositionConfig",
    "CompositionMode",
    "AggregationPolicy",
    "StrategyExecutionResult",
    "FallbackProviderStrategy",
    "FallbackConfig",
    "FallbackMode",
    "CircuitState",
    "CircuitBreakerState",
    "LoadBalancingProviderStrategy",
    "LoadBalancingConfig",
    "LoadBalancingAlgorithm",
    "HealthCheckMode",
    "StrategyStats",
]


# Convenience functions
def create_provider_context(logger=None) -> ProviderContext:
    """
    Create a new provider context with default configuration.

    Args:
        logger: Optional logger instance

    Returns:
        Configured ProviderContext instance
    """
    context = ProviderContext(logger=logger)

    # Load strategies from the provider registry
    try:
        from infrastructure.registry.provider_registry import get_provider_registry

        registry = get_provider_registry()

        # Load strategies from registered provider instances (not generic types)
        registered_instances = registry.get_registered_provider_instances()
        for instance_name in registered_instances:
            try:
                # Get the registration to find the provider type
                registration = registry.get_provider_instance_registration(instance_name)
                if registration:
                    # Get the actual provider config from configuration manager
                    from config.manager import get_config_manager

                    config_manager = get_config_manager()
                    provider_config = config_manager.get_provider_config()

                    # Find the matching provider instance config
                    provider_instance_config = None
                    for provider_instance in provider_config.get_active_providers():
                        if provider_instance.name == instance_name:
                            provider_instance_config = provider_instance.config
                            break

                    if provider_instance_config:
                        # Create strategy using the actual instance config
                        strategy = registry.create_strategy_from_instance(
                            instance_name, provider_instance_config
                        )
                        if strategy:
                            # Register strategy with instance name to ensure uniqueness
                            context.register_strategy(strategy, instance_name)
                        if logger:
                            logger.debug(
                                "Loaded strategy for provider instance: %s:%s",
                                registration.type_name,
                                instance_name,
                            )
            except Exception as e:
                if logger:
                    logger.warning(
                        "Failed to load strategy for provider instance %s: %s", instance_name, e
                    )

    except Exception as e:
        if logger:
            logger.error("Failed to load strategies from provider registry: %s", e)

    return context


def create_selector(policy: SelectionPolicy, logger=None) -> ProviderSelector:
    """
    Create a provider selector for the given policy.

    Args:
        policy: Selection policy to use
        logger: Optional logger instance

    Returns:
        ProviderSelector instance
    """
    return SelectorFactory.create_selector(policy, logger)


def create_composite_strategy(
    strategies: list, config: CompositionConfig = None, logger=None
) -> CompositeProviderStrategy:
    """
    Create a composite provider strategy.

    Args:
        strategies: List of provider strategies to compose
        config: Optional composition configuration
        logger: Optional logger instance

    Returns:
        CompositeProviderStrategy instance
    """
    return CompositeProviderStrategy(strategies, config, logger)


def create_fallback_strategy(
    primary: ProviderStrategy,
    fallbacks: list,
    config: FallbackConfig = None,
    logger=None,
) -> FallbackProviderStrategy:
    """
    Create a fallback provider strategy.

    Args:
        primary: Primary provider strategy
        fallbacks: List of fallback strategies
        config: Optional fallback configuration
        logger: Optional logger instance

    Returns:
        FallbackProviderStrategy instance
    """
    return FallbackProviderStrategy(primary, fallbacks, config, logger)


def create_load_balancing_strategy(
    strategies: list,
    weights: dict = None,
    config: LoadBalancingConfig = None,
    logger=None,
) -> LoadBalancingProviderStrategy:
    """
    Create a load balancing provider strategy.

    Args:
        strategies: List of provider strategies to load balance
        weights: Optional weights for each strategy
        config: Optional load balancing configuration
        logger: Optional logger instance

    Returns:
        LoadBalancingProviderStrategy instance
    """
    return LoadBalancingProviderStrategy(strategies, weights, config, logger)
