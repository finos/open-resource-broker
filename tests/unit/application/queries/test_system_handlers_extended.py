"""Unit tests for application/queries/system_handlers.py — extended coverage."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.dto.queries import ValidateMCPQuery, ValidateStorageQuery
from orb.application.dto.system import (
    ConfigurationSectionResponse,
    ProviderConfigDTO,
    SystemStatusDTO,
    ValidationResultDTO,
)
from orb.application.queries.system import (
    GetConfigurationSectionQuery,
    GetProviderConfigQuery,
    GetSystemConfigQuery,
    GetSystemStatusQuery,
    ValidateProviderConfigQuery,
)
from orb.application.queries.system_handlers import (
    GetConfigurationSectionHandler,
    GetProviderConfigHandler,
    GetSystemConfigHandler,
    GetSystemStatusHandler,
    ValidateMCPHandler,
    ValidateProviderConfigHandler,
    ValidateStorageHandler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


def _make_timestamp_service():
    svc = MagicMock()
    svc.format_for_display.return_value = "2026-01-01"
    return svc


def _make_system_info():
    svc = MagicMock()
    svc.get_uptime_seconds.return_value = 1000.0
    svc.get_memory_usage_mb.return_value = 256.0
    svc.get_cpu_usage_percent.return_value = 10.0
    svc.get_disk_usage_percent.return_value = 50.0
    svc.get_package_version.return_value = "1.0.0"
    svc.get_env.return_value = None
    svc.get_file_mtime.return_value = 0.0
    svc.path_exists.return_value = False
    return svc


def _make_container(config_raises=False, config_value=None):
    container = MagicMock()
    cfg_mgr = MagicMock()

    if config_raises:
        container.get.side_effect = RuntimeError("config not found")
    else:
        container.get.return_value = cfg_mgr
        if config_value is not None:
            cfg_mgr.get.return_value = config_value
        else:
            cfg_mgr.get.return_value = {}

    return container, cfg_mgr


def _make_uow_factory():
    uow = MagicMock()
    uow.requests.find_all.return_value = []
    uow.machines.find_all.return_value = []

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


# ---------------------------------------------------------------------------
# GetConfigurationSectionHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConfigurationSectionHandler:
    @pytest.mark.asyncio
    async def test_returns_section_config(self):
        container, cfg = _make_container()
        cfg.get.return_value = {"key": "val"}
        h = GetConfigurationSectionHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
        )
        q = GetConfigurationSectionQuery(section="storage")
        result = await h.execute_query(q)
        assert isinstance(result, ConfigurationSectionResponse)
        assert result.section == "storage"
        assert result.found is True

    @pytest.mark.asyncio
    async def test_returns_not_found_when_empty(self):
        container, cfg = _make_container()
        cfg.get.return_value = {}
        h = GetConfigurationSectionHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
        )
        q = GetConfigurationSectionQuery(section="missing_section")
        result = await h.execute_query(q)
        assert result.found is False

    @pytest.mark.asyncio
    async def test_non_dict_config_returns_empty(self):
        container, cfg = _make_container()
        cfg.get.return_value = "not-a-dict"
        h = GetConfigurationSectionHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
        )
        q = GetConfigurationSectionQuery(section="x")
        result = await h.execute_query(q)
        assert result.config == {}


# ---------------------------------------------------------------------------
# GetProviderConfigHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderConfigHandler:
    def _build_handler(self, active_providers=None, config_file=None, last_updated=None):
        container, cfg = _make_container()
        provider_config = MagicMock()
        if active_providers is not None:
            provider_config.get_active_providers.return_value = active_providers
        else:
            p = MagicMock()
            p.name = "aws"
            provider_config.get_active_providers.return_value = [p]
        mode = MagicMock()
        mode.value = "strategy"
        provider_config.get_mode.return_value = mode

        cfg.get_provider_config.return_value = provider_config
        sources = {"primary_source": "file", "config_file": config_file}
        cfg.get_configuration_sources.return_value = sources

        system_info = _make_system_info()
        if last_updated:
            system_info.get_file_mtime.return_value = last_updated

        return GetProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service(),
            system_info=system_info,
        )

    @pytest.mark.asyncio
    async def test_returns_provider_config_dto(self):
        h = self._build_handler()
        q = GetProviderConfigQuery()
        result = await h.execute_query(q)
        assert isinstance(result, ProviderConfigDTO)
        assert result.provider_mode == "strategy"
        assert result.provider_count == 1

    @pytest.mark.asyncio
    async def test_no_active_providers_returns_none_default(self):
        h = self._build_handler(active_providers=[])
        q = GetProviderConfigQuery()
        result = await h.execute_query(q)
        assert result.default_provider is None
        assert result.provider_count == 0

    @pytest.mark.asyncio
    async def test_config_file_mtime_fetched_when_present(self):
        h = self._build_handler(config_file="/etc/orb/config.yml", last_updated=1.0)
        q = GetProviderConfigQuery()
        result = await h.execute_query(q)
        assert result.last_updated is not None

    @pytest.mark.asyncio
    async def test_oserror_on_mtime_leaves_last_updated_none(self):
        container, cfg = _make_container()
        p = MagicMock()
        p.name = "aws"
        pc = MagicMock()
        pc.get_active_providers.return_value = [p]
        mode = MagicMock()
        mode.value = "legacy"
        pc.get_mode.return_value = mode
        cfg.get_provider_config.return_value = pc
        cfg.get_configuration_sources.return_value = {
            "primary_source": "file",
            "config_file": "/etc/orb/config.yml",
        }

        system_info = _make_system_info()
        system_info.get_file_mtime.side_effect = OSError("no such file")

        h = GetProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service(),
            system_info=system_info,
        )
        q = GetProviderConfigQuery()
        result = await h.execute_query(q)
        assert result.last_updated is None

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        container = MagicMock()
        container.get.side_effect = RuntimeError("container gone")
        h = GetProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service(),
            system_info=_make_system_info(),
        )
        q = GetProviderConfigQuery()
        with pytest.raises(RuntimeError, match="container gone"):
            await h.execute_query(q)


# ---------------------------------------------------------------------------
# ValidateProviderConfigHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateProviderConfigHandler:
    @pytest.mark.asyncio
    async def test_valid_configuration(self):
        container, cfg = _make_container()
        cfg.validate_configuration.return_value = {"errors": [], "warnings": []}
        pc = MagicMock()
        pc.get_active_providers.return_value = [MagicMock()]
        cfg.get_provider_config.return_value = pc

        h = ValidateProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateProviderConfigQuery()
        result = await h.execute_query(q)
        assert isinstance(result, ValidationResultDTO)
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_no_active_providers_adds_warning(self):
        container, cfg = _make_container()
        cfg.validate_configuration.return_value = {"errors": [], "warnings": []}
        pc = MagicMock()
        pc.get_active_providers.return_value = []
        cfg.get_provider_config.return_value = pc

        h = ValidateProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateProviderConfigQuery()
        result = await h.execute_query(q)
        assert any("No active providers" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_validation_errors_reported(self):
        container, cfg = _make_container()
        cfg.validate_configuration.return_value = {"errors": ["bad field"], "warnings": []}
        cfg.get_provider_config.return_value = None

        h = ValidateProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateProviderConfigQuery()
        result = await h.execute_query(q)
        assert result.is_valid is False
        assert "bad field" in result.validation_errors

    @pytest.mark.asyncio
    async def test_provider_config_none_adds_warning(self):
        container, cfg = _make_container()
        cfg.validate_configuration.return_value = {"errors": [], "warnings": []}
        cfg.get_provider_config.return_value = None

        h = ValidateProviderConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateProviderConfigQuery()
        result = await h.execute_query(q)
        assert any("Unable to access" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# GetSystemStatusHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSystemStatusHandler:
    def _handler(self, config_raises=False, env_val=None):
        container, _cfg = _make_container(config_raises=config_raises)
        system_info = _make_system_info()
        if env_val:
            system_info.get_env.return_value = env_val
        return GetSystemStatusHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service(),
            system_info=system_info,
        )

    @pytest.mark.asyncio
    async def test_returns_system_status_dto(self):
        h = self._handler()
        q = GetSystemStatusQuery()
        result = await h.execute_query(q)
        assert isinstance(result, SystemStatusDTO)
        assert result.status == "operational"
        assert result.uptime_seconds == 1000.0

    @pytest.mark.asyncio
    async def test_degraded_when_config_not_accessible(self):
        h = self._handler(config_raises=True)
        q = GetSystemStatusQuery()
        result = await h.execute_query(q)
        assert result.status == "degraded"
        assert "configuration" in result.components

    @pytest.mark.asyncio
    async def test_env_from_orb_environment(self):
        h = self._handler(env_val="staging")
        q = GetSystemStatusQuery()
        result = await h.execute_query(q)
        assert result.environment == "staging"

    @pytest.mark.asyncio
    async def test_env_defaults_to_production(self):
        h = self._handler(env_val=None)
        q = GetSystemStatusQuery()
        result = await h.execute_query(q)
        assert result.environment == "production"


# ---------------------------------------------------------------------------
# ValidateStorageHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateStorageHandler:
    @pytest.mark.asyncio
    async def test_success_when_storage_accessible(self):
        container, _ = _make_container()
        factory = _make_uow_factory()
        h = ValidateStorageHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            uow_factory=factory,
        )
        q = ValidateStorageQuery()
        result = await h.execute_query(q)
        assert result["status"] == "success"
        assert result["storage_accessible"] is True

    @pytest.mark.asyncio
    async def test_error_when_storage_unavailable(self):
        container, _ = _make_container()
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        h = ValidateStorageHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            uow_factory=factory,
        )
        q = ValidateStorageQuery()
        result = await h.execute_query(q)
        assert result["status"] == "error"
        assert result["storage_accessible"] is False
        assert "db gone" in result["error"]


# ---------------------------------------------------------------------------
# ValidateMCPHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateMCPHandler:
    @pytest.mark.asyncio
    async def test_valid_mcp_config(self):
        container, cfg = _make_container()
        cfg.get.return_value = {"enabled": True, "endpoint": "http://localhost:8080"}
        h = ValidateMCPHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateMCPQuery()
        result = await h.execute_query(q)
        assert result["status"] == "success"
        assert result["is_valid"] is True
        assert result["mcp_enabled"] is True

    @pytest.mark.asyncio
    async def test_missing_mcp_config_adds_warnings(self):
        container, cfg = _make_container()
        cfg.get.return_value = {}
        h = ValidateMCPHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateMCPQuery()
        result = await h.execute_query(q)
        # Empty config → warnings but still valid (no errors)
        assert result["is_valid"] is True
        assert len(result["warnings"]) > 0

    @pytest.mark.asyncio
    async def test_non_dict_mcp_config_adds_error(self):
        container, cfg = _make_container()
        cfg.get.return_value = "not-a-dict"
        h = ValidateMCPHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateMCPQuery()
        result = await h.execute_query(q)
        assert result["is_valid"] is False
        assert len(result["validation_errors"]) > 0

    @pytest.mark.asyncio
    async def test_exception_returns_error_response(self):
        container = MagicMock()
        container.get.side_effect = RuntimeError("container gone")
        h = ValidateMCPHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateMCPQuery()
        result = await h.execute_query(q)
        assert result["status"] == "error"
        assert result["is_valid"] is False
        assert result["mcp_enabled"] is False


# ---------------------------------------------------------------------------
# GetSystemConfigHandler — basic smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSystemConfigHandler:
    def _build_cfg(self):
        cfg = MagicMock()
        cfg.get.return_value = {}
        cfg.get_storage_strategy.return_value = "memory"
        cfg.get_storage_config.return_value = {}
        cfg.get_scheduler_strategy.return_value = "default"
        cfg.get_logging_config.return_value = {"level": "INFO", "console_enabled": True}
        cfg.get_request_config.return_value = {}
        cfg.get_root_dir.return_value = "/opt/orb"
        cfg.get_config_dir.return_value = "/opt/orb/config"
        cfg.get_work_dir.return_value = "/tmp/orb"
        cfg.get_log_dir.return_value = "/var/log/orb"
        cfg.get_scripts_dir.return_value = None
        cfg.get_loaded_config_file.return_value = None
        cfg.get_provider_config.return_value = None
        return cfg

    @pytest.mark.asyncio
    async def test_returns_system_config_dto(self):
        from orb.application.dto.system import SystemConfigDTO

        cfg = self._build_cfg()
        container = MagicMock()
        container.get.return_value = cfg
        system_info = _make_system_info()

        h = GetSystemConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            system_info=system_info,
        )
        q = GetSystemConfigQuery(verbose=False)
        result = await h.execute_query(q)
        assert isinstance(result, SystemConfigDTO)
        assert result.storage.strategy == "memory"
        assert result.scheduler.scheduler_type == "default"

    @pytest.mark.asyncio
    async def test_verbose_includes_circuit_breaker(self):
        from orb.application.dto.system import SystemConfigDTO

        cfg = self._build_cfg()
        cb = MagicMock()
        cb.enabled = True
        cb.failure_threshold = 5
        cb.recovery_timeout = 60
        cfg.app_config = MagicMock()
        cfg.app_config.circuit_breaker = cb

        container = MagicMock()
        container.get.return_value = cfg

        h = GetSystemConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            system_info=_make_system_info(),
        )
        q = GetSystemConfigQuery(verbose=True)
        result = await h.execute_query(q)
        assert isinstance(result, SystemConfigDTO)
        # Circuit breaker should be populated
        assert result.circuit_breaker is not None

    @pytest.mark.asyncio
    async def test_server_section_when_enabled(self):
        from orb.application.dto.system import SystemConfigDTO

        cfg = self._build_cfg()
        cfg.get.side_effect = lambda key, default=None: (
            {"enabled": True, "host": "0.0.0.0", "port": 8080} if key == "server" else default
        )

        container = MagicMock()
        container.get.return_value = cfg

        h = GetSystemConfigHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
            system_info=_make_system_info(),
        )
        q = GetSystemConfigQuery(verbose=False)
        result = await h.execute_query(q)
        assert isinstance(result, SystemConfigDTO)
        assert result.server is not None
        assert result.server.host == "0.0.0.0"
