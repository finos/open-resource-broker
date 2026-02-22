"""Port for request creation operations."""

from abc import ABC, abstractmethod

from domain.request.aggregate import Request


class RequestCreationPort(ABC):
    """Port for creating request aggregates with validation and metadata."""

    @abstractmethod
    def create_machine_request(
        self,
        command: "CreateRequestCommand",
        template: "Template",
        selection_result: "ProviderSelectionResult",
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
