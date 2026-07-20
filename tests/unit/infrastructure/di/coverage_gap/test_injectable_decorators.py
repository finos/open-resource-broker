"""Unit tests for orb.infrastructure.di.injectable (marker decorator + helpers).

Coverage targets: lines 38,120,122-123,125,139,141-142,144,157-158,177,179-182,
184,200,202-205,207,223,225-228,230,262,275,288,301-304,317-319,321-323,325,340,
359,372-374,376
"""

from __future__ import annotations

import pytest

from orb.infrastructure.di.injectable import (
    InjectableMetadata,
    OptionalDependency,
    command_handler,
    event_handler,
    factory,
    get_dependencies,
    get_handler_type,
    get_injectable_metadata,
    injectable,
    is_cqrs_handler,
    is_injectable,
    is_singleton,
    lazy,
    optional_dependency,
    query_handler,
    requires,
    singleton,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# InjectableMetadata
# ---------------------------------------------------------------------------


class TestInjectableMetadata:
    def test_default_values(self):
        meta = InjectableMetadata()
        assert meta.auto_wire is True
        assert meta.singleton is False
        assert meta.dependencies == []
        assert meta.factory is None
        assert meta.lazy is False

    def test_custom_values(self):
        def my_factory():
            return object()

        meta = InjectableMetadata(
            auto_wire=False,
            singleton=True,
            dependencies=[str, int],
            factory=my_factory,
            lazy=True,
        )
        assert meta.auto_wire is False
        assert meta.singleton is True
        assert meta.dependencies == [str, int]
        assert meta.factory is my_factory
        assert meta.lazy is True

    def test_to_dict_structure(self):
        meta = InjectableMetadata(singleton=True)
        d = meta.to_dict()
        assert "auto_wire" in d
        assert "singleton" in d
        assert d["singleton"] is True
        assert "dependencies" in d
        assert "factory" in d
        assert "lazy" in d


# ---------------------------------------------------------------------------
# @injectable marker
# ---------------------------------------------------------------------------


class TestInjectableDecorator:
    def test_sets_injectable_flag(self):
        @injectable
        class MyService:
            def __init__(self, value: str) -> None:
                self.value = value

        assert is_injectable(MyService)

    def test_populates_injectable_metadata(self):
        @injectable
        class MyService:
            def __init__(self, dep: int) -> None:
                self.dep = dep

        meta = get_injectable_metadata(MyService)
        assert meta is not None
        assert isinstance(meta, InjectableMetadata)

    def test_extracts_constructor_dependencies(self):
        class Dep1:
            pass

        class Dep2:
            pass

        @injectable
        class MyService:
            def __init__(self, a: Dep1, b: Dep2) -> None:
                self.a = a
                self.b = b

        meta = get_injectable_metadata(MyService)
        assert meta is not None
        # Dependencies may be stored as type objects or string annotations depending
        # on how inspect.signature resolves annotations; check by name or presence
        assert len(meta.dependencies) == 2

    def test_stores_original_init(self):
        @injectable
        class MyService:
            def __init__(self) -> None:
                pass

        assert hasattr(MyService, "_original_init")

    def test_injectable_class_still_instantiates_normally(self):
        @injectable
        class Counter:
            def __init__(self) -> None:
                self.count = 0

        obj = Counter()
        assert obj.count == 0

    def test_non_injectable_class_returns_false(self):
        class Plain:
            pass

        assert not is_injectable(Plain)

    def test_get_injectable_metadata_returns_none_for_non_injectable(self):
        class Plain:
            pass

        assert get_injectable_metadata(Plain) is None


# ---------------------------------------------------------------------------
# @singleton
# ---------------------------------------------------------------------------


class TestSingletonDecorator:
    def test_sets_singleton_flag(self):
        @singleton
        class MySingleton:
            pass

        assert is_singleton(MySingleton)

    def test_non_singleton_returns_false(self):
        class Plain:
            pass

        assert not is_singleton(Plain)

    def test_combined_singleton_and_injectable(self):
        @injectable
        @singleton
        class MyService:
            def __init__(self) -> None:
                pass

        assert is_injectable(MyService)
        meta = get_injectable_metadata(MyService)
        assert meta is not None
        assert meta.singleton is True


# ---------------------------------------------------------------------------
# @requires
# ---------------------------------------------------------------------------


class TestRequiresDecorator:
    def test_sets_dependencies_attribute(self):
        class DepA:
            pass

        class DepB:
            pass

        @requires(DepA, DepB)
        class MyService:
            pass

        assert hasattr(MyService, "_dependencies")
        assert DepA in MyService._dependencies  # type: ignore[attr-defined]
        assert DepB in MyService._dependencies  # type: ignore[attr-defined]

    def test_get_dependencies_reads_requires_attribute(self):
        class DepX:
            pass

        @requires(DepX)
        class MyService:
            pass

        deps = get_dependencies(MyService)
        assert DepX in deps

    def test_get_dependencies_via_injectable_metadata(self):
        class DepY:
            pass

        @injectable
        class MyService:
            def __init__(self, dep: DepY) -> None:
                self.dep = dep

        deps = get_dependencies(MyService)
        # Dependencies resolved from injectable metadata (may be type or str annotation)
        assert len(deps) == 1

    def test_get_dependencies_returns_empty_for_plain_class(self):
        class Plain:
            pass

        assert get_dependencies(Plain) == []


# ---------------------------------------------------------------------------
# @factory
# ---------------------------------------------------------------------------


class TestFactoryDecorator:
    def test_sets_factory_attribute(self):
        def my_factory():
            return object()

        @factory(my_factory)
        class MyService:
            pass

        assert MyService._factory is my_factory  # type: ignore[attr-defined]

    def test_combined_factory_and_injectable(self):
        def creator():
            return object()

        @injectable
        @factory(creator)
        class MyService:
            def __init__(self) -> None:
                pass

        meta = get_injectable_metadata(MyService)
        assert meta is not None
        assert meta.factory is creator


# ---------------------------------------------------------------------------
# @lazy
# ---------------------------------------------------------------------------


class TestLazyDecorator:
    def test_sets_lazy_flag(self):
        @lazy
        class LazyService:
            pass

        assert LazyService._lazy is True  # type: ignore[attr-defined]

    def test_combined_lazy_and_injectable(self):
        @injectable
        @lazy
        class LazyService:
            def __init__(self) -> None:
                pass

        meta = get_injectable_metadata(LazyService)
        assert meta is not None
        assert meta.lazy is True


# ---------------------------------------------------------------------------
# @command_handler
# ---------------------------------------------------------------------------


class TestCommandHandlerDecorator:
    def test_marks_as_command_handler(self):
        class MyCommand:
            pass

        @command_handler(MyCommand)
        class MyHandler:
            def __init__(self) -> None:
                pass

        assert is_cqrs_handler(MyHandler)
        assert get_handler_type(MyHandler) == "command"
        assert MyHandler._command_type is MyCommand  # type: ignore[attr-defined]

    def test_command_handler_is_also_injectable(self):
        class ACommand:
            pass

        @command_handler(ACommand)
        class AHandler:
            def __init__(self) -> None:
                pass

        assert is_injectable(AHandler)


# ---------------------------------------------------------------------------
# @query_handler
# ---------------------------------------------------------------------------


class TestQueryHandlerDecorator:
    def test_marks_as_query_handler(self):
        class MyQuery:
            pass

        @query_handler(MyQuery)
        class MyHandler:
            def __init__(self) -> None:
                pass

        assert is_cqrs_handler(MyHandler)
        assert get_handler_type(MyHandler) == "query"
        assert MyHandler._query_type is MyQuery  # type: ignore[attr-defined]

    def test_query_handler_is_also_injectable(self):
        class AQuery:
            pass

        @query_handler(AQuery)
        class AHandler:
            def __init__(self) -> None:
                pass

        assert is_injectable(AHandler)


# ---------------------------------------------------------------------------
# @event_handler
# ---------------------------------------------------------------------------


class TestEventHandlerDecorator:
    def test_marks_as_event_handler(self):
        class MyEvent:
            pass

        @event_handler(MyEvent)
        class MyEventHandler:
            def __init__(self) -> None:
                pass

        assert is_cqrs_handler(MyEventHandler)
        assert get_handler_type(MyEventHandler) == "event"
        assert MyEventHandler._event_type is MyEvent  # type: ignore[attr-defined]

    def test_event_handler_is_also_injectable(self):
        class AnEvent:
            pass

        @event_handler(AnEvent)
        class AnEventHandler:
            def __init__(self) -> None:
                pass

        assert is_injectable(AnEventHandler)


# ---------------------------------------------------------------------------
# is_cqrs_handler / get_handler_type for non-handlers
# ---------------------------------------------------------------------------


class TestCqrsHelpers:
    def test_plain_class_is_not_cqrs_handler(self):
        class Plain:
            pass

        assert not is_cqrs_handler(Plain)

    def test_get_handler_type_returns_none_for_plain_class(self):
        class Plain:
            pass

        assert get_handler_type(Plain) is None

    def test_injectable_non_cqrs_is_not_cqrs_handler(self):
        @injectable
        class RegularService:
            def __init__(self) -> None:
                pass

        assert not is_cqrs_handler(RegularService)


# ---------------------------------------------------------------------------
# OptionalDependency / optional_dependency helper
# ---------------------------------------------------------------------------


class TestOptionalDependency:
    def test_stores_dependency_type(self):
        dep = OptionalDependency(str)
        assert dep.dependency_type is str

    def test_repr_contains_type_name(self):
        dep = OptionalDependency(int)
        assert "int" in repr(dep)

    def test_optional_dependency_factory_function(self):
        dep = optional_dependency(float)
        assert isinstance(dep, OptionalDependency)
        assert dep.dependency_type is float
