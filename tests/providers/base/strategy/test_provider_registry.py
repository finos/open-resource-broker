"""Unit tests for ProviderRegistry.

Each test constructs an isolated ProviderRegistry directly (bypassing the
global singleton) so tests never share state.  The singleton ``_instances``
dict is patched for the duration of each test that touches the module-level
``get_provider_registry`` function.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from orb.providers.registry.provider_registry import ProviderRegistry
from orb.providers.registry.types import UnsupportedProviderError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_registry(config_port=None) -> ProviderRegistry:
    """Return a NEW ProviderRegistry instance, bypassing the singleton.

    BaseRegistry uses ``__new__`` to enforce a singleton per class name.
    We override ``_instances`` for the class so each call returns a fresh object.
    """
    from orb.infrastructure.registry.base_registry import BaseRegistry

    # Remove the cached singleton so __new__ creates a fresh one.
    BaseRegistry._instances.pop("ProviderRegistry", None)
    registry = cast(ProviderRegistry, ProviderRegistry(config_port=config_port))
    # Remove again so subsequent calls in the same test don't get this instance.
    BaseRegistry._instances.pop("ProviderRegistry", None)
    return registry


def _noop_strategy_factory(config=None):
    s = MagicMock()
    s.is_initialized = False
    s.initialize.return_value = True
    return s


def _noop_config_factory(data=None):
    return {}


# ---------------------------------------------------------------------------
# Basic registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryBasicRegistration:
    def test_register_and_is_provider_registered(self):
        reg = _fresh_registry()
        reg.register_provider(
            "fake_prov",
            _noop_strategy_factory,
            _noop_config_factory,
        )
        assert reg.is_provider_registered("fake_prov")

    def test_unregister_provider_removes_it(self):
        reg = _fresh_registry()
        reg.register_provider("tmp_prov", _noop_strategy_factory, _noop_config_factory)
        assert reg.unregister_provider("tmp_prov") is True
        assert not reg.is_provider_registered("tmp_prov")

    def test_unregister_nonexistent_returns_false(self):
        reg = _fresh_registry()
        assert reg.unregister_provider("ghost_prov") is False

    def test_get_registered_providers_returns_list(self):
        reg = _fresh_registry()
        reg.register_provider("list_prov", _noop_strategy_factory, _noop_config_factory)
        providers = reg.get_registered_providers()
        assert "list_prov" in providers

    def test_register_instance_and_is_instance_registered(self):
        reg = _fresh_registry()
        reg.register_provider_instance(
            "base_type",
            "inst_name_a",
            _noop_strategy_factory,
            _noop_config_factory,
        )
        assert reg.is_provider_instance_registered("inst_name_a")

    def test_unregister_instance_removes_it(self):
        reg = _fresh_registry()
        reg.register_provider_instance(
            "bt_unregister",
            "inst_to_remove",
            _noop_strategy_factory,
            _noop_config_factory,
        )
        assert reg.unregister_provider_instance("inst_to_remove") is True
        assert not reg.is_provider_instance_registered("inst_to_remove")

    def test_unregister_nonexistent_instance_returns_false(self):
        reg = _fresh_registry()
        assert reg.unregister_provider_instance("ghost_inst") is False

    def test_duplicate_instance_is_idempotent(self):
        reg = _fresh_registry()
        reg.register_provider_instance(
            "dup_type", "dup_inst", _noop_strategy_factory, _noop_config_factory
        )
        # Second registration of the same instance name is a silent no-op (idempotent)
        reg.register_provider_instance(
            "dup_type", "dup_inst", _noop_strategy_factory, _noop_config_factory
        )
        assert reg.is_provider_instance_registered("dup_inst")

    def test_get_registered_instances_returns_list(self):
        reg = _fresh_registry()
        reg.register_provider_instance(
            "rt", "my_inst_b", _noop_strategy_factory, _noop_config_factory
        )
        instances = reg.get_registered_provider_instances()
        assert "my_inst_b" in instances


# ---------------------------------------------------------------------------
# Fallback strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryFallbackStrategy:
    def test_fallback_is_none_initially(self):
        reg = _fresh_registry()
        assert reg.get_fallback_strategy() is None

    def test_register_and_get_fallback_strategy(self):
        reg = _fresh_registry()
        sentinel = object()
        reg.register_fallback_strategy(sentinel)
        assert reg.get_fallback_strategy() is sentinel


# ---------------------------------------------------------------------------
# update_provider_health / get_strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryHealthAndCache:
    def test_update_and_retrieve_health_state(self):
        reg = _fresh_registry()
        reg.update_provider_health("prov_x", {"healthy": True})
        assert reg._health_states["prov_x"]["healthy"] is True

    def test_get_strategy_returns_none_when_not_cached(self):
        reg = _fresh_registry()
        assert reg.get_strategy("missing") is None

    def test_get_strategy_returns_cached_instance(self):
        reg = _fresh_registry()
        sentinel = object()
        reg._strategy_cache["cached_prov"] = sentinel
        assert reg.get_strategy("cached_prov") is sentinel


# ---------------------------------------------------------------------------
# create_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryCreateConfig:
    def test_create_config_calls_factory(self):
        reg = _fresh_registry()
        factory = MagicMock(return_value={"created": True})
        reg.register_provider("cfg_type", _noop_strategy_factory, factory)
        result = reg.create_config("cfg_type", {"key": "val"})
        factory.assert_called_once_with({"key": "val"})
        assert result == {"created": True}

    def test_create_config_raises_unsupported_error_for_unknown_type(self):
        reg = _fresh_registry()
        with pytest.raises(UnsupportedProviderError, match="not registered"):
            reg.create_config("nonexistent_type", {})


# ---------------------------------------------------------------------------
# create_resolver / create_validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryResolverValidator:
    def test_create_resolver_returns_none_when_not_registered(self):
        reg = _fresh_registry()
        reg.register_provider("no_resolver", _noop_strategy_factory, _noop_config_factory)
        assert reg.create_resolver("no_resolver") is None

    def test_create_validator_returns_none_when_not_registered(self):
        reg = _fresh_registry()
        reg.register_provider("no_validator", _noop_strategy_factory, _noop_config_factory)
        assert reg.create_validator("no_validator") is None

    def test_create_resolver_calls_factory(self):
        resolver_sentinel = object()
        resolver_factory = MagicMock(return_value=resolver_sentinel)
        reg = _fresh_registry()
        reg.register_provider(
            "has_resolver",
            _noop_strategy_factory,
            _noop_config_factory,
            resolver_factory=resolver_factory,
        )
        result = reg.create_resolver("has_resolver")
        assert result is resolver_sentinel

    def test_create_validator_calls_factory(self):
        validator_sentinel = object()
        validator_factory = MagicMock(return_value=validator_sentinel)
        reg = _fresh_registry()
        reg.register_provider(
            "has_validator",
            _noop_strategy_factory,
            _noop_config_factory,
            validator_factory=validator_factory,
        )
        result = reg.create_validator("has_validator")
        assert result is validator_sentinel


# ---------------------------------------------------------------------------
# get_default_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryDefaultApi:
    def test_get_default_api_returns_value_when_set(self):
        reg = _fresh_registry()
        reg.register_provider(
            "api_prov",
            _noop_strategy_factory,
            _noop_config_factory,
            default_api="EC2Fleet",
        )
        assert reg.get_default_api("api_prov") == "EC2Fleet"

    def test_get_default_api_returns_none_for_unknown_provider(self):
        reg = _fresh_registry()
        assert reg.get_default_api("unknown_api_prov") is None


# ---------------------------------------------------------------------------
# get_config_factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryConfigFactory:
    def test_get_config_factory_returns_callable(self):
        reg = _fresh_registry()
        reg.register_provider("cf_prov", _noop_strategy_factory, _noop_config_factory)
        factory = reg.get_config_factory("cf_prov")
        assert factory is _noop_config_factory

    def test_get_config_factory_returns_none_for_unknown(self):
        reg = _fresh_registry()
        assert reg.get_config_factory("unknown_cf_prov") is None


# ---------------------------------------------------------------------------
# ensure_provider_type_registered (dynamic import path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryEnsureTypeRegistered:
    def test_already_registered_returns_true(self):
        reg = _fresh_registry()
        reg.register_provider("known_type", _noop_strategy_factory, _noop_config_factory)
        assert reg.ensure_provider_type_registered("known_type") is True

    def test_invalid_provider_type_raises_value_error(self):
        reg = _fresh_registry()
        with pytest.raises(ValueError, match="Invalid provider type"):
            reg.ensure_provider_type_registered("invalid.type")

    def test_import_error_returns_false(self):
        reg = _fresh_registry()
        # "nonexistent_xyz" will fail importlib.import_module
        result = reg.ensure_provider_type_registered("nonexistent_xyz")
        assert result is False

    def test_valid_type_without_module_returns_false(self):
        reg = _fresh_registry()
        # "testprov" is a valid identifier but no module exists for it
        result = reg.ensure_provider_type_registered("testprov")
        assert result is False

    def test_module_without_register_function_returns_false(self):
        """Module imports OK but has no register_<type>_provider function."""
        import importlib
        from unittest.mock import patch

        reg = _fresh_registry()
        fake_module = MagicMock(spec=[])  # no attributes
        with patch.object(importlib, "import_module", return_value=fake_module):
            result = reg.ensure_provider_type_registered("validtype")
        assert result is False

    def test_module_with_register_function_calls_it_and_returns_true(self):
        """Module imports OK and has the register function — it is called."""
        import importlib
        from unittest.mock import patch

        reg = _fresh_registry()
        fake_module = MagicMock()
        fake_module.register_callprov_provider = MagicMock()
        with patch.object(importlib, "import_module", return_value=fake_module):
            result = reg.ensure_provider_type_registered("callprov")
        assert result is True
        fake_module.register_callprov_provider.assert_called_once()

    def test_exception_in_registration_function_returns_false(self):
        """Module imports but register function raises a generic Exception."""
        import importlib
        from unittest.mock import patch

        reg = _fresh_registry()
        fake_module = MagicMock()
        fake_module.register_excprov_provider = MagicMock(side_effect=RuntimeError("reg boom"))
        with patch.object(importlib, "import_module", return_value=fake_module):
            result = reg.ensure_provider_type_registered("excprov")
        assert result is False


# ---------------------------------------------------------------------------
# ensure_provider_instance_registered_from_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryEnsureInstanceRegistered:
    def test_already_registered_returns_true(self):
        reg = _fresh_registry()
        reg.register_provider_instance(
            "bt_ensure", "known_inst", _noop_strategy_factory, _noop_config_factory
        )
        mock_config = MagicMock()
        mock_config.name = "known_inst"
        mock_config.type = "bt_ensure"
        assert reg.ensure_provider_instance_registered_from_config(mock_config) is True

    def test_invalid_provider_type_raises_value_error(self):
        reg = _fresh_registry()
        mock_config = MagicMock()
        mock_config.name = "some_inst"
        mock_config.type = "Bad.Type"
        with pytest.raises(ValueError, match="Invalid provider type"):
            reg.ensure_provider_instance_registered_from_config(mock_config)

    def test_import_or_attribute_error_returns_false(self):
        reg = _fresh_registry()
        mock_config = MagicMock()
        mock_config.name = "new_inst_fail"
        mock_config.type = "nonexistent_xyz2"
        result = reg.ensure_provider_instance_registered_from_config(mock_config)
        assert result is False


# ---------------------------------------------------------------------------
# get_or_create_strategy — cache hit path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryGetOrCreateStrategy:
    def test_cache_hit_returns_cached_strategy(self):
        reg = _fresh_registry()
        cached = MagicMock()
        reg._strategy_cache["hit_prov"] = cached
        result = reg.get_or_create_strategy("hit_prov")
        assert result is cached

    def test_registered_type_creates_and_caches_strategy(self):
        strategy_mock = MagicMock()
        strategy_mock.is_initialized = True
        factory = MagicMock(return_value=strategy_mock)
        reg = _fresh_registry()
        reg.register_provider("type_create", factory, _noop_config_factory)
        result = reg.get_or_create_strategy("type_create")
        assert result is strategy_mock
        assert reg._strategy_cache.get("type_create") is strategy_mock

    def test_strategy_initialization_failure_returns_none(self):
        strategy_mock = MagicMock()
        strategy_mock.is_initialized = False
        strategy_mock.initialize.return_value = False
        factory = MagicMock(return_value=strategy_mock)
        reg = _fresh_registry()
        reg.register_provider("failing_init", factory, _noop_config_factory)
        result = reg.get_or_create_strategy("failing_init")
        assert result is None

    def test_registered_instance_creates_strategy(self):
        strategy_mock = MagicMock()
        strategy_mock.is_initialized = True
        factory = MagicMock(return_value=strategy_mock)
        reg = _fresh_registry()
        reg.register_provider_instance("bt_gc", "inst_gc", factory, _noop_config_factory)
        result = reg.get_or_create_strategy("inst_gc")
        assert result is strategy_mock

    def test_registered_instance_with_none_config_tries_config_port(self):
        strategy_mock = MagicMock()
        strategy_mock.is_initialized = True
        factory = MagicMock(return_value=strategy_mock)

        mock_config_port = MagicMock()
        mock_provider_config = MagicMock()
        inst_cfg = MagicMock()
        inst_cfg.name = "inst_with_port"
        mock_provider_config.get_active_providers.return_value = [inst_cfg]
        mock_config_port.get_provider_config.return_value = mock_provider_config

        reg = _fresh_registry(config_port=mock_config_port)
        reg.register_provider_instance("bt_port", "inst_with_port", factory, _noop_config_factory)
        result = reg.get_or_create_strategy("inst_with_port")
        assert result is strategy_mock
        # The None-config path must consult the config port to locate the
        # matching provider-instance config...
        mock_config_port.get_provider_config.assert_called_once()
        mock_provider_config.get_active_providers.assert_called_once()
        # ...and the located instance config must be passed to the factory.
        assert factory.call_args[0][0] is inst_cfg

    def test_instance_not_registered_type_fallback_succeeds(self):
        """Instance name contains underscore-separated type prefix; type is registered."""
        strategy_mock = MagicMock()
        strategy_mock.is_initialized = True
        factory = MagicMock(return_value=strategy_mock)
        reg = _fresh_registry()
        # Register the type "fallback_type"
        reg.register_provider("fallbacktype", factory, _noop_config_factory)
        # "fallbacktype_instance" has type prefix "fallbacktype" via extract_provider_type
        # extract_provider_type splits on hyphen, not underscore — use hyphen
        result = reg.get_or_create_strategy("fallbacktype-myinstance")
        assert result is strategy_mock

    def test_instance_not_registered_type_factory_exception_returns_none(self):
        factory = MagicMock(side_effect=RuntimeError("factory boom"))
        reg = _fresh_registry()
        reg.register_provider("boom_type", factory, _noop_config_factory)
        result = reg.get_or_create_strategy("boom_type-instance_xyz")
        assert result is None

    def test_config_with_name_and_type_triggers_instance_registration(self):
        # Provide a config-like object with name+type attributes
        mock_config = MagicMock()
        mock_config.name = "conf_inst"
        mock_config.type = "nonexistent_xyz3"  # will fail to import, but that's ok

        reg = _fresh_registry()
        # Without the type registered or instance registered, result is None
        result = reg.get_or_create_strategy("conf_inst", config=mock_config)
        assert result is None

    def test_config_port_none_does_not_crash_when_instance_needs_config(self):
        strategy_mock = MagicMock()
        strategy_mock.is_initialized = True
        factory = MagicMock(return_value=strategy_mock)
        reg = _fresh_registry(config_port=None)
        reg.register_provider_instance("bt_noport", "inst_noport", factory, _noop_config_factory)
        # config_port is None — should log warning and still proceed with config=None
        result = reg.get_or_create_strategy("inst_noport")
        assert result is strategy_mock


# ---------------------------------------------------------------------------
# collect_defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryCollectDefaults:
    def test_collect_defaults_empty_when_no_providers(self):
        reg = _fresh_registry()
        assert reg.collect_defaults() == {}

    def test_collect_defaults_merges_provider_defaults(self):
        class FakeStrategy:
            @staticmethod
            def get_defaults_config():
                return {"section": {"key": "value"}}

        reg = _fresh_registry()
        reg.register_provider(
            "defaults_prov",
            _noop_strategy_factory,
            _noop_config_factory,
            strategy_class=FakeStrategy,
        )
        merged = reg.collect_defaults()
        assert merged.get("section", {}).get("key") == "value"

    def test_collect_defaults_skips_provider_that_raises(self):
        class BrokenStrategy:
            @staticmethod
            def get_defaults_config():
                raise RuntimeError("boom defaults")

        reg = _fresh_registry()
        reg.register_provider(
            "broken_defaults",
            _noop_strategy_factory,
            _noop_config_factory,
            strategy_class=BrokenStrategy,
        )
        # Should not raise — returns empty dict
        result = reg.collect_defaults()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# list_all_provider_apis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryListAllApis:
    def test_returns_provider_type_as_fallback(self):
        reg = _fresh_registry()
        reg.register_provider("api_fallback", _noop_strategy_factory, _noop_config_factory)
        apis = reg.list_all_provider_apis()
        assert "api_fallback" in apis

    def test_uses_default_api_when_strategy_class_absent(self):
        reg = _fresh_registry()
        reg.register_provider(
            "api_default",
            _noop_strategy_factory,
            _noop_config_factory,
            default_api="MyAPI",
        )
        apis = reg.list_all_provider_apis()
        assert "MyAPI" in apis

    def test_deduplicates_apis(self):
        reg = _fresh_registry()
        reg.register_provider(
            "dup_api_1", _noop_strategy_factory, _noop_config_factory, default_api="SharedAPI"
        )
        reg.register_provider(
            "dup_api_2", _noop_strategy_factory, _noop_config_factory, default_api="SharedAPI"
        )
        apis = reg.list_all_provider_apis()
        assert apis.count("SharedAPI") == 1

    def test_uses_get_supported_apis_when_present(self):
        class ApiStrategy:
            def get_supported_apis(self):  # type: ignore[misc]
                return ["SpecialAPI"]

        reg = _fresh_registry()
        reg.register_provider(
            "api_method_prov",
            _noop_strategy_factory,
            _noop_config_factory,
            strategy_class=ApiStrategy,
        )
        apis = reg.list_all_provider_apis()
        assert "SpecialAPI" in apis


# ---------------------------------------------------------------------------
# get_provider_instance_registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryInstanceRegistration:
    def test_get_returns_registration_when_present(self):
        reg = _fresh_registry()
        reg.register_provider_instance(
            "bt_reg", "inst_reg_a", _noop_strategy_factory, _noop_config_factory
        )
        result = reg.get_provider_instance_registration("inst_reg_a")
        assert result is not None

    def test_get_returns_none_when_not_found(self):
        reg = _fresh_registry()
        assert reg.get_provider_instance_registration("nonexistent_instance") is None


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryDeepMerge:
    def test_shallow_merge(self):
        base = {"a": 1}
        ProviderRegistry._deep_merge(base, {"b": 2})
        assert base == {"a": 1, "b": 2}

    def test_nested_dict_merge(self):
        base = {"x": {"y": 1}}
        ProviderRegistry._deep_merge(base, {"x": {"z": 2}})
        assert base == {"x": {"y": 1, "z": 2}}

    def test_list_replaced_wholesale(self):
        base = {"items": [1, 2]}
        ProviderRegistry._deep_merge(base, {"items": [3, 4]})
        assert base["items"] == [3, 4]

    def test_nested_key_overwrite(self):
        base = {"x": {"y": 1}}
        ProviderRegistry._deep_merge(base, {"x": {"y": 99}})
        assert base["x"]["y"] == 99


# ---------------------------------------------------------------------------
# create_strategy delegates to get_or_create_strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderRegistryCreateStrategy:
    def test_create_strategy_delegates(self):
        reg = _fresh_registry()
        sentinel = object()
        reg._strategy_cache["delegate_prov"] = sentinel
        result = reg.create_strategy("delegate_prov")
        assert result is sentinel
