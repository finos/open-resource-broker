"""Service for creating request aggregates with proper validation and metadata."""

from application.dto.commands import CreateRequestCommand
from domain.base.ports import LoggingPort
from domain.base.results import ProviderSelectionResult
from domain.request.aggregate import Request
from domain.request.value_objects import RequestType
from domain.template.template_aggregate import Template


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

        # Create request aggregate with selected provider
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=command.template_id,
            machine_count=command.requested_count,
            provider_type=selection_result.provider_type,
            provider_name=selection_result.provider_name,
            metadata={
                **command.metadata,
                "dry_run": getattr(command, "dry_run", False),
                "provider_selection_reason": selection_result.selection_reason,
                "provider_confidence": selection_result.confidence,
            },
            request_id=command.request_id,
        )

        # Store provider API in domain field
        request.provider_api = template.provider_api or "RunInstances"

        self._logger.info(
            "Created request %s with provider API: %s",
            request.request_id,
            request.provider_api,
        )

        return request
