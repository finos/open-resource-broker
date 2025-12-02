"""Configuration adapter implementing domain ConfigurationPort."""

from typing import Any, Optional

from config import NamingConfig, RequestConfig, TemplateConfig
from config.manager import ConfigurationManager
from domain.base.ports import ConfigurationPort


class ConfigurationAdapter(ConfigurationPort):
    """Infrastructure adapter implementing ConfigurationPort for domain layer."""

    def __init__(self, config_manager: ConfigurationManager) -> None:
        """Initialize with configuration manager."""
        self._config_manager = config_manager

    def get_app_config(self) -> dict[str, Any]:
        """Get structured application configuration."""
        return self._config_manager.get_app_config()

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
                        config.prefixes.request if hasattr(config.prefixes, "request") else "req-"
                    ),
                    "return": (
                        config.prefixes.return_prefix
                        if hasattr(config.prefixes, "return_prefix")
                        else "ret-"
                    ),
                },
            }
        except Exception:
            # Fallback configuration if config not available
            return {
                "patterns": {
                    "request_id": r"^(req-|ret-)[a-f0-9\-]{36}$",
                    "ec2_instance": r"^i-[a-f0-9]{8,17}$",
                    "instance_type": r"^[a-z0-9]+\.[a-z0-9]+$",
                    "cidr_block": r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$",
                },
                "prefixes": {"request": "req-", "return": "ret-"},
            }

    def get_validation_config(self) -> dict[str, Any]:
        """Get validation configuration for domain layer."""
        try:
            request_config = self._config_manager.get_typed(RequestConfig)
            return {
                "max_machines_per_request": getattr(
                    request_config, "max_machines_per_request", 100
                ),
                "default_timeout": getattr(request_config, "default_timeout", 300),
                "min_timeout": getattr(request_config, "min_timeout", 30),
                "max_timeout": getattr(request_config, "max_timeout", 3600),
            }
        except Exception:
            # Fallback validation config
            return {
                "max_machines_per_request": 100,
                "default_timeout": 300,
                "min_timeout": 30,
                "max_timeout": 3600,
            }

    def get_provider_config(self):
        """Get provider configuration - delegate to ConfigurationManager."""
        return self._config_manager.get_provider_config()

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
            }
        except Exception:
            return {
                "max_machines_per_request": 100,
                "default_timeout": 300,
                "min_timeout": 30,
                "max_timeout": 3600,
            }

    def get_template_config(self) -> dict[str, Any]:
        """Get template configuration."""
        try:
            template_config = self._config_manager.get_typed(TemplateConfig)
            return {
                "default_instance_tags": getattr(template_config, "default_instance_tags", {}),
                "default_image_id": getattr(template_config, "default_image_id", ""),
                "default_instance_type": getattr(
                    template_config, "default_instance_type", "t2.micro"
                ),
            }
        except Exception:
            return {
                "default_instance_tags": {},
                "default_image_id": "",
                "default_instance_type": "t2.micro",
            }

    def get_metrics_config(self) -> dict[str, Any]:
        """Get metrics configuration."""

        # Defaults with nested aws_metrics
        defaults: dict[str, Any] = {
            "metrics_enabled": False,
            "metrics_dir": "./metrics",
            "metrics_interval": 60,
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
                result.update({k: metrics_config.get(k, v) for k, v in defaults.items() if k != "aws_metrics"})
                if "aws_metrics" in metrics_config and isinstance(metrics_config["aws_metrics"], dict):
                    result["aws_metrics"].update(metrics_config["aws_metrics"])

            return result
        except Exception:
            # Fallback configuration
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
        except Exception:
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
        except Exception:
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
            }
        except Exception:
            return {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "file_enabled": True,
            }

    def get_storage_strategy(self) -> str:
        """Get storage strategy - delegate to ConfigurationManager."""
        return self._config_manager.get_storage_strategy()

    def get_scheduler_strategy(self) -> str:
        """Get scheduler strategy - delegate to ConfigurationManager."""
        return self._config_manager.get_scheduler_strategy()

    def get_typed(self, config_type):
        """Get typed configuration for compatibility with ConfigurationManager."""
        return self._config_manager.get_typed(config_type)

    def resolve_file(
        self, file_type: str, filename: str, explicit_path: Optional[str] = None
    ) -> str:
        """Resolve file path for compatibility with ConfigurationManager."""
        return self._config_manager.resolve_file(file_type, filename, explicit_path)

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

    def get_native_spec_config(self) -> dict[str, Any]:
        """Get native spec configuration."""
        try:
            from config.schemas.native_spec_schema import NativeSpecConfig

            config = self._config_manager.get_typed(NativeSpecConfig)
            return {"enabled": config.enabled, "merge_mode": config.merge_mode}
        except Exception:
            # Fallback configuration if config not available
            return {"enabled": False, "merge_mode": "merge"}

    def get_package_info(self) -> dict[str, Any]:
        """Get package metadata information."""
        try:
            from _package import AUTHOR, DESCRIPTION, PACKAGE_NAME, __version__

            return {
                "name": PACKAGE_NAME,
                "version": __version__,
                "description": DESCRIPTION,
                "author": AUTHOR,
            }
        except ImportError:
            # If _package.py itself fails, we have bigger problems - let it fail
            raise
