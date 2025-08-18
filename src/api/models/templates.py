"""Template API models."""

from typing import Dict, List, Optional

from pydantic import Field

from api.models.base import APIRequest, APIResponse


class TemplateAttribute(APIRequest):
    """Template attribute model."""

    type: Optional[List[str]] = None
    ncpus: Optional[List[str]] = None
    nram: Optional[List[str]] = None
    ncores: Optional[List[str]] = None
    rank: Optional[List[str]] = None
    price_info: Optional[List[str]] = None


class Template(APIRequest):
    """Template model with both snake_case and camelCase support via aliases."""

    template_id: str = Field(
        alias="templateId",
        description="Unique ID to identify this template in the host provider",
    )
    max_number: int = Field(
        alias="maxNumber",
        description="Maximum number of machines that can be provisioned with this template configuration",
    )
    attributes: TemplateAttribute = Field(description="Template attributes")
    available_number: Optional[int] = Field(
        default=None,
        alias="availableNumber",
        description="Number of machines that can be currently provisioned with this template",
    )
    requested_machines: Optional[List[str]] = Field(
        default=None,
        alias="requestedMachines",
        description="Names of machines provisioned from this template",
    )
    # Additional fields for provider-specific attributes
    pgrp_name: Optional[str] = Field(
        default=None, alias="pgrpName", description="Placement group name"
    )
    on_demand_capacity: Optional[int] = Field(
        default=0, alias="onDemandCapacity", description="On-demand capacity"
    )
    vm_types: Optional[Dict[str, int]] = Field(
        default=None, alias="vmTypes", description="VM types with weights"
    )
    instance_tags: Optional[str] = Field(
        default=None, alias="instanceTags", description="Instance tags"
    )


class GetAvailableTemplatesRequest(APIRequest):
    """Get available templates request model."""


class GetAvailableTemplatesResponse(APIResponse):
    """Get available templates response model."""

    templates: List[Template] = Field(description="List of available templates")
    message: str = Field(
        default="Get available templates success.",
        description="Any additional message the caller should know",
    )
