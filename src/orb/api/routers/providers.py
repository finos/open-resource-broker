"""Provider management API routes."""

from __future__ import annotations

from typing import Any, cast

try:
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import get_config_manager, get_di_container, require_role
from orb.infrastructure.error.decorators import handle_rest_exceptions
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/providers", tags=["Providers"])

CONFIG_MANAGER = Depends(get_config_manager)


async def _probe_provider_health(provider_name: str) -> tuple[str, dict[str, Any]]:
    """Run the ``HEALTH_CHECK`` operation through the registry.

    Returns ``(status, details)`` where status is one of
    ``healthy`` / ``degraded`` / ``unknown``. Failures are caught — a
    read-only status endpoint must never throw.

    Error details are logged server-side; only a generic status is returned to
    the client to prevent leaking provider credentials, account IDs, or ARNs.
    """
    try:
        from orb.application.services.provider_registry_service import (
            ProviderRegistryService,
        )
        from orb.domain.base.operations import (
            Operation as ProviderOperation,
            OperationType as ProviderOperationType,
        )

        container = get_di_container()
        registry = container.get(ProviderRegistryService)
        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
            context={"source": "providers_health_endpoint"},
        )
        result = await registry.execute_operation(provider_name, operation)
    except Exception as exc:
        # Log full error server-side; never forward provider internals to client.
        logger.warning("Provider health probe failed for '%s': %s", provider_name, exc)
        return "unknown", {}

    if not result.success or not result.data:
        # Log the internal error; return only a generic status to the caller.
        logger.warning(
            "Provider health check unhealthy for '%s': %s",
            provider_name,
            result.error_message or "health check failed",
        )
        return "degraded", {}

    data = result.data
    is_healthy = bool(data.get("is_healthy", False))
    details: dict[str, Any] = {}
    if "response_time_ms" in data:
        details["response_time_ms"] = data["response_time_ms"]
    if data.get("status_message"):
        details["status_message"] = data["status_message"]
    return ("healthy" if is_healthy else "degraded"), details


@router.get(
    "/health",
    summary="Provider Health",
    description=(
        "Returns per-provider configuration + live connectivity status. "
        "Each enabled provider is probed via the registry's HEALTH_CHECK "
        "operation (AWS: sts:GetCallerIdentity or equivalent)."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/providers/health", method="GET")
async def get_providers_health(
    config_manager=CONFIG_MANAGER,
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """Return per-provider health/status.

    Status values:
    - ``healthy``   – provider is enabled and HEALTH_CHECK succeeded
    - ``degraded``  – provider is enabled but HEALTH_CHECK failed
    - ``unhealthy`` – provider is explicitly disabled
    - ``unknown``   – probe could not run (registry resolution failed)
    """
    providers_info: list[dict[str, Any]] = []
    active_provider_name: str | None = None
    default_provider_instance: str | None = None

    try:
        provider_config: Any = cast(Any, config_manager.get_provider_config())

        if provider_config:
            # Determine active / default provider name from selection policy config
            try:
                default_provider_instance = getattr(provider_config, "default_provider", None)
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
                instance_config: dict[str, Any] = getattr(provider_instance, "config", {}) or {}

                details: dict[str, Any] = {}
                if enabled:
                    status, probe_details = await _probe_provider_health(name)
                    details.update(probe_details)
                else:
                    status = "unhealthy"

                # Best-effort details — never crash on missing attributes
                region = instance_config.get("region")
                if region:
                    details["region"] = region
                profile = instance_config.get("profile") or instance_config.get("aws_profile")
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
