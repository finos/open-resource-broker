"""FastAPI server factory and application setup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

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
from orb.api.system_routes import register_system_routes
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

    register_system_routes(app, server_config, auth_port)

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
