"""Provider Configuration Builder - Separates config creation from factory logic.

This module extracts configuration building logic from the Provider Strategy Factory,
following SRP and making the code more maintainable and testable.
"""

import json
import os
from typing import Any

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from orb.domain.base.ports import LoggingPort


class ProviderConfigBuilder:
    """Builds provider configurations with environment variable override support."""

    def __init__(self, logger: LoggingPort) -> None:
        """Initialize config builder.

        Args:
            logger: Logger instance for logging config operations
        """
        self._logger = logger

    def build_config(self, instance_config: ProviderInstanceConfig) -> Any:
        """Build provider configuration with automatic env var loading.

        Environment variables have precedence over config file values.

        Args:
            instance_config: Provider instance configuration

        Returns:
            Provider-specific configuration object
        """
        if instance_config.type == "aws":
            return self._build_aws_config(instance_config)

        # Fallback to dict config for other providers
        return instance_config.config

    def _build_aws_config(self, instance_config: ProviderInstanceConfig) -> Any:
        """Build AWS provider configuration.

        Args:
            instance_config: Provider instance configuration

        Returns:
            AWSProviderConfig instance
        """
        from orb.providers.aws.configuration.config import AWSProviderConfig

        config_dict = instance_config.config.copy()

        # Ensure minimal authentication
        if not any(
            key in config_dict
            for key in ["profile", "role_arn", "access_key_id", "credential_file"]
        ):
            config_dict["profile"] = "default"

        # Override with environment variables
        self._apply_env_var_overrides(config_dict)

        # Handle complex nested fields (JSON env vars)
        self._apply_json_env_vars(config_dict)

        return AWSProviderConfig(**config_dict)

    def _apply_env_var_overrides(self, config_dict: dict[str, Any]) -> None:
        """Apply environment variable overrides to config dictionary.

        Args:
            config_dict: Configuration dictionary to update in-place
        """
        # Map of config field names to their environment variable names
        env_var_mapping = {
            "region": "ORB_AWS_REGION",
            "profile": "ORB_AWS_PROFILE",
            "role_arn": "ORB_AWS_ROLE_ARN",
            "access_key_id": "ORB_AWS_ACCESS_KEY_ID",
            "secret_access_key": "ORB_AWS_SECRET_ACCESS_KEY",
            "session_token": "ORB_AWS_SESSION_TOKEN",
            "endpoint_url": "ORB_AWS_ENDPOINT_URL",
            "aws_max_retries": "ORB_AWS_MAX_RETRIES",
            "aws_read_timeout": "ORB_AWS_READ_TIMEOUT",
            "service_role_spot_fleet": "ORB_AWS_SERVICE_ROLE_SPOT_FLEET",
            "credential_file": "ORB_AWS_CREDENTIAL_FILE",
            "key_file": "ORB_AWS_KEY_FILE",
            "proxy_host": "ORB_AWS_PROXY_HOST",
            "proxy_port": "ORB_AWS_PROXY_PORT",
            "aws_connect_timeout": "ORB_AWS_CONNECT_TIMEOUT",
            "request_retry_attempts": "ORB_AWS_REQUEST_RETRY_ATTEMPTS",
            "instance_pending_timeout_sec": "ORB_AWS_INSTANCE_PENDING_TIMEOUT_SEC",
            "describe_request_retry_attempts": "ORB_AWS_DESCRIBE_REQUEST_RETRY_ATTEMPTS",
            "describe_request_interval": "ORB_AWS_DESCRIBE_REQUEST_INTERVAL",
        }

        # Integer fields that need type conversion
        integer_fields = {
            "aws_max_retries",
            "aws_read_timeout",
            "proxy_port",
            "aws_connect_timeout",
            "request_retry_attempts",
            "instance_pending_timeout_sec",
            "describe_request_retry_attempts",
            "describe_request_interval",
        }

        # Override config_dict with environment variables where they exist
        for field_name, env_var_name in env_var_mapping.items():
            if env_var_name in os.environ:
                env_value = os.environ[env_var_name]

                # Convert to appropriate type
                if field_name in integer_fields:
                    try:
                        config_dict[field_name] = int(env_value)
                    except ValueError:
                        self._logger.warning(
                            "Failed to convert %s to integer: %s", env_var_name, env_value
                        )
                else:
                    config_dict[field_name] = env_value

    def _apply_json_env_vars(self, config_dict: dict[str, Any]) -> None:
        """Apply JSON environment variables to config dictionary.

        Args:
            config_dict: Configuration dictionary to update in-place
        """
        json_env_vars = {
            "ORB_AWS_HANDLERS": "handlers",
            "ORB_AWS_LAUNCH_TEMPLATE": "launch_template",
        }

        for env_var_name, field_name in json_env_vars.items():
            if env_var_name in os.environ:
                try:
                    config_dict[field_name] = json.loads(os.environ[env_var_name])
                except (json.JSONDecodeError, ValueError) as e:
                    self._logger.warning("Failed to parse JSON from %s: %s", env_var_name, e)
