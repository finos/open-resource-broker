"""ORB UI — Reflex app entry point.

Two deployment modes (selected via ``UIConfig.mode`` or the ``ORB_MODE``
env var, env wins):

  - ``embedded`` (default): ORB's FastAPI app is mounted into the Reflex
    backend via ``api_transformer``. One process, one port serves UI + API.
  - ``remote``: Reflex runs standalone, talks to a remote ORB over HTTP.

The Reflex CLI ``reflex run`` always loads this module. Whether the UI is
actually exposed by the deployed ORB process is gated separately by
``UIConfig.enabled`` in the application bootstrap (see ``orb.run``).
"""

from __future__ import annotations

import os

import reflex as rx

from orb.ui.pages.config import config_page
from orb.ui.pages.dashboard import dashboard_page
from orb.ui.pages.machines import machines_page
from orb.ui.pages.requests import requests_page
from orb.ui.pages.templates import templates_page


def _resolve_mode() -> str:
    """Resolve deployment mode from env or config.

    This controls whether ORB's FastAPI app is mounted inside the Reflex
    process via ``api_transformer`` (embedded) or not (remote/standalone).
    It does NOT affect the UI's data path — the UI always talks to ORB over
    HTTP regardless of mode (see api.py / api_http.py).
    """
    env = os.getenv("ORB_MODE")
    if env:
        return env.lower()
    try:
        from orb.config.schemas.ui_schema import UIConfig

        # Default ctor — config manager may not be wired here.
        return UIConfig().mode
    except Exception:
        return "embedded"


ORB_MODE = _resolve_mode()


def _initialize_orb_application_sync() -> bool:
    """Bootstrap ORB providers in the current process at import time.

    The Reflex backend runs in a separate Python process from the parent
    ``orb server start`` invocation, so the DI container in this process
    has no provider strategies registered until ``Application.initialize``
    runs. Reflex does not propagate FastAPI's lifespan/startup events
    into sub-apps mounted via ``api_transformer``, so this runs at module
    import (when Reflex imports ``orb.ui.app``) rather than as a startup
    hook on the sub-app.

    Returns True on success, False otherwise. A failure is logged but
    does not raise so that the UI still renders — mutating requests
    will fail later with the canonical 'No strategy found' error from
    the provider registry, which is the actionable signal.
    """
    import asyncio

    from orb.bootstrap import Application
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    try:
        container = get_container()
        config_manager = container.get(ConfigurationPort)
        app_instance = Application(
            config_path=getattr(config_manager, "_config_file", None),
            skip_validation=True,
            container=container,
        )
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(app_instance.initialize())
        finally:
            loop.close()
        if not ok:
            logger.error(
                "Application.initialize returned False — provider strategies are not "
                "registered in the Reflex backend container; UI mutating requests will fail."
            )
        return ok
    except Exception as exc:
        logger.error("ORB application bootstrap raised: %s", exc, exc_info=True)
        return False


def _resolve_static_dir():
    """Locate the compiled SPA bundle.

    Two layouts are supported:

    * Dev / editable install: ``reflex export`` + ``bun run export`` write
      the bundle to ``<pkg>/.web/build/client``.  This is what Reflex's
      own ``prerequisites.get_web_dir()`` resolves to at runtime.
    * Wheel install: ``make ui-build`` copies the bundle to
      ``<pkg>/_static`` and ``pyproject.toml`` ships it inside the wheel.
      ``.web`` is *not* present in a wheel install.

    Prefer ``.web/build/client`` if it exists, otherwise fall back to the
    packaged ``_static`` directory.  Returns ``None`` if neither exists so
    the caller can bail out cleanly instead of crashing at import.
    """
    from pathlib import Path

    from reflex.utils import prerequisites
    from reflex_base import constants

    web_bundle = (prerequisites.get_web_dir() / constants.Dirs.STATIC).resolve()
    if (web_bundle / "index.html").is_file():
        return web_bundle

    packaged = (Path(__file__).parent / "_static").resolve()
    if (packaged / "index.html").is_file():
        return packaged

    return None


def _mount_spa_routes(reflex_app, static_dir):
    """Attach the compiled SPA + fallback routes to a Reflex backend.

    Reflex owns ``/``, ``/_event`` (WebSocket state sync), ``/_upload`` and
    ``/_health`` after ``_compile`` runs.  The compiled SPA bundle from
    ``.web/build/client`` ships with the wheel and needs to be served on
    the same port as those routes.  Route order is load-bearing (Starlette
    matches in registration order):

      1. Reflex's own ``/_event`` / ``/_upload`` / ``/_health``
      2. ``/assets/*``    — hashed JS/CSS/etc. from the bundle
      3. ``/sitemap.xml`` — from the bundle
      4. Catch-all GET    — returns ``index.html`` so React Router can
         resolve client-side routes like ``/machines``, ``/requests``.
    """
    from starlette.requests import Request
    from starlette.responses import FileResponse, PlainTextResponse
    from starlette.routing import Route
    from starlette.staticfiles import StaticFiles

    _spa_index = static_dir / "index.html"

    async def _spa_fallback(request: Request) -> FileResponse | PlainTextResponse:
        if _spa_index.is_file():
            return FileResponse(_spa_index, media_type="text/html")
        return PlainTextResponse("Not Found", status_code=404)

    async def _sitemap(request: Request) -> FileResponse | PlainTextResponse:
        p = static_dir / "sitemap.xml"
        if p.is_file():
            return FileResponse(p, media_type="application/xml")
        return PlainTextResponse("Not Found", status_code=404)

    from starlette.routing import Mount

    reflex_app.routes.append(
        Mount("/assets", app=StaticFiles(directory=str(static_dir / "assets")), name="spa-assets")
    )
    reflex_app.routes.append(Route("/sitemap.xml", _sitemap, methods=["GET"], name="spa-sitemap"))
    reflex_app.routes.append(
        Route("/{full_path:path}", _spa_fallback, methods=["GET"], name="spa-fallback")
    )


def _orb_api_transformer(reflex_app):
    """Mount ORB's FastAPI app + the compiled SPA onto the Reflex backend.

    Runs in ``embedded`` mode where a single process serves everything:

      - ``/orb/api/v1/...``   — ORB REST API
      - ``/orb/health``       — health
      - ``/orb/info``         — service info
      - ``/orb/docs``         — Swagger UI
      - ``/orb/openapi.json`` — OpenAPI schema
      - ``/orb/metrics``      — Prometheus metrics
      - ``/_event``           — Reflex state-sync WebSocket
      - ``/_upload``          — Reflex upload endpoint
      - ``/_health``          — Reflex health
      - ``/assets/*``         — SPA static bundle
      - ``/`` + SPA routes    — ``index.html`` (React Router client-side)
    """
    from starlette.routing import Mount

    from orb.api.dependencies import get_server_config
    from orb.api.server import create_fastapi_app
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    orb_app = create_fastapi_app(get_server_config())
    static_dir = _resolve_static_dir()

    # ``/orb`` must be inserted BEFORE the SPA catch-all fallback route
    # would swallow it.  Insert at the head to also precede Reflex's
    # ``/_event`` etc. (mounts are unambiguous by prefix, so order within
    # the specific-prefix set does not matter -- but the fallback is a
    # catch-all Route that would win otherwise).
    reflex_app.routes.insert(0, Mount("/orb", app=orb_app, name="orb"))
    if static_dir is not None:
        _mount_spa_routes(reflex_app, static_dir)
    else:
        logger.error(
            "No compiled SPA bundle found (.web/build/client or _static/). "
            "UI pages will 404; run `make ui-build` before packaging.",
        )
    return reflex_app


def _spa_only_api_transformer(reflex_app):
    """Mount the compiled SPA onto the Reflex backend without ORB REST.

    Runs in ``remote`` (split) mode where the ORB REST API lives in a
    separate uvicorn process on a different port and the UI talks to it
    over HTTP.  Only static SPA serving is added here.
    """
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    static_dir = _resolve_static_dir()
    if static_dir is not None:
        _mount_spa_routes(reflex_app, static_dir)
    else:
        logger.error(
            "No compiled SPA bundle found (.web/build/client or _static/). "
            "UI pages will 404; run `make ui-build` before packaging.",
        )
    return reflex_app


# Run provider/orchestrator/storage registration in this process before the
# Reflex backend serves its first request. This happens at module import
# (i.e. ``reflex run`` importing orb.ui.app).
if ORB_MODE == "embedded":
    _initialize_orb_application_sync()


_HEAD = [
    rx.el.link(rel="icon", type="image/svg+xml", href="/favicon.svg"),
]

if ORB_MODE == "embedded":
    app = rx.App(api_transformer=_orb_api_transformer, head_components=_HEAD)
else:
    # ``remote``/split mode: the Reflex backend still needs to serve the
    # compiled SPA on its port; the ORB REST API lives elsewhere.
    app = rx.App(api_transformer=_spa_only_api_transformer, head_components=_HEAD)

app.add_page(dashboard_page, route="/", title="ORB · Dashboard")
app.add_page(machines_page, route="/machines", title="ORB · Machines")
app.add_page(requests_page, route="/requests", title="ORB · Requests")
app.add_page(templates_page, route="/templates", title="ORB · Templates")
app.add_page(config_page, route="/config", title="ORB · Config")
