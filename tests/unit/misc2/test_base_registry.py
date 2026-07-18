"""Unit tests for orb.infrastructure.registry.base_registry — uncovered branches."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.registry.base_registry import (
    BaseRegistration,
    BaseRegistry,
    RegistryMode,
)

# ---------------------------------------------------------------------------
# Concrete subclass for testing (BaseRegistry is abstract)
# ---------------------------------------------------------------------------


class _ConcreteRegistry(BaseRegistry):
    """Minimal concrete registry used in tests — fresh per test via unique class."""

    def register(self, type_name: str, strategy_factory, config_factory, **kwargs) -> None:
        self.register_type(type_name, strategy_factory, config_factory, **kwargs)

    def create_strategy(self, type_name: str, config: Any) -> Any:
        return self.create_strategy_by_type(type_name, config)


def _fresh_registry(
    mode: RegistryMode = RegistryMode.SINGLE_CHOICE, factory=None
) -> _ConcreteRegistry:
    """Return a fresh (non-singleton) registry instance for isolation."""
    # Bypass the __new__ singleton to get a fresh instance per test
    reg = object.__new__(_ConcreteRegistry)
    reg.mode = mode
    reg._factory = factory
    reg._type_registrations = {}
    reg._instance_registrations = {}
    import threading

    reg._registry_lock = threading.RLock()
    reg._initialized = True
    return reg


# ---------------------------------------------------------------------------
# BaseRegistration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseRegistration:
    def test_get_factory_returns_none_for_unknown_key(self) -> None:
        reg = BaseRegistration("mytype", lambda c: None, lambda: None)
        assert reg.get_factory("nonexistent") is None

    def test_get_factory_returns_callable_for_known_key(self) -> None:
        fn = lambda: "resolver"
        reg = BaseRegistration("mytype", lambda c: None, lambda: None, resolver_factory=fn)
        assert reg.get_factory("resolver_factory") is fn


# ---------------------------------------------------------------------------
# register_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterType:
    def test_register_type_succeeds(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: "strategy", lambda: None)
        assert reg.is_registered("typeA") is True

    def test_register_type_idempotent(self) -> None:
        """Re-registering same type does not raise."""
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: "v1", lambda: None)
        reg.register_type("typeA", lambda c: "v2", lambda: None)
        # Should still exist and be the first registration
        assert reg.is_registered("typeA") is True

    def test_register_type_with_factory_calls_register_constructor(self) -> None:
        mock_factory = MagicMock()
        reg = _fresh_registry(factory=mock_factory)
        reg.register_type("typeA", lambda c: "s", lambda: None)
        mock_factory.register_constructor.assert_called_once()


# ---------------------------------------------------------------------------
# register_instance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterInstance:
    def test_register_instance_requires_multi_choice_mode(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.SINGLE_CHOICE)
        with pytest.raises(ValueError, match="MULTI_CHOICE"):
            reg.register_instance("typeA", "inst1", lambda c: "s", lambda: None)

    def test_register_instance_succeeds_in_multi_choice(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        reg.register_instance("typeA", "inst1", lambda c: "s", lambda: None)
        assert reg.is_instance_registered("inst1") is True

    def test_register_instance_idempotent(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        reg.register_instance("typeA", "inst1", lambda c: "v1", lambda: None)
        reg.register_instance("typeA", "inst1", lambda c: "v2", lambda: None)
        assert reg.is_instance_registered("inst1") is True


# ---------------------------------------------------------------------------
# create_strategy_by_type / create_strategy_by_instance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateStrategy:
    def test_create_strategy_by_type_calls_factory(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: f"strategy({c})", lambda: None)
        result = reg.create_strategy_by_type("typeA", "myconfig")
        assert result == "strategy(myconfig)"

    def test_create_strategy_by_type_unknown_raises(self) -> None:
        reg = _fresh_registry()
        with pytest.raises(ValueError, match="not registered"):
            reg.create_strategy_by_type("unknown", None)

    def test_create_strategy_by_instance_requires_multi_choice(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.SINGLE_CHOICE)
        with pytest.raises(ValueError, match="MULTI_CHOICE"):
            reg.create_strategy_by_instance("inst1", None)

    def test_create_strategy_by_instance_calls_factory(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        reg.register_instance("typeA", "inst1", lambda c: f"inst_strategy({c})", lambda: None)
        result = reg.create_strategy_by_instance("inst1", "cfg")
        assert result == "inst_strategy(cfg)"

    def test_create_strategy_by_instance_unknown_raises(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        with pytest.raises(ValueError, match="not registered"):
            reg.create_strategy_by_instance("nonexistent", None)

    def test_create_strategy_by_type_with_factory(self) -> None:
        """When _factory is set, strategy_factory is called directly (factory path)."""
        mock_factory = MagicMock()
        mock_factory.register_constructor = MagicMock()
        reg = _fresh_registry(factory=mock_factory)
        called_with = []
        reg.register_type("typeA", lambda c: called_with.append(c) or "strat", lambda: None)
        reg.create_strategy_by_type("typeA", "cfg123")
        assert "cfg123" in called_with

    def test_create_strategy_raises_configuration_error_on_exception(self) -> None:
        """_create_strategy_from_registration wraps factory exceptions in ConfigurationError."""
        from orb.domain.base.exceptions import ConfigurationError

        def _bad_factory(c):
            raise RuntimeError("factory fail")

        reg = _fresh_registry()
        reg.register_type("typeA", _bad_factory, lambda: None)
        with pytest.raises(ConfigurationError, match="Failed to create strategy"):
            reg.create_strategy_by_type("typeA", "cfg")


# ---------------------------------------------------------------------------
# unregister
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnregister:
    def test_unregister_type_returns_true_for_existing(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None)
        assert reg.unregister_type("typeA") is True
        assert reg.is_registered("typeA") is False

    def test_unregister_type_returns_false_for_missing(self) -> None:
        reg = _fresh_registry()
        assert reg.unregister_type("nonexistent") is False

    def test_unregister_instance_returns_true_for_existing(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        reg.register_instance("typeA", "inst1", lambda c: None, lambda: None)
        assert reg.unregister_instance("inst1") is True
        assert reg.is_instance_registered("inst1") is False

    def test_unregister_instance_returns_false_for_missing(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        assert reg.unregister_instance("nonexistent") is False


# ---------------------------------------------------------------------------
# format_not_registered_error / format_registry_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatErrors:
    def test_format_not_registered_error_with_no_registrations(self) -> None:
        reg = _fresh_registry()
        msg = reg.format_not_registered_error("foo", "provider")
        assert "No providers registered" in msg

    def test_format_not_registered_error_with_types(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None)
        msg = reg.format_not_registered_error("missing", "provider")
        assert "missing" in msg
        assert "typeA" in msg

    def test_format_not_registered_error_with_instances(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        reg.register_instance("typeA", "inst1", lambda c: None, lambda: None)
        msg = reg.format_not_registered_error("missing", "provider")
        assert "inst1" in msg

    def test_format_registry_error_is_alias(self) -> None:
        reg = _fresh_registry()
        assert reg.format_registry_error("x", "y") == reg.format_not_registered_error("x", "y")


# ---------------------------------------------------------------------------
# clear_registrations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClearRegistrations:
    def test_clear_removes_all_type_registrations(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None)
        reg.clear_registrations()
        assert reg.get_registered_types() == []

    def test_clear_removes_all_instance_registrations(self) -> None:
        reg = _fresh_registry(mode=RegistryMode.MULTI_CHOICE)
        reg.register_instance("typeA", "inst1", lambda c: None, lambda: None)
        reg.clear_registrations()
        assert reg.get_registered_instances() == []


# ---------------------------------------------------------------------------
# create_additional_component
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateAdditionalComponent:
    def test_returns_none_when_factory_missing(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None)
        result = reg.create_additional_component("typeA", "resolver_factory")
        assert result is None

    def test_returns_component_when_factory_present(self) -> None:
        resolver = MagicMock()
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None, resolver_factory=lambda: resolver)
        result = reg.create_additional_component("typeA", "resolver_factory")
        assert result is resolver

    def test_returns_none_on_factory_exception(self) -> None:
        def _bad():
            raise RuntimeError("fail")

        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None, resolver_factory=_bad)
        result = reg.create_additional_component("typeA", "resolver_factory")
        assert result is None

    def test_passes_config_to_factory_when_provided(self) -> None:
        received = []

        def _factory(cfg):
            received.append(cfg)
            return "component"

        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None, resolver_factory=_factory)
        reg.create_additional_component("typeA", "resolver_factory", config="my_cfg")
        assert received == ["my_cfg"]


# ---------------------------------------------------------------------------
# ensure_types_registered / get_available_types_with_registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnsureRegistration:
    def test_ensure_types_registered_calls_fn_when_empty(self) -> None:
        reg = _fresh_registry()
        called = []
        reg.ensure_types_registered(lambda: called.append(True))
        assert called == [True]

    def test_ensure_types_registered_skips_fn_when_non_empty(self) -> None:
        reg = _fresh_registry()
        reg.register_type("typeA", lambda c: None, lambda: None)
        called = []
        reg.ensure_types_registered(lambda: called.append(True))
        assert called == []

    def test_get_available_types_with_registration(self) -> None:
        reg = _fresh_registry()

        def _register():
            reg.register_type("typeA", lambda c: None, lambda: None)

        result = reg.get_available_types_with_registration(_register)
        assert "typeA" in result
