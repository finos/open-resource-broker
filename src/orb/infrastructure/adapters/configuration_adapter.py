"""Configuration adapter implementing domain ConfigurationPort."""

import logging
from typing import Any, Optional

from orb.config.manager import ConfigurationManager
from orb.config.schemas.app_schema import AppConfig
from orb.config.schemas.common_schema import NamingConfig, RequestConfig
from orb.config.schemas.template_schema import TemplateConfig
from orb.domain.base.ports import ConfigurationPort
from orb.domain.constants import REQUEST_ID_PREFIX_ACQUIRE, REQUEST_ID_PREFIX_RETURN

_logger = logging.getLogger(__name__)


class ConfigurationAdapter(ConfigurationPort):
    """Infrastructure adapter implementing ConfigurationPort for domain layer."""

    def __init__(self, config_manager: ConfigurationManager) -> None:
        """Initialize with configuration manager."""
        self._config_manager = config_manager

    def get_app_config(self) -> dict[str, Any]:
        """Get structured application configuration."""
        return self._config_manager.app_config.model_dump()

    @property
    def app_config(self) -> "AppConfig":
        """Get application configuration object."""
        return self._config_manager.app_config

    def find_templates_file(self, provider_type: str) -> str:
        """Find templates file for given provider type."""
        return self._config_manager.find_templates_file(provider_type)

    def get_naming_config(self) -> dict[str, Any]:
        """Get naming configuration for domain layer."""
        try:
            config = self._config_manager.get_typed(NamingConfig)
            return {
                "patterns": {
                    "request_id": config.patterns.get("request_id", r"^(req-|ret-)[a-f0-9\-]{36}$"),
                    "ec2_instance": config.patterns.get("ec2_instance", r"^i-[a-f0-9]{8,17}$"),
                    "instance_type": config.patterns.get(
                        "instance_type", r"^[a-z0-9]+\.[a-z0-9]+$"
                    ),
                    "cidr_block": config.patterns.get(
                        "cidr_block", r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$"
                    ),
                },
                "prefixes": {
                    "request": (
                        config.prefixes.request
                        if hasattr(config.prefixes, "request")
                        else REQUEST_ID_PREFIX_ACQUIRE
                    ),
                    "return": (
                        config.prefixes.return_prefix
                        if hasattr(config.prefixes, "return_prefix")
                        else REQUEST_ID_PREFIX_RETURN
                    ),
                },
            }
        except Exception as e:
            _logger.warning("Failed to load naming config, using defaults: %s", e)
            return {
                "patterns": {
                    "request_id": r"^(req-|ret-)[a-f0-9\-]{36}$",
                    "ec2_instance": r"^i-[a-f0-9]{8,17}$",
                    "instance_type": r"^[a-z0-9]+\.[a-z0-9]+$",
                    "cidr_block": r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$",
                },
                "prefixes": {
                    "request": REQUEST_ID_PREFIX_ACQUIRE,
                    "return": REQUEST_ID_PREFIX_RETURN,
                },
            }

    def get_provider_config(self):
        """Get provider configuration - delegate to ConfigurationManager."""
        return self._config_manager.get_provider_config()

    def get_provider_instance_config(self, provider_name: str):
        """Get configuration for a specific provider instance."""
        return self._config_manager.get_provider_instance_config(provider_name)

    def get_request_config(self) -> dict[str, Any]:
        """Get request configuration for domain layer."""
        try:
            request_config = self._config_manager.get_typed(RequestConfig)
            return {
                "max_machines_per_request": getattr(
                    request_config, "max_machines_per_request", 100
                ),
                "default_timeout": getattr(request_config, "default_timeout", 300),
                "min_timeout": getattr(request_config, "min_timeout", 30),
                "max_timeout": getattr(request_config, "max_timeout", 3600),
                "fulfillment_max_retries": request_config.fulfillment_max_retries,
                "fulfillment_timeout_seconds": request_config.fulfillment_timeout_seconds,
                "fulfillment_batch_size": request_config.fulfillment_batch_size,
                "fulfillment_fallback_template_id": request_config.fulfillment_fallback_template_id,
            }
        except Exception as e:
            _logger.warning("Failed to load request config, using defaults: %s", e)
            return {
                "max_machines_per_request": 100,
                "default_timeout": 300,
                "min_timeout": 30,
                "max_timeout": 3600,
                "fulfillment_max_retries": 3,
                "fulfillment_timeout_seconds": 300,
                "fulfillment_batch_size": 1000,
                "fulfillment_fallback_template_id": None,
            }

    def get_template_config(self) -> dict[str, Any]:
        """Get template configuration."""
        try:
            template_config = self._config_manager.get_typed(TemplateConfig)
            return template_config.model_dump(exclude_none=True)
        except Exception as e:
            # Fallback to empty config if loading fails
            from orb.infrastructure.logging.logger import get_logger

            get_logger(__name__).warning("Failed to get template config: %s", e)
            return {}

    def get_metrics_config(self) -> dict[str, Any]:
        """Get metrics configuration."""

        # Defaults with nested aws_metrics
        defaults: dict[str, Any] = {
            "metrics_enabled": False,
            "metrics_dir": "./metrics",
            "metrics_interval": 60,
            "trace_enabled": False,
            "trace_buffer_size": 1000,
            "trace_file_max_size_mb": 10,
            "aws_metrics": {
                "aws_metrics_enabled": False,
                "sample_rate": 1.0,
                "monitored_services": [],
                "monitored_operations": [],
                "track_payload_sizes": False,
            },
        }

        try:
            # Get metrics section from raw config
            raw = self._config_manager._ensure_raw_config()  # type: ignore[attr-defined]
            metrics_config = raw.get("metrics", {}) if isinstance(raw, dict) else {}

            result: dict[str, Any] = defaults.copy()
            result["aws_metrics"] = defaults["aws_metrics"].copy()

            if isinstance(metrics_config, dict):
                result.update(
                    {k: metrics_config.get(k, v) for k, v in defaults.items() if k != "aws_metrics"}
                )
                if "aws_metrics" in metrics_config and isinstance(
                    metrics_config["aws_metrics"], dict
                ):
                    result["aws_metrics"].update(metrics_config["aws_metrics"])

            return result
        except Exception as e:
            _logger.warning("Failed to load metrics config, using defaults: %s", e)
            return defaults

    def get_storage_config(self) -> dict[str, Any]:
        """Get storage configuration."""
        try:
            storage_config = self._config_manager.get("storage", {})
            return {
                "type": storage_config.get("type", "json"),
                "path": storage_config.get("path", "data"),
                "backup_enabled": storage_config.get("backup_enabled", True),
            }
        except Exception as e:
            _logger.warning("Failed to load storage config, using defaults: %s", e)
            return {"type": "json", "path": "data", "backup_enabled": True}

    def get_events_config(self) -> dict[str, Any]:
        """Get events configuration."""
        try:
            events_config = self._config_manager.get("events", {})
            return {
                "enabled": events_config.get("enabled", True),
                "mode": events_config.get("mode", "logging"),
                "batch_size": events_config.get("batch_size", 10),
            }
        except Exception as e:
            _logger.warning("Failed to load events config, using defaults: %s", e)
            return {"enabled": True, "mode": "logging", "batch_size": 10}

    def get_logging_config(self) -> dict[str, Any]:
        """Get logging configuration."""
        try:
            logging_config = self._config_manager.get("logging", {})
            return {
                "level": logging_config.get("level", "INFO"),
                "format": logging_config.get(
                    "format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                ),
                "file_enabled": logging_config.get("file_enabled", True),
                "console_enabled": logging_config.get("console_enabled", True),
            }
        except Exception as e:
            _logger.warning("Failed to load logging config, using defaults: %s", e)
            return {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "file_enabled": True,
                "console_enabled": True,
            }

    def get_config_file_path(self) -> str:
        """Get the config file path from configuration."""
        return self._config_manager.get("config_file", "")

    def get_storage_strategy(self) -> str:
        """Get storage strategy - delegate to ConfigurationManager."""
        return self._config_manager.get_storage_strategy()

    def get_scheduler_strategy(self) -> str:
        """Get scheduler strategy - delegate to ConfigurationManager."""
        return self._config_manager.get_scheduler_strategy()

    def get_typed(self, key, _expected_type=None, _default=None):  # type: ignore[override]
        """Get typed configuration for compatibility with ConfigurationManager."""
        return self._config_manager.get_typed(key)  # type: ignore[arg-type]

    def resolve_file(
        self, path: str, filename: str = "", explicit_path: Optional[str] = None
    ) -> str:  # type: ignore[override]
        """Resolve file path for compatibility with ConfigurationManager."""
        return self._config_manager.resolve_file(path, filename, explicit_path)

    def get_provider_type(self) -> str:
        """Get provider type - delegate to ConfigurationManager."""
        return self._config_manager.get_provider_type()

    def get_work_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get work directory - delegate to ConfigurationManager."""
        return self._config_manager.get_work_dir(default_path, config_path)

    def get_conf_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get config directory - delegate to ConfigurationManager."""
        return self._config_manager.get_conf_dir(default_path, config_path)

    def get_log_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get log directory - delegate to ConfigurationManager."""
        return self._config_manager.get_log_dir(default_path, config_path)

    def get_cache_dir(self) -> str:
        """Get cache directory - delegate to ConfigurationManager."""
        return self._config_manager.get_cache_dir()

    def get_native_spec_config(self) -> dict[str, Any]:
        """Get native spec configuration."""
        try:
            from orb.config.schemas.native_spec_schema import NativeSpecConfig

            config = self._config_manager.get_typed(NativeSpecConfig)
            return {"enabled": config.enabled, "merge_mode": config.merge_mode}
        except Exception as e:
            _logger.warning("Failed to load native spec config, using defaults: %s", e)
            return {"enabled": False, "merge_mode": "merge"}

    def get_package_info(self) -> dict[str, Any]:
        """Get package metadata information."""
        try:
            from orb._package import AUTHOR, DESCRIPTION, PACKAGE_NAME, __version__

            return {
                "name": PACKAGE_NAME,
                "version": __version__,
                "description": DESCRIPTION,
                "author": AUTHOR,
            }
        except ImportError:
            # If _package.py itself fails, we have bigger problems - let it fail
            raise

    def override_scheduler_strategy(self, strategy: str) -> None:  # type: ignore[override]
        """Override scheduler strategy - delegate to ConfigurationManager."""
        self._config_manager.override_scheduler_strategy(strategy)

    def override_provider_instance(self, provider_name: str) -> None:
        """Override provider instance - delegate to ConfigurationManager."""
        self._config_manager.override_provider_instance(provider_name)

    def override_provider_region(self, region: str) -> None:
        """Override provider region - delegate to ConfigurationManager."""
        self._config_manager.override_provider_region(region)

    def override_provider_profile(self, profile: str) -> None:
        """Override provider credential profile - delegate to ConfigurationManager."""
        self._config_manager.override_provider_profile(profile)

    def get_effective_region(self, default_region: str = "") -> str:
        """Get effective provider region - delegate to ConfigurationManager."""
        return self._config_manager.get_effective_region(default_region)

    def get_effective_profile(self, default_profile: str = "") -> str:
        """Get effective provider credential profile - delegate to ConfigurationManager."""
        return self._config_manager.get_effective_profile(default_profile)

    def get_resource_prefix(self, resource_type: str) -> str:
        """Get resource naming prefix for the given resource type."""
        try:
            resource_config = self._config_manager.app_config.resource
            if hasattr(resource_config.prefixes, resource_type):
                return getattr(resource_config.prefixes, resource_type)
            return resource_config.default_prefix
        except Exception as e:
            _logger.warning(
                "Failed to get resource prefix for '%s', using empty prefix: %s", resource_type, e
            )
            return ""

    def get_active_provider_override(self) -> str | None:
        """Get current provider override from CLI."""
        return self._config_manager.get_active_provider_override()

    def get_configuration_value(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config_manager.get(key, default)

    def set_configuration_value(self, key: str, value: Any) -> None:
        """Set configuration value."""
        self._config_manager.set(key, value)

    def get_configuration_sources(self) -> dict[str, Any]:
        """Get configuration source information using existing methods."""
        # Get actual loaded config file
        actual_config_file = self._config_manager.get_loaded_config_file()

        # Get active template file from scheduler
        template_file = self._get_active_template_file()

        return {
            "config_file": actual_config_file,
            "template_file": template_file,
            "config_dir": self.get_conf_dir(),
            "work_dir": self.get_work_dir(),
            "primary_source": "config_file" if actual_config_file else "environment",
        }

    def _get_active_template_file(self) -> str | None:
        """Get active template file from scheduler."""
        try:
            from orb.infrastructure.di.container import get_container
            from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory

            container = get_container()
            scheduler_factory = container.get(SchedulerStrategyFactory)
            scheduler_type = self.get_scheduler_strategy()
            scheduler = scheduler_factory.create_strategy(scheduler_type, container)

            template_paths = scheduler.get_template_paths()
            for path in template_paths:
                import os

                if os.path.exists(path):
                    return path
            return None
        except Exception as e:
            _logger.debug("Could not determine active template file: %s", e)
            return None
