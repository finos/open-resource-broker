"""Additional unit tests for server_runtime.

Focuses on run_embedded_foreground dev-mode, split-mode (unknown-mode fallback),
_find_reflex_bin, _orb_ui_dir, and _run_split_mode error paths.
"""

from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_ui_config(mode="embedded", backend_port=3001, frontend_port=3000):
    cfg = MagicMock()
    cfg.mode = mode
    cfg.backend_port = backend_port
    cfg.frontend_port = frontend_port
    cfg.enabled = True
    return cfg


def _make_server_config(host="127.0.0.1", port=8000, workers=1, log_level="info"):
    cfg = MagicMock()
    cfg.host = host
    cfg.port = port
    cfg.workers = workers
    cfg.log_level = log_level
    return cfg


# ---------------------------------------------------------------------------
# _find_reflex_bin
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindReflexBin:
    def test_returns_path_when_found(self):
        from orb.interface.server_runtime import _find_reflex_bin

        with patch("shutil.which", return_value="/usr/local/bin/reflex"):
            result = _find_reflex_bin()

        assert result == "/usr/local/bin/reflex"

    def test_raises_import_error_when_not_found(self):
        from orb.interface.server_runtime import _find_reflex_bin

        with patch("shutil.which", return_value=None):
            with pytest.raises(ImportError, match="reflex"):
                _find_reflex_bin()


# ---------------------------------------------------------------------------
# _orb_ui_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrbUiDir:
    def test_ends_with_ui(self):
        from orb.interface.server_runtime import _orb_ui_dir

        result = _orb_ui_dir()

        assert result.endswith("ui"), f"Expected path ending with 'ui', got: {result!r}"

    def test_is_absolute(self):
        from orb.interface.server_runtime import _orb_ui_dir

        result = _orb_ui_dir()

        assert os.path.isabs(result), f"Expected absolute path, got: {result!r}"


# ---------------------------------------------------------------------------
# run_embedded_foreground — dev mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunEmbeddedForegroundDevMode:
    @pytest.mark.asyncio
    async def test_dev_mode_spawns_reflex_run(self):
        ui_cfg = _make_ui_config(mode="dev")

        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        spawn_calls = []

        async def fake_create_subprocess(*args, **kwargs):
            spawn_calls.append(args)
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        result = await run_embedded_foreground(ui_cfg)

        assert spawn_calls, "Expected at least one subprocess spawn"
        # First positional arg is the reflex binary
        assert "/usr/bin/reflex" in spawn_calls[0]
        # Second arg is "run"
        assert "run" in spawn_calls[0]
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_dev_mode_sets_orb_mode_env(self):
        ui_cfg = _make_ui_config(mode="dev", backend_port=3001, frontend_port=3000)

        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        captured_env: dict = {}

        async def fake_create_subprocess(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg)

        assert captured_env.get("ORB_MODE") == "dev"
        assert captured_env.get("ORB_UI_BACKEND_PORT") == "3001"
        assert captured_env.get("ORB_UI_FRONTEND_PORT") == "3000"

    @pytest.mark.asyncio
    async def test_dev_mode_scheduler_env_set_when_provided(self):
        ui_cfg = _make_ui_config(mode="dev")

        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        captured_env: dict = {}

        async def fake_create_subprocess(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg, scheduler="my-sched")

        assert captured_env.get("ORB_SCHEDULER_OVERRIDE") == "my-sched"

    @pytest.mark.asyncio
    async def test_dev_mode_exit_code_in_result(self):
        ui_cfg = _make_ui_config(mode="dev")

        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = 42
        mock_proc.wait = AsyncMock(return_value=42)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        result = await run_embedded_foreground(ui_cfg)

        assert result["exit_code"] == 42
        assert "Reflex exited" in result["message"]

    @pytest.mark.asyncio
    async def test_sighup_not_registered_dev_mode(self):
        """SIGHUP must NOT be added to event loop in dev mode."""
        ui_cfg = _make_ui_config(mode="dev")

        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        registered: list[int] = []

        def fake_add_handler(sig, callback, *args):
            registered.append(sig)

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", fake_add_handler):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg)

        assert signal.SIGHUP not in registered


# ---------------------------------------------------------------------------
# run_embedded_foreground — embedded mode with server_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunEmbeddedForegroundWithServerConfig:
    @pytest.mark.asyncio
    async def test_embedded_mode_uses_server_config_port(self):
        ui_cfg = _make_ui_config(mode="embedded", backend_port=4000)
        server_cfg = _make_server_config(port=5000)

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        captured_env: dict = {}

        async def fake_create_subprocess(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        result = await run_embedded_foreground(ui_cfg, server_config=server_cfg)

        # When server_config provided, its port wins over ui_config.backend_port
        assert captured_env.get("ORB_UI_BACKEND_PORT") == "5000"
        assert captured_env.get("ORB_MODE") == "embedded"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_embedded_mode_fallback_to_ui_backend_port_without_server_config(self):
        ui_cfg = _make_ui_config(mode="embedded", backend_port=7777)

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        captured_env: dict = {}

        async def fake_create_subprocess(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg, server_config=None)

        assert captured_env.get("ORB_UI_BACKEND_PORT") == "7777"


# ---------------------------------------------------------------------------
# run_embedded_foreground — unknown mode is normalized to "embedded"
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunEmbeddedForegroundUnknownMode:
    @pytest.mark.asyncio
    async def test_unknown_mode_normalizes_to_embedded(self):
        """An unrecognised mode string is normalized to 'embedded' (source code guarantee)."""
        ui_cfg = _make_ui_config(mode="bogus")

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        captured_env: dict = {}

        async def fake_create_subprocess(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        result = await run_embedded_foreground(ui_cfg)

        # The unknown mode is normalised to "embedded" — ORB_MODE should be "embedded"
        assert captured_env.get("ORB_MODE") == "embedded"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_unknown_mode_uses_ui_config_backend_port_when_no_server_config(self):
        """Unknown mode normalised to embedded, uses ui_config.backend_port."""
        ui_cfg = _make_ui_config(mode="custom", backend_port=6789)

        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        captured_env: dict = {}

        async def fake_create_subprocess(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return mock_proc

        with patch("shutil.which", return_value="/usr/bin/reflex"):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
                with patch("os.getpgid", side_effect=ProcessLookupError):
                    loop = asyncio.get_event_loop()
                    with patch.object(loop, "add_signal_handler", MagicMock()):
                        from orb.interface.server_runtime import run_embedded_foreground

                        await run_embedded_foreground(ui_cfg, server_config=None)

        assert captured_env.get("ORB_UI_BACKEND_PORT") == "6789"


# ---------------------------------------------------------------------------
# run_embedded_foreground — split mode requires server_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunEmbeddedForegroundSplitMode:
    @pytest.mark.asyncio
    async def test_split_mode_raises_without_server_config(self):
        ui_cfg = _make_ui_config(mode="split")

        with pytest.raises(ValueError, match="server_config is required"):
            from orb.interface.server_runtime import run_embedded_foreground

            await run_embedded_foreground(ui_cfg, server_config=None)

    @pytest.mark.asyncio
    async def test_split_mode_calls_run_split_mode(self):
        ui_cfg = _make_ui_config(mode="split")
        server_cfg = _make_server_config()

        with patch(
            "orb.interface.server_runtime._run_split_mode",
            new_callable=AsyncMock,
            return_value={"message": "Split mode stopped", "exit_code": 0},
        ) as mock_split:
            from orb.interface.server_runtime import run_embedded_foreground

            result = await run_embedded_foreground(ui_cfg, server_config=server_cfg)

        mock_split.assert_awaited_once()
        assert "Split mode stopped" in result["message"]
