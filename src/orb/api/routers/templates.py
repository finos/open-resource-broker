"""Template management API routes."""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Body, Depends, Query
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import (
    require_role,
    get_create_template_orchestrator,
    get_delete_template_orchestrator,
    get_get_template_orchestrator,
    get_list_templates_orchestrator,
    get_refresh_templates_orchestrator,
    get_request_scheduler,
    get_template_generation_service,
    get_update_template_orchestrator,
    get_validate_template_orchestrator,
)
from orb.api.models.base import APIRequest
from orb.api.models.responses import (
    GenerateTemplatesBody,
    TemplateListResponse,
    TemplateMutationResponse,
)
from orb.application.dto.template_generation_dto import TemplateGenerationRequest
from orb.application.services.template_generation_service import TemplateGenerationService
from orb.application.services.orchestration.dtos import (
    CreateTemplateInput,
    DeleteTemplateInput,
    GetTemplateInput,
    ListTemplatesInput,
    RefreshTemplatesInput,
    UpdateTemplateInput,
    ValidateTemplateInput,
)
from orb.domain.base.exceptions import EntityNotFoundError
from orb.infrastructure.error.decorators import handle_rest_exceptions

router = APIRouter(prefix="/templates", tags=["Templates"])

# Module-level dependency variables to avoid B008 warnings
LIST_ORCHESTRATOR = Depends(get_list_templates_orchestrator)
GET_ORCHESTRATOR = Depends(get_get_template_orchestrator)
CREATE_ORCHESTRATOR = Depends(get_create_template_orchestrator)
UPDATE_ORCHESTRATOR = Depends(get_update_template_orchestrator)
DELETE_ORCHESTRATOR = Depends(get_delete_template_orchestrator)
VALIDATE_ORCHESTRATOR = Depends(get_validate_template_orchestrator)
REFRESH_ORCHESTRATOR = Depends(get_refresh_templates_orchestrator)
SCHEDULER_STRATEGY = Depends(get_request_scheduler)
TEMPLATE_GENERATION_SERVICE = Depends(get_template_generation_service)
PROVIDER_API_QUERY = Query(None, description="Filter by provider API")
TEMPLATE_DATA_BODY = Body(...)


class TemplateCreateRequest(APIRequest):
    """Request model for creating templates.

    Accepts both camelCase and snake_case field names.
    """

    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    provider_api: Optional[str] = None
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
    description: Optional[str] = None
    provider_api: Optional[str] = None
    image_id: Optional[str] = None
    instance_type: Optional[str] = None
    key_name: Optional[str] = None
    security_group_ids: Optional[list[str]] = None
    subnet_ids: Optional[list[str]] = None
    user_data: Optional[str] = None
    tags: Optional[dict[str, str]] = None
    version: Optional[str] = None


@router.get(
    "/",
    summary="List Templates",
    description="Get all available templates",
    response_model=TemplateListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates", method="GET")
async def list_templates(
    provider_api: Optional[str] = PROVIDER_API_QUERY,
    limit: int = Query(50, description="Limit number of results"),
    offset: int = Query(0, description="Number of results to skip"),
    cursor: Optional[str] = Query(
        None, description="Opaque pagination cursor (preferred over offset)"
    ),
    q: Optional[str] = Query(None, description="Case-insensitive substring search"),
    sort: Optional[str] = Query(None, description='Sort: "field" or "-field" (desc)'),
    orchestrator=LIST_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """List all available templates with server-side filter/sort/pagination."""
    result = await orchestrator.execute(
        ListTemplatesInput(
            active_only=True,
            provider_api=provider_api,
            limit=limit,
            offset=offset,
            cursor=cursor,
            q=q,
            sort=sort,
        )
    )
    payload = scheduler.format_templates_response(result.templates)
    # The scheduler formatter does not carry pagination metadata, so the
    # orchestrator's total_count and next_cursor are overlaid on the
    # response body. total_count falls back to the page size when the
    # orchestrator does not provide it.
    if isinstance(payload, dict):
        payload = {
            **payload,
            "total_count": (
                result.total_count if result.total_count is not None else len(result.templates)
            ),
            "next_cursor": result.next_cursor,
        }
    return JSONResponse(status_code=200, content=payload)


@router.post(
    "/validate",
    summary="Validate Template",
    description="Validate template configuration",
    response_model=TemplateMutationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates/validate", method="POST")
async def validate_template(
    template_data: dict[str, Any] = TEMPLATE_DATA_BODY,
    orchestrator=VALIDATE_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Validate template configuration.

    - **template_data**: Template configuration to validate
    """
    result = await orchestrator.execute(
        ValidateTemplateInput(
            template_id=template_data.get("template_id"),
            config=template_data,
        )
    )
    return JSONResponse(
        status_code=200,
        content=scheduler.format_template_mutation_response(
            {
                "template_id": result.template_id,
                "status": "validated",
                "valid": result.valid,
                "validation_errors": result.errors,
                "message": result.message,
            }
        ),
    )


@router.post(
    "/refresh",
    summary="Refresh Templates",
    description="Refresh template cache",
    response_model=TemplateListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates/refresh", method="POST")
async def refresh_templates(
    _user=Depends(require_role("admin")),
    orchestrator=REFRESH_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Refresh template cache and reload from files.
    """
    result = await orchestrator.execute(RefreshTemplatesInput())
    return JSONResponse(
        status_code=200,
        content=scheduler.format_templates_response(result.templates),
    )


@router.post(
    "/generate",
    summary="Generate example templates",
    description="Generate example templates per provider (idempotent unless force=true)",
)
@handle_rest_exceptions(endpoint="/api/v1/templates/generate", method="POST")
async def generate_templates(
    body: GenerateTemplatesBody,
    _user=Depends(require_role("admin")),
    service: TemplateGenerationService = TEMPLATE_GENERATION_SERVICE,
) -> JSONResponse:
    """
    Generate example templates for one or all providers.

    - **body**: Generation options (provider selection, force overwrite, etc.)
    """
    request = TemplateGenerationRequest(
        specific_provider=body.provider,
        all_providers=body.all_providers,
        provider_api=body.provider_api,
        provider_specific=body.provider_specific,
        provider_type_filter=body.provider_type,
        force_overwrite=body.force,
    )
    result = await service.generate_templates(request)
    return JSONResponse(
        status_code=200,
        content={
            "status": result.status,
            "message": result.message,
            "total_templates": result.total_templates,
            "created_count": result.created_count,
            "skipped_count": result.skipped_count,
            "providers": [
                {
                    "provider": p.provider,
                    "filename": p.filename,
                    "templates_count": p.templates_count,
                    "status": p.status,
                    "reason": p.reason,
                }
                for p in result.providers
            ],
        },
    )


@router.get(
    "/{template_id}",
    summary="Get Template",
    description="Get template by ID",
    response_model=TemplateListResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="GET")
async def get_template(
    template_id: str,
    orchestrator=GET_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Get a specific template by ID.

    - **template_id**: Template identifier
    """
    result = await orchestrator.execute(GetTemplateInput(template_id=template_id))
    if not result.template:
        raise EntityNotFoundError("Template", template_id)
    return JSONResponse(
        status_code=200,
        content=scheduler.format_template_for_display(result.template),
    )


@router.post(
    "/",
    summary="Create Template",
    description="Create a new template",
    response_model=TemplateMutationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates", method="POST")
async def create_template(
    template_data: TemplateCreateRequest,
    _user=Depends(require_role("admin")),
    orchestrator=CREATE_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Create a new template.

    - **template_data**: Template configuration data
    """
    template_dict = template_data.model_dump(exclude_unset=True)
    result = await orchestrator.execute(
        CreateTemplateInput(
            template_id=template_dict["template_id"],
            name=template_dict.get("name"),
            description=template_dict.get("description"),
            provider_api=template_dict.get("provider_api"),
            instance_type=template_dict.get("instance_type"),
            image_id=template_dict.get("image_id") or "",
            tags=template_dict.get("tags") or {},
            configuration=template_dict,
        )
    )
    return JSONResponse(
        status_code=201,
        content=scheduler.format_template_mutation_response(
            {
                "template_id": result.template_id,
                "status": "created" if result.created else "validation_failed",
                "created": result.created,
                "validation_errors": result.validation_errors,
            }
        ),
    )


@router.put(
    "/{template_id}",
    summary="Update Template",
    description="Update an existing template",
    response_model=TemplateMutationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="PUT")
async def update_template(
    template_id: str,
    template_data: TemplateUpdateRequest,
    _user=Depends(require_role("admin")),
    orchestrator=UPDATE_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Update an existing template.

    - **template_id**: Template identifier
    - **template_data**: Updated template configuration data
    """
    template_dict = template_data.model_dump(exclude_unset=True)
    result = await orchestrator.execute(
        UpdateTemplateInput(
            template_id=template_id,
            name=template_dict.get("name"),
            description=template_dict.get("description"),
            instance_type=template_dict.get("instance_type"),
            image_id=template_dict.get("image_id"),
            configuration=template_dict,
        )
    )
    return JSONResponse(
        status_code=200,
        content=scheduler.format_template_mutation_response(
            {
                "template_id": result.template_id,
                "status": "updated" if result.updated else "validation_failed",
                "updated": result.updated,
                "validation_errors": result.validation_errors,
            }
        ),
    )


@router.delete(
    "/{template_id}",
    summary="Delete Template",
    description="Delete a template",
    response_model=TemplateMutationResponse,
)
@handle_rest_exceptions(endpoint="/api/v1/templates/{template_id}", method="DELETE")
async def delete_template(
    template_id: str,
    _user=Depends(require_role("admin")),
    orchestrator=DELETE_ORCHESTRATOR,
    scheduler=SCHEDULER_STRATEGY,
) -> JSONResponse:
    """
    Delete a template.

    - **template_id**: Template identifier
    """
    result = await orchestrator.execute(DeleteTemplateInput(template_id=template_id))
    return JSONResponse(
        status_code=200,
        content=scheduler.format_template_mutation_response(
            {
                "template_id": result.template_id,
                "status": "deleted" if result.deleted else "not_found",
                "deleted": result.deleted,
            }
        ),
    )
