"""Template management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Body, Depends, HTTPException, Query
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import get_command_bus, get_query_bus, get_scheduler_strategy
from orb.api.models.base import APIRequest
from orb.application.dto.queries import GetTemplateQuery, ListTemplatesQuery, ValidateTemplateQuery
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/templates", tags=["Templates"])

# Module-level dependency variables to avoid B008 warnings
QUERY_BUS = Depends(get_query_bus)
COMMAND_BUS = Depends(get_command_bus)
SCHEDULER_STRATEGY = Depends(get_scheduler_strategy)
PROVIDER_API_QUERY = Query(None, description="Filter by provider API")
TEMPLATE_DATA_BODY = Body(...)


class TemplateCreateRequest(APIRequest):
    """Request model for creating templates.

    Accepts both camelCase and snake_case field names.
    """

    template_id: str
    name: Optional[str] = None
    provider_api: Optional[str] = "aws"
    image_id: Optional[str] = None
    instance_type: Optional[str] = None
    key_name: Optional[str] = None
    security_group_ids: Optional[list[str]] = None
    subnet_ids: Optional[list[str]] = None
    user_data: Optional[str] = None
    tags: Optional[dict[str, str]] = None
    version: Optional[str] = "1.0"


class TemplateUpdateRequest(APIRequest):
    """Request model for updating templates.

    Accepts both camelCase and snake_case field names.
    """

    name: Optional[str] = None
    provider_api: Optional[str] = None
    image_id: Optional[str] = None
    instance_type: Optional[str] = None
    key_name: Optional[str] = None
    security_group_ids: Optional[list[str]] = None
    subnet_ids: Optional[list[str]] = None
    user_data: Optional[str] = None
    tags: Optional[dict[str, str]] = None
    version: Optional[str] = None


@router.get("/", summary="List Templates", description="Get all available templates")
@handle_rest_exceptions(endpoint="/api/v1/templates", method="GET")
async def list_templates(
    provider_api: Optional[str] = PROVIDER_API_QUERY,
    query_bus=QUERY_BUS,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    List all available templates.

    - **provider_api**: Filter templates by provider API
    """
    try:
        query = ListTemplatesQuery(provider_api=provider_api, active_only=True)
        templates = await query_bus.execute(query)
        return JSONResponse(
            status_code=200,
            content=scheduler.format_templates_response(templates),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list templates: {e!s}")


@router.get("/{template_id}", summary="Get Template", description="Get template by ID")
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="GET")
async def get_template(
    template_id: str,
    query_bus=QUERY_BUS,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Get a specific template by ID.

    - **template_id**: Template identifier
    """
    try:
        query = GetTemplateQuery(template_id=template_id)
        template = await query_bus.execute(query)

        if template:
            return JSONResponse(
                status_code=200,
                content={"template": scheduler.format_template_for_display(template)},
            )
        else:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get template: {e!s}")


@router.post("/", summary="Create Template", description="Create a new template")
@handle_rest_exceptions(endpoint="/api/v1/templates", method="POST")
async def create_template(
    template_data: TemplateCreateRequest, command_bus=COMMAND_BUS
) -> JSONResponse:
    """
    Create a new template.

    - **template_data**: Template configuration data
    """
    try:
        template_dict = template_data.model_dump(exclude_unset=True)

        command = CreateTemplateCommand(
            template_id=template_dict["template_id"],
            name=template_dict.get("name"),
            description=template_dict.get("description"),
            provider_api=template_dict.get("provider_api", "aws"),
            instance_type=template_dict.get("instance_type"),
            image_id=template_dict.get("image_id") or "",
            tags=template_dict.get("tags", {}),
            configuration=template_dict,
        )

        await command_bus.execute(command)

        return JSONResponse(
            status_code=201,
            content={
                "template_id": template_dict["template_id"],
                "status": "created",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create template: {e!s}")


@router.put(
    "/{template_id}",
    summary="Update Template",
    description="Update an existing template",
)
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="PUT")
async def update_template(
    template_id: str, template_data: TemplateUpdateRequest, command_bus=COMMAND_BUS
) -> JSONResponse:
    """
    Update an existing template.

    - **template_id**: Template identifier
    - **template_data**: Updated template configuration data
    """
    try:
        template_dict = template_data.model_dump(exclude_unset=True)

        command = UpdateTemplateCommand(
            template_id=template_id,
            name=template_dict.get("name"),
            description=template_dict.get("description"),
            configuration=template_dict,
        )

        await command_bus.execute(command)

        return JSONResponse(
            status_code=200,
            content={
                "template_id": template_id,
                "status": "updated",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update template: {e!s}")


@router.delete("/{template_id}", summary="Delete Template", description="Delete a template")
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="DELETE")
async def delete_template(template_id: str, command_bus=COMMAND_BUS) -> JSONResponse:
    """
    Delete a template.

    - **template_id**: Template identifier
    """
    try:
        command = DeleteTemplateCommand(template_id=template_id)
        await command_bus.execute(command)

        return JSONResponse(
            status_code=200,
            content={
                "template_id": template_id,
                "status": "deleted",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete template: {e!s}")


@router.post(
    "/validate",
    summary="Validate Template",
    description="Validate template configuration",
)
@handle_rest_exceptions(endpoint="/api/v1/templates/validate", method="POST")
async def validate_template(
    template_data: dict[str, Any] = TEMPLATE_DATA_BODY,
    query_bus=QUERY_BUS,
) -> JSONResponse:
    """
    Validate template configuration.

    - **template_data**: Template configuration to validate
    """
    try:
        query = ValidateTemplateQuery(template_config=template_data)
        validation_result = await query_bus.execute(query)

        is_valid = not validation_result.errors if hasattr(validation_result, "errors") else True

        return JSONResponse(
            status_code=200,
            content={
                "valid": is_valid,
                "template_id": template_data.get("template_id", "validation-template"),
                "validation_errors": (
                    validation_result.errors if hasattr(validation_result, "errors") else []
                ),
                "validation_warnings": (
                    validation_result.warnings if hasattr(validation_result, "warnings") else []
                ),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate template: {e!s}")


@router.post("/refresh", summary="Refresh Templates", description="Refresh template cache")
@handle_rest_exceptions(endpoint="/api/v1/templates/refresh", method="POST")
async def refresh_templates(query_bus=QUERY_BUS, scheduler=SCHEDULER_STRATEGY) -> JSONResponse:
    """
    Refresh template cache and reload from files.
    """
    try:
        query = ListTemplatesQuery(provider_api=None, active_only=True)
        templates = await query_bus.execute(query)
        return JSONResponse(
            status_code=200,
            content=scheduler.format_templates_response(templates),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh templates: {e!s}")
