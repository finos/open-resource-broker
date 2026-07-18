"""Additional coverage tests for DIContainer.

Coverage targets: lines 100,137,141-143,145,191,206,208,210,216-219,222,
226-229,232,237,242-245,249-252,256-258,343,385-386
"""

from __future__ import annotations

import pytest

from orb.infrastructure.di.container import (
    DIContainer,
    get_container,
    is_container_ready,
    reset_container,
    set_container_factory,
)
from orb.infrastructure.di.exceptions import (
    DependencyResolutionError,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ServiceA:
    pass


class _ServiceB:
    def __init__(self, a: _ServiceA) -> None:
        self.a = a


# ---------------------------------------------------------------------------
# register / get_registrations
# ---------------------------------------------------------------------------


class TestRegisterAndGetRegistrations:
    def test_register_via_registration_object(self):
        from orb.infrastructure.di.contracts import DependencyRegistration, DIScope

        container = DIContainer()
        reg = DependencyRegistration(
            dependency_type=_ServiceA,
            implementation_type=_ServiceA,
            scope=DIScope.TRANSIENT,
        )
        container.register(reg)
        assert container.is_registered(_ServiceA)

    def test_register_type_interface_to_impl(self):
        container = DIContainer()
        container.register_type(_ServiceA, _ServiceA)
        assert container.is_registered(_ServiceA)

    def test_unregister_removes_type(self):
        container = DIContainer()
        container.register_singleton(_ServiceA)
        result = container.unregister(_ServiceA)
        assert result is True
        assert container.is_registered(_ServiceA) is False

    def test_unregister_not_registered_returns_false(self):
        container = DIContainer()
        result = container.unregister(_ServiceA)
        assert result is False

    def test_get_registrations_returns_dict(self):
        container = DIContainer()
        container.register_singleton(_ServiceA)
        regs = container.get_registrations()
        assert isinstance(regs, dict)
        assert _ServiceA in regs


# ---------------------------------------------------------------------------
# get / get_optional / get_all
# ---------------------------------------------------------------------------


class TestGetMethods:
    def test_get_optional_returns_none_for_truly_unresolvable(self):
        # Use a unique type that definitely has no constructor deps and is not injectable
        class _Unique12345:
            def __init__(self, must_have: "int") -> None:
                pass  # primitive — can't resolve

        container = DIContainer()
        result = container.get_optional(_Unique12345)
        # A required primitive `int` param is not injectable, so get() raises and
        # get_optional() catches it, returning None deterministically.
        assert result is None

    def test_get_all_returns_list_with_instance_when_registered(self):
        container = DIContainer()
        instance = _ServiceA()
        container.register_instance(_ServiceA, instance)
        result = container.get_all(_ServiceA)
        assert len(result) == 1
        assert result[0] is instance

    def test_get_all_returns_empty_for_unresolvable(self):
        # A type with a required primitive param is not resolvable
        class _NeedsInt:
            def __init__(self, x: int) -> None:
                pass

        container = DIContainer()
        result = container.get_all(_NeedsInt)
        assert result == []

    def test_get_raises_on_unregistered_primitive_dep(self):
        class _NeedsStr:
            def __init__(self, val: str) -> None:
                pass

        container = DIContainer()
        with pytest.raises(DependencyResolutionError, match="primitive type 'str'"):
            container.get(_NeedsStr)


# ---------------------------------------------------------------------------
# CQRS handler registration methods (lines 191, 206, 208, 210)
# ---------------------------------------------------------------------------


class _TestCommand:
    pass


class _TestQuery:
    pass


class _TestEvent:
    pass


class _TestCommandHandler:
    pass


class _TestQueryHandler:
    pass


class _TestEventHandler:
    pass


class TestCQRSHandlerMethods:
    def test_register_command_handler_makes_handler_retrievable(self):
        container = DIContainer()
        container.register_instance(_TestCommandHandler, _TestCommandHandler())
        container.register_command_handler(_TestCommand, _TestCommandHandler)
        handler = container.get_command_handler(_TestCommand)
        assert isinstance(handler, _TestCommandHandler)

    def test_get_command_handler_raises_for_unregistered(self):
        container = DIContainer()
        with pytest.raises(DependencyResolutionError):
            container.get_command_handler(_TestCommand)

    def test_register_query_handler_makes_handler_retrievable(self):
        container = DIContainer()
        container.register_instance(_TestQueryHandler, _TestQueryHandler())
        container.register_query_handler(_TestQuery, _TestQueryHandler)
        handler = container.get_query_handler(_TestQuery)
        assert isinstance(handler, _TestQueryHandler)

    def test_get_query_handler_raises_for_unregistered(self):
        container = DIContainer()
        with pytest.raises(DependencyResolutionError):
            container.get_query_handler(_TestQuery)

    def test_register_event_handler_makes_handlers_retrievable(self):
        container = DIContainer()
        container.register_instance(_TestEventHandler, _TestEventHandler())
        container.register_event_handler(_TestEvent, _TestEventHandler)
        handlers = container.get_event_handlers(_TestEvent)
        assert len(handlers) == 1
        assert isinstance(handlers[0], _TestEventHandler)


# ---------------------------------------------------------------------------
# Lazy loading methods (lines 216-219, 222, 226-229, 232, 237, 242-245, ...)
# ---------------------------------------------------------------------------


class TestLazyLoadingMethods:
    def test_register_lazy_factory_stores_factory_when_enabled(self):
        container = DIContainer()
        # Default lazy config is enabled=True, so the factory is stored in
        # _lazy_factories under the class key (not registered immediately).
        assert container._lazy_config.enabled is True
        instance = _ServiceA()

        def factory(c):
            return instance

        container.register_lazy_factory(_ServiceA, factory)
        assert _ServiceA in container._lazy_factories
        assert container._lazy_factories[_ServiceA] is factory

    def test_register_lazy_factory_falls_through_when_disabled(self):
        from orb.infrastructure.di.lazy_config import LazyLoadingConfig

        container = DIContainer()
        container._lazy_config = LazyLoadingConfig({"enabled": False})
        instance = _ServiceA()

        container.register_lazy_factory(_ServiceA, lambda c: instance)
        # Disabled branch registers the factory immediately instead of deferring.
        assert _ServiceA not in container._lazy_factories
        assert container.is_registered(_ServiceA) is True

    def test_register_on_demand_immediate_when_lazy_disabled(self):
        container = DIContainer()
        from orb.infrastructure.di.lazy_config import LazyLoadingConfig

        # LazyLoadingConfig looks at top-level "enabled" key
        lazy = LazyLoadingConfig({"enabled": False})
        container._lazy_config = lazy
        assert container._lazy_config.enabled is False

        called = []

        def reg_func(c):
            called.append(c)
            c.register_instance(_ServiceA, _ServiceA())

        container.register_on_demand(_ServiceA, reg_func)
        # When lazy loading disabled, registration happens immediately
        assert len(called) == 1
        assert container.is_registered(_ServiceA)

    def test_is_lazy_loading_enabled_reflects_config(self):
        from orb.infrastructure.di.lazy_config import LazyLoadingConfig

        container = DIContainer()
        # Default LazyLoadingConfig has enabled=True.
        assert container.is_lazy_loading_enabled() is True

        container._lazy_config = LazyLoadingConfig({"enabled": False})
        assert container.is_lazy_loading_enabled() is False

    def test_get_lazy_config_returns_config(self):
        from orb.infrastructure.di.lazy_config import LazyLoadingConfig

        container = DIContainer()
        cfg = container.get_lazy_config()
        assert isinstance(cfg, LazyLoadingConfig)
        assert cfg is container._lazy_config


# ---------------------------------------------------------------------------
# register_injectable_class with CQRS attrs
# ---------------------------------------------------------------------------


class TestRegisterInjectableClass:
    def test_registers_class_with_command_type_attr(self):
        container = DIContainer()

        class _Handler:
            _command_type = _TestCommand

        container.register_injectable_class(_Handler)
        assert container.is_registered(_Handler)

    def test_registers_class_with_query_type_attr(self):
        container = DIContainer()

        class _QHandler:
            _query_type = _TestQuery

        container.register_injectable_class(_QHandler)
        assert container.is_registered(_QHandler)

    def test_registers_class_with_event_type_attr(self):
        container = DIContainer()

        class _EHandler:
            _event_type = _TestEvent

        container.register_injectable_class(_EHandler)
        assert container.is_registered(_EHandler)


# ---------------------------------------------------------------------------
# validate_required_ports
# ---------------------------------------------------------------------------


class TestValidateRequiredPorts:
    def test_returns_empty_when_all_registered(self):
        container = DIContainer()
        container.register_singleton(_ServiceA)
        missing = container.validate_required_ports([_ServiceA])
        assert missing == []

    def test_returns_names_of_missing_types(self):
        container = DIContainer()
        missing = container.validate_required_ports([_ServiceA])
        assert "_ServiceA" in missing


# ---------------------------------------------------------------------------
# clear / get_stats
# ---------------------------------------------------------------------------


class TestClearAndStats:
    def test_clear_removes_registrations(self):
        container = DIContainer()
        container.register_singleton(_ServiceA)
        container.clear()
        assert container.is_registered(_ServiceA) is False

    def test_get_stats_returns_dict(self):
        container = DIContainer()
        stats = container.get_stats()
        assert "container_type" in stats
        assert stats["container_type"] == "modular"


# ---------------------------------------------------------------------------
# Module-level functions: set_container_factory, get_container, reset_container
# ---------------------------------------------------------------------------


class TestContainerSingleton:
    def setup_method(self):
        reset_container()

    def teardown_method(self):
        reset_container()
        # These tests replace the module-level container factory with no-op
        # stubs; restore the canonical composition-root factory so the leaked
        # stub does not break later tests (e.g. API router tests) that resolve
        # real ports from the global container.
        from orb.bootstrap.services import register_all_services
        from orb.infrastructure.di.container import set_container_factory

        set_container_factory(register_all_services)

    def test_get_container_raises_without_factory(self):
        from orb.infrastructure.di import container as _c_mod

        orig = _c_mod._container_factory
        _c_mod._container_factory = None
        try:
            with pytest.raises(RuntimeError, match="No container factory"):
                get_container()
        finally:
            _c_mod._container_factory = orig

    def test_is_container_ready_false_before_get(self):
        assert is_container_ready() is False

    def test_set_container_factory_and_get_container(self):
        def factory(c: DIContainer):
            pass

        set_container_factory(factory)
        c = get_container()
        assert isinstance(c, DIContainer)
        assert is_container_ready() is True

    def test_reset_container_clears_ready_flag(self):
        def factory(c: DIContainer):
            pass

        set_container_factory(factory)
        get_container()
        reset_container()
        assert is_container_ready() is False
