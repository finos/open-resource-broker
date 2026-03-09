"""Tests for ProviderRegistry._provider_supports_api — no hardcoded AWS API list."""

from unittest.mock import MagicMock

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


def _make_registry(provider, strategy=None):
    """Build a ProviderRegistry with a minimal config_port and optional cached strategy."""
    registry = ProviderRegistry.__new__(ProviderRegistry)
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None
    registry._lock = __import__("threading").Lock()
    registry._registrations = {}
    registry._instance_registrations = {}
    registry._mode = MagicMock()

    provider_config = MagicMock()
    provider_config.providers = [provider]
    provider_config.provider_defaults = {provider.type: None}

    config_port = MagicMock()
    config_port.get_provider_config.return_value = provider_config
    registry._config_port = config_port

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
        assert "RunInstances" not in source, "Hardcoded 'RunInstances' found in _provider_supports_api"
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
