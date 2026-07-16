"""Unit tests for :class:`orb.providers.k8s.provider_plugin.K8sPlugin`.

Covers:
- All mandatory satellites populated.
- K8sRetryClassifier registered by _do_initialize.
- inbound_auth_enabled flag passed through register_auth_strategies.
- register_services_with_di registers K8sNativeSpecService.
- Idempotency.
- register_k8s_plugin thin wrapper delegates and is idempotent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import orb.providers.k8s.registration as _k8s_reg
from orb.providers.base.provider_plugin import reset_for_testing
from orb.providers.k8s.provider_plugin import K8sPlugin

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_provider_state():
    reset_for_testing()
    yield
    reset_for_testing()


@pytest.fixture()
def plugin() -> K8sPlugin:
    return K8sPlugin()


# ---------------------------------------------------------------------------
# Satellites populated
# ---------------------------------------------------------------------------


class TestSatellitesPopulated:
    """K8sPlugin must return non-None values for every mandatory satellite."""

    def test_strategy_factory_is_callable(self, plugin: K8sPlugin) -> None:
        factory = plugin.strategy_factory()
        assert callable(factory), "strategy_factory must return a callable"

    def test_config_factory_is_callable(self, plugin: K8sPlugin) -> None:
        factory = plugin.config_factory()
        assert callable(factory), "config_factory must return a callable"

    def test_template_dto_config_is_class(self, plugin: K8sPlugin) -> None:
        dto_cls = plugin.template_dto_config()
        assert dto_cls is not None, "template_dto_config must return a class"
        assert isinstance(dto_cls, type)

    def test_cli_spec_is_instance(self, plugin: K8sPlugin) -> None:
        spec = plugin.cli_spec()
        assert spec is not None, "cli_spec must return a non-None instance"

    def test_field_mapping_is_instance(self, plugin: K8sPlugin) -> None:
        mapping = plugin.field_mapping()
        assert mapping is not None, "field_mapping must return a non-None instance"

    def test_defaults_loader_is_instance(self, plugin: K8sPlugin) -> None:
        loader = plugin.defaults_loader()
        assert loader is not None, "defaults_loader must return a non-None instance"

    def test_resolver_factory_is_callable(self, plugin: K8sPlugin) -> None:
        factory = plugin.resolver_factory()
        assert callable(factory), "resolver_factory must return a callable"

    def test_validator_factory_is_callable(self, plugin: K8sPlugin) -> None:
        factory = plugin.validator_factory()
        assert callable(factory), "validator_factory must return a callable"

    def test_strategy_class_is_class(self, plugin: K8sPlugin) -> None:
        cls = plugin.strategy_class()
        assert cls is not None
        assert isinstance(cls, type)

    def test_provider_settings_class_is_class(self, plugin: K8sPlugin) -> None:
        settings_cls = plugin.provider_settings_class()
        assert settings_cls is not None
        assert isinstance(settings_cls, type)

    def test_provider_name(self, plugin: K8sPlugin) -> None:
        assert plugin.provider_name == "k8s"

    def test_default_api(self, plugin: K8sPlugin) -> None:
        assert plugin.default_api() == "Pod"


# ---------------------------------------------------------------------------
# inbound_auth_enabled flag
# ---------------------------------------------------------------------------


class TestInboundAuthEnabled:
    def test_default_is_false(self) -> None:
        p = K8sPlugin()
        assert p._inbound_auth_enabled is False

    def test_can_be_set_true(self) -> None:
        p = K8sPlugin(inbound_auth_enabled=True)
        assert p._inbound_auth_enabled is True

    def test_auth_strategies_passes_flag(self) -> None:
        p = K8sPlugin(inbound_auth_enabled=True)
        with patch("orb.providers.k8s.registration.register_k8s_auth_strategies") as mock_fn:
            p.register_auth_strategies()

        mock_fn.assert_called_once_with(None, inbound_auth_enabled=True)

    def test_auth_strategies_passes_false_flag(self) -> None:
        p = K8sPlugin(inbound_auth_enabled=False)
        with patch("orb.providers.k8s.registration.register_k8s_auth_strategies") as mock_fn:
            p.register_auth_strategies()

        mock_fn.assert_called_once_with(None, inbound_auth_enabled=False)


# ---------------------------------------------------------------------------
# _do_initialize registers K8sRetryClassifier
# ---------------------------------------------------------------------------


class TestDoInitialize:
    """K8sPlugin._do_initialize must register K8sRetryClassifier."""

    def test_registers_retry_classifier(self, plugin: K8sPlugin) -> None:
        mock_classifier_cls = MagicMock()
        mock_classifier_instance = MagicMock()
        mock_classifier_cls.return_value = mock_classifier_instance

        with (
            patch(
                "orb.infrastructure.resilience.retry_classifier_registry.register_retry_classifier"
            ) as mock_register,
            patch(
                "orb.providers.k8s.resilience.retry_classifier.K8sRetryClassifier",
                mock_classifier_cls,
            ),
        ):
            plugin._do_initialize()

        mock_register.assert_called_once_with(mock_classifier_instance)

    def test_do_initialize_silences_import_error(self, plugin: K8sPlugin) -> None:
        """_do_initialize must not propagate ImportError."""
        with patch(
            "orb.infrastructure.resilience.retry_classifier_registry.register_retry_classifier",
            side_effect=ImportError("kubernetes not installed"),
        ):
            # Must not raise
            plugin._do_initialize()


# ---------------------------------------------------------------------------
# register_additional_services registers K8sNativeSpecService
# ---------------------------------------------------------------------------


class TestRegisterAdditionalServices:
    """register_additional_services wires K8sTemplateAdapter and K8sNativeSpecService."""

    def test_registers_template_adapter(self, plugin: K8sPlugin) -> None:
        mock_container = MagicMock()
        mock_logger = MagicMock()
        mock_container.get.return_value = mock_logger

        mock_adapter_factory = MagicMock()

        with (
            patch("orb.providers.k8s.infrastructure.adapters.template_adapter.K8sTemplateAdapter"),
            patch(
                "orb.providers.k8s.infrastructure.adapters.template_adapter.create_k8s_template_adapter",
                mock_adapter_factory,
            ),
            patch("orb.domain.base.ports.template_adapter_port.TemplateAdapterPort"),
        ):
            plugin.register_additional_services(mock_container)

        # register_singleton called for K8sTemplateAdapter and TemplateAdapterPort
        assert mock_container.register_singleton.call_count >= 2


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """K8sPlugin.initialize_provider must only run satellites once."""

    def test_initialize_provider_is_idempotent(self, plugin: K8sPlugin) -> None:
        call_count: list[int] = [0]
        original_auth = plugin.register_auth_strategies

        def _counting_auth(logger: Any = None) -> None:
            call_count[0] += 1
            return original_auth(logger)

        plugin.register_auth_strategies = _counting_auth  # type: ignore[method-assign]

        with (
            patch(
                "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry.get_or_none",
                return_value=None,
            ),
            patch(
                "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry.register_provider_settings"
            ),
            patch(
                "orb.infrastructure.registry.template_extension_registry.TemplateExtensionRegistry.has_extension",
                return_value=False,
            ),
            patch(
                "orb.infrastructure.registry.template_extension_registry.TemplateExtensionRegistry.register_extension"
            ),
            patch("orb.infrastructure.registry.cli_spec_registry.CLISpecRegistry.register"),
            patch(
                "orb.infrastructure.scheduler.hostfactory.field_mapping_registry.FieldMappingRegistry.register"
            ),
            patch(
                "orb.providers.registry.defaults_loader_registry.DefaultsLoaderRegistry.register"
            ),
            patch.object(plugin, "_do_initialize"),
        ):
            plugin.initialize_provider()
            plugin.initialize_provider()

        assert call_count[0] == 1, "register_auth_strategies must only run once"


# ---------------------------------------------------------------------------
# register_k8s_plugin thin wrapper
# ---------------------------------------------------------------------------


class TestRegisterK8sPluginWrapper:
    """The backward-compat register_k8s_plugin must delegate and be idempotent."""

    @pytest.fixture(autouse=True)
    def _reset_k8s_sentinel(self):
        _k8s_reg._REGISTERED_PROVIDERS.clear()
        yield
        _k8s_reg._REGISTERED_PROVIDERS.clear()

    def test_delegates_to_plugin_register_provider(self) -> None:
        mock_registry = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            _k8s_reg.register_k8s_plugin()

        mock_registry.register_provider.assert_called_once()
        call_kwargs = mock_registry.register_provider.call_args.kwargs
        assert call_kwargs["provider_type"] == "k8s"

    def test_idempotent(self) -> None:
        mock_registry = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            _k8s_reg.register_k8s_plugin()
            _k8s_reg.register_k8s_plugin()

        mock_registry.register_provider.assert_called_once()

    def test_appends_sentinel(self) -> None:
        mock_registry = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            assert "k8s" not in _k8s_reg._REGISTERED_PROVIDERS
            _k8s_reg.register_k8s_plugin()
            assert "k8s" in _k8s_reg._REGISTERED_PROVIDERS
