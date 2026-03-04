"""Template commands - template use case commands."""

from typing import Any, Optional

from pydantic import Field

from application.dto.base import BaseCommand, BaseResponse


class CreateTemplateCommand(BaseCommand):
    """Command to create a new template.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    provider_api: str
    instance_type: Optional[str] = None
    image_id: str
    tags: dict[str, str] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)

    # Mutable result fields for CQRS compliance
    validation_errors: Optional[list[str]] = None
    created: bool = False


class UpdateTemplateCommand(BaseCommand):
    """Command to update an existing template.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    configuration: dict[str, Any] = Field(default_factory=dict)

    # Mutable result fields for CQRS compliance
    validation_errors: Optional[list[str]] = None
    updated: bool = False


class DeleteTemplateCommand(BaseCommand):
    """Command to delete a template.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    template_id: str

    # Mutable result fields for CQRS compliance
    deleted: bool = False


class TemplateCommandResponse(BaseResponse):
    """Response for template commands."""

    template_id: Optional[str] = None
    validation_errors: list[str] = Field(default_factory=list)
