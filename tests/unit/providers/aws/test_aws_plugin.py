"""Unit tests for :class:`orb.providers.aws.provider_plugin.AWSPlugin`.

Covers:
- All 5 core satellites populated (strategy_factory, config_factory,
  template_dto_config, cli_spec, field_mapping) plus defaults_loader and
  template_example_generator.
- :meth:`register_services_with_di` delegates to the existing function
  and preserves the try/except-logger.warning wrapper.
- :meth:`_do_initialize` registers dynamodb and aurora storage.
- :class:`AWSPlugin` is idempotent: calling initialize_provider twice only
  runs satellites once.
- ``register_aws_plugin`` thin wrapper delegates to ``_aws_plugin`` and
  remains idempotent.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import orb.providers.aws.registration as _aws_reg
from orb.providers.aws.provider_plugin import AWSPlugin
from orb.providers.base.provider_plugin import reset_for_testing

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_provider_state():
    reset_for_testing()
    yield
    reset_for_testing()


@pytest.fixture()
def plugin() -> AWSPlugin:
    return AWSPlugin()


# ---------------------------------------------------------------------------
# All 5 core satellites populated
# ---------------------------------------------------------------------------


class TestSatellitesPopulated:
    """AWSPlugin must return non-None values for every mandatory satellite."""

    def test_strategy_factory_is_callable(self, plugin: AWSPlugin) -> None:
        factory = plugin.strategy_factory()
        assert callable(factory), "strategy_factory must return a callable"

    def test_config_factory_is_callable(self, plugin: AWSPlugin) -> None:
        factory = plugin.config_factory()
        assert callable(factory), "config_factory must return a callable"

    def test_template_dto_config_is_class(self, plugin: AWSPlugin) -> None:
        dto_cls = plugin.template_dto_config()
        assert dto_cls is not None, "template_dto_config must return a class"
        assert isinstance(dto_cls, type), "template_dto_config must be a class (not an instance)"

    def test_cli_spec_is_instance(self, plugin: AWSPlugin) -> None:
        spec = plugin.cli_spec()
        assert spec is not None, "cli_spec must return a non-None instance"

    def test_field_mapping_is_instance(self, plugin: AWSPlugin) -> None:
        mapping = plugin.field_mapping()
        assert mapping is not None, "field_mapping must return a non-None instance"

    def test_defaults_loader_is_instance(self, plugin: AWSPlugin) -> None:
        loader = plugin.defaults_loader()
        assert loader is not None, "defaults_loader must return a non-None instance"

    def test_resolver_factory_is_callable(self, plugin: AWSPlugin) -> None:
        factory = plugin.resolver_factory()
        assert callable(factory), "resolver_factory must return a callable"

    def test_validator_factory_is_callable(self, plugin: AWSPlugin) -> None:
        factory = plugin.validator_factory()
        assert callable(factory), "validator_factory must return a callable"

    def test_strategy_class_is_class(self, plugin: AWSPlugin) -> None:
        cls = plugin.strategy_class()
        assert cls is not None, "strategy_class must return a class"
        assert isinstance(cls, type), "strategy_class must be a type"

    def test_provider_settings_class_is_class(self, plugin: AWSPlugin) -> None:
        settings_cls = plugin.provider_settings_class()
        assert settings_cls is not None, "provider_settings_class must return a class"
        assert isinstance(settings_cls, type)

    def test_provider_name(self, plugin: AWSPlugin) -> None:
        assert plugin.provider_name == "aws"

    def test_template_example_generator_returns_value_when_container_available(
        self, plugin: AWSPlugin
    ) -> None:
        """template_example_generator returns a non-None instance when DI container available."""
        mock_container = MagicMock()
        mock_logger = MagicMock()
        mock_container.get.return_value = mock_logger

        result = plugin.template_example_generator(mock_container)
        assert result is not None, "template_example_generator must return a generator adapter"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """AWSPlugin.initialize_provider must only run satellites once."""

    def test_initialize_provider_is_idempotent(self, plugin: AWSPlugin) -> None:
        """Calling initialize_provider twice must not double-register satellites."""
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
            plugin.initialize_provider()  # second call

        assert call_count[0] == 1, "register_auth_strategies must only run once"

    def test_initialized_provider_in_guard_set(self) -> None:
        """After a successful initialize_provider, the guard set contains 'aws'."""
        from orb.providers.base.provider_plugin import _initialized_providers

        plugin = AWSPlugin()
        assert "aws" not in _initialized_providers

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
            patch.object(plugin, "register_auth_strategies"),
            patch.object(plugin, "_do_initialize"),
        ):
            plugin.initialize_provider()

        assert "aws" in _initialized_providers


# ---------------------------------------------------------------------------
# _do_initialize registers storage
# ---------------------------------------------------------------------------


class TestDoInitialize:
    """AWSPlugin._do_initialize must register dynamodb and aurora storage."""

    def test_do_initialize_registers_dynamodb_and_aurora(self, plugin: AWSPlugin) -> None:
        mock_registry = MagicMock()
        mock_registry.is_registered.return_value = False

        with (
            patch(
                "orb.infrastructure.storage.registry.get_storage_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.providers.aws.storage.registration.register_dynamodb_storage"
            ) as mock_dynamo,
            patch("orb.providers.aws.storage.registration.register_aurora_storage") as mock_aurora,
        ):
            plugin._do_initialize()

        mock_dynamo.assert_called_once_with(mock_registry, None)
        mock_aurora.assert_called_once_with(mock_registry, None)

    def test_do_initialize_skips_if_already_registered(self, plugin: AWSPlugin) -> None:
        mock_registry = MagicMock()
        mock_registry.is_registered.return_value = True  # already registered

        with (
            patch(
                "orb.infrastructure.storage.registry.get_storage_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.providers.aws.storage.registration.register_dynamodb_storage"
            ) as mock_dynamo,
            patch("orb.providers.aws.storage.registration.register_aurora_storage") as mock_aurora,
        ):
            plugin._do_initialize()

        mock_dynamo.assert_not_called()
        mock_aurora.assert_not_called()

    def test_do_initialize_silences_import_error(self, plugin: AWSPlugin) -> None:
        """_do_initialize must not propagate ImportError (optional dependency guard)."""
        with patch(
            "orb.infrastructure.storage.registry.get_storage_registry",
            side_effect=ImportError("boto3 not installed"),
        ):
            # Must not raise
            plugin._do_initialize()


# ---------------------------------------------------------------------------
# register_services_with_di delegates to existing function
# ---------------------------------------------------------------------------


class TestRegisterServicesWithDI:
    """AWSPlugin.register_services_with_di must delegate to register_aws_services_with_di."""

    def test_delegates_to_existing_function(self, plugin: AWSPlugin) -> None:
        mock_container = MagicMock()

        with patch("orb.providers.aws.registration.register_aws_services_with_di") as mock_fn:
            plugin.register_services_with_di(mock_container)

        mock_fn.assert_called_once_with(mock_container)


# ---------------------------------------------------------------------------
# register_aws_plugin thin wrapper
# ---------------------------------------------------------------------------


class TestRegisterAwsPluginWrapper:
    """The backward-compat register_aws_plugin must delegate to _aws_plugin and be idempotent."""

    @pytest.fixture(autouse=True)
    def _reset_aws_sentinel(self):
        _aws_reg._REGISTERED_PROVIDERS.clear()
        yield
        _aws_reg._REGISTERED_PROVIDERS.clear()

    def test_delegates_to_plugin_register_provider(self) -> None:
        mock_registry = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            _aws_reg.register_aws_plugin()

        mock_registry.register_provider.assert_called_once()
        call_kwargs = mock_registry.register_provider.call_args.kwargs
        assert call_kwargs["provider_type"] == "aws"

    def test_idempotent(self) -> None:
        mock_registry = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            _aws_reg.register_aws_plugin()
            _aws_reg.register_aws_plugin()

        mock_registry.register_provider.assert_called_once()

    def test_appends_sentinel(self) -> None:
        mock_registry = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            assert "aws" not in _aws_reg._REGISTERED_PROVIDERS
            _aws_reg.register_aws_plugin()
            assert "aws" in _aws_reg._REGISTERED_PROVIDERS
