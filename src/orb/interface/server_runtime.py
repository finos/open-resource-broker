"""Foreground server runtime helpers.

Three pure async entrypoints that block until the server stops:

  - ``run_api_foreground`` — launches uvicorn directly in this process.
  - ``run_embedded_foreground`` — branches on ``ui_config.mode``:

      * ``"embedded"`` (default, production) — spawns Reflex's production
        backend as a child process in the ``orb/ui`` directory.  The Reflex
        app (``orb.ui.app``) mounts ORB's FastAPI at ``/orb`` via its
        ``api_transformer``, so a single port serves the SPA, WebSocket state
        sync (``/_event``), file upload (``/_upload``), health
        (``/orb/health``), and all REST API routes.  No Node/Bun at runtime.

      * ``"split"`` — two managed child processes.
          – Process A: uvicorn ORB FastAPI on ``server_config.port`` (API
            only, no api_transformer).
          – Process B: Reflex production backend on
            ``ui_config.backend_port`` (SPA + WebSocket state, no ORB API
            routes — ``ORB_MODE=remote``).
        Both processes are managed together: SIGINT/SIGTERM are forwarded to
        both process groups and the function blocks until both exit.

      * ``"dev"`` — spawns ``reflex run`` (dev mode) as a child in its own
        POSIX session and forwards SIGINT/SIGTERM to the process group.
        Requires Node/Bun.  For local development iteration only.

These are the building blocks shared by ``orb server start --foreground``
and (transitively, via the daemon module) ``orb server start``. Neither
function knows anything about pid files, log files, or detaching — that
lives in ``daemon.py``.

SIGHUP semantics: ``run_api_foreground`` installs a SIGHUP handler that
calls ``ConfigurationManager.reload()``.  The subprocess-based runtimes
(embedded, split, dev) do not forward SIGHUP to child process groups.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

from orb.infrastructure.logging.logger import get_logger


def _reload_config_from_signal(logger: Any, cm: Any) -> None:
    """Invoke ConfigurationManager.reload() using the pre-resolved instance.

    Best-effort: any failure is logged but does not abort the running
    server. Called from SIGHUP handlers wired by both API and embedded
    runtimes so ``orb server reload`` works in both modes.

    Args:
        logger: Logger instance.
        cm: Pre-resolved ConfigurationManager (obtained at server-start time
            so the SIGHUP handler does not call get_container() at signal
            delivery time).
    """
    try:
        cm.reload()
        logger.info("SIGHUP: configuration reloaded from disk")
    except Exception as exc:
        logger.error("SIGHUP: ConfigurationManager.reload() failed: %s", exc)


async def run_api_foreground(
    server_config: Any,
    *,
    socket_path: str | None = None,
    reload: bool = False,
    log_level: str | None = None,
) -> dict[str, Any]:
    """Run uvicorn in-process against ``orb.api.server.create_fastapi_app``.

    Blocks until the server exits. Returns a small result dict for the
    caller to log/return.
    """
    try:
        import uvicorn  # type: ignore

        from orb.api.server import create_fastapi_app
    except ImportError:
        raise ImportError("API server requires: pip install orb-py[api]") from None

    logger = get_logger(__name__)
    app = create_fastapi_app(server_config)

    if socket_path:
        logger.info("Starting REST API server on Unix socket %s", socket_path)
        config = uvicorn.Config(
            app=app,
            uds=socket_path,
            workers=1,  # UDS mode requires single worker
            log_level=log_level or server_config.log_level,
            access_log=True,
        )
    else:
        logger.info("Starting REST API server on %s:%s", server_config.host, server_config.port)
        logger.info(
            "Workers: %s, Reload: %s, Log Level: %s",
            server_config.workers,
            reload,
            server_config.log_level,
        )
        config = uvicorn.Config(
            app=app,
            host=server_config.host,
            port=server_config.port,
            # Reload mode requires single worker.
            workers=server_config.workers if not reload else 1,
            reload=reload,
            log_level=log_level or server_config.log_level,
            access_log=True,
        )

    server = uvicorn.Server(config)

    # Pre-resolve ConfigurationManager once so the SIGHUP handler does not
    # need to call get_container() at signal-delivery time (service-locator
    # avoided; the reload() call itself is intentional on the live instance).
    _cm: Any = None
    try:
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.infrastructure.di.container import get_container

        _cm = get_container().get(ConfigurationManager)
    except Exception as exc:
        logger.warning("Could not pre-resolve ConfigurationManager for SIGHUP: %s", exc)

    def signal_handler(signum, frame) -> None:
        logger.info("Received signal %s, shutting down gracefully...", signum)
        server.should_exit = True

    def sighup_handler(signum, frame) -> None:
        logger.info("Received SIGHUP — reloading configuration from disk")
        if _cm is None:
            logger.error("SIGHUP: ConfigurationManager unavailable; reload skipped")
            return
        _reload_config_from_signal(logger, _cm)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, sighup_handler)

    if socket_path:
        logger.info("ORB REST API listening on unix socket %s", socket_path)
    else:
        logger.info(
            "ORB REST API listening on http://%s:%s", server_config.host, server_config.port
        )

    await server.serve()
    return {"message": "Server stopped"}


def _find_reflex_bin() -> str:
    """Return the absolute path to the ``reflex`` CLI binary.

    Uses ``shutil.which`` to locate the binary on ``PATH``.  In a
    virtualenv the venv's ``bin/`` directory is on ``PATH`` so the
    venv-local ``reflex`` binary is found automatically.

    Raises ``ImportError`` when no binary is found so callers can surface
    a clear install message.
    """
    import shutil

    found = shutil.which("reflex")
    if found is not None:
        return found

    raise ImportError("UI mode requires the 'reflex' CLI. Install with: pip install orb-py[ui]")


def _orb_ui_dir() -> str:
    """Return the absolute path to ``src/orb/ui/`` (contains rxconfig.py).

    Computed relative to this file so the lookup works whether orb-py is
    installed from PyPI (``site-packages/orb/interface/``) or run from a
    local source checkout (``src/orb/interface/``).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "ui"))


async def run_embedded_foreground(
    ui_config: Any,
    server_config: Any = None,
    scheduler: str | None = None,
) -> dict[str, Any]:
    """Run the UI server according to ``ui_config.mode``.

    Three modes are supported:

    ``"embedded"`` (default, production)
        Reflex's production backend (``reflex run --env prod --backend-only``)
        is spawned as a child process.  The Reflex app (``orb.ui.app``)
        has ``api_transformer`` wired to mount ORB's FastAPI at ``/orb``,
        so a single port serves the SPA, WebSocket state sync, file upload,
        and all REST API routes.

        ``ORB_UI_BACKEND_PORT`` is set to ``server_config.port`` so Reflex
        listens on the same port that callers expect the ORB server on.  If
        ``server_config`` is not provided, ``ui_config.backend_port`` is used
        as a fallback (useful for testing and standalone Reflex invocations).

        No Node/Bun is required at runtime; the compiled frontend bundle is
        served directly by Reflex's backend from the pre-built ``_static/``
        directory.

    ``"split"``
        Two child processes are started and managed together:

        * **Process A** — uvicorn with ORB's FastAPI on ``server_config.port``
          (API-only, no Reflex, no ``api_transformer``).
        * **Process B** — Reflex production backend on
          ``ui_config.backend_port`` (SPA + WebSocket state, ``ORB_MODE``
          set to ``"remote"`` so ``api_transformer`` is *not* applied).

        Both process groups receive SIGINT/SIGTERM on shutdown.  The function
        blocks until both processes have exited.

        Requires a reverse proxy in front to route ``/orb/*`` requests to
        Process A and ``/`` (SPA + websocket) to Process B.  See
        ``docs/root/deployment/embedded-ui.md`` for an nginx sample.

    ``"dev"``
        ``reflex run`` (dev mode, with hot reload) is spawned as a child
        process in its own POSIX session.  SIGINT/SIGTERM are forwarded to
        the entire process group so that both the Reflex backend and the
        Node/Bun frontend dev server are torn down cleanly.  Requires
        Node/Bun at runtime.

    Returns when the subprocess (or both subprocesses in split mode) exits.
    """
    logger = get_logger(__name__)

    _raw_mode = getattr(ui_config, "mode", None)
    mode: str = str(_raw_mode) if _raw_mode in ("embedded", "split", "dev") else "embedded"

    # ------------------------------------------------------------------
    # mode = "split" — two managed processes: API + Reflex backend
    # ------------------------------------------------------------------
    if mode == "split":
        if server_config is None:
            raise ValueError("server_config is required for split mode")
        return await _run_split_mode(ui_config, server_config, scheduler, logger)

    # ------------------------------------------------------------------
    # mode = "embedded" — Reflex production backend (api_transformer active)
    # ------------------------------------------------------------------
    if mode == "embedded":
        reflex_bin = _find_reflex_bin()

        # In embedded mode the Reflex backend *is* the main server port.
        # Override ORB_UI_BACKEND_PORT so rxconfig.py picks up the right
        # port, falling back to ui_config.backend_port when server_config
        # is not provided (e.g. tests, standalone Reflex invocations).
        backend_port = server_config.port if server_config is not None else ui_config.backend_port
        host = getattr(server_config, "host", "0.0.0.0") if server_config is not None else "0.0.0.0"

        env = os.environ.copy()
        env["ORB_MODE"] = "embedded"
        env["ORB_UI_BACKEND_PORT"] = str(backend_port)
        # Reflex 0.9.x rejects a user-supplied frontend_port when running
        # backend-only; omit ORB_UI_FRONTEND_PORT so rxconfig.py does too.
        # The compiled SPA is served by orb.ui.app's api_transformer, which
        # mounts /assets, /sitemap.xml and a SPA-fallback route directly on
        # the Reflex backend — see orb/ui/app.py:_orb_api_transformer.
        env.pop("ORB_UI_FRONTEND_PORT", None)
        if scheduler:
            env["ORB_SCHEDULER_OVERRIDE"] = str(scheduler)

        orb_ui = _orb_ui_dir()

        logger.info(
            "Starting ORB embedded UI (Reflex prod backend) on %s:%s",
            host,
            backend_port,
        )

        proc = await asyncio.create_subprocess_exec(
            reflex_bin,
            "run",
            "--env",
            "prod",
            "--backend-only",
            "--backend-port",
            str(backend_port),
            "--backend-host",
            host,
            cwd=orb_ui,
            env=env,
            start_new_session=True,
        )

        loop = asyncio.get_running_loop()

        def _terminate_embedded(signum: int) -> None:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                return
            logger.info("Forwarding signal %s to embedded Reflex process group %s", signum, pgid)
            try:
                os.killpg(pgid, signum)
            except ProcessLookupError as exc:
                logger.debug("killpg race for signal %s: %s", signum, exc)

        # SIGHUP is NOT wired here: the CLI ``orb server reload`` command
        # targets the Reflex backend via a loopback IPC call instead of
        # forwarding SIGHUP (which would kill the process group).
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _terminate_embedded, sig)
            except (NotImplementedError, RuntimeError) as exc:
                logger.debug("add_signal_handler(%s) unsupported: %s", sig, exc)

        try:
            rc = await proc.wait()
        finally:
            if proc.returncode is None:
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    # Process already exited before signal reached it — nothing to do.
                    pass
                except PermissionError as exc:
                    logger.debug("killpg permission denied: %s", exc)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError, ValueError) as exc:
                    logger.debug("remove_signal_handler(%s) failed: %s", sig, exc)

        return {"message": f"Reflex exited with code {rc}", "exit_code": rc}

    # ------------------------------------------------------------------
    # mode = "dev" — spawn reflex run (dev mode) as a subprocess
    # ------------------------------------------------------------------
    if mode == "dev":
        reflex_bin = _find_reflex_bin()

        orb_ui = _orb_ui_dir()

        env = os.environ.copy()
        env["ORB_MODE"] = "dev"
        env["ORB_UI_BACKEND_PORT"] = str(ui_config.backend_port)
        env["ORB_UI_FRONTEND_PORT"] = str(ui_config.frontend_port)
        if scheduler:
            env["ORB_SCHEDULER_OVERRIDE"] = str(scheduler)

        logger.info(
            "Starting ORB with Reflex dev UI: frontend :%s, backend+API :%s",
            ui_config.frontend_port,
            ui_config.backend_port,
        )

        proc = await asyncio.create_subprocess_exec(
            reflex_bin,
            "run",
            cwd=orb_ui,
            env=env,
            start_new_session=True,
        )

        loop = asyncio.get_running_loop()

        def _terminate_group(signum: int) -> None:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                return
            logger.info("Forwarding signal %s to Reflex process group %s", signum, pgid)
            try:
                os.killpg(pgid, signum)
            except ProcessLookupError as exc:
                # Process group exited between getpgid and killpg — expected
                # race on shutdown, nothing to forward.
                logger.debug("killpg race for signal %s: %s", signum, exc)

        # SIGHUP is NOT wired here: forwarding it to the Reflex CLI child
        # group kills the Bun frontend dev server. The CLI ``orb server
        # reload`` command instead targets the Reflex backend via a
        # loopback IPC call — see
        # ``server_command_handlers.handle_server_reload``.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _terminate_group, sig)
            except (NotImplementedError, RuntimeError) as exc:
                # Some loops (Windows / restricted runtimes) don't support
                # signal handlers; non-fatal — we just lose signal forwarding.
                logger.debug("add_signal_handler(%s) unsupported: %s", sig, exc)

        try:
            rc = await proc.wait()
        finally:
            if proc.returncode is None:
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    # Process group already exited; nothing to terminate.
                    pass
                except PermissionError as exc:
                    # We do not own the group (uncommon — would require a uid
                    # drop between spawn and wait). Log and continue; the
                    # orphan will be reaped by init.
                    logger.debug("killpg permission denied: %s", exc)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError, ValueError) as exc:
                    # Handler was never installed (see add_signal_handler
                    # above) or the loop is already closed — non-fatal.
                    logger.debug("remove_signal_handler(%s) failed: %s", sig, exc)

        return {"message": f"Reflex exited with code {rc}", "exit_code": rc}

    # Unknown mode — fall back to API-only and warn
    logger.warning(
        "Unknown UI mode %r — falling back to API-only. Expected one of: embedded, split, dev.",
        mode,
    )
    if server_config is None:
        raise ValueError(f"server_config is required for unknown UI mode {mode!r} fallback")
    return await run_api_foreground(server_config, log_level=None)


async def _run_split_mode(
    ui_config: Any,
    server_config: Any,
    scheduler: str | None,
    logger: Any,
) -> dict[str, Any]:
    """Implement ``mode=split``: manage API uvicorn + Reflex backend together.

    Process A  — uvicorn with ORB FastAPI on ``server_config.port``.
                 Started via ``asyncio.create_subprocess_exec`` so we can
                 stream its output with a ``[api]`` prefix.

    Process B  — Reflex production backend on ``ui_config.backend_port``.
                 ``ORB_MODE`` is set to ``"remote"`` so ``api_transformer``
                 is NOT applied (the API lives in Process A).

    Both process groups receive SIGINT/SIGTERM on shutdown.  The function
    blocks until *both* processes have exited and returns a combined result.
    """
    reflex_bin = _find_reflex_bin()

    orb_ui = _orb_ui_dir()

    api_host = server_config.host or "0.0.0.0"
    api_port = server_config.port
    reflex_port = ui_config.backend_port

    # --- Process A: ORB FastAPI (API-only, no ui) ---
    api_env = os.environ.copy()
    if scheduler:
        api_env["ORB_SCHEDULER_OVERRIDE"] = str(scheduler)

    logger.info("split mode: starting API process on %s:%s", api_host, api_port)

    api_proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "orb.api.server:create_fastapi_app",
        "--factory",
        "--host",
        str(api_host),
        "--port",
        str(api_port),
        "--workers",
        str(getattr(server_config, "workers", 1)),
        "--log-level",
        str(getattr(server_config, "log_level", "info")),
        env=api_env,
        start_new_session=True,
    )

    # --- Process B: Reflex production backend (no api_transformer) ---
    reflex_env = os.environ.copy()
    reflex_env["ORB_MODE"] = "remote"
    reflex_env["ORB_UI_BACKEND_PORT"] = str(reflex_port)
    # Reflex 0.9.x rejects a user-supplied frontend_port when running
    # backend-only; omit ORB_UI_FRONTEND_PORT so rxconfig.py does too.
    reflex_env.pop("ORB_UI_FRONTEND_PORT", None)
    # Serve the pre-compiled SPA from the Reflex backend so split mode
    # only needs two processes (uvicorn API + Reflex backend) rather than
    # three (add a static frontend server).
    reflex_env["REFLEX_MOUNT_FRONTEND_COMPILED_APP"] = "1"
    if scheduler:
        reflex_env["ORB_SCHEDULER_OVERRIDE"] = str(scheduler)

    logger.info("split mode: starting Reflex backend on :%s", reflex_port)

    reflex_proc = await asyncio.create_subprocess_exec(
        reflex_bin,
        "run",
        "--env",
        "prod",
        "--backend-only",
        "--backend-port",
        str(reflex_port),
        cwd=orb_ui,
        env=reflex_env,
        start_new_session=True,
    )

    loop = asyncio.get_running_loop()

    def _terminate_both(signum: int) -> None:
        for proc, label in ((api_proc, "api"), (reflex_proc, "reflex")):
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                continue
            logger.info("Forwarding signal %s to %s process group %s", signum, label, pgid)
            try:
                os.killpg(pgid, signum)
            except ProcessLookupError as exc:
                logger.debug("killpg race (%s, signal %s): %s", label, signum, exc)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _terminate_both, sig)
        except (NotImplementedError, RuntimeError) as exc:
            logger.debug("add_signal_handler(%s) unsupported: %s", sig, exc)

    api_rc: int | None = None
    reflex_rc: int | None = None

    try:
        api_task = asyncio.create_task(api_proc.wait())
        reflex_task = asyncio.create_task(reflex_proc.wait())

        # Block until both processes have exited.
        results = await asyncio.gather(api_task, reflex_task, return_exceptions=True)
        api_rc = results[0] if isinstance(results[0], int) else None
        reflex_rc = results[1] if isinstance(results[1], int) else None
    finally:
        # Ensure both process groups are torn down on any exit path.
        for proc, label in ((api_proc, "api"), (reflex_proc, "reflex")):
            if proc.returncode is None:
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    # Process already exited before signal reached it — nothing to do.
                    pass
                except PermissionError as exc:
                    logger.debug("killpg permission denied (%s): %s", label, exc)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError) as exc:
                logger.debug("remove_signal_handler(%s) failed: %s", sig, exc)

    return {
        "message": "Split mode stopped",
        "api_exit_code": api_rc,
        "reflex_exit_code": reflex_rc,
        "exit_code": max(api_rc or 0, reflex_rc or 0),
    }
