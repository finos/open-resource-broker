"""Provider Strategy Factory - Configuration-driven provider strategy creation.

This factory creates provider strategies and contexts based on integrated configuration,
integrating the existing provider strategy ecosystem with the CQRS architecture.
"""

import os
from typing import Any, Optional

from config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
    ProviderMode,
)
from domain.base.exceptions import ConfigurationError
from domain.base.ports import ConfigurationPort, LoggingPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.registry import (
    UnsupportedProviderError,
    get_provider_registry,
)


class ProviderCreationError(Exception):
    """Exception raised when provider creation fails."""


class ProviderStrategyFactory:
    """Factory for creating provider strategies from integrated configuration."""

    def __init__(
        self, config_manager: ConfigurationPort, logger: Optional[LoggingPort] = None
    ) -> None:
        """
        Initialize provider strategy factory.

        Args:
            config_manager: Configuration manager instance
            logger: Optional logger instance
        """
        self._config_manager = config_manager
        self._logger = logger
        self._provider_cache: dict[str, ProviderStrategy] = {}

    @handle_infrastructure_exceptions(context="provider_registry_setup")
    def setup_provider_registry(self) -> None:
        """
        Setup provider registry based on integrated configuration.

        Raises:
            ConfigurationError: If configuration is invalid
            ProviderCreationError: If provider setup fails
        """
        try:
            # Get integrated provider configuration
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                raise ConfigurationError("Provider configuration not found")

            mode = provider_config.get_mode()
            self._logger.info("Setting up provider registry in %s mode", mode.value)

            # Register all active providers with registry
            active_providers = provider_config.get_active_providers()
            registry = get_provider_registry()
            
            for provider_instance in active_providers:
                if not registry.is_provider_instance_registered(provider_instance.name):
                    registry.ensure_provider_instance_registered_from_config(provider_instance)

            self._logger.info(
                "Provider registry setup complete with %s providers",
                len(active_providers)
            )

        except Exception as e:
            self._logger.error("Failed to setup provider registry: %s", str(e))
            raise ProviderCreationError(f"Provider registry setup failed: {e!s}")



    def _create_provider_config(self, instance_config: ProviderInstanceConfig):
        """Create provider configuration with automatic env var loading.
        
        Environment variables have precedence over config file values.
        """
        
        if instance_config.type == "aws":
            # Use AWSProviderConfig directly - it inherits from BaseSettings
            from providers.aws.configuration.config import AWSProviderConfig
            
            config_dict = instance_config.config.copy()
            
            # Ensure minimal authentication
            if not any(key in config_dict for key in ['profile', 'role_arn', 'access_key_id', 'credential_file']):
                config_dict['profile'] = 'default'
            
            # Simple and reliable approach: Check env vars directly and override config_dict
            # This ensures env vars have precedence over config file values
            
            # Map of config field names to their environment variable names
            env_var_mapping = {
                'region': 'ORB_AWS_REGION',
                'profile': 'ORB_AWS_PROFILE', 
                'role_arn': 'ORB_AWS_ROLE_ARN',
                'access_key_id': 'ORB_AWS_ACCESS_KEY_ID',
                'secret_access_key': 'ORB_AWS_SECRET_ACCESS_KEY',
                'session_token': 'ORB_AWS_SESSION_TOKEN',
                'endpoint_url': 'ORB_AWS_ENDPOINT_URL',
                'aws_max_retries': 'ORB_AWS_AWS_MAX_RETRIES',
                'aws_read_timeout': 'ORB_AWS_AWS_READ_TIMEOUT',
                'service_role_spot_fleet': 'ORB_AWS_SERVICE_ROLE_SPOT_FLEET',
                'ssm_parameter_prefix': 'ORB_AWS_SSM_PARAMETER_PREFIX',
                'credential_file': 'ORB_AWS_CREDENTIAL_FILE',
                'key_file': 'ORB_AWS_KEY_FILE',
                'proxy_host': 'ORB_AWS_PROXY_HOST',
                'proxy_port': 'ORB_AWS_PROXY_PORT',
                'aws_connect_timeout': 'ORB_AWS_AWS_CONNECT_TIMEOUT',
                'request_retry_attempts': 'ORB_AWS_REQUEST_RETRY_ATTEMPTS',
                'instance_pending_timeout_sec': 'ORB_AWS_INSTANCE_PENDING_TIMEOUT_SEC',
                'describe_request_retry_attempts': 'ORB_AWS_DESCRIBE_REQUEST_RETRY_ATTEMPTS',
                'describe_request_interval': 'ORB_AWS_DESCRIBE_REQUEST_INTERVAL',
            }
            
            # Override config_dict with environment variables where they exist
            for field_name, env_var_name in env_var_mapping.items():
                if env_var_name in os.environ:
                    env_value = os.environ[env_var_name]
                    
                    # Convert to appropriate type
                    if field_name in ['aws_max_retries', 'aws_read_timeout', 'proxy_port', 
                                    'aws_connect_timeout', 'request_retry_attempts',
                                    'instance_pending_timeout_sec', 'describe_request_retry_attempts',
                                    'describe_request_interval']:
                        try:
                            config_dict[field_name] = int(env_value)
                        except ValueError:
                            # Keep original value if conversion fails
                            pass
                    else:
                        config_dict[field_name] = env_value
            
            # Handle complex nested fields (JSON env vars)
            if 'ORB_AWS_HANDLERS' in os.environ:
                try:
                    import json
                    config_dict['handlers'] = json.loads(os.environ['ORB_AWS_HANDLERS'])
                except (json.JSONDecodeError, ValueError):
                    # Keep original value if JSON parsing fails
                    pass
            
            if 'ORB_AWS_LAUNCH_TEMPLATE' in os.environ:
                try:
                    import json
                    config_dict['launch_template'] = json.loads(os.environ['ORB_AWS_LAUNCH_TEMPLATE'])
                except (json.JSONDecodeError, ValueError):
                    # Keep original value if JSON parsing fails
                    pass
            
            return AWSProviderConfig(**config_dict)
        
        # Fallback to dict config for other providers
        return instance_config.config

    def _create_provider_strategy(
        self, provider_config: ProviderInstanceConfig
    ) -> Any:
        """
        Create individual provider strategy using registry pattern.

        Args:
            provider_config: Provider instance configuration

        Returns:
            Configured ProviderStrategy instance

        Raises:
            ProviderCreationError: If provider creation fails
        """
        # Check cache first
        cache_key = f"{provider_config.type}:{provider_config.name}"
        if cache_key in self._provider_cache:
            self._logger.debug("Using cached provider strategy: %s", cache_key)
            return self._provider_cache[cache_key]

        try:
            # Use registry pattern with named instances
            registry = get_provider_registry()

            # Create provider configuration
            config = self._create_provider_config(provider_config)

            # Try to create from named instance first (preferred for multi-instance)
            if registry.is_provider_instance_registered(provider_config.name):
                strategy = registry.get_or_create_strategy(
                    provider_config.name, config
                )
                self._logger.debug(
                    "Created provider strategy from instance: %s", provider_config.name
                )
            else:
                # Fallback to provider type (backward compatibility)
                strategy = registry.get_or_create_strategy(provider_config.type, config)
                self._logger.debug("Created provider strategy from type: %s", provider_config.type)

            # Set provider name for identification
            if hasattr(strategy, "name"):
                strategy.name = provider_config.name

            # Cache the strategy
            self._provider_cache[cache_key] = strategy

            self._logger.debug(
                "Created provider strategy: %s (%s)",
                provider_config.name,
                provider_config.type,
            )
            return strategy

        except UnsupportedProviderError:
            available_providers = get_provider_registry().get_registered_providers()
            raise ProviderCreationError(
                f"Unsupported provider type: {provider_config.type}. "
                f"Available providers: {', '.join(available_providers)}"
            )
        except Exception as e:
            raise ProviderCreationError(
                f"Failed to create {provider_config.type} provider '{provider_config.name}': {e!s}"
            )



    def get_provider_info(self) -> dict[str, Any]:
        """
        Get information about current provider configuration.

        Returns:
            Dictionary with provider configuration information
        """
        try:
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                return {"mode": "error", "error": "Provider configuration not found"}

            mode = provider_config.get_mode()
            active_providers = provider_config.get_active_providers()

            return {
                "mode": mode.value,
                "selection_policy": provider_config.selection_policy,
                "active_provider": provider_config.active_provider,
                "total_providers": len(provider_config.providers),
                "active_providers": len(active_providers),
                "provider_names": [p.name for p in active_providers],
                "health_check_interval": provider_config.health_check_interval,
                "circuit_breaker_enabled": provider_config.circuit_breaker.enabled,
            }

        except Exception as e:
            self._logger.error("Failed to get provider info: %s", str(e))
            return {"mode": "error", "error": str(e)}

    def validate_configuration(self) -> dict[str, Any]:
        """
        Validate current provider configuration.

        Returns:
            Validation result dictionary
        """
        validation_result = {
            "valid": False,
            "errors": [],
            "warnings": [],
            "provider_count": 0,
            "mode": "unknown",
        }

        try:
            # Get and validate integrated configuration
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                validation_result["errors"].append("Provider configuration not found")
                return validation_result

            mode = provider_config.get_mode()
            active_providers = provider_config.get_active_providers()

            validation_result["mode"] = mode.value
            validation_result["provider_count"] = len(active_providers)

            # Validate based on mode
            if mode == ProviderMode.NONE:
                validation_result["errors"].append("No valid provider configuration found")
            elif mode == ProviderMode.SINGLE:
                if len(active_providers) == 0:
                    validation_result["errors"].append(
                        "Single provider mode requires at least one active provider"
                    )
                elif len(active_providers) > 1:
                    validation_result["warnings"].append(
                        "Multiple active providers in single provider mode"
                    )
            elif mode == ProviderMode.MULTI:
                if len(active_providers) < 2:
                    validation_result["errors"].append(
                        "Multi-provider mode requires at least 2 active providers"
                    )

            # Validate provider configurations
            registry = get_provider_registry()
            for provider_instance in active_providers:
                try:
                    # Test provider strategy creation
                    config = self._create_provider_config(provider_instance)
                    registry.get_or_create_strategy(provider_instance.name, config)
                except Exception as e:
                    validation_result["errors"].append(
                        f"Provider '{provider_instance.name}' validation failed: {e!s}"
                    )

            # Set overall validation status
            validation_result["valid"] = len(validation_result["errors"]) == 0

        except Exception as e:
            validation_result["errors"].append(f"Configuration validation failed: {e!s}")

        return validation_result

    def clear_cache(self) -> None:
        """Clear provider strategy cache."""
        self._provider_cache.clear()
        self._logger.debug("Provider strategy cache cleared")
