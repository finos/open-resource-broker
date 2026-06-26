"""CLI handlers for the ``orb server`` lifecycle commands.

start    → daemonize (or ``--foreground``) the API + optional embedded UI
stop     → SIGTERM → wait → SIGKILL the running daemon's process group
status   → PID-file check plus a best-effort ``/health`` probe
restart  → stop + start
reload   → SIGHUP the daemon
logs     → tail the daemon's log file
"""

from __future__ import annotations

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


@handle_interface_exceptions(context="server_reload", interface_type="cli")
async def handle_server_reload(args) -> dict[str, Any]:
    """Reload server configuration without restarting the process.

    Tries the HTTP admin endpoint first (works for both API-only and
    embedded UI modes — both expose ``POST /admin/reload-config``).
    Falls back to SIGHUP if the HTTP call cannot be made; SIGHUP wakes
    up the in-process handler installed by ``run_api_foreground``.

    The HTTP path is preferred because it goes directly to the
    process that owns the live DI container — in embedded mode that
    is the Reflex backend, not the orchestrator parent that holds the
    PID file. Sending SIGHUP to the parent would kill the Bun
    frontend dev server as a side effect.
    """
    import json

    import requests

    from orb.interface import server_daemon as daemon_mod

    server_config, ui_config = _resolve_configs(args)
    pid_file, _log_file, _wd = _resolve_lifecycle_paths(server_config)

    # Pick the URL the same way `status` does — embedded mode mounts
    # ORB at /orb on the UI backend port; API-only exposes /admin
    # directly on the configured host/port.
    if ui_config and ui_config.enabled and ui_config.mode == "embedded":
        url = f"http://127.0.0.1:{ui_config.backend_port}/orb/api/v1/admin/reload-config"
    else:
        host = server_config.host
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        url = f"http://{host}:{server_config.port}/api/v1/admin/reload-config"

    try:
        # URL is loopback HTTP by design — ``orb server reload`` is a
        # localhost-only IPC channel between the CLI and the daemon
        # running on the same host. TLS would add no value here.
        # ``requests`` is bound to http(s):// only — urllib would also
        # accept file:// which is a tail-risk if the URL is misconfigured.
        # nosemgrep: python.lang.security.audit.insecure-transport.requests.request-with-http.request-with-http
        resp = requests.post(url, data=b"", timeout=5)
        body = resp.json() if resp.content else {}
        return {"method": "http", "url": url, "status": resp.status_code, **body}
    except (requests.RequestException, OSError, json.JSONDecodeError) as exc:
        # HTTP path unavailable (server not listening, wrong port, etc.)
        # — fall back to SIGHUP so API-only deployments still get a
        # working reload signal even if the HTTP probe misfires.
        return {
            "method": "sighup_fallback",
            "http_error": str(exc),
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
