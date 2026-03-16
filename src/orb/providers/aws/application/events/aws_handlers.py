"""AWS-specific event handlers."""

from typing import Any

from orb.domain.base.events import DomainEvent
from orb.infrastructure.logging.logger import get_logger

_logger = get_logger(__name__)


def _get_field(event: DomainEvent, name: str, default: Any = None) -> Any:
    """Extract a named field from a domain event's attributes or metadata."""
    value = getattr(event, name, None)
    if value is not None:
        return value
    return event.metadata.get(name, default)


def handle_aws_client_operation(event: DomainEvent) -> None:
    """Handle AWS client operation events."""
    service = _get_field(event, "service", "unknown")
    operation = _get_field(event, "operation", "unknown")
    success = _get_field(event, "success", False)
    region = _get_field(event, "region")
    request_id = _get_field(event, "request_id")

    message_parts = [f"AWS operation: {service}.{operation} | Success: {success}"]
    if region:
        message_parts.append(f"Region: {region}")
    if request_id:
        message_parts.append(f"RequestId: {request_id}")
    message = " | ".join(message_parts)

    log_level = "info" if success else "warning"
    getattr(_logger, log_level)(message)


def handle_aws_rate_limit(event: DomainEvent) -> None:
    """Handle AWS rate limit events."""
    service = _get_field(event, "service", "unknown")
    operation = _get_field(event, "operation", "unknown")
    retry_after = _get_field(event, "retry_after")
    request_id = _get_field(event, "request_id")

    message_parts = [
        f"AWS RATE LIMIT: {service}.{operation}",
        f"Retry after: {retry_after}s",
    ]
    if request_id:
        message_parts.append(f"RequestId: {request_id}")
    message = " | ".join(message_parts)

    _logger.warning(message)


def handle_aws_credentials_event(event: DomainEvent) -> None:
    """Handle AWS provider auth config events."""
    event_type = _get_field(event, "event_type", "unknown")
    profile = _get_field(event, "profile")
    region = _get_field(event, "region")

    message = f"AWS provider auth config: {event_type}"
    if profile:
        message += f" | Profile: {profile}"
    if region:
        message += f" | Region: {region}"

    _logger.info(message)


# AWS-specific event handler registry
AWS_EVENT_HANDLERS = {
    "AWSClientOperationEvent": handle_aws_client_operation,
    "AWSRateLimitEvent": handle_aws_rate_limit,
    "AWSCredentialsEvent": handle_aws_credentials_event,
}
