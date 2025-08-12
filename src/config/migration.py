"""Configuration migration utilities for consolidated provider configuration."""

import logging
from typing import Any, Dict, List, Optional

from .schemas.provider_strategy_schema import ProviderConfig


class ConfigurationMigrator:
    """Migrate configurations to consolidated provider format."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        """Initialize configuration migrator."""
        self._logger = logger or logging.getLogger(__name__)

    def migrate_to_consolidated_format(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate any configuration to consolidated provider format.

        Args:
            config_data: Original configuration data

        Returns:
            Configuration data in consolidated format
        """
        provider_config = config_data.get("provider", {})

        # Already in consolidated format
        if "providers" in provider_config:
            self._logger.debug("Configuration already in consolidated format")
            return config_data

        # Legacy AWS format
        if provider_config.get("type") == "aws":
            self._logger.info("Migrating legacy AWS configuration to consolidated format")
            return self._migrate_legacy_aws(config_data)

        # No provider configuration
        if not provider_config:
            self._logger.warning("No provider configuration found")
            return config_data

        # Unknown format
        self._logger.warning(f"Unknown provider configuration format: {provider_config}")
        return config_data

    def _migrate_legacy_aws(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate legacy AWS configuration to consolidated format.

        Args:
            config_data: Original configuration with legacy AWS format

        Returns:
            Configuration with consolidated provider format
        """
        aws_config = config_data["provider"].get("aws", {})

        # Create consolidated provider configuration
        consolidated_provider_config = {
            "active_provider": "aws-default",  # Single provider mode
            "selection_policy": "FIRST_AVAILABLE",
            "health_check_interval": 300,
            "circuit_breaker": {
                "enabled": True,
                "failure_threshold": 5,
                "recovery_timeout": 60,
            },
            "providers": [
                {
                    "name": "aws-default",
                    "type": "aws",
                    "enabled": True,
                    "priority": 1,
                    "weight": 100,
                    "config": aws_config,
                    "capabilities": self._infer_aws_capabilities(aws_config),
                    "health_check": {
                        "enabled": True,
                        "interval": 300,
                        "timeout": 30,
                        "retry_count": 3,
                    },
                }
            ],
        }

        # Create migrated configuration
        migrated_config = config_data.copy()
        migrated_config["provider"] = consolidated_provider_config

        self._logger.info("Successfully migrated legacy AWS configuration")
        return migrated_config

    def _infer_aws_capabilities(self, aws_config: Dict[str, Any]) -> List[str]:
        """
        Infer AWS capabilities from configuration.

        Args:
            aws_config: AWS configuration

        Returns:
            List of inferred capabilities
        """
        capabilities = ["instances"]  # Basic capability

        # Add capabilities based on configuration
        if aws_config.get("spot_fleet_enabled", False):
            capabilities.append("spot_instances")

        if aws_config.get("auto_scaling_enabled", False):
            capabilities.append("auto_scaling")

        if aws_config.get("load_balancer_enabled", False):
            capabilities.append("load_balancers")

        return capabilities

    def create_multi_region_aws_config(
        self,
        regions: List[str],
        profile: str = "default",
        base_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create multi-region AWS configuration.

        Args:
            regions: List of AWS regions
            profile: AWS profile to use
            base_config: Base AWS configuration to extend

        Returns:
            Multi-provider configuration for multiple AWS regions
        """
        base_aws_config = base_config or {}
        providers = []

        for i, region in enumerate(regions):
            provider_config = base_aws_config.copy()
            provider_config["region"] = region
            provider_config["profile"] = profile

            provider = {
                "name": f"aws-{region}",
                "type": "aws",
                "enabled": True,
                "priority": i + 1,
                "weight": 100 // len(regions),  # Distribute weight evenly
                "config": provider_config,
                "capabilities": self._infer_aws_capabilities(provider_config),
                "health_check": {
                    "enabled": True,
                    "interval": 300,
                    "timeout": 30,
                    "retry_count": 3,
                },
            }
            providers.append(provider)

        return {
            "provider": {
                "selection_policy": "ROUND_ROBIN",
                "health_check_interval": 300,
                "circuit_breaker": {
                    "enabled": True,
                    "failure_threshold": 5,
                    "recovery_timeout": 60,
                },
                "providers": providers,
            }
        }

    def validate_migration(
        self, original_config: Dict[str, Any], migrated_config: Dict[str, Any]
    ) -> bool:
        """
        Validate that migration preserved essential configuration.

        Args:
            original_config: Original configuration
            migrated_config: Migrated configuration

        Returns:
            True if migration is valid
        """
        try:
            # Validate consolidated configuration can be parsed
            provider_config_data = migrated_config.get("provider", {})
            provider_config = ProviderConfig(**provider_config_data)

            # Validate at least one provider is active
            active_providers = provider_config.get_active_providers()
            if not active_providers:
                self._logger.error("No active providers after migration")
                return False

            # Validate AWS configuration is preserved (if applicable)
            if original_config.get("provider", {}).get("type") == "aws":
                aws_provider = next((p for p in active_providers if p.type == "aws"), None)
                if not aws_provider:
                    self._logger.error("AWS provider not found after migration")
                    return False

                original_aws_config = original_config["provider"].get("aws", {})
                migrated_aws_config = aws_provider.config

                # Check essential AWS configuration is preserved
                essential_fields = ["region", "profile"]
                for field in essential_fields:
                    if (
                        field in original_aws_config
                        and migrated_aws_config.get(field) != original_aws_config[field]
                    ):
                        self._logger.error(f"AWS {field} not preserved in migration")
                        return False

            self._logger.info("Migration validation successful")
            return True

        except Exception as e:
            self._logger.error(f"Migration validation failed: {str(e)}")
            return False

    def get_migration_summary(
        self, original_config: Dict[str, Any], migrated_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Get summary of migration changes.

        Args:
            original_config: Original configuration
            migrated_config: Migrated configuration

        Returns:
            Migration summary
        """
        summary = {
            "migration_type": "none",
            "providers_before": 0,
            "providers_after": 0,
            "mode_before": "unknown",
            "mode_after": "unknown",
            "changes": [],
        }

        try:
            # Analyze original configuration
            original_provider = original_config.get("provider", {})
            if original_provider.get("type") == "aws":
                summary["migration_type"] = "legacy_aws_to_consolidated"
                summary["providers_before"] = 1
                summary["mode_before"] = "legacy"
                summary["changes"].append("Converted legacy AWS configuration to consolidated format")

            # Analyze migrated configuration
            migrated_provider = migrated_config.get("provider", {})
            if "providers" in migrated_provider:
                provider_config = ProviderConfig(**migrated_provider)
                summary["providers_after"] = len(provider_config.providers)
                summary["mode_after"] = provider_config.get_mode().value

                if provider_config.active_provider:
                    summary["changes"].append(
                        f"Set active provider to '{provider_config.active_provider}'"
                    )

                summary["changes"].append(
                    f"Created {len(provider_config.providers)} provider instance(s)"
                )

        except Exception as e:
            summary["error"] = str(e)

        return summary
