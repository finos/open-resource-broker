"""Tests for ProviderRegistry — no hardcoded AWS API list and provider_type allowlist."""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.registry.provider_registry import ProviderRegistry


def _make_provider(name="aws-us-east-1", provider_type="aws", capabilities=None, handlers=None):
    """Build a minimal ProviderInstanceConfig-like mock."""
    p = MagicMock()
    p.name = name
    p.type = provider_type
    p.enabled = True
    p.capabilities = capabilities or []
    p.get_effective_handlers.return_value = handlers or {}
    return p


def _make_registry(provider, strategy=None) -> ProviderRegistry:
    """Build a ProviderRegistry with a minimal config_port and optional cached strategy."""
    registry = cast(ProviderRegistry, ProviderRegistry.__new__(ProviderRegistry))
    # Initialise BaseRegistry internals that __init__ would normally set
    registry._type_registrations = {}
    registry._instance_registrations = {}
    registry._registry_lock = __import__("threading").RLock()
    registry.mode = __import__(
        "orb.infrastructure.registry.base_registry", fromlist=["RegistryMode"]
    ).RegistryMode.MULTI_CHOICE
    registry._factory = None
    registry._initialized = True
    # Initialise ProviderRegistry-specific internals
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None

    provider_config = MagicMock()
    provider_config.providers = [provider]
    provider_config.provider_defaults = {provider.type: None}

    config_port = MagicMock()
    config_port.get_provider_config.return_value = provider_config
    registry._config_port = config_port
    registry._logger = MagicMock()

    if strategy is not None:
        registry._strategy_cache[provider.name] = strategy

    return registry


@pytest.mark.unit
class TestProviderSupportsApiNoHardcode:
    """_provider_supports_api must not contain hardcoded AWS API lists or type checks."""

    def test_no_hardcoded_aws_api_list_in_source(self):
        """The source of _provider_supports_api must not contain the hardcoded AWS API list."""
        import inspect

        source = inspect.getsource(ProviderRegistry._provider_supports_api)
        assert "EC2Fleet" not in source, "Hardcoded 'EC2Fleet' found in _provider_supports_api"
        assert "SpotFleet" not in source, "Hardcoded 'SpotFleet' found in _provider_supports_api"
        assert "RunInstances" not in source, (
            "Hardcoded 'RunInstances' found in _provider_supports_api"
        )
        assert "ASG" not in source, "Hardcoded 'ASG' found in _provider_supports_api"

    def test_no_provider_type_aws_check_in_source(self):
        """The source must not contain 'provider.type == \"aws\"'."""
        import inspect

        source = inspect.getsource(ProviderRegistry._provider_supports_api)
        assert 'provider.type == "aws"' not in source
        assert "provider.type == 'aws'" not in source

    def test_delegates_to_strategy_capabilities(self):
        """When strategy has get_capabilities() with supported_apis, delegate to it."""
        provider = _make_provider()

        caps = MagicMock()
        caps.supported_apis = ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"]
        strategy = MagicMock()
        strategy.get_capabilities.return_value = caps

        registry = _make_registry(provider, strategy=strategy)

        assert registry._provider_supports_api(provider, "EC2Fleet") is True
        assert registry._provider_supports_api(provider, "SpotFleet") is True
        assert registry._provider_supports_api(provider, "RunInstances") is True
        assert registry._provider_supports_api(provider, "ASG") is True

    def test_rejects_unknown_api_when_strategy_has_capabilities(self):
        """When strategy has a non-empty supported_apis, unknown APIs return False."""
        provider = _make_provider()

        caps = MagicMock()
        caps.supported_apis = ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"]
        strategy = MagicMock()
        strategy.get_capabilities.return_value = caps

        registry = _make_registry(provider, strategy=strategy)

        assert registry._provider_supports_api(provider, "UnknownAPI") is False

    def test_falls_through_to_true_when_no_strategy_cached(self):
        """When no strategy is cached, fall through to permissive True."""
        provider = _make_provider()
        registry = _make_registry(provider, strategy=None)

        assert registry._provider_supports_api(provider, "EC2Fleet") is True

    def test_falls_through_to_true_when_strategy_has_empty_supported_apis(self):
        """When strategy.get_capabilities().supported_apis is empty, fall through to True."""
        provider = _make_provider()

        caps = MagicMock()
        caps.supported_apis = []
        strategy = MagicMock()
        strategy.get_capabilities.return_value = caps

        registry = _make_registry(provider, strategy=strategy)

        assert registry._provider_supports_api(provider, "AnyAPI") is True

    def test_falls_through_to_true_when_get_capabilities_raises(self):
        """When get_capabilities() raises, fall through to True (no crash)."""
        provider = _make_provider()

        strategy = MagicMock()
        strategy.get_capabilities.side_effect = RuntimeError("boom")

        registry = _make_registry(provider, strategy=strategy)

        assert registry._provider_supports_api(provider, "EC2Fleet") is True

    def test_handler_config_still_takes_precedence(self):
        """Explicit handler config in provider still returns True before strategy lookup."""
        provider = _make_provider(handlers={"EC2Fleet": {}})

        caps = MagicMock()
        caps.supported_apis = []
        strategy = MagicMock()
        strategy.get_capabilities.return_value = caps

        registry = _make_registry(provider, strategy=strategy)

        assert registry._provider_supports_api(provider, "EC2Fleet") is True

    def test_capabilities_instance_attr_still_takes_precedence(self):
        """provider.capabilities list still returns True before strategy lookup."""
        provider = _make_provider(capabilities=["SpotFleet"])

        caps = MagicMock()
        caps.supported_apis = []
        strategy = MagicMock()
        strategy.get_capabilities.return_value = caps

        registry = _make_registry(provider, strategy=strategy)

        assert registry._provider_supports_api(provider, "SpotFleet") is True


# ---------------------------------------------------------------------------
# Provider type allowlist — ensure_provider_type_registered and
# ensure_provider_instance_registered_from_config must reject provider_type
# values that would allow module-injection via importlib.import_module.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderTypeAllowlist:
    """ensure_provider_type_registered must reject non-alphanumeric provider types."""

    def _make_bare_registry(self) -> ProviderRegistry:
        registry = cast(ProviderRegistry, ProviderRegistry.__new__(ProviderRegistry))
        registry._type_registrations = {}
        registry._instance_registrations = {}
        registry._registry_lock = __import__("threading").RLock()
        registry.mode = __import__(
            "orb.infrastructure.registry.base_registry", fromlist=["RegistryMode"]
        ).RegistryMode.MULTI_CHOICE
        registry._factory = None
        registry._initialized = True
        registry._strategy_cache = {}
        registry._health_states = {}
        registry._fallback_strategy = None
        registry._config_port = None
        registry._logger = MagicMock()
        return registry

    # --- ensure_provider_type_registered ---

    def test_valid_provider_type_attempts_import(self):
        """A valid snake_case type like 'aws' attempts the dynamic import (no ValueError)."""
        registry = self._make_bare_registry()

        with patch("importlib.import_module", side_effect=ImportError("no module")) as mock_import:
            result = registry.ensure_provider_type_registered("aws")

        # ImportError → returns False, but the import WAS attempted (no ValueError raised)
        mock_import.assert_called_once_with("orb.providers.aws.registration")
        assert result is False

    def test_valid_provider_type_with_underscores(self):
        """Types like 'my_provider' pass the allowlist."""
        registry = self._make_bare_registry()

        with patch("importlib.import_module", side_effect=ImportError("no module")):
            # Should not raise ValueError
            registry.ensure_provider_type_registered("my_provider")

    def test_valid_provider_type_with_digits(self):
        """Types like 'provider1' pass the allowlist."""
        registry = self._make_bare_registry()

        with patch("importlib.import_module", side_effect=ImportError("no module")):
            registry.ensure_provider_type_registered("provider1")

    def test_dot_in_provider_type_raises_value_error(self):
        """A provider_type containing a dot (e.g. 'os.path') must raise ValueError."""
        registry = self._make_bare_registry()

        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.ensure_provider_type_registered("os.path")

    def test_path_traversal_raises_value_error(self):
        """A path-traversal string must raise ValueError."""
        registry = self._make_bare_registry()

        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.ensure_provider_type_registered("../../etc/passwd")

    def test_uppercase_raises_value_error(self):
        """Uppercase letters are not permitted (consistent snake_case convention)."""
        registry = self._make_bare_registry()

        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.ensure_provider_type_registered("AWS")

    def test_leading_digit_raises_value_error(self):
        """A provider_type starting with a digit must raise ValueError."""
        registry = self._make_bare_registry()

        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.ensure_provider_type_registered("1provider")

    def test_space_in_provider_type_raises_value_error(self):
        """A provider_type with whitespace must raise ValueError."""
        registry = self._make_bare_registry()

        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.ensure_provider_type_registered("aws provider")

    # --- ensure_provider_instance_registered_from_config ---

    def test_instance_registration_dot_in_type_raises_value_error(self):
        """ensure_provider_instance_registered_from_config rejects dotted provider types."""
        registry = self._make_bare_registry()

        provider_instance = MagicMock()
        provider_instance.name = "my-instance"
        provider_instance.type = "malicious.module"
        # Not yet registered
        registry._instance_registrations = {}

        with pytest.raises(ValueError, match="Invalid provider type"):
            registry.ensure_provider_instance_registered_from_config(provider_instance)

    def test_instance_registration_valid_type_attempts_import(self):
        """ensure_provider_instance_registered_from_config passes validation for 'aws'."""
        registry = self._make_bare_registry()

        provider_instance = MagicMock()
        provider_instance.name = "aws-east"
        provider_instance.type = "aws"

        with patch("importlib.import_module", side_effect=ImportError("no module")):
            result = registry.ensure_provider_instance_registered_from_config(provider_instance)

        assert result is False  # ImportError → False, but no ValueError


def _make_registry_multi(providers) -> ProviderRegistry:
    """Build a ProviderRegistry backed by multiple provider instances."""
    registry = cast(ProviderRegistry, ProviderRegistry.__new__(ProviderRegistry))
    registry._type_registrations = {}
    registry._instance_registrations = {}
    registry._registry_lock = __import__("threading").RLock()
    registry.mode = __import__(
        "orb.infrastructure.registry.base_registry", fromlist=["RegistryMode"]
    ).RegistryMode.MULTI_CHOICE
    registry._factory = None
    registry._initialized = True
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None

    provider_config = MagicMock()
    provider_config.providers = providers
    provider_config.selection_policy = "FIRST_AVAILABLE"
    provider_config.get_active_providers.return_value = [p for p in providers if p.enabled]

    config_port = MagicMock()
    config_port.get_provider_config.return_value = provider_config

    registry._config_port = config_port
    registry._logger = MagicMock()
    return registry


@pytest.mark.unit
class TestSelectActiveProviderOverrides:
    """select_active_provider honours provider_name and provider_type method arguments."""

    def _make_provider(self, name, provider_type="aws", enabled=True):
        p = MagicMock()
        p.name = name
        p.type = provider_type
        p.enabled = enabled
        p.priority = 1
        return p

    def test_name_override_returns_single_matching_instance(self):
        """provider_name kwarg returns exactly the named instance."""
        aws1 = self._make_provider("aws-us-east-1", "aws")
        aws2 = self._make_provider("aws-eu-west-1", "aws")
        registry = _make_registry_multi([aws1, aws2])

        result = registry.select_active_provider(provider_name="aws-eu-west-1")

        assert result.provider_name == "aws-eu-west-1"
        assert result.provider_type == "aws"
        assert "name override" in result.selection_reason

    def test_name_override_raises_when_instance_not_found(self):
        """provider_name kwarg raises ValueError when the named instance does not exist."""
        aws1 = self._make_provider("aws-us-east-1", "aws")
        registry = _make_registry_multi([aws1])

        with pytest.raises(ValueError, match="'nonexistent' not found"):
            registry.select_active_provider(provider_name="nonexistent")

    def test_name_override_raises_when_instance_disabled(self):
        """provider_name kwarg raises ValueError when the named instance is disabled."""
        disabled = self._make_provider("aws-disabled", "aws", enabled=False)
        registry = _make_registry_multi([disabled])

        with pytest.raises(ValueError, match="'aws-disabled' is disabled"):
            registry.select_active_provider(provider_name="aws-disabled")

    def test_type_override_filters_to_matching_type_single(self):
        """provider_type kwarg returns the sole instance of that type."""
        aws1 = self._make_provider("aws-us-east-1", "aws")
        k8s1 = self._make_provider("k8s-main", "k8s")
        registry = _make_registry_multi([aws1, k8s1])

        result = registry.select_active_provider(provider_type="k8s")

        assert result.provider_name == "k8s-main"
        assert result.provider_type == "k8s"
        assert "type override" in result.selection_reason

    def test_type_override_load_balances_over_filtered_set(self):
        """provider_type kwarg load-balances when multiple instances of that type are active."""
        k8s1 = self._make_provider("k8s-main", "k8s")
        k8s2 = self._make_provider("k8s-secondary", "k8s")
        aws1 = self._make_provider("aws-us-east-1", "aws")
        registry = _make_registry_multi([k8s1, k8s2, aws1])

        result = registry.select_active_provider(provider_type="k8s")

        # Must be one of the k8s instances — aws is excluded
        assert result.provider_type == "k8s"
        assert result.provider_name in {"k8s-main", "k8s-secondary"}
        assert "aws" not in result.provider_name

    def test_type_override_raises_when_no_matching_instances(self):
        """provider_type kwarg raises ValueError when no active instances match."""
        aws1 = self._make_provider("aws-us-east-1", "aws")
        registry = _make_registry_multi([aws1])

        with pytest.raises(ValueError, match="No active providers of type 'k8s'"):
            registry.select_active_provider(provider_type="k8s")

    def test_no_override_uses_default_behaviour(self):
        """Without overrides, select_active_provider falls back to normal selection."""
        aws1 = self._make_provider("aws-us-east-1", "aws")
        registry = _make_registry_multi([aws1])

        result = registry.select_active_provider()

        assert result.provider_name == "aws-us-east-1"

    def test_name_override_takes_precedence_over_type_override(self):
        """When both provider_name and provider_type are given, provider_name wins."""
        aws1 = self._make_provider("aws-us-east-1", "aws")
        aws2 = self._make_provider("aws-eu-west-1", "aws")
        registry = _make_registry_multi([aws1, aws2])

        result = registry.select_active_provider(
            provider_name="aws-eu-west-1", provider_type="aws"
        )

        assert result.provider_name == "aws-eu-west-1"
        assert "name override" in result.selection_reason


@pytest.mark.unit
class TestListMachinesQueryProviderFilter:
    """ListMachinesQuery provider_name and provider_type fields filter results correctly."""

    def _make_machine(self, provider_name, provider_type):
        m = MagicMock()
        m.provider_name = provider_name
        m.provider_type = provider_type
        m.status = MagicMock()
        m.status.value = "running"
        m.request_id = None
        return m

    def test_provider_type_field_present_on_query(self):
        """ListMachinesQuery accepts provider_type without validation errors."""
        from orb.application.dto.queries import ListMachinesQuery

        q = ListMachinesQuery(provider_type="k8s", all_resources=True)
        assert q.provider_type == "k8s"

    def test_provider_name_and_type_default_to_none(self):
        """Both filter fields default to None."""
        from orb.application.dto.queries import ListMachinesQuery

        q = ListMachinesQuery()
        assert q.provider_name is None
        assert q.provider_type is None
