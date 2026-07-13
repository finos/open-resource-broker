"""FastAPI dependency injection integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

_deps_logger = logging.getLogger(__name__)

try:
    from fastapi import Depends, HTTPException, Request, status
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
from orb.application.services.orchestration.dashboard_summary import DashboardSummaryOrchestrator
from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.application.services.orchestration.refresh_templates import RefreshTemplatesOrchestrator
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
from orb.application.services.orchestration.validate_template import ValidateTemplateOrchestrator
from orb.application.services.template_generation_service import TemplateGenerationService
from orb.config.schemas.server_schema import ServerConfig
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.di.container import get_container
from orb.interface.response_formatting_service import ResponseFormattingService

T = TypeVar("T")


def get_di_container():
    """Get the DI container instance."""
    return get_container()


def get_service(service_type: type[T]) -> T:
    """Get services from DI container."""

    def _get_service() -> T:
        container = get_di_container()
        return container.get(service_type)

    return _get_service  # type: ignore[return-value]


def get_query_bus() -> QueryBus:
    """Get QueryBus from DI container."""
    return get_di_container().get(QueryBus)


def get_command_bus() -> CommandBus:
    """Get CommandBus from DI container."""
    return get_di_container().get(CommandBus)


def get_scheduler_strategy() -> SchedulerPort:
    """Get SchedulerPort from DI container."""
    return get_di_container().get(SchedulerPort)


def get_config_manager() -> ConfigurationPort:
    """Get ConfigurationPort from DI container."""
    return get_di_container().get(ConfigurationPort)


def get_server_config() -> ServerConfig:
    """Get ServerConfig from configuration manager."""
    config_manager = get_config_manager()
    return config_manager.get_typed(ServerConfig)  # type: ignore[arg-type]


# Orchestrator dependencies
def get_acquire_machines_orchestrator() -> AcquireMachinesOrchestrator:
    """Get AcquireMachinesOrchestrator from DI container."""
    return get_di_container().get(AcquireMachinesOrchestrator)


def get_request_status_orchestrator() -> GetRequestStatusOrchestrator:
    """Get GetRequestStatusOrchestrator from DI container."""
    return get_di_container().get(GetRequestStatusOrchestrator)


def get_list_requests_orchestrator() -> ListRequestsOrchestrator:
    """Get ListRequestsOrchestrator from DI container."""
    return get_di_container().get(ListRequestsOrchestrator)


def get_return_machines_orchestrator() -> ReturnMachinesOrchestrator:
    """Get ReturnMachinesOrchestrator from DI container."""
    return get_di_container().get(ReturnMachinesOrchestrator)


def get_cancel_request_orchestrator() -> CancelRequestOrchestrator:
    """Get CancelRequestOrchestrator from DI container."""
    return get_di_container().get(CancelRequestOrchestrator)


def get_list_machines_orchestrator() -> ListMachinesOrchestrator:
    """Get ListMachinesOrchestrator from DI container."""
    return get_di_container().get(ListMachinesOrchestrator)


def get_machine_orchestrator() -> GetMachineOrchestrator:
    """Get GetMachineOrchestrator from DI container."""
    return get_di_container().get(GetMachineOrchestrator)


def get_sync_machine_orchestrator():
    """Get SyncMachineOrchestrator from DI container."""
    from orb.application.services.orchestration.sync_machine import SyncMachineOrchestrator

    return get_di_container().get(SyncMachineOrchestrator)


def get_list_templates_orchestrator() -> ListTemplatesOrchestrator:
    """Get ListTemplatesOrchestrator from DI container."""
    return get_di_container().get(ListTemplatesOrchestrator)


def get_list_return_requests_orchestrator() -> ListReturnRequestsOrchestrator:
    """Get ListReturnRequestsOrchestrator from DI container."""
    return get_di_container().get(ListReturnRequestsOrchestrator)


def get_get_template_orchestrator() -> GetTemplateOrchestrator:
    """Get GetTemplateOrchestrator from DI container."""
    return get_di_container().get(GetTemplateOrchestrator)


def get_create_template_orchestrator() -> CreateTemplateOrchestrator:
    """Get CreateTemplateOrchestrator from DI container."""
    return get_di_container().get(CreateTemplateOrchestrator)


def get_update_template_orchestrator() -> UpdateTemplateOrchestrator:
    """Get UpdateTemplateOrchestrator from DI container."""
    return get_di_container().get(UpdateTemplateOrchestrator)


def get_delete_template_orchestrator() -> DeleteTemplateOrchestrator:
    """Get DeleteTemplateOrchestrator from DI container."""
    return get_di_container().get(DeleteTemplateOrchestrator)


def get_validate_template_orchestrator() -> ValidateTemplateOrchestrator:
    """Get ValidateTemplateOrchestrator from DI container."""
    return get_di_container().get(ValidateTemplateOrchestrator)


def get_refresh_templates_orchestrator() -> RefreshTemplatesOrchestrator:
    """Get RefreshTemplatesOrchestrator from DI container."""
    return get_di_container().get(RefreshTemplatesOrchestrator)


def get_dashboard_summary_orchestrator() -> DashboardSummaryOrchestrator:
    """Get DashboardSummaryOrchestrator from DI container."""
    return get_di_container().get(DashboardSummaryOrchestrator)


def get_response_formatting_service() -> ResponseFormattingService:
    """Get ResponseFormattingService from DI container."""
    return get_di_container().get(ResponseFormattingService)


def _caller_has_operator_or_higher(request: Request) -> bool:
    """Return True when the authenticated caller holds at least the operator role.

    Reads the identity that AuthMiddleware already resolved onto request.state so
    this check is a pure dict lookup with no I/O.
    """
    raw_roles: list[str] = getattr(request.state, "user_roles", []) or []
    # Filter out the anonymous sentinel (mirrors the logic in get_current_user).
    meaningful = [r for r in raw_roles if r.lower() != "anonymous"]
    role = _resolve_role(meaningful) if meaningful else "viewer"
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK["operator"]


def get_request_formatter(
    request: Request,
    container=Depends(get_di_container),
) -> ResponseFormattingService:
    """Get ResponseFormattingService, optionally overridden by X-ORB-Scheduler header.

    The header is only honoured when the caller holds operator-or-higher privileges.
    Below-operator callers receive the default scheduler silently.
    """
    scheduler_override = request.headers.get("X-ORB-Scheduler")
    if scheduler_override:
        if not _caller_has_operator_or_higher(request):
            _deps_logger.debug(
                "X-ORB-Scheduler header ignored for sub-operator caller on %s",
                request.url.path,
            )
        else:
            from orb.infrastructure.scheduler.registry import get_scheduler_registry

            registry = get_scheduler_registry()
            if registry.is_registered(scheduler_override):
                try:
                    scheduler = registry.create_strategy(scheduler_override, container)
                    return ResponseFormattingService(scheduler)
                except Exception:
                    pass  # Fall through to default
    return container.get(ResponseFormattingService)


def get_request_scheduler(
    request: Request,
    container=Depends(get_di_container),
) -> SchedulerPort:
    """Get SchedulerPort, optionally overridden by X-ORB-Scheduler header.

    The header is only honoured when the caller holds operator-or-higher privileges.
    Below-operator callers receive the default scheduler silently.
    """
    scheduler_override = request.headers.get("X-ORB-Scheduler")
    if scheduler_override:
        if not _caller_has_operator_or_higher(request):
            _deps_logger.debug(
                "X-ORB-Scheduler header ignored for sub-operator caller on %s",
                request.url.path,
            )
        else:
            from orb.infrastructure.scheduler.registry import get_scheduler_registry

            registry = get_scheduler_registry()
            if registry.is_registered(scheduler_override):
                try:
                    return registry.create_strategy(scheduler_override, container)
                except Exception:
                    pass  # Fall through to default
    return container.get(SchedulerPort)


def get_template_generation_service() -> TemplateGenerationService:
    """Get TemplateGenerationService from DI container."""
    return get_di_container().get(TemplateGenerationService)


def get_health_check_port() -> Any:
    """Get HealthCheckPort from DI container."""
    from orb.domain.base.ports.health_check_port import HealthCheckPort

    return get_di_container().get(HealthCheckPort)


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

# Role rank used for "at least" comparisons.  Higher number = more privilege.
_ROLE_RANK: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}

# Permissions granted to each role (cumulative).
_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "viewer": ["read"],
    "operator": ["read", "request_machines", "return_machines", "cancel_request"],
    "admin": [
        "read",
        "request_machines",
        "return_machines",
        "cancel_request",
        "create_template",
        "update_template",
        "delete_template",
    ],
}


def _resolve_role(user_roles: list[str]) -> str:
    """
    Resolve the highest RBAC role from a list of raw role/group claims.

    Priority order (highest wins): admin > operator > viewer.

    Recognised values (case-insensitive):
      - "admin" / "orb-admin"   → admin
      - "operator" / "orb-operator" → operator
      - anything else           → viewer (least privilege)

    Args:
        user_roles: Raw roles/groups list from the JWT claim or AuthResult.

    Returns:
        One of "viewer", "operator", "admin".
    """
    _KNOWN_ROLES = frozenset({"admin", "orb-admin", "operator", "orb-operator"})
    best = "viewer"
    for raw in user_roles:
        lower = raw.lower()
        if lower in ("admin", "orb-admin"):
            return "admin"  # Can't do better; short-circuit.
        if lower in ("operator", "orb-operator"):
            best = "operator"
        elif lower not in _KNOWN_ROLES:
            # Warn about any claim value that does not match a known role token.
            # This fires when an IdP sends an unexpected group name, helping operators
            # catch misconfigured role mappings before they silently grant viewer access.
            _deps_logger.warning(
                "unknown role claim %r, defaulting to viewer; check IDP mappings",
                raw,
            )
    return best


@dataclass
class CurrentUser:
    """Lightweight representation of the authenticated caller."""

    username: str
    role: str  # One of "viewer", "operator", "admin"
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def permissions(self) -> list[str]:
        """Return the permission list for this user's role."""
        return _ROLE_PERMISSIONS.get(self.role, _ROLE_PERMISSIONS["viewer"])


def get_current_user(request: Request) -> CurrentUser:
    """
    FastAPI dependency that returns the authenticated caller.

    Reads identity from ``request.state`` populated by AuthMiddleware:
      - ``request.state.user_id``    → username
      - ``request.state.user_roles`` → raw roles list used to derive RBAC role
      - ``request.state.auth_result`` → full AuthResult (claims stored in metadata)

    When auth is disabled (no ``user_id`` on state), falls back to an
    anonymous viewer — least privilege, never admin.

    Returns:
        CurrentUser with username, role, and raw claims.
    """
    user_id: str | None = getattr(request.state, "user_id", None)

    if not user_id:
        # Auth disabled / excluded path — grant least privilege (viewer).
        # Never elevate an unauthenticated caller to admin.
        return CurrentUser(username="anonymous", role="viewer", claims={})

    raw_roles: list[str] = getattr(request.state, "user_roles", []) or []
    auth_result = getattr(request.state, "auth_result", None)
    claims: dict[str, Any] = {}
    if auth_result is not None:
        claims = getattr(auth_result, "metadata", {}) or {}

    # Filter out the sentinel "anonymous" value set by NoAuthStrategy; it does
    # not represent an authenticated claim so it must not be resolved to admin.
    meaningful_roles = [r for r in raw_roles if r.lower() != "anonymous"]

    # If no meaningful role claims arrive, default to least privilege.
    role = _resolve_role(meaningful_roles) if meaningful_roles else "viewer"

    return CurrentUser(username=user_id, role=role, claims=claims)


def check_destructive_admin_allowed(request: Request) -> None:
    """FastAPI Depends that gates destructive admin actions.

    Enforces three independent conditions (all must pass):

    1. Authentication must be enabled — an anonymous caller must never reach a
       destructive endpoint even if the flag below is set.
    2. ``allow_destructive_admin`` must be True in the application config.
    3. The active environment must not be ``production``.

    Reads config at call time so a restart is not required to disable the
    feature after it has been enabled.

    Raises:
        HTTPException(403): when auth is disabled, ``allow_destructive_admin``
            is false, or the active environment is ``production``.
    """
    from orb.infrastructure.logging.logger import get_logger as _get_logger

    _logger = _get_logger(__name__)
    _PRODUCTION_ENVIRONMENT = "production"

    container = get_di_container()

    # Guard 0: authentication must be enabled with a real strategy.
    #
    # Two conditions must both hold:
    #   (a) auth.enabled is True — an anonymous caller must never reach a
    #       destructive endpoint.
    #   (b) auth.strategy is not the "none" pass-through — NoAuthStrategy
    #       grants permissions=["*"] to every anonymous request, so even when
    #       enabled=True is set in config the server is effectively unprotected.
    #       Accepting "none" here would let any caller satisfy this guard while
    #       logging a misleading "auth enabled" message.
    _NO_REAL_AUTH_STRATEGIES = frozenset({"none", "", None})
    try:
        server_config = get_server_config()
        auth_cfg = server_config.auth
        if not auth_cfg.enabled:
            _logger.warning(
                "DESTRUCTIVE_ADMIN blocked: authentication is disabled; "
                "destructive operations require an authenticated identity"
            )
            raise HTTPException(  # type: ignore[misc]
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH_DISABLED",
                    "message": "Destructive admin requires authentication enabled.",
                },
            )
        if getattr(auth_cfg, "strategy", None) in _NO_REAL_AUTH_STRATEGIES:
            _logger.warning(
                "DESTRUCTIVE_ADMIN blocked: auth.strategy=%r is a pass-through "
                "that grants every anonymous caller full permissions; "
                "a real authentication strategy is required",
                getattr(auth_cfg, "strategy", None),
            )
            raise HTTPException(  # type: ignore[misc]
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH_STRATEGY_NONE",
                    "message": (
                        "Destructive admin requires a real authentication strategy. "
                        "The configured strategy enforces no access control."
                    ),
                },
            )
    except HTTPException:
        raise
    except Exception:
        # If we cannot determine auth config, fail closed.
        raise HTTPException(  # type: ignore[misc]
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONFIG_UNAVAILABLE",
                "message": "Could not verify server configuration; destructive action blocked.",
            },
        )

    # Guards 1 & 2: config flag + environment.
    try:
        from orb.domain.base.ports.configuration_port import ConfigurationPort

        config_port = container.get(ConfigurationPort)
        allow_destructive: bool = bool(
            config_port.get_configuration_value("allow_destructive_admin", False)
        )
        environment: str = str(
            config_port.get_configuration_value("environment", "production")
        ).lower()
    except Exception:
        # If we cannot read config, fail closed.
        allow_destructive = False
        environment = _PRODUCTION_ENVIRONMENT

    if environment == _PRODUCTION_ENVIRONMENT:
        _logger.warning("ADMIN_WIPE blocked: environment is '%s'", environment)
        raise HTTPException(  # type: ignore[misc]
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PRODUCTION_ENVIRONMENT",
                "message": "Destructive admin actions are never permitted in production environments.",
            },
        )

    if not allow_destructive:
        _logger.warning("ADMIN_WIPE blocked: allow_destructive_admin=False")
        raise HTTPException(  # type: ignore[misc]
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "DESTRUCTIVE_ADMIN_DISABLED",
                "message": (
                    "Destructive admin actions are disabled. "
                    "Set allow_destructive_admin=true in the application config to enable."
                ),
            },
        )


def require_role(min_role: str) -> Callable[..., CurrentUser]:
    """
    Factory that returns a FastAPI Depends enforcing a minimum RBAC role.

    Usage::

        @router.post("/templates")
        async def create_template(
            _user: CurrentUser = Depends(require_role("admin")),
            ...
        ):
            ...

    Args:
        min_role: Minimum required role — one of "viewer", "operator", "admin".

    Returns:
        A dependency callable that resolves to the CurrentUser or raises 403.

    Raises:
        HTTPException(403): When the caller's role ranks below ``min_role``.
        ValueError: When ``min_role`` is not a recognised role name.
    """
    if min_role not in _ROLE_RANK:
        raise ValueError(f"Unknown role '{min_role}'. Must be one of: {list(_ROLE_RANK)}")

    required_rank = _ROLE_RANK[min_role]

    def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if _ROLE_RANK.get(user.role, 0) < required_rank:
            raise HTTPException(  # type: ignore[misc]
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {min_role}.",
            )
        return user

    return _check
