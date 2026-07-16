"""Provider registration functions.

To add a new provider, declare an entry-point in your package's
``pyproject.toml`` (or ``setup.cfg``)::

    [project.entry-points."orb.providers"]
    myprovider = "myprovider.registration:register_myprovider_plugin"

No edits to any shared ORB file are needed.  The entry-point callable must
be zero-argument and idempotent; it is invoked by
:func:`discover_provider_plugins` at startup.

Third-party plugins are discovered via the ``orb.providers`` entry-point
group (see ``docs/root/providers/k8s/plugin-authoring.md``) and
are loaded by :func:`discover_provider_plugins`.  The built-in aws and k8s
providers are also wired via entry-points so all provider bootstrapping
follows the same path.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orb.infrastructure.di.container import DIContainer

# ---------------------------------------------------------------------------
# Live provider list — populated by entry-point discovery at startup.
# ---------------------------------------------------------------------------
_REGISTERED_PROVIDERS: list[str] = []
"""Providers discovered via the ``orb.providers`` entry-point group.

Do NOT edit this list to add new providers.  Declare an entry-point in your
provider package's ``pyproject.toml`` pointing to a zero-argument
``register_<name>_plugin()`` callable instead.  The list is populated at
startup by :func:`discover_provider_plugins` and is then used by the
bootstrap loops in :func:`register_all_providers`,
:func:`register_all_provider_cli_specs`, and
:func:`register_all_defaults_loaders`.
"""

# Entry-point group used by third-party plugins to register provider
# extensions.  See ``discover_provider_plugins`` and
# ``docs/root/providers/k8s/plugin-authoring.md``.
_PROVIDER_ENTRY_POINT_GROUP = "orb.providers"

_logger = logging.getLogger(__name__)


def discover_provider_plugins(
    entry_point_group: str = _PROVIDER_ENTRY_POINT_GROUP,
) -> list[str]:
    """Discover and load third-party provider plugins.

    Walks ``importlib.metadata.entry_points(group=entry_point_group)`` and
    invokes each entry point's loaded callable.  The contract for the
    callable is documented in
    ``docs/root/providers/k8s/plugin-authoring.md``:

    * Zero-argument callable.
    * Returns ``None``.
    * Must not raise — plugins should log and swallow internal errors.

    Failure modes are tolerant by design: a broken plugin is logged at
    ERROR and skipped so ORB still boots with its built-in providers.

    Args:
        entry_point_group: Entry-point group to query.  Defaults to
            ``orb.providers``; tests can pass a custom group to drive
            simulated entry points.

    Returns:
        The names of plugins that were loaded successfully (in discovery
        order).
    """
    loaded: list[str] = []
    try:
        entry_points = importlib.metadata.entry_points(group=entry_point_group)
    except TypeError:
        # Python <3.10 selectable-entry-points API differences.  ORB's
        # supported Python range is 3.10+, so this branch is defensive.
        try:
            entry_points = importlib.metadata.entry_points().get(entry_point_group, ())  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover — extremely defensive
            _logger.error("Failed to query entry points for group %r: %s", entry_point_group, exc)
            return loaded

    for entry_point in entry_points:
        name = getattr(entry_point, "name", "<unknown>")
        try:
            callable_ = entry_point.load()
        except Exception as exc:
            _logger.error(
                "Failed to load provider plugin entry point %r: %s",
                name,
                exc,
                exc_info=True,
            )
            continue
        if not callable(callable_):
            _logger.error("Provider plugin entry point %r resolved to a non-callable target", name)
            continue
        try:
            callable_()
        except Exception as exc:
            _logger.error(
                "Provider plugin %r raised during registration: %s",
                name,
                exc,
                exc_info=True,
            )
            continue
        loaded.append(name)
        _logger.info("Loaded provider plugin %r", name)
    return loaded


def register_all_providers(container: DIContainer | None = None) -> None:
    """Register all providers listed in ``_REGISTERED_PROVIDERS``.

    For each provider name ``n`` this function:
    1. Imports ``orb.providers.<n>.registration`` (skips silently on ImportError
       so that optional provider extras are handled gracefully).
    2. Calls ``register_<n>_provider(registry)`` to register the strategy and
       supporting factories with the global provider registry.
    3. If *container* is given, resolves ``template_factory`` and ``logger``
       from the container and calls
       ``initialize_<n>_provider(template_factory=…, logger=…)``
       when that function exists in the module.

    Args:
        container: Optional DI container.  When supplied, per-provider DI
            wiring is performed in the same call; when omitted only the
            registry-level registration is performed.
    """
    # Discover and load provider plugins FIRST so that entry-point-declared
    # providers are appended to ``_REGISTERED_PROVIDERS`` before the loop
    # below iterates over them.
    discover_provider_plugins()

    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()

    for name in _REGISTERED_PROVIDERS:
        module_path = f"orb.providers.{name}.registration"
        try:
            spec = importlib.util.find_spec(module_path)
        except (ImportError, ValueError):
            # find_spec can raise ModuleNotFoundError (subclass of ImportError)
            # when a parent package exists as a namespace but the child does not.
            # This can happen with test-injected provider names.
            continue
        if spec is None:
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
                # Resolve template_factory and logger from the container so
                # each provider's initialize function receives the correct
                # typed arguments instead of the container object.
                from orb.domain.base.ports.logging_port import LoggingPort
                from orb.domain.template.factory import TemplateFactory

                try:
                    template_factory = container.get(TemplateFactory)
                except Exception:
                    template_factory = None
                try:
                    logger_port = container.get(LoggingPort)
                except Exception:
                    logger_port = None

                init_fn(template_factory=template_factory, logger=logger_port)


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
    # Ensure entry-point providers are registered before iterating the list;
    # this function may be called before full bootstrap (e.g. from cli/args.py).
    discover_provider_plugins()

    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
    from orb.providers.base.provider_cli_spec_port import ProviderCLISpecPort

    # Provider-agnostic discovery: import ``orb.providers.<name>.cli.<name>_cli_spec``
    # and pick the first class defined in that module whose runtime instance
    # satisfies ``ProviderCLISpecPort``.  Each provider owns its own class
    # name; this loop stays generic.
    for name in _REGISTERED_PROVIDERS:
        if CLISpecRegistry.get_or_none(name) is not None:
            continue
        module_path = f"orb.providers.{name}.cli.{name}_cli_spec"
        if importlib.util.find_spec(module_path) is None:
            continue
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue  # provider extra not installed
        spec_instance = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if not isinstance(attr, type):
                continue
            if attr.__module__ != mod.__name__:
                continue
            try:
                instance = attr()
            except Exception:
                continue
            if isinstance(instance, ProviderCLISpecPort):
                spec_instance = instance
                break
        if spec_instance is not None:
            CLISpecRegistry.register(name, spec_instance)


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
    # Ensure entry-point providers are registered before iterating the list;
    # this function may be called before full bootstrap (e.g. from config/loader.py).
    discover_provider_plugins()

    # Provider-agnostic discovery: each provider's ``defaults_loader`` module
    # is expected to export exactly one class whose runtime instance satisfies
    # ``ProviderDefaultsLoaderPort``.  We import the module from the well-known
    # path ``orb.providers.<name>.defaults_loader`` and pick the first such
    # class.  Provider-specific class names live inside the provider's own
    # folder; the loop here stays generic.
    from orb.domain.base.ports.provider_defaults_loader_port import (
        ProviderDefaultsLoaderPort,
    )
    from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry

    for name in _REGISTERED_PROVIDERS:
        if DefaultsLoaderRegistry.get_or_none(name) is not None:
            continue
        module_path = f"orb.providers.{name}.defaults_loader"
        if importlib.util.find_spec(module_path) is None:
            continue
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue  # provider extra not installed
        loader_instance = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if not isinstance(attr, type):
                continue
            if attr.__module__ != mod.__name__:
                continue
            try:
                instance = attr()
            except Exception:
                continue
            if isinstance(instance, ProviderDefaultsLoaderPort):
                loader_instance = instance
                break
        if loader_instance is not None:
            DefaultsLoaderRegistry.register(name, loader_instance)


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
        metrics: Optional ProviderMetricsPort for emitting fallback/circuit-breaker metrics.
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
