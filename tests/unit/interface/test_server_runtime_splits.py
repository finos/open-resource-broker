"""Unit tests for server_runtime — _run_split_mode and run_api_foreground socket path.

Covers:
- run_api_foreground with socket_path (Unix socket config branch)
- run_api_foreground SIGHUP when _cm is None (logs error, skips)
- run_api_foreground ImportError when uvicorn not installed
- _run_split_mode: happy path, SIGINT/SIGTERM forwarded to both procs
- _run_split_mode: cleanup when one proc still running after gather
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_server_config(host="127.0.0.1", port=8000, workers=1, log_level="info"):
    cfg = MagicMock()
    cfg.host = host
    cfg.port = port
    cfg.workers = workers
    cfg.log_level = log_level
    return cfg


def _make_ui_config(mode="split", backend_port=3001, frontend_port=3000):
    cfg = MagicMock()
    cfg.mode = mode
    cfg.backend_port = backend_port
    cfg.frontend_port = frontend_port
    return cfg


# ---------------------------------------------------------------------------
# run_api_foreground — socket_path branch (Unix domain socket)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunApiForegroundSocketPath:
    @pytest.mark.asyncio
    async def test_socket_path_uses_uds_config(self):
        """When socket_path is provided, uvicorn.Config should use uds= parameter."""
        server_cfg = _make_server_config()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)
        mock_server.should_exit = False

        uds_configs = []

        def fake_config(*args, **kwargs):
            uds_configs.append(kwargs)
            return MagicMock()

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", side_effect=fake_config):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal"):
                        from orb.interface.server_runtime import run_api_foreground

                        result = await run_api_foreground(server_cfg, socket_path="/tmp/orb.sock")

        assert result == {"message": "Server stopped"}
        assert len(uds_configs) == 1
        assert uds_configs[0].get("uds") == "/tmp/orb.sock"
        # UDS mode must use single worker
        assert uds_configs[0].get("workers") == 1

    @pytest.mark.asyncio
    async def test_socket_path_overrides_host_port_config(self):
        """UDS mode must not set host/port in uvicorn.Config."""
        server_cfg = _make_server_config()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        configs_made = []

        def fake_config(*args, **kwargs):
            configs_made.append(kwargs)
            return MagicMock()

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", side_effect=fake_config):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal"):
                        from orb.interface.server_runtime import run_api_foreground

                        await run_api_foreground(server_cfg, socket_path="/tmp/test.sock")

        cfg = configs_made[0]
        assert "host" not in cfg
        assert "port" not in cfg


# ---------------------------------------------------------------------------
# run_api_foreground — SIGHUP when _cm is None
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunApiForegroundSigHupCmNone:
    @pytest.mark.asyncio
    async def test_sighup_logs_error_when_cm_is_none(self):
        """SIGHUP handler must log an error and return safely when _cm is None."""
        server_cfg = _make_server_config()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        handlers: dict = {}

        def fake_signal(signum, handler):
            handlers[signum] = handler

        with patch("orb.api.server.create_fastapi_app", return_value=MagicMock()):
            with patch("uvicorn.Config", return_value=MagicMock()):
                with patch("uvicorn.Server", return_value=mock_server):
                    with patch("signal.signal", side_effect=fake_signal):
                        # Make get_container() raise so _cm stays None
                        with patch(
                            "orb.infrastructure.di.container.get_container",
                            side_effect=RuntimeError("no container"),
                        ):
                            from orb.interface.server_runtime import run_api_foreground

                            await run_api_foreground(server_cfg)

        sighup_handler = handlers.get(signal.SIGHUP)
        assert sighup_handler is not None

        # Calling the handler with _cm=None must not raise
        sighup_handler(signal.SIGHUP, None)  # Should silently log error and return


# ---------------------------------------------------------------------------
# run_api_foreground — ImportError when uvicorn missing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunApiForegroundImportError:
    @pytest.mark.asyncio
    async def test_raises_import_error_when_uvicorn_missing(self):
        """run_api_foreground raises ImportError with install hint when uvicorn absent."""
        server_cfg = _make_server_config()

        # Patch the import inside run_api_foreground's body by removing uvicorn from sys.modules
        import sys as _sys

        from orb.interface.server_runtime import run_api_foreground

        original = _sys.modules.pop("uvicorn", None)
        try:
            with patch.dict(_sys.modules, {"uvicorn": None}):
                with pytest.raises(ImportError, match="pip install orb-py"):
                    await run_api_foreground(server_cfg)
        finally:
            if original is not None:
                _sys.modules["uvicorn"] = original


# ---------------------------------------------------------------------------
# _run_split_mode — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunSplitMode:
    @pytest.mark.asyncio
    async def test_split_mode_returns_combined_exit_codes(self):
        """_run_split_mode should wait for both procs and return combined result."""
        from orb.interface.server_runtime import _run_split_mode

        ui_cfg = _make_ui_config(mode="split", backend_port=3001)
        server_cfg = _make_server_config(host="127.0.0.1", port=8000)
        logger = MagicMock()

        api_proc = AsyncMock()
        api_proc.pid = 100
        api_proc.returncode = 0
        api_proc.wait = AsyncMock(return_value=0)

        reflex_proc = AsyncMock()
        reflex_proc.pid = 101
        reflex_proc.returncode = 0
        reflex_proc.wait = AsyncMock(return_value=0)

        procs = [api_proc, reflex_proc]
        call_count = 0

        async def fake_create_subprocess(*args, **kwargs):
            nonlocal call_count
            proc = procs[call_count % len(procs)]
            call_count += 1
            return proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        result = await _run_split_mode(ui_cfg, server_cfg, None, logger)

        assert result["exit_code"] == 0
        assert "api_exit_code" in result
        assert "reflex_exit_code" in result

    @pytest.mark.asyncio
    async def test_split_mode_reflex_nonzero_exit_code_propagated(self):
        """Non-zero Reflex exit code should be in result."""
        from orb.interface.server_runtime import _run_split_mode

        ui_cfg = _make_ui_config(mode="split", backend_port=3001)
        server_cfg = _make_server_config()
        logger = MagicMock()

        api_proc = AsyncMock()
        api_proc.pid = 200
        api_proc.returncode = 0
        api_proc.wait = AsyncMock(return_value=0)

        reflex_proc = AsyncMock()
        reflex_proc.pid = 201
        reflex_proc.returncode = 1
        reflex_proc.wait = AsyncMock(return_value=1)

        procs = [api_proc, reflex_proc]
        call_count = 0

        async def fake_create_subprocess(*args, **kwargs):
            nonlocal call_count
            proc = procs[call_count % len(procs)]
            call_count += 1
            return proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        result = await _run_split_mode(ui_cfg, server_cfg, None, logger)

        assert result["reflex_exit_code"] == 1
        assert result["exit_code"] == 1  # max(0, 1)

    @pytest.mark.asyncio
    async def test_split_mode_scheduler_env_forwarded(self):
        """When scheduler is provided, ORB_SCHEDULER_OVERRIDE must be set in both envs."""
        from orb.interface.server_runtime import _run_split_mode

        ui_cfg = _make_ui_config(mode="split", backend_port=3001)
        server_cfg = _make_server_config()
        logger = MagicMock()

        api_proc = AsyncMock()
        api_proc.pid = 300
        api_proc.returncode = 0
        api_proc.wait = AsyncMock(return_value=0)

        reflex_proc = AsyncMock()
        reflex_proc.pid = 301
        reflex_proc.returncode = 0
        reflex_proc.wait = AsyncMock(return_value=0)

        captured_envs: list[dict] = []

        procs = [api_proc, reflex_proc]
        call_count = 0

        async def fake_create_subprocess(*args, **kwargs):
            nonlocal call_count
            captured_envs.append(kwargs.get("env", {}))
            proc = procs[call_count % len(procs)]
            call_count += 1
            return proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        await _run_split_mode(ui_cfg, server_cfg, "my-scheduler", logger)

        assert len(captured_envs) >= 2
        # Both procs should have the scheduler env
        for env in captured_envs:
            assert env.get("ORB_SCHEDULER_OVERRIDE") == "my-scheduler"

    @pytest.mark.asyncio
    async def test_split_mode_orb_mode_remote_set_for_reflex(self):
        """The Reflex process environment must have ORB_MODE=remote."""
        from orb.interface.server_runtime import _run_split_mode

        ui_cfg = _make_ui_config(mode="split", backend_port=3001)
        server_cfg = _make_server_config()
        logger = MagicMock()

        api_proc = AsyncMock()
        api_proc.pid = 400
        api_proc.returncode = 0
        api_proc.wait = AsyncMock(return_value=0)

        reflex_proc = AsyncMock()
        reflex_proc.pid = 401
        reflex_proc.returncode = 0
        reflex_proc.wait = AsyncMock(return_value=0)

        captured_envs: list[dict] = []

        procs = [api_proc, reflex_proc]
        call_count = 0

        async def fake_create_subprocess(*args, **kwargs):
            nonlocal call_count
            captured_envs.append(kwargs.get("env", {}))
            proc = procs[call_count % len(procs)]
            call_count += 1
            return proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        await _run_split_mode(ui_cfg, server_cfg, None, logger)

        # Second proc env (reflex) should have ORB_MODE=remote
        assert len(captured_envs) >= 2
        reflex_env = captured_envs[1]
        assert reflex_env.get("ORB_MODE") == "remote"

    @pytest.mark.asyncio
    async def test_split_mode_cleanup_kills_still_running_proc(self):
        """If a proc hasn't exited, SIGTERM must be sent to its process group."""
        from orb.interface.server_runtime import _run_split_mode

        ui_cfg = _make_ui_config(mode="split", backend_port=3001)
        server_cfg = _make_server_config()
        logger = MagicMock()

        api_proc = AsyncMock()
        api_proc.pid = 500
        api_proc.returncode = None  # still running
        api_proc.wait = AsyncMock(return_value=0)

        reflex_proc = AsyncMock()
        reflex_proc.pid = 501
        reflex_proc.returncode = 0
        reflex_proc.wait = AsyncMock(return_value=0)

        procs = [api_proc, reflex_proc]
        call_count = 0

        async def fake_create_subprocess(*args, **kwargs):
            nonlocal call_count
            proc = procs[call_count % len(procs)]
            call_count += 1
            return proc

        killpg_calls: list[tuple] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", return_value=999):
                    with patch("os.killpg", side_effect=fake_killpg):
                        loop = asyncio.get_event_loop()
                        with patch.object(loop, "add_signal_handler", MagicMock()):
                            await _run_split_mode(ui_cfg, server_cfg, None, logger)

        # The still-running api_proc should have received SIGTERM
        assert any(sig == signal.SIGTERM for _, sig in killpg_calls)


# ---------------------------------------------------------------------------
# run_embedded_foreground — embedded mode cleanup when proc still running
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunEmbeddedForegroundCleanup:
    @pytest.mark.asyncio
    async def test_still_running_proc_receives_sigterm_on_exit(self):
        """When the proc is still running at finally block, SIGTERM is sent to its pgid."""
        from orb.interface.server_runtime import run_embedded_foreground

        ui_cfg = MagicMock()
        ui_cfg.mode = "embedded"
        ui_cfg.backend_port = 3001

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = None  # still running
        mock_proc.wait = AsyncMock(return_value=0)

        killpg_calls: list = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))
            mock_proc.returncode = 0  # mark as exited after kill

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("os.getpgid", return_value=5678):
                    with patch("os.killpg", side_effect=fake_killpg):
                        loop = asyncio.get_event_loop()
                        with patch.object(loop, "add_signal_handler", MagicMock()):
                            await run_embedded_foreground(ui_cfg)

        assert any(sig == signal.SIGTERM for _, sig in killpg_calls)
