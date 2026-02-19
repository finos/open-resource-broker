"""Domain port for scheduler-specific operations."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from application.dto.responses import MachineDTO
    from application.request.dto import RequestDTO
    from infrastructure.template.dtos import TemplateDTO


class SchedulerPort(ABC):
    """Application port for scheduler-specific operations."""

    @abstractmethod
    def get_config_file_path(self) -> str:
        """Get config file path for this scheduler."""

    @abstractmethod
    def get_config_directory(self) -> str:
        """Get config directory with coalesce pattern."""

    @abstractmethod
    def get_working_directory(self) -> str:
        """Get working directory with coalesce pattern."""

    @abstractmethod
    def get_logs_directory(self) -> str:
        """Get logs directory with coalesce pattern."""

    @abstractmethod
    def get_log_level(self) -> str:
        """Get log level for this scheduler."""

    @abstractmethod
    def parse_template_config(self, raw_data: dict[str, Any]) -> TemplateDTO:
        """Parse scheduler template config to TemplateDTO."""

    @abstractmethod
    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Parse scheduler request data to domain-compatible format."""

    @abstractmethod
    def format_templates_response(self, templates: list[TemplateDTO]) -> dict[str, Any]:
        """Format TemplateDTOs to scheduler response."""

    @abstractmethod
    def format_templates_for_generation(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to scheduler's expected input format."""

    @abstractmethod
    def format_request_status_response(self, requests: list[RequestDTO]) -> dict[str, Any]:
        """Format RequestDTOs to scheduler response."""

    @abstractmethod
    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response to scheduler format."""

    @abstractmethod
    def format_machine_status_response(self, machines: list[MachineDTO]) -> dict[str, Any]:
        """Format MachineDTOs to scheduler response."""

    @abstractmethod
    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details for CLI display."""

    @abstractmethod
    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""

    @abstractmethod
    def format_template_for_display(self, template: TemplateDTO) -> dict[str, Any]:
        """Format template for CLI/API display using scheduler-specific field mapping."""

    @abstractmethod
    def format_template_for_provider(self, template: TemplateDTO) -> dict[str, Any]:
        """Format template for provider operations using scheduler-specific field mapping."""

    @abstractmethod
    def format_request_for_display(self, request: RequestDTO) -> dict[str, Any]:
        """Format request for CLI/API display using scheduler-specific field mapping."""

    @abstractmethod
    def get_exit_code_for_status(self, status: str) -> int:
        """Get appropriate exit code for request status."""

    @abstractmethod
    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""

    @abstractmethod
    def get_templates_filename(self, provider_name: str, provider_type: str) -> str:
        """Get templates filename for the given provider."""
