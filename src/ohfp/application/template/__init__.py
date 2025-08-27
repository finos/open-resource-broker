"""Template application context - template use cases."""

# Import queries from the centralized dto.queries module
from application.dto.queries import (
    GetTemplateQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)

from .commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    TemplateCommandResponse,
    UpdateTemplateCommand,
    ValidateTemplateCommand,
)

__all__: list[str] = [
    # Commands
    "CreateTemplateCommand",
    "DeleteTemplateCommand",
    # Queries
    "GetTemplateQuery",
    "ListTemplatesQuery",
    "TemplateCommandResponse",
    "UpdateTemplateCommand",
    "ValidateTemplateCommand",
    "ValidateTemplateQuery",
]
