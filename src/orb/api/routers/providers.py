"""Provider management API routes."""

from __future__ import annotations

from typing import Any, cast

try:
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import get_config_manager
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/providers", tags=["Providers"])

CONFIG_MANAGER = Depends(get_config_manager)


@router.get(
    "/health",
    summary="Provider Health",
    description=(
        "Returns per-provider configuration status for the UI Config page. "
        "Connectivity status is config-driven; real probes are not performed."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/providers/health", method="GET")
async def get_providers_health(
    config_manager=CONFIG_MANAGER,
) -> JSONResponse:
    """Return per-provider health/status derived from configuration.

    Status values:
    - ``healthy``   – provider is enabled and has a registered strategy
    - ``degraded``  – provider is enabled but its strategy could not be resolved
    - ``unhealthy`` – provider is explicitly disabled
    - ``unknown``   – provider exists in config but health state cannot be determined

    No outbound connectivity checks are performed (no AWS API calls, etc.).
    TODO: Once a ProviderHealthPort or similar probe is available, replace the
    ``status: "unknown"`` path with a real health check so ``healthy`` /
    ``degraded`` reflect actual connectivity.
    """
    providers_info: list[dict[str, Any]] = []
    active_provider_name: str | None = None
    default_provider_instance: str | None = None

    try:
        provider_config: Any = cast(Any, config_manager.get_provider_config())

        if provider_config:
            # Determine active / default provider name from selection policy config
            try:
                default_provider_instance = getattr(
                    provider_config, "default_provider", None
                )
            except Exception:
                default_provider_instance = None

            try:
                active_providers = provider_config.get_active_providers()
            except Exception:
                active_providers = []

            for provider_instance in active_providers:
                name: str = getattr(provider_instance, "name", "")
                ptype: str = getattr(provider_instance, "type", "unknown")
                enabled: bool = bool(getattr(provider_instance, "enabled", True))
                instance_config: dict[str, Any] = getattr(
                    provider_instance, "config", {}
                ) or {}

                if enabled:
                    # No live probe — status is config-driven
                    # TODO: call provider health port once available
                    status = "unknown"
                else:
                    status = "unhealthy"

                # Best-effort details — never crash on missing attributes
                details: dict[str, Any] = {}
                region = instance_config.get("region")
                if region:
                    details["region"] = region
                profile = instance_config.get("profile") or instance_config.get(
                    "aws_profile"
                )
                if profile:
                    details["profile"] = profile

                is_active = active_provider_name is None and enabled
                if is_active:
                    active_provider_name = name

                providers_info.append(
                    {
                        "name": name,
                        "type": ptype,
                        "enabled": enabled,
                        "active": is_active,
                        "status": status,
                        "details": details,
                    }
                )

            # Mark the first enabled provider as active if we found one
            if active_provider_name and providers_info:
                for p in providers_info:
                    if p["name"] == active_provider_name:
                        p["active"] = True
                        break

    except Exception:
        # Return empty-but-valid response; never 500 from a read-only status endpoint
        providers_info = []

    return JSONResponse(
        content={
            "providers": providers_info,
            "active_provider": active_provider_name,
            "default_provider_instance": default_provider_instance or active_provider_name,
        },
        status_code=200,
    )
