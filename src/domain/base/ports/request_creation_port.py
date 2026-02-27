"""Port for request creation operations."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

from domain.request.aggregate import Request

if TYPE_CHECKING:
    from domain.base.results import ProviderSelectionResult
    from domain.template.template_aggregate import Template


class MachineRequestCommand(Protocol):
    """Minimal protocol describing what RequestCreationPort needs from a create-request command."""

    request_id: Optional[str]
    template_id: str
    requested_count: int
    metadata: Dict[str, Any]


class RequestCreationPort(ABC):
    """Port for creating request aggregates with validation and metadata."""

    @abstractmethod
    def create_machine_request(
        self,
        command: MachineRequestCommand,
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
