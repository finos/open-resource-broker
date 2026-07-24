"""Additional unit tests for server_command_handlers.

Covers uncovered branches:
- _resolve_lifecycle_paths: config vs platform_dirs fallbacks
- _resolve_configs: exception path, None path, scheduler override path
- _build_runtime: api_only, ui_config embedded/split/dev branches
- _health_url: dev mode, IPv6 wildcard, embedded with wildcard host
- _read_loopback_token: present, absent, OSError
- _loopback_reload_request: non-loopback ValueError
- handle_server_reload: embedded mode, dev mode url paths
- handle_server_ui_export: static_dir None, dest not a dir, non-empty+no force, force overwrite, happy path
- _ui_resolve_static_dir: ImportError path
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "foreground": False,
        "timeout": None,
        "host": None,
        "port": None,
        "workers": None,
        "server_log_level": None,
        "scheduler": None,
        "api_only": False,
        "socket_path": None,
        "reload": False,
        "lines": None,
    }
    defaults.update(kwargs)
    ns = argparse.Namespace(**defaults)
    return ns


def _make_server_config(
    host="127.0.0.1",
    port=8000,
    workers=1,
    log_level="info",
    stop_timeout_seconds=10,
    pid_file=None,
    log_file=None,
    working_dir=None,
):
    cfg = MagicMock()
    cfg.host = host
    cfg.port = port
    cfg.workers = workers
    cfg.log_level = log_level
    cfg.stop_timeout_seconds = stop_timeout_seconds
    cfg.pid_file = pid_file
    cfg.log_file = log_file
    cfg.working_dir = working_dir
    return cfg


def _make_ui_config(enabled=True, mode="embedded", backend_port=3001, frontend_port=3000):
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.mode = mode
    cfg.backend_port = backend_port
    cfg.frontend_port = frontend_port
    return cfg


# ---------------------------------------------------------------------------
# _resolve_lifecycle_paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveLifecyclePaths:
    def test_uses_config_values_when_set(self):
        """When server_config has pid_file/log_file/working_dir, those win."""
        from orb.interface.server_command_handlers import _resolve_lifecycle_paths

        cfg = _make_server_config(
            pid_file="/custom/server.pid",
            log_file="/custom/server.log",
            working_dir="/custom/work",
        )
        pid, log, wd = _resolve_lifecycle_paths(cfg)

        assert pid == "/custom/server.pid"
        assert log == "/custom/server.log"
        assert wd == "/custom/work"

    def test_falls_back_to_platform_dirs_when_config_empty(self):
        """When server_config fields are None/empty, platform_dirs helpers are called."""
        from orb.interface.server_command_handlers import _resolve_lifecycle_paths

        cfg = _make_server_config(pid_file=None, log_file=None, working_dir=None)

        fake_work = Path("/fake/work")
        fake_logs = Path("/fake/logs")

        with (
            patch("orb.config.platform_dirs.get_work_location", return_value=fake_work),
            patch("orb.config.platform_dirs.get_logs_location", return_value=fake_logs),
        ):
            pid, log, wd = _resolve_lifecycle_paths(cfg)

        assert "orb-server.pid" in pid
        assert "orb-server.log" in log
        assert wd == str(fake_work)


# ---------------------------------------------------------------------------
# _resolve_configs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveConfigs:
    def _make_container_with_cm(self, raises=None, returns=None, scheduler_override=False):
        from unittest.mock import MagicMock

        mock_cm = MagicMock()
        if raises:
            mock_cm.get_typed_with_defaults.side_effect = raises
        else:
            mock_cm.get_typed_with_defaults.return_value = returns or MagicMock()

        mock_port_manager = MagicMock()
        container = MagicMock()

        def get_side_effect(t):
            from orb.config.managers.configuration_manager import ConfigurationManager
            from orb.domain.base.ports.configuration_port import ConfigurationPort

            if t is ConfigurationManager:
                return mock_cm
            if t is ConfigurationPort:
                return mock_port_manager
            return MagicMock()

        container.get.side_effect = get_side_effect
        return container, mock_cm, mock_port_manager

    def test_raises_configuration_error_when_cm_throws(self):
        from orb.domain.base.exceptions import ConfigurationError
        from orb.interface.server_command_handlers import _resolve_configs

        container, _, _ = self._make_container_with_cm(raises=RuntimeError("boom"))
        args = _args()
        args._container = container

        with pytest.raises(ConfigurationError, match="ServerConfig could not be loaded"):
            _resolve_configs(args)

    def test_raises_configuration_error_when_server_config_is_none(self):
        from orb.domain.base.exceptions import ConfigurationError
        from orb.interface.server_command_handlers import _resolve_configs

        # get_typed_with_defaults must actually return None (not a MagicMock)
        container, mock_cm, _ = self._make_container_with_cm(returns=MagicMock())
        mock_cm.get_typed_with_defaults.return_value = None
        args = _args()
        args._container = container

        with pytest.raises(ConfigurationError, match="resolved to None"):
            _resolve_configs(args)

    def test_scheduler_override_calls_port_manager(self):
        from orb.interface.server_command_handlers import _resolve_configs

        mock_server_config = MagicMock()
        container, mock_cm, mock_port_manager = self._make_container_with_cm(
            returns=mock_server_config
        )
        # UIConfig load raises to keep test focused
        mock_cm.get_typed_with_defaults.side_effect = [mock_server_config, Exception("no UIConfig")]

        args = _args(scheduler="hf")
        args._container = container

        _resolve_configs(args)

        mock_port_manager.override_scheduler_strategy.assert_called_once_with("hf")

    def test_ui_config_load_failure_is_debug_logged_not_raised(self):
        """UIConfig load failure is debug-only; server_config is still returned."""
        from orb.interface.server_command_handlers import _resolve_configs

        mock_server_config = MagicMock()
        container, mock_cm, _ = self._make_container_with_cm(returns=mock_server_config)
        mock_cm.get_typed_with_defaults.side_effect = [
            mock_server_config,
            ImportError("ui not installed"),
        ]

        args = _args()
        args._container = container

        server_config, ui_config = _resolve_configs(args)

        assert server_config is mock_server_config
        assert ui_config is None


# ---------------------------------------------------------------------------
# _build_runtime — branching on api_only and ui_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRuntime:
    _RESOLVE_CONFIGS = "orb.interface.server_command_handlers._resolve_configs"

    @patch(_RESOLVE_CONFIGS)
    def test_api_only_selects_run_api_foreground(self, mock_resolve):
        """api_only=True must call run_api_foreground regardless of ui_config."""
        from orb.interface.server_command_handlers import _build_runtime

        server_cfg = _make_server_config()
        ui_cfg = _make_ui_config(enabled=True, mode="embedded")
        mock_resolve.return_value = (server_cfg, ui_cfg)

        args = _args(api_only=True)
        args._container = MagicMock()

        runtime, _sc, _ui = _build_runtime(args)

        mock_run_api = AsyncMock(return_value={"exit_code": 0})
        mock_run_embedded = AsyncMock(return_value={"exit_code": 0})

        with (
            patch("orb.interface.server_runtime.run_api_foreground", mock_run_api),
            patch("orb.interface.server_runtime.run_embedded_foreground", mock_run_embedded),
            patch("orb.interface.server_command_handlers._initialize_application", AsyncMock()),
            patch("orb.interface.server_command_handlers._schedule_startup_recovery"),
        ):
            asyncio_run = __import__("asyncio")
            asyncio_run.run(runtime())

        mock_run_api.assert_awaited_once()
        mock_run_embedded.assert_not_awaited()

    @patch(_RESOLVE_CONFIGS)
    def test_embedded_ui_selects_run_embedded_foreground(self, mock_resolve):
        """enabled UI with embedded mode must call run_embedded_foreground."""
        from orb.interface.server_command_handlers import _build_runtime

        server_cfg = _make_server_config()
        ui_cfg = _make_ui_config(enabled=True, mode="embedded")
        mock_resolve.return_value = (server_cfg, ui_cfg)

        args = _args(api_only=False)
        args._container = MagicMock()

        runtime, _sc, _ui = _build_runtime(args)

        mock_run_embedded = AsyncMock(return_value={"exit_code": 0})
        mock_run_api = AsyncMock(return_value={"exit_code": 0})

        import asyncio as _asyncio

        with (
            patch("orb.interface.server_runtime.run_api_foreground", mock_run_api),
            patch("orb.interface.server_runtime.run_embedded_foreground", mock_run_embedded),
            patch("orb.interface.server_command_handlers._initialize_application", AsyncMock()),
            patch("orb.interface.server_command_handlers._schedule_startup_recovery"),
        ):
            _asyncio.run(runtime())

        mock_run_embedded.assert_awaited_once()
        mock_run_api.assert_not_awaited()

    @patch(_RESOLVE_CONFIGS)
    def test_no_ui_config_selects_run_api_foreground(self, mock_resolve):
        """ui_config=None always routes to run_api_foreground."""
        from orb.interface.server_command_handlers import _build_runtime

        server_cfg = _make_server_config()
        mock_resolve.return_value = (server_cfg, None)

        args = _args(api_only=False)
        args._container = MagicMock()

        runtime, _sc, _ui = _build_runtime(args)

        mock_run_api = AsyncMock(return_value={"exit_code": 0})

        import asyncio as _asyncio

        with (
            patch("orb.interface.server_runtime.run_api_foreground", mock_run_api),
            patch("orb.interface.server_command_handlers._initialize_application", AsyncMock()),
            patch("orb.interface.server_command_handlers._schedule_startup_recovery"),
        ):
            _asyncio.run(runtime())

        mock_run_api.assert_awaited_once()


# ---------------------------------------------------------------------------
# _health_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthUrl:
    def test_no_ui_config_returns_standard_health(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="127.0.0.1", port=9000)
        url = _health_url(cfg, None)
        assert url == "http://127.0.0.1:9000/health"

    def test_wildcard_host_becomes_loopback(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="0.0.0.0", port=8000)
        url = _health_url(cfg, None)
        assert "127.0.0.1" in url
        assert "0.0.0.0" not in url

    def test_ipv6_wildcard_becomes_loopback(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="::", port=8000)
        url = _health_url(cfg, None)
        assert "127.0.0.1" in url

    def test_embedded_mode_uses_orb_prefix(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui = _make_ui_config(enabled=True, mode="embedded")
        url = _health_url(cfg, ui)
        assert url == "http://127.0.0.1:8000/orb/health"

    def test_embedded_mode_wildcard_host_becomes_loopback(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="0.0.0.0", port=8000)
        ui = _make_ui_config(enabled=True, mode="embedded")
        url = _health_url(cfg, ui)
        assert "127.0.0.1" in url
        assert "/orb/health" in url

    def test_dev_mode_uses_backend_port(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui = _make_ui_config(enabled=True, mode="dev", backend_port=4567)
        url = _health_url(cfg, ui)
        assert url == "http://127.0.0.1:4567/orb/health"

    def test_disabled_ui_uses_standard_health(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui = _make_ui_config(enabled=False, mode="embedded")
        url = _health_url(cfg, ui)
        assert url == "http://127.0.0.1:8000/health"

    def test_split_mode_uses_standard_health(self):
        from orb.interface.server_command_handlers import _health_url

        cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui = _make_ui_config(enabled=True, mode="split", backend_port=3001)
        url = _health_url(cfg, ui)
        # split mode is not embedded/dev — falls through to standard health
        assert url == "http://127.0.0.1:8000/health"


# ---------------------------------------------------------------------------
# _read_loopback_token
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadLoopbackToken:
    def test_returns_token_when_file_exists(self, tmp_path):
        from orb.interface.server_command_handlers import _read_loopback_token

        pid_file = tmp_path / "orb-server.pid"
        token_file = tmp_path / "orb-server.token"
        token_file.write_text("my-secret-token", encoding="ascii")

        result = _read_loopback_token(str(pid_file))
        assert result == "my-secret-token"

    def test_returns_none_when_token_file_missing(self, tmp_path):
        from orb.interface.server_command_handlers import _read_loopback_token

        pid_file = tmp_path / "orb-server.pid"
        # token file does not exist

        result = _read_loopback_token(str(pid_file))
        assert result is None

    def test_returns_none_when_token_file_empty(self, tmp_path):
        from orb.interface.server_command_handlers import _read_loopback_token

        pid_file = tmp_path / "orb-server.pid"
        token_file = tmp_path / "orb-server.token"
        token_file.write_text("   ", encoding="ascii")  # whitespace only

        result = _read_loopback_token(str(pid_file))
        assert result is None

    def test_returns_none_on_oserror(self, tmp_path):
        from orb.interface.server_command_handlers import _read_loopback_token

        pid_file = tmp_path / "orb-server.pid"

        with patch("pathlib.Path.exists", side_effect=OSError("permission denied")):
            result = _read_loopback_token(str(pid_file))

        assert result is None


# ---------------------------------------------------------------------------
# _loopback_reload_request — non-loopback host raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoopbackReloadRequest:
    def test_non_loopback_host_raises_value_error(self):
        from orb.interface.server_command_handlers import _loopback_reload_request

        with pytest.raises(ValueError, match="loopback"):
            _loopback_reload_request("10.0.0.1", 8000, "/api/v1/admin/reload-config")

    def test_loopback_host_makes_http_request(self):
        from orb.interface.server_command_handlers import _loopback_reload_request

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"reloaded": true}'

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            result = _loopback_reload_request("127.0.0.1", 8000, "/path")

        assert result["status"] == 200
        assert result["method"] == "loopback-ipc"

    def test_non_json_body_wrapped_in_raw(self):
        from orb.interface.server_command_handlers import _loopback_reload_request

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"not-json"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            result = _loopback_reload_request("localhost", 8000, "/path")

        assert "raw" in result

    def test_empty_body_returns_empty_dict(self):
        from orb.interface.server_command_handlers import _loopback_reload_request

        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.read.return_value = b""

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            result = _loopback_reload_request("127.0.0.1", 8000, "/path")

        assert result["status"] == 204
        assert result["method"] == "loopback-ipc"

    def test_bearer_token_sent_in_header(self):
        from orb.interface.server_command_handlers import _loopback_reload_request

        captured: dict = {}

        def fake_request(method, path, body, headers=None):
            captured.update(headers or {})

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"{}"

        mock_conn = MagicMock()
        mock_conn.request = fake_request
        mock_conn.getresponse.return_value = mock_resp

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            _loopback_reload_request("127.0.0.1", 8000, "/path", token="tok-123")

        assert captured.get("Authorization") == "Bearer tok-123"


# ---------------------------------------------------------------------------
# handle_server_reload — embedded and dev mode path selection
# ---------------------------------------------------------------------------


_RESOLVE_CONFIGS = "orb.interface.server_command_handlers._resolve_configs"
_RESOLVE_PATHS = "orb.interface.server_command_handlers._resolve_lifecycle_paths"


@pytest.mark.unit
class TestHandleServerReloadModePaths:
    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_embedded_mode_uses_orb_prefix_path(
        self, mock_resolve_configs, _mock_paths
    ):
        """Embedded mode must POST to /orb/api/v1/admin/reload-config."""
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui_cfg = _make_ui_config(enabled=True, mode="embedded")
        mock_resolve_configs.return_value = (server_cfg, ui_cfg)

        captured_calls: list[dict] = []

        def fake_loopback(host, port, path, token=None):
            captured_calls.append({"host": host, "port": port, "path": path})
            return {"method": "loopback-ipc", "status": 200}

        mock_daemon = MagicMock()

        with (
            patch("orb.interface.server_daemon", mock_daemon, create=True),
            patch("orb.interface.server_command_handlers._read_loopback_token", return_value=None),
        ):
            # Directly patch _loopback_reload_request to avoid real HTTP
            with patch(
                "orb.interface.server_command_handlers._loopback_reload_request",
                side_effect=fake_loopback,
            ):
                with patch(
                    "asyncio.to_thread", new=lambda fn, *a, **kw: _fake_to_thread(fn, *a, **kw)
                ):
                    from orb.interface.server_command_handlers import handle_server_reload

                    await handle_server_reload(_args())

        # Should have called loopback with /orb path
        assert len(captured_calls) == 1
        assert captured_calls[0]["path"] == "/orb/api/v1/admin/reload-config"
        assert captured_calls[0]["port"] == 8000

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_dev_mode_uses_backend_port(self, mock_resolve_configs, _mock_paths):
        """Dev mode must target backend_port with /orb/api/v1/admin/reload-config."""
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui_cfg = _make_ui_config(enabled=True, mode="dev", backend_port=4567)
        mock_resolve_configs.return_value = (server_cfg, ui_cfg)

        captured_calls: list[dict] = []

        def fake_loopback(host, port, path, token=None):
            captured_calls.append({"host": host, "port": port, "path": path})
            return {"method": "loopback-ipc", "status": 200}

        mock_daemon = MagicMock()

        with (
            patch("orb.interface.server_daemon", mock_daemon, create=True),
            patch("orb.interface.server_command_handlers._read_loopback_token", return_value=None),
        ):
            with patch(
                "orb.interface.server_command_handlers._loopback_reload_request",
                side_effect=fake_loopback,
            ):
                with patch(
                    "asyncio.to_thread", new=lambda fn, *a, **kw: _fake_to_thread(fn, *a, **kw)
                ):
                    from orb.interface.server_command_handlers import handle_server_reload

                    await handle_server_reload(_args())

        assert len(captured_calls) == 1
        assert captured_calls[0]["port"] == 4567
        assert captured_calls[0]["path"] == "/orb/api/v1/admin/reload-config"

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_embedded_wildcard_host_becomes_loopback(
        self, mock_resolve_configs, _mock_paths
    ):
        """Embedded mode with 0.0.0.0 host must use 127.0.0.1 for loopback IPC."""
        server_cfg = _make_server_config(host="0.0.0.0", port=8000)
        ui_cfg = _make_ui_config(enabled=True, mode="embedded")
        mock_resolve_configs.return_value = (server_cfg, ui_cfg)

        captured_calls: list[dict] = []

        def fake_loopback(host, port, path, token=None):
            captured_calls.append({"host": host, "port": port, "path": path})
            return {"method": "loopback-ipc", "status": 200}

        mock_daemon = MagicMock()

        with (
            patch("orb.interface.server_daemon", mock_daemon, create=True),
            patch("orb.interface.server_command_handlers._read_loopback_token", return_value=None),
        ):
            with patch(
                "orb.interface.server_command_handlers._loopback_reload_request",
                side_effect=fake_loopback,
            ):
                with patch(
                    "asyncio.to_thread", new=lambda fn, *a, **kw: _fake_to_thread(fn, *a, **kw)
                ):
                    from orb.interface.server_command_handlers import handle_server_reload

                    await handle_server_reload(_args())

        assert captured_calls[0]["host"] == "127.0.0.1"


async def _fake_to_thread(fn, *args, **kwargs):
    """Synchronous-to-async wrapper that calls fn directly (no real thread)."""
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# _ui_resolve_static_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUiResolveStaticDir:
    def test_raises_validation_error_when_ui_not_installed(self):
        from orb.domain.base.exceptions import ValidationError
        from orb.interface.server_command_handlers import _ui_resolve_static_dir

        # Injecting None makes `from orb.ui.app import _resolve_static_dir` raise
        # ImportError, which the real wrapper must convert into a ValidationError
        # with an actionable "UI extras" install hint.
        with patch.dict("sys.modules", {"orb.ui.app": None}):
            with pytest.raises(ValidationError, match="UI extras"):
                _ui_resolve_static_dir()


# ---------------------------------------------------------------------------
# handle_server_ui_export
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleServerUiExport:
    @pytest.mark.asyncio
    async def test_static_dir_none_raises_validation_error(self, tmp_path):
        """When _ui_resolve_static_dir returns None, ValidationError is raised."""
        from orb.domain.base.exceptions import ValidationError
        from orb.interface.server_command_handlers import handle_server_ui_export

        dest = tmp_path / "output"
        args = argparse.Namespace(dest=str(dest), force=False)

        with patch(
            "orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=None
        ):
            with pytest.raises(ValidationError, match="bundle not found"):
                await handle_server_ui_export(args)

    @pytest.mark.asyncio
    async def test_dest_is_file_raises_validation_error(self, tmp_path):
        """dest pointing at a file (not dir) raises ValidationError."""
        from orb.domain.base.exceptions import ValidationError
        from orb.interface.server_command_handlers import handle_server_ui_export

        dest_file = tmp_path / "notadir.txt"
        dest_file.write_text("content")

        fake_static = tmp_path / "static"
        fake_static.mkdir()
        args = argparse.Namespace(dest=str(dest_file), force=False)

        with patch(
            "orb.interface.server_command_handlers._ui_resolve_static_dir",
            return_value=fake_static,
        ):
            with pytest.raises(ValidationError, match="not a directory"):
                await handle_server_ui_export(args)

    @pytest.mark.asyncio
    async def test_dest_non_empty_without_force_raises_validation_error(self, tmp_path):
        """Non-empty dest without --force raises ValidationError."""
        from orb.domain.base.exceptions import ValidationError
        from orb.interface.server_command_handlers import handle_server_ui_export

        dest = tmp_path / "output"
        dest.mkdir()
        (dest / "existing.txt").write_text("data")

        fake_static = tmp_path / "static"
        fake_static.mkdir()
        args = argparse.Namespace(dest=str(dest), force=False)

        with patch(
            "orb.interface.server_command_handlers._ui_resolve_static_dir",
            return_value=fake_static,
        ):
            with pytest.raises(ValidationError, match="not empty"):
                await handle_server_ui_export(args)

    @pytest.mark.asyncio
    async def test_happy_path_copies_files(self, tmp_path):
        """With a valid static dir and new dest, shutil.copytree is called."""
        from orb.interface.server_command_handlers import handle_server_ui_export

        fake_static = tmp_path / "static"
        fake_static.mkdir()
        (fake_static / "index.html").write_text("<html/>")

        dest = tmp_path / "output"
        args = argparse.Namespace(dest=str(dest), force=False)

        with patch(
            "orb.interface.server_command_handlers._ui_resolve_static_dir",
            return_value=fake_static,
        ):
            result = await handle_server_ui_export(args)

        assert result["status"] == "ok"
        assert result["file_count"] >= 1
        assert "output" in result["dest"]

    @pytest.mark.asyncio
    async def test_force_flag_allows_overwrite(self, tmp_path):
        """--force with an existing non-empty dest succeeds via dirs_exist_ok."""
        from orb.interface.server_command_handlers import handle_server_ui_export

        fake_static = tmp_path / "static"
        fake_static.mkdir()
        (fake_static / "app.js").write_text("js")

        dest = tmp_path / "output"
        dest.mkdir()
        (dest / "old.txt").write_text("old")

        args = argparse.Namespace(dest=str(dest), force=True)

        with patch(
            "orb.interface.server_command_handlers._ui_resolve_static_dir",
            return_value=fake_static,
        ):
            result = await handle_server_ui_export(args)

        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# _initialize_application
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeApplication:
    @pytest.mark.asyncio
    async def test_logs_error_when_initialize_returns_false(self):
        """When Application.initialize() returns False, error is logged and the function
        returns early without starting daemon services."""
        from orb.interface.server_command_handlers import _initialize_application

        mock_container = MagicMock()
        mock_config_port = MagicMock()
        mock_config_port._config_file = None
        mock_container.get.return_value = mock_config_port

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=False)
        mock_app.start_daemon_services = AsyncMock(return_value=True)

        mock_logger = MagicMock()

        with (
            patch("orb.bootstrap.Application", return_value=mock_app),
            patch(
                "orb.infrastructure.logging.logger.get_logger",
                return_value=mock_logger,
            ),
        ):
            await _initialize_application(mock_container)

        # Early return: an error is logged and daemon services are never started.
        mock_logger.error.assert_called_once()
        assert "initialize" in mock_logger.error.call_args[0][0].lower()
        mock_app.start_daemon_services.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_logs_warning_when_start_daemon_returns_false(self):
        """When start_daemon_services() returns False, a warning is logged (no raise)."""
        from orb.interface.server_command_handlers import _initialize_application

        mock_container = MagicMock()
        mock_config_port = MagicMock()
        mock_config_port._config_file = None
        mock_container.get.return_value = mock_config_port

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.start_daemon_services = AsyncMock(return_value=False)

        mock_logger = MagicMock()

        with (
            patch("orb.bootstrap.Application", return_value=mock_app),
            patch(
                "orb.infrastructure.logging.logger.get_logger",
                return_value=mock_logger,
            ),
        ):
            await _initialize_application(mock_container)

        mock_logger.warning.assert_called_once()
        mock_logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_does_not_run_recovery_sweep(self):
        """Structural guard: the best-effort recovery sweep must NOT run on the
        awaited pre-serve init path.

        The sweep queries active requests and routes each through the state
        machine, which can push init past the server-start window and keep
        /health returning 503. It is scheduled post-serve instead. Here we prove
        _initialize_application never resolves the recovery service and never
        invokes recover_stuck_acquiring_requests.
        """
        from orb.application.services.provisioning_orchestration_service import (
            ProvisioningOrchestrationService,
        )
        from orb.interface.server_command_handlers import _initialize_application

        recovery_service = MagicMock()

        def _container_get(cls):
            if cls is ProvisioningOrchestrationService:
                return recovery_service
            mock_config_port = MagicMock()
            mock_config_port._config_file = None
            return mock_config_port

        mock_container = MagicMock()
        mock_container.get.side_effect = _container_get

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.start_daemon_services = AsyncMock(return_value=True)

        with patch("orb.bootstrap.Application", return_value=mock_app):
            await _initialize_application(mock_container)

        # The recovery service is never resolved and never invoked on the
        # awaited init path.
        recovery_service.recover_stuck_acquiring_requests.assert_not_called()
        assert ProvisioningOrchestrationService not in [
            c.args[0] for c in mock_container.get.call_args_list if c.args
        ]


# ---------------------------------------------------------------------------
# Startup recovery sweep — post-serve, best-effort, non-blocking to readiness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartupRecovery:
    @pytest.mark.asyncio
    async def test_run_startup_recovery_offloads_and_logs_sweep_count(self):
        """_run_startup_recovery resolves the service, runs the sweep off-thread,
        and logs when rows were swept."""
        from orb.application.services.provisioning_orchestration_service import (
            ProvisioningOrchestrationService,
        )
        from orb.interface.server_command_handlers import _run_startup_recovery

        recovery_service = MagicMock()
        recovery_service.recover_stuck_acquiring_requests.return_value = 3

        def _container_get(cls):
            if cls is ProvisioningOrchestrationService:
                return recovery_service
            return MagicMock()

        mock_container = MagicMock()
        mock_container.get.side_effect = _container_get

        await _run_startup_recovery(mock_container)

        recovery_service.recover_stuck_acquiring_requests.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_startup_recovery_swallows_exceptions(self):
        """A failure in the sweep is best-effort: logged, never raised."""
        from orb.application.services.provisioning_orchestration_service import (
            ProvisioningOrchestrationService,
        )
        from orb.interface.server_command_handlers import _run_startup_recovery

        recovery_service = MagicMock()
        recovery_service.recover_stuck_acquiring_requests.side_effect = RuntimeError("boom")

        def _container_get(cls):
            if cls is ProvisioningOrchestrationService:
                return recovery_service
            return MagicMock()

        mock_container = MagicMock()
        mock_container.get.side_effect = _container_get

        # Must not raise.
        await _run_startup_recovery(mock_container)

    @pytest.mark.asyncio
    async def test_schedule_startup_recovery_is_fire_and_forget(self):
        """_schedule_startup_recovery returns immediately without awaiting the
        sweep, and the sweep only executes once the loop yields (after serve)."""
        import asyncio as _asyncio

        from orb.interface import server_command_handlers as sch

        started = _asyncio.Event()
        release = _asyncio.Event()

        async def _fake_recovery(container):
            started.set()
            await release.wait()

        with patch.object(sch, "_run_startup_recovery", _fake_recovery):
            task = sch._schedule_startup_recovery(MagicMock())
            # Scheduling did not block on / await the sweep: at the point
            # _schedule_startup_recovery returned the coroutine had not yet run.
            assert not started.is_set()
            assert not task.done()
            # Yield so the scheduled task gets a chance to start.
            await _asyncio.sleep(0)
            assert started.is_set()
            # Let it finish and clean up the strong reference. Draining the
            # task fires the done-callback that drops it from the set.
            release.set()
            result = await task
            assert result is None
            assert task not in sch._background_tasks

    def test_runtime_schedules_recovery_after_init_not_awaited(self):
        """Structural guard on the serve path: runtime() schedules the recovery
        sweep (fire-and-forget) and starts serving without awaiting it, so
        readiness never depends on the sweep completing."""
        from orb.interface.server_command_handlers import _build_runtime

        server_cfg = _make_server_config()
        with patch(_RESOLVE_CONFIGS, return_value=(server_cfg, None)):
            args = _args(api_only=True)
            args._container = MagicMock()
            runtime, _sc, _ui = _build_runtime(args)

        call_order: list[str] = []

        init_mock = AsyncMock(side_effect=lambda *_a, **_k: call_order.append("init"))
        schedule_mock = MagicMock(side_effect=lambda *_a, **_k: call_order.append("schedule"))

        async def _fake_serve(*_a, **_k):
            call_order.append("serve")
            return {"exit_code": 0}

        import asyncio as _asyncio

        with (
            patch("orb.interface.server_command_handlers._initialize_application", init_mock),
            patch(
                "orb.interface.server_command_handlers._schedule_startup_recovery",
                schedule_mock,
            ),
            patch("orb.interface.server_runtime.run_api_foreground", _fake_serve),
        ):
            _asyncio.run(runtime())

        # Recovery is scheduled (not awaited) after init and before serve blocks.
        schedule_mock.assert_called_once()
        assert call_order == ["init", "schedule", "serve"]
