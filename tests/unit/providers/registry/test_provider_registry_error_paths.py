"""Unit tests for ProviderRegistry error/fallback paths not already covered.

Focuses on branches that only trigger on failure: type-level fallback when
an instance has been removed from config, strategy initialization failures,
registration-function import/attribute edge cases, and the exception
translation performed by register()/create_config()/register_provider_instance().
"""

import sys
import threading
import types
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.registry.base_registry import RegistryMode
from orb.providers.registry.provider_registry import ProviderRegistry, get_provider_registry
from orb.providers.registry.types import ProviderRegistration, UnsupportedProviderError


def _make_bare_registry() -> ProviderRegistry:
    """A ProviderRegistry instance with internals initialised but no real registrations."""
    registry = cast(ProviderRegistry, ProviderRegistry.__new__(ProviderRegistry))
    registry._type_registrations = {}
    registry._instance_registrations = {}
    registry._registry_lock = threading.RLock()
    registry.mode = RegistryMode.MULTI_CHOICE
    registry._factory = None
    registry._initialized = True
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None
    registry._selection_service = None
    registry._config_port = None
    registry._logger = MagicMock()
    return registry


class TestGetOrCreateStrategyFallback:
    """Type-level fallback when an instance name has no instance registration."""

    def test_type_fallback_succeeds_when_instance_missing(self) -> None:
        """When the instance isn't registered but its type is, fall back to type creation."""
        registry = _make_bare_registry()
        strategy = MagicMock()
        strategy.is_initialized = True
        registry.register_type(
            "aws", strategy_factory=lambda cfg: strategy, config_factory=lambda data: data
        )

        result = registry.get_or_create_strategy("aws-us-east-1")

        assert result is strategy
        assert registry._strategy_cache["aws-us-east-1"] is strategy

    def test_type_fallback_failure_is_swallowed(self) -> None:
        """When the type-level fallback strategy factory raises, None is returned (no crash)."""
        registry = _make_bare_registry()

        def _boom(_config):
            raise RuntimeError("cannot construct")

        registry.register_type("aws", strategy_factory=_boom, config_factory=lambda data: data)

        result = registry.get_or_create_strategy("aws-us-east-1")

        assert result is None
        assert "aws-us-east-1" not in registry._strategy_cache

    def test_strategy_without_initialize_attribute_is_cached_directly(self) -> None:
        """A strategy with no .initialize() is cached as-is."""
        registry = _make_bare_registry()
        strategy = object()  # No `initialize` attribute at all.
        registry.register_type(
            "aws", strategy_factory=lambda cfg: strategy, config_factory=lambda data: data
        )

        result = registry.get_or_create_strategy("aws")

        assert result is strategy

    def test_strategy_initialize_failure_returns_none(self) -> None:
        """When strategy.initialize() returns False, get_or_create_strategy returns None."""
        registry = _make_bare_registry()
        strategy = MagicMock()
        strategy.is_initialized = False
        strategy.initialize.return_value = False
        registry.register_type(
            "aws", strategy_factory=lambda cfg: strategy, config_factory=lambda data: data
        )

        result = registry.get_or_create_strategy("aws")

        assert result is None
        assert "aws" not in registry._strategy_cache


class TestEnsureProviderTypeRegistered:
    """Edge cases in dynamic import/registration of provider types."""

    def test_already_registered_short_circuits(self) -> None:
        """A type that is already registered returns True without importing anything."""
        registry = _make_bare_registry()
        registry.register_type(
            "aws", strategy_factory=lambda cfg: None, config_factory=lambda data: data
        )

        with patch("importlib.import_module") as mock_import:
            assert registry.ensure_provider_type_registered("aws") is True
        mock_import.assert_not_called()

    def test_missing_register_function_returns_false(self) -> None:
        """A module that exists but lacks the expected register_<type>_provider function."""
        registry = _make_bare_registry()
        fake_module = types.ModuleType("orb.providers.aws.registration")

        with patch("importlib.import_module", return_value=fake_module):
            assert registry.ensure_provider_type_registered("aws") is False

    def test_successful_registration_calls_register_function(self) -> None:
        """When the register function exists, it is invoked with (registry, logger)."""
        registry = _make_bare_registry()
        fake_module = types.ModuleType("orb.providers.aws.registration")
        register_fn = MagicMock()
        setattr(fake_module, "register_aws_provider", register_fn)

        with patch("importlib.import_module", return_value=fake_module):
            assert registry.ensure_provider_type_registered("aws") is True

        register_fn.assert_called_once_with(registry, registry._logger)

    def test_generic_exception_during_import_returns_false(self) -> None:
        """A non-ImportError exception during registration is logged and returns False."""
        registry = _make_bare_registry()

        with patch("importlib.import_module", side_effect=RuntimeError("unexpected")):
            assert registry.ensure_provider_type_registered("aws") is False


class TestEnsureProviderInstanceRegisteredFromConfig:
    """Dynamic import/registration of named provider instances."""

    def test_already_registered_short_circuits(self) -> None:
        """An already-registered instance name returns True without importing anything."""
        registry = _make_bare_registry()
        registry._instance_registrations["aws-east"] = MagicMock()
        provider_instance = MagicMock(name="aws-east", type="aws")
        provider_instance.name = "aws-east"
        provider_instance.type = "aws"

        with patch("importlib.import_module") as mock_import:
            assert (
                registry.ensure_provider_instance_registered_from_config(provider_instance) is True
            )
        mock_import.assert_not_called()

    def test_successful_registration_calls_register_function(self) -> None:
        """The provider's register_<type>_provider_instance function is invoked."""
        registry = _make_bare_registry()
        fake_module = types.ModuleType("orb.providers.aws.registration")
        register_fn = MagicMock()
        setattr(fake_module, "register_aws_provider_instance", register_fn)

        provider_instance = MagicMock()
        provider_instance.name = "aws-east"
        provider_instance.type = "aws"

        with patch("importlib.import_module", return_value=fake_module):
            result = registry.ensure_provider_instance_registered_from_config(provider_instance)

        assert result is True
        register_fn.assert_called_once_with(provider_instance, registry._logger)

    def test_missing_register_function_returns_false(self) -> None:
        """A module without the expected instance-registration function returns False."""
        registry = _make_bare_registry()
        fake_module = types.ModuleType("orb.providers.aws.registration")

        provider_instance = MagicMock()
        provider_instance.name = "aws-east"
        provider_instance.type = "aws"

        with patch("importlib.import_module", return_value=fake_module):
            result = registry.ensure_provider_instance_registered_from_config(provider_instance)

        assert result is False


class TestRegisterExceptionTranslation:
    """register()/register_provider_instance() translate ValueErrors from BaseRegistry."""

    def test_register_translates_value_error_to_configuration_error(self) -> None:
        """A ValueError raised by register_type() surfaces as ConfigurationError."""
        registry = _make_bare_registry()

        with (
            patch.object(registry, "register_type", side_effect=ValueError("already registered")),
            pytest.raises(ConfigurationError),
        ):
            registry.register("aws", lambda cfg: None, lambda data: data)

    def test_register_provider_instance_mode_mismatch_raises_value_error(self) -> None:
        """register_instance's mode-mismatch ValueError is translated to a clearer message."""
        registry = _make_bare_registry()
        registry.mode = RegistryMode.SINGLE_CHOICE  # Force the mismatch branch.

        with pytest.raises(ValueError, match="already registered"):
            registry.register_provider_instance(
                "aws", "aws-east", lambda cfg: None, lambda data: data
            )


class TestCreateConfigExceptionTranslation:
    """create_config() wraps unexpected factory failures as ConfigurationError."""

    def test_config_factory_exception_is_wrapped(self) -> None:
        """When the registered config_factory raises, create_config raises ConfigurationError."""
        registry = _make_bare_registry()

        def _boom(_data):
            raise RuntimeError("bad config")

        registry.register_type("aws", strategy_factory=lambda cfg: None, config_factory=_boom)

        with pytest.raises(ConfigurationError, match="Failed to create config"):
            registry.create_config("aws", {})

    def test_unregistered_type_raises_unsupported_provider_error(self) -> None:
        """Creating a config for an unregistered provider type raises UnsupportedProviderError."""
        registry = _make_bare_registry()

        with pytest.raises(UnsupportedProviderError):
            registry.create_config("does-not-exist", {})


class TestGetDefaultApiAndInstanceRegistration:
    """get_default_api / get_provider_instance_registration miss-path handling."""

    def test_get_default_api_returns_none_when_not_registered(self) -> None:
        """An unregistered provider type has no default API."""
        registry = _make_bare_registry()
        assert registry.get_default_api("does-not-exist") is None

    def test_get_provider_instance_registration_returns_none_when_missing(self) -> None:
        """An unregistered instance name returns None rather than raising."""
        registry = _make_bare_registry()
        assert registry.get_provider_instance_registration("missing-instance") is None

    def test_get_provider_instance_registration_returns_registration(self) -> None:
        """A registered instance's ProviderRegistration is returned."""
        registry = _make_bare_registry()
        registry.register_provider_instance("aws", "aws-east", lambda cfg: None, lambda data: data)

        result = registry.get_provider_instance_registration("aws-east")

        assert isinstance(result, ProviderRegistration)
        assert result.type_name == "aws"


class TestListAllProviderApis:
    """list_all_provider_apis aggregates across providers and tolerates failures."""

    def test_failing_provider_is_skipped_but_others_are_collected(self) -> None:
        """A provider whose strategy raises is skipped; other providers still contribute."""
        registry = _make_bare_registry()

        broken_strategy_class = MagicMock()
        broken_strategy_class.get_supported_apis.side_effect = RuntimeError("boom")
        registry.register_type(
            "broken",
            strategy_factory=lambda cfg: None,
            config_factory=lambda data: data,
            strategy_class=broken_strategy_class,
        )

        working_strategy_class = MagicMock()
        working_strategy_class.get_supported_apis.return_value = ["EC2Fleet", "ASG"]
        registry.register_type(
            "aws",
            strategy_factory=lambda cfg: None,
            config_factory=lambda data: data,
            strategy_class=working_strategy_class,
        )

        apis = registry.list_all_provider_apis()

        assert apis == ["ASG", "EC2Fleet"]

    def test_falls_back_to_default_api_when_no_strategy_class(self) -> None:
        """A provider without a strategy_class contributes its default_api."""
        registry = _make_bare_registry()
        registry.register_type(
            "aws",
            strategy_factory=lambda cfg: None,
            config_factory=lambda data: data,
            default_api="EC2Fleet",
        )

        assert registry.list_all_provider_apis() == ["EC2Fleet"]

    def test_falls_back_to_provider_type_when_nothing_else_available(self) -> None:
        """A provider with neither strategy_class nor default_api contributes its own name."""
        registry = _make_bare_registry()
        registry.register_type(
            "custom", strategy_factory=lambda cfg: None, config_factory=lambda data: data
        )

        assert registry.list_all_provider_apis() == ["custom"]


class TestProviderSupportsCapabilities:
    """_provider_supports_capabilities helper."""

    def test_no_required_capabilities_always_true(self) -> None:
        """An empty capability requirement list is trivially satisfied."""
        registry = _make_bare_registry()
        assert registry._provider_supports_capabilities(MagicMock(), []) is True

    def test_all_required_capabilities_present(self) -> None:
        """True when every required capability is in the strategy's supported list."""
        registry = _make_bare_registry()
        strategy = MagicMock()
        strategy.supported_capabilities = ["a", "b", "c"]
        assert registry._provider_supports_capabilities(strategy, ["a", "b"]) is True

    def test_missing_capability_returns_false(self) -> None:
        """False when a required capability is absent from the strategy's supported list."""
        registry = _make_bare_registry()
        strategy = MagicMock()
        strategy.supported_capabilities = ["a"]
        assert registry._provider_supports_capabilities(strategy, ["a", "b"]) is False

    def test_no_supported_capabilities_attribute_defaults_to_empty(self) -> None:
        """A strategy with no supported_capabilities attribute behaves as having none."""
        registry = _make_bare_registry()
        bare_strategy = object()
        assert registry._provider_supports_capabilities(bare_strategy, ["a"]) is False


class TestGetProviderRegistrySingleton:
    """get_provider_registry() module-level singleton accessor."""

    def test_returns_same_instance_across_calls(self, monkeypatch) -> None:
        """Repeated calls return the exact same ProviderRegistry instance."""
        monkeypatch.setattr(
            sys.modules[ProviderRegistry.__module__], "_provider_registry_instance", None
        )

        first = get_provider_registry()
        second = get_provider_registry()

        assert first is second
        assert isinstance(first, ProviderRegistry)
