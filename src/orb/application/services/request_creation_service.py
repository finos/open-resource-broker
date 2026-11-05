"""Service for creating request aggregates with proper validation and metadata."""

from typing import Any

from orb.application.dto.commands import CreateRequestCommand
from orb.application.services.template_snapshot import (
    SNAPSHOT_METADATA_KEY,
    capture_template_snapshot,
)
from orb.domain.base.ports import LoggingPort
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import RequestValidationError
from orb.domain.request.value_objects import RequestType
from orb.domain.template.template_aggregate import Template

# Set by the service itself; caller-supplied values would be trusted later.
RESERVED_METADATA_KEYS: frozenset[str] = frozenset({SNAPSHOT_METADATA_KEY, "template_overrides"})


class RequestCreationService:
    """Service for creating request aggregates with validation and metadata."""

    def __init__(self, logger: LoggingPort):
        """Initialize the service."""
        self._logger = logger

    def create_machine_request(
        self,
        command: CreateRequestCommand,
        template: Template,
        selection_result: ProviderSelectionResult,
    ) -> Request:
        """
        Create request aggregate with validation and metadata.

        Args:
            command: The create request command
            template: The template to use for the request
            selection_result: The provider selection result

        Returns:
            Request aggregate with proper metadata and provider information
        """
        self._logger.info(
            "Creating request for template %s with provider %s",
            command.template_id,
            selection_result.provider_name,
        )

        reserved = sorted(RESERVED_METADATA_KEYS & set(command.additional_data or {}))
        if reserved:
            raise RequestValidationError(
                f"additional_data may not set reserved keys: {', '.join(reserved)}"
            )
        snapshot = capture_template_snapshot(template)
        overrides: dict[str, Any] = dict(getattr(command, "template_overrides", None) or {})

        # Create request aggregate with selected provider
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=command.template_id,
            machine_count=command.requested_count,
            provider_type=selection_result.provider_type,
            provider_name=selection_result.provider_name,
            metadata={
                **command.metadata,
                **(command.additional_data or {}),
                "dry_run": getattr(command, "dry_run", False),
                **({SNAPSHOT_METADATA_KEY: snapshot} if snapshot else {}),
                **({"template_overrides": overrides} if overrides else {}),
                "provider_selection_reason": selection_result.selection_reason,
                "provider_confidence": selection_result.confidence,
            },
            request_id=command.request_id,
        )

        # Store provider API in domain field
        if not template.provider_api:
            raise RequestValidationError(
                f"Template {template.template_id} has no provider_api configured"
            )
        request.provider_api = template.provider_api

        self._logger.info(
            "Created request %s with provider API: %s",
            request.request_id,
            request.provider_api,
        )

        return request
