"""Process-wide registry of plugin handlers for the Kubernetes provider.

A third-party plugin calls :func:`register_k8s_handler` from its
``orb.providers`` entry point to add a ``provider_api`` to the Kubernetes
provider.  Everything that needs the list of supported APIs reads it from
:func:`supported_provider_apis` instead of keeping its own copy:

* every :class:`K8sProviderStrategy` seeds its handler factories from here;
* the template validators and the template adapter accept the registered
  names;
* the defaults loader adds a ``handlers`` entry for each one, so the
  generic template validation knows the provider supports it.

This module imports nothing from the strategy or the handlers, so the
validators can use it without an import cycle.
"""

from __future__ import annotations

from typing import Any, Callable

from orb.providers.k8s.value_objects import KubernetesProviderApi

_BUILT_IN_APIS: tuple[str, ...] = tuple(api.value for api in KubernetesProviderApi)

# provider_api -> (handler factory, capability flags), in registration order.
_PLUGIN_HANDLERS: dict[str, tuple[Callable[..., Any], dict[str, bool]]] = {}


def register_k8s_handler(
    provider_api: str,
    handler_class: Callable[..., Any],
    *,
    supports_spot: bool = False,
    supports_ondemand: bool = True,
) -> None:
    """Register a plugin handler for ``provider_api`` on the Kubernetes provider.

    Args:
        provider_api: The template ``provider_api`` value the handler serves.
        handler_class: A callable returning a handler instance, usually a
            subclass of ``K8sHandlerBase``.  It is constructed with the same
            keyword arguments as the built-in handlers.
        supports_spot: Capability flag reported in the provider defaults.
        supports_ondemand: Capability flag reported in the provider defaults.

    Raises:
        ValueError: If ``provider_api`` is empty, names a built-in API, or is
            already registered to a different handler.  Registering the same
            handler again is allowed, so a plugin reload does not fail.
    """
    if not provider_api:
        raise ValueError("provider_api must be a non-empty string")
    if provider_api in _BUILT_IN_APIS:
        raise ValueError(f"provider_api {provider_api!r} is a built-in Kubernetes API")
    existing = _PLUGIN_HANDLERS.get(provider_api)
    if existing is not None and existing[0] is not handler_class:
        raise ValueError(
            f"provider_api {provider_api!r} is already registered to a "
            f"different handler class ({existing[0]!r}); refusing to overwrite."
        )
    _PLUGIN_HANDLERS[provider_api] = (
        handler_class,
        {"supports_spot": supports_spot, "supports_ondemand": supports_ondemand},
    )


def unregister_k8s_handler(provider_api: str) -> None:
    """Remove a registered plugin handler (for tests and plugin reloads)."""
    _PLUGIN_HANDLERS.pop(provider_api, None)


def registered_k8s_handlers() -> dict[str, Callable[..., Any]]:
    """Return a copy of the registered plugin handler factories."""
    return {api: entry[0] for api, entry in _PLUGIN_HANDLERS.items()}


def supported_provider_apis() -> list[str]:
    """Return the built-in APIs followed by the registered plugin APIs."""
    return [*_BUILT_IN_APIS, *_PLUGIN_HANDLERS]


def plugin_handler_defaults() -> dict[str, dict[str, Any]]:
    """Return a provider-defaults ``handlers`` entry for each registered plugin."""
    return {
        # getattr: a handler factory may be any callable, not only a class.
        api: {"handler_class": getattr(factory, "__name__", repr(factory)), **flags}
        for api, (factory, flags) in _PLUGIN_HANDLERS.items()
    }


__all__: list[str] = [
    "plugin_handler_defaults",
    "register_k8s_handler",
    "registered_k8s_handlers",
    "supported_provider_apis",
    "unregister_k8s_handler",
]
