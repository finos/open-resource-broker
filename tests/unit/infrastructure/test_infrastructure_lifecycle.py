"""Unit tests for infrastructure/lifecycle.py (LifecycleManager)."""

import pytest

from orb.infrastructure.lifecycle import (
    Lifecycle,
    LifecycleManager,
    get_lifecycle_manager,
    register_with_lifecycle_manager,
)


class _ConcreteComponent(Lifecycle):
    """Minimal concrete Lifecycle for testing."""

    def __init__(self, name: str = "component") -> None:
        self.name = name
        self.init_called = False
        self.shutdown_called = False

    def initialize(self) -> None:
        self.init_called = True

    def shutdown(self) -> None:
        self.shutdown_called = True


class _BrokenComponent(Lifecycle):
    """Component that throws on init and shutdown."""

    def initialize(self) -> None:
        raise RuntimeError("init broken")

    def shutdown(self) -> None:
        raise RuntimeError("shutdown broken")


@pytest.fixture(autouse=True)
def _reset_lifecycle_manager():
    """Ensure the singleton is reset between tests."""
    mgr = LifecycleManager.get_instance()
    mgr.reset()
    # Also clear the singleton so each test gets a fresh state
    LifecycleManager._instance = None
    yield
    LifecycleManager._instance = None


@pytest.mark.unit
class TestLifecycleManagerRegister:
    """Tests for register behaviour."""

    def test_register_component(self) -> None:
        mgr = LifecycleManager()
        comp = _ConcreteComponent()
        mgr.register(comp)
        assert mgr.get_component(_ConcreteComponent) is comp

    def test_register_same_type_twice_is_idempotent(self) -> None:
        mgr = LifecycleManager()
        comp1 = _ConcreteComponent()
        comp2 = _ConcreteComponent()
        mgr.register(comp1)
        mgr.register(comp2)  # same type, should be ignored
        assert len(mgr._components) == 1
        assert mgr.get_component(_ConcreteComponent) is comp1

    def test_get_component_returns_none_for_unregistered_type(self) -> None:
        mgr = LifecycleManager()
        assert mgr.get_component(_ConcreteComponent) is None


@pytest.mark.unit
class TestLifecycleManagerInitializeAll:
    """Tests for initialize_all."""

    def test_initialize_all_calls_initialize_on_each_component(self) -> None:
        mgr = LifecycleManager()
        c1 = _ConcreteComponent("a")

        class _Second(Lifecycle):
            def __init__(self):
                self.init_called = False
                self.shutdown_called = False

            def initialize(self):
                self.init_called = True

            def shutdown(self):
                self.shutdown_called = True

        mgr.register(c1)
        s = _Second()
        mgr.register(s)
        mgr.initialize_all()
        assert c1.init_called is True
        assert s.init_called is True

    def test_initialize_all_continues_on_component_failure(self) -> None:
        mgr = LifecycleManager()
        broken = _BrokenComponent()

        class _GoodAfterBroken(Lifecycle):
            def __init__(self):
                self.init_called = False
                self.shutdown_called = False

            def initialize(self):
                self.init_called = True

            def shutdown(self):
                pass

        good = _GoodAfterBroken()
        mgr.register(broken)
        mgr.register(good)
        mgr.initialize_all()  # must not raise
        assert good.init_called is True


@pytest.mark.unit
class TestLifecycleManagerShutdownAll:
    """Tests for shutdown_all."""

    def test_shutdown_all_calls_shutdown_in_reverse_order(self) -> None:
        mgr = LifecycleManager()
        order: list[str] = []

        class _A(Lifecycle):
            def initialize(self):
                pass

            def shutdown(self):
                order.append("A")

        class _B(Lifecycle):
            def initialize(self):
                pass

            def shutdown(self):
                order.append("B")

        mgr.register(_A())
        mgr.register(_B())
        mgr.shutdown_all()
        assert order == ["B", "A"]

    def test_shutdown_all_continues_on_component_failure(self) -> None:
        mgr = LifecycleManager()
        broken = _BrokenComponent()

        class _AfterBroken(Lifecycle):
            def __init__(self):
                self.shutdown_called = False

            def initialize(self):
                pass

            def shutdown(self):
                self.shutdown_called = True

        after = _AfterBroken()
        mgr.register(after)
        mgr.register(broken)  # broken registered second, shutdown first in reverse
        mgr.shutdown_all()  # must not raise


@pytest.mark.unit
class TestLifecycleManagerSingleton:
    """Tests for singleton and reset behaviour."""

    def test_get_instance_returns_same_instance(self) -> None:
        i1 = LifecycleManager.get_instance()
        i2 = LifecycleManager.get_instance()
        assert i1 is i2

    def test_reset_clears_components(self) -> None:
        mgr = LifecycleManager()
        mgr.register(_ConcreteComponent())
        mgr.reset()
        assert mgr._components == []
        assert mgr._component_types == {}

    def test_get_lifecycle_manager_returns_singleton(self) -> None:
        mgr = get_lifecycle_manager()
        assert isinstance(mgr, LifecycleManager)

    def test_register_with_lifecycle_manager_helper(self) -> None:
        LifecycleManager._instance = None  # force fresh singleton
        comp = _ConcreteComponent()
        register_with_lifecycle_manager(comp)
        assert get_lifecycle_manager().get_component(_ConcreteComponent) is comp
