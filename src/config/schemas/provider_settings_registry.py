"""Registry for provider-specific BaseSettings classes."""

import os
from typing import Type

from pydantic_settings import BaseSettings


class ProviderSettingsRegistry:
    """Registry for provider-specific BaseSettings classes."""

    _settings_classes = {
        # Provider settings classes will be registered dynamically
        # "aws": AWSProviderSettings,  # Will be added when AWS provider is registered
    }

    @classmethod
    def register_provider_settings(
        cls, provider_type: str, settings_class: Type[BaseSettings]
    ) -> None:
        """Register a provider-specific settings class."""
        cls._settings_classes[provider_type] = settings_class

    @classmethod
    def get_registered_provider_types(cls) -> list[str]:
        """Get list of registered provider types."""
        return list(cls._settings_classes.keys())

    @classmethod
    def get_settings_class(cls, provider_type: str) -> Type[BaseSettings]:
        return cls._settings_classes.get(provider_type, BaseSettings)

    @classmethod
    def create_settings(cls, provider_type: str, config_dict: dict) -> BaseSettings:
        settings_class = cls.get_settings_class(provider_type)

        # Make a copy to avoid modifying original
        final_config = config_dict.copy()

        # Ensure minimal authentication for AWS
        if provider_type == "aws":
            if not any(
                key in final_config
                for key in ["profile", "role_arn", "access_key_id", "credential_file"]
            ):
                final_config["profile"] = "default"

            # Override with environment variables where they exist
            # This ensures env vars have precedence over config_dict values
            env_var_mapping = {
                "region": "ORB_AWS_REGION",
                "profile": "ORB_AWS_PROFILE",
                "role_arn": "ORB_AWS_ROLE_ARN",
                "access_key_id": "ORB_AWS_ACCESS_KEY_ID",
                "secret_access_key": "ORB_AWS_SECRET_ACCESS_KEY",  # nosec B105
                "session_token": "ORB_AWS_SESSION_TOKEN",  # nosec B105
                "endpoint_url": "ORB_AWS_ENDPOINT_URL",
                "aws_max_retries": "ORB_AWS_AWS_MAX_RETRIES",
                "aws_read_timeout": "ORB_AWS_AWS_READ_TIMEOUT",
                "service_role_spot_fleet": "ORB_AWS_SERVICE_ROLE_SPOT_FLEET",
                "ssm_parameter_prefix": "ORB_AWS_SSM_PARAMETER_PREFIX",
                "credential_file": "ORB_AWS_CREDENTIAL_FILE",
                "key_file": "ORB_AWS_KEY_FILE",
                "proxy_host": "ORB_AWS_PROXY_HOST",
                "proxy_port": "ORB_AWS_PROXY_PORT",
                "aws_connect_timeout": "ORB_AWS_AWS_CONNECT_TIMEOUT",
            }

            for field_name, env_var_name in env_var_mapping.items():
                if env_var_name in os.environ:
                    env_value = os.environ[env_var_name]

                    # Convert to appropriate type
                    if field_name in [
                        "aws_max_retries",
                        "aws_read_timeout",
                        "proxy_port",
                        "aws_connect_timeout",
                    ]:
                        try:
                            final_config[field_name] = int(env_value)
                        except ValueError:
                            pass
                    else:
                        final_config[field_name] = env_value

        # Create settings instance with final config
        settings = settings_class(**final_config)

        return settings
