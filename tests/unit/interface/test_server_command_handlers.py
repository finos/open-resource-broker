"""Unit tests for server lifecycle CLI handlers."""

from __future__ import annotations

import argparse
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _args(**kwargs) -> argparse.Namespace:
    """Build a minimal Namespace with sensible defaults."""
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
    return argparse.Namespace(**defaults)


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


def _make_ui_config(enabled=False, mode="embedded", backend_port=3001, frontend_port=3000):
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.mode = mode
    cfg.backend_port = backend_port
    cfg.frontend_port = frontend_port
    return cfg


# Patch target constants
_RESOLVE_CONFIGS = "orb.interface.server_command_handlers._resolve_configs"
_RESOLVE_PATHS = "orb.interface.server_command_handlers._resolve_lifecycle_paths"
_BUILD_RUNTIME = "orb.interface.server_command_handlers._build_runtime"


# ---------------------------------------------------------------------------
# handle_server_start
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestHandleServerStart:
    @pytest.mark.asyncio
    @patch(_BUILD_RUNTIME)
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    async def test_start_foreground_false_calls_daemon_start(self, _mock_paths, mock_build_runtime):
        server_cfg = _make_server_config()
        runtime = AsyncMock(return_value={"message": "Server stopped"})
        mock_build_runtime.return_value = (runtime, server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.start.return_value = {"pid": 42, "status": "started"}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_start

            await handle_server_start(_args(foreground=False))

        mock_daemon.start.assert_called_once()
        call_kwargs = mock_daemon.start.call_args.kwargs
        assert call_kwargs["foreground"] is False

    @pytest.mark.asyncio
    @patch(_BUILD_RUNTIME)
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    async def test_start_foreground_true_passes_flag(self, _mock_paths, mock_build_runtime):
        server_cfg = _make_server_config()
        runtime = AsyncMock(return_value={"message": "Server stopped"})
        mock_build_runtime.return_value = (runtime, server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.start.return_value = {"pid": 42, "status": "foreground"}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_start

            await handle_server_start(_args(foreground=True))

        call_kwargs = mock_daemon.start.call_args.kwargs
        assert call_kwargs["foreground"] is True

    @pytest.mark.asyncio
    @patch(_BUILD_RUNTIME)
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    async def test_start_returns_daemon_result(self, _mock_paths, mock_build_runtime):
        server_cfg = _make_server_config()
        runtime = AsyncMock(return_value={})
        mock_build_runtime.return_value = (runtime, server_cfg, None)

        expected = {"pid": 99, "status": "started"}
        mock_daemon = MagicMock()
        mock_daemon.start.return_value = expected

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_start

            result = await handle_server_start(_args())

        assert result == expected


# ---------------------------------------------------------------------------
# handle_server_stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestHandleServerStop:
    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_stop_maps_args_timeout(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config(stop_timeout_seconds=30)
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.stop.return_value = {"status": "stopped"}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_stop

            await handle_server_stop(_args(timeout=60))

        call_kwargs = mock_daemon.stop.call_args.kwargs
        # CLI-provided timeout wins over config
        assert call_kwargs["timeout"] == 60.0

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_stop_falls_back_to_config_timeout(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config(stop_timeout_seconds=25)
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.stop.return_value = {"status": "stopped"}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_stop

            await handle_server_stop(_args(timeout=None))

        call_kwargs = mock_daemon.stop.call_args.kwargs
        assert call_kwargs["timeout"] == 25.0

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_stop_returns_daemon_result(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config()
        mock_resolve_configs.return_value = (server_cfg, None)
        expected = {"status": "stopped", "pid": 0}

        mock_daemon = MagicMock()
        mock_daemon.stop.return_value = expected

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_stop

            result = await handle_server_stop(_args())

        assert result == expected


# ---------------------------------------------------------------------------
# handle_server_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestHandleServerStatus:
    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_status_api_only_uses_server_host_port(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.status.return_value = {"running": True}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_status

            await handle_server_status(_args())

        call_kwargs = mock_daemon.status.call_args.kwargs
        assert call_kwargs["health_url"] == "http://127.0.0.1:8000/health"

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_status_embedded_ui_uses_backend_port_orb_prefix(
        self, mock_resolve_configs, _mock_paths
    ):
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        ui_cfg = _make_ui_config(enabled=True, mode="embedded", backend_port=3001)
        mock_resolve_configs.return_value = (server_cfg, ui_cfg)

        mock_daemon = MagicMock()
        mock_daemon.status.return_value = {"running": True}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_status

            await handle_server_status(_args())

        call_kwargs = mock_daemon.status.call_args.kwargs
        assert call_kwargs["health_url"] == "http://127.0.0.1:3001/orb/health"

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_status_wildcard_host_becomes_loopback(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config(host="0.0.0.0", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.status.return_value = {}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_status

            await handle_server_status(_args())

        call_kwargs = mock_daemon.status.call_args.kwargs
        assert "127.0.0.1" in call_kwargs["health_url"]

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_status_returns_daemon_result(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config()
        mock_resolve_configs.return_value = (server_cfg, None)
        expected = {"running": True, "pid": 1234}

        mock_daemon = MagicMock()
        mock_daemon.status.return_value = expected

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_status

            result = await handle_server_status(_args())

        assert result == expected


# ---------------------------------------------------------------------------
# handle_server_restart
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestHandleServerRestart:
    @pytest.mark.asyncio
    @patch("orb.interface.server_command_handlers.handle_server_start")
    @patch("orb.interface.server_command_handlers.handle_server_stop")
    async def test_restart_chains_stop_then_start(self, mock_stop, mock_start):
        mock_stop.return_value = {"status": "stopped"}
        mock_start.return_value = {"pid": 99}
        from orb.interface.server_command_handlers import handle_server_restart

        result = await handle_server_restart(_args())

        mock_stop.assert_called_once()
        mock_start.assert_called_once()
        assert result == {"stop": {"status": "stopped"}, "start": {"pid": 99}}

    @pytest.mark.asyncio
    @patch("orb.interface.server_command_handlers.handle_server_start")
    @patch("orb.interface.server_command_handlers.handle_server_stop")
    async def test_restart_same_args_passed_to_both(self, mock_stop, mock_start):
        mock_stop.return_value = {}
        mock_start.return_value = {}
        args = _args(timeout=30)
        from orb.interface.server_command_handlers import handle_server_restart

        await handle_server_restart(args)

        assert mock_stop.call_args[0][0] is args
        assert mock_start.call_args[0][0] is args


# ---------------------------------------------------------------------------
# handle_server_reload
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestHandleServerReload:
    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_loopback_success_returns_method_loopback_ipc(
        self, mock_resolve_configs, _mock_paths
    ):
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        response_body = json.dumps({"reloaded": True}).encode()
        mock_http_resp = MagicMock()
        mock_http_resp.status = 200
        mock_http_resp.read.return_value = response_body

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_http_resp

        mock_daemon = MagicMock()

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            with patch(
                "orb.interface.server_command_handlers._read_loopback_token",
                return_value=None,
            ):
                with patch("http.client.HTTPConnection", return_value=mock_conn):
                    from orb.interface.server_command_handlers import handle_server_reload

                    result = await handle_server_reload(_args())

        assert result["method"] == "loopback-ipc"
        assert result["status"] == 200

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_loopback_oserror_falls_back_to_sighup(
        self, mock_resolve_configs, _mock_paths
    ):
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.reload.return_value = {"method": "sighup", "sent": True}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            with patch(
                "orb.interface.server_command_handlers._read_loopback_token",
                return_value=None,
            ):
                with patch("http.client.HTTPConnection", side_effect=OSError("refused")):
                    from orb.interface.server_command_handlers import handle_server_reload

                    result = await handle_server_reload(_args())

        # ipc_error is always present on the fallback path
        assert "ipc_error" in result
        assert "refused" in result["ipc_error"]
        mock_daemon.reload.assert_called_once()

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_valueerror_non_loopback_falls_back_to_sighup(
        self, mock_resolve_configs, _mock_paths
    ):
        # A public host causes ValueError inside _loopback_reload_request's validation
        server_cfg = _make_server_config(host="10.0.0.1", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.reload.return_value = {"method": "sighup", "sent": True}

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            with patch(
                "orb.interface.server_command_handlers._read_loopback_token",
                return_value=None,
            ):
                # Patch asyncio.to_thread to raise ValueError (simulating the host validation)
                with patch(
                    "asyncio.to_thread",
                    side_effect=ValueError("reload IPC requires a loopback host"),
                ):
                    from orb.interface.server_command_handlers import handle_server_reload

                    result = await handle_server_reload(_args())

        assert "ipc_error" in result
        mock_daemon.reload.assert_called_once()

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_sends_bearer_token_when_token_file_exists(
        self, mock_resolve_configs, _mock_paths
    ):
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        captured_headers: dict[str, str] = {}

        def fake_conn_request(method, path, body, headers=None):
            captured_headers.update(headers or {})

        mock_http_resp = MagicMock()
        mock_http_resp.status = 200
        mock_http_resp.read.return_value = b"{}"

        mock_conn = MagicMock()
        mock_conn.request = fake_conn_request
        mock_conn.getresponse.return_value = mock_http_resp

        mock_daemon = MagicMock()

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            with patch(
                "orb.interface.server_command_handlers._read_loopback_token",
                return_value="my-secret-token",
            ):
                with patch("http.client.HTTPConnection", return_value=mock_conn):
                    from orb.interface.server_command_handlers import handle_server_reload

                    await handle_server_reload(_args())

        assert captured_headers.get("Authorization") == "Bearer my-secret-token"

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/srv.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_reload_omits_bearer_header_when_no_token(
        self, mock_resolve_configs, _mock_paths
    ):
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        mock_resolve_configs.return_value = (server_cfg, None)

        captured_headers: dict[str, str] = {}

        def fake_conn_request(method, path, body, headers=None):
            captured_headers.update(headers or {})

        mock_http_resp = MagicMock()
        mock_http_resp.status = 200
        mock_http_resp.read.return_value = b"{}"

        mock_conn = MagicMock()
        mock_conn.request = fake_conn_request
        mock_conn.getresponse.return_value = mock_http_resp

        mock_daemon = MagicMock()

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            with patch(
                "orb.interface.server_command_handlers._read_loopback_token",
                return_value=None,
            ):
                with patch("http.client.HTTPConnection", return_value=mock_conn):
                    from orb.interface.server_command_handlers import handle_server_reload

                    await handle_server_reload(_args())

        assert "Authorization" not in captured_headers


# ---------------------------------------------------------------------------
# handle_server_logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestHandleServerLogs:
    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/orb-server.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_logs_returns_log_file_path_and_tail(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config()
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.tail_log.return_value = "line1\nline2\n"

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_logs

            result = await handle_server_logs(_args(lines=20))

        assert result["log_file"] == "/tmp/orb-server.log"
        assert result["tail"] == "line1\nline2\n"
        mock_daemon.tail_log.assert_called_once_with(log_file="/tmp/orb-server.log", lines=20)

    @pytest.mark.asyncio
    @patch(_RESOLVE_PATHS, return_value=("/tmp/srv.pid", "/tmp/orb-server.log", "/tmp"))
    @patch(_RESOLVE_CONFIGS)
    async def test_logs_defaults_to_50_lines(self, mock_resolve_configs, _mock_paths):
        server_cfg = _make_server_config()
        mock_resolve_configs.return_value = (server_cfg, None)

        mock_daemon = MagicMock()
        mock_daemon.tail_log.return_value = ""

        with patch("orb.interface.server_daemon", mock_daemon, create=True):
            from orb.interface.server_command_handlers import handle_server_logs

            await handle_server_logs(_args(lines=None))

        call_kwargs = mock_daemon.tail_log.call_args.kwargs
        assert call_kwargs["lines"] == 50
