"""Tests for TemplateDefaultsService — registry delegation and default-API chain."""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.application.services.template_defaults_service import TemplateDefaultsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    provider_defaults: dict | None = None,
    global_config: dict | None = None,
    provider_registry: object | None = None,
) -> TemplateDefaultsService:
    """Build a TemplateDefaultsService with controllable config mocks."""
    logger = MagicMock()
    config_manager = MagicMock()

    # Mimic get_template_config() returning a dict (no model_dump needed).
    config_manager.get_template_config.return_value = global_config or {}

    # Mimic get_provider_config().provider_defaults.get(provider_type).
    if provider_defaults is not None:
        mock_provider_config = MagicMock()
        mock_provider_config.provider_defaults = provider_defaults
        mock_provider_config.providers = []
    else:
        mock_provider_config = MagicMock(provider_defaults={}, providers=[])
    config_manager.get_provider_config.return_value = mock_provider_config

    return TemplateDefaultsService(
        config_manager=config_manager,
        logger=logger,
        provider_registry=provider_registry,  # type: ignore[arg-type]
    )


def _make_provider_defaults_with_api(provider_api: str | None) -> MagicMock:
    """Create a provider_defaults mock with template_defaults.provider_api set."""
    defaults_obj = MagicMock()
    template_defaults: dict = {}
    if provider_api is not None:
        template_defaults["provider_api"] = provider_api
    defaults_obj.template_defaults = template_defaults
    return defaults_obj


# ---------------------------------------------------------------------------
# _get_provider_type_defaults — registry delegation
# ---------------------------------------------------------------------------


class TestGetProviderTypeDefaultsDelegatesRegistry:
    """When template_defaults has no provider_api, registry is consulted."""

    def test_registry_default_api_fills_missing_provider_api(self):
        """Registry.get_default_api() result is injected when template_defaults lacks provider_api."""
        registry = MagicMock()
        registry.get_default_api.return_value = "EC2Fleet"

        provider_defaults_obj = _make_provider_defaults_with_api(None)
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=registry,
        )

        result = svc._get_provider_type_defaults("aws")

        assert result.get("provider_api") == "EC2Fleet"
        registry.get_default_api.assert_called_once_with("aws")

    def test_registry_not_called_when_template_defaults_has_provider_api(self):
        """Registry is NOT consulted when template_defaults already has provider_api."""
        registry = MagicMock()
        registry.get_default_api.return_value = "SpotFleet"

        provider_defaults_obj = _make_provider_defaults_with_api("EC2Fleet")
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=registry,
        )

        result = svc._get_provider_type_defaults("aws")

        assert result.get("provider_api") == "EC2Fleet"
        registry.get_default_api.assert_not_called()

    def test_no_registry_injected_returns_empty_provider_api(self):
        """Without registry, if template_defaults has no provider_api, result has none."""
        provider_defaults_obj = _make_provider_defaults_with_api(None)
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=None,
        )

        result = svc._get_provider_type_defaults("aws")

        assert "provider_api" not in result

    def test_registry_returns_none_leaves_provider_api_absent(self):
        """When registry returns None, provider_api is not added to result."""
        registry = MagicMock()
        registry.get_default_api.return_value = None

        provider_defaults_obj = _make_provider_defaults_with_api(None)
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=registry,
        )

        result = svc._get_provider_type_defaults("aws")

        assert "provider_api" not in result

    def test_unknown_provider_type_returns_empty_dict(self):
        """A provider type with no registration returns {}."""
        registry = MagicMock()
        registry.get_default_api.return_value = None
        svc = _make_service(provider_defaults={}, provider_registry=registry)

        result = svc._get_provider_type_defaults("unknown-provider")

        assert result == {}


# ---------------------------------------------------------------------------
# TemplateDefaultsService init — provider_registry optional
# ---------------------------------------------------------------------------


class TestTemplateDefaultsServiceInit:
    def test_no_registry_param_accepted(self):
        """Service can be instantiated without provider_registry (backward compat)."""
        logger = MagicMock()
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.return_value = MagicMock(
            provider_defaults={}, providers=[]
        )

        svc = TemplateDefaultsService(config_manager=config_manager, logger=logger)

        assert svc.provider_registry is None

    def test_registry_stored_on_instance(self):
        """Injected provider_registry is accessible via attribute."""
        registry = MagicMock()
        svc = _make_service(provider_registry=registry)
        assert svc.provider_registry is registry
