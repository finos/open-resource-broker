"""Request ID generation utilities for API layer."""

import uuid

from domain.request.value_objects import RequestType


def generate_request_id(request_type: RequestType) -> str:
    """
    Generate a prefixed request ID using the same logic as the domain layer.

    This ensures consistency between API-generated IDs and domain-generated IDs.

    Args:
        request_type: The type of request (ACQUIRE or RETURN)

    Returns:
        Prefixed request ID string (e.g., "req-uuid" or "ret-uuid")
    """
    prefix = "req-" if request_type == RequestType.ACQUIRE else "ret-"
    return f"{prefix}{uuid.uuid4()}"
