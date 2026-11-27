"""Registration of CLI handler classes in the dependency injection container.

Each ``register_<group>_handlers`` function registers the handler classes of one
command group. They are called lazily by :func:`orb.cli.registry.bind` on the
current container, so a command only pays for the group it runs, and each is
safe to call repeatedly: a class that is already registered is left alone.

Collaborators are resolved when a handler class is first built. A collaborator
that the handler only reaches inside a guarded block is passed as a provider
(``Callable[[], T]``) instead, so a failure to resolve it surfaces at the same
place it always did.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


def _register(container: Any, cls: type[T], factory: Callable[[Any], T]) -> None:
    """Register ``cls`` as a singleton built by ``factory`` unless already registered."""
    if not container.is_registered(cls):
        container.register_singleton(cls, factory)
