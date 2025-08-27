"""Domain port for scheduler-specific operations."""

from abc import ABC, abstractmethod
from typing import Any

from domain.machine.aggregate import Machine
from domain.request.aggregate import Request
from domain.template.aggregate import Template


class SchedulerPort(ABC):
    """Domain port for scheduler-specific operations - SINGLE FIELD MAPPING POINT."""

    @abstractmethod
    def get_templates_file_path(self) -> str:
        """Get templates file path for this scheduler."""

    @abstractmethod
    def get_config_file_path(self) -> str:
        """Get config file path for this scheduler."""

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
    def format_request_status_response(self, requests: list[Request]) -> dict[str, Any]:
        """Format domain Requests to scheduler response - uses domain.model_dump()."""

    @abstractmethod
    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response to scheduler format."""

    @abstractmethod
    def format_machine_status_response(self, machines: list[Machine]) -> dict[str, Any]:
        """Format domain Machines to scheduler response - uses domain.model_dump()."""

    @abstractmethod
    def get_working_directory(self) -> str:
        """Get working directory for this scheduler."""

    @abstractmethod
    def get_config_directory(self) -> str:
        """Get config directory for this scheduler."""

    @abstractmethod
    def get_logs_directory(self) -> str:
        """Get logs directory for this scheduler."""

    @abstractmethod
    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
