"""Build CLI handler classes in tests the way the CLI wiring does."""

from __future__ import annotations

import inspect
from typing import Any, TypeVar
from unittest.mock import MagicMock

T = TypeVar("T")


class _RecordingContainer:
    """Collects the factories that the group registrars would register."""

    def __init__(self) -> None:
        self.factories: dict[type, Any] = {}

    def is_registered(self, cls: type) -> bool:
        return cls in self.factories

    def register_singleton(self, cls: type, factory: Any) -> None:
        self.factories[cls] = factory


_factories: dict[type, Any] | None = None


def _wiring_factories() -> dict[type, Any]:
    global _factories
    if _factories is None:
        from orb.cli import handler_wiring

        recorder = _RecordingContainer()
        for name, fn in inspect.getmembers(handler_wiring, inspect.isfunction):
            if name.startswith("register_") and name.endswith("_handlers"):
                fn(recorder)
        _factories = recorder.factories
    return _factories


def build_handler(cls: type[T], container: Any) -> T:
    """Build ``cls`` with the real wiring factory, resolving collaborators from ``container``."""
    return _wiring_factories()[cls](container)


def make_handler(cls: type[T], **collaborators: Any) -> T:
    """Build ``cls`` from keyword collaborators; unspecified parameters get a ``MagicMock``."""
    params = [p for p in inspect.signature(cls.__init__).parameters if p != "self"]
    unknown = set(collaborators) - set(params)
    assert not unknown, f"{cls.__name__} has no parameters {sorted(unknown)}"
    return cls(**{p: collaborators.get(p, MagicMock()) for p in params})  # type: ignore[call-arg]


def handler_method(cls: type, method: str) -> Any:
    """Return ``async def (args)`` that runs ``cls.method`` on a handler built from ``args._container``.

    The handler is built by the real wiring factory, so the test exercises the same
    constructor wiring as the CLI while still supplying its own container.
    """

    async def call(args: Any) -> Any:
        return await getattr(build_handler(cls, args._container), method)(args)

    call.__name__ = f"{cls.__name__}.{method}"
    return call
