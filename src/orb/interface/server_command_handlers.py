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
from typing import Any

from orb.domain.base.exceptions import ConfigurationError, ValidationError
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
    from orb.config.managers.configuration_manager import ConfigurationManager
    from orb.config.schemas.server_schema import ServerConfig
    from orb.domain.base.ports.configuration_port import ConfigurationPort

    logger = get_logger(__name__)
    container = args._container

    # Resolve the concrete ConfigurationManager for typed config loads — its
    # get_typed_with_defaults(config_type) takes a single positional argument
    # (the type to hydrate), which is the correct call signature.
    # ConfigurationPort.get_typed_with_defaults has a different signature
    # (key, expected_type, default) and must NOT be used for typed schema loads.
    cm = container.get(ConfigurationManager)

    try:
        server_config = cm.get_typed_with_defaults(ServerConfig)
    except Exception as e:
        # Refuse to fall back to a default ServerConfig.  A silent fallback
        # would produce a server with no intentional auth posture (fail-open).
        # Surface the load failure so the operator can fix the configuration.
        raise ConfigurationError(
            f"ServerConfig could not be loaded: {e}. "
            "Fix the server configuration before starting the server."
        ) from e
    if server_config is None:
        raise ConfigurationError(
            "ServerConfig resolved to None from the configuration manager. "
            "Ensure the server section is present in the configuration file."
        )

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
        # override_scheduler_strategy lives on both ConfigurationManager and
        # ConfigurationPort; use the port reference from the container so
        # the override propagates through the same object the rest of the
        # application resolves when it asks for ConfigurationPort.
        port_manager = container.get(ConfigurationPort)
        port_manager.override_scheduler_strategy(scheduler)

    ui_config = None
    try:
        from orb.config.schemas.ui_schema import UIConfig

        ui_config = cm.get_typed_with_defaults(UIConfig)
    except Exception as ui_e:
        logger.debug("UIConfig load failed, defaults used: %s", ui_e)

    return server_config, ui_config


async def _initialize_application(container: Any) -> None:
    """Initialise the DI container's providers — same as serve handler.

    Also starts provider daemon services (watch streams, startup reconcilers,
    orphan GC, etc). Only the REST/daemon path calls this — CLI commands
    explicitly skip it so they stay synchronous and don't issue per-command
    cluster sweeps.

    Args:
        container: The already-resolved DI container from the CLI dispatch boundary.
    """
    from orb.bootstrap import Application
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    config_manager = container.get(ConfigurationPort)
    orb_app = Application(
        config_path=getattr(config_manager, "_config_file", None),
        skip_validation=True,
        container=container,
    )
    if not await orb_app.initialize():
        logger.error("Failed to initialize application; providers may not be available")
        return
    if not await orb_app.start_daemon_services():
        logger.warning(
            "One or more provider daemon services failed to start; "
            "the REST API will continue with reduced functionality."
        )


def _build_runtime(args):
    """Return a zero-arg coroutine factory that runs the server."""
    server_config, ui_config = _resolve_configs(args)
    socket_path = getattr(args, "socket_path", None)
    reload_flag = getattr(args, "reload", False)
    log_level = getattr(args, "server_log_level", None)
    scheduler = getattr(args, "scheduler", None)
    api_only = getattr(args, "api_only", False)
    _container = args._container

    async def runtime() -> dict[str, Any]:
        from orb.interface.server_runtime import (
            run_api_foreground,
            run_embedded_foreground,
        )

        await _initialize_application(_container)

        # --api-only forces split (API-only) mode regardless of config.
        if api_only:
            return await run_api_foreground(
                server_config,
                socket_path=socket_path,
                reload=reload_flag,
                log_level=log_level,
            )

        if ui_config and ui_config.enabled and ui_config.mode in ("embedded", "split", "dev"):
            return await run_embedded_foreground(
                ui_config,
                server_config=server_config,
                scheduler=scheduler,
            )

        return await run_api_foreground(
            server_config,
            socket_path=socket_path,
            reload=reload_flag,
            log_level=log_level,
        )

    return runtime, server_config, ui_config


def _health_url(server_config: Any, ui_config: Any | None) -> str:
    """Build the URL to probe for ``status``.

    ``embedded`` mode: FastAPI is mounted at ``/orb`` on ``server_config.port``
    (single uvicorn, no Reflex), so the health endpoint is ``/orb/health``.

    ``dev`` mode (legacy Reflex path): FastAPI lives at ``/orb`` on the Reflex
    backend port, so the health endpoint is ``/orb/health`` on
    ``ui_config.backend_port``.

    All other cases (``split``, API-only, no UI config): the standard
    ``/health`` at the root on ``server_config.port``.
    """
    if ui_config and ui_config.enabled:
        if ui_config.mode == "embedded":
            host = server_config.host
            if host in ("0.0.0.0", "::"):
                host = "127.0.0.1"
            return f"http://{host}:{server_config.port}/orb/health"
        if ui_config.mode == "dev":
            return f"http://127.0.0.1:{ui_config.backend_port}/orb/health"
    host = server_config.host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{server_config.port}/health"


@handle_interface_exceptions(context="server_start", interface_type="cli")
async def handle_server_start(args) -> dict[str, Any]:
    """Start the server. Daemonized by default; ``--foreground`` to block."""
    import os

    from orb.interface import server_daemon as daemon_mod

    runtime, server_config, _ui_config = _build_runtime(args)
    pid_file, log_file, working_dir = _resolve_lifecycle_paths(server_config)
    foreground = getattr(args, "foreground", False)

    if foreground:
        # Foreground mode runs inside the CLI's own event loop (orb.run.main
        # is dispatched via asyncio.run).  Routing through daemon_mod.start
        # in this path would nest a second asyncio.run, which RuntimeError's
        # and surfaces as exit_code=1 with no actual server having started.
        # Take the pid + token lock directly here and await the runtime.
        logger = get_logger(__name__)
        pid_path = daemon_mod._expand(str(pid_file))
        log_path = daemon_mod._expand(str(log_file))
        wd_path = daemon_mod._expand(str(working_dir))
        wd_path.mkdir(parents=True, exist_ok=True)
        lock_fd = daemon_mod._acquire_pid_lock(pid_path)
        try:
            daemon_mod._write_pid(lock_fd, os.getpid())
            try:
                daemon_mod._write_token_file(pid_path)
            except OSError as exc:
                logger.warning("loopback handshake file write failed: %s", exc)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            result = await runtime()
            rc = int(result.get("exit_code", 0)) if isinstance(result, dict) else 0
        finally:
            pid_path.unlink(missing_ok=True)
            daemon_mod._cleanup_token_file(pid_path)
            os.close(lock_fd)
        return {"pid": os.getpid(), "status": "exited", "exit_code": rc}

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
        # The handshake file is optional — absent when the daemon was started
        # before this feature was introduced or when auth is disabled.
        # Silently return None so the caller falls back to SIGHUP reload.
        return None
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
        # Embedded mode: single uvicorn on server_config.port, API at /orb.
        host = server_config.host
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = server_config.port
        path = "/orb/api/v1/admin/reload-config"
    elif ui_config and ui_config.enabled and ui_config.mode == "dev":
        # Dev mode: Reflex backend process owns the DI container.
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


def _ui_resolve_static_dir():
    """Thin wrapper around ``orb.ui.app._resolve_static_dir``.

    Importing lazily keeps the heavy Reflex/page graph out of the CLI process
    when the UI extras are not installed.  The wrapper lives in this module so
    tests can patch ``orb.interface.server_command_handlers._ui_resolve_static_dir``
    without having to import ``orb.ui.app`` (and its reflex/page side-effects).

    Raises:
        ValidationError: When the UI extras (reflex and the orb page graph) are
            not installed, giving the user an actionable install instruction
            rather than an opaque ``ModuleNotFoundError`` traceback.
    """
    try:
        from orb.ui.app import _resolve_static_dir
    except ImportError as exc:
        raise ValidationError(
            "UI extras are not installed — run: pip install 'orb-py[ui]'"
        ) from exc

    return _resolve_static_dir()


@handle_interface_exceptions(context="server_ui_export", interface_type="cli")
async def handle_server_ui_export(args) -> dict[str, Any]:
    """Copy the compiled SPA bundle to a local directory for CDN / static-host serving.

    Locates the bundle via ``orb.ui.app._resolve_static_dir()`` (single source
    of truth shared with the embedded server route) and copies it with
    ``shutil.copytree``.

    # ponytail: local dir only; users pipe to s3/gcs with their own tooling
    """
    import shutil
    from pathlib import Path

    dest_arg: str = getattr(args, "dest", None) or ""
    force: bool = getattr(args, "force", False)

    static_dir: Path | None = _ui_resolve_static_dir()
    if static_dir is None:
        raise ValidationError(
            "UI bundle not found — no compiled SPA is available. "
            "Install the UI extras and build the bundle: "
            "pip install 'orb-py[ui]' then run 'make ui-build'."
        )

    dest = Path(dest_arg).resolve()
    if dest.exists() and not dest.is_dir():
        raise ValidationError(
            f"Destination '{dest}' exists and is not a directory. "
            "Provide a path to a directory (existing or new)."
        )
    if dest.exists() and any(dest.iterdir()) and not force:
        raise ValidationError(
            f"Destination '{dest}' already exists and is not empty. Use --force to overwrite."
        )

    shutil.copytree(str(static_dir), str(dest), dirs_exist_ok=force)

    file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
    return {
        "status": "ok",
        "dest": str(dest),
        "file_count": file_count,
        "message": f"SPA bundle exported to '{dest}' ({file_count} files).",
    }
