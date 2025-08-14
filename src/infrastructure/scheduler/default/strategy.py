"""Default scheduler strategy using native domain fields - no conversion needed."""

from typing import Any, Dict, List

from src.config.manager import ConfigurationManager
from src.domain.base.ports.logging_port import LoggingPort
from src.domain.machine.aggregate import Machine
from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template
from src.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy


class DefaultSchedulerStrategy(BaseSchedulerStrategy):
    """
    Default scheduler strategy using native domain fields.

    This strategy uses templates and data in their native domain format
    without any field mapping or conversion. It serves as:
    - Reference implementation for scheduler strategies
    - Testing baseline with pure domain objects
    - Simple integration for systems using domain format directly
    """

    def __init__(self, config_manager: ConfigurationManager, logger: "LoggingPort"):
        """Initialize the instance."""
        self.config_manager = config_manager
        self._logger = logger

    def get_templates_file_path(self) -> str:
        """Get templates file path - using native domain format."""
        # Use templates.json with native domain format
        return self.config_manager.resolve_file("template", "templates.json")

    def get_template_paths(self) -> List[str]:
        """Get template file paths."""
        return [self.get_templates_file_path()]

    def load_templates_from_path(self, template_path: str) -> List[Dict[str, Any]]:
        """Load templates from path - no field mapping needed."""
        try:
            import json

            with open(template_path, "r") as f:
                data = json.load(f)

            # Handle different template file formats
            if isinstance(data, dict) and "templates" in data:
                return data["templates"]
            elif isinstance(data, list):
                return data
            else:
                return []

        except Exception:
            # Return empty list on error - let caller handle logging
            return []

    def get_config_file_path(self) -> str:
        """Get config file path - using default configuration."""
        # Use default_config.json
        return self.config_manager.resolve_file("config", "default_config.json")

    def parse_template_config(self, raw_data: Dict[str, Any]) -> Template:
        """
        Parse template using native domain fields - no conversion needed.

        Since the template data is already in domain format, we can create
        the Template object directly without any field mapping.
        """
        try:
            # Direct domain object creation - templates are in native format
            return Template(**raw_data)
        except Exception as e:
            # Provide helpful error message for debugging
            raise ValueError(f"Failed to create Template from data: {e}. Data: {raw_data}")

    def parse_request_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse request data using native domain format - no conversion needed.

        Request data is expected to be in domain format already.
        """
        # Return as-is since it's already in domain format
        return {
            "template_id": raw_data.get("template_id"),
            "requested_count": raw_data.get("requested_count", raw_data.get("count", 1)),
            "request_type": raw_data.get("request_type", "provision"),
            "request_id": raw_data.get("request_id"),
            "metadata": raw_data.get("metadata", {}),
        }

    def format_templates_response(self, templates: List[Template]) -> Dict[str, Any]:
        """
        Format domain Templates to native domain response format.

        Uses the Template's to_dict() method to serialize to native format.
        """
        return {
            "templates": [template.to_dict() for template in templates],
            "message": "Templates retrieved successfully",
            "count": len(templates),
        }

    def format_request_status_response(self, requests: List[Request]) -> Dict[str, Any]:
        """
        Format domain Requests to native domain response format.

        Uses the Request's to_dict() method to serialize to native format.
        """
        return {
            "requests": [request.to_dict() for request in requests],
            "message": "Request status retrieved successfully",
            "count": len(requests),
        }

    def format_machine_status_response(self, machines: List[Machine]) -> Dict[str, Any]:
        """
        Format domain Machines to native domain response format.

        Uses the Machine's to_dict() method to serialize to native format.
        """
        return {
            "machines": [machine.to_dict() for machine in machines],
            "message": "Machine status retrieved successfully",
            "count": len(machines),
        }

    def get_working_directory(self) -> str:
        """Get working directory - use current directory."""
        import os

        return os.getcwd()

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        import os

        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    def format_request_response(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format request creation response to native domain format."""
        return {
            "requestId": request_data.get("request_id", request_data.get("requestId")),
            "message": request_data.get("message", "Request submitted successfully"),
            "template_id": request_data.get("template_id"),
            "count": request_data.get("count"),
        }
