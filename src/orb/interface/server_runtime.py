"""Foreground server runtime helpers.

Two pure async entrypoints that block until the server stops:

  - ``run_api_foreground`` — launches uvicorn directly in this process.
  - ``run_embedded_foreground`` — exec's ``reflex run`` as a child in its
    own POSIX session and forwards SIGINT/SIGTERM to the process group.

These are the building blocks shared by ``orb server start --foreground``
and (transitively, via the daemon module) ``orb server start``. Neither
function knows anything about pid files, log files, or detaching — that
lives in ``daemon.py``.

SIGHUP semantics: both entrypoints install a SIGHUP handler that calls
``ConfigurationManager.reload()`` to re-read the on-disk config without
restarting the process. Useful after ``orb init`` or hand-edits to
``config.json`` — operators don't have to bounce the daemon. Python code
changes still require ``--reload`` (uvicorn watchdog) or a full restart.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from orb.infrastructure.logging.logger import get_logger


def _reload_config_from_signal(logger: Any) -> None:
    """Invoke ConfigurationManager.reload() on the live DI container.

    Best-effort: any failure is logged but does not abort the running
    server. Called from SIGHUP handlers wired by both API and embedded
    runtimes so ``orb server reload`` works in both modes.
    """
    try:
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.infrastructure.di.container import get_container

        cm = get_container().get(ConfigurationManager)
    except Exception as exc:
        logger.error("SIGHUP: cannot resolve ConfigurationManager: %s", exc)
        return
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

    def signal_handler(signum, frame) -> None:
        logger.info("Received signal %s, shutting down gracefully...", signum)
        server.should_exit = True

    def sighup_handler(signum, frame) -> None:
        logger.info("Received SIGHUP — reloading configuration from disk")
        _reload_config_from_signal(logger)

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


async def run_embedded_foreground(
    ui_config: Any,
    scheduler: str | None = None,
) -> dict[str, Any]:
    """Run ``reflex run`` as a child in its own session.

    Reflex's own backend will host the UI websocket AND ORB's FastAPI app
    (via the ``api_transformer`` in ``orb.ui.app``). We fork the child
    with ``start_new_session=True`` so we can take the whole tree down
    (Reflex backend + Node/Next dev server) via ``killpg`` on shutdown.

    Returns when the subprocess exits.
    """
    import shutil

    logger = get_logger(__name__)

    reflex_bin = shutil.which("reflex")
    if reflex_bin is None:
        raise ImportError(
            "UI is enabled but the 'reflex' CLI is not installed. "
            "Install with: pip install orb-py[ui]"
        )

    # rxconfig.py ships inside the wheel at orb/ui/rxconfig.py.  Point
    # reflex at that directory so it works whether orb-py is installed from
    # PyPI or run from a local checkout.  The repo-root rxconfig.py is a
    # thin re-export that delegates here for local ``reflex run`` workflows.
    here = os.path.dirname(os.path.abspath(__file__))
    orb_ui_dir = os.path.abspath(os.path.join(here, "..", "ui"))

    env = os.environ.copy()
    env["ORB_MODE"] = "embedded"
    env["ORB_UI_BACKEND_PORT"] = str(ui_config.backend_port)
    env["ORB_UI_FRONTEND_PORT"] = str(ui_config.frontend_port)
    if scheduler:
        env["ORB_SCHEDULER_OVERRIDE"] = str(scheduler)

    logger.info(
        "Starting ORB with embedded UI: frontend :%s, backend+API :%s",
        ui_config.frontend_port,
        ui_config.backend_port,
    )

    proc = await asyncio.create_subprocess_exec(
        reflex_bin,
        "run",
        cwd=orb_ui_dir,
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
    # group kills the Bun frontend dev server. The CLI `orb server
    # reload` command instead targets the Reflex backend via a
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
                # We do not own the group (uncommon — would require a
                # uid drop between spawn and wait). Log and continue;
                # the orphan will be reaped by init.
                logger.debug("killpg permission denied: %s", exc)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError) as exc:
                # Handler was never installed (see add_signal_handler
                # above) or the loop is already closed — non-fatal.
                logger.debug("remove_signal_handler(%s) failed: %s", sig, exc)

    return {"message": f"Reflex exited with code {rc}", "exit_code": rc}
