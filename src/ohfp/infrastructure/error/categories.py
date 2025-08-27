"""Error categories and codes for exception classification."""


class ErrorCategory:
    """Error categories for classification."""

    # Domain errors
    VALIDATION = "validation_error"
    BUSINESS_RULE = "business_rule_violation"
    ENTITY_NOT_FOUND = "entity_not_found"

    # Specific not found errors
    TEMPLATE_NOT_FOUND = "template_not_found"
    MACHINE_NOT_FOUND = "machine_not_found"
    REQUEST_NOT_FOUND = "request_not_found"

    # Business rule errors
    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    INVALID_STATE = "invalid_state"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"

    # Infrastructure errors
    CONFIGURATION = "configuration_error"
    DATABASE_ERROR = "database_error"
    NETWORK_ERROR = "network_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"

    # System errors
    INTERNAL_ERROR = "internal_error"
    UNEXPECTED_ERROR = "unexpected_error"

    # Legacy compatibility
    NOT_FOUND = "not_found_error"
    BUSINESS_RULE = "business_rule_error"
    INFRASTRUCTURE = "infrastructure_error"
    EXTERNAL_SERVICE = "external_service_error"
    UNAUTHORIZED = "unauthorized_error"
    FORBIDDEN = "forbidden_error"
    INTERNAL = "internal_error"


class ErrorCode:
    """Specific error codes for detailed error reporting."""

    # Validation errors
    INVALID_INPUT = "invalid_input"
    MISSING_FIELD = "missing_field"
    INVALID_FORMAT = "invalid_format"

    # Not found errors
    RESOURCE_NOT_FOUND = "resource_not_found"
    TEMPLATE_NOT_FOUND = "template_not_found"
    MACHINE_NOT_FOUND = "machine_not_found"
    REQUEST_NOT_FOUND = "request_not_found"

    # Business rule errors
    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    INVALID_STATE = "invalid_state"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"

    # Infrastructure errors
    DATABASE_ERROR = "database_error"
    NETWORK_ERROR = "network_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"

    # System errors
    INTERNAL_ERROR = "internal_error"
    UNEXPECTED_ERROR = "unexpected_error"
