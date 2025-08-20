"""Request validation utilities for API handlers."""

import json
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

from infrastructure.error.decorators import handle_interface_exceptions
from infrastructure.logging.logger import get_logger

# Type variable for Pydantic models
T = TypeVar("T", bound=BaseModel)

# Get logger
logger = get_logger(__name__)


class ValidationException(Exception):
    """Exception raised for validation errors."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Initialize validation exception.

        Args:
            message: Error message
            errors: List of validation errors
        """
        self.message = message
        self.errors = errors or []
        super().__init__(self.message)


@handle_interface_exceptions(context="request_body_validation", interface_type="api")
def validate_request_body(model_class: Type[T], request_body: Union[str, Dict[str, Any]]) -> T:
    """
    Validate request body against a Pydantic model.

    Args:
        model_class: Pydantic model class to validate against
        request_body: Request body as string or dictionary

    Returns:
        Validated model instance

    Raises:
        ValidationException: If validation fails
    """
    try:
        # Parse JSON if request_body is a string
        if isinstance(request_body, str):
            try:
                data = json.loads(request_body)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in request body: %s", e)
                raise ValidationException(f"Invalid JSON in request body: {e}")
        else:
            data = request_body

        # Validate against model
        return model_class.model_validate(data)
    except ValidationError as e:
        # Extract error details
        error_details = []
        for error in e.errors():
            error_details.append(
                {
                    "loc": error.get("loc", []),
                    "msg": error.get("msg", ""),
                    "type": error.get("type", ""),
                }
            )

        logger.error("Validation error: %s", e)
        raise ValidationException(f"Validation error: {e}", error_details)


def create_error_response(
    message: str, errors: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Create standardized error response.

    Args:
        message: Error message
        errors: List of validation errors

    Returns:
        Error response dictionary
    """
    response = {"status": "error", "message": message}

    if errors:
        response["errors"] = errors

    return response


class RequestValidator:
    """Request validator for API handlers."""

    @staticmethod
    def validate(model_class: Type[T], request_body: Union[str, Dict[str, Any]]) -> T:
        """
        Validate request body against a Pydantic model.

        Args:
            model_class: Pydantic model class to validate against
            request_body: Request body as string or dictionary

        Returns:
            Validated model instance

        Raises:
            ValidationException: If validation fails
        """
        return validate_request_body(model_class, request_body)

    @staticmethod
    def handle_validation_error(e: ValidationException) -> Dict[str, Any]:
        """
        Handle validation error and create error response.

        Args:
            e: Validation exception

        Returns:
            Error response dictionary
        """
        return create_error_response(e.message, e.errors)
