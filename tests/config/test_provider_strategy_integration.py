"""Integration tests for provider strategy system."""

import pytest

from src.config.managers.type_converter import ConfigTypeConverter
from src.providers.aws.configuration.config import AWSProviderConfig


class TestProviderStrategyIntegration:
    """Test provider strategy system integration scenarios."""

    def test_configuration_manager_multi_provider_integration(self):
        """Test ConfigurationManager with multi-provider setup."""
        raw_config = {
            "version": "2.0.0",
            "provider": {
                "active_provider": "aws-production",
                "selection_policy": "FIRST_AVAILABLE",
                "providers": [
                    {
                        "name": "aws-development",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-west-2", "profile": "dev-profile"},
                    },
                    {
                        "name": "aws-production",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "prod-profile"},
                    },
                ],
            },
            "logging": {"level": "INFO"},
        }

        # Test the type converter directly since ConfigurationManager needs file path
        converter = ConfigTypeConverter(raw_config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "prod-profile"

    def test_provider_strategy_with_circuit_breaker_config(self):
        """Test provider strategy with circuit breaker configuration."""
        config = {
            "provider": {
                "active_provider": "aws-resilient",
                "circuit_breaker": {
                    "enabled": True,
                    "failure_threshold": 3,
                    "recovery_timeout": 30,
                },
                "providers": [
                    {
                        "name": "aws-resilient",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1",
                            "profile": "resilient-profile",
                            "max_retries": 5,
                        },
                    }
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "resilient-profile"
        assert aws_config.max_retries == 5

    def test_provider_strategy_health_check_configuration(self):
        """Test provider strategy with health check configuration."""
        config = {
            "provider": {
                "health_check_interval": 120,
                "providers": [
                    {
                        "name": "aws-monitored",
                        "type": "aws",
                        "enabled": True,
                        "health_check": {
                            "enabled": True,
                            "interval": 60,
                            "timeout": 10,
                            "retry_count": 2,
                        },
                        "config": {
                            "region": "eu-west-1",
                            "profile": "monitored-profile",
                        },
                    }
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "eu-west-1"
        assert aws_config.profile == "monitored-profile"

    def test_provider_strategy_with_capabilities(self):
        """Test provider strategy with provider capabilities."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-compute-only",
                        "type": "aws",
                        "enabled": True,
                        "capabilities": ["compute"],
                        "config": {"region": "us-east-1", "profile": "compute-profile"},
                    },
                    {
                        "name": "aws-full-service",
                        "type": "aws",
                        "enabled": True,
                        "capabilities": ["compute", "storage", "networking"],
                        "config": {
                            "region": "us-west-2",
                            "profile": "full-service-profile",
                        },
                    },
                ]
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        # Should pick first enabled provider
        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "compute-profile"

    def test_provider_strategy_weight_and_priority(self):
        """Test provider strategy with weight and priority settings."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-low-priority",
                        "type": "aws",
                        "enabled": True,
                        "priority": 3,
                        "weight": 50,
                        "config": {
                            "region": "ap-southeast-1",
                            "profile": "low-priority-profile",
                        },
                    },
                    {
                        "name": "aws-high-priority",
                        "type": "aws",
                        "enabled": True,
                        "priority": 1,
                        "weight": 100,
                        "config": {
                            "region": "us-east-1",
                            "profile": "high-priority-profile",
                        },
                    },
                ]
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        # Current implementation picks first in list, not by priority
        # This test documents current behavior
        assert aws_config.region == "ap-southeast-1"
        assert aws_config.profile == "low-priority-profile"

    def test_provider_strategy_selection_policies(self):
        """Test different provider selection policies."""
        config = {
            "provider": {
                "selection_policy": "ROUND_ROBIN",
                "providers": [
                    {
                        "name": "aws-primary",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "primary-profile"},
                    },
                    {
                        "name": "aws-secondary",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-west-2",
                            "profile": "secondary-profile",
                        },
                    },
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        # Current implementation doesn't implement selection policies yet
        # This test documents current behavior (first available)
        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "primary-profile"

    def test_provider_validation_with_invalid_config(self):
        """Test provider validation with invalid configuration."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-invalid",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1"
                            # Missing required authentication (profile, role_arn, etc.)
                        },
                    }
                ]
            }
        }

        converter = ConfigTypeConverter(config)

        with pytest.raises(Exception) as exc_info:
            converter.get_typed(AWSProviderConfig)

        assert "At least one authentication method must be provided" in str(exc_info.value)

    def test_provider_name_validation(self):
        """Test provider name validation requirements."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-valid-name_123",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "test-profile"},
                    }
                ]
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "test-profile"

    def test_provider_type_case_sensitivity(self):
        """Test provider type case sensitivity."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-uppercase",
                        "type": "AWS",  # Uppercase
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "test-profile"},
                    },
                    {
                        "name": "aws-lowercase",
                        "type": "aws",  # Lowercase
                        "enabled": True,
                        "config": {"region": "us-west-2", "profile": "test-profile-2"},
                    },
                ]
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        # Should pick the lowercase 'aws' type (first match)
        assert aws_config.region == "us-west-2"
        assert aws_config.profile == "test-profile-2"
