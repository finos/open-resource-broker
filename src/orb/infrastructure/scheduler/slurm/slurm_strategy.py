"""SLURM scheduler strategy — resource provider integration via power save hooks."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy

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

    def parse_template_config(self, raw_data: dict[str, Any]) -> Any:
        """Parse scheduler template config to template DTO."""
        raise NotImplementedError("SLURM: not yet implemented")

    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        """Parse scheduler request data to domain-compatible format."""
        raise NotImplementedError("SLURM: not yet implemented")

    def format_templates_response(self, templates: list[Any]) -> dict[str, Any]:
        """Format template DTOs to scheduler response."""
        raise NotImplementedError("SLURM: not yet implemented")

    def format_templates_for_dispatch(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to SLURM's expected input format."""
        raise NotImplementedError("SLURM: not yet implemented")

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response to scheduler format."""
        raise NotImplementedError("SLURM: not yet implemented")

    def format_machine_status_response(self, machines: list[Any]) -> dict[str, Any]:
        """Format machine DTOs to scheduler response."""
        raise NotImplementedError("SLURM: not yet implemented")

    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details for CLI display."""
        raise NotImplementedError("SLURM: not yet implemented")
