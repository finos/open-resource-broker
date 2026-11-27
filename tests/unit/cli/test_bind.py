"""Tests for ``orb.cli.registry.bind``: dispatch-time resolution of handler classes."""

from __future__ import annotations

import argparse
import inspect
from unittest.mock import patch

import pytest

from orb.cli import registry
from orb.domain.base.exceptions import DomainException
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.infrastructure.error.exception_handler import get_exception_handler

pytestmark = [pytest.mark.unit, pytest.mark.cli]


class _Dependency:
    pass


class _Handlers:
    def __init__(self, dependency: _Dependency) -> None:
        self.dependency = dependency

    async def ok(self, args: argparse.Namespace) -> dict:
        return {"dependency": self.dependency, "args": args}

    @handle_interface_exceptions(context="boom", interface_type="cli")
    async def boom(self, args: argparse.Namespace) -> dict:
        raise DomainException("method failed")


def _group(container: DIContainer) -> None:
    if not container.is_registered(_Handlers):
        container.register_singleton(_Handlers, lambda c: _Handlers(c.get(_Dependency)))


@pytest.fixture
def container():
    di = DIContainer()
    di.register_singleton(_Dependency, lambda _: _Dependency())
    with patch("orb.infrastructure.di.container.get_container", return_value=di):
        yield di


@pytest.mark.asyncio
async def test_bound_handler_resolves_the_class_and_awaits_the_method(container) -> None:
    handler = registry.bind(_Handlers, "ok", group=_group)
    args = argparse.Namespace()

    result = await handler(args)

    assert result["args"] is args
    assert result["dependency"] is container.get(_Dependency)


@pytest.mark.asyncio
async def test_group_is_registered_on_the_current_container_once(container) -> None:
    calls: list[DIContainer] = []

    def group(c: DIContainer) -> None:
        calls.append(c)
        _group(c)

    handler = registry.bind(_Handlers, "ok", group=group)
    await handler(argparse.Namespace())
    first = container.get(_Handlers)
    await handler(argparse.Namespace())

    assert calls == [container, container]
    assert container.get(_Handlers) is first


@pytest.mark.asyncio
async def test_a_failing_method_is_handled_exactly_once(container) -> None:
    handler = registry.bind(_Handlers, "boom", group=_group)

    with patch.object(
        type(get_exception_handler()), "handle", autospec=True, side_effect=lambda s, e, c: e
    ) as handled:
        with pytest.raises(DomainException, match="method failed"):
            await handler(argparse.Namespace())

    assert handled.call_count == 1


@pytest.mark.asyncio
async def test_a_failing_dependency_is_handled_in_the_resolution_step(container) -> None:
    def broken(_: DIContainer) -> _Dependency:
        raise DomainException("dependency failed")

    container.register_singleton(_Dependency, broken)
    container.unregister(_Handlers)
    handler = registry.bind(_Handlers, "ok", group=_group)

    with patch.object(
        type(get_exception_handler()), "handle", autospec=True, side_effect=lambda s, e, c: e
    ) as handled:
        with pytest.raises(Exception, match="dependency failed"):
            await handler(argparse.Namespace())

    assert handled.call_count == 1
    assert handled.call_args.args[2].operation == "_Handlers.ok"


def test_only_the_resolution_step_is_decorated() -> None:
    seen: list[tuple[str, bool]] = []

    def spy(context: str, interface_type: str = "api", additional_context=None):
        def decorate(func):
            seen.append((context, inspect.iscoroutinefunction(func)))
            return func

        return decorate

    with patch("orb.infrastructure.error.decorators.handle_interface_exceptions", spy):
        bound = registry.bind(_Handlers, "ok", group=_group)

    assert seen == [("_Handlers.ok", False)]
    assert inspect.iscoroutinefunction(bound)
