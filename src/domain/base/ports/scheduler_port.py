"""Domain port for scheduler-specific operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Use forward references to avoid circular imports
    # These types should be defined in domain layer or passed as Any
    pass


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
    def parse_template_config(self, raw_data: dict[str, Any]) -> Any:
        """Parse scheduler template config to template DTO."""

    @abstractmethod
    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        """Parse scheduler request data to domain-compatible format."""

    @abstractmethod
    def format_templates_response(self, templates: list[Any]) -> dict[str, Any]:
        """Format template DTOs to scheduler response."""

    @abstractmethod
    def format_templates_for_generation(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to scheduler's expected input format."""

    @abstractmethod
    def format_request_status_response(self, requests: list[Any]) -> dict[str, Any]:
        """Format request DTOs to scheduler response."""

    @abstractmethod
    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response to scheduler format."""

    @abstractmethod
    def format_machine_status_response(self, machines: list[Any]) -> dict[str, Any]:
        """Format machine DTOs to scheduler response."""

    @abstractmethod
    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details for CLI display."""

    @abstractmethod
    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""

    @abstractmethod
    def format_template_for_display(self, template: Any) -> dict[str, Any]:
        """Format template for CLI/API display using scheduler-specific field mapping."""

    @abstractmethod
    def format_template_for_provider(self, template: Any) -> dict[str, Any]:
        """Format template for provider operations using scheduler-specific field mapping."""

    @abstractmethod
    def format_request_for_display(self, request: Any) -> dict[str, Any]:
        """Format request for CLI/API display using scheduler-specific field mapping."""

    @abstractmethod
    def get_exit_code_for_status(self, status: str) -> int:
        """Get appropriate exit code for request status."""

    @abstractmethod
    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""

    @abstractmethod
    def get_templates_filename(self, provider_name: str, provider_type: str, config: dict | None = None) -> str:
        """Get templates filename for the given provider."""

    @abstractmethod
    def get_scheduler_type(self) -> str:
        """Return the scheduler type identifier (e.g. 'hostfactory', 'default')."""

    @abstractmethod
    def get_scripts_directory(self) -> Path | None:
        """Return the path to the scheduler's scripts directory, or None if not applicable."""
