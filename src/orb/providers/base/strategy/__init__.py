"""Provider Strategy Pattern - Base strategy interfaces and implementations.

This package implements the Strategy pattern for provider operations,
enabling runtime selection and switching of provider strategies while
maintaining clean separation of concerns and SOLID principles compliance.

Key Components:
- ProviderStrategy: Abstract base class for all provider strategies
- ProviderSelector: Algorithms for selecting optimal strategies
- CompositeProviderStrategy: Multi-provider composition and orchestration
- FallbackProviderStrategy: Resilience and failover capabilities
- LoadBalancingProviderStrategy: Performance optimization and load distribution
- Value Objects: Operation, Result, Capabilities, Health status

Usage Example:
    from providers.base.strategy import (
        ProviderOperation,
        ProviderOperationType,
        SelectorFactory,
        SelectionPolicy,
        CompositeProviderStrategy,
        FallbackProviderStrategy,
        LoadBalancingProviderStrategy
    )
    from providers.registry import get_provider_registry

    # Get registry for strategy execution
    registry = get_provider_registry()

    # Or use advanced strategies
    composite = CompositeProviderStrategy([aws_strategy, provider1_strategy])
    fallback = FallbackProviderStrategy(aws_strategy, [provider1_strategy])
    load_balancer = LoadBalancingProviderStrategy([aws_strategy, provider1_strategy])

    # Execute operations via registry
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={'count': 5, 'template_id': 'web-server'}
    )

    result = await registry.execute_operation("aws", operation)
"""

# Advanced strategy patterns
from typing import Optional

from orb.domain.base.ports import LoggingPort

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

# Strategy context and management - Using Provider Registry directly
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
    "AggregationPolicy",
    # Core interfaces
    "BaseProviderStrategy",
    "CircuitBreakerState",
    "CircuitState",
    # Advanced strategies
    "CompositeProviderStrategy",
    "CompositionConfig",
    "CompositionMode",
    "FallbackConfig",
    "FallbackMode",
    "FallbackProviderStrategy",
    "FirstAvailableSelector",
    "HealthCheckMode",
    "LoadBalancingAlgorithm",
    "LoadBalancingConfig",
    "LoadBalancingProviderStrategy",
    "PerformanceBasedSelector",
    "ProviderCapabilities",
    # Context management - Using Provider Registry directly
    "ProviderHealthStatus",
    "ProviderOperation",
    "ProviderOperationType",
    "ProviderResult",
    # Selection algorithms
    "ProviderSelector",
    "ProviderStrategy",
    "RandomSelector",
    "RoundRobinSelector",
    "SelectionCriteria",
    "SelectionPolicy",
    "SelectionResult",
    "SelectorFactory",
    "StrategyExecutionResult",
    "StrategyStats",
]


# Convenience functions - Using Provider Registry directly


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
    strategies: list,
    config: Optional[CompositionConfig] = None,
    logger: Optional[LoggingPort] = None,
) -> CompositeProviderStrategy:
    """
    Create a composite provider strategy.

    Args:
        strategies: List of provider strategies to compose
        config: Optional composition configuration
        logger: Logger instance

    Returns:
        CompositeProviderStrategy instance
    """
    if logger is None:
        raise ValueError("logger is required")
    return CompositeProviderStrategy(logger, strategies, config)


def create_fallback_strategy(
    primary: ProviderStrategy,
    fallbacks: list,
    config: Optional[FallbackConfig] = None,
    logger: Optional[LoggingPort] = None,
) -> FallbackProviderStrategy:
    """
    Create a fallback provider strategy.

    Args:
        primary: Primary provider strategy
        fallbacks: List of fallback strategies
        config: Optional fallback configuration
        logger: Logger instance

    Returns:
        FallbackProviderStrategy instance
    """
    if logger is None:
        raise ValueError("logger is required")
    return FallbackProviderStrategy(logger, primary, fallbacks, config)


def create_load_balancing_strategy(
    strategies: list,
    weights: Optional[dict] = None,
    config: Optional[LoadBalancingConfig] = None,
    logger: Optional[LoggingPort] = None,
) -> LoadBalancingProviderStrategy:
    """
    Create a load balancing provider strategy.

    Args:
        strategies: List of provider strategies to load balance
        weights: Optional weights for each strategy
        config: Optional load balancing configuration
        logger: Logger instance

    Returns:
        LoadBalancingProviderStrategy instance
    """
    if logger is None:
        raise ValueError("logger is required")
    return LoadBalancingProviderStrategy(logger, strategies, weights, config)
