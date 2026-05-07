"""Application port for scheduler-specific operations."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


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
    def format_templates_for_dispatch(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to scheduler's expected input format."""

    def serialize_template_for_storage(self, template_dict: dict) -> dict:
        """Serialize a TemplateDTO dict to on-disk format. No defaults applied."""
        return self.format_templates_for_dispatch([template_dict])[0]

    @abstractmethod
    def format_request_status_response(self, requests: list[Any]) -> dict[str, Any]:
        """Format request DTOs to scheduler response."""

    @abstractmethod
    def format_return_requests_response(self, requests: list[Any]) -> dict[str, Any]:
        """Format return-request items for this scheduler's wire protocol.

        Structurally different from format_request_status_response — per IBM
        Symphony HF 7.3.2 spec, items are flat {machine, gracePeriod} pairs
        rather than request-status envelopes.
        """

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
    def get_exit_code_for_status(self, status: str) -> int:
        """Get appropriate exit code for request status."""

    @abstractmethod
    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""

    @abstractmethod
    def get_templates_filename(
        self, provider_name: str, provider_type: str, config: dict | None = None
    ) -> str:
        """Get templates filename for the given provider."""

    @abstractmethod
    def get_scheduler_type(self) -> str:
        """Return the scheduler type identifier (e.g. 'hostfactory', 'default')."""

    @abstractmethod
    def get_scripts_directory(self) -> Path | None:
        """Return the path to the scheduler's scripts directory, or None if not applicable."""

    @abstractmethod
    def should_log_to_console(self) -> bool:
        """Return True if log output should be written to the console."""

    @abstractmethod
    def format_template_mutation_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Format template mutation response to scheduler format."""

    @abstractmethod
    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        """Format health check results into a scheduler-specific response dict."""

    @abstractmethod
    def get_template_paths(self) -> list[str]:
        """Return the list of template file paths for this scheduler."""

    @abstractmethod
    def format_system_status_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Format system status dict for CLI display."""

    @abstractmethod
    def format_provider_detail_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Format provider detail dict for CLI display."""

    @abstractmethod
    def format_storage_test_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Format storage test result dict for CLI display."""

    @classmethod
    def get_defaults_config(cls) -> dict:
        return {}
