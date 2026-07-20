"""Unit tests for ProviderPlugin abstract base class and reset_for_testing.

Tests are isolated from real providers: each test creates its own concrete
ProviderPlugin subclass with mocked satellites so no real files are imported
or registries mutated between tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import orb.providers.base.provider_plugin as _plugin_mod

ProviderPlugin = _plugin_mod.ProviderPlugin
reset_for_testing = _plugin_mod.reset_for_testing

# ---------------------------------------------------------------------------
# Test-only concrete ProviderPlugin implementations
# ---------------------------------------------------------------------------


def _make_plugin(
    name: str = "testplug",
    *,
    strategy_factory=None,
    config_factory=None,
    template_dto=None,
    cli_spec_instance=None,
    field_mapping_instance=None,
    defaults_loader_instance=None,
    example_gen_instance=None,
    settings_class=None,
    template_class=None,
    resolver=None,
    validator=None,
    strategy_cls=None,
    default_api_val=None,
) -> ProviderPlugin:
    """Build a minimal concrete ProviderPlugin subclass and return an instance."""

    class _Plugin(ProviderPlugin):
        provider_name = name

        def strategy_factory(self):
            return strategy_factory or (lambda cfg: MagicMock())

        def config_factory(self):
            return config_factory or (lambda data: {})

        def template_dto_config(self):
            return template_dto

        def cli_spec(self):
            return cli_spec_instance

        def field_mapping(self):
            return field_mapping_instance

        def defaults_loader(self):
            return defaults_loader_instance

        def template_example_generator(self, container):
            return example_gen_instance

        def provider_settings_class(self):
            return settings_class

        def template_class(self):
            return template_class

        def resolver_factory(self):
            return resolver

        def validator_factory(self):
            return validator

        def strategy_class(self):
            return strategy_cls

        def default_api(self):
            return default_api_val

    return _Plugin()


@pytest.fixture(autouse=True)
def _reset_initialized_set():
    """Clear module-level provider state before/after each test.

    Resets both the initialized-providers guard and the discovered-providers
    list in orb.providers.registration.  The latter is necessary because
    import orb.providers.registration inside register_plugin() resolves
    through the parent package attribute (orb.providers.registration), which
    bypasses sys.modules patching.  Without cleanup, test_register_plugin_*
    tests that call register_plugin() append to the real _REGISTERED_PROVIDERS
    list, polluting subsequent tests that rely on the bootstrap state.
    """
    import orb.providers.registration as _reg_mod

    providers_before = list(_reg_mod._REGISTERED_PROVIDERS)
    reset_for_testing()
    yield
    # Restore _REGISTERED_PROVIDERS to its pre-test state so any entries
    # appended during the test (e.g. "entry_plug") are removed.
    _reg_mod._REGISTERED_PROVIDERS[:] = providers_before
    reset_for_testing()


# ---------------------------------------------------------------------------
# reset_for_testing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResetForTesting:
    def test_clears_initialized_set(self):
        _plugin_mod._initialized_providers.add("whatever")
        reset_for_testing()
        assert len(_plugin_mod._initialized_providers) == 0

    def test_classmethod_removes_own_provider(self):
        plugin = _make_plugin("removable_plug")
        _plugin_mod._initialized_providers.add("removable_plug")
        type(plugin).reset_for_testing()
        assert "removable_plug" not in _plugin_mod._initialized_providers


# ---------------------------------------------------------------------------
# register_provider — type registration path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPluginRegisterProvider:
    def test_register_calls_registry_register_provider(self):
        plugin = _make_plugin("reg_plug")
        mock_registry = MagicMock()
        mock_logger = MagicMock()
        plugin.register_provider(mock_registry, mock_logger)
        mock_registry.register_provider.assert_called_once()
        call_kwargs = mock_registry.register_provider.call_args[1]
        assert call_kwargs["provider_type"] == "reg_plug"

    def test_register_with_instance_name_calls_register_instance(self):
        plugin = _make_plugin("inst_reg_plug")
        mock_registry = MagicMock()
        plugin.register_provider(mock_registry, instance_name="my_instance")
        mock_registry.register_provider_instance.assert_called_once()
        call_kwargs = mock_registry.register_provider_instance.call_args[1]
        assert call_kwargs["instance_name"] == "my_instance"

    def test_register_logs_success(self):
        plugin = _make_plugin("log_plug")
        mock_registry = MagicMock()
        mock_logger = MagicMock()
        plugin.register_provider(mock_registry, mock_logger)
        mock_logger.info.assert_called()

    def test_register_exception_is_reraised(self):
        plugin = _make_plugin("exc_plug")
        mock_registry = MagicMock()
        mock_registry.register_provider.side_effect = RuntimeError("reg failed")
        with pytest.raises(RuntimeError, match="reg failed"):
            plugin.register_provider(mock_registry)

    def test_register_exception_logs_error(self):
        plugin = _make_plugin("errlog_plug")
        mock_registry = MagicMock()
        mock_registry.register_provider.side_effect = RuntimeError("oops")
        mock_logger = MagicMock()
        with pytest.raises(RuntimeError):
            plugin.register_provider(mock_registry, mock_logger)
        mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# initialize_provider — idempotency guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPluginInitializeProvider:
    def test_initialize_adds_to_initialized_set(self):
        plugin = _make_plugin("init_once_plug")
        plugin.initialize_provider()
        assert "init_once_plug" in _plugin_mod._initialized_providers

    def test_second_call_is_no_op(self):
        plugin = _make_plugin("idem_plug")
        # Register a spy on _do_initialize
        call_count = []
        real_do_init = plugin._do_initialize
        plugin._do_initialize = lambda logger=None: call_count.append(1) or real_do_init(logger)
        plugin.initialize_provider()
        plugin.initialize_provider()  # second call
        assert len(call_count) == 1  # only ran once

    def test_failure_does_not_add_to_set(self):
        plugin = _make_plugin("fail_init_plug")

        def _exploding_do_init(logger=None):
            raise RuntimeError("init exploded")

        plugin._do_initialize = _exploding_do_init
        with pytest.raises(RuntimeError):
            plugin.initialize_provider()
        assert "fail_init_plug" not in _plugin_mod._initialized_providers

    def test_failure_logs_error(self):
        plugin = _make_plugin("faillog_plug")
        mock_logger = MagicMock()
        plugin._do_initialize = lambda logger=None: (_ for _ in ()).throw(RuntimeError("boom init"))
        with pytest.raises(RuntimeError):
            plugin.initialize_provider(logger=mock_logger)
        mock_logger.error.assert_called()

    def test_provider_settings_registered_when_settings_class_present(self):
        class FakeSettings:
            pass

        plugin = _make_plugin("settings_plug", settings_class=FakeSettings)

        fake_registry = MagicMock()
        fake_registry.get_or_none.return_value = None
        fake_module = MagicMock(ProviderSettingsRegistry=fake_registry)

        # The registry is imported function-locally inside initialize_provider
        # via `from orb.config.schemas.provider_settings_registry import
        # ProviderSettingsRegistry`, so patch sys.modules for that path.
        with patch.dict(
            "sys.modules",
            {"orb.config.schemas.provider_settings_registry": fake_module},
        ):
            plugin.initialize_provider()

        fake_registry.register_provider_settings.assert_called_once_with(
            "settings_plug", FakeSettings
        )
        assert "settings_plug" in _plugin_mod._initialized_providers

    def test_initialize_with_template_class_and_factory(self):
        class FakeTplClass:
            pass

        plugin = _make_plugin("tpl_class_plug", template_class=FakeTplClass)
        mock_factory = MagicMock()
        plugin.initialize_provider(template_factory=mock_factory)
        mock_factory.register_provider_template_class.assert_called_once_with(
            "tpl_class_plug", FakeTplClass
        )

    def test_template_factory_registration_exception_is_caught(self):
        class FakeTplClass:
            pass

        plugin = _make_plugin("tpl_err_plug", template_class=FakeTplClass)
        mock_factory = MagicMock()
        mock_factory.register_provider_template_class.side_effect = RuntimeError("tpl fail")
        # Should NOT raise — the exception is swallowed
        plugin.initialize_provider(template_factory=mock_factory)
        assert "tpl_err_plug" in _plugin_mod._initialized_providers

    def test_success_logs_info(self):
        plugin = _make_plugin("success_log_plug")
        mock_logger = MagicMock()
        plugin.initialize_provider(logger=mock_logger)
        mock_logger.info.assert_called()


# ---------------------------------------------------------------------------
# register_services_with_di
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPluginRegisterServicesWithDI:
    def test_calls_register_additional_services(self):
        plugin = _make_plugin("di_plug")
        mock_container = MagicMock()
        # Ensure get(LoggingPort) doesn't fail
        mock_container.get.return_value = MagicMock()

        additional_called = []
        plugin.register_additional_services = lambda container, logger=None: (
            additional_called.append(1)
        )
        plugin.register_services_with_di(mock_container)
        assert len(additional_called) == 1

    def test_template_example_generator_registered_when_present(self):
        gen = MagicMock()
        plugin = _make_plugin("gen_di_plug", example_gen_instance=gen)
        mock_container = MagicMock()
        mock_container.get.return_value = MagicMock()

        fake_registry = MagicMock()
        fake_module = MagicMock(TemplateExampleGeneratorRegistry=fake_registry)

        with patch.dict(
            "sys.modules",
            {
                "orb.infrastructure.registry.template_example_generator_registry": fake_module,
            },
        ):
            plugin.register_services_with_di(mock_container)

        # The generator instance from template_example_generator() must be
        # registered under the provider name.
        fake_registry.register.assert_called_once_with("gen_di_plug", gen)

    def test_exception_in_register_services_logs_warning(self):
        plugin = _make_plugin("exc_di_plug")
        mock_container = MagicMock()
        mock_logger = MagicMock()
        mock_container.get.return_value = mock_logger
        plugin.register_additional_services = MagicMock(side_effect=RuntimeError("di boom"))
        # Should NOT propagate
        plugin.register_services_with_di(mock_container)
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# register_plugin (classmethod entry-point)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPluginRegisterPlugin:
    def test_register_plugin_calls_register_provider(self):
        class EntryPlugin(ProviderPlugin):
            provider_name = "entry_plug"

            def strategy_factory(self):
                return lambda cfg: MagicMock()

            def config_factory(self):
                return lambda data: {}

            def template_dto_config(self):
                return None

            def cli_spec(self):
                return None

            def field_mapping(self):
                return None

            def defaults_loader(self):
                return None

            def template_example_generator(self, container):
                return None

        mock_registry = MagicMock()
        with patch(
            "orb.providers.base.provider_plugin.ProviderPlugin.register_provider"
        ) as mock_reg:
            with patch(
                "orb.providers.base.provider_plugin.get_provider_registry",
                return_value=mock_registry,
                create=True,
            ):
                # Patch the module import used inside register_plugin
                with patch.dict(
                    "sys.modules",
                    {"orb.providers.registration": MagicMock(_REGISTERED_PROVIDERS=[])},
                ):
                    EntryPlugin.register_plugin()
        mock_reg.assert_called_once()

    def test_register_plugin_raises_when_provider_name_empty(self):
        class NoNamePlugin(ProviderPlugin):
            provider_name = ""

            def strategy_factory(self):
                return lambda cfg: MagicMock()

            def config_factory(self):
                return lambda data: {}

            def template_dto_config(self):
                return None

            def cli_spec(self):
                return None

            def field_mapping(self):
                return None

            def defaults_loader(self):
                return None

            def template_example_generator(self, container):
                return None

        with pytest.raises(ValueError, match="provider_name must be set"):
            with patch("orb.providers.base.provider_plugin.get_provider_registry", create=True):
                NoNamePlugin.register_plugin()


# ---------------------------------------------------------------------------
# Optional hook defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPluginOptionalHooks:
    def test_resolver_factory_default_is_none(self):
        plugin = _make_plugin("hooks_plug")
        assert plugin.resolver_factory() is None

    def test_validator_factory_default_is_none(self):
        plugin = _make_plugin("hooks_plug2")
        assert plugin.validator_factory() is None

    def test_strategy_class_default_is_none(self):
        plugin = _make_plugin("hooks_plug3")
        assert plugin.strategy_class() is None

    def test_default_api_default_is_none(self):
        plugin = _make_plugin("hooks_plug4")
        assert plugin.default_api() is None

    def test_provider_settings_class_default_is_none(self):
        plugin = _make_plugin("hooks_plug5")
        assert plugin.provider_settings_class() is None

    def test_template_class_default_is_none(self):
        plugin = _make_plugin("hooks_plug6")
        assert plugin.template_class() is None

    def test_register_auth_strategies_is_noop(self):
        plugin = _make_plugin("auth_plug")
        plugin.register_auth_strategies()  # must not raise

    def test_register_additional_services_is_noop(self):
        plugin = _make_plugin("addl_plug")
        plugin.register_additional_services(MagicMock())  # must not raise

    def test_do_initialize_is_noop(self):
        plugin = _make_plugin("do_init_plug")
        plugin._do_initialize()  # must not raise
