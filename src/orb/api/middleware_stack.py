"""Middleware registration for the REST server.

``install_middleware`` registers every middleware of the application in one
place and in one order.  Starlette wraps middleware in reverse registration
order, so the sequence of ``add_middleware`` calls below is the security
boundary: do not reorder or split them.

Everything that needs FastAPI or Starlette is imported inside the function body
so this module can be imported when the ``api`` extra is not installed.
"""

from typing import Any, cast

from orb.api.loopback_admin import (
    _load_loopback_token,
    _LoopbackAdminAuthWrapper,
    _LoopbackAdminTokenMiddleware,
)
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.auth.registry import get_auth_registry
from orb.infrastructure.logging.logger import get_logger

# Log under the server's own logger name so existing log filters and alerts keep
# matching after the code moved out of orb.api.server.
_LOGGER_NAME = "orb.api.server"


def install_middleware(app: Any, server_config: Any, logger: Any) -> Any:
    """Register the full middleware stack on *app*.

    Returns the auth port the stack authenticates with, or None when
    authentication is disabled; the health routes use it to decide whether a
    caller may see per-check detail.
    """
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    from orb.api.middleware import (
        AuditLogMiddleware,
        AuthMiddleware,
        ForwardedProtoMiddleware,
        LoggingMiddleware,
        RateLimitMiddleware,
        ReadOnlyMiddleware,
        SecurityHeadersMiddleware,
    )

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
    #   LoopbackAdminToken -> Logging -> ReadOnly -> ForwardedProto ->
    #   HTTPSRedirect -> SecurityHeaders -> router
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
        app.add_middleware(AuditLogMiddleware, trusted_proxies=server_config.trusted_proxies)
        logger.info("Audit-log middleware enabled")

    # Workers count is used by both the rate-limit and SSE multi-worker warnings below.
    _workers = getattr(server_config, "workers", 1) or 1
    if _workers > 1:
        logger.warning(
            "MULTI_WORKER_FLEET_RELEASE: server.workers=%d. Returns of one fleet are "
            "serialised within a single process only, so returns handled by different "
            "workers (or by `orb return` beside the server) can interleave their capacity "
            "reductions. Use server.workers=1 if returns of the same fleet can overlap.",
            _workers,
        )

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
    # Bound only when authentication is enabled; /health uses it to decide
    # whether a caller may see the per-check detail.
    auth_port: Any = None
    if server_config.auth.enabled:
        auth_strategy = _create_auth_strategy(server_config.auth)
        if auth_strategy:
            # Wrap the real strategy so loopback tokens are checked first.
            auth_port = _LoopbackAdminAuthWrapper(auth_strategy)

            # Build the excluded-paths list for auth middleware.
            # /health, /livez, /readyz are always public probe endpoints so
            # orchestrators can poll them unauthenticated; without credentials
            # /health and /readyz return only the overall and per-check status.
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
            expose_headers=["X-ORB-Scheduler"],
        )
        logger.info("CORS middleware enabled")
        if server_config.cors.origins == ["*"] and server_config.auth.enabled:
            logger.warning(
                "CORS allows all origins (origins=['*']) with auth enabled — "
                "consider restricting to known UI origins in production."
            )

    return auth_port


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
    logger = get_logger(_LOGGER_NAME)

    strategy_name = getattr(auth_config, "strategy", "unknown")
    try:
        auth_registry = get_auth_registry()
        return auth_registry.get_strategy(strategy_name, auth_config)

    except ValueError:
        logger.error("Unknown authentication strategy: %s", strategy_name)
        return None
    except Exception as e:
        raise ConfigurationError(f"Failed to create auth strategy '{strategy_name}': {e}") from e
