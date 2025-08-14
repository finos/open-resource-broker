"""Tests for multi-provider configuration resolution."""

import pytest

from src.config.managers.type_converter import ConfigTypeConverter
from src.providers.aws.configuration.config import AWSProviderConfig


class TestMultiProviderConfiguration:
    """Test multi-provider configuration resolution scenarios."""

    def test_multiple_aws_providers_with_active_selection(self):
        """Test resolving AWS config with multiple providers and active selection."""
        config = {
            "provider": {
                "active_provider": "aws-eu-west-1",
                "providers": [
                    {
                        "name": "aws-us-east-1",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "default"},
                    },
                    {
                        "name": "aws-eu-west-1",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "eu-west-1",
                            "profile": "fsi-pace-amer+ms-symphony",
                        },
                    },
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "eu-west-1"
        assert aws_config.profile == "fsi-pace-amer+ms-symphony"

    def test_multiple_aws_providers_fallback_to_first_enabled(self):
        """Test fallback to first enabled AWS provider when no active provider specified."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-disabled",
                        "type": "aws",
                        "enabled": False,
                        "config": {
                            "region": "us-west-1",
                            "profile": "disabled-profile",
                        },
                    },
                    {
                        "name": "aws-first-enabled",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1",
                            "profile": "first-enabled-profile",
                        },
                    },
                    {
                        "name": "aws-second-enabled",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "eu-west-1",
                            "profile": "second-enabled-profile",
                        },
                    },
                ]
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "first-enabled-profile"

    def test_mixed_provider_types_aws_resolution(self):
        """Test AWS config resolution with mixed provider types."""
        config = {
            "provider": {
                "active_provider": "aws-production",
                "providers": [
                    {
                        "name": "provider1-dev",
                        "type": "provider1",
                        "enabled": True,
                        "config": {
                            "subscription_id": "test-sub",
                            "resource_group": "test-rg",
                        },
                    },
                    {
                        "name": "aws-production",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1",
                            "profile": "production-profile",
                        },
                    },
                    {
                        "name": "provider2-staging",
                        "type": "provider2",
                        "enabled": True,
                        "config": {
                            "project_id": "test-project",
                            "zone": "us-central1-a",
                        },
                    },
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "production-profile"

    def test_no_aws_providers_error_handling(self):
        """Test error handling when no AWS providers are available."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "provider1-only",
                        "type": "provider1",
                        "enabled": True,
                        "config": {"subscription_id": "test-sub"},
                    },
                    {
                        "name": "provider2-only",
                        "type": "provider2",
                        "enabled": True,
                        "config": {"project_id": "test-project"},
                    },
                ]
            }
        }

        converter = ConfigTypeConverter(config)

        with pytest.raises(Exception) as exc_info:
            converter.get_typed(AWSProviderConfig)

        assert "At least one authentication method must be provided" in str(exc_info.value)

    def test_active_provider_not_found_fallback(self):
        """Test fallback when specified active provider is not found."""
        config = {
            "provider": {
                "active_provider": "aws-nonexistent",
                "providers": [
                    {
                        "name": "aws-available",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1",
                            "profile": "available-profile",
                        },
                    }
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "available-profile"

    def test_active_provider_disabled_fallback(self):
        """Test fallback when specified active provider is disabled."""
        config = {
            "provider": {
                "active_provider": "aws-disabled",
                "providers": [
                    {
                        "name": "aws-disabled",
                        "type": "aws",
                        "enabled": False,
                        "config": {
                            "region": "us-west-1",
                            "profile": "disabled-profile",
                        },
                    },
                    {
                        "name": "aws-enabled",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "enabled-profile"},
                    },
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "enabled-profile"

    def test_active_provider_wrong_type_fallback(self):
        """Test fallback when specified active provider is not AWS type."""
        config = {
            "provider": {
                "active_provider": "provider1-primary",
                "providers": [
                    {
                        "name": "provider1-primary",
                        "type": "provider1",
                        "enabled": True,
                        "config": {"subscription_id": "test-sub"},
                    },
                    {
                        "name": "aws-secondary",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1",
                            "profile": "secondary-profile",
                        },
                    },
                ],
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        assert aws_config.region == "us-east-1"
        assert aws_config.profile == "secondary-profile"

    def test_empty_providers_list(self):
        """Test handling of empty providers list."""
        config = {"provider": {"providers": []}}

        converter = ConfigTypeConverter(config)

        with pytest.raises(Exception) as exc_info:
            converter.get_typed(AWSProviderConfig)

        assert "At least one authentication method must be provided" in str(exc_info.value)

    def test_missing_provider_config_section(self):
        """Test handling when provider config section is missing."""
        config = {}

        converter = ConfigTypeConverter(config)

        with pytest.raises(Exception) as exc_info:
            converter.get_typed(AWSProviderConfig)

        assert "At least one authentication method must be provided" in str(exc_info.value)

    def test_provider_with_missing_config(self):
        """Test handling of provider instance with missing config section."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-no-config",
                        "type": "aws",
                        "enabled": True,
                        # Missing 'config' section
                    }
                ]
            }
        }

        converter = ConfigTypeConverter(config)

        with pytest.raises(Exception) as exc_info:
            converter.get_typed(AWSProviderConfig)

        assert "At least one authentication method must be provided" in str(exc_info.value)

    def test_multiple_aws_providers_priority_order(self):
        """Test that provider order matters when no active provider specified."""
        config = {
            "provider": {
                "providers": [
                    {
                        "name": "aws-third",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "ap-southeast-1",
                            "profile": "third-profile",
                        },
                    },
                    {
                        "name": "aws-first",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1", "profile": "first-profile"},
                    },
                    {
                        "name": "aws-second",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "eu-west-1", "profile": "second-profile"},
                    },
                ]
            }
        }

        converter = ConfigTypeConverter(config)
        aws_config = converter.get_typed(AWSProviderConfig)

        # Should pick the first enabled AWS provider in the list
        assert aws_config.region == "ap-southeast-1"
        assert aws_config.profile == "third-profile"
