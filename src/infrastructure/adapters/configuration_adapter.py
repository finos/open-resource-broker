"""Configuration adapter implementing domain ConfigurationPort."""

from typing import Any, Dict

from src.config import NamingConfig, RequestConfig, TemplateConfig
from src.config.manager import ConfigurationManager
from src.domain.base.ports import ConfigurationPort


class ConfigurationAdapter(ConfigurationPort):
    """Infrastructure adapter implementing ConfigurationPort for domain layer."""

    def __init__(self, config_manager: ConfigurationManager):
        """Initialize with configuration manager."""
        self._config_manager = config_manager

    def get_naming_config(self) -> Dict[str, Any]:
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

    def get_validation_config(self) -> Dict[str, Any]:
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

    def get_provider_config(self, provider_type: str) -> Dict[str, Any]:
        """Get provider-specific configuration for domain layer."""
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
            # Fallback provider config
            return {
                "default_instance_tags": {},
                "default_image_id": "",
                "default_instance_type": "t2.micro",
            }

    def get_request_config(self) -> Dict[str, Any]:
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

    def get_template_config(self) -> Dict[str, Any]:
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

    def get_storage_config(self) -> Dict[str, Any]:
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

    def get_events_config(self) -> Dict[str, Any]:
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

    def get_logging_config(self) -> Dict[str, Any]:
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
