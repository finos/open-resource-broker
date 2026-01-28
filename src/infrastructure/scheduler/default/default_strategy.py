"""Default scheduler strategy using native domain fields - no conversion needed."""

from typing import Any, Union

from config.manager import ConfigurationManager
from domain.base.ports.logging_port import LoggingPort
from domain.machine.aggregate import Machine
from domain.template.template_aggregate import Template
from infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from infrastructure.scheduler.default.field_mapper import DefaultFieldMapper


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

        # Initialize provider selection service for provider selection
        from application.services.provider_selection_service import (
            ProviderSelectionService,
        )
        from infrastructure.di.container import get_container

        container = get_container()
        self._provider_selection_service = container.get(ProviderSelectionService)

        # Initialize field mapper
        self.field_mapper = DefaultFieldMapper()

    def get_templates_file_path(self) -> str:
        """Get templates file path using strategy pattern."""
        try:
            # Use provider selection service that respects CLI override
            selection_result = (
                self._provider_selection_service.select_provider_for_template_loading()
            )
            provider_name = selection_result.provider_instance
            provider_type = selection_result.provider_type

            # Use scheduler strategy for filename (consistent with generation)
            filename = self.get_templates_filename(provider_name, provider_type)

            # Use ConfigurationManager to resolve the file path
            return self.config_manager.resolve_file("template", filename)

        except Exception as e:
            self._logger.error("Failed to determine templates file path: %s", e)
            # Fallback to default behavior
            filename = self.get_templates_filename("default", "default")
            return self.config_manager.resolve_file("template", filename)

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

        Uses the new architecture-compliant method for consistency.
        """
        return {
            "templates": [self.format_template_for_display(template) for template in templates],
            "message": "Templates retrieved successfully",
            "count": len(templates),
        }

    def format_templates_for_generation(self, templates: list[dict]) -> list[dict]:
        """No conversion needed - use field mapper (identity mapping)."""
        return self.field_mapper.format_for_generation(templates)

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

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        import os

        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    @classmethod
    def get_templates_filename(
        cls, provider_name: str, provider_type: str, config: dict = None
    ) -> str:
        """Get templates filename with config override support.

        Can be called as classmethod (before app init) or instance method.
        """
        # Check config override first
        if config:
            scheduler_config = config.get("scheduler", {})
            config_filename = scheduler_config.get("templates_filename")
            if config_filename:
                return config_filename

        # Use Default scheduler default: 'templates.json'
        return "templates.json"

    def should_log_to_console(self) -> bool:
        """Check if logs should be written to console for Default scheduler.

        Default scheduler is interactive, always log to console.
        """
        return True

    def format_error_response(self, error: Exception, context: dict[str, Any]) -> dict[str, Any]:
        """Format error response for Default scheduler (console + JSON)."""
        import sys
        import traceback

        # Print to console
        print(f"ERROR: {error}", file=sys.stderr)
        if context.get("verbose"):
            traceback.print_exc()

        # Return JSON
        response = {"success": False, "error": str(error)}

        if context.get("verbose"):
            response["traceback"] = traceback.format_exc()

        return response

    def get_exit_code_for_status(self, status: str) -> int:
        """Default scheduler exit codes: 1 for any problem, 0 for success."""
        problem_statuses = ["failed", "cancelled", "timeout", "partial"]
        return 1 if status in problem_statuses else 0

    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        """Format health check response for Default scheduler."""
        passed = sum(1 for c in checks if c.get("status") == "pass")
        failed = len(checks) - passed

        return {
            "success": failed == 0,
            "checks": checks,
            "summary": {"total": len(checks), "passed": passed, "failed": failed},
        }

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
        # If this is a status/detail response, pass through the requests list and status
        if "requests" in request_data:
            return {
                "requests": request_data.get("requests", []),
                "status": request_data.get("status", "complete"),
                "message": request_data.get("message", "Status retrieved successfully"),
                "errors": request_data.get("errors"),
            }

        # Get status and error info
        status = request_data.get("status", "pending")
        error_message = request_data.get("error_message")
        request_id = request_data.get("request_id", request_data.get("requestId"))

        # Status-based message and response logic
        if status == "failed":
            return {
                "error": f"Request failed: {error_message or 'Unknown error'}",
                "request_id": request_id,
            }
        elif status == "cancelled":
            return {"error": "Request cancelled", "request_id": request_id}
        elif status == "timeout":
            return {"error": "Request timed out", "request_id": request_id}
        elif status == "partial":
            return {
                "warning": f"Request partially completed: {error_message or 'Some resources failed'}",
                "request_id": request_id,
            }
        elif status == "complete":
            return {"request_id": request_id, "message": "Request completed successfully"}
        elif status == "in_progress":
            return {"request_id": request_id, "message": "Request in progress"}
        elif status == "pending":
            return {"request_id": request_id, "message": "Request submitted successfully"}
        else:
            return {
                "request_id": request_id,
                "message": request_data.get("message", "Request status unknown"),
            }
