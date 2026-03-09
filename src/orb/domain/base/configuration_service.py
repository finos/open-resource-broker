"""Domain configuration service using ConfigurationPort."""

from orb.domain.base.ports.configuration_port import ConfigurationPort


class DomainConfigurationService:
    """Domain service for accessing configuration through ports."""

    def __init__(self, config_port: ConfigurationPort) -> None:
        """Initialize with configuration port."""
        self._config_port = config_port

    def get_request_id_prefix(self, request_type: str) -> str:
        """Get request ID prefix for the given type."""
        naming_config = self._config_port.get_naming_config()
        prefixes = naming_config.get("prefixes", {})

        if request_type.lower() in ["acquire", "request"]:
            return str(prefixes.get("request", "req-"))
        else:
            return str(prefixes.get("return", "ret-"))

    def get_request_id_pattern(self) -> str:
        """Get request ID validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return str(patterns.get("request_id", r"^(req-|ret-)[a-f0-9\-]{36}$"))

    def get_machine_id_pattern(self) -> str:
        """Get machine ID validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return str(patterns.get("ec2_instance", r"^i-[a-f0-9]{8,17}$"))

    def get_instance_type_pattern(self) -> str:
        """Get instance type validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return str(patterns.get("instance_type", r"^[a-z0-9]+\.[a-z0-9]+$"))

    def get_cidr_block_pattern(self) -> str:
        """Get CIDR block validation pattern."""
        naming_config = self._config_port.get_naming_config()
        patterns = naming_config.get("patterns", {})
        return str(patterns.get("cidr_block", r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$"))

    def get_default_timeout(self) -> int:
        """Get default request timeout."""
        validation_config = self._config_port.get_validation_config()
        return int(validation_config.get("default_timeout", 300))

    def get_max_machines_per_request(self) -> int:
        """Get maximum machines per request."""
        validation_config = self._config_port.get_validation_config()
        return int(validation_config.get("max_machines_per_request", 100))

    def get_default_instance_tags(self, provider_type: str) -> dict[str, str]:
        """Get default instance tags for the specified provider."""
        provider_config = self._config_port.get_provider_instance_config(provider_type)
        tags = provider_config.get("default_instance_tags", {})
        return {str(k): str(v) for k, v in tags.items()}

