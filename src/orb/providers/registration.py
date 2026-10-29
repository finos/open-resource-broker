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
    failures: dict[str, Exception] | None = None,
) -> list[str]:
    """Discover and load third-party provider plugins.

    Walks ``importlib.metadata.entry_points(group=entry_point_group)`` and
    invokes each entry point's loaded callable.  The contract for the
    callable is documented in
    ``docs/root/providers/k8s/plugin-authoring.md``:

    * Zero-argument callable.
    * Returns ``None``.
    * Should not raise — plugins should log and swallow internal errors.

    This function never raises for a broken plugin: the plugin is skipped so
    the remaining plugins still load. By default the failure is logged at
    ERROR. A caller that decides what a failure means (see
    :func:`register_all_providers`, which fails startup for a plugin the
    configuration names) passes ``failures``; each failed plugin is then
    recorded there, keyed by entry-point name, instead of being logged, and
    the caller reports it.

    Args:
        entry_point_group: Entry-point group to query.  Defaults to
            ``orb.providers``; tests can pass a custom group to drive
            simulated entry points.
        failures: Optional mapping that receives the exception of every
            plugin that failed to load, was not callable, or raised while
            registering.

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

    def _failed(name: str, exc: Exception, message: str) -> None:
        if failures is not None:
            failures[name] = exc
        else:
            _logger.error(message, name, exc, exc_info=exc)

    for entry_point in entry_points:
        name = getattr(entry_point, "name", "<unknown>")
        try:
            callable_ = entry_point.load()
        except Exception as exc:
            _failed(name, exc, "Failed to load provider plugin entry point %r: %s")
            continue
        if not callable(callable_):
            _failed(
                name,
                TypeError("entry point resolved to a non-callable target"),
                "Provider plugin entry point %r is invalid: %s",
            )
            continue
        try:
            callable_()
        except Exception as exc:
            _failed(name, exc, "Provider plugin %r raised during registration: %s")
            continue
        loaded.append(name)
        _logger.info("Loaded provider plugin %r", name)
    return loaded


def _configured_provider_types(container: DIContainer | None) -> frozenset[str] | None:
    """Return the provider types named in the active configuration.

    Returns ``None`` when the configuration cannot be read; callers then treat
    every provider as optional so a configuration problem is reported by the
    configuration layer rather than masked here. When there is no configuration
    at all (no config file and no in-memory config) nothing is configured, so no
    provider is required: the built-in default provider block must not make a
    fresh install fail for a provider whose extra it never asked for.
    """
    try:
        from orb.config.managers.configuration_manager import ConfigurationManager

        manager = (
            container.get(ConfigurationManager)
            if container is not None and container.has(ConfigurationManager)
            else ConfigurationManager()
        )
        if manager.get_source_config_file() is None and not manager.has_in_memory_config:
            return frozenset()
        provider_config = manager.get_provider_config()
        if not provider_config:
            return frozenset()
        return frozenset(str(p.type) for p in provider_config.get_active_providers())
    except Exception as exc:
        _logger.warning(
            "Could not read configured provider types; treating all providers as optional: %s",
            exc,
            exc_info=True,
        )
        return None


def _provider_load_failure(
    name: str,
    exc: Exception,
    container: DIContainer | None,
    configured: dict[str, frozenset[str] | None],
    *,
    optional_dependency: bool,
) -> None:
    """Fail startup for a configured provider; log and continue for any other.

    ``configured`` memoises the configuration lookup so it runs at most once,
    and only after a provider has actually failed to load.
    """
    if "types" not in configured:
        configured["types"] = _configured_provider_types(container)
    configured_types = configured["types"]
    if configured_types is not None and name in configured_types:
        hint = (
            f" Install the matching extra: pip install 'orb-py[{name}]'."
            if optional_dependency
            else ""
        )
        raise RuntimeError(
            f"Configured provider '{name}' could not be loaded: {exc}.{hint}"
        ) from exc
    if optional_dependency:
        _logger.warning(
            "Provider '%s' is not available (optional dependency missing?): %s",
            name,
            exc,
            exc_info=exc,
        )
    else:
        _logger.error("Provider '%s' failed to register: %s", name, exc, exc_info=exc)


def register_all_providers(
    container: DIContainer | None = None,
    config_container: DIContainer | None = None,
) -> None:
    """Register all providers listed in ``_REGISTERED_PROVIDERS``.

    For each provider name ``n`` this function:
    1. Imports ``orb.providers.<n>.registration`` (an ImportError for a
       provider that is not configured is logged as a warning and skipped, so
       optional provider extras are handled gracefully; a configured provider
       that cannot be loaded fails startup).
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
    plugin_failures: dict[str, Exception] = {}
    discover_provider_plugins(failures=plugin_failures)

    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    configured: dict[str, frozenset[str] | None] = {}
    config_source = container if container is not None else config_container

    # A plugin whose entry point fails is reported once, here: it fails
    # startup when the configuration names it (the entry-point name is the
    # provider type) and is logged otherwise. Every failure is reported before
    # any provider is registered, and one bad plugin never blocks another.
    for name, exc in plugin_failures.items():
        _provider_load_failure(
            name,
            exc,
            config_source,
            configured,
            optional_dependency=isinstance(exc, ImportError),
        )

    for name in list(_REGISTERED_PROVIDERS):
        if name in plugin_failures:
            continue
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
        except ImportError as exc:
            _provider_load_failure(name, exc, config_source, configured, optional_dependency=True)
            continue

        try:
            # Registry-level registration
            register_fn = getattr(mod, f"register_{name}_provider", None)
            if register_fn is not None:
                register_fn(registry)

            # DI-level initialisation (only when a container is supplied)
            if container is not None:
                _initialize_provider_module(mod, name, container)
        except Exception as exc:
            _provider_load_failure(
                name,
                exc,
                config_source,
                configured,
                optional_dependency=isinstance(exc, ImportError),
            )


def _initialize_provider_module(mod: object, name: str, container: DIContainer) -> None:
    """Run a provider module's ``initialize_<name>_provider`` with DI-resolved arguments."""
    init_fn = getattr(mod, f"initialize_{name}_provider", None)
    if init_fn is None:
        return

    # Resolve template_factory and logger from the container so each provider's
    # initialize function receives the correct typed arguments instead of the
    # container object.
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
# Bootstrap aliases – kept for backward compatibility with existing callers.
# ---------------------------------------------------------------------------


def register_all_provider_types(container: DIContainer | None = None) -> None:
    """Register all available provider types.

    Backward-compatible alias that delegates to ``register_all_providers()``
    so existing callers continue to work without modification.

    *container* is used only to find the active configuration, so a configured
    provider that cannot load fails startup; no per-provider DI wiring is done.
    """
    if container is None:
        register_all_providers(container=None)
    else:
        register_all_providers(container=None, config_container=container)


def register_all_provider_cli_specs() -> None:
    """Register CLI argument specs for all available providers.

    Lightweight bootstrap that only registers CLI specs so that
    ``build_parser`` can call it before any application context exists.
    Retained so that ``cli/args.py`` and other early-bootstrap callers
    continue to work.
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
            except Exception:  # noqa: S112  # other classes in the module may not construct with zero args; keep scanning
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
    call it before a full application context has been set up.  Retained so
    that ``config/loader.py`` and other early-bootstrap callers continue to
    work.
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
            except Exception:  # noqa: S112  # other classes in the module may not construct with zero args; keep scanning
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
