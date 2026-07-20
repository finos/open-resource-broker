"""Unit tests for ProviderSelectionService — uncovered branches."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.services.provider_selection_service import ProviderSelectionService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(
    name: str = "aws1",
    ptype: str = "aws",
    enabled: bool = True,
    priority: int = 0,
    weight: int = 100,
    capabilities: Any = None,
    handlers: Any = None,
) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.type = ptype
    p.enabled = enabled
    p.priority = priority
    p.weight = weight
    p.capabilities = capabilities
    p.get_effective_handlers.return_value = handlers or {}
    return p


def _make_provider_config(
    providers: list | None = None,
    selection_policy: str = "FIRST_AVAILABLE",
    default_provider_type: str | None = None,
    default_provider_instance: str | None = None,
    provider_defaults: dict | None = None,
):
    cfg = MagicMock()
    cfg.providers = providers or []
    cfg.selection_policy = selection_policy
    cfg.get_active_providers.return_value = [p for p in (providers or []) if p.enabled]
    cfg.default_provider_type = default_provider_type
    cfg.default_provider_instance = default_provider_instance
    cfg.provider_defaults = provider_defaults or {}
    return cfg


def _make_service(provider_config=None, registry_strategy=None) -> ProviderSelectionService:
    registry = MagicMock()
    registry.get_strategy.return_value = registry_strategy
    registry.get_fallback_strategy.return_value = None

    config_port = MagicMock()
    config_port.get_provider_config.return_value = provider_config

    svc = ProviderSelectionService(registry=registry, config_port=config_port)
    return svc


# ---------------------------------------------------------------------------
# select_active_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectActiveProvider:
    def test_raises_when_no_provider_config(self) -> None:
        svc = _make_service(provider_config=None)
        with pytest.raises(ValueError, match="No provider configuration"):
            svc.select_active_provider()

    def test_raises_when_no_active_providers(self) -> None:
        cfg = _make_provider_config(providers=[])
        svc = _make_service(provider_config=cfg)
        with pytest.raises(ValueError, match="No active providers"):
            svc.select_active_provider()

    def test_single_active_provider_selected_directly(self) -> None:
        p = _make_provider("aws1")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        result = svc.select_active_provider()
        assert result.provider_name == "aws1"
        assert result.selection_reason == "single_active_provider"

    def test_multiple_providers_load_balanced(self) -> None:
        p1 = _make_provider("aws1", priority=0)
        p2 = _make_provider("aws2", priority=1)
        cfg = _make_provider_config(providers=[p1, p2], selection_policy="FIRST_AVAILABLE")
        svc = _make_service(provider_config=cfg)
        result = svc.select_active_provider()
        # FIRST_AVAILABLE returns first element
        assert result.provider_name in ("aws1", "aws2")

    def test_provider_name_override(self) -> None:
        p = _make_provider("aws1")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        result = svc.select_active_provider(provider_name="aws1")
        assert result.provider_name == "aws1"
        assert "name override" in result.selection_reason

    def test_provider_name_override_raises_for_disabled(self) -> None:
        p = _make_provider("aws1", enabled=False)
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        with pytest.raises(ValueError, match="disabled"):
            svc.select_active_provider(provider_name="aws1")

    def test_provider_name_override_raises_for_not_found(self) -> None:
        cfg = _make_provider_config(providers=[])
        svc = _make_service(provider_config=cfg)
        with pytest.raises(ValueError, match="not found"):
            svc.select_active_provider(provider_name="nonexistent")

    def test_provider_type_override_single_match(self) -> None:
        p = _make_provider("aws1", ptype="aws")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        result = svc.select_active_provider(provider_type="aws")
        assert result.provider_name == "aws1"
        assert "single_active_match" in result.selection_reason

    def test_provider_type_override_raises_when_no_match(self) -> None:
        p = _make_provider("aws1", ptype="aws")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        with pytest.raises(ValueError, match="No active providers of type"):
            svc.select_active_provider(provider_type="k8s")

    def test_provider_type_override_multiple_load_balanced(self) -> None:
        p1 = _make_provider("aws1", ptype="aws", priority=0)
        p2 = _make_provider("aws2", ptype="aws", priority=1)
        cfg = _make_provider_config(providers=[p1, p2], selection_policy="FIRST_AVAILABLE")
        svc = _make_service(provider_config=cfg)
        result = svc.select_active_provider(provider_type="aws")
        assert result.provider_name in ("aws1", "aws2")
        assert "load_balanced" in result.selection_reason


# ---------------------------------------------------------------------------
# select_provider_for_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectProviderForTemplate:
    def test_cli_override_takes_precedence(self) -> None:
        p = _make_provider("aws1")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = None
        tmpl.provider_api = None
        result = svc.select_provider_for_template(tmpl, provider_name="aws1")
        assert result.provider_name == "aws1"

    def test_explicit_provider_from_template(self) -> None:
        p = _make_provider("aws1")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = "aws1"
        tmpl.provider_type = None
        tmpl.provider_api = None
        result = svc.select_provider_for_template(tmpl)
        assert result.provider_name == "aws1"

    def test_explicit_provider_raises_when_not_found(self) -> None:
        cfg = _make_provider_config(providers=[])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = "nonexistent"
        tmpl.provider_type = None
        tmpl.provider_api = None
        with pytest.raises(ValueError, match="not found"):
            svc.select_provider_for_template(tmpl)

    def test_explicit_provider_raises_when_disabled(self) -> None:
        p = _make_provider("aws1", enabled=False)
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = "aws1"
        tmpl.provider_type = None
        tmpl.provider_api = None
        with pytest.raises(ValueError, match="disabled"):
            svc.select_provider_for_template(tmpl)

    def test_provider_type_selection(self) -> None:
        p = _make_provider("aws1", ptype="aws")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = "aws"
        tmpl.provider_api = None
        result = svc.select_provider_for_template(tmpl)
        assert result.provider_name == "aws1"

    def test_provider_type_raises_when_no_instances(self) -> None:
        cfg = _make_provider_config(providers=[])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = "aws"
        tmpl.provider_api = None
        with pytest.raises(ValueError, match="No enabled instances"):
            svc.select_provider_for_template(tmpl)

    def test_provider_api_selection(self) -> None:
        p = _make_provider("aws1", ptype="aws")
        cfg = _make_provider_config(providers=[p], provider_defaults={})
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = None
        tmpl.provider_api = "EC2Fleet"
        result = svc.select_provider_for_template(tmpl)
        assert result.provider_name == "aws1"

    def test_fallback_to_default_provider(self) -> None:
        p = _make_provider("aws1")
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = None
        tmpl.provider_api = None
        result = svc.select_provider_for_template(tmpl)
        assert result.provider_name == "aws1"

    def test_fallback_raises_when_no_enabled_providers(self) -> None:
        p = _make_provider("aws1", enabled=False)
        cfg = _make_provider_config(providers=[p])
        svc = _make_service(provider_config=cfg)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = None
        tmpl.provider_api = None
        with pytest.raises(ValueError, match="No enabled providers"):
            svc.select_provider_for_template(tmpl)

    def test_fallback_uses_registry_fallback_when_no_config(self) -> None:
        from orb.domain.base.results import ProviderSelectionResult

        fallback = ProviderSelectionResult(
            provider_type="aws",
            provider_name="fallback_aws",
            selection_reason="fallback",
        )
        registry = MagicMock()
        registry.get_strategy.return_value = None
        registry.get_fallback_strategy.return_value = fallback
        config_port = MagicMock()
        config_port.get_provider_config.return_value = None
        svc = ProviderSelectionService(registry=registry, config_port=config_port)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = None
        tmpl.provider_api = None
        result = svc.select_provider_for_template(tmpl)
        assert result.provider_name == "fallback_aws"

    def test_fallback_raises_when_no_config_and_no_fallback(self) -> None:
        registry = MagicMock()
        registry.get_strategy.return_value = None
        registry.get_fallback_strategy.return_value = None
        config_port = MagicMock()
        config_port.get_provider_config.return_value = None
        svc = ProviderSelectionService(registry=registry, config_port=config_port)
        tmpl = MagicMock()
        tmpl.provider_name = None
        tmpl.provider_type = None
        tmpl.provider_api = None
        with pytest.raises(ValueError, match="No provider configuration"):
            svc.select_provider_for_template(tmpl)


# ---------------------------------------------------------------------------
# _apply_load_balancing_strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancing:
    def _svc_with_policy(self, policy: str, providers: list) -> ProviderSelectionService:
        cfg = _make_provider_config(providers=providers, selection_policy=policy)
        return _make_service(provider_config=cfg)

    def test_first_available_returns_first(self) -> None:
        p1 = _make_provider("p1", priority=0)
        p2 = _make_provider("p2", priority=1)
        svc = self._svc_with_policy("FIRST_AVAILABLE", [p1, p2])
        result = svc._apply_load_balancing_strategy([p1, p2], "FIRST_AVAILABLE")
        assert result.name == "p1"

    def test_default_policy_uses_min_priority(self) -> None:
        p1 = _make_provider("p1", priority=5)
        p2 = _make_provider("p2", priority=2)
        svc = self._svc_with_policy("UNKNOWN_POLICY", [p1, p2])
        result = svc._apply_load_balancing_strategy([p1, p2], "UNKNOWN_POLICY")
        assert result.name == "p2"

    def test_health_based_returns_min_priority(self) -> None:
        p1 = _make_provider("p1", priority=3)
        p2 = _make_provider("p2", priority=1)
        svc = self._svc_with_policy("HEALTH_BASED", [p1, p2])
        result = svc._apply_load_balancing_strategy([p1, p2], "HEALTH_BASED")
        assert result.name == "p2"

    def test_weighted_round_robin_single_highest_priority(self) -> None:
        p1 = _make_provider("p1", priority=0, weight=100)
        p2 = _make_provider("p2", priority=5, weight=50)
        svc = self._svc_with_policy("WEIGHTED_ROUND_ROBIN", [p1, p2])
        result = svc._apply_load_balancing_strategy([p1, p2], "WEIGHTED_ROUND_ROBIN")
        assert result.name == "p1"

    def test_weighted_round_robin_multiple_same_priority_picks_highest_weight(self) -> None:
        p1 = _make_provider("p1", priority=0, weight=50)
        p2 = _make_provider("p2", priority=0, weight=100)
        svc = self._svc_with_policy("WEIGHTED_ROUND_ROBIN", [p1, p2])
        result = svc._apply_load_balancing_strategy([p1, p2], "WEIGHTED_ROUND_ROBIN")
        assert result.name == "p2"


# ---------------------------------------------------------------------------
# _provider_supports_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderSupportsApi:
    def test_returns_false_when_no_provider_config(self) -> None:
        svc = _make_service(provider_config=None)
        p = _make_provider("p1")
        assert svc._provider_supports_api(p, "EC2Fleet") is False

    def test_returns_true_when_api_in_effective_handlers(self) -> None:
        p = _make_provider("p1")
        p.get_effective_handlers.return_value = {"EC2Fleet": {}}
        cfg = _make_provider_config(providers=[p], provider_defaults={"aws": None})
        svc = _make_service(provider_config=cfg)
        assert svc._provider_supports_api(p, "EC2Fleet") is True

    def test_returns_true_when_api_in_capabilities(self) -> None:
        p = _make_provider("p1")
        p.get_effective_handlers.return_value = {}
        p.capabilities = ["MyAPI"]
        cfg = _make_provider_config(providers=[p], provider_defaults={})
        svc = _make_service(provider_config=cfg)
        assert svc._provider_supports_api(p, "MyAPI") is True

    def test_returns_true_via_strategy_capabilities(self) -> None:
        p = _make_provider("p1")
        p.get_effective_handlers.return_value = {}
        p.capabilities = None
        caps = MagicMock()
        caps.supported_apis = ["StrategyAPI"]
        strategy = MagicMock()
        strategy.get_capabilities.return_value = caps
        cfg = _make_provider_config(providers=[p], provider_defaults={})
        svc = _make_service(provider_config=cfg, registry_strategy=strategy)
        assert svc._provider_supports_api(p, "StrategyAPI") is True

    def test_returns_true_fallback_when_strategy_capabilities_raise(self) -> None:
        p = _make_provider("p1")
        p.get_effective_handlers.return_value = {}
        p.capabilities = None
        strategy = MagicMock()
        strategy.get_capabilities.side_effect = Exception("fail")
        cfg = _make_provider_config(providers=[p], provider_defaults={})
        svc = _make_service(provider_config=cfg, registry_strategy=strategy)
        # Falls through to default True
        assert svc._provider_supports_api(p, "AnyAPI") is True


# ---------------------------------------------------------------------------
# _provider_supports_capabilities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderSupportsCapabilities:
    def test_empty_capabilities_always_true(self) -> None:
        svc = _make_service()
        strategy = MagicMock()
        assert svc._provider_supports_capabilities(strategy, []) is True

    def test_all_capabilities_present_returns_true(self) -> None:
        svc = _make_service()
        strategy = MagicMock()
        strategy.supported_capabilities = ["cap_a", "cap_b"]
        assert svc._provider_supports_capabilities(strategy, ["cap_a", "cap_b"]) is True

    def test_missing_capability_returns_false(self) -> None:
        svc = _make_service()
        strategy = MagicMock()
        strategy.supported_capabilities = ["cap_a"]
        assert svc._provider_supports_capabilities(strategy, ["cap_a", "cap_b"]) is False
