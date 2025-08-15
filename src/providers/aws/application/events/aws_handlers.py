"""AWS-specific event handlers."""

import logging

from domain.base.events import DomainEvent


def handle_aws_client_operation(event: DomainEvent) -> None:
    """Handle AWS client operation events."""
    from application.events.handlers.system_handlers import system_handler

    fields = system_handler.extract_fields(
        event,
        {
            "service": "unknown",
            "operation": "unknown",
            "success": False,
            "request_id": None,
            "region": None,
        },
    )

    # Use list and join for better performance
    message_parts = [
        f"AWS operation: {fields['service']}.{fields['operation']} | Success: {fields['success']}"
    ]
    if fields["region"]:
        message_parts.append(f"Region: {fields['region']}")
    if fields["request_id"]:
        message_parts.append(f"RequestId: {fields['request_id']}")
    message = " | ".join(message_parts)

    log_level = logging.INFO if fields["success"] else logging.WARNING
    system_handler.log_event(event, message, log_level)


def handle_aws_rate_limit(event: DomainEvent) -> None:
    """Handle AWS rate limit events."""
    from application.events.handlers.system_handlers import system_handler

    fields = system_handler.extract_fields(
        event,
        {
            "service": "unknown",
            "operation": "unknown",
            "retry_after": None,
            "request_id": None,
        },
    )

    # Use list and join for better performance
    message_parts = [
        f"AWS RATE LIMIT: {fields['service']}.{fields['operation']}",
        f"Retry after: {fields['retry_after']}s",
    ]
    if fields["request_id"]:
        message_parts.append(f"RequestId: {fields['request_id']}")
    message = " | ".join(message_parts)

    system_handler.log_event(event, message, logging.WARNING)


def handle_aws_credentials_event(event: DomainEvent) -> None:
    """Handle AWS credentials events."""
    from application.events.handlers.system_handlers import system_handler

    fields = system_handler.extract_fields(
        event, {"event_type": "unknown", "profile": None, "region": None}
    )

    message = f"AWS credentials: {fields['event_type']}"
    if fields["profile"]:
        message += f" | Profile: {fields['profile']}"
    if fields["region"]:
        message += f" | Region: {fields['region']}"

    system_handler.log_event(event, message, logging.INFO)


# AWS-specific event handler registry
AWS_EVENT_HANDLERS = {
    "AWSClientOperationEvent": handle_aws_client_operation,
    "AWSRateLimitEvent": handle_aws_rate_limit,
    "AWSCredentialsEvent": handle_aws_credentials_event,
}
