"""Unit tests for system query handlers using a fake SystemInfoPort.

Verifies that GetProviderConfigHandler, GetSystemStatusHandler, and
GetSystemConfigHandler delegate all system I/O to the injected
SystemInfoPort — no real psutil/os calls happen in the application layer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.application.ports.system_info_port import SystemInfoPort
from orb.application.queries.system import (
    GetProviderConfigQuery,
    GetSystemConfigQuery,
    GetSystemStatusQuery,
)
from orb.application.queries.system_handlers import (
    GetProviderConfigHandler,
    GetSystemConfigHandler,
    GetSystemStatusHandler,
)

# ---------------------------------------------------------------------------
# Fake SystemInfoPort — returns deterministic canned values
# ---------------------------------------------------------------------------


class FakeSystemInfo(SystemInfoPort):
    """Deterministic in-memory SystemInfoPort for testing."""

    def __init__(
        self,
        *,
        uptime_seconds: float = 3600.0,
        memory_usage_mb: float = 256.0,
        cpu_usage_percent: float = 42.0,
        disk_usage_percent: float = 55.0,
        file_mtime: float = 1_700_000_000.0,
        path_exists_result: bool = True,
        env_values: dict[str, str] | None = None,
        package_versions: dict[str, str] | None = None,
    ) -> None:
        self._uptime_seconds = uptime_seconds
        self._memory_usage_mb = memory_usage_mb
        self._cpu_usage_percent = cpu_usage_percent
        self._disk_usage_percent = disk_usage_percent
        self._file_mtime = file_mtime
        self._path_exists_result = path_exists_result
        self._env_values: dict[str, str] = env_values if env_values is not None else {}
        self._package_versions: dict[str, str] = (
            package_versions if package_versions is not None else {"orb": "1.2.3"}
        )

    def get_uptime_seconds(self) -> float:
        return self._uptime_seconds

    def get_memory_usage_mb(self) -> float:
        return self._memory_usage_mb

    def get_cpu_usage_percent(self) -> float:
        return self._cpu_usage_percent

    def get_disk_usage_percent(self, path: str = "/") -> float:
        return self._disk_usage_percent

    def get_file_mtime(self, path: str) -> float:
        return self._file_mtime

    def path_exists(self, path: str) -> bool:
        return self._path_exists_result

    def get_env(self, key: str, default: str | None = None) -> str | None:
        return self._env_values.get(key, default)

    def get_package_version(self, package: str) -> str:
        return self._package_versions.get(package, "unknown")


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    return MagicMock()


def _make_error_handler() -> MagicMock:
    return MagicMock()


def _make_container(get_map: dict[Any, Any]) -> MagicMock:
    """Return a MagicMock container whose .get() dispatches on type."""
    container = MagicMock()
    container.get.side_effect = lambda cls: get_map[cls]
    return container


def _make_timestamp_service(formatted: str = "2023-11-14T12:00:00Z") -> MagicMock:
    svc = MagicMock()
    svc.format_for_display.return_value = formatted
    return svc


# ---------------------------------------------------------------------------
# GetProviderConfigHandler tests
# ---------------------------------------------------------------------------


class TestGetProviderConfigHandlerUsesSystemInfoPort:
    """GetProviderConfigHandler must delegate file-mtime lookup to SystemInfoPort."""

    def _make_handler(
        self,
        *,
        system_info: SystemInfoPort | None = None,
        config_sources: dict | None = None,
        provider_config: Any = None,
    ) -> GetProviderConfigHandler:
        from orb.domain.base.ports import ConfigurationPort

        if config_sources is None:
            config_sources = {"primary_source": "file", "config_file": "/etc/orb/config.json"}

        mock_cfg = MagicMock(spec=ConfigurationPort)
        mock_cfg.get_provider_config.return_value = provider_config
        mock_cfg.get_configuration_sources.return_value = config_sources

        container = _make_container({ConfigurationPort: mock_cfg})

        return GetProviderConfigHandler(
            logger=_make_logger(),
            config_manager=container.get(ConfigurationPort),
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service("2023-11-14T00:00:00Z"),
            system_info=system_info or FakeSystemInfo(),
        )

    @pytest.mark.asyncio
    async def test_last_updated_comes_from_system_info_port(self) -> None:
        """last_updated should be produced from SystemInfoPort.get_file_mtime."""
        fake_info = FakeSystemInfo(file_mtime=1_699_920_000.0)
        handler = self._make_handler(system_info=fake_info)

        result = await handler.execute_query(GetProviderConfigQuery())

        # timestamp_service received the mtime returned by FakeSystemInfo
        assert result.last_updated == "2023-11-14T00:00:00Z"

    @pytest.mark.asyncio
    async def test_no_real_os_getmtime_called(self) -> None:
        """os.path.getmtime must never be called inside the handler."""
        handler = self._make_handler()

        with patch("os.path.getmtime") as mock_getmtime:
            await handler.execute_query(GetProviderConfigQuery())
            mock_getmtime.assert_not_called()

    @pytest.mark.asyncio
    async def test_last_updated_none_when_no_config_file(self) -> None:
        """last_updated is None when no config_file key in sources."""
        handler = self._make_handler(
            config_sources={"primary_source": "defaults"},
        )

        result = await handler.execute_query(GetProviderConfigQuery())

        assert result.last_updated is None

    @pytest.mark.asyncio
    async def test_last_updated_none_on_oserror(self) -> None:
        """get_file_mtime raising OSError should leave last_updated as None (non-fatal)."""
        broken_info = FakeSystemInfo()
        broken_info.get_file_mtime = MagicMock(side_effect=OSError("no such file"))  # type: ignore[method-assign]
        handler = self._make_handler(system_info=broken_info)

        result = await handler.execute_query(GetProviderConfigQuery())

        assert result.last_updated is None


# ---------------------------------------------------------------------------
# GetSystemStatusHandler tests
# ---------------------------------------------------------------------------


class TestGetSystemStatusHandlerUsesSystemInfoPort:
    """GetSystemStatusHandler must delegate all metrics to SystemInfoPort."""

    def _make_handler(
        self,
        *,
        system_info: SystemInfoPort | None = None,
    ) -> GetSystemStatusHandler:
        from orb.domain.base.ports import ConfigurationPort

        mock_cfg = MagicMock(spec=ConfigurationPort)
        container = _make_container({ConfigurationPort: mock_cfg})

        return GetSystemStatusHandler(
            logger=_make_logger(),
            config_manager=container.get(ConfigurationPort),
            error_handler=_make_error_handler(),
            timestamp_service=_make_timestamp_service(),
            system_info=system_info or FakeSystemInfo(),
        )

    @pytest.mark.asyncio
    async def test_status_dto_uses_fake_metrics(self) -> None:
        """SystemStatusDTO fields should match FakeSystemInfo return values."""
        fake_info = FakeSystemInfo(
            uptime_seconds=7200.0,
            memory_usage_mb=512.0,
            cpu_usage_percent=88.5,
            disk_usage_percent=33.3,
            env_values={"ORB_ENVIRONMENT": "staging"},
            package_versions={"orb": "2.0.0"},
        )
        handler = self._make_handler(system_info=fake_info)

        result = await handler.execute_query(GetSystemStatusQuery())

        assert result.uptime_seconds == 7200.0
        assert result.memory_usage_mb == 512.0
        assert result.cpu_usage_percent == 88.5
        assert result.disk_usage_percent == 33.3
        assert result.version == "2.0.0"
        assert result.environment == "staging"

    @pytest.mark.asyncio
    async def test_environment_falls_back_to_env_key(self) -> None:
        """When ORB_ENVIRONMENT is absent, ENV should be used."""
        fake_info = FakeSystemInfo(env_values={"ENV": "dev"})
        handler = self._make_handler(system_info=fake_info)

        result = await handler.execute_query(GetSystemStatusQuery())

        assert result.environment == "dev"

    @pytest.mark.asyncio
    async def test_environment_defaults_to_production(self) -> None:
        """When neither ORB_ENVIRONMENT nor ENV is set, default is 'production'."""
        fake_info = FakeSystemInfo(env_values={})
        handler = self._make_handler(system_info=fake_info)

        result = await handler.execute_query(GetSystemStatusQuery())

        assert result.environment == "production"

    @pytest.mark.asyncio
    async def test_unknown_version_when_package_not_installed(self) -> None:
        """get_package_version returning 'unknown' propagates to the DTO."""
        fake_info = FakeSystemInfo(package_versions={})
        handler = self._make_handler(system_info=fake_info)

        result = await handler.execute_query(GetSystemStatusQuery())

        assert result.version == "unknown"

    @pytest.mark.asyncio
    async def test_no_real_psutil_called(self) -> None:
        """psutil must never be imported or called inside the handler."""
        handler = self._make_handler()

        with (
            patch("psutil.boot_time") as mock_boot,
            patch("psutil.cpu_percent") as mock_cpu,
            patch("psutil.disk_usage") as mock_disk,
        ):
            await handler.execute_query(GetSystemStatusQuery())
            mock_boot.assert_not_called()
            mock_cpu.assert_not_called()
            mock_disk.assert_not_called()


# ---------------------------------------------------------------------------
# GetSystemConfigHandler tests
# ---------------------------------------------------------------------------


class TestGetSystemConfigHandlerUsesSystemInfoPort:
    """GetSystemConfigHandler must delegate path existence checks to SystemInfoPort."""

    def _make_handler(
        self,
        *,
        system_info: SystemInfoPort | None = None,
        template_paths: list[str] | None = None,
    ) -> GetSystemConfigHandler:
        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.domain.base.ports import ConfigurationPort

        mock_cfg = MagicMock(spec=ConfigurationPort)
        mock_cfg.get_storage_strategy.return_value = "sql"
        mock_cfg.get_scheduler_strategy.return_value = "default"
        mock_cfg.get_storage_config.return_value = {}
        mock_cfg.get_logging_config.return_value = {}
        mock_cfg.get_request_config.return_value = {}
        mock_cfg.get.return_value = {}
        # Return proper strings so pydantic models validate cleanly
        mock_cfg.get_root_dir.return_value = "/root"
        mock_cfg.get_config_dir.return_value = "/root/config"
        mock_cfg.get_work_dir.return_value = "/root/work"
        mock_cfg.get_log_dir.return_value = "/root/logs"
        mock_cfg.get_scripts_dir.return_value = None
        mock_cfg.get_loaded_config_file.return_value = None
        # Return None so the handler falls back to defaults for provider info
        mock_cfg.get_provider_config.return_value = None

        mock_scheduler = MagicMock(spec=SchedulerPort)
        mock_scheduler.get_template_paths.return_value = template_paths or []

        container = _make_container({ConfigurationPort: mock_cfg, SchedulerPort: mock_scheduler})

        return GetSystemConfigHandler(
            logger=_make_logger(),
            config_manager=container.get(ConfigurationPort),
            scheduler=container.get(SchedulerPort),
            error_handler=_make_error_handler(),
            system_info=system_info or FakeSystemInfo(),
        )

    @pytest.mark.asyncio
    async def test_first_existing_path_is_loaded_templates_file(self) -> None:
        """loaded_templates_file should be the first path for which path_exists returns True."""
        fake_info = FakeSystemInfo(path_exists_result=True)
        handler = self._make_handler(
            system_info=fake_info,
            template_paths=["/a/templates.json", "/b/templates.json"],
        )

        result = await handler.execute_query(GetSystemConfigQuery())

        assert result.paths.loaded_templates_file == "/a/templates.json"

    @pytest.mark.asyncio
    async def test_none_when_no_path_exists(self) -> None:
        """loaded_templates_file is None when all candidate paths are absent."""
        fake_info = FakeSystemInfo(path_exists_result=False)
        handler = self._make_handler(
            system_info=fake_info,
            template_paths=["/a/templates.json", "/b/templates.json"],
        )

        result = await handler.execute_query(GetSystemConfigQuery())

        assert result.paths.loaded_templates_file is None

    @pytest.mark.asyncio
    async def test_no_real_os_path_exists_called(self) -> None:
        """os.path.exists must never be called inside the handler."""
        handler = self._make_handler(
            template_paths=["/somewhere/templates.json"],
        )

        with patch("os.path.exists") as mock_exists:
            await handler.execute_query(GetSystemConfigQuery())
            mock_exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_template_search_paths_returned(self) -> None:
        """template_search_paths should reflect the full list from the scheduler port."""
        paths = ["/x/t.json", "/y/t.json", "/z/t.json"]
        handler = self._make_handler(template_paths=paths)

        result = await handler.execute_query(GetSystemConfigQuery())

        assert result.paths.template_search_paths == paths
