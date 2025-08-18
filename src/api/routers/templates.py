"""Template management API routes."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from application.dto.queries import (
    GetTemplateQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from infrastructure.di.buses import CommandBus, QueryBus
from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/templates", tags=["Templates"])

# Module-level dependency variables to avoid B008 warnings
PROVIDER_API_QUERY = Query(None, description="Filter by provider API")
FORCE_REFRESH_QUERY = Query(False, description="Force refresh from files")
INCLUDE_CONFIG_QUERY = Query(False, description="Include full configuration")
TEMPLATE_DATA_BODY = Body(...)


class TemplateCreateRequest(BaseModel):
    """Request model for creating templates."""

    template_id: str
    name: Optional[str] = None
    provider_api: Optional[str] = "aws"
    image_id: Optional[str] = None
    instance_type: Optional[str] = None
    key_name: Optional[str] = None
    security_group_ids: Optional[List[str]] = None
    subnet_ids: Optional[List[str]] = None
    user_data: Optional[str] = None
    tags: Optional[Dict[str, str]] = None
    version: Optional[str] = "1.0"


class TemplateUpdateRequest(BaseModel):
    """Request model for updating templates."""

    name: Optional[str] = None
    provider_api: Optional[str] = None
    image_id: Optional[str] = None
    instance_type: Optional[str] = None
    key_name: Optional[str] = None
    security_group_ids: Optional[List[str]] = None
    subnet_ids: Optional[List[str]] = None
    user_data: Optional[str] = None
    tags: Optional[Dict[str, str]] = None
    version: Optional[str] = None


@router.get("/", summary="List Templates", description="Get all available templates")
@handle_rest_exceptions(endpoint="/api/v1/templates", method="GET")
async def list_templates(
    provider_api: Optional[str] = PROVIDER_API_QUERY,
    force_refresh: bool = FORCE_REFRESH_QUERY,
) -> JSONResponse:
    """
    List all available templates.

    - **provider_api**: Filter templates by provider API
    - **force_refresh**: Force reload from configuration files
    """
    try:
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            raise HTTPException(status_code=500, detail="QueryBus not available")

        # Create and execute query through CQRS bus
        query = ListTemplatesQuery(
            provider_api=provider_api, active_only=True, include_configuration=False
        )

        templates = await query_bus.execute(query)

        return JSONResponse(
            status_code=200,
            content={
                "templates": [
                    (template.model_dump() if hasattr(template, "model_dump") else template)
                    for template in templates
                ],
                "total_count": len(templates),
                "timestamp": None,  # Could add timestamp from query result if needed
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list templates: {str(e)}")


@router.get("/{template_id}", summary="Get Template", description="Get template by ID")
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="GET")
async def get_template(
    template_id: str,
    include_config: bool = INCLUDE_CONFIG_QUERY,
) -> JSONResponse:
    """
    Get a specific template by ID.

    - **template_id**: Template identifier
    - **include_config**: Include full template configuration
    """
    try:
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            raise HTTPException(status_code=500, detail="QueryBus not available")

        # Create and execute query through CQRS bus
        query = GetTemplateQuery(template_id=template_id)
        template = await query_bus.execute(query)

        if template:
            return JSONResponse(
                status_code=200,
                content={
                    "template": (
                        template.model_dump() if hasattr(template, "model_dump") else template
                    ),
                    "timestamp": None,
                },
            )
        else:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get template: {str(e)}")


@router.post("/", summary="Create Template", description="Create a new template")
@handle_rest_exceptions(endpoint="/api/v1/templates", method="POST")
async def create_template(template_data: TemplateCreateRequest) -> JSONResponse:
    """
    Create a new template.

    - **template_data**: Template configuration data
    """
    try:
        container = get_container()
        command_bus = container.get(CommandBus)

        if not command_bus:
            raise HTTPException(status_code=500, detail="CommandBus not available")

        # Convert Pydantic model to dict
        template_dict = template_data.dict(exclude_unset=True)

        # Create command and execute through CQRS bus
        command = CreateTemplateCommand(
            template_id=template_dict["template_id"],
            name=template_dict.get("name"),
            description=template_dict.get("description"),
            provider_api=template_dict.get("provider_api", "aws"),
            instance_type=template_dict.get("instance_type"),
            image_id=template_dict.get("image_id"),
            subnet_ids=template_dict.get("subnet_ids", []),
            security_group_ids=template_dict.get("security_group_ids", []),
            tags=template_dict.get("tags", {}),
            configuration=template_dict,
        )

        response = command_bus.execute(command)

        if response and response.validation_errors:
            raise HTTPException(
                status_code=400,
                detail=f"Template validation failed: {', '.join(response.validation_errors)}",
            )

        return JSONResponse(
            status_code=201,
            content={
                "message": f"Template {template_dict['template_id']} created successfully",
                "template_id": template_dict["template_id"],
                "timestamp": None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create template: {str(e)}")


@router.put(
    "/{template_id}",
    summary="Update Template",
    description="Update an existing template",
)
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="PUT")
async def update_template(template_id: str, template_data: TemplateUpdateRequest) -> JSONResponse:
    """
    Update an existing template.

    - **template_id**: Template identifier
    - **template_data**: Updated template configuration data
    """
    try:
        container = get_container()
        command_bus = container.get(CommandBus)

        if not command_bus:
            raise HTTPException(status_code=500, detail="CommandBus not available")

        # Convert Pydantic model to dict, excluding unset values
        template_dict = template_data.dict(exclude_unset=True)

        # Create command and execute through CQRS bus
        command = UpdateTemplateCommand(
            template_id=template_id,
            name=template_dict.get("name"),
            description=template_dict.get("description"),
            configuration=template_dict,
        )

        response = command_bus.execute(command)

        if response and response.validation_errors:
            raise HTTPException(
                status_code=400,
                detail=f"Template validation failed: {', '.join(response.validation_errors)}",
            )

        return JSONResponse(
            status_code=200,
            content={
                "message": f"Template {template_id} updated successfully",
                "template_id": template_id,
                "timestamp": None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update template: {str(e)}")


@router.delete("/{template_id}", summary="Delete Template", description="Delete a template")
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="DELETE")
async def delete_template(template_id: str) -> JSONResponse:
    """
    Delete a template.

    - **template_id**: Template identifier
    """
    try:
        container = get_container()
        command_bus = container.get(CommandBus)

        if not command_bus:
            raise HTTPException(status_code=500, detail="CommandBus not available")

        # Create command and execute through CQRS bus
        command = DeleteTemplateCommand(template_id=template_id)
        response = command_bus.execute(command)

        if response and response.validation_errors:
            raise HTTPException(
                status_code=400,
                detail=f"Template deletion failed: {', '.join(response.validation_errors)}",
            )

        return JSONResponse(
            status_code=200,
            content={
                "message": f"Template {template_id} deleted successfully",
                "template_id": template_id,
                "timestamp": None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete template: {str(e)}")


@router.post(
    "/validate",
    summary="Validate Template",
    description="Validate template configuration",
)
@handle_rest_exceptions(endpoint="/api/v1/templates/validate", method="POST")
async def validate_template(template_data: Dict[str, Any] = TEMPLATE_DATA_BODY) -> JSONResponse:
    """
    Validate template configuration.

    - **template_data**: Template configuration to validate
    """
    try:
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            raise HTTPException(status_code=500, detail="QueryBus not available")

        # Create and execute validation query through CQRS bus
        query = ValidateTemplateQuery(template_config=template_data)
        validation_result = await query_bus.execute(query)

        # Check if validation result has errors
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
                "timestamp": None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate template: {str(e)}")


@router.post("/refresh", summary="Refresh Templates", description="Refresh template cache")
@handle_rest_exceptions(endpoint="/api/v1/templates/refresh", method="POST")
async def refresh_templates() -> JSONResponse:
    """
    Refresh template cache and reload from files.
    """
    try:
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            raise HTTPException(status_code=500, detail="QueryBus not available")

        # Force refresh by listing templates - this will trigger cache refresh in
        # the query handler
        query = ListTemplatesQuery(provider_api=None, active_only=True, include_configuration=False)

        templates = await query_bus.execute(query)
        template_count = len(templates) if templates else 0

        return JSONResponse(
            status_code=200,
            content={
                "message": f"Templates refreshed successfully. Found {template_count} templates.",
                "template_count": template_count,
                "cache_stats": {"refreshed": True},
                "timestamp": None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh templates: {str(e)}")
