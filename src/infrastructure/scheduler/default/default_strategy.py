"""Default scheduler strategy using native domain fields - no conversion needed."""

import os
from typing import Any

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

    def __init__(self) -> None:
        """Initialize the instance."""
        self._config_manager = None
        self._logger = None
        # Initialize field mapper
        self.field_mapper = DefaultFieldMapper()

    @property
    def config_manager(self) -> Any:
        if self._config_manager is None:
            from domain.base.ports.configuration_port import ConfigurationPort
            from infrastructure.di.container import get_container, is_container_ready

            if is_container_ready():
                self._config_manager = get_container().get(ConfigurationPort)
        return self._config_manager

    @property
    def logger(self) -> Any:
        if self._logger is None:
            from infrastructure.di.container import get_container, is_container_ready

            if is_container_ready():
                self._logger = get_container().get(LoggingPort)
        return self._logger

    def get_template_paths(self) -> list[str]:
        """Get template file paths with fallback hierarchy."""
        paths = []

        try:
            # 1. Provider-specific file (highest priority) - only if provider info available
            provider_name = self._get_provider_name()
            provider_type = self._get_active_provider_type()

            provider_specific_filename = self.get_templates_filename(provider_name, provider_type)
            provider_specific_path = self.config_manager.resolve_file(
                "template", provider_specific_filename
            )
            paths.append(provider_specific_path)

            # 2. Generic provider-type file (fallback)
            generic_filename = f"{provider_type}_templates.json"
            generic_path = self.config_manager.resolve_file("template", generic_filename)

            # Avoid duplicates
            if generic_path != provider_specific_path:
                paths.append(generic_path)

        except Exception as e:
            # Fallback to default paths if provider info not available
            from infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.debug(f"Failed to get provider info for path resolution: {e}")

        # 3. Default fallback paths (always available)
        default_paths = [
            self.config_manager.resolve_file("template", "aws_templates.json"),
            self.config_manager.resolve_file("template", "templates.json"),
        ]

        # Add default paths if not already included
        for default_path in default_paths:
            if default_path not in paths:
                paths.append(default_path)

        return paths

    def load_templates_from_path(self, template_path: str, provider_override: Any = None) -> list[dict[str, Any]]:
        """Load templates from a specific path."""
        if not os.path.exists(template_path):
            self.logger.debug("Template file not found: %s", template_path)
            return []

        try:
            templates = self._load_single_file(template_path)
            self.logger.debug("Loaded %d templates from %s", len(templates), template_path)
            return templates
        except Exception as e:
            self.logger.error("Error loading templates from %s: %s", template_path, e)
            return []

    def _load_single_file(self, template_path: str) -> list[dict[str, Any]]:
        """Load templates from a single file."""
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
            return []

    def _get_provider_name(self) -> str:
        """Get the active provider instance name via proper DI."""
        try:
            from application.services.provider_registry_service import ProviderRegistryService
            from infrastructure.di.container import get_container

            container = get_container()
            provider_service = container.get(ProviderRegistryService)
            selection_result = provider_service.select_active_provider()
            return selection_result.provider_name
        except Exception as e:
            self.logger.warning("Failed to get provider instance name: %s", e)
            return "default"

    def _get_active_provider_type(self) -> str:
        """Get the active provider type via proper DI."""
        try:
            from application.services.provider_registry_service import ProviderRegistryService
            from infrastructure.di.container import get_container

            container = get_container()
            provider_service = container.get(ProviderRegistryService)
            selection_result = provider_service.select_active_provider()
            provider_type = selection_result.provider_type
            self.logger.debug("Active provider type: %s", provider_type)
            return provider_type
        except Exception as e:
            self.logger.warning("Failed to get active provider type, defaulting to 'aws': %s", e)
            return "aws"

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
    ) -> dict[str, Any]:
        """
        Parse request data using native domain format - no conversion needed.

        Request data is expected to be in domain format already.
        """

        # Request Status
        if "requests" in raw_data:
            return {"requests": [{"request_id": req.get("request_id")} for req in raw_data["requests"]]}

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

    def format_templates_response(self, templates: list[Any]) -> dict[str, Any]:
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

    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details with default fields."""
        return {
            "id": machine_data.get("id"),
            "name": machine_data.get("name"),
            "status": machine_data.get("status"),
            "provider": "default",
            "instance_type": machine_data.get("instance_type"),
            "region": machine_data.get("region"),
            "image_id": machine_data.get("image_id"),
            "private_ip": machine_data.get("private_ip"),
            "public_ip": machine_data.get("public_ip"),
            "subnet_id": machine_data.get("subnet_id"),
            "security_group_ids": machine_data.get("security_group_ids"),
            "status_reason": machine_data.get("status_reason"),
            "launch_time": machine_data.get("launch_time"),
            "termination_time": machine_data.get("termination_time"),
            "tags": machine_data.get("tags"),
        }

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        import os

        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    def get_templates_filename(
        self, provider_name: str, provider_type: str, config: dict | None = None
    ) -> str:
        """Get templates filename with config override support."""
        if config:
            template_config = config.get("template", {})
            patterns = template_config.get("filename_patterns", {})

            # Use generic pattern for default scheduler
            if pattern := patterns.get("generic"):
                return pattern.format(provider_name=provider_name, provider_type=provider_type)

            # Check for explicit filename override
            if config_filename := template_config.get("templates_filename"):
                return config_filename

        # Hardcoded fallback for backward compatibility
        return "templates.json"

    def should_log_to_console(self) -> bool:
        """Check if logs should be written to console for Default scheduler.

        Default scheduler is interactive, always log to console.
        """
        return True

    def format_error_response(self, error: Exception, context: dict[str, Any]) -> dict[str, Any]:
        """Format error response for Default scheduler (console + JSON)."""
        import traceback

        # Log error
        self.logger.error("Scheduler error: %s", error)
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
