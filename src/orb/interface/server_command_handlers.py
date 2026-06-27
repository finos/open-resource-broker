"""CLI handlers for the ``orb server`` lifecycle commands.

start    → daemonize (or ``--foreground``) the API + optional embedded UI
stop     → SIGTERM → wait → SIGKILL the running daemon's process group
status   → PID-file check plus a best-effort ``/health`` probe
restart  → stop + start
reload   → SIGHUP the daemon
logs     → tail the daemon's log file
"""

from __future__ import annotations

import json
from typing import Any, cast

from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.infrastructure.logging.logger import get_logger


def _resolve_lifecycle_paths(server_config: Any) -> tuple[str, str, str]:
    """Resolve (pid_file, log_file, working_dir) honouring config + platform_dirs.

    Config wins when set; otherwise we use ORB's platform_dirs helpers so
    PID and log files land under the same work/logs locations the rest of
    ORB writes to (e.g. respects ORB_WORK_DIR / ORB_LOG_DIR env vars).
    """
    from orb.config.platform_dirs import get_logs_location, get_work_location

    work_dir = server_config.working_dir or str(get_work_location())
    pid_file = server_config.pid_file or str(get_work_location() / "server" / "orb-server.pid")
    log_file = server_config.log_file or str(get_logs_location() / "orb-server.log")
    return pid_file, log_file, work_dir


def _resolve_configs(args) -> tuple[Any, Any | None]:
    """Resolve ServerConfig + (optional) UIConfig, applying CLI overrides."""
    from orb.config.schemas.server_schema import ServerConfig
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.di.container import get_container

    logger = get_logger(__name__)
    container = get_container()
    config_manager = container.get(ConfigurationPort)

    try:
        server_config = cast(Any, config_manager).get_typed_with_defaults(ServerConfig)
    except Exception as e:
        logger.warning(f"ServerConfig load failed, using defaults: {e}", exc_info=True)
        server_config = ServerConfig()  # type: ignore[call-arg]
    if server_config is None:
        server_config = ServerConfig()  # type: ignore[call-arg]

    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    workers = getattr(args, "workers", None)
    log_level = getattr(args, "server_log_level", None)
    scheduler = getattr(args, "scheduler", None)

    if host:
        server_config.host = host
    if port:
        server_config.port = port
    if workers:
        server_config.workers = workers
    if log_level:
        server_config.log_level = log_level
    if scheduler:
        config_manager.override_scheduler_strategy(scheduler)

    ui_config = None
    try:
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.config.schemas.ui_schema import UIConfig

        cm = container.get(ConfigurationManager)
        ui_config = cm.get_typed_with_defaults(UIConfig)
    except Exception as ui_e:
        logger.debug("UIConfig load failed, defaults used: %s", ui_e)

    return server_config, ui_config


async def _initialize_application() -> None:
    """Initialise the DI container's providers — same as serve handler."""
    from orb.bootstrap import Application
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    config_manager = container.get(ConfigurationPort)
    orb_app = Application(
        config_path=getattr(config_manager, "_config_file", None),
        skip_validation=True,
        container=container,
    )
    await orb_app.initialize()


def _build_runtime(args):
    """Return a zero-arg coroutine factory that runs the server."""
    server_config, ui_config = _resolve_configs(args)
    socket_path = getattr(args, "socket_path", None)
    reload_flag = getattr(args, "reload", False)
    log_level = getattr(args, "server_log_level", None)
    scheduler = getattr(args, "scheduler", None)
    api_only = getattr(args, "api_only", False)

    async def runtime() -> dict[str, Any]:
        from orb.interface.server_runtime import (
            run_api_foreground,
            run_embedded_foreground,
        )

        await _initialize_application()

        if not api_only and ui_config and ui_config.enabled and ui_config.mode == "embedded":
            return await run_embedded_foreground(ui_config, scheduler)
        return await run_api_foreground(
            server_config,
            socket_path=socket_path,
            reload=reload_flag,
            log_level=log_level,
        )

    return runtime, server_config, ui_config


def _health_url(server_config: Any, ui_config: Any | None) -> str:
    """Build the URL to probe for ``status``.

    Embedded UI mounts ORB FastAPI at ``/orb`` on the UI backend port.
    Standalone API exposes ``/health`` at the root.
    """
    if ui_config and ui_config.enabled and ui_config.mode == "embedded":
        return f"http://127.0.0.1:{ui_config.backend_port}/orb/health"
    host = server_config.host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{server_config.port}/health"


@handle_interface_exceptions(context="server_start", interface_type="cli")
async def handle_server_start(args) -> dict[str, Any]:
    """Start the server. Daemonized by default; ``--foreground`` to block."""
    from orb.interface import server_daemon as daemon_mod

    runtime, server_config, _ui_config = _build_runtime(args)
    pid_file, log_file, working_dir = _resolve_lifecycle_paths(server_config)
    foreground = getattr(args, "foreground", False)
    return daemon_mod.start(
        pid_file=pid_file,
        log_file=log_file,
        working_dir=working_dir,
        runtime=runtime,
        foreground=foreground,
    )


@handle_interface_exceptions(context="server_stop", interface_type="cli")
async def handle_server_stop(args) -> dict[str, Any]:
    """Stop the running daemon."""
    from orb.interface import server_daemon as daemon_mod

    server_config, _ = _resolve_configs(args)
    pid_file, _log_file, _wd = _resolve_lifecycle_paths(server_config)
    timeout = getattr(args, "timeout", None) or server_config.stop_timeout_seconds
    return daemon_mod.stop(pid_file=pid_file, timeout=float(timeout))


@handle_interface_exceptions(context="server_status", interface_type="cli")
async def handle_server_status(args) -> dict[str, Any]:
    """Show daemon status: pid, alive, /health probe."""
    from orb.interface import server_daemon as daemon_mod

    server_config, ui_config = _resolve_configs(args)
    pid_file, _log_file, _wd = _resolve_lifecycle_paths(server_config)
    return daemon_mod.status(
        pid_file=pid_file,
        health_url=_health_url(server_config, ui_config),
    )


@handle_interface_exceptions(context="server_restart", interface_type="cli")
async def handle_server_restart(args) -> dict[str, Any]:
    """Stop then start."""
    stop_res = await handle_server_stop(args)
    start_res = await handle_server_start(args)
    return {"stop": stop_res, "start": start_res}


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _read_loopback_token(pid_file: str) -> str | None:
    """Read the loopback-admin token written by the daemon at start time.

    Returns the token string, or None if the file does not exist (e.g. the
    daemon was started before this feature was added, or auth is disabled).
    The token file is a sibling of the PID file:
    ``<work_dir>/server/orb-server.token``.
    """
    from pathlib import Path as _Path

    try:
        pid_path = _Path(pid_file)
        token_file = pid_path.with_name(pid_path.stem + ".token")
        if token_file.exists():
            token = token_file.read_text(encoding="ascii").strip()
            return token if token else None
    except OSError:
        # The credential file is optional — absent when the daemon was started
        # before this feature was introduced or when auth is disabled.
        # Silently return None so the caller falls back to SIGHUP reload.
        pass
    return None


def _loopback_reload_request(
    host: str, port: int, path: str, token: str | None = None
) -> dict[str, Any]:
    """POST to the admin reload endpoint over a raw loopback TCP socket.

    Bypasses ``requests`` so static analysis doesn't flag this as a
    public HTTP egress: this is intra-host IPC to a loopback address
    that has already been validated by the caller. The transport is
    plaintext HTTP/1.1 because the peer lives in the same trust
    boundary as the CLI invoking it (same machine, same uid).

    When ``token`` is provided it is sent as ``Authorization: Bearer <token>``
    so the request succeeds even when bearer-token auth is active on the server.
    If ``token`` is None (legacy daemon or auth disabled), the header is omitted.

    This function is synchronous and must be called via ``asyncio.to_thread``
    when invoked from an async context to avoid blocking the event loop.
    """
    import http.client

    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"reload IPC requires a loopback host, refusing to call {host!r}")

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("POST", path, body=b"", headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    finally:
        conn.close()

    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"raw": raw.decode("utf-8", errors="replace")}
    return {"method": "loopback-ipc", "status": status, **body}


@handle_interface_exceptions(context="server_reload", interface_type="cli")
async def handle_server_reload(args) -> dict[str, Any]:
    """Reload server configuration without restarting the process.

    Embedded mode targets the Reflex backend (which owns the live DI
    container) over loopback IPC. API-only mode goes to the API
    process via the same loopback channel. SIGHUP is used as a
    fallback when the loopback peer is unreachable; the daemon's
    signal handler invokes ``ConfigurationManager.reload()``.

    The loopback IPC call is dispatched via ``asyncio.to_thread`` so the
    synchronous ``http.client`` socket I/O does not block the event loop.
    When a loopback-admin token file exists it is read and forwarded as the
    ``Authorization: Bearer`` header so the reload succeeds even when
    bearer-token auth is active on the server.
    """
    import asyncio as _asyncio

    from orb.interface import server_daemon as daemon_mod

    server_config, ui_config = _resolve_configs(args)
    pid_file, _log_file, _wd = _resolve_lifecycle_paths(server_config)

    if ui_config and ui_config.enabled and ui_config.mode == "embedded":
        host, port = "127.0.0.1", ui_config.backend_port
        path = "/orb/api/v1/admin/reload-config"
    else:
        host = server_config.host
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = server_config.port
        path = "/api/v1/admin/reload-config"

    # Read the loopback-admin token (if available) so the HTTP request carries
    # a valid Authorization header when bearer-token auth is enabled.
    token = _read_loopback_token(pid_file)

    try:
        return await _asyncio.to_thread(_loopback_reload_request, host, port, path, token)
    except (OSError, ValueError) as exc:
        return {
            "method": "sighup_fallback",
            "ipc_error": str(exc),
            **daemon_mod.reload(pid_file=pid_file),
        }


@handle_interface_exceptions(context="server_logs", interface_type="cli")
async def handle_server_logs(args) -> dict[str, Any]:
    """Tail the daemon's log file (no follow yet)."""
    from orb.interface import server_daemon as daemon_mod

    server_config, _ = _resolve_configs(args)
    _pid, log_file, _wd = _resolve_lifecycle_paths(server_config)
    lines = getattr(args, "lines", None) or 50
    return {
        "log_file": log_file,
        "tail": daemon_mod.tail_log(log_file=log_file, lines=int(lines)),
    }
