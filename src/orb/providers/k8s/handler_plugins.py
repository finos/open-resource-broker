"""Process-wide registry of plugin handlers for the Kubernetes provider.

A third-party plugin calls :func:`register_k8s_handler` from its
``orb.providers`` entry point to add a ``provider_api`` to the Kubernetes
provider.  Everything that needs the list of supported APIs reads it from
:func:`supported_provider_apis` instead of keeping its own copy:

* every :class:`K8sProviderStrategy` seeds its handler factories from here;
* the template validators and the template adapter accept the registered
  names;
* the defaults loader adds a ``handlers`` entry for each one, so the
  generic template validation knows the provider supports it;
* the orphan garbage collector also sweeps the custom resources a handler
  declares, with the same ownership rules it applies to pods.

This module imports nothing from the strategy or the handlers, so the
validators can use it without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from orb.providers.k8s.value_objects import KubernetesProviderApi

_BUILT_IN_APIS: tuple[str, ...] = tuple(api.value for api in KubernetesProviderApi)


@dataclass(frozen=True)
class K8sCustomResource:
    """A namespaced custom resource kind that a plugin handler creates.

    Declared through ``register_k8s_handler(custom_resources=...)`` so the
    orphan garbage collector can find objects of this kind that ORB lost
    track of.  Only objects carrying ``<label_prefix>/managed=true``, this
    installation's ``<label_prefix>/orb-instance`` and a
    ``<label_prefix>/request-id`` label are ever considered.
    """

    group: str
    version: str
    plural: str

    def __post_init__(self) -> None:
        fields = {"group": self.group, "version": self.version, "plural": self.plural}
        for field_name, value in fields.items():
            if not value:
                raise ValueError(f"K8sCustomResource.{field_name} must be a non-empty string")

    def __str__(self) -> str:
        return f"{self.plural}.{self.group}/{self.version}"


@dataclass(frozen=True)
class _PluginEntry:
    factory: Callable[..., Any]
    flags: dict[str, bool]
    custom_resources: tuple[K8sCustomResource, ...]


# provider_api -> registration, in registration order.
_PLUGIN_HANDLERS: dict[str, _PluginEntry] = {}


def register_k8s_handler(
    provider_api: str,
    handler_class: Callable[..., Any],
    *,
    supports_spot: bool = False,
    supports_ondemand: bool = True,
    custom_resources: Iterable[K8sCustomResource] = (),
) -> None:
    """Register a plugin handler for ``provider_api`` on the Kubernetes provider.

    Args:
        provider_api: The template ``provider_api`` value the handler serves.
        handler_class: A callable returning a handler instance, usually a
            subclass of ``K8sHandlerBase``.  It is constructed with the same
            keyword arguments as the built-in handlers.
        supports_spot: Capability flag reported in the provider defaults.
        supports_ondemand: Capability flag reported in the provider defaults.
        custom_resources: The custom resource kinds the handler creates.  The
            orphan garbage collector sweeps them alongside pods, so the
            handler must stamp ``managed``, ``request-id`` and (when
            ``orb_instance_id`` is set) ``orb-instance`` labels on every
            object it creates.

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
    if existing is not None and existing.factory is not handler_class:
        raise ValueError(
            f"provider_api {provider_api!r} is already registered to a "
            f"different handler class ({existing.factory!r}); refusing to overwrite."
        )
    resources = tuple(custom_resources)
    for resource in resources:
        if not isinstance(resource, K8sCustomResource):
            raise TypeError(f"custom_resources entries must be K8sCustomResource, got {resource!r}")
    _PLUGIN_HANDLERS[provider_api] = _PluginEntry(
        factory=handler_class,
        flags={"supports_spot": supports_spot, "supports_ondemand": supports_ondemand},
        custom_resources=resources,
    )


def unregister_k8s_handler(provider_api: str) -> None:
    """Remove a registered plugin handler (for tests and plugin reloads)."""
    _PLUGIN_HANDLERS.pop(provider_api, None)


def registered_k8s_handlers() -> dict[str, Callable[..., Any]]:
    """Return a copy of the registered plugin handler factories."""
    return {api: entry.factory for api, entry in _PLUGIN_HANDLERS.items()}


def registered_custom_resources() -> list[K8sCustomResource]:
    """Return every custom resource kind declared by a plugin, without duplicates."""
    seen: dict[K8sCustomResource, None] = {}
    for entry in _PLUGIN_HANDLERS.values():
        for resource in entry.custom_resources:
            seen.setdefault(resource, None)
    return list(seen)


def supported_provider_apis() -> list[str]:
    """Return the built-in APIs followed by the registered plugin APIs."""
    return [*_BUILT_IN_APIS, *_PLUGIN_HANDLERS]


def plugin_handler_defaults() -> dict[str, dict[str, Any]]:
    """Return a provider-defaults ``handlers`` entry for each registered plugin."""
    return {
        # getattr: a handler factory may be any callable, not only a class.
        api: {
            "handler_class": getattr(entry.factory, "__name__", repr(entry.factory)),
            **entry.flags,
        }
        for api, entry in _PLUGIN_HANDLERS.items()
    }


__all__: list[str] = [
    "K8sCustomResource",
    "plugin_handler_defaults",
    "register_k8s_handler",
    "registered_custom_resources",
    "registered_k8s_handlers",
    "supported_provider_apis",
    "unregister_k8s_handler",
]
