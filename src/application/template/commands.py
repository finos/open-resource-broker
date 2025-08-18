"""Template commands - template use case commands."""

from typing import Any, Dict, List, Optional

from pydantic import Field

from application.dto.base import BaseCommand, BaseResponse


class CreateTemplateCommand(BaseCommand):
    """Command to create a new template."""

    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    provider_api: str
    instance_type: Optional[str] = None
    image_id: str
    subnet_ids: List[str] = Field(default_factory=list)
    security_group_ids: List[str] = Field(default_factory=list)
    tags: Dict[str, str] = Field(default_factory=dict)
    configuration: Dict[str, Any] = Field(default_factory=dict)


class UpdateTemplateCommand(BaseCommand):
    """Command to update an existing template."""

    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    configuration: Dict[str, Any] = Field(default_factory=dict)


class DeleteTemplateCommand(BaseCommand):
    """Command to delete a template."""

    template_id: str


class ValidateTemplateCommand(BaseCommand):
    """Command to validate a template configuration."""

    template_id: str
    configuration: Dict[str, Any]


class TemplateCommandResponse(BaseResponse):
    """Response for template commands."""

    template_id: Optional[str] = None
    validation_errors: List[str] = Field(default_factory=list)
