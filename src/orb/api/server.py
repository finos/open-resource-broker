"""FastAPI server factory and application setup."""

import ipaddress
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

try:
    from fastapi import Depends, FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import JSONResponse, Response

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    Depends = None  # type: ignore[assignment,misc]
    FastAPI = None  # type: ignore[assignment,misc]
    CORSMiddleware = None  # type: ignore[assignment,misc]
    HTTPSRedirectMiddleware = None  # type: ignore[assignment,misc]
    TrustedHostMiddleware = None  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment,misc]
    Response = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from fastapi import Depends, FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import JSONResponse, Response

from orb._package import __version__
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.auth.registry import get_auth_registry
from orb.infrastructure.logging.logger import get_logger, setup_audit_logger

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

    The token set is stored as a class attribute so that it is tied to the class
    object rather than the module namespace.  This survives module reloads in
    test scenarios (where ``sys.modules["orb.api.server"]`` is mutated) because
    code that holds a reference to this class always reads the same set regardless
    of which module object ``orb.api.server`` currently points to.

    Token cache invalidation: the token file's mtime is checked on each request
    (cheap ``os.stat``).  When the mtime has changed since the last load the
    file is reloaded automatically.  The daemon (or any privileged caller) can
    also call ``rotate_token()`` on SIGHUP to force an immediate reload.
    """

    _tokens: ClassVar[set[str]] = set()
    # Path of the token file that was last loaded; None if tokens were
    # registered programmatically (e.g. in tests via _tokens.add()).
    _token_file: ClassVar[Path | None] = None
    # mtime of the token file at the time of the last successful load.
    _token_file_mtime: ClassVar[float] = 0.0

    @classmethod
    def rotate_token(cls) -> None:
        """Force an immediate reload of the token file.

        Intended to be called by the daemon on SIGHUP so that a rotated token
        becomes active without a full server restart.  If no token file is
        registered (auth disabled or fresh install) the call is a no-op.
        """
        if cls._token_file is None:
            return
        cls._reload_token_file(cls._token_file)

    @classmethod
    def _reload_token_file(cls, token_file: Path) -> None:
        """Load tokens from *token_file* and replace the current token set."""
        try:
            token = token_file.read_text(encoding="ascii").strip()
            if token:
                cls._tokens = {token}
                try:
                    cls._token_file_mtime = os.stat(token_file).st_mtime
                except OSError:
                    cls._token_file_mtime = 0.0
                _server_logger.info("loopback-admin token reloaded from %s", token_file)
        except OSError as exc:
            _server_logger.debug("loopback-admin token reload skipped: %s", exc)

    @classmethod
    def _check_and_refresh(cls) -> None:
        """Reload the token file if its mtime has changed since last load.

        Called on each authenticate() invocation — ``os.stat`` is a single
        syscall and is cheap enough for per-request use.
        """
        if cls._token_file is None:
            return
        try:
            current_mtime = os.stat(cls._token_file).st_mtime
        except OSError:
            return
        if current_mtime != cls._token_file_mtime:
            cls._reload_token_file(cls._token_file)

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._logger = get_logger(__name__)

    async def authenticate(self, context: Any) -> Any:
        from orb.infrastructure.adapters.ports.auth import AuthResult, AuthStatus

        # Cheap mtime check — reloads token file only when it has changed.
        self._check_and_refresh()

        auth_header: str = context.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            candidate = auth_header[7:].strip()
            try:
                candidate_bytes = candidate.encode("ascii") if candidate else b""
            except UnicodeEncodeError:
                # Non-ASCII bearer token can never match the loopback secret;
                # return UNAUTHENTICATED rather than crashing or silently skipping
                # the auth stamp (which could constitute an auth bypass).
                return AuthResult(
                    status=AuthStatus.INVALID,
                    user_id=None,
                    user_roles=[],
                    permissions=[],
                    error_message="invalid credentials",
                )
            if candidate_bytes and any(
                secrets.compare_digest(candidate_bytes, t.encode("ascii"))
                for t in _LoopbackAdminAuthWrapper._tokens
            ):
                # Mirror _LoopbackAdminTokenMiddleware below: the token value
                # alone is never enough. Only the direct TCP peer counts,
                # never X-Forwarded-For/other client-supplied headers, and
                # never context.client_ip (which can be resolved through a
                # trusted proxy forwarded header) so a remote caller cannot
                # spoof a loopback origin merely by presenting the token.
                # A missing peer is treated as non-loopback.
                if _is_loopback_address(getattr(context, "direct_client_ip", None)):
                    self._logger.debug("loopback-admin token accepted for %s", context.path)
                    return AuthResult(
                        status=AuthStatus.SUCCESS,
                        user_id="loopback-admin",
                        user_roles=["admin"],
                        permissions=["*"],
                        metadata={"strategy": "loopback_admin_token"},
                    )
                self._logger.warning(
                    "loopback-admin token presented from non-loopback peer for %s; "
                    "ignoring and falling through to normal auth",
                    context.path,
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
                _LoopbackAdminAuthWrapper._tokens = {token}
                _LoopbackAdminAuthWrapper._token_file = token_file
                try:
                    _LoopbackAdminAuthWrapper._token_file_mtime = os.stat(token_file).st_mtime
                except OSError:
                    _LoopbackAdminAuthWrapper._token_file_mtime = 0.0
                _server_logger.debug("loopback-admin token loaded from %s", token_file)
    except Exception as exc:
        _server_logger.debug("loopback-admin token load skipped: %s", exc)


def _is_loopback_address(host: str | None) -> bool:
    """Return True only when *host* is a loopback IP address literal.

    Shared by every call site that needs to decide whether a request
    originated on the local host. Callers must pass the *direct* socket
    peer address — never a value derived from ``X-Forwarded-For`` or any
    other client-supplied header — or this check can be spoofed by anyone
    who can reach the server over the network. Accepts IPv4 loopback
    (127.0.0.0/8), IPv6 loopback (``::1``), and IPv4-mapped IPv6 loopback
    (``::ffff:127.0.0.1``).
    """
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_loopback


def _is_loopback_peer(request: Any) -> bool:
    """Return True only when the request's direct TCP peer is loopback.

    Deliberately reads only ``request.client`` — the actual socket peer
    address as seen by the ASGI server — and never consults
    ``X-Forwarded-For`` or any other client-supplied header. Those headers
    can be set to any value by anyone who can reach the server over the
    network, so trusting them here would defeat the whole point of this
    check.
    """
    client = request.client
    return _is_loopback_address(client.host if client else None)


class _LoopbackAdminTokenMiddleware:
    """Always-on middleware that stamps admin identity for valid loopback tokens.

    Runs regardless of whether the primary auth middleware is enabled.  When
    the request carries ``Authorization: Bearer <token>`` and that token
    matches the value the daemon wrote to ``<work_dir>/server/orb-server.token``
    at startup, the middleware stamps ``request.state`` with the admin role so
    role-guarded routes (POST /machines/request, /admin/*, etc.) accept the
    call.

    Fall-through (no header, or non-matching token) leaves request state
    untouched so the ``AuthMiddleware`` (when present) and ``get_current_user``
    dependency continue to behave as before.
    """

    def __init__(self, app: Any) -> None:
        from starlette.middleware.base import BaseHTTPMiddleware

        self._impl = BaseHTTPMiddleware(app, dispatch=self._dispatch)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._impl(scope, receive, send)

    @staticmethod
    async def _dispatch(request: Any, call_next: Any) -> Any:
        # Cheap mtime check so the token file is reloaded when rotated.
        _LoopbackAdminAuthWrapper._check_and_refresh()

        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            candidate = auth[7:].strip()
            try:
                candidate_bytes = candidate.encode("ascii") if candidate else b""
            except UnicodeEncodeError:
                # Non-ASCII bearer value can never match the loopback secret;
                # skip the stamp so the request is not erroneously elevated.
                return await call_next(request)
            if candidate_bytes and any(
                secrets.compare_digest(candidate_bytes, t.encode("ascii"))
                for t in _LoopbackAdminAuthWrapper._tokens
            ):
                if _is_loopback_peer(request):
                    request.state.user_id = "loopback-admin"
                    request.state.user_roles = ["admin"]
                    request.state.permissions = ["*"]
                else:
                    # Token value matched, but the request did not originate
                    # from the local host. Never honour it from a
                    # non-loopback peer, and never trust X-Forwarded-For (or
                    # any other client-supplied header) for this decision —
                    # fall through to normal auth instead. Never log the
                    # token value itself.
                    client = request.client
                    _server_logger.warning(
                        "loopback-admin token presented from non-loopback peer "
                        "%s; ignoring and falling through to normal auth",
                        client.host if client else "<no client>",
                    )
        return await call_next(request)


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
    from orb.api.middleware import (
        AuditLogMiddleware,
        AuthMiddleware,
        ForwardedProtoMiddleware,
        LoggingMiddleware,
        RateLimitMiddleware,
        ReadOnlyMiddleware,
        SecurityHeadersMiddleware,
    )
    from orb.infrastructure.error.exception_handler import get_exception_handler

    # Install the dedicated audit-log handler early so that audit records
    # written during middleware setup (e.g. by AuditLogMiddleware) land in the
    # right place from the first request onward.
    _audit_log_file: str | None = getattr(server_config, "audit_log_file", None)
    setup_audit_logger(_audit_log_file)
    if _audit_log_file:
        logger.info("orb.audit logger writing to dedicated file: %s", _audit_log_file)

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

    # Effective request-processing order (outermost first). Starlette wraps
    # middleware in REVERSE registration order: each app.add_middleware()
    # call wraps everything already registered, so the LAST call below ends
    # up OUTERMOST and runs first on every request:
    #   CORS -> TrustedHost -> Auth -> RateLimit -> AuditLog ->
    #   LoopbackAdminToken -> Logging -> ReadOnly -> HTTPSRedirect ->
    #   ForwardedProto -> SecurityHeaders -> router
    # CORS is registered last (outermost) so a cross-origin preflight OPTIONS
    # request is answered directly before TrustedHost, auth, or rate limiting
    # ever see it. RateLimit is registered before AuthMiddleware so Auth ends
    # up more deeply nested and runs first — request.state.user_id is already
    # populated by the time RateLimitMiddleware resolves the caller's identity.

    # Add security headers middleware unconditionally — all responses, including
    # excluded-auth paths and auth-disabled deployments, must carry hardening headers.
    _require_https: bool = getattr(server_config, "require_https", False)

    app.add_middleware(
        SecurityHeadersMiddleware,
        require_https=_require_https,
        hsts_max_age=getattr(server_config, "hsts_max_age", 31536000),
    )
    logger.info("Security headers middleware enabled")

    if _require_https:
        app.add_middleware(cast(Any, HTTPSRedirectMiddleware))
        logger.info("HTTPS redirect middleware enabled (HTTP requests redirect to HTTPS with 307)")

        # Behind a TLS-terminating reverse proxy the connection to ORB is plain
        # HTTP, so scope["scheme"] is "http" and HTTPSRedirectMiddleware would
        # 307-redirect every request into an infinite loop (the proxy forwards
        # the follow-up as HTTP again).  Resolve the real scheme from
        # X-Forwarded-Proto for configured trusted proxies BEFORE the redirect
        # middleware runs.  Added after HTTPSRedirectMiddleware so it executes
        # first (Starlette runs middleware in reverse registration order).
        # Only trusted proxies are honoured, so HTTPS enforcement is not
        # weakened for direct/untrusted clients.
        if server_config.trusted_proxies:
            app.add_middleware(
                cast(Any, ForwardedProtoMiddleware),
                trusted_proxies=server_config.trusted_proxies,
            )
            logger.info(
                "X-Forwarded-Proto resolution enabled for trusted proxies "
                "(prevents HTTPS redirect loops behind TLS-terminating proxies)"
            )
        else:
            logger.warning(
                "require_https is enabled but no trusted_proxies are configured. "
                "If ORB runs behind a reverse proxy that terminates TLS and forwards "
                "plain HTTP, requests will be caught in an HTTPS redirect loop. Set "
                "server.trusted_proxies to the proxy IP(s) so X-Forwarded-Proto is honoured."
            )

    # Add read-only mode middleware.
    if getattr(server_config, "read_only", False):
        app.add_middleware(ReadOnlyMiddleware, enabled=True)
        logger.info("Read-only mode middleware enabled")

    # Add logging middleware
    app.add_middleware(LoggingMiddleware)
    logger.info("Logging middleware enabled")

    # Load the daemon-issued loopback-admin token unconditionally so the CLI's
    # reload command and the live REST tests can authenticate as admin
    # regardless of whether the primary auth middleware is enabled.  The
    # token-only middleware below validates Authorization headers and stamps
    # request.state with the admin role; if no token (or a non-matching token)
    # is present, request.state is left untouched and the rest of the pipeline
    # behaves as before.
    _load_loopback_token(server_config)
    app.add_middleware(_LoopbackAdminTokenMiddleware)
    logger.info("Loopback-admin token middleware enabled")

    # Add audit-log middleware. Registered before RateLimit/Auth below, which
    # (per the effective-order note above) leaves it more deeply nested than
    # both — by the time its post-response logging runs, Auth has already
    # populated request.state.user_id/user_roles for the audit record.
    if getattr(server_config, "audit_log_enabled", True):
        app.add_middleware(AuditLogMiddleware)
        logger.info("Audit-log middleware enabled")

    # Workers count is used by both the rate-limit and SSE multi-worker warnings below.
    _workers = getattr(server_config, "workers", 1) or 1

    # Add rate-limit middleware. Registered before AuthMiddleware below so Auth
    # ends up outside it (see the effective-order note above) and always runs
    # first, populating request.state.user_id before this middleware resolves
    # the caller's identity. Pass trusted_proxies so the limiter keys on the
    # real client IP rather than the proxy's IP when requests arrive through a
    # known reverse proxy.
    rate_limiting_cfg = getattr(server_config, "rate_limiting", None)
    if rate_limiting_cfg is not None and getattr(rate_limiting_cfg, "enabled", True):
        app.add_middleware(
            RateLimitMiddleware,
            rate_limiting_config=rate_limiting_cfg,
            trusted_proxies=server_config.trusted_proxies,
        )
        _rpm = getattr(rate_limiting_cfg, "requests_per_minute", 300)
        logger.info(
            "Rate-limit middleware enabled (%s req/min, burst %s)",
            _rpm,
            getattr(rate_limiting_cfg, "burst", 60),
        )
        # Rate-limit buckets are per-process: each worker maintains its own
        # in-memory counter, so the effective limit seen by a single client is
        # requests_per_minute × workers when requests are spread across processes
        # by the load balancer.  Warn operators so they can scale the configured
        # limit down (divide by workers) or move to a shared backend limiter.
        if _workers > 1:
            logger.warning(
                "MULTI_WORKER_RATE_LIMIT: server.workers=%d but rate-limit buckets are "
                "per-process. The effective per-client limit is %d req/min × %d workers = "
                "%d req/min. Divide requests_per_minute by the worker count or use a "
                "shared rate-limit backend to enforce the intended per-client cap.",
                _workers,
                _rpm,
                _workers,
                _rpm * _workers,
            )

    # Add authentication middleware if enabled. Registered after RateLimit
    # above and before TrustedHost/CORS below, so Auth is more deeply nested
    # than CORS/TrustedHost (both of which must clear the request first) but
    # less deeply nested than RateLimit (see the effective-order note above).
    if server_config.auth.enabled:
        auth_strategy = _create_auth_strategy(server_config.auth)
        if auth_strategy:
            # Wrap the real strategy so loopback tokens are checked first.
            auth_port: Any = _LoopbackAdminAuthWrapper(auth_strategy)

            # Build the excluded-paths list for auth middleware.
            # /health, /livez, /readyz are always public probe endpoints
            # (no sensitive detail) so orchestrators can poll them unauthenticated.
            # /favicon.ico is static UI chrome, not sensitive.
            # /docs, /redoc, /openapi.json are excluded ONLY when
            # docs.require_auth=False — by default they are protected so that
            # the full route map is not disclosed to unauthenticated callers.
            _docs_require_auth: bool = getattr(
                getattr(server_config, "docs", None), "require_auth", True
            )
            _excluded: list[str] = ["/health", "/livez", "/readyz", "/favicon.ico"]
            if not _docs_require_auth:
                _excluded.extend(["/docs", "/redoc", "/openapi.json"])
            else:
                logger.info(
                    "API documentation endpoints (/docs, /redoc, /openapi.json) "
                    "are protected by authentication (docs.require_auth=true). "
                    "Set docs.require_auth=false to make them public."
                )

            app.add_middleware(
                AuthMiddleware,
                auth_port=auth_port,
                require_auth=True,
                excluded_paths=_excluded,
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

    # Add trusted host middleware only when a restrictive allowlist is provided.
    # An empty list or a wildcard ('*') disables Host-header validation entirely,
    # so warn the operator that this protection has been turned off. Registered
    # second-to-last, just inside CORS (see the effective-order note above), so
    # an untrusted Host header is rejected before any middleware above runs.
    if not server_config.trusted_hosts or "*" in server_config.trusted_hosts:
        logger.warning(
            "SECURITY WARNING: trusted_hosts is %s — Host-header protection is "
            "effectively DISABLED. The server will accept requests with any Host "
            "header, exposing it to DNS-rebinding and Host-header spoofing attacks. "
            "Set server.trusted_hosts to an explicit list of expected hostnames.",
            "empty" if not server_config.trusted_hosts else "['*']",
        )
    else:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=server_config.trusted_hosts)  # type: ignore[arg-type]

    # Add CORS middleware last so it is the outermost layer in the effective
    # request order (see the note above). This lets CORSMiddleware answer a
    # cross-origin preflight OPTIONS request directly, before TrustedHost,
    # auth, or rate limiting ever see it — otherwise a preflight to any
    # non-excluded route is authenticated like any other request and gets
    # rejected with 401 instead of a CORS response.
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

    @app.get("/health", tags=["System"], operation_id="healthCheck")
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
    async def readiness_check(health_port: Any = Depends(get_health_check_port)) -> Any:  # type: ignore[misc]
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
        try:
            health_port.run_all_checks()
            status = health_port.get_readiness()
        except Exception:
            status = {"status": "unknown"}

        http_status = 503 if status.get("status") in ("unhealthy", "degraded") else 200
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
    except Exception:
        pass  # Config or DI resolution failed; skip without crashing.

    # Warn when multiple uvicorn workers are configured alongside the SSE
    # events router.  The in-process pubsub (SseEventBus) is not shared across
    # worker processes, so events published in one worker are invisible to
    # subscribers connected to a different worker.  This is a data-loss risk,
    # not an error — operators may have valid reasons (e.g. a shared queue
    # upstream), so we warn but do not refuse to start.
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
