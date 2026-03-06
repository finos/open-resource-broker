"""Provider registration functions."""


def register_all_provider_types() -> None:
    """Register all available provider types."""
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()

    # Register AWS provider
    from orb.providers.aws.registration import register_aws_provider

    register_aws_provider(registry)

    # Future providers would be added here
    # register_provider1_provider(registry)
    # register_provider2_provider(registry)


def register_fallback_provider(
    primary_strategy, fallback_strategies, config=None, logger=None
) -> None:
    """Construct and register a FallbackProviderStrategy with the provider registry.

    The strategy is constructed here (not in the DI container) and registered
    directly with the registry so it is used when no provider config matches.

    Args:
        primary_strategy: Primary ProviderStrategy instance.
        fallback_strategies: List of fallback ProviderStrategy instances.
        config: Optional FallbackConfig.
        logger: Optional LoggingPort.
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.base.strategy.fallback_strategy import FallbackProviderStrategy
    from orb.providers.registry import get_provider_registry

    effective_logger = logger or LoggingAdapter()
    strategy = FallbackProviderStrategy(
        logger=effective_logger,
        primary_strategy=primary_strategy,
        fallback_strategies=fallback_strategies,
        config=config,
    )
    registry = get_provider_registry()
    registry.register_fallback_strategy(strategy)
