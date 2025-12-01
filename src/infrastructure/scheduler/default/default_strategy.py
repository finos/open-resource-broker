"""Default scheduler strategy using native domain fields - no conversion needed."""

from typing import Any, Union

from config.manager import ConfigurationManager
from domain.base.ports.logging_port import LoggingPort
from domain.machine.aggregate import Machine
from domain.template.template_aggregate import Template
from infrastructure.scheduler.base.strategy import BaseSchedulerStrategy


class DefaultSchedulerStrategy(BaseSchedulerStrategy):
    """
    Default scheduler strategy using native domain fields.

    This strategy uses templates and data in their native domain format
    without any field mapping or conversion. It serves as:
    - Reference implementation for scheduler strategies
    - Testing baseline with pure domain objects
    - Simple integration for systems using domain format directly
    """

    def __init__(self, config_manager: ConfigurationManager, logger: "LoggingPort") -> None:
        """Initialize the instance."""
        self.config_manager = config_manager
        self._logger = logger

    def get_templates_file_path(self) -> str:
        """Get templates file path - using native domain format."""
        # Use templates.json with native domain format
        return self.config_manager.resolve_file("template", "templates.json")

    def get_template_paths(self) -> list[str]:
        """Get template file paths."""
        return [self.get_templates_file_path()]

    def load_templates_from_path(self, template_path: str) -> list[dict[str, Any]]:
        """Load templates from path - no field mapping needed."""
        try:
            import json

            with open(template_path) as f:
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

    def parse_template_config(self, raw_data: dict[str, Any]) -> Template:
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

    def parse_request_data(
        self, raw_data: dict[str, Any]
    ) -> Union[dict[str, Any], list[dict[str, Any]]]:
        """
        Parse request data using native domain format - no conversion needed.

        Request data is expected to be in domain format already.
        """

        # Request Status
        if "requests" in raw_data:
            return [{"request_id": req.get("request_id")} for req in raw_data["requests"]]

        # Request Machines - handle nested format: {"template": {"template_id": ..., "machine_count": ...}}
        if "template" in raw_data:
            template_data = raw_data["template"]
            return {
                "template_id": template_data.get("template_id"),
                "requested_count": template_data.get("machine_count", 1),
                "request_type": template_data.get("request_type", "provision"),
                "metadata": raw_data.get("metadata", {}),
            }

        # Request Machines - flat format
        # Return as-is since it's already in domain format
        return {
            "template_id": raw_data.get("template_id"),
            "requested_count": raw_data.get("requested_count", raw_data.get("count", 1)),
            "request_type": raw_data.get("request_type", "provision"),
            "request_id": raw_data.get("request_id"),
            "metadata": raw_data.get("metadata", {}),
        }

    def format_templates_response(self, templates: list[Template]) -> dict[str, Any]:
        """
        Format domain Templates to native domain response format.

        Uses the Template's model_dump() method to serialize to native format.
        """
        return {
            "templates": [template.model_dump() for template in templates],
            "message": "Templates retrieved successfully",
            "count": len(templates),
        }

    def format_machine_status_response(self, machines: list[Machine]) -> dict[str, Any]:
        """
        Format domain Machines to native domain response format.

        Uses the Machine's model_dump() method to serialize to native format.
        """
        return {
            "machines": [machine.model_dump() for machine in machines],
            "message": "Machine status retrieved successfully",
            "count": len(machines),
        }

    def get_working_directory(self) -> str:
        """Get working directory from DEFAULT_PROVIDER_WORKDIR or current directory."""
        import os

        return os.environ.get("DEFAULT_PROVIDER_WORKDIR", os.getcwd())

    def get_config_directory(self) -> str:
        """Get config directory from DEFAULT_PROVIDER_CONFDIR or working_dir/config."""
        import os

        confdir = os.environ.get("DEFAULT_PROVIDER_CONFDIR")
        if confdir:
            return confdir
        return os.path.join(self.get_working_directory(), "config")

    def get_logs_directory(self) -> str:
        """Get logs directory from DEFAULT_PROVIDER_LOGDIR or working_dir/logs."""
        import os

        logdir = os.environ.get("DEFAULT_PROVIDER_LOGDIR")
        if logdir:
            return logdir
        return os.path.join(self.get_working_directory(), "logs")

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        import os

        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""
        import os

        workdir = self.get_working_directory()

        if file_type in ["conf", "template", "legacy"]:
            return os.path.join(workdir, "config")
        elif file_type == "log":
            return os.path.join(workdir, "logs")
        elif file_type in ["work", "data"]:
            return workdir
        else:
            return workdir

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response to native domain format."""
        return {
            "request_id": request_data.get("request_id", request_data.get("requestId")),
            "message": request_data.get("message", "Request submitted successfully"),
            "template_id": request_data.get("template_id"),
            "count": request_data.get("count"),
        }
