"""Template domain exceptions."""

from domain.base.exceptions import DomainException, EntityNotFoundError, ValidationError


class TemplateException(DomainException):
    """Base exception for template domain errors."""


class TemplateNotFoundError(EntityNotFoundError):
    """Raised when a template is not found."""

    def __init__(self, template_id: str):
        """Initialize the instance."""
        super().__init__("Template", template_id)


class TemplateValidationError(ValidationError):
    """Raised when template validation fails."""


class InvalidTemplateConfigurationError(TemplateException):
    """Raised when template configuration is invalid."""


class TemplateAlreadyExistsError(TemplateException):
    """Raised when attempting to create a template that already exists."""

    def __init__(self, template_id: str):
        """Initialize template already exists error with template ID."""
        message = f"Template with ID '{template_id}' already exists"
        super().__init__(message, "TEMPLATE_ALREADY_EXISTS", {"template_id": template_id})
