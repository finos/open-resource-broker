"""Provider management API routes."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional, cast

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import get_config_manager, get_di_container, require_role
from orb.domain.base.exceptions import EntityNotFoundError, InfrastructureError
from orb.infrastructure.error.decorators import handle_rest_exceptions
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/providers", tags=["Providers"])

CONFIG_MANAGER = Depends(get_config_manager)

# Resource types the discovery endpoint accepts, mapped to the identifiers the
# provider strategy understands.
_DISCOVERY_RESOURCE_TYPES: dict[str, str] = {
    "vpcs": "vpcs",
    "subnets": "subnets",
    "security_groups": "security_groups",
}

# Discovery results are cached for 5 minutes: infrastructure topology changes
# rarely, and each miss issues several EC2 Describe* calls.
_DISCOVERY_CACHE_TTL_SECONDS = 300
# Hard cap on cached entries. The cache key includes the caller-supplied
# ``vpc_id`` query parameter, and AWS vpc-id filters return an empty-but-
# successful result for unknown IDs, so an operator issuing many distinct
# vpc_id values could otherwise grow the cache without bound. Bounding the
# size (with LRU eviction) keeps memory usage predictable.
_DISCOVERY_CACHE_MAX_ENTRIES = 512
# Insertion-ordered so the oldest entry is the first key — enabling O(1)
# LRU-style eviction once the size cap is reached.
_discovery_cache: "OrderedDict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]]" = (
    OrderedDict()
)
_discovery_cache_lock = threading.Lock()

VPC_ID_QUERY = Query(
    None,
    description="VPC to scope the lookup to. Required for 'subnets' and 'security_groups'.",
)


def _discovery_cache_get(
    key: tuple[str, str, str],
) -> Optional[list[dict[str, Any]]]:
    """Return a non-expired cached discovery result, or ``None`` on miss."""
    with _discovery_cache_lock:
        entry = _discovery_cache.get(key)
        if entry is None:
            return None
        cached_at, value = entry
        if (time.monotonic() - cached_at) >= _DISCOVERY_CACHE_TTL_SECONDS:
            _discovery_cache.pop(key, None)
            return None
        # Mark as most-recently-used so it survives size-cap eviction.
        _discovery_cache.move_to_end(key)
        return value


def _discovery_cache_put(key: tuple[str, str, str], value: list[dict[str, Any]]) -> None:
    """Store a discovery result, evicting expired and overflowing entries.

    Before inserting, expired entries are swept (they are otherwise only
    dropped when re-requested) and, once the size cap is reached, the oldest
    entries are evicted LRU-style. This keeps the cache bounded even when the
    caller-supplied ``vpc_id`` produces an unbounded set of distinct keys.
    """
    now = time.monotonic()
    with _discovery_cache_lock:
        # Sweep expired entries so stale keys never count toward the cap.
        expired = [
            k
            for k, (cached_at, _) in _discovery_cache.items()
            if (now - cached_at) >= _DISCOVERY_CACHE_TTL_SECONDS
        ]
        for k in expired:
            _discovery_cache.pop(k, None)

        # Refresh/insert as most-recently-used.
        _discovery_cache[key] = (now, value)
        _discovery_cache.move_to_end(key)

        # Enforce the hard cap by evicting the oldest entries.
        while len(_discovery_cache) > _DISCOVERY_CACHE_MAX_ENTRIES:
            _discovery_cache.popitem(last=False)


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
        # Strip CR/LF before logging so a crafted provider_type can't forge
        # extra log entries.
        logger.warning(
            "Failed to retrieve UI column schema for provider '%s': %s",
            provider_type.replace("\r", "").replace("\n", ""),
            exc,
            exc_info=True,
        )
        return []


@router.get(
    "/schemas",
    operation_id="getAllProviderSchemas",
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
    operation_id="getProviderSchema",
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
        # Strip CR/LF before logging so a crafted provider name can't forge
        # extra log entries.
        logger.warning(
            "Failed to build schema for provider '%s': %s",
            name.replace("\r", "").replace("\n", ""),
            exc,
            exc_info=True,
        )
        schema = []

    return JSONResponse(
        content={"schema_version": 1, "schema": schema},
        status_code=200,
        headers={"x-schema-version": "1"},
    )


@router.get(
    "/",
    operation_id="listProviders",
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
    operation_id="getProvidersHealth",
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


def _discover_resources(
    provider_api: str, resource_type: str, vpc_id: Optional[str]
) -> list[dict[str, Any]]:
    """Resolve the provider strategy and return discovered resources.

    Runs synchronously (issues blocking boto3 EC2 Describe* calls); callers
    offload it to a worker thread. Raises ``EntityNotFoundError`` when the
    provider is not registered and ``NotImplementedError`` when the strategy
    has no machine-readable discovery.

    A dedicated ``EntityNotFoundError`` — rather than the broad ``LookupError``
    — signals the not-registered case, so genuine ``KeyError`` / ``IndexError``
    (both ``LookupError`` subclasses) raised while parsing an AWS response
    surface as 5xx instead of being mistaken for provider-not-found (404).
    """
    from orb.providers.registry.provider_registry import get_provider_registry

    registry = get_provider_registry()
    if not registry.ensure_provider_type_registered(provider_api):
        raise EntityNotFoundError("Provider", provider_api)

    strategy = registry.get_or_create_strategy(provider_api)
    if strategy is None:
        raise EntityNotFoundError("Provider", provider_api)

    list_resources = getattr(strategy, "list_resources", None)
    if not callable(list_resources):
        raise NotImplementedError(provider_api)

    try:
        return cast("list[dict[str, Any]]", list_resources(resource_type, vpc_id))
    except Exception as exc:
        # A failure while issuing the boto3 Describe* calls or parsing their
        # responses (e.g. a KeyError from an unexpected AWS response shape) is a
        # server-side problem, not a bad client request. Wrap it as an
        # InfrastructureError so it surfaces as 5xx — never as a 404
        # provider-not-found (only the explicit not-registered / null-strategy
        # cases above yield that) nor a 400 client error.
        raise InfrastructureError(
            f"Resource discovery failed for provider '{provider_api}'"
        ) from exc


@router.get(
    "/discover/{provider_api}/{resource_type}",
    operation_id="discoverProviderResources",
    summary="Discover Provider Infrastructure Resources",
    description=(
        "Discovers infrastructure resources of the given type for a provider so "
        "the template form can offer dropdowns instead of free-text IDs. "
        "Supported resource types: vpcs, subnets, security_groups. "
        "'subnets' and 'security_groups' require a 'vpc_id' query parameter. "
        "Results are cached for 5 minutes."
    ),
)
@handle_rest_exceptions(
    endpoint="/api/v1/providers/discover/{provider_api}/{resource_type}", method="GET"
)
async def discover_provider_resources(
    provider_api: str,
    resource_type: str,
    vpc_id: str | None = VPC_ID_QUERY,
    _user=Depends(require_role("operator")),
) -> JSONResponse:
    """Discover VPCs / subnets / security groups for a provider.

    Requires the ``operator`` role: discovery enumerates account infrastructure
    (VPC/subnet/SG IDs, CIDR blocks) that viewer-role callers must not see.
    """
    normalised_type = _DISCOVERY_RESOURCE_TYPES.get(resource_type.lower())
    if normalised_type is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported resource type '{resource_type}'. "
                f"Supported: {', '.join(sorted(_DISCOVERY_RESOURCE_TYPES))}."
            ),
        )

    if normalised_type in ("subnets", "security_groups") and not vpc_id:
        raise HTTPException(
            status_code=400,
            detail=f"Query parameter 'vpc_id' is required for resource type '{normalised_type}'.",
        )

    provider_api = provider_api.lower()
    cache_key = (provider_api, normalised_type, vpc_id or "")
    cached = _discovery_cache_get(cache_key)
    if cached is not None:
        return JSONResponse(
            content={
                "provider_api": provider_api,
                "resource_type": normalised_type,
                "vpc_id": vpc_id,
                "resources": cached,
                "cached": True,
            },
            status_code=200,
        )

    try:
        resources = await run_in_threadpool(
            _discover_resources, provider_api, normalised_type, vpc_id
        )
    except EntityNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Provider '{provider_api}' is not available."
        ) from None
    except NotImplementedError:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider_api}' does not support resource discovery.",
        ) from None

    _discovery_cache_put(cache_key, resources)

    return JSONResponse(
        content={
            "provider_api": provider_api,
            "resource_type": normalised_type,
            "vpc_id": vpc_id,
            "resources": resources,
            "cached": False,
        },
        status_code=200,
    )
