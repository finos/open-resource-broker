"""FastAPI server factory and application setup."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

try:
    from fastapi import Depends, FastAPI
    from fastapi.responses import JSONResponse, Response

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    Depends = None  # type: ignore[assignment,misc]
    FastAPI = None  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment,misc]
    Response = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from fastapi import Depends, FastAPI
    from fastapi.responses import JSONResponse, Response

from orb._package import __version__

# The loopback-admin classes and _create_auth_strategy live in their own modules;
# they are re-exported here because tests and callers import them from orb.api.server.
from orb.api.loopback_admin import (
    _load_loopback_token as _load_loopback_token,
    _LoopbackAdminAuthWrapper as _LoopbackAdminAuthWrapper,
    _LoopbackAdminTokenMiddleware as _LoopbackAdminTokenMiddleware,
)
from orb.api.middleware_stack import (
    _create_auth_strategy as _create_auth_strategy,
    install_middleware,
)
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.logging.logger import get_logger, setup_audit_logger


def _resolve_event_publisher() -> Any:
    """Return the shared event publisher, or None when it is unavailable."""
    try:
        from orb.domain.base.ports import EventPublisherPort
        from orb.infrastructure.di.container import get_container

        publisher = get_container().get_optional(EventPublisherPort)
    except Exception as exc:
        get_logger(__name__).debug("Event publisher unavailable for lifespan hooks: %s", exc)
        return None
    if publisher is None or not hasattr(publisher, "drain"):
        return None
    return publisher


def _resolve_request_reconciler() -> Any:
    """Return the background request reconciler, or None when it is unavailable."""
    try:
        from orb.application.services.request_reconciler import RequestReconciler
        from orb.infrastructure.di.container import get_container

        reconciler = get_container().get_optional(RequestReconciler)
        return reconciler if isinstance(reconciler, RequestReconciler) else None
    except Exception as exc:
        get_logger(__name__).debug("Request reconciler unavailable for lifespan hooks: %s", exc)
        return None


def create_fastapi_app(server_config: Any) -> Any:
    """
    Create and configure FastAPI application.

    Args:
        server_config: Server configuration

    Returns:
        Configured FastAPI application

    Raises:
        ImportError: If FastAPI is not installed
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. API mode requires FastAPI.\n"
            "Install with: pip install orb-py[api]"
        )

    logger = get_logger(__name__)

    # Validate configuration: refuse to boot with a None or structurally invalid
    # ServerConfig.  Silently falling back to a default would produce an
    # unauthenticated server with no intentional auth posture, which is
    # fail-open in production.  Callers must always supply a well-formed config.
    if server_config is None:
        raise ConfigurationError(
            "create_fastapi_app() requires a ServerConfig; received None. "
            "Ensure server configuration is loaded before calling this function."
        )

    if not hasattr(server_config, "docs_enabled"):
        raise ConfigurationError(
            "create_fastapi_app() received an invalid ServerConfig object "
            "(missing required attribute 'docs_enabled'). "
            "Pass a properly constructed ServerConfig instance."
        )

    from orb.api.documentation import configure_openapi
    from orb.infrastructure.error.exception_handler import get_exception_handler

    # Install the dedicated audit-log handler early so that audit records
    # written during middleware setup (e.g. by AuditLogMiddleware) land in the
    # right place from the first request onward.
    _audit_log_file: str | None = getattr(server_config, "audit_log_file", None)
    setup_audit_logger(_audit_log_file)
    if _audit_log_file:
        logger.info("orb.audit logger writing to dedicated file: %s", _audit_log_file)

    @asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        publisher = _resolve_event_publisher()
        if publisher is not None:
            # Events raised from worker threads are handed to this loop, which
            # owns the SSE subscriber queues.
            publisher.bind_loop()
        reconciler = _resolve_request_reconciler()
        if reconciler is not None:
            workers = getattr(server_config, "workers", 1) or 1
            if workers > 1:
                # Every worker would run its own loop against the same provider
                # and storage, racing writes with each other.
                get_logger(__name__).warning(
                    "Background request reconciliation is not started: server.workers=%d "
                    "(it runs only with a single worker)",
                    workers,
                )
                reconciler = None
            else:
                reconciler.start()
        try:
            yield
        finally:
            if reconciler is not None:
                await reconciler.stop()
            if publisher is not None:
                await publisher.drain()

    # Create FastAPI app with configuration
    app = FastAPI(  # type: ignore[operator]
        lifespan=_lifespan,
        title="Open Resource Broker API",
        description="REST API for Open Resource Broker - Dynamic cloud resource provisioning",
        version=__version__,
        docs_url=server_config.docs_url if server_config.docs_enabled else None,
        redoc_url=server_config.redoc_url if server_config.docs_enabled else None,
        openapi_url=server_config.openapi_url if server_config.docs_enabled else None,
    )

    logger = get_logger(__name__)

    auth_port = install_middleware(app, server_config, logger)

    # Add global exception handler
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Any, exc: Exception) -> Any:
        """Global exception handler for all unhandled exceptions."""
        try:
            # Use the existing exception handler infrastructure
            error_response = exception_handler.handle_error_for_http(exc)
            return JSONResponse(  # type: ignore[misc]
                status_code=error_response.http_status or 500,
                content={
                    "success": False,
                    "error": {
                        "code": (
                            error_response.error_code.value
                            if not isinstance(error_response.error_code, str)
                            else error_response.error_code
                        ),
                        "message": error_response.message,
                        "details": error_response.details,
                    },
                    "timestamp": error_response.timestamp.isoformat()
                    if hasattr(error_response.timestamp, "isoformat")
                    else error_response.timestamp,
                    "correlation_id": getattr(request.state, "request_id", "unknown"),
                },
            )
        except Exception as handler_error:
            # Fallback error response
            logger.error("Exception handler failed: %s", handler_error, exc_info=True)
            return JSONResponse(  # type: ignore[misc]
                status_code=500,
                content={
                    "success": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "An internal server error occurred",
                    },
                },
            )

    # Add health check endpoint
    from orb.api.dependencies import get_health_check_port

    def _run_checks_then(health_port: Any, summary: str) -> Any:
        health_port.run_all_checks()
        return getattr(health_port, summary)()

    from fastapi import Request

    _health_trusted_proxies: frozenset[str] = frozenset(server_config.trusted_proxies or [])

    async def _may_see_health_detail(request: Request) -> bool:
        """True when the per-check detail of /health and /readyz may be returned to this caller.

        Both are excluded from authentication so probes can reach them. With
        authentication enabled, only a caller presenting valid credentials gets
        the detail (host CPU, memory and disk figures, check messages); everyone
        else gets the overall status (and, on /readyz, each check's status).
        Any failure counts as anonymous.
        """
        if auth_port is None:
            return True
        if "authorization" not in request.headers:
            return False
        try:
            from orb.api.middleware._utils import get_real_client_ip
            from orb.infrastructure.adapters.ports.auth import AuthContext

            context = AuthContext(
                method=request.method,
                path=request.url.path,
                headers={k.lower(): str(v)[:1000] for k, v in request.headers.items()},
                query_params=dict(request.query_params),
                client_ip=get_real_client_ip(request, _health_trusted_proxies),
                # The raw socket peer, never a forwarded header: the loopback
                # admin token is honoured only for a loopback peer.
                direct_client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
                metadata={
                    "url": str(request.url)[:2000],
                    "base_url": str(request.base_url)[:2000],
                },
            )
            result = await auth_port.authenticate(context)
            return bool(result.is_authenticated)
        except Exception:
            return False

    @app.get("/health", tags=["System"], operation_id="healthCheck")
    async def health_check(  # type: ignore[misc]
        request: Request,
        health_port: Any = Depends(get_health_check_port),  # type: ignore[misc]
    ) -> Any:
        """Health check endpoint."""
        # With authentication enabled, anonymous callers get only the service,
        # version and overall status; authenticated callers also get the checks.
        try:
            # The checks make blocking provider/storage calls: keep them off the loop.
            status = await asyncio.to_thread(_run_checks_then, health_port, "get_status")
        except Exception:
            status = {"status": "unknown"}

        status = {"service": "open-resource-broker", "version": __version__, **status}
        http_status = 503 if status.get("status") == "unhealthy" else 200
        if not await _may_see_health_detail(request):
            status = {key: status.get(key) for key in ("service", "version", "status")}
        return JSONResponse(content=status, status_code=http_status)  # type: ignore[misc]

    @app.get("/livez", tags=["System"], operation_id="livenessCheck")
    async def liveness_check() -> Any:  # type: ignore[misc]
        """Liveness probe.

        Returns 200 whenever the process is up. Never gates on any
        dependency or provider — a liveness failure means the process
        should be restarted, which is not the right response to an
        unreachable downstream dependency.
        """
        return JSONResponse(content={"status": "alive"}, status_code=200)  # type: ignore[misc]

    @app.get("/readyz", tags=["System"], operation_id="readinessCheck")
    async def readiness_check(  # type: ignore[misc]
        request: Request,
        health_port: Any = Depends(get_health_check_port),  # type: ignore[misc]
    ) -> Any:
        """Readiness probe.

        Returns 503 when a CORE dependency is not fully healthy, otherwise
        200. Core dependencies are storage/database plus — in a
        single-provider deployment — the sole provider's connectivity: a
        broken sole provider means the pod cannot serve provisioning
        requests, so it must be taken out of rotation. In a multi-provider
        deployment, provider connectivity is classified non-core, so a single
        unreachable optional provider does NOT gate readiness.

        Both ``unhealthy`` and ``degraded`` core states map to 503: a
        connectivity failure surfaces as ``degraded``, and for a sole
        (core) provider that still means not-ready.
        """
        status: dict[str, Any]
        try:
            status = await asyncio.to_thread(_run_checks_then, health_port, "get_readiness")
        except Exception:
            status = {"status": "unknown"}

        http_status = 503 if status.get("status") in ("unhealthy", "degraded") else 200
        # The failure path has no "checks" to strip.
        if "checks" in status and not await _may_see_health_detail(request):
            status = {
                **status,
                "checks": {
                    name: {"status": (check or {}).get("status")}
                    for name, check in status["checks"].items()
                },
            }
        return JSONResponse(content=status, status_code=http_status)  # type: ignore[misc]

    # Add metrics endpoint
    @app.get("/metrics", tags=["System"], operation_id="getMetrics")
    async def metrics() -> Any:
        """Prometheus metrics endpoint.

        Serves all metrics from the prometheus_client global REGISTRY, which
        includes:
          - OTel SDK metrics bridged via PrometheusMetricReader (all provider +
            domain-level OTel instruments: AWS botocore, k8s, event handler,
            storage decorator, base handler, fallback strategy)
          - Native prometheus_client metrics (k8s K8sMetrics registered
            directly on REGISTRY)
          - Default python process metrics (python_gc_*, python_info, etc.)

        prometheus_client is an optional [monitoring] extra — when absent the
        endpoint returns an empty 200 rather than a 500 so a minimal install
        remains usable.
        """
        # prometheus_client is an optional [monitoring] extra — guard the import
        # so a minimal install without it still serves an empty but valid response.
        try:
            from prometheus_client import REGISTRY, generate_latest

            body = generate_latest(REGISTRY).decode("utf-8")
        except Exception:  # noqa: BLE001 — ImportError or prometheus_client internal error
            body = ""

        return Response(content=body, media_type="text/plain; version=0.0.4")  # type: ignore[misc]

    # Add info endpoint
    @app.get("/info", tags=["System"], operation_id="getServiceInfo")
    async def info() -> dict[str, Any]:
        """Service information endpoint.

        Returns basic service metadata only.  Authentication configuration is
        intentionally omitted so that unauthenticated callers cannot discover
        which auth method is in use and tailor attacks accordingly.
        """
        return {
            "service": "open-resource-broker",
            "version": __version__,
            "description": "REST API for Open Resource Broker",
        }

    # Serve the favicon from a packaged resource rather than a path derived
    # from __file__, so the route never touches request-controllable input
    # and works regardless of install layout (editable, sdist, wheel).
    try:
        from importlib import resources as _resources

        _favicon_bytes: bytes | None = (
            _resources.files("orb.api.assets").joinpath("orb-icon.png").read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        _favicon_bytes = None

    if _favicon_bytes is not None:

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon() -> Any:
            return Response(content=_favicon_bytes, media_type="image/png")  # type: ignore[misc]

    # Stamp auth-enabled status on app state so request-time dependencies
    # (get_current_user) can distinguish "auth disabled → grant admin" from
    # "auth enabled but excluded path → grant viewer".
    app.state.auth_enabled = server_config.auth.enabled

    # Register API routers
    _register_routers(app)

    # FastAPI auto-instrumentation — must be called after routers are registered
    # so the instrumentor sees the complete route table when building span names.
    # Guarded try/except ImportError: no crash when the package is absent.
    # Gated on OtelConfig.instrument_fastapi (default True when OTel is enabled).
    try:
        from orb.config.schemas.app_schema import AppConfig
        from orb.config.schemas.observability_schema import OtelConfig
        from orb.infrastructure.di.container import get_container as _get_container

        try:
            from orb.domain.base.ports.configuration_port import ConfigurationPort

            _otel_cfg: OtelConfig = (
                _get_container().get(ConfigurationPort).get_typed(AppConfig).observability  # type: ignore[arg-type]
            )
        except Exception:
            _otel_cfg = OtelConfig.model_validate({})

        if _otel_cfg.enabled and _otel_cfg.instrument_fastapi:
            try:
                from opentelemetry.instrumentation.fastapi import (  # type: ignore[import-not-found]
                    FastAPIInstrumentor,
                )

                FastAPIInstrumentor().instrument_app(app, excluded_urls="/health,/metrics")
                logger.info("FastAPI OTel auto-instrumentation enabled")
            except ImportError:
                pass  # opentelemetry-instrumentation-fastapi not installed; skip.
    except Exception as exc:
        logger.debug("Config or DI resolution failed; skipping without crashing: %s", exc)

    # Warn when multiple uvicorn workers are configured alongside the SSE
    # events router.  The in-process pubsub (SseEventBus) is not shared across
    # worker processes, so events published in one worker are invisible to
    # subscribers connected to a different worker.  This is a data-loss risk,
    # not an error — operators may have valid reasons (e.g. a shared queue
    # upstream), so we warn but do not refuse to start.
    _workers = getattr(server_config, "workers", 1) or 1
    if _workers > 1:
        _registered_routes = {getattr(r, "path", "") for r in app.routes}
        _has_events_route = any("/events" in p for p in _registered_routes)
        if _has_events_route:
            logger.warning(
                "MULTI_WORKER_SSE: server.workers=%d but the SSE events router is registered. "
                "The in-process event queue is NOT shared across worker processes — SSE "
                "subscribers may silently miss events published by other workers. "
                "Set server.workers=1 or route SSE through a shared pub/sub backend.",
                _workers,
            )

    # Configure OpenAPI documentation
    configure_openapi(app, server_config)

    logger.info("FastAPI application created with %s routes", len(app.routes))
    return app


def _register_routers(app: Any) -> None:
    """
    Register API routers.

    Args:
        app: FastAPI application
    """
    try:
        from orb.api.routers import (
            admin,
            config,
            events,
            machines,
            me,
            observability,
            providers,
            requests,
            system,
            templates,
        )

        app.include_router(templates.router, prefix="/api/v1")
        app.include_router(machines.router, prefix="/api/v1")
        app.include_router(requests.router, prefix="/api/v1")
        app.include_router(system.router, prefix="/api/v1")
        app.include_router(events.router, prefix="/api/v1")
        app.include_router(me.router, prefix="/api/v1")
        app.include_router(observability.router, prefix="/api/v1")
        app.include_router(providers.router, prefix="/api/v1")
        app.include_router(admin.router, prefix="/api/v1")
        app.include_router(config.router, prefix="/api/v1")

    except ImportError as e:
        logger = get_logger(__name__)
        logger.error("Failed to import routers: %s", e, exc_info=True)
        # Continue without routers - they might not be fully implemented yet
