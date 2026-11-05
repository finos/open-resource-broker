"""Tests for the URLs reported by ``orb server start`` and ``orb server status``."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.interface import server_command_handlers as handlers

_BUILD_RUNTIME = "orb.interface.server_command_handlers._build_runtime"
_RESOLVE_PATHS = "orb.interface.server_command_handlers._resolve_lifecycle_paths"
_RESOLVE_CONFIGS = "orb.interface.server_command_handlers._resolve_configs"


def _server(host: str = "127.0.0.1", port: int = 8000) -> SimpleNamespace:
    return SimpleNamespace(host=host, port=port)


def _ui(mode: str, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, mode=mode, backend_port=8001, frontend_port=3000)


def _args(**overrides) -> argparse.Namespace:
    base = {"foreground": False, "api_only": False, "socket_path": None, "timeout": None}
    base.update(overrides)
    return argparse.Namespace(**base)


# (case id, server config, ui config, args overrides, expected urls)
MODES = [
    ("no-ui", _server(), None, {}, {"api_url": "http://127.0.0.1:8000"}),
    ("ui-disabled", _server(), _ui("embedded", False), {}, {"api_url": "http://127.0.0.1:8000"}),
    (
        "api-only",
        _server(port=9100),
        _ui("embedded"),
        {"api_only": True},
        {"api_url": "http://127.0.0.1:9100"},
    ),
    (
        "embedded",
        _server(),
        _ui("embedded"),
        {},
        {"api_url": "http://127.0.0.1:8000/orb", "ui_url": "http://127.0.0.1:8000/"},
    ),
    (
        "split",
        _server(),
        _ui("split"),
        {},
        {"api_url": "http://127.0.0.1:8000", "ui_url": "http://127.0.0.1:8001/"},
    ),
    (
        "dev",
        _server(),
        _ui("dev"),
        {},
        {"api_url": "http://127.0.0.1:8001/orb", "ui_url": "http://127.0.0.1:3000/"},
    ),
    (
        "ipv6-host",
        _server(host="::1"),
        None,
        {},
        {"api_url": "http://[::1]:8000"},
    ),
    ("wildcard-v4", _server(host="0.0.0.0"), None, {}, {"api_url": "http://127.0.0.1:8000"}),  # noqa: S104
    (
        "wildcard-v6",
        _server(host="::"),
        _ui("embedded"),
        {},
        {"api_url": "http://localhost:8000/orb", "ui_url": "http://localhost:8000/"},
    ),
    (
        "dev-wildcard",
        _server(host="0.0.0.0"),  # noqa: S104
        _ui("dev"),
        {},
        {"api_url": "http://127.0.0.1:8001/orb", "ui_url": "http://127.0.0.1:3000/"},
    ),
    (
        "unix-socket",
        _server(),
        None,
        {"socket_path": "/tmp/orb.sock"},
        {"api_url": "unix:/tmp/orb.sock"},
    ),
]
MODE_IDS = [case[0] for case in MODES]


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize(("_id", "server", "ui", "overrides", "expected"), MODES, ids=MODE_IDS)
def test_server_urls_per_mode(_id, server, ui, overrides, expected) -> None:
    urls = handlers._server_urls(
        server,
        ui,
        api_only=overrides.get("api_only", False),
        socket_path=overrides.get("socket_path"),
    )
    assert urls == expected


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
@pytest.mark.parametrize("foreground", [False, True], ids=["daemonized", "foreground"])
@pytest.mark.parametrize(("_id", "server", "ui", "overrides", "expected"), MODES, ids=MODE_IDS)
async def test_start_prints_urls_in_every_mode(
    _id, server, ui, overrides, expected, foreground, capsys
) -> None:
    runtime = AsyncMock(return_value={"exit_code": 0})
    daemon = MagicMock()
    daemon.start.return_value = {"pid": 42, "status": "started"}
    daemon._expand.side_effect = lambda p: MagicMock()

    with (
        patch(_BUILD_RUNTIME, return_value=(runtime, server, ui)),
        patch(_RESOLVE_PATHS, return_value=("/tmp/p.pid", "/tmp/p.log", "/tmp")),
        patch("orb.interface.server_daemon", daemon, create=True),
        patch("os.close"),
    ):
        result = await handlers.handle_server_start(_args(foreground=foreground, **overrides))

    err = capsys.readouterr().err
    assert err.count("ORB API listening on ") == 1
    assert f"ORB API listening on {expected['api_url']}\n" in err
    if "ui_url" in expected:
        assert f"ORB UI available at {expected['ui_url']}\n" in err
    else:
        assert "ORB UI" not in err
    for key, value in expected.items():
        assert result[key] == value


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_start_failure_does_not_announce_urls(capsys) -> None:
    daemon = MagicMock()
    daemon.start.side_effect = RuntimeError("already running")

    with (
        patch(_BUILD_RUNTIME, return_value=(AsyncMock(), _server(), None)),
        patch(_RESOLVE_PATHS, return_value=("/tmp/p.pid", "/tmp/p.log", "/tmp")),
        patch("orb.interface.server_daemon", daemon, create=True),
        pytest.raises(Exception),  # noqa: B017 - the error is wrapped by the interface decorator
    ):
        await handlers.handle_server_start(_args())

    assert "listening on" not in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
@pytest.mark.parametrize(("_id", "server", "ui", "overrides", "expected"), MODES, ids=MODE_IDS)
async def test_status_reports_urls_when_running(_id, server, ui, overrides, expected) -> None:
    daemon = MagicMock()
    daemon.status.return_value = {"pid": 7, "running": True}

    with (
        patch(_RESOLVE_CONFIGS, return_value=(server, ui)),
        patch(_RESOLVE_PATHS, return_value=("/tmp/p.pid", "/tmp/p.log", "/tmp")),
        patch("orb.interface.server_daemon", daemon, create=True),
    ):
        result = await handlers.handle_server_status(_args(**overrides))

    for key, value in expected.items():
        assert result[key] == value
    assert ("ui_url" in result) == ("ui_url" in expected)


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
async def test_status_omits_urls_when_not_running() -> None:
    daemon = MagicMock()
    daemon.status.return_value = {"pid": None, "running": False}

    with (
        patch(_RESOLVE_CONFIGS, return_value=(_server(), None)),
        patch(_RESOLVE_PATHS, return_value=("/tmp/p.pid", "/tmp/p.log", "/tmp")),
        patch("orb.interface.server_daemon", daemon, create=True),
    ):
        result = await handlers.handle_server_status(_args())

    assert "api_url" not in result


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["running", "failed"])
async def test_daemon_start_announces_only_after_success(outcome, capsys) -> None:
    daemon = MagicMock()
    if outcome == "failed":
        daemon.start.side_effect = RuntimeError("daemon failed")
    else:
        daemon.start.return_value = {"pid": 5, "status": "running"}

    with (
        patch(_BUILD_RUNTIME, return_value=(AsyncMock(), _server(), None)),
        patch(_RESOLVE_PATHS, return_value=("/tmp/p.pid", "/tmp/p.log", "/tmp")),
        patch("orb.interface.server_daemon", daemon, create=True),
    ):
        try:
            result = await handlers.handle_server_start(_args())
        except Exception:  # noqa: BLE001 - wrapped by the interface decorator
            result = {}

    assert "listening on" not in capsys.readouterr().err
    assert "api_url" not in result
