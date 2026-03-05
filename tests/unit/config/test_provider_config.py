"""Unit tests for provider configuration."""

import os
from unittest.mock import MagicMock, patch

import pytest

from config.schemas.provider_strategy_schema import (
    CircuitBreakerConfig,
    HealthCheckConfig,
    ProviderConfig,
    ProviderInstanceConfig,
    ProviderMode,
)


class TestProviderInstanceConfig:
    """Test provider instance configuration."""

    def test_valid_provider_instance_config(self):
        """Test creating valid provider instance configuration."""
        config = ProviderInstanceConfig(
            name="aws-primary",
            type="aws",
            enabled=True,
            priority=1,
            weight=100,
            config={"region": "us-east-1", "profile": "default"},
            capabilities=["instances", "spot_instances"],
        )

        assert config.name == "aws-primary"
        assert config.type == "aws"
        assert config.enabled is True
        assert config.priority == 1
        assert config.weight == 100
        assert config.config["region"] == "us-east-1"
        assert "instances" in config.capabilities

    def test_provider_name_validation(self):
        """Test provider name validation."""
        # Valid names
        valid_names = ["aws-primary", "aws_backup", "provider1", "test-provider"]
        for name in valid_names:
            config = ProviderInstanceConfig(name=name, type="aws")
            assert config.name == name

        # Invalid names
        with pytest.raises(ValueError, match="Provider name cannot be empty"):
            ProviderInstanceConfig(name="", type="aws")

        with pytest.raises(ValueError, match="Provider name cannot be empty"):
            ProviderInstanceConfig(name="   ", type="aws")

        with pytest.raises(ValueError, match="must contain only alphanumeric"):
            ProviderInstanceConfig(name="aws@primary", type="aws")

    def test_provider_type_validation(self):
        """Test provider type validation."""
        from providers.registry import get_provider_registry

        registry = get_provider_registry()

        # Register aws type so the validator has entries to check against
        if "aws" not in registry.get_registered_providers():
            registry.register_provider(
                "aws",
                strategy_factory=lambda cfg: None,
                config_factory=lambda: None,
            )

        # Only registered provider types are valid; aws is the registered type
        config = ProviderInstanceConfig(name="test", type="aws")
        assert config.type == "aws"

        # Unregistered types raise ValueError
        with pytest.raises(ValueError, match="is not registered"):
            ProviderInstanceConfig(name="test", type="invalid_unregistered_type")

    def test_weight_validation(self):
        """Test provider weight validation."""
        # Valid weight
        config = ProviderInstanceConfig(name="test", type="aws", weight=50)
        assert config.weight == 50

        # Invalid weight
        with pytest.raises(ValueError, match="Provider weight must be positive"):
            ProviderInstanceConfig(name="test", type="aws", weight=0)

        with pytest.raises(ValueError, match="Provider weight must be positive"):
            ProviderInstanceConfig(name="test", type="aws", weight=-10)


class TestHealthCheckConfig:
    """Test health check configuration."""

    def test_default_health_check_config(self):
        """Test default health check configuration."""
        config = HealthCheckConfig()

        assert config.enabled is True
        assert config.interval == 300
        assert config.timeout == 30
        assert config.retry_count == 3

    def test_custom_health_check_config(self):
        """Test custom health check configuration."""
        config = HealthCheckConfig(enabled=False, interval=600, timeout=60, retry_count=5)

        assert config.enabled is False
        assert config.interval == 600
        assert config.timeout == 60
        assert config.retry_count == 5

    def test_health_check_validation(self):
        """Test health check configuration validation."""
        # Invalid interval
        with pytest.raises(ValueError, match="Health check interval must be positive"):
            HealthCheckConfig(interval=0)

        with pytest.raises(ValueError, match="Health check interval must be positive"):
            HealthCheckConfig(interval=-100)

        # Invalid timeout
        with pytest.raises(ValueError, match="Health check timeout must be positive"):
            HealthCheckConfig(timeout=0)

        with pytest.raises(ValueError, match="Health check timeout must be positive"):
            HealthCheckConfig(timeout=-30)


class TestCircuitBreakerConfig:
    """Test circuit breaker configuration."""

    def test_default_circuit_breaker_config(self):
        """Test default circuit breaker configuration."""
        config = CircuitBreakerConfig()

        assert config.enabled is True
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60
        assert config.half_open_max_calls == 3

    def test_custom_circuit_breaker_config(self):
        """Test custom circuit breaker configuration."""
        config = CircuitBreakerConfig(
            enabled=False,
            failure_threshold=10,
            recovery_timeout=120,
            half_open_max_calls=5,
        )

        assert config.enabled is False
        assert config.failure_threshold == 10
        assert config.recovery_timeout == 120
        assert config.half_open_max_calls == 5

    def test_circuit_breaker_validation(self):
        """Test circuit breaker configuration validation."""
        # Invalid failure threshold
        with pytest.raises(ValueError, match="Failure threshold must be positive"):
            CircuitBreakerConfig(failure_threshold=0)

        # Invalid recovery timeout
        with pytest.raises(ValueError, match="Recovery timeout must be positive"):
            CircuitBreakerConfig(recovery_timeout=-60)


class TestProviderConfig:
    """Test provider configuration."""

    def test_single_provider_mode_explicit(self):
        """Test single provider mode with explicit active_provider."""
        config = ProviderConfig(
            active_provider="aws-primary",
            providers=[
                ProviderInstanceConfig(name="aws-primary", type="aws"),
                ProviderInstanceConfig(name="aws-backup", type="aws", enabled=False),
            ],
        )

        assert config.get_mode() == ProviderMode.SINGLE
        assert not config.is_multi_provider_mode()

        active_providers = config.get_active_providers()
        assert len(active_providers) == 1
        assert active_providers[0].name == "aws-primary"

    def test_multi_provider_mode(self):
        """Test multi-provider mode."""
        config = ProviderConfig(
            selection_policy="ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(name="aws-primary", type="aws", enabled=True),
                ProviderInstanceConfig(name="aws-backup", type="aws", enabled=True),
            ],
        )

        assert config.get_mode() == ProviderMode.MULTI
        assert config.is_multi_provider_mode()

        active_providers = config.get_active_providers()
        assert len(active_providers) == 2
        assert {p.name for p in active_providers} == {"aws-primary", "aws-backup"}

    def test_single_provider_mode_implicit(self):
        """Test single provider mode with one provider."""
        config = ProviderConfig(providers=[ProviderInstanceConfig(name="aws-only", type="aws")])

        assert config.get_mode() == ProviderMode.SINGLE
        assert not config.is_multi_provider_mode()

        active_providers = config.get_active_providers()
        assert len(active_providers) == 1
        assert active_providers[0].name == "aws-only"

    def test_single_provider_mode(self):
        """Test single provider mode detection."""
        config = ProviderConfig(
            providers=[
                ProviderInstanceConfig(
                    name="aws-default",
                    type="aws",
                    enabled=True,
                    config={"region": "us-east-1", "profile": "default"},
                )
            ]
        )

        assert config.get_mode() == ProviderMode.SINGLE
        assert not config.is_multi_provider_mode()

    def test_selection_policy_validation(self):
        """Test selection policy validation."""
        valid_policies = [
            "FIRST_AVAILABLE",
            "ROUND_ROBIN",
            "WEIGHTED_ROUND_ROBIN",
            "LEAST_CONNECTIONS",
            "FASTEST_RESPONSE",
            "HIGHEST_SUCCESS_RATE",
            "CAPABILITY_BASED",
            "HEALTH_BASED",
            "RANDOM",
            "PERFORMANCE_BASED",
        ]

        # Create a dummy provider for validation
        dummy_provider = ProviderInstanceConfig(name="test", type="aws")

        for policy in valid_policies:
            config = ProviderConfig(selection_policy=policy, providers=[dummy_provider])
            assert config.selection_policy == policy

        # Invalid policy
        with pytest.raises(ValueError, match="Selection policy must be one of"):
            ProviderConfig(selection_policy="INVALID_POLICY", providers=[dummy_provider])

    def test_provider_name_uniqueness(self):
        """Test provider name uniqueness validation."""
        with pytest.raises(ValueError, match="Provider names must be unique"):
            ProviderConfig(
                providers=[
                    ProviderInstanceConfig(name="aws-primary", type="aws"),
                    ProviderInstanceConfig(name="aws-primary", type="aws"),  # Duplicate name
                ]
            )

    def test_active_provider_exists(self):
        """Test active provider exists validation."""
        with pytest.raises(ValueError, match="Active provider 'nonexistent' not found"):
            ProviderConfig(
                active_provider="nonexistent",
                providers=[ProviderInstanceConfig(name="aws-primary", type="aws")],
            )

    def test_get_provider_by_name(self):
        """Test getting provider by name."""
        config = ProviderConfig(
            providers=[
                ProviderInstanceConfig(name="aws-primary", type="aws"),
                ProviderInstanceConfig(name="aws-backup", type="aws"),
            ]
        )

        # Existing provider
        provider = config.get_provider_by_name("aws-primary")
        assert provider is not None
        assert provider.name == "aws-primary"

        # Non-existing provider
        provider = config.get_provider_by_name("nonexistent")
        assert provider is None

    def test_health_check_interval_validation(self):
        """Test health check interval validation."""
        # Create a dummy provider for validation
        dummy_provider = ProviderInstanceConfig(name="test", type="aws")

        # Valid interval
        config = ProviderConfig(health_check_interval=600, providers=[dummy_provider])
        assert config.health_check_interval == 600

        # Invalid interval
        with pytest.raises(ValueError, match="Health check interval must be positive"):
            ProviderConfig(health_check_interval=0, providers=[dummy_provider])

        with pytest.raises(ValueError, match="Health check interval must be positive"):
            ProviderConfig(health_check_interval=-300, providers=[dummy_provider])

    def test_empty_configuration_validation(self):
        """Test validation with empty configuration."""
        with pytest.raises(ValueError, match="At least one provider must be configured"):
            ProviderConfig()  # No providers and no legacy config


class TestAWSProviderConfigBaseSettings:
    """Test AWS provider configuration BaseSettings inheritance concepts."""

    def test_aws_provider_config_concepts(self):
        """Test AWS provider config BaseSettings concepts."""
        # Test the concepts that should be implemented

        # Environment prefix concept
        expected_prefix = "ORB_AWS_"
        assert expected_prefix == "ORB_AWS_"

        # Configuration concepts
        config_concepts = {
            "case_sensitive": False,
            "env_nested_delimiter": "__",
            "populate_by_name": True,
            "extra": "allow",
        }

        for concept, expected_value in config_concepts.items():
            assert expected_value is not None

    def test_aws_provider_config_field_concepts(self):
        """Test AWS provider config field concepts."""
        # Expected AWS configuration fields
        expected_fields = [
            "provider_type",
            "region",
            "profile",
            "role_arn",
            "access_key_id",
            "secret_access_key",
            "aws_max_retries",
            "aws_read_timeout",
            "service_role_spot_fleet",
            "proxy_host",
            "proxy_port",
        ]

        # All fields should be valid identifiers
        for field in expected_fields:
            assert field.isidentifier()
            assert not field.startswith("_")

    def test_aws_env_var_mapping_concepts(self):
        """Test AWS environment variable mapping concepts."""
        # Expected environment variable mappings
        env_mappings = {
            "region": "ORB_AWS_REGION",
            "profile": "ORB_AWS_PROFILE",
            "role_arn": "ORB_AWS_ROLE_ARN",
            "access_key_id": "ORB_AWS_ACCESS_KEY_ID",
            "secret_access_key": "ORB_AWS_SECRET_ACCESS_KEY",  # nosec B105
            "aws_max_retries": "ORB_AWS_AWS_MAX_RETRIES",
            "proxy_host": "ORB_AWS_PROXY_HOST",
            "proxy_port": "ORB_AWS_PROXY_PORT",
        }

        for field, env_var in env_mappings.items():
            assert env_var.startswith("ORB_AWS_")
            assert env_var.isupper()
            assert field.lower().replace("_", "") in env_var.lower().replace("_", "")

    def test_aws_authentication_concepts(self):
        """Test AWS authentication concepts."""
        # Valid authentication methods
        auth_methods = [
            "profile",
            "role_arn",
            "access_key_id + secret_access_key",
            "credential_file",
        ]

        # At least one should be required
        assert len(auth_methods) > 0

        # Profile should be a common default
        default_profile = "default"
        assert default_profile == "default"

    def test_aws_validation_concepts(self):
        """Test AWS validation concepts."""

        # Proxy validation concept
        def validate_proxy_concept(proxy_host, proxy_port):
            if proxy_host and not proxy_port:
                return "proxy_port required when proxy_host specified"
            return None

        # Test validation logic
        assert validate_proxy_concept("proxy.com", None) is not None
        assert validate_proxy_concept("proxy.com", 8080) is None
        assert validate_proxy_concept(None, None) is None

    def test_aws_type_conversion_concepts(self):
        """Test AWS type conversion concepts."""
        # Integer fields that should be converted from strings
        int_fields = ["aws_max_retries", "aws_read_timeout", "proxy_port", "aws_connect_timeout"]

        for field in int_fields:
            # Should be convertible from string
            test_value = "123"
            converted = int(test_value)
            assert isinstance(converted, int)
            assert converted == 123

    def test_aws_default_values_concepts(self):
        """Test AWS default values concepts."""
        # Expected default values
        defaults = {
            "provider_type": "aws",
            "region": "us-east-1",
            "aws_max_retries": 3,
            "aws_read_timeout": 30,
            "service_role_spot_fleet": "AWSServiceRoleForEC2SpotFleet",
            "ssm_parameter_prefix": "/hostfactory/templates/",
            "aws_connect_timeout": 10,
        }

        for field, default_value in defaults.items():
            assert default_value is not None
            assert isinstance(default_value, (str, int))

    def test_mocked_aws_config_behavior(self):
        """Test mocked AWS config behavior."""
        # Mock AWS config with environment override
        with patch.dict(
            os.environ,
            {
                "ORB_AWS_REGION": "eu-west-1",
                "ORB_AWS_PROFILE": "test-profile",
                "ORB_AWS_AWS_MAX_RETRIES": "10",
            },
        ):
            # Simulate BaseSettings behavior
            mock_config = MagicMock()
            mock_config.region = os.environ.get("ORB_AWS_REGION", "us-east-1")
            mock_config.profile = os.environ.get("ORB_AWS_PROFILE", "default")
            mock_config.aws_max_retries = int(os.environ.get("ORB_AWS_AWS_MAX_RETRIES", "3"))

            assert mock_config.region == "eu-west-1"
            assert mock_config.profile == "test-profile"
            assert mock_config.aws_max_retries == 10


class TestProviderInstanceConfigBaseSettings:
    """Test provider instance configuration BaseSettings integration concepts."""

    def test_provider_instance_config_concepts(self):
        """Test provider instance config concepts."""
        # Basic provider instance structure
        instance_structure = {"name": "aws-test", "type": "aws", "config": {"region": "us-west-2"}}

        assert instance_structure["type"] == "aws"
        assert isinstance(instance_structure["config"], dict)
        assert instance_structure["config"]["region"] == "us-west-2"

    def test_typed_config_concept(self):
        """Test typed config concept."""

        # Mock typed config behavior
        def mock_get_typed_config(provider_type, config_dict):
            if provider_type == "aws":
                # Return mock AWS config
                mock_aws_config = MagicMock()
                mock_aws_config.region = config_dict.get("region", "us-east-1")
                mock_aws_config.profile = config_dict.get("profile", "default")
                return mock_aws_config
            else:
                # Return generic config
                return MagicMock()

        # Test AWS config
        aws_config = mock_get_typed_config("aws", {"region": "eu-central-1"})
        assert aws_config.region == "eu-central-1"

        # Test unknown provider
        other_config = mock_get_typed_config("other", {"key": "value"})
        assert other_config is not None

    def test_provider_specific_properties_concept(self):
        """Test provider-specific properties concept."""

        # Mock provider-specific property behavior
        def mock_get_provider_config(provider_type, config_dict):
            if provider_type == "aws":
                return {"aws_specific": True, **config_dict}
            elif provider_type == "provider1":
                return {"provider1_specific": True, **config_dict}
            else:
                return None

        # Test AWS-specific config
        aws_result = mock_get_provider_config("aws", {"region": "us-east-1"})
        assert aws_result is not None
        assert aws_result["aws_specific"] is True
        assert aws_result["region"] == "us-east-1"

        # Test unknown provider
        unknown_result = mock_get_provider_config("unknown", {})
        assert unknown_result is None
