"""Template bounded context - template domain logic."""

from .aggregate import Template
from .exceptions import (
    InvalidTemplateConfigurationError,
    TemplateAlreadyExistsError,
    TemplateException,
    TemplateNotFoundError,
    TemplateValidationError,
)

__all__: list[str] = [
    "InvalidTemplateConfigurationError",
    "Template",
    "TemplateAlreadyExistsError",
    "TemplateException",
    "TemplateNotFoundError",
    "TemplateValidationError",
]
