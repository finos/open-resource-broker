"""Base scheduler strategy interface.

This module provides the base abstract class for all scheduler strategies,
ensuring consistent interface implementation across different scheduler types.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.request.dto import RequestDTO

# Import from Application layer (correct Clean Architecture)
from orb.domain.base.ports.path_resolution_port import PathResolutionPort
from orb.domain.constants import PROVIDER_TYPE_AWS
from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort
from orb.infrastructure.template.dtos import TemplateDTO

if TYPE_CHECKING:
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.domain.base.ports.logging_port import LoggingPort


class BaseSchedulerStrategy(SchedulerPort, ABC):
    """Base class for all scheduler strategies.

    This abstract base class defines the common interface and behavior
    that all scheduler strategy implementations must provide.

    Inherits from SchedulerPort which defines all the required abstract methods.
    """

    _template_defaults_service: TemplateDefaultsPort | None = None

    def _init_base(
        self,
        config_port: "ConfigurationPort | None" = None,
        logger: "LoggingPort | None" = None,
        provider_registry_service: "ProviderRegistryService | None" = None,
        path_resolver: "PathResolutionPort | None" = None,
    ) -> None:
        """Initialise base dependencies. Call from subclass __init__."""
        self._config_manager = config_port
        self._logger = logger
        self._provider_registry_service = provider_registry_service
        self._path_resolver = path_resolver

    @property
    def config_manager(self) -> Any:
        """Configuration manager instance."""
        return self._config_manager

    @property
    def logger(self) -> Any:
        """Logger instance, with fallback to module logger when not injected."""
        if self._logger is None:
            from orb.infrastructure.logging.logger import get_logger

            return get_logger(__name__)
        return self._logger

    def _get_provider_name(self) -> str:
        """Get the active provider instance name."""
        try:
            if self._provider_registry_service is None:
                return "default"
            selection_result = self._provider_registry_service.select_active_provider()
            return selection_result.provider_name
        except Exception as e:
            self.logger.warning("Failed to get provider instance name: %s", e)
            return "default"

    def _get_active_provider_type(self) -> str:
        """Get the active provider type."""
        try:
            if self._provider_registry_service is None:
                return PROVIDER_TYPE_AWS
            selection_result = self._provider_registry_service.select_active_provider()
            provider_type = selection_result.provider_type
            self.logger.debug("Active provider type: %s", provider_type)
            return provider_type
        except Exception as e:
            self.logger.warning("Failed to get active provider type, defaulting to 'aws': %s", e)
            return PROVIDER_TYPE_AWS

    def _load_single_file(self, template_path: str) -> list[dict[str, Any]]:
        """Load templates from a single file."""
        try:
            import json

            with open(template_path) as f:
                data = json.load(f)
            if isinstance(data, dict) and "templates" in data:
                return data["templates"]
            elif isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        import os

        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    def get_exit_code_for_status(self, status: str) -> int:
        """Get appropriate exit code for request status."""
        problem_statuses = ["failed", "cancelled", "timeout", "partial"]
        return 1 if status in problem_statuses else 0

    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        """Format health check response."""
        passed = sum(1 for c in checks if c.get("status") == "pass")
        failed = len(checks) - passed
        return {
            "success": failed == 0,
            "checks": checks,
            "summary": {"total": len(checks), "passed": passed, "failed": failed},
        }

    def _apply_template_defaults(self, template_dict: dict, provider_name: str) -> dict:
        """Apply template defaults via the template defaults service if available."""
        if self._template_defaults_service is None:
            return template_dict
        return self._template_defaults_service.resolve_template_defaults(
            template_dict, provider_name
        )

    def format_request_status_response(self, requests: list[RequestDTO]) -> dict[str, Any]:
        """Format RequestDTOs passing domain status values through unchanged.

        Domain statuses: pending, in_progress, complete, failed, cancelled, timeout, partial.
        Protocol-specific strategies (e.g. HostFactory) override this method to apply
        their own status vocabulary mapping.
        """
        formatted = []
        for request in requests:
            formatted.append(request.to_dict())
        return {
            "requests": formatted,
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
        # 3. Injected config — ORB_LOG_LEVEL resolved by _load_from_env
        if self._config_manager is not None:
            if level := self._config_manager.get_logging_config().get("level"):
                return level
        # 4. Hard default
        return "INFO"

    def _coalesce_directory(
        self, config_override: str | None, env_var_name: str, default_factory: "Callable[[], str]"
    ) -> str:
        """Coalesce directory from multiple sources."""
        # 1. Config override
        if config_override:
            return config_override

        # 2. Scheduler-specific env var
        if scheduler_var := self._get_scheduler_env_var(env_var_name):
            return scheduler_var

        # 3. Default factory
        return default_factory()

    def _get_scheduler_env_var(self, suffix: str) -> str | None:
        """Get scheduler-specific env var. Override in subclass if needed."""
        return None

    def _get_path_resolver(self) -> "PathResolutionPort":
        if self._path_resolver is None:
            from orb.infrastructure.adapters.path_resolution_adapter import PathResolutionAdapter

            self._path_resolver = PathResolutionAdapter()
        return self._path_resolver

    def _get_platform_config_dir(self) -> str:
        """Get platform default config directory."""
        return self._get_path_resolver().get_config_dir()

    def _get_platform_work_dir(self) -> str:
        """Get platform default work directory."""
        return self._get_path_resolver().get_work_dir()

    def _get_platform_logs_dir(self) -> str:
        """Get platform default logs directory."""
        return self._get_path_resolver().get_logs_dir()

    @abstractmethod
    def get_scripts_directory(self) -> Path | None:
        """Return the path to the scheduler's scripts directory, or None if not applicable."""
        ...

    def get_template_paths(self) -> list[str]:
        """Get template file paths with fallback hierarchy."""
        paths = []

        try:
            provider_name = self._get_provider_name()
            provider_type = self._get_active_provider_type()

            provider_specific_filename = self.get_templates_filename(provider_name, provider_type)
            provider_specific_path = self.config_manager.resolve_file(
                "template", provider_specific_filename
            )
            paths.append(provider_specific_path)

            generic_filename = f"{provider_type}_templates.json"
            generic_path = self.config_manager.resolve_file("template", generic_filename)

            if generic_path != provider_specific_path:
                paths.append(generic_path)

        except Exception as e:
            from orb.infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.debug(f"Failed to get provider info for path resolution: {e}")

        default_paths = [
            self.config_manager.resolve_file("template", "aws_templates.json"),
            self.config_manager.resolve_file("template", "templates.json"),
        ]

        for default_path in default_paths:
            if default_path not in paths:
                paths.append(default_path)

        return paths

    def get_templates_filename(
        self, provider_name: str, provider_type: str, config: dict | None = None
    ) -> str:
        """Get templates filename with config override support."""
        if config:
            template_config = config.get("template", {})
            patterns = template_config.get("filename_patterns", {})

            if pattern := patterns.get(self._templates_filename_pattern_key()):
                return pattern.format(provider_name=provider_name, provider_type=provider_type)

            if config_filename := template_config.get("templates_filename"):
                return config_filename

        return self._templates_filename_fallback(provider_name, provider_type)

    def _templates_filename_pattern_key(self) -> str:
        """Pattern key to look up in config filename_patterns. Override per strategy."""
        return "provider_type"

    def _templates_filename_fallback(self, provider_name: str, provider_type: str) -> str:
        """Fallback filename when no config is present. Override per strategy."""
        return f"{provider_type}_templates.json"

    def format_template_for_display(self, template: TemplateDTO) -> dict[str, Any]:
        """Default implementation - clean to_dict without scheduler-specific formatting."""
        return template.to_dict()

    def format_template_for_provider(self, template: TemplateDTO) -> dict[str, Any]:
        """Default implementation - clean to_dict without scheduler-specific formatting."""
        return template.to_dict()

    @staticmethod
    def _unwrap_request_id(value: Any) -> str | None:
        """Unwrap a request_id that may be a plain value, a dict with 'value', or an object with .value."""
        if value is None:
            return None
        if isinstance(value, dict) and "value" in value:
            return str(value["value"])
        if not isinstance(value, (str, int, float, bool, dict)) and hasattr(value, "value"):
            return str(value.value)
        return str(value)

    def _delegate_load_to_strategy(
        self, scheduler_type: str, template_path: str, provider_override: Any = None
    ) -> "list[dict[str, Any]] | None":
        """Delegate template loading to the strategy that owns the given scheduler type.

        Uses the scheduler registry to resolve the correct strategy class, then
        constructs a minimal instance with the same injected dependencies so that
        template defaults are applied consistently.

        Returns None when the scheduler type is not registered or construction fails,
        allowing the caller to fall back to best-effort loading.
        """
        from orb.infrastructure.scheduler.registry import get_scheduler_registry

        registry = get_scheduler_registry()
        if not registry.is_registered(scheduler_type):
            self.logger.warning(
                "Scheduler type '%s' not registered, cannot delegate template loading",
                scheduler_type,
            )
            return None

        try:
            strategy_class = registry.get_strategy_class(scheduler_type)
            delegate = strategy_class(
                template_defaults_service=self._template_defaults_service,
                config_port=self._config_manager,
                logger=self._logger,
                provider_registry_service=self._provider_registry_service,
                path_resolver=self._path_resolver,
            )
            return delegate.load_templates_from_path(template_path, provider_override)
        except Exception as e:
            self.logger.warning(
                "Failed to delegate template loading to '%s' strategy: %s", scheduler_type, e
            )
            return None

    @staticmethod
    def _coerce_to_dict(request_data: Any) -> dict[str, Any]:
        """Coerce request_data to a plain dict regardless of its type."""
        if hasattr(request_data, "model_dump"):
            return request_data.model_dump()
        if hasattr(request_data, "to_dict"):
            return request_data.to_dict()
        if isinstance(request_data, dict):
            return request_data
        try:
            return dict(request_data)
        except Exception:
            return {}
