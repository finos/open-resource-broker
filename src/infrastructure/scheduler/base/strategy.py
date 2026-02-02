"""Base scheduler strategy interface.

This module provides the base abstract class for all scheduler strategies,
ensuring consistent interface implementation across different scheduler types.
"""

from abc import ABC
from typing import Any

# Import from Application layer (correct Clean Architecture)
from domain.base.ports.scheduler_port import SchedulerPort
from application.dto.responses import MachineDTO
from application.request.dto import RequestDTO
from infrastructure.template.dtos import TemplateDTO


class BaseSchedulerStrategy(SchedulerPort, ABC):
    """Base class for all scheduler strategies.

    This abstract base class defines the common interface and behavior
    that all scheduler strategy implementations must provide.

    Inherits from SchedulerPort which defines all the required abstract methods.
    """

    def __init__(self, config_manager: Any, logger: Any) -> None:
        """Initialize base scheduler strategy.

        Args:
            config_manager: Configuration manager instance
            logger: Logger instance for this strategy
        """
        self.config_manager = config_manager
        self.logger = logger

    def format_request_status_response(self, requests: list[RequestDTO]) -> dict[str, Any]:
        """
        Format RequestDTOs to native domain response format.

        Uses the RequestDTO's to_dict() method to serialize to native format.
        """
        return {
            "requests": [request.to_dict() for request in requests],
            "message": "Request status retrieved successfully",
            "count": len(requests),
        }

    def get_config_directory(self) -> str:
        """Get config directory with coalesce pattern."""
        return self._coalesce_directory(
            config_override=getattr(self.config_manager.app_config.scheduler, "config_dir", None),
            env_var_name="CONFIG_DIR",
            default_factory=lambda: self._get_platform_config_dir(),
        )

    def get_working_directory(self) -> str:
        """Get working directory with coalesce pattern."""
        return self._coalesce_directory(
            config_override=getattr(self.config_manager.app_config.scheduler, "work_dir", None),
            env_var_name="WORK_DIR",
            default_factory=lambda: self._get_platform_work_dir(),
        )

    def get_logs_directory(self) -> str:
        """Get logs directory with coalesce pattern."""
        return self._coalesce_directory(
            config_override=getattr(self.config_manager.app_config.scheduler, "log_dir", None),
            env_var_name="LOG_DIR",
            default_factory=lambda: self._get_platform_logs_dir(),
        )

    def get_log_level(self) -> str:
        """Get log level with coalesce pattern."""
        # 1. Config override
        if level := getattr(self.config_manager.app_config.scheduler, "log_level", None):
            return level
        # 2. Scheduler-specific env
        if level := self._get_scheduler_env_var("LOG_LEVEL"):
            return level
        # 3. Standard ORB env
        import os

        return os.environ.get("ORB_LOG_LEVEL", "INFO")

    def _coalesce_directory(
        self, config_override: str | None, env_var_name: str, default_factory: callable
    ) -> str:
        """Coalesce directory from multiple sources."""
        import os

        # 1. Config override
        if config_override:
            return config_override

        # 2. Scheduler-specific env var
        if scheduler_var := self._get_scheduler_env_var(env_var_name):
            return scheduler_var

        # 3. Standard ORB env var
        if orb_var := os.environ.get(f"ORB_{env_var_name}"):
            return orb_var

        # 4. Default factory
        return default_factory()

    def _get_scheduler_env_var(self, suffix: str) -> str | None:
        """Get scheduler-specific env var. Override in subclass if needed."""
        return None

    def _get_platform_config_dir(self) -> str:
        """Get platform default config directory."""
        from config.platform_dirs import get_config_location

        return str(get_config_location())

    def _get_platform_work_dir(self) -> str:
        """Get platform default work directory."""
        from config.platform_dirs import get_work_location

        return str(get_work_location())

    def _get_platform_logs_dir(self) -> str:
        """Get platform default logs directory."""
        from config.platform_dirs import get_logs_location

        return str(get_logs_location())

    def format_template_for_display(self, template: TemplateDTO) -> dict[str, Any]:
        """Default implementation - clean to_dict without scheduler-specific formatting."""
        return template.to_dict()

    def format_template_for_provider(self, template: TemplateDTO) -> dict[str, Any]:
        """Default implementation - clean to_dict without scheduler-specific formatting."""
        return template.to_dict()

    def format_request_for_display(self, request: RequestDTO) -> dict[str, Any]:
        """Default implementation - clean to_dict without scheduler-specific formatting."""
        return request.to_dict()
