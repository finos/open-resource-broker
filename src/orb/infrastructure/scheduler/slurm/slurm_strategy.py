"""SLURM scheduler strategy — resource provider integration via power save hooks."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from orb.infrastructure.scheduler.slurm.field_mapper import SlurmFieldMapper
from orb.infrastructure.scheduler.slurm.response_formatter import SlurmResponseFormatter

if TYPE_CHECKING:
    from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort


class SlurmSchedulerStrategy(BaseSchedulerStrategy):
    """SLURM scheduler strategy for ResumeProgram/SuspendProgram power hooks."""

    def __init__(
        self,
        template_defaults_service: "TemplateDefaultsPort | None" = None,
        config_port: Any = None,
        logger: Any = None,
        provider_registry_service: Any = None,
        path_resolver: Any = None,
    ) -> None:
        """Initialize the instance."""
        self._template_defaults_service = template_defaults_service
        self._init_base(
            config_port=config_port,
            logger=logger,
            provider_registry_service=provider_registry_service,
            path_resolver=path_resolver,
        )
        self._field_mapper = SlurmFieldMapper()
        self._response_formatter = SlurmResponseFormatter()

    def get_scheduler_type(self) -> str:
        """Return the scheduler type identifier."""
        return "slurm"

    def get_scripts_directory(self) -> Path | None:
        """Return the path to the SLURM scripts directory."""
        return Path(__file__).parent / "scripts"

    def should_log_to_console(self) -> bool:
        """SLURM scheduler logs to console."""
        return True

    def get_config_file_path(self) -> str:
        """Get config file path for SLURM scheduler."""
        return self.config_manager.resolve_file("config", "slurm_config.json")

    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""
        workdir = self.get_working_directory()
        if file_type in ("config", "template", "legacy"):
            return os.path.join(workdir, "config")
        elif file_type == "log":
            return os.path.join(workdir, "logs")
        else:
            return workdir

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
                    "Could not delegate to '%s' strategy, loading best-effort",
                    file_scheduler_type,
                )

            raw_templates = self._load_single_file(template_path)
            provider_name = provider_override or self._get_provider_name()
            templates = [self._apply_template_defaults(t, provider_name) for t in raw_templates]
            self.logger.debug("Loaded %d templates from %s", len(templates), template_path)
            return templates
        except Exception as e:
            self.logger.error("Error loading templates from %s: %s", template_path, e)
            return []

    def parse_template_config(self, raw_data: dict[str, Any]) -> Any:
        """Parse SLURM partition data to TemplateDTO using field mapper."""
        from orb.infrastructure.template.dtos import TemplateDTO

        mapped = self._field_mapper.map_input_fields(raw_data)
        return TemplateDTO.from_dict(mapped)

    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        """Parse incoming SLURM resume/suspend request data."""
        # List of requests (status query)
        if "requests" in raw_data:
            return [
                {"request_id": req.get("request_id")}
                for req in raw_data["requests"]
            ]

        # Nested template format
        if "template" in raw_data:
            template_data = raw_data["template"]
            return {
                "template_id": template_data.get("template_id") or template_data.get("partition_name"),
                "requested_count": template_data.get("machine_count", 1),
                "request_type": template_data.get("request_type", "provision"),
                "node_names": template_data.get("node_names", []),
                "metadata": raw_data.get("metadata", {}),
            }

        # Flat format (e.g. from ResumeProgram with node list)
        return {
            "template_id": raw_data.get("template_id") or raw_data.get("partition_name"),
            "requested_count": raw_data.get("requested_count", raw_data.get("count", 1)),
            "request_type": raw_data.get("request_type", "provision"),
            "node_names": raw_data.get("node_names", []),
            "metadata": raw_data.get("metadata", {}),
        }

    def format_templates_response(self, templates: list[Any]) -> dict[str, Any]:
        """Format template DTOs to SLURM response."""
        return self._response_formatter.format_templates_response(templates)

    def format_templates_for_dispatch(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to SLURM format."""
        return self._field_mapper.format_for_generation(templates)

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response."""
        data = self._coerce_to_dict(request_data)
        return self._response_formatter.format_request_response(data)

    def format_machine_status_response(self, machines: list[Any]) -> dict[str, Any]:
        """Format machine DTOs to SLURM response."""
        return self._response_formatter.format_machine_status_response(machines)

    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details for CLI display."""
        return self._response_formatter.format_machine_details_response(machine_data)
