"""Domain port for scheduler-specific operations."""

from abc import ABC, abstractmethod
from typing import Any

from domain.machine.aggregate import Machine
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template


class SchedulerPort(ABC):
    """Domain port for scheduler-specific operations - SINGLE FIELD MAPPING POINT."""

    @abstractmethod
    def get_templates_file_path(self) -> str:
        """Get templates file path for this scheduler."""

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
    def parse_template_config(self, raw_data: dict[str, Any]) -> Template:
        """Parse scheduler template config to domain Template - SINGLE MAPPING POINT."""

    @abstractmethod
    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Parse scheduler request data to domain-compatible format - SINGLE MAPPING POINT."""

    @abstractmethod
    def format_templates_response(self, templates: list[Template]) -> dict[str, Any]:
        """Format domain Templates to scheduler response - uses domain.model_dump()."""

    @abstractmethod
    def format_templates_for_generation(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to scheduler's expected input format."""

    @abstractmethod
    def format_request_status_response(self, requests: list[Request]) -> dict[str, Any]:
        """Format domain Requests to scheduler response - uses domain.model_dump()."""

    @abstractmethod
    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response to scheduler format."""

    @abstractmethod
    def format_machine_status_response(self, machines: list[Machine]) -> dict[str, Any]:
        """Format domain Machines to scheduler response - uses domain.model_dump()."""

    @abstractmethod
    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""

    @abstractmethod
    def format_template_for_display(self, template: Template) -> dict[str, Any]:
        """Format template for CLI/API display using scheduler-specific field mapping."""
        pass

    @abstractmethod
    def format_template_for_provider(self, template: Template) -> dict[str, Any]:
        """Format template for provider operations using scheduler-specific field mapping."""
        pass

    @abstractmethod
    def format_request_for_display(self, request: Request) -> dict[str, Any]:
        """Format request for CLI/API display using scheduler-specific field mapping."""
        pass

    @abstractmethod
    def get_exit_code_for_status(self, status: str) -> int:
        """Get appropriate exit code for request status."""
        pass

    @abstractmethod
    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type.

        Args:
            file_type: Type of file (conf, template, log, work, data, etc.)

        Returns:
            Directory path or None if not available
        """

    @abstractmethod
    def get_templates_filename(self, provider_name: str, provider_type: str) -> str:
        """Get templates filename for the given provider."""

    @abstractmethod
    def should_log_to_console(self) -> bool:
        """Check if logs should be written to console for this scheduler.

        Returns:
            True if logs should go to console (Default/interactive mode)
            False if logs should only go to file (HostFactory script mode)
        """

    @abstractmethod
    def format_error_response(self, error: Exception, context: dict[str, Any]) -> dict[str, Any]:
        """Format error response for this scheduler."""

    @abstractmethod
    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        """Format health check response for this scheduler."""
