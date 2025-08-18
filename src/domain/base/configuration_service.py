"""Domain configuration service using ConfigurationPort."""

from typing import Dict, Optional

from domain.ports import ConfigurationPort


class DomainConfigurationService:
    """Domain service for accessing configuration through ports."""

    def __init__(self, config_port: ConfigurationPort):
        """Initialize with configuration port."""
        self._config_port = config_port

    def get_request_id_prefix(self, request_type: str) -> str:
        """Get request ID prefix for the given type."""
        naming_config = self._config_port.get_naming_config()
        prefixes = naming_config.get("prefixes", {})

        if request_type.lower() in ["acquire", "request"]:
            return prefixes.get("request", "req-")
        else:
            return prefixes.get("return", "ret-")

    def get_request_id_pattern(self) -> str:
        """Get request ID validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return patterns.get("request_id", r"^(req-|ret-)[a-f0-9\-]{36}$")

    def get_machine_id_pattern(self) -> str:
        """Get machine ID validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return patterns.get("ec2_instance", r"^i-[a-f0-9]{8,17}$")

    def get_instance_type_pattern(self) -> str:
        """Get instance type validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return patterns.get("instance_type", r"^[a-z0-9]+\.[a-z0-9]+$")

    def get_cidr_block_pattern(self) -> str:
        """Get CIDR block validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return patterns.get("cidr_block", r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$")

    def get_default_timeout(self) -> int:
        """Get default request timeout."""
        validation_config = self._config_port.get_validation_config()
        return validation_config.get("default_timeout", 300)

    def get_max_machines_per_request(self) -> int:
        """Get maximum machines per request."""
        validation_config = self._config_port.get_validation_config()
        return validation_config.get("max_machines_per_request", 100)

    def get_default_instance_tags(self, provider_type: str) -> Dict[str, str]:
        """Get default instance tags for the specified provider."""
        provider_config = self._config_port.get_provider_config(provider_type)
        return provider_config.get("default_instance_tags", {})


# Global service instance for easy access
_domain_config_service: Optional[DomainConfigurationService] = None


def set_domain_config_service(service: DomainConfigurationService) -> None:
    """Set the global domain configuration service."""
    global _domain_config_service
    _domain_config_service = service


def get_domain_config_service() -> Optional[DomainConfigurationService]:
    """Get the global domain configuration service."""
    return _domain_config_service
