"""Configuration management API routes."""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    check_destructive_admin_allowed as _check_destructive_admin_allowed,
    get_config_manager,
    require_role,
)

router = APIRouter(prefix="/config", tags=["Configuration"])

CONFIG_MANAGER = Depends(get_config_manager)

_NOT_FOUND_SENTINEL = object()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SetValueRequest(BaseModel):
    value: Any


class SetValueResponse(BaseModel):
    key: str
    value: Any
    persisted: bool = False
    note: str = (
        "Set in memory only. Call POST /config/save to persist to the loaded "
        "config file, or POST /admin/reload-config to revert to disk."
    )


class SaveRequest(BaseModel):
    path: str | None = None  # optional override; default = loaded config file


class SaveResponse(BaseModel):
    persisted: bool
    path: str


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    summary="Get full effective configuration",
    description=(
        "Returns the full effective configuration tree as a dict. "
        "Pass ``?source=file`` to get the raw on-disk dict before Pydantic hydration."
    ),
)
async def get_full_config(
    source: str | None = Query(
        default=None, description="Use 'file' to return raw on-disk config."
    ),
    config_manager=CONFIG_MANAGER,
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Return the full effective configuration (default) or the raw file config (?source=file)."""
    if source == "file":
        # Return the raw dict read from disk before Pydantic hydration.
        try:
            raw: dict[str, Any] = config_manager.get_raw_config()
            return JSONResponse(content=dict(raw), status_code=200)
        except AttributeError:
            # Fallback: re-read from disk directly via the loaded file path.
            import json as _json

            config_file: str | None = None
            try:
                config_file = config_manager.get_loaded_config_file()  # type: ignore[attr-defined]
            except AttributeError as exc:
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    "config_manager lacks get_loaded_config_file (%s); "
                    "falling back to bundled default config",
                    exc,
                )
            if not config_file:
                # Final fallback: use the bundled default config.
                import os as _os

                config_file = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "config",
                    "default_config.json",
                )
            try:
                _loop = asyncio.get_running_loop()
                raw_bytes = await _loop.run_in_executor(
                    None, functools.partial(Path(config_file).read_bytes)
                )
                return JSONResponse(content=_json.loads(raw_bytes), status_code=200)
            except Exception:
                return JSONResponse(content={}, status_code=200)

    # Default: effective (Pydantic-hydrated) config.
    try:
        # ConfigurationAdapter.get_app_config() calls app_config.model_dump()
        full_config: dict[str, Any] = config_manager.get_app_config()
    except AttributeError:
        # Fallback for implementations that don't have get_app_config
        full_config = {}
    return JSONResponse(content=full_config, status_code=200)


@router.get(
    "/sources",
    summary="Get configuration source files",
    description="Returns the list of configuration source file paths.",
)
async def get_config_sources(
    config_manager=CONFIG_MANAGER,
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Return configuration source information."""
    sources: dict[str, Any] = config_manager.get_configuration_sources()
    return JSONResponse(content=sources, status_code=200)


@router.post(
    "/save",
    summary="Save current in-memory configuration to disk",
    description=(
        "Persists the current raw configuration dict to the loaded config file "
        "(or a path supplied in the body). Use after PUT /{key} edits to make "
        "the changes survive a server restart."
    ),
    responses={
        200: {"description": "Configuration written to disk."},
        400: {"description": "No config file path resolved."},
        403: {"description": "Destructive-admin guard rejected the call."},
    },
)
async def save_config(
    request: Request,
    body: SaveRequest | None = None,
    _user=Depends(require_role("admin")),
    config_manager=CONFIG_MANAGER,
) -> JSONResponse:
    """Write the in-memory raw config to disk."""
    _check_destructive_admin_allowed(request)
    target_path: str | None = body.path if body and body.path else None
    resolved_save_path: str | None = None
    if target_path is not None:
        try:
            config_root = Path(config_manager.get_config_dir()).resolve()
            resolved_target = Path(target_path).resolve()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_PATH",
                    "message": "invalid path",
                },
            )
        if not resolved_target.is_relative_to(config_root):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PATH_OUTSIDE_CONFIG_DIR",
                    "message": "path outside config directory",
                },
            )
        resolved_save_path = str(resolved_target)
    try:
        written_to = config_manager.save_config(resolved_save_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_CONFIG_PATH", "message": str(exc)},
        ) from exc
    return JSONResponse(
        content=SaveResponse(persisted=True, path=written_to).model_dump(),
        status_code=200,
    )


@router.post(
    "/validate",
    summary="Validate current configuration",
    description="Validates the current in-memory configuration and returns any errors.",
)
async def validate_config(
    config_manager=CONFIG_MANAGER,
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Validate the current configuration and return errors."""
    errors: list[Any] = config_manager.validate_configuration()
    return JSONResponse(
        content=ValidateResponse(valid=not errors, errors=errors).model_dump(),
        status_code=200,
    )


@router.get(
    "/{key:path}",
    summary="Get a single configuration value",
    description=(
        "Returns a single configuration value by dot-notation key "
        "(e.g. ``server.port``). Returns 404 if the key is not present."
    ),
)
async def get_config_value(
    key: str,
    config_manager=CONFIG_MANAGER,
    _user=Depends(require_role("admin")),
) -> JSONResponse:
    """Return a single config value by dot-notation key."""
    value = config_manager.get_configuration_value(key, _NOT_FOUND_SENTINEL)
    if value is _NOT_FOUND_SENTINEL:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CONFIG_KEY_NOT_FOUND",
                "message": f"Configuration key '{key}' not found.",
            },
        )
    return JSONResponse(content={"key": key, "value": value}, status_code=200)


@router.put(
    "/{key:path}",
    summary="Set a configuration value (in-memory only)",
    description=(
        "Sets a configuration value in memory. The change is **not** persisted to disk. "
        "Reloading from file will revert this change. "
        "Blocked when the server is in read-only mode."
    ),
    responses={
        200: {"description": "Value set successfully (in-memory)."},
        400: {"description": "Missing or malformed request body."},
    },
)
async def set_config_value(
    key: str,
    body: SetValueRequest,
    _user=Depends(require_role("admin")),
    config_manager=CONFIG_MANAGER,
) -> JSONResponse:
    """Set a configuration value in memory and return the new value with a persistence warning."""
    config_manager.set_configuration_value(key, body.value)
    # Read back the value to confirm it was applied
    new_value = config_manager.get_configuration_value(key, body.value)
    response = SetValueResponse(key=key, value=new_value)
    return JSONResponse(content=response.model_dump(), status_code=200)
