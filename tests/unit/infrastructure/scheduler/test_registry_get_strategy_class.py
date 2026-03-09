"""Tests for SchedulerRegistry.get_strategy_class registry-based dispatch."""

import ast
import inspect

import pytest

import orb.infrastructure.di.scheduler_services as svc_module
import orb.infrastructure.scheduler.registry as registry_module


class TestNoIfElifInGetStrategyClass:
    """AST scan: get_strategy_class must not contain if/elif dispatch."""

    def test_no_if_elif_dispatch(self):
        source = inspect.getsource(registry_module)
        tree = ast.parse(source)

        # Find get_strategy_class method body
        method_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_strategy_class":
                method_body = node
                break

        assert method_body is not None, "get_strategy_class method not found"

        # Flag any if/elif that compares against a string literal — that's the
        # old hardcoded dispatch pattern (e.g. `if scheduler_type in ["hostfactory", "hf"]`).
        # A None-guard (`if registration.strategy_class is None`) is fine.
        for node in ast.walk(method_body):
            if not isinstance(node, ast.If):
                continue
            # Check whether any string constant appears in the test expression
            for child in ast.walk(node.test):
                assert not isinstance(child, ast.Constant) or not isinstance(child.value, str), (
                    "get_strategy_class must not dispatch on string literals — "
                    "use registry lookup instead"
                )


class TestSchedulerRegistrationHasStrategyClass:
    """SchedulerRegistration must carry a strategy_class field."""

    def test_strategy_class_field_exists(self):
        reg = registry_module.SchedulerRegistration(
            scheduler_type="default",
            strategy_factory=lambda c: None,
            config_factory=lambda d: d,
            strategy_class=None,
        )
        assert hasattr(reg, "strategy_class")

    def test_strategy_class_stored(self):
        sentinel = object
        reg = registry_module.SchedulerRegistration(
            scheduler_type="default",
            strategy_factory=lambda c: None,
            config_factory=lambda d: d,
            strategy_class=sentinel,
        )
        assert reg.strategy_class is sentinel

    def test_strategy_class_defaults_to_none(self):
        reg = registry_module.SchedulerRegistration(
            scheduler_type="default",
            strategy_factory=lambda c: None,
            config_factory=lambda d: d,
        )
        assert reg.strategy_class is None


class TestGetStrategyClassRegistryLookup:
    """get_strategy_class reads from registration, not hardcoded dispatch."""

    @pytest.fixture(autouse=True)
    def fresh_registry(self):
        """Each test gets a clean registry state."""
        registry = registry_module.get_scheduler_registry()
        registry.clear_registrations()
        yield registry
        registry.clear_registrations()

    def test_get_strategy_class_hostfactory(self, fresh_registry):
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )
        from orb.infrastructure.scheduler.registration import (
            register_symphony_hostfactory_scheduler,
        )

        register_symphony_hostfactory_scheduler(fresh_registry)

        result = fresh_registry.get_strategy_class("hostfactory")
        assert result is HostFactorySchedulerStrategy

    def test_get_strategy_class_hf_alias(self, fresh_registry):
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )
        from orb.infrastructure.scheduler.registration import (
            register_symphony_hostfactory_scheduler,
        )

        register_symphony_hostfactory_scheduler(fresh_registry)

        result = fresh_registry.get_strategy_class("hf")
        assert result is HostFactorySchedulerStrategy

    def test_get_strategy_class_default(self, fresh_registry):
        from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
        from orb.infrastructure.scheduler.registration import register_default_scheduler

        register_default_scheduler(fresh_registry)

        result = fresh_registry.get_strategy_class("default")
        assert result is DefaultSchedulerStrategy

    def test_get_strategy_class_raises_when_strategy_class_none(self, fresh_registry):
        """Registration without strategy_class raises ValueError, not returns None."""
        fresh_registry.register(
            type_name="bare",
            strategy_factory=lambda c: None,
            config_factory=lambda d: d,
            # no strategy_class
        )

        with pytest.raises(ValueError, match="bare"):
            fresh_registry.get_strategy_class("bare")

    def test_get_strategy_class_raises_when_not_registered(self, fresh_registry):
        with pytest.raises((ValueError, Exception)):
            fresh_registry.get_strategy_class("nonexistent")


class TestSchedulerServicesNoStaleAssertion:
    """scheduler_services.py must not call ensure_type_registered('default')."""

    def test_no_stale_ensure_type_registered_call(self):
        source = inspect.getsource(svc_module)
        assert "ensure_type_registered" not in source, (
            "scheduler_services.py must not call ensure_type_registered — "
            "it fires before any scheduler is registered"
        )

    def test_register_scheduler_services_does_not_raise(self):
        """register_scheduler_services must not raise even with empty registry."""
        from unittest.mock import MagicMock

        container = MagicMock()
        # Should not raise
        svc_module.register_scheduler_services(container)
