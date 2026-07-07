"""Provider management API routes."""

from __future__ import annotations

from typing import Any, cast

try:
    from fastapi import APIRouter, Depends, HTTPException
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


def _get_schema_for_provider_type(provider_type: str) -> list[dict[str, Any]]:
    """Return serialised UIColumnDescriptors for a single provider type.

    Resolves the strategy class registered under *provider_type* (no
    live instance needed — schema is declared on the class/method level)
    and calls ``get_ui_column_schema()``.

    Raises ``KeyError`` when the provider type is not registered.
    """
    from orb.providers.registry.provider_registry import get_provider_registry
    from orb.providers.registry.types import ProviderRegistration

    registry = get_provider_registry()
    reg = registry._get_type_registration(provider_type)  # raises ValueError if missing
    if not isinstance(reg, ProviderRegistration) or reg.strategy_class is None:
        return []

    try:
        # Call the classmethod directly — no instance needed.
        # get_ui_column_schema only constructs UIColumnDescriptor objects; no I/O.
        schema = reg.strategy_class.get_ui_column_schema()
        return [col.to_dict() for col in schema]
    except Exception as exc:
        logger.warning(
            "Failed to retrieve UI column schema for provider '%s': %s",
            provider_type,
            exc,
            exc_info=True,
        )
        return []


@router.get(
    "/schemas",
    summary="All Provider UI Column Schemas",
    description=(
        "Returns a mapping of provider name → list of UIColumnDescriptor objects "
        "contributed by every registered provider strategy. "
        "The UI layer merges these at render time to build per-resource column sets."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/providers/schemas", method="GET")
async def get_all_provider_schemas(
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """Aggregate UI column schemas from all registered provider strategies."""
    from orb.providers.registry.provider_registry import get_provider_registry

    registry = get_provider_registry()
    result: dict[str, list[dict[str, Any]]] = {}

    for provider_type in registry.get_registered_providers():
        try:
            result[provider_type] = _get_schema_for_provider_type(provider_type)
        except Exception as exc:
            logger.warning(
                "Skipping schema for provider '%s': %s", provider_type, exc, exc_info=True
            )
            result[provider_type] = []

    return JSONResponse(
        content={"schema_version": 1, "schemas": result},
        status_code=200,
        headers={"x-schema-version": "1"},
    )


@router.get(
    "/{name}/schema",
    summary="Provider UI Column Schema",
    description=(
        "Returns the list of UIColumnDescriptor objects contributed by the named "
        "provider strategy.  Use this to discover provider-specific columns for "
        "machines, requests, and templates resource types."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/providers/{name}/schema", method="GET")
async def get_provider_schema(
    name: str,
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """Return UI column schema for a single named provider."""
    from orb.providers.registry.provider_registry import get_provider_registry

    registry = get_provider_registry()

    if not registry.is_provider_registered(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found.")

    try:
        schema = _get_schema_for_provider_type(name)
    except Exception as exc:
        logger.warning("Failed to build schema for provider '%s': %s", name, exc, exc_info=True)
        schema = []

    return JSONResponse(
        content={"schema_version": 1, "schema": schema},
        status_code=200,
        headers={"x-schema-version": "1"},
    )


@router.get(
    "/",
    summary="List Providers",
    description=(
        "Returns all configured provider instances with name, type, enabled flag, "
        "and a provider-specific config object.  Does not perform live connectivity "
        "probes; use GET /providers/health for live status."
    ),
)
@handle_rest_exceptions(endpoint="/api/v1/providers", method="GET")
async def list_providers(
    config_manager=CONFIG_MANAGER,
    _user=Depends(require_role("viewer")),
) -> JSONResponse:
    """Return all configured provider instances.

    Each entry includes:

    * ``name``    – the unique provider instance identifier
    * ``type``    – the provider type (e.g. ``"aws"``, ``"k8s"``)
    * ``enabled`` – whether the instance is active
    * ``config``  – provider-specific configuration keys (nested object)

    AWS-specific top-level keys such as ``"profile"`` are intentionally
    absent; they are nested inside ``config`` when present.
    """
    providers_list: list[dict[str, Any]] = []

    try:
        provider_config: Any = cast(Any, config_manager.get_provider_config())

        if provider_config:
            try:
                active_providers = provider_config.get_active_providers()
            except Exception as exc:
                logger.warning("Failed to retrieve active providers: %s", exc, exc_info=True)
                active_providers = []

            for provider_instance in active_providers:
                name: str = getattr(provider_instance, "name", "")
                ptype: str = getattr(provider_instance, "type", "unknown")
                enabled: bool = bool(getattr(provider_instance, "enabled", True))
                instance_config: dict[str, Any] = getattr(provider_instance, "config", {}) or {}

                providers_list.append(
                    {
                        "name": name,
                        "type": ptype,
                        "enabled": enabled,
                        "config": instance_config,
                    }
                )

    except Exception as exc:
        logger.warning("Unhandled error building providers list response: %s", exc, exc_info=True)
        providers_list = []

    return JSONResponse(
        content={"providers": providers_list, "total_count": len(providers_list)},
        status_code=200,
    )


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
            except Exception as e:
                logger.warning(
                    "Failed to read default_provider from provider config: %s",
                    e,
                    exc_info=True,
                )
                default_provider_instance = None

            try:
                active_providers = provider_config.get_active_providers()
            except Exception as e:
                logger.warning(
                    "Failed to retrieve active providers: %s",
                    e,
                    exc_info=True,
                )
                active_providers = []

            for provider_instance in active_providers:
                name: str = getattr(provider_instance, "name", "")
                ptype: str = getattr(provider_instance, "type", "unknown")
                enabled: bool = bool(getattr(provider_instance, "enabled", True))

                details: dict[str, Any] = {}
                if enabled:
                    status, probe_details = await _probe_provider_health(name)
                    details.update(probe_details)
                else:
                    status = "unhealthy"

                # region and profile are operator-only fields — strip them so
                # viewer-role callers cannot enumerate AWS profiles or regions.
                # Operators see the full details via the operator-scoped
                # /providers/health?details=full endpoint (future) or by
                # checking provider config directly.

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

    except Exception as e:
        # Return empty-but-valid response; never 500 from a read-only status endpoint
        logger.warning(
            "Unhandled error building providers health response: %s",
            e,
            exc_info=True,
        )
        providers_info = []

    return JSONResponse(
        content={
            "providers": providers_info,
            "active_provider": active_provider_name,
            "default_provider_instance": default_provider_instance or active_provider_name,
        },
        status_code=200,
    )
