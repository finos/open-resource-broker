"""System routes (health, liveness, readiness, metrics, info, favicon) of the REST server.

FastAPI and Starlette names are imported inside ``register_system_routes`` so this
module can be imported when the ``api`` extra is not installed.
"""

import asyncio
from typing import Any

from orb._package import __version__


def register_system_routes(app: Any, server_config: Any, auth_port: Any) -> None:
    """Register the system routes on *app*.

    ``auth_port`` is the port returned by ``install_middleware`` (None when
    authentication is disabled); /health and /readyz use it to decide whether a
    caller may see per-check detail.
    """
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse, Response

    # Add health check endpoint
    from orb.api.dependencies import get_health_check_port

    def _run_checks_then(health_port: Any, summary: str) -> Any:
        health_port.run_all_checks()
        return getattr(health_port, summary)()

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

    # Serve the favicon from a packaged resource rather than a path built from
    # the module location, so the route never touches request-controllable
    # input and works regardless of install layout (editable, sdist, wheel).
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
