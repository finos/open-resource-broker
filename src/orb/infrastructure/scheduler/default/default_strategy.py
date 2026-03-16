"""Default scheduler strategy using native domain fields - no conversion needed."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orb.domain.machine.aggregate import Machine
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from orb.infrastructure.scheduler.default.field_mapper import DefaultFieldMapper

if TYPE_CHECKING:
    from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort


class DefaultSchedulerStrategy(BaseSchedulerStrategy):
    """
    Default scheduler strategy using native domain fields.

    This strategy uses templates and data in their native domain format
    without any field mapping or conversion. It serves as:
    - Reference implementation for scheduler strategies
    - Testing baseline with pure domain objects
    - Simple integration for systems using domain format directly
    """

    def __init__(
        self,
        template_defaults_service: "TemplateDefaultsPort | None" = None,
        config_port: "Any | None" = None,
        logger: "Any | None" = None,
        provider_registry_service: "Any | None" = None,
        path_resolver: "Any | None" = None,
    ) -> None:
        """Initialize the instance."""
        self._template_defaults_service = template_defaults_service
        self._init_base(
            config_port=config_port,
            logger=logger,
            provider_registry_service=provider_registry_service,
            path_resolver=path_resolver,
        )
        # Initialize field mapper
        self.field_mapper = DefaultFieldMapper()

    def get_scheduler_type(self) -> str:
        """Return the scheduler type identifier."""
        return "default"

    def get_scripts_directory(self) -> Path | None:
        """Default strategy has no scripts directory."""
        return None

    def _templates_filename_pattern_key(self) -> str:
        return "provider_type"

    def _templates_filename_fallback(self, provider_name: str, provider_type: str) -> str:
        return f"{provider_type}_templates.json"

    def load_templates_from_path(
        self, template_path: str, provider_override: Any = None
    ) -> list[dict[str, Any]]:
        """Load templates from a specific path."""
        if not os.path.exists(template_path):
            self.logger.debug("Template file not found: %s", template_path)
            return []

        try:
            import json

            with open(template_path) as f:
                data = json.load(f)

            file_scheduler_type = data.get("scheduler_type") if isinstance(data, dict) else None

            if file_scheduler_type and file_scheduler_type != self.get_scheduler_type():
                delegated = self._delegate_load_to_strategy(
                    file_scheduler_type, template_path, provider_override
                )
                if delegated is not None:
                    return delegated
                self.logger.warning(
                    "Could not delegate to '%s' strategy, loading best-effort without field mapping",
                    file_scheduler_type,
                )

            raw_templates = self._load_single_file_from_data(data)
            provider_name = provider_override or self._get_provider_name()
            templates = [self._apply_template_defaults(t, provider_name) for t in raw_templates]
            self.logger.debug("Loaded %d templates from %s", len(templates), template_path)
            return templates
        except Exception as e:
            self.logger.error("Error loading templates from %s: %s", template_path, e)
            return []

    def _load_single_file_from_data(self, data: Any) -> list[dict[str, Any]]:
        """Extract templates list from already-parsed JSON data."""
        if isinstance(data, dict) and "templates" in data:
            return data["templates"]
        elif isinstance(data, list):
            return data
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

    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Parse request data using native domain format - no conversion needed.

        Request data is expected to be in domain format already.
        """

        # Request Status
        if "requests" in raw_data:
            return [
                {"request_id": req.get("request_id") or req.get("requestId")}
                for req in raw_data["requests"]
            ]

        # Request Machines - handle nested format (both snake_case and HF camelCase):
        # {"template": {"template_id": ..., "machine_count": ...}}
        # {"template": {"templateId": ..., "machineCount": ...}}
        if "template" in raw_data:
            template_data = raw_data["template"]
            return {
                "template_id": template_data.get("template_id") or template_data.get("templateId"),
                "requested_count": template_data.get("machine_count")
                or template_data.get("machineCount", 1),
                "request_type": template_data.get("request_type")
                or template_data.get("requestType", "provision"),
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
            "total_count": len(templates),
        }

    def format_template_for_display(self, template: Any) -> dict[str, Any]:
        """Format template for display, adding required schema fields."""
        d = template.to_dict()
        # Schema requires max_capacity (alias for max_instances)
        if "max_capacity" not in d:
            d["max_capacity"] = d.get("max_instances", 1)
        # Schema requires instance_type - derive from machine_types if available
        if "instance_type" not in d:
            machine_types = d.get("machine_types", {})
            d["instance_type"] = next(iter(machine_types), "") if machine_types else ""
        return d

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

    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""
        import os

        workdir = self.get_working_directory()

        if file_type in ["config", "template", "legacy"]:
            return os.path.join(workdir, "config")
        elif file_type == "log":
            return os.path.join(workdir, "logs")
        elif file_type in ["work", "data"]:
            return workdir
        else:
            return workdir

    def format_request_response(self, request_data: Any) -> dict[str, Any]:
        """Format request creation response to native domain format."""
        request_dict = self._coerce_to_dict(request_data)

        # If this is a status/detail response, pass through the requests list and status
        if "requests" in request_dict:
            return {
                "requests": request_dict.get("requests", []),
                "status": request_dict.get("status", "complete"),
                "message": request_dict.get("message", "Status retrieved successfully"),
                "errors": request_dict.get("errors"),
            }

        # Get status and error info
        status = request_dict.get("status", "pending")
        error_message = request_dict.get("status_message") or request_dict.get("message")
        raw_id = request_dict.get("request_id", request_dict.get("requestId"))
        request_id = self._unwrap_request_id(raw_id)

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
            return {"request_id": request_id, "message": "Request submitted successfully."}
        elif status == "pending":
            return {"request_id": request_id, "message": "Request submitted successfully."}
        else:
            return {
                "request_id": request_id,
                "message": request_dict.get("message", "Request status unknown"),
            }
