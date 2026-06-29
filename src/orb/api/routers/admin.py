"""Admin API routes — destructive administrative actions.

SECURITY NOTICE
---------------
This router exposes irreversible operations.  Every endpoint is protected by
two independent runtime guards (checked on every call, not just at startup):

  1. Config guard — ``app.allow_destructive_admin`` must be True.
     Defaults to False; must be explicitly set to True in the environment's
     config file.

  2. Environment guard — the active environment must NOT be "production".
     Even if someone accidentally sets allow_destructive_admin=true in prod,
     the production guard blocks execution.

Both guards returning 403 with a descriptive error body so callers can
distinguish "feature disabled" from "wrong environment".

The wipe endpoint additionally requires the caller to echo the string "WIPE"
in the request body — a confirmation token that prevents accidents from
simple HTTP replays or misconfigured automation.

The init endpoint requires the caller to echo "INIT" and creates default
config + data directories so ORB is usable from a wiped state.
"""

from __future__ import annotations

import shutil
from typing import Annotated, Any, Optional

try:
    from fastapi import APIRouter, Body, Depends, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import check_destructive_admin_allowed, get_di_container, require_role
from orb.application.services.admin.cleanup_database import (
    CleanupDatabaseService,
    InvalidCleanupStatusError,
)
from orb.application.services.admin.wipe_database import WipeDatabaseService
from orb.domain.base import UnitOfWorkFactory
from orb.infrastructure.logging.logger import get_logger

router = APIRouter(prefix="/admin", tags=["Admin"])

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _forbidden(code: str, message: str):
    """Return a JSONResponse-wrapped HTTPException with 403."""
    from fastapi import HTTPException

    raise HTTPException(
        status_code=403,
        detail={"code": code, "message": message},
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/database/wipe",
    summary="Wipe all ORB database tables",
    description=(
        "Truncate all ORB data tables (machines, requests, templates). "
        "Requires `allow_destructive_admin=true` in config AND environment != production. "
        "Body must contain `confirm: 'WIPE'` to prevent accidents."
    ),
    responses={
        200: {"description": "All tables truncated successfully."},
        400: {"description": "Confirmation token missing or incorrect."},
        403: {"description": "Feature disabled or production environment."},
    },
)
async def wipe_database(
    request: Request,
    body: Annotated[dict[str, Any], Body()] = None,  # type: ignore[assignment]
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Truncate all ORB data tables.

    Security guards (both enforced on every call):
    - ``allow_destructive_admin`` must be True in application config.
    - Active environment must not be ``production``.

    Body parameter:
    - ``confirm`` (str, required): must equal the exact string ``"WIPE"``.
    """
    # ── Guard 1 & 2: config flag + environment ──────────────────────────────
    check_destructive_admin_allowed(request)

    # ── Guard 3: explicit confirmation token ────────────────────────────────
    if body is None:
        body = {}
    confirm = body.get("confirm", "")
    if confirm != "WIPE":
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "MISSING_CONFIRMATION",
                    "message": (
                        'Confirmation token required. Send {"confirm": "WIPE"} in the request body.'
                    ),
                },
            },
        )

    # ── Resolve service and execute ─────────────────────────────────────────
    container = get_di_container()
    service = WipeDatabaseService(
        # Use UnitOfWorkFactory so the wipe hits the SAME storage
        # instance/buckets the read/write paths use. Singleton repos
        # target a separate ``generic`` JSON bucket on single-file storage
        # and would silently no-op.
        uow_factory=container.get(UnitOfWorkFactory),
    )

    # Capture caller identity for audit trail (AuditLogMiddleware also logs
    # this request automatically, but we add an explicit WARNING entry so the
    # event is unambiguous in the application log, separate from HTTP access).
    caller_ip = request.client.host if request.client else "unknown"
    caller_id = getattr(request.state, "user_id", "anonymous") or "anonymous"
    logger.warning("DATABASE_WIPED by user=%s ip=%s", caller_id, caller_ip)

    result = service.execute()

    return JSONResponse(
        status_code=200,
        content={
            "wiped": True,
            "tables_truncated": result.tables_truncated,
            "rows_deleted": result.rows_deleted,
        },
    )


# ---------------------------------------------------------------------------
# Init body model
# ---------------------------------------------------------------------------


class InitBody(BaseModel):
    force: bool = False
    generate_templates: bool = True
    confirm: str = ""


class CleanupDatabaseBody(BaseModel):
    """Body for POST /admin/database/cleanup."""

    confirm: str = ""
    older_than_days: Optional[int] = None
    request_statuses: list[str] = []
    include_machines: bool = True


# ---------------------------------------------------------------------------
# Init endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/init",
    summary="Initialize ORB",
    description=(
        "Create default config file, data directories, and optionally refresh templates. "
        "Requires `allow_destructive_admin=true` in config AND environment != production. "
        "Body must contain `confirm: 'INIT'` to prevent accidents."
    ),
    responses={
        200: {"description": "ORB initialized successfully."},
        400: {"description": "Confirmation token missing or incorrect."},
        403: {"description": "Feature disabled or production environment."},
    },
)
async def init_orb(
    request: Request,
    body: InitBody = Body(default_factory=InitBody),
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Initialize ORB: ensure config file and data directories exist.

    Security guards (both enforced on every call):
    - ``allow_destructive_admin`` must be True in application config.
    - Active environment must not be ``production``.

    Body parameters:
    - ``confirm`` (str, required): must equal the exact string ``"INIT"``.
    - ``force`` (bool, default False): overwrite existing config if present.
    - ``generate_templates`` (bool, default True): trigger a template refresh
      after directory/config setup.
    """
    # ── Guard 1 & 2: config flag + environment ──────────────────────────────
    check_destructive_admin_allowed(request)

    # ── Guard 3: explicit confirmation token ────────────────────────────────
    if body.confirm != "INIT":
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "MISSING_CONFIRMATION",
                    "message": (
                        'Confirmation token required. Send {"confirm": "INIT"} in the request body.'
                    ),
                },
            },
        )

    caller_ip = request.client.host if request.client else "unknown"
    caller_id = getattr(request.state, "user_id", "anonymous") or "anonymous"
    logger.warning("ORB_INIT called by user=%s ip=%s force=%s", caller_id, caller_ip, body.force)

    created_files: list[str] = []
    created_dirs: list[str] = []
    templates_count = 0

    try:
        from orb.config.platform_dirs import (
            get_config_location,
            get_logs_location,
            get_scripts_location,
            get_work_location,
        )

        config_dir = get_config_location()
        work_dir = get_work_location()
        logs_dir = get_logs_location()
        scripts_dir = get_scripts_location()

        # ── Create directories ───────────────────────────────────────────────
        for d in [config_dir, work_dir, work_dir / ".cache", logs_dir]:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created_dirs.append(str(d))

        # ── Ensure config file ───────────────────────────────────────────────
        config_file = config_dir / "config.json"
        if not config_file.exists() or body.force:
            # Copy default_config.json shipped with the package
            from pathlib import Path

            default_src = Path(__file__).resolve().parents[3] / "config" / "default_config.json"
            if default_src.exists():
                shutil.copy2(default_src, config_file)
            else:
                # Fallback: write a minimal valid config
                import json

                minimal: dict[str, Any] = {
                    "scheduler": {"type": "default"},
                    "provider": {"providers": []},
                    "environment": "development",
                    "allow_destructive_admin": False,
                }
                config_file.write_text(json.dumps(minimal, indent=2))
            created_files.append(str(config_file))

        # ── Copy platform scripts (best-effort) ──────────────────────────────
        try:
            from orb.interface.init_command_handler import _copy_scripts

            _copy_scripts(scripts_dir)
        except Exception as e:
            logger.debug("Script copy skipped: %s", e)

        # ── Optionally refresh templates ─────────────────────────────────────
        if body.generate_templates:
            try:
                from orb.application.services.orchestration.dtos import RefreshTemplatesInput
                from orb.application.services.orchestration.refresh_templates import (
                    RefreshTemplatesOrchestrator,
                )

                container = get_di_container()
                orchestrator = container.get(RefreshTemplatesOrchestrator)
                result = await orchestrator.execute(RefreshTemplatesInput())
                templates_count = len(result.templates)
            except Exception as e:
                logger.warning("Template refresh during init skipped: %s", e)

    except Exception as exc:
        logger.error("ORB init failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INIT_FAILED",
                    "message": str(exc),
                },
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "initialized": True,
            "created_files": created_files,
            "created_dirs": created_dirs,
            "templates_generated": templates_count,
        },
    )


# ---------------------------------------------------------------------------
# Cleanup endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/database/cleanup",
    summary="Bulk cleanup terminal request and machine rows",
    description=(
        "Hard-delete requests (and optionally their machine rows) that match the "
        "given status filter and optional age filter. "
        "Requires `allow_destructive_admin=true` in config AND environment != production. "
        "Body must contain `confirm: 'CLEANUP'` and a non-empty `request_statuses` list "
        "containing only terminal statuses."
    ),
    responses={
        200: {"description": "Cleanup completed."},
        400: {"description": "Bad confirmation token or invalid status list."},
        403: {"description": "Feature disabled or production environment."},
    },
)
async def cleanup_database(
    request: Request,
    body: CleanupDatabaseBody = Body(default_factory=CleanupDatabaseBody),
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Bulk-delete terminal request rows (and optionally their machine rows).

    Security guards (both enforced on every call):
    - ``allow_destructive_admin`` must be True in application config.
    - Active environment must not be ``production``.

    Body parameters:
    - ``confirm`` (str, required): must equal the exact string ``"CLEANUP"``.
    - ``request_statuses`` (list[str], required): terminal statuses to target.
    - ``older_than_days`` (int, optional): only delete rows older than N days.
    - ``include_machines`` (bool, default True): cascade-delete machine rows.
    """
    # ── Guard 1 & 2: config flag + environment ──────────────────────────────
    check_destructive_admin_allowed(request)

    # ── Guard 3: explicit confirmation token ────────────────────────────────
    if body.confirm != "CLEANUP":
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "MISSING_CONFIRMATION",
                    "message": (
                        "Confirmation token required. "
                        'Send {"confirm": "CLEANUP"} in the request body.'
                    ),
                },
            },
        )

    # ── Guard 4: status list validation ─────────────────────────────────────
    if not body.request_statuses:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {
                    "code": "EMPTY_STATUS_LIST",
                    "message": "request_statuses must contain at least one terminal status.",
                },
            },
        )

    # ── Resolve service and execute ─────────────────────────────────────────
    caller_ip = request.client.host if request.client else "unknown"
    caller_id = getattr(request.state, "user_id", "anonymous") or "anonymous"

    container = get_di_container()
    service = CleanupDatabaseService(uow_factory=container.get(UnitOfWorkFactory))

    try:
        result = service.bulk_cleanup(
            statuses=body.request_statuses,
            older_than_days=body.older_than_days,
            include_machines=body.include_machines,
            caller_id=caller_id,
        )
    except InvalidCleanupStatusError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": {"code": "INVALID_STATUS", "message": str(exc)},
            },
        )

    logger.warning(
        "ADMIN_CLEANUP by user=%s ip=%s requests_deleted=%d machines_deleted=%d",
        caller_id,
        caller_ip,
        result.requests_deleted,
        result.machines_deleted,
    )

    return JSONResponse(
        status_code=200,
        content={
            "cleaned": True,
            "requests_deleted": result.requests_deleted,
            "machines_deleted": result.machines_deleted,
        },
    )


# ---------------------------------------------------------------------------
# Reload — non-destructive, admin role required, no destructive-admin guard
#
# Config reload reads config.json from disk and replaces the in-memory cache.
# No data is modified or deleted, so the allow_destructive_admin flag is NOT
# consulted.  The admin role requirement ensures only authorised callers can
# trigger a reload.
# ---------------------------------------------------------------------------


@router.post(
    "/reload-config",
    summary="Reload ORB configuration from disk",
    description=(
        "Invalidate the in-memory configuration cache and re-read "
        "``config.json`` (and any source the ConfigurationManager loader "
        "knows about). Provider strategies are re-resolved on next access. "
        "Used by ``orb server reload`` — operators can edit config and pick "
        "up the changes without bouncing the daemon. Python code changes "
        "are NOT covered; use ``orb server start --reload`` for that."
    ),
    responses={
        200: {"description": "Configuration cache invalidated."},
        500: {"description": "Reload failed; previous config still active."},
    },
)
async def reload_config(request: Request, _user=Depends(require_role("admin"))) -> JSONResponse:
    """Force ConfigurationManager.reload() on the live DI container.

    This endpoint is non-destructive: it reads from disk and updates the
    in-memory config cache only.  No data is deleted or modified, so the
    ``allow_destructive_admin`` guard is intentionally absent.
    """
    caller_ip = request.client.host if request.client else "unknown"
    try:
        from orb.config.managers.configuration_manager import ConfigurationManager

        container = get_di_container()
        cm = container.get(ConfigurationManager)
        cm.reload()
        logger.warning("ADMIN_RELOAD_CONFIG by ip=%s", caller_ip)
        return JSONResponse(
            status_code=200,
            content={"reloaded": True, "message": "Configuration reloaded from disk."},
        )
    except Exception as exc:
        logger.error("Config reload failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "reloaded": False,
                "error": {"code": "RELOAD_FAILED", "message": str(exc)},
            },
        )
