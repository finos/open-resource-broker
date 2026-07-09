"""Unit tests for server_runtime foreground entry-points."""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_server_config(
    host="127.0.0.1",
    port=8000,
    workers=1,
    log_level="info",
):
    cfg = MagicMock()
    cfg.host = host
    cfg.port = port
    cfg.workers = workers
    cfg.log_level = log_level
    return cfg


def _make_ui_config(backend_port=3001, frontend_port=3000):
    cfg = MagicMock()
    cfg.backend_port = backend_port
    cfg.frontend_port = frontend_port
    return cfg


# ---------------------------------------------------------------------------
# _reload_config_from_signal
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestReloadConfigFromSignal:
    def test_happy_path_calls_cm_reload(self):
        """ConfigurationManager.reload() is called when a valid cm is supplied."""
        mock_cm = MagicMock()
        logger = MagicMock()

        from orb.interface.server_runtime import _reload_config_from_signal

        _reload_config_from_signal(logger, mock_cm)

        mock_cm.reload.assert_called_once()

    def test_reload_failure_logs_error_no_raise(self):
        """reload() raising an exception must not propagate; error is logged."""
        mock_cm = MagicMock()
        mock_cm.reload.side_effect = ValueError("bad config")
        logger = MagicMock()

        from orb.interface.server_runtime import _reload_config_from_signal

        # Must not raise
        _reload_config_from_signal(logger, mock_cm)

        # At least one error log emitted
        logger.error.assert_called()


# ---------------------------------------------------------------------------
# run_api_foreground
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestRunApiForeground:
    @pytest.mark.asyncio
    async def test_serves_and_returns_stopped_message(self):
        server_cfg = _make_server_config()

        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)
        mock_server.should_exit = False

        installed_signals: dict[int, Callable[..., Any]] = {}

        def fake_signal(signum, handler):
            installed_signals[signum] = handler

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", return_value=MagicMock()):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal", side_effect=fake_signal):
                        from orb.interface.server_runtime import run_api_foreground

                        result = await run_api_foreground(server_cfg)

        assert result == {"message": "Server stopped"}
        mock_server.serve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_installs_sigint_sigterm_sighup(self):
        server_cfg = _make_server_config()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        installed: list[int] = []

        def fake_signal(signum, handler):
            installed.append(signum)

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", return_value=MagicMock()):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal", side_effect=fake_signal):
                        from orb.interface.server_runtime import run_api_foreground

                        await run_api_foreground(server_cfg)

        assert signal.SIGINT in installed
        assert signal.SIGTERM in installed
        assert signal.SIGHUP in installed

    @pytest.mark.asyncio
    async def test_sighup_handler_invokes_reload_config(self):
        """The SIGHUP handler must call _reload_config_from_signal when cm is available."""
        server_cfg = _make_server_config()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        handlers: dict[int, Callable[..., Any]] = {}

        def fake_signal(signum, handler):
            handlers[signum] = handler

        mock_cm = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_cm

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", return_value=MagicMock()):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal", side_effect=fake_signal):
                        with patch(
                            "orb.interface.server_runtime._reload_config_from_signal"
                        ) as mock_reload:
                            with patch(
                                "orb.infrastructure.di.container.get_container",
                                return_value=mock_container,
                            ):
                                from orb.interface.server_runtime import run_api_foreground

                                await run_api_foreground(server_cfg)

                                # Must invoke the handler while the patch is still active
                                sighup_handler = handlers.get(signal.SIGHUP)
                                assert sighup_handler is not None, "SIGHUP handler not installed"
                                sighup_handler(signal.SIGHUP, None)
                                mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_sigint_sets_should_exit(self):
        server_cfg = _make_server_config()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()
        mock_server.should_exit = False

        handlers: dict[int, Callable[..., Any]] = {}

        def fake_signal(signum, handler):
            handlers[signum] = handler

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", return_value=MagicMock()):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal", side_effect=fake_signal):
                        from orb.interface.server_runtime import run_api_foreground

                        await run_api_foreground(server_cfg)

                        sigint_handler = handlers.get(signal.SIGINT)
                        assert sigint_handler is not None
                        sigint_handler(signal.SIGINT, None)
                        assert mock_server.should_exit is True


# ---------------------------------------------------------------------------
# run_embedded_foreground
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cli
class TestRunEmbeddedForeground:
    @pytest.mark.asyncio
    async def test_raises_when_reflex_not_installed(self):
        ui_cfg = _make_ui_config()

        with patch("shutil.which", return_value=None):
            from orb.interface.server_runtime import run_embedded_foreground

            with pytest.raises(ImportError, match="reflex"):
                await run_embedded_foreground(ui_cfg)

    @pytest.mark.asyncio
    async def test_cwd_points_to_orb_ui_directory(self):
        ui_cfg = _make_ui_config()

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        loop_signals: list[int] = []

        def fake_add_signal_handler(sig, callback, *args):
            loop_signals.append(sig)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", fake_add_signal_handler):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg)

        call_kwargs = mock_exec.call_args
        # cwd must point at orb/ui directory
        cwd = call_kwargs.kwargs.get("cwd") or call_kwargs[1].get("cwd", "")
        assert cwd.endswith("ui"), f"cwd={cwd!r} should end with 'ui'"

    @pytest.mark.asyncio
    async def test_environment_includes_orb_mode_and_ports(self):
        ui_cfg = _make_ui_config(backend_port=3001, frontend_port=3000)

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg)

        call_kwargs = mock_exec.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
        assert env.get("ORB_MODE") == "embedded"
        assert env.get("ORB_UI_BACKEND_PORT") == "3001"
        # ORB_UI_FRONTEND_PORT is deliberately unset in embedded mode --
        # Reflex 0.9.x rejects a user-supplied frontend_port when running
        # ``reflex run --backend-only``, so run_embedded_foreground pops
        # it out of the env even if the parent process had it set.
        assert env.get("ORB_UI_FRONTEND_PORT") is None

    @pytest.mark.asyncio
    async def test_sighup_not_registered_with_embedded_loop(self):
        """SIGHUP must NOT be added to the event loop for embedded mode."""
        ui_cfg = _make_ui_config()

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        registered_sigs: list[int] = []

        def fake_add_signal_handler(sig, callback, *args):
            registered_sigs.append(sig)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", fake_add_signal_handler):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg)

        assert signal.SIGHUP not in registered_sigs

    @pytest.mark.asyncio
    async def test_sigint_and_sigterm_registered_with_embedded_loop(self):
        ui_cfg = _make_ui_config()

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        registered_sigs: list[int] = []

        def fake_add_signal_handler(sig, callback, *args):
            registered_sigs.append(sig)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", fake_add_signal_handler):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg)

        assert signal.SIGINT in registered_sigs
        assert signal.SIGTERM in registered_sigs

    @pytest.mark.asyncio
    async def test_returns_exit_code_from_reflex(self):
        ui_cfg = _make_ui_config()

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        result = await run_embedded_foreground(ui_cfg)

        assert result["exit_code"] == 0
        assert "Reflex exited" in result["message"]
