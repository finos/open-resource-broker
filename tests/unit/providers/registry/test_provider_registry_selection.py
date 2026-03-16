"""Unit tests for ProviderRegistry selection logic.

Tests select_active_provider() and select_provider_for_template() using a mock
ConfigurationPort and a directly constructed ProviderRegistry (not the global singleton).
"""

import threading
from unittest.mock import MagicMock

import pytest

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.results import ProviderSelectionResult
from orb.providers.registry.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_instance(name, provider_type="aws", enabled=True, priority=1, weight=1):
    p = MagicMock()
    p.name = name
    p.type = provider_type
    p.enabled = enabled
    p.priority = priority
    p.weight = weight
    p.provider_name = None
    p.provider_type = None
    p.provider_api = None
    p.capabilities = []
    p.get_effective_handlers.return_value = {}
    return p


def _make_config_port(providers, selection_policy="FIRST_AVAILABLE"):
    provider_config = MagicMock()
    provider_config.providers = providers
    provider_config.selection_policy = selection_policy
    provider_config.get_active_providers.return_value = [p for p in providers if p.enabled]
    provider_config.provider_defaults = {p.type: None for p in providers}
    provider_config.default_provider_instance = None
    provider_config.default_provider_type = None

    config_port = MagicMock(spec=ConfigurationPort)
    config_port.get_provider_config.return_value = provider_config
    return config_port


def _make_registry(providers=None, config_port=None, selection_policy="FIRST_AVAILABLE"):
    registry = ProviderRegistry.__new__(ProviderRegistry)
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None
    registry._lock = threading.Lock()
    registry._registrations = {}
    registry._instance_registrations = {}
    registry._mode = MagicMock()
    registry._logger = MagicMock()

    if config_port is not None:
        registry._config_port = config_port
    else:
        registry._config_port = _make_config_port(providers or [], selection_policy)

    return registry


def _make_template(provider_name=None, provider_type=None, provider_api=None):
    t = MagicMock()
    t.template_id = "tpl-1"
    t.provider_name = provider_name
    t.provider_type = provider_type
    t.provider_api = provider_api
    return t


# ---------------------------------------------------------------------------
# select_active_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectActiveProvider:
    def test_single_active_provider_returns_it(self):
        p = _make_provider_instance("aws-primary")
        registry = _make_registry(providers=[p])

        result = registry.select_active_provider()

        assert isinstance(result, ProviderSelectionResult)
        assert result.provider_name == "aws-primary"
        assert result.selection_reason == "single_active_provider"
        assert result.confidence == 1.0

    def test_multiple_providers_applies_load_balancing(self):
        p1 = _make_provider_instance("aws-east", priority=1)
        p2 = _make_provider_instance("aws-west", priority=2)
        registry = _make_registry(providers=[p1, p2], selection_policy="FIRST_AVAILABLE")

        result = registry.select_active_provider()

        assert result.provider_name == "aws-east"
        assert "load_balanced_first_available" in result.selection_reason

    def test_no_active_providers_raises_value_error(self):
        p = _make_provider_instance("aws-disabled", enabled=False)
        registry = _make_registry(providers=[p])

        with pytest.raises(ValueError, match="No active providers"):
            registry.select_active_provider()

    def test_config_port_none_raises_value_error(self):
        registry = _make_registry(providers=[])
        registry._config_port = None

        with pytest.raises(ValueError, match="No provider configuration available"):
            registry.select_active_provider()

    def test_alternatives_populated_when_multiple_providers(self):
        p1 = _make_provider_instance("aws-a")
        p2 = _make_provider_instance("aws-b")
        p3 = _make_provider_instance("aws-c")
        registry = _make_registry(providers=[p1, p2, p3], selection_policy="FIRST_AVAILABLE")

        result = registry.select_active_provider()

        assert result.provider_name == "aws-a"
        assert "aws-b" in result.alternatives
        assert "aws-c" in result.alternatives


# ---------------------------------------------------------------------------
# select_provider_for_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectProviderForTemplate:
    def test_strategy1_cli_override_takes_precedence(self):
        p = _make_provider_instance("aws-primary")
        registry = _make_registry(providers=[p])
        template = _make_template()

        result = registry.select_provider_for_template(template, provider_name="aws-primary")

        assert isinstance(result, ProviderSelectionResult)
        assert result.provider_name == "aws-primary"
        assert "CLI override" in result.selection_reason

    def test_strategy1_cli_override_disabled_provider_raises(self):
        p = _make_provider_instance("aws-disabled", enabled=False)
        registry = _make_registry(providers=[p])
        template = _make_template()

        with pytest.raises(ValueError, match="is disabled"):
            registry.select_provider_for_template(template, provider_name="aws-disabled")

    def test_strategy2_explicit_template_provider_name(self):
        p = _make_provider_instance("aws-primary")
        registry = _make_registry(providers=[p])
        template = _make_template(provider_name="aws-primary")

        result = registry.select_provider_for_template(template)

        assert result.provider_name == "aws-primary"
        assert result.selection_reason == "Explicitly specified in template"

    def test_strategy3_provider_type_load_balancing(self):
        p1 = _make_provider_instance("aws-east", provider_type="aws")
        p2 = _make_provider_instance("aws-west", provider_type="aws")
        registry = _make_registry(providers=[p1, p2], selection_policy="FIRST_AVAILABLE")
        template = _make_template(provider_type="aws")

        result = registry.select_provider_for_template(template)

        assert result.provider_type == "aws"
        assert "Load balanced" in result.selection_reason
        assert result.confidence == 0.9

    def test_strategy3_no_instances_for_type_raises(self):
        p = _make_provider_instance("aws-east", provider_type="aws")
        registry = _make_registry(providers=[p])
        template = _make_template(provider_type="gcp")

        with pytest.raises(ValueError, match="No enabled instances found for provider type"):
            registry.select_provider_for_template(template)

    def test_strategy4_api_capability_selection(self):
        p = _make_provider_instance("aws-primary")
        p.get_effective_handlers.return_value = {"EC2Fleet": {}}
        config_port = _make_config_port([p])
        # provider_defaults must include the provider type key
        config_port.get_provider_config.return_value.provider_defaults = {"aws": None}
        registry = _make_registry(config_port=config_port)
        template = _make_template(provider_api="EC2Fleet")

        result = registry.select_provider_for_template(template)

        assert "Supports required API" in result.selection_reason
        assert result.confidence == 0.8

    def test_strategy4_no_compatible_providers_permissive_fallthrough(self):
        # When no strategy is cached and handlers/capabilities are empty,
        # _provider_supports_api falls through to True (permissive behaviour).
        p = _make_provider_instance("aws-primary")
        p.get_effective_handlers.return_value = {}
        p.capabilities = []
        config_port = _make_config_port([p])
        config_port.get_provider_config.return_value.provider_defaults = {"aws": None}
        registry = _make_registry(config_port=config_port)
        template = _make_template(provider_api="UnknownAPI")

        # Permissive fallthrough — result is not None
        result = registry.select_provider_for_template(template)

        assert result is not None

    def test_strategy5_default_fallback_no_template_hints(self):
        p = _make_provider_instance("aws-primary")
        registry = _make_registry(providers=[p])
        template = _make_template()

        result = registry.select_provider_for_template(template)

        assert (
            result.selection_reason == "Configuration default (no provider specified in template)"
        )
        assert result.confidence == 0.7

    def test_strategy5_fallback_strategy_used_when_no_config(self):
        registry = _make_registry(providers=[])
        registry._config_port = None
        fallback = MagicMock()
        registry._fallback_strategy = fallback
        template = _make_template()

        result = registry.select_provider_for_template(template)

        assert result is fallback

    def test_strategy5_no_config_no_fallback_raises(self):
        registry = _make_registry(providers=[])
        registry._config_port = None
        registry._fallback_strategy = None
        template = _make_template()

        with pytest.raises(ValueError, match="No provider configuration available"):
            registry.select_provider_for_template(template)
