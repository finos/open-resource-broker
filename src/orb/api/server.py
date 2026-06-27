"""FastAPI server factory and application setup."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

# ---------------------------------------------------------------------------
# Loopback-admin token store
#
# The daemon writes a per-instance secret to <work_dir>/server/orb-server.token
# (mode 0o600) at startup.  The API server loads it here and stores it in this
# module-level frozenset so the LoopbackAdminAuthWrapper can grant admin access
# to the CLI's reload request without requiring a full JWT.
#
# The set is populated once during create_fastapi_app; it is intentionally
# module-level (not instance-level) so it survives any re-import of this module.
# ---------------------------------------------------------------------------
_LOOPBACK_ADMIN_TOKENS: set[str] = set()

try:
    from fastapi import Depends, FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import JSONResponse, Response

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    Depends = None  # type: ignore[assignment,misc]
    FastAPI = None  # type: ignore[assignment,misc]
    CORSMiddleware = None  # type: ignore[assignment,misc]
    TrustedHostMiddleware = None  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment,misc]
    Response = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from fastapi import Depends, FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import JSONResponse, Response

from orb._package import __version__
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.auth.registry import get_auth_registry
from orb.infrastructure.logging.logger import get_logger

_server_logger = get_logger(__name__)


class _LoopbackAdminAuthWrapper:
    """Thin auth-port wrapper that accepts the loopback-admin token.

    When the daemon's loopback reload IPC sends ``Authorization: Bearer <token>``
    and that token matches the value written to ``orb-server.token``, this
    wrapper short-circuits normal JWT validation and grants an admin identity.
    For every other token it delegates to the real inner strategy unchanged.

    This keeps the loopback capability fully isolated: it never modifies the
    existing JWT strategy, and the token is only ever read from a file that is
    mode 0o600 (daemon-UID-only readable).
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._logger = get_logger(__name__)

    async def authenticate(self, context: Any) -> Any:
        from orb.infrastructure.adapters.ports.auth import AuthResult, AuthStatus

        auth_header: str = context.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            candidate = auth_header[7:].strip()
            if candidate and candidate in _LOOPBACK_ADMIN_TOKENS:
                self._logger.debug("loopback-admin token accepted for %s", context.path)
                return AuthResult(
                    status=AuthStatus.SUCCESS,
                    user_id="loopback-admin",
                    user_roles=["admin"],
                    permissions=["*"],
                    metadata={"strategy": "loopback_admin_token"},
                )
        return await self._inner.authenticate(context)

    def get_strategy_name(self) -> str:
        return self._inner.get_strategy_name()

    def is_enabled(self) -> bool:
        return self._inner.is_enabled()

    # Delegate all other attribute access to the inner strategy.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _load_loopback_token(server_config: Any) -> None:
    """Read the daemon-written loopback-admin token file and register it.

    The token file path mirrors the PID file path: if the PID file is
    ``<work_dir>/server/orb-server.pid``, the token file is
    ``<work_dir>/server/orb-server.token``.

    Silently skips if the file does not exist (auth disabled, fresh install,
    or daemon not yet started).
    """
    try:
        from orb.config.platform_dirs import get_work_location

        pid_file = getattr(server_config, "pid_file", None) or str(
            get_work_location() / "server" / "orb-server.pid"
        )
        token_file = Path(pid_file).with_name(Path(pid_file).stem + ".token")
        if token_file.exists():
            token = token_file.read_text(encoding="ascii").strip()
            if token:
                _LOOPBACK_ADMIN_TOKENS.add(token)
                _server_logger.debug("loopback-admin token loaded from %s", token_file)
    except Exception as exc:
        _server_logger.debug("loopback-admin token load skipped: %s", exc)


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

    # Validate and default configuration
    if server_config is None:
        logger.warning("No server configuration provided, using defaults")
        from orb.config.schemas.server_schema import ServerConfig

        server_config = ServerConfig()  # type: ignore[call-arg]

    # Validate configuration object has required attributes
    if not hasattr(server_config, "docs_enabled"):
        logger.error("Invalid server configuration: missing docs_enabled attribute")
        from orb.config.schemas.server_schema import ServerConfig

        server_config = ServerConfig()  # type: ignore[call-arg]

    from orb.api.documentation import configure_openapi
    from orb.api.middleware import (
        AuditLogMiddleware,
        AuthMiddleware,
        LoggingMiddleware,
        RateLimitMiddleware,
        ReadOnlyMiddleware,
    )
    from orb.infrastructure.error.exception_handler import get_exception_handler

    # Create FastAPI app with configuration
    app = FastAPI(  # type: ignore[operator]
        title="Open Resource Broker API",
        description="REST API for Open Resource Broker - Dynamic cloud resource provisioning",
        version=__version__,
        docs_url=server_config.docs_url if server_config.docs_enabled else None,
        redoc_url=server_config.redoc_url if server_config.docs_enabled else None,
        openapi_url=server_config.openapi_url if server_config.docs_enabled else None,
    )

    logger = get_logger(__name__)

    # Warn loudly when auth is disabled but the server is bound to a non-loopback
    # address — this combination exposes every endpoint without authentication.
    _LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
    bind_host: str = getattr(server_config, "host", "127.0.0.1") or "127.0.0.1"
    if not server_config.auth.enabled and bind_host not in _LOOPBACK_HOSTS:
        logger.warning(
            "SECURITY WARNING: authentication is DISABLED and the server is bound to '%s' "
            "(non-loopback). All API endpoints are accessible without credentials. "
            "Enable authentication (server.auth.enabled=true) before exposing this service "
            "on a network interface.",
            bind_host,
        )

    # Add trusted host middleware only when an explicit allowlist is provided.
    # The default is [] (disabled), so omitting this in config is safe.
    if server_config.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=server_config.trusted_hosts)  # type: ignore[arg-type]

    # Add read-only mode middleware (runs before CORS so preflight OPTIONS still pass freely)
    if getattr(server_config, "read_only", False):
        app.add_middleware(ReadOnlyMiddleware, enabled=True)
        logger.info("Read-only mode middleware enabled")

    # Add CORS middleware
    if server_config.cors.enabled:
        app.add_middleware(  # type: ignore[arg-type]
            cast(Any, CORSMiddleware),
            allow_origins=server_config.cors.origins,
            allow_credentials=server_config.cors.credentials,
            allow_methods=server_config.cors.methods,
            allow_headers=server_config.cors.headers,
        )
        logger.info("CORS middleware enabled")
        if server_config.cors.origins == ["*"] and server_config.auth.enabled:
            logger.warning(
                "CORS allows all origins (origins=['*']) with auth enabled — "
                "consider restricting to known UI origins in production."
            )

    # Add logging middleware
    app.add_middleware(LoggingMiddleware)
    logger.info("Logging middleware enabled")

    # Add authentication middleware if enabled
    if server_config.auth.enabled:
        auth_strategy = _create_auth_strategy(server_config.auth)
        if auth_strategy:
            # Load the daemon-issued loopback-admin token (if present) so the
            # CLI reload command can authenticate without a user-facing JWT.
            _load_loopback_token(server_config)
            # Wrap the real strategy so loopback tokens are checked first.
            auth_port: Any = _LoopbackAdminAuthWrapper(auth_strategy)
            app.add_middleware(
                AuthMiddleware,
                auth_port=auth_port,
                require_auth=True,
                trusted_proxies=server_config.trusted_proxies,
            )
            logger.info(
                "Authentication middleware enabled with strategy: %s",
                auth_strategy.get_strategy_name(),
            )
        else:
            raise ConfigurationError(
                f"Authentication enabled but strategy '{server_config.auth.strategy}' could not be created"
            )

    # Add rate-limit middleware (runs inside Auth so user identity is already resolved)
    rate_limiting_cfg = getattr(server_config, "rate_limiting", None)
    if rate_limiting_cfg is not None and rate_limiting_cfg.get("enabled", True):
        app.add_middleware(RateLimitMiddleware, rate_limiting_config=rate_limiting_cfg)
        logger.info(
            "Rate-limit middleware enabled (%s req/min)",
            rate_limiting_cfg.get("requests_per_minute", 100),
        )

    # Add audit-log middleware (innermost — status_code and latency are most accurate here)
    if getattr(server_config, "audit_log_enabled", True):
        app.add_middleware(AuditLogMiddleware)
        logger.info("Audit-log middleware enabled")

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

    @app.get("/health", tags=["System"])
    async def health_check(health_port: Any = Depends(get_health_check_port)) -> Any:  # type: ignore[misc]
        """Health check endpoint."""
        try:
            health_port.run_all_checks()
            status = health_port.get_status()
        except Exception:
            status = {"status": "unknown"}

        status = {"service": "open-resource-broker", "version": __version__, **status}
        http_status = 503 if status.get("status") == "unhealthy" else 200
        return JSONResponse(content=status, status_code=http_status)  # type: ignore[misc]

    # Add metrics endpoint
    @app.get("/metrics", tags=["System"])
    async def metrics() -> Any:
        """Prometheus metrics endpoint."""
        from orb.infrastructure.di.container import get_container
        from orb.monitoring.metrics import MetricsCollector

        try:
            collector = get_container().get_optional(MetricsCollector)
            if collector is None:
                return Response(content="", media_type="text/plain; version=0.0.4")  # type: ignore[misc]
            prometheus_text = collector.to_prometheus_text()
            return Response(content=prometheus_text, media_type="text/plain; version=0.0.4")  # type: ignore[misc]
        except Exception:
            return Response(content="", media_type="text/plain; version=0.0.4")  # type: ignore[misc]

    # Add info endpoint
    @app.get("/info", tags=["System"])
    async def info() -> dict[str, Any]:
        """Service information endpoint."""
        return {
            "service": "open-resource-broker",
            "version": __version__,
            "description": "REST API for Open Resource Broker",
            "auth_enabled": server_config.auth.enabled,
            "auth_strategy": (server_config.auth.strategy if server_config.auth.enabled else None),
        }

    # Serve favicon from project logo assets
    _favicon_path = Path(__file__).resolve().parents[3] / "docs" / "assets" / "orb-icon.png"
    if _favicon_path.exists():

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon() -> Any:
            from fastapi.responses import FileResponse

            return FileResponse(_favicon_path, media_type="image/png")

    # Register API routers
    _register_routers(app)

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


def _create_auth_strategy(auth_config: Any) -> Any:
    """
    Create authentication strategy based on configuration.

    Delegates config extraction entirely to each strategy's ``from_auth_config``
    classmethod via the auth registry.  No per-strategy dispatch lives here.

    Args:
        auth_config: AuthConfig instance

    Returns:
        Authentication strategy instance, or None if the strategy name is unknown

    Raises:
        ConfigurationError: If the strategy is known but its config is invalid
    """
    logger = get_logger(__name__)

    strategy_name = getattr(auth_config, "strategy", "unknown")
    try:
        auth_registry = get_auth_registry()
        return auth_registry.get_strategy(strategy_name, auth_config)

    except ValueError:
        logger.error("Unknown authentication strategy: %s", strategy_name)
        return None
    except Exception as e:
        raise ConfigurationError(f"Failed to create auth strategy '{strategy_name}': {e}") from e


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
