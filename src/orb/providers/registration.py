"""Provider registration functions."""


def register_all_provider_cli_specs() -> None:
    """Register CLI argument specs for all available providers.

    This is a lightweight bootstrap that only registers CLI specs (no full
    provider strategy initialisation) so that ``build_parser`` can call it
    before any application context exists.
    """
    from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
    from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec

    if CLISpecRegistry.get("aws") is None:
        CLISpecRegistry.register("aws", AWSCLISpec())

    # Future providers register their CLI specs here:
    # from orb.providers.oci.cli.oci_cli_spec import OCICLISpec
    # if CLISpecRegistry.get("oci") is None:
    #     CLISpecRegistry.register("oci", OCICLISpec())


def register_all_defaults_loaders() -> None:
    """Register defaults loaders for all available providers.

    Lightweight bootstrap that only registers ``ProviderDefaultsLoaderPort``
    implementations so that ``ConfigurationLoader._load_strategy_defaults`` can
    call it before a full application context (DI container / ``initialize_aws_provider``)
    has been set up.
    """
    from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry

    if DefaultsLoaderRegistry.get("aws") is None:
        from orb.providers.aws.defaults_loader import AWSDefaultsLoader

        DefaultsLoaderRegistry.register("aws", AWSDefaultsLoader())

    # Future providers register their defaults loaders here:
    # from orb.providers.oci.defaults_loader import OCIDefaultsLoader
    # if DefaultsLoaderRegistry.get("oci") is None:
    #     DefaultsLoaderRegistry.register("oci", OCIDefaultsLoader())


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
    primary_strategy, fallback_strategies, config=None, logger=None, metrics=None
) -> None:
    """Construct and register a FallbackProviderStrategy with the provider registry.

    The strategy is constructed here (not in the DI container) and registered
    directly with the registry so it is used when no provider config matches.

    Args:
        primary_strategy: Primary ProviderStrategy instance.
        fallback_strategies: List of fallback ProviderStrategy instances.
        config: Optional FallbackConfig.
        logger: Optional LoggingPort.
        metrics: Optional MetricsCollector for emitting fallback/circuit-breaker metrics.
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
        metrics=metrics,
    )
    registry = get_provider_registry()
    registry.register_fallback_strategy(strategy)
