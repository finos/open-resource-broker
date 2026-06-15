"""Provider registration functions.

To add a new provider:
  1. Add its name to ``_REGISTERED_PROVIDERS`` below (one line).
  2. Create ``src/orb/providers/<name>/registration.py`` that exposes:
     - ``register_<name>_provider(registry)``   – registers strategy + factories
     - ``initialize_<name>_provider(container)`` – wires DI services (optional)

That is the only edit outside the new provider package.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orb.infrastructure.di.container import DIContainer

# ---------------------------------------------------------------------------
# Central provider list – the single line to edit when adding a new provider.
# ---------------------------------------------------------------------------
_REGISTERED_PROVIDERS: list[str] = ["aws"]


def register_all_providers(container: "DIContainer | None" = None) -> None:
    """Register all providers listed in ``_REGISTERED_PROVIDERS``.

    For each provider name ``n`` this function:
    1. Imports ``orb.providers.<n>.registration`` (skips silently on ImportError
       so that optional provider extras are handled gracefully).
    2. Calls ``register_<n>_provider(registry)`` to register the strategy and
       supporting factories with the global provider registry.
    3. If *container* is given, calls ``initialize_<n>_provider(container)``
       when that function exists in the module.

    Args:
        container: Optional DI container.  When supplied, per-provider DI
            wiring is performed in the same call; when omitted only the
            registry-level registration is performed.
    """
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()

    for name in _REGISTERED_PROVIDERS:
        module_path = f"orb.providers.{name}.registration"
        if importlib.util.find_spec(module_path) is None:
            continue
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue

        # Registry-level registration
        register_fn = getattr(mod, f"register_{name}_provider", None)
        if register_fn is not None:
            register_fn(registry)

        # DI-level initialisation (only when a container is supplied)
        if container is not None:
            init_fn = getattr(mod, f"initialize_{name}_provider", None)
            if init_fn is not None:
                init_fn(container)


# ---------------------------------------------------------------------------
# Deprecated aliases – kept for backward compatibility with existing callers.
# ---------------------------------------------------------------------------

def register_all_provider_types() -> None:
    """Register all available provider types.

    Deprecated: use ``register_all_providers()`` instead.  Kept as a
    backward-compatible alias so existing callers continue to work without
    modification.
    """
    register_all_providers(container=None)


def register_all_provider_cli_specs() -> None:
    """Register CLI argument specs for all available providers.

    Lightweight bootstrap that only registers CLI specs so that
    ``build_parser`` can call it before any application context exists.

    Deprecated: ``register_all_providers()`` now handles CLI spec registration
    as part of ``initialize_<name>_provider``.  This alias is retained so that
    ``cli/args.py`` and other early-bootstrap callers continue to work.
    """
    from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
    from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec

    if CLISpecRegistry.get("aws") is None:
        CLISpecRegistry.register("aws", AWSCLISpec())


def register_all_defaults_loaders() -> None:
    """Register defaults loaders for all available providers.

    Lightweight bootstrap that only registers ``ProviderDefaultsLoaderPort``
    implementations so that ``ConfigurationLoader._load_strategy_defaults`` can
    call it before a full application context has been set up.

    Deprecated: ``register_all_providers()`` now handles defaults-loader
    registration as part of ``initialize_<name>_provider``.  This alias is
    retained so that ``config/loader.py`` and other early-bootstrap callers
    continue to work.
    """
    from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry

    if DefaultsLoaderRegistry.get("aws") is None:
        from orb.providers.aws.defaults_loader import AWSDefaultsLoader

        DefaultsLoaderRegistry.register("aws", AWSDefaultsLoader())


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
