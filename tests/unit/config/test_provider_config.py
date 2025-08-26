"""Unit tests for provider configuration."""

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
        # Valid types
        valid_types = ["aws", "provider1", "provider2"]
        for provider_type in valid_types:
            config = ProviderInstanceConfig(name="test", type=provider_type)
            assert config.type == provider_type

        # Invalid type
        with pytest.raises(ValueError, match="Provider type must be one of"):
            ProviderInstanceConfig(name="test", type="invalid")

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
