"""Unit tests for orb.cli.factories.system_command_factory.SystemCommandFactory.

Verifies that each factory method produces the correct query/command
with the right field values.
"""

from __future__ import annotations

import pytest

from orb.cli.factories.system_command_factory import SystemCommandFactory


@pytest.fixture
def factory() -> SystemCommandFactory:
    return SystemCommandFactory()


@pytest.mark.unit
class TestCreateReloadProviderConfigCommand:
    def test_config_path_set(self, factory):
        cmd = factory.create_reload_provider_config_command(config_path="/etc/orb/config.yaml")
        assert cmd.config_path == "/etc/orb/config.yaml"

    def test_config_path_defaults_to_none(self, factory):
        cmd = factory.create_reload_provider_config_command()
        assert cmd.config_path is None


@pytest.mark.unit
class TestCreateRefreshTemplatesCommand:
    def test_returns_command_instance(self, factory):
        from orb.application.commands.system import RefreshTemplatesCommand

        cmd = factory.create_refresh_templates_command()
        assert isinstance(cmd, RefreshTemplatesCommand)


@pytest.mark.unit
class TestCreateGetSystemStatusQuery:
    def test_defaults(self, factory):
        q = factory.create_get_system_status_query()
        assert q.include_provider_health is True

    def test_include_metrics_verbose_flag(self, factory):
        q = factory.create_get_system_status_query(include_metrics=True)
        assert q.verbose is True

    def test_include_config_verbose_flag(self, factory):
        q = factory.create_get_system_status_query(include_config=True)
        assert q.verbose is True

    def test_all_false_verbose_is_false(self, factory):
        q = factory.create_get_system_status_query(
            include_health=True, include_metrics=False, include_config=False
        )
        assert q.verbose is False


@pytest.mark.unit
class TestCreateGetProviderConfigQuery:
    def test_defaults(self, factory):
        q = factory.create_get_provider_config_query()
        assert q.provider_name is None
        assert q.include_sensitive is False

    def test_provider_name_set(self, factory):
        q = factory.create_get_provider_config_query(provider_name="aws-prod")
        assert q.provider_name == "aws-prod"

    def test_include_sensitive_set(self, factory):
        q = factory.create_get_provider_config_query(include_sensitive=True)
        assert q.include_sensitive is True


@pytest.mark.unit
class TestCreateGetProviderMetricsQuery:
    def test_defaults(self, factory):
        from orb.application.provider.queries import GetProviderMetricsQuery

        q = factory.create_get_provider_metrics_query()
        assert isinstance(q, GetProviderMetricsQuery)
        assert q.timeframe == "1h"

    def test_timeframe_set(self, factory):
        q = factory.create_get_provider_metrics_query(timeframe="24h")
        assert q.timeframe == "24h"

    def test_provider_name_set(self, factory):
        q = factory.create_get_provider_metrics_query(provider_name="aws-dev")
        assert q.provider_name == "aws-dev"


@pytest.mark.unit
class TestCreateValidateProviderConfigQuery:
    def test_verbose_default_false(self, factory):
        q = factory.create_validate_provider_config_query()
        assert q.verbose is False

    def test_verbose_true(self, factory):
        q = factory.create_validate_provider_config_query(verbose=True)
        assert q.verbose is True


@pytest.mark.unit
class TestCreateGetConfigurationQuery:
    def test_key_set(self, factory):
        q = factory.create_get_configuration_query(key="database.host")
        assert q.key == "database.host"

    def test_default_passed_through(self, factory):
        q = factory.create_get_configuration_query(key="x", default="fallback")
        assert q.default == "fallback"

    def test_default_none_when_not_provided(self, factory):
        q = factory.create_get_configuration_query(key="x")
        assert q.default is None


@pytest.mark.unit
class TestCreateSetConfigurationCommand:
    def test_key_and_value_set(self, factory):
        cmd = factory.create_set_configuration_command(key="log.level", value="DEBUG")
        assert cmd.key == "log.level"
        assert cmd.value == "DEBUG"


@pytest.mark.unit
class TestCreateGetSystemConfigQuery:
    def test_verbose_default_false(self, factory):
        q = factory.create_get_system_config_query()
        assert q.verbose is False

    def test_verbose_set_true(self, factory):
        q = factory.create_get_system_config_query(verbose=True)
        assert q.verbose is True
