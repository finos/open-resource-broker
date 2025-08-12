"""Integration tests for multi-provider configuration system."""

from src.config.schemas.provider_strategy_schema import HandlerConfig, ProviderConfig


class TestMultiProviderConfiguration:
    """Test suite for multi-provider configuration functionality."""

    def test_provider_schema_validation(self):
        """Test provider schema validation with inheritance system."""
        config_data = {
            "active_provider": None,
            "selection_policy": "WEIGHTED_ROUND_ROBIN",
            "provider_defaults": {
                "aws": {
                    "handlers": {
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supported_fleet_types": ["instant", "request", "maintain"],
                            "default_fleet_type": "instant",
                            "supports_spot": True,
                            "supports_ondemand": True,
                        },
                        "SpotFleet": {
                            "handler_class": "SpotFleetHandler",
                            "supported_fleet_types": ["request", "maintain"],
                            "default_fleet_type": "request",
                            "supports_spot": True,
                            "supports_ondemand": False,
                        },
                    }
                }
            },
            "providers": [
                {
                    "name": "aws-primary",
                    "type": "aws",
                    "enabled": True,
                    "weight": 100,
                    "config": {"region": "eu-west-1"},
                },
                {
                    "name": "aws-secondary",
                    "type": "aws",
                    "enabled": True,
                    "weight": 50,
                    "config": {"region": "eu-west-2"},
                    "handler_overrides": {"SpotFleet": None},
                },
            ],
        }

        provider_config = ProviderConfig(**config_data)
        active_providers = provider_config.get_active_providers()

        assert provider_config.selection_policy == "WEIGHTED_ROUND_ROBIN"
        assert len(active_providers) == 2

        # Test inheritance
        aws_defaults = provider_config.provider_defaults.get("aws")

        primary_handlers = active_providers[0].get_effective_handlers(aws_defaults)
        secondary_handlers = active_providers[1].get_effective_handlers(aws_defaults)

        # Primary should inherit all default handlers
        assert "EC2Fleet" in primary_handlers
        assert "SpotFleet" in primary_handlers

        # Secondary should have EC2Fleet but not SpotFleet (override removes it)
        assert "EC2Fleet" in secondary_handlers
        assert "SpotFleet" not in secondary_handlers

    def test_configuration_files_validity(self):
        """Test that configuration files are valid and properly structured."""
        import json
        from pathlib import Path

        config_files = ["config/default_config.json", "awscpinst/config/config.json"]

        for config_file in config_files:
            config_path = Path(config_file)
            assert config_path.exists(), f"Configuration file {config_file} not found"

            with open(config_path, "r") as f:
                config_data = json.load(f)

            provider_section = config_data.get("provider", {})
            active_provider = provider_section.get("active_provider")
            selection_policy = provider_section.get("selection_policy")
            providers = provider_section.get("providers", [])

            assert active_provider is None, "active_provider should be null for multi-provider mode"
            assert selection_policy == "WEIGHTED_ROUND_ROBIN"
            assert len(providers) >= 2, "Should have multiple providers configured"

            # Verify providers don't have old capabilities field
            for provider in providers:
                assert (
                    "capabilities" not in provider
                ), f"Provider {provider['name']} has deprecated capabilities field"

    def test_handler_configuration_flexibility(self):
        """Test handler configuration with flexible additional fields."""
        handler_data = {
            "handler_class": "EC2FleetHandler",
            "supported_fleet_types": ["instant", "request", "maintain"],
            "default_fleet_type": "instant",
            "supports_spot": True,
            "supports_ondemand": True,
        }

        handler_config = HandlerConfig(**handler_data)

        assert handler_config.handler_class == "EC2FleetHandler"

        # Verify additional fields are preserved
        handler_dict = handler_config.model_dump()
        assert handler_dict["supported_fleet_types"] == [
            "instant",
            "request",
            "maintain",
        ]
        assert handler_dict["supports_spot"] is True

    def test_multi_provider_mode_detection(self):
        """Test that multi-provider mode is correctly detected."""
        config_data = {
            "active_provider": None,
            "selection_policy": "WEIGHTED_ROUND_ROBIN",
            "providers": [
                {
                    "name": "aws-primary",
                    "type": "aws",
                    "enabled": True,
                    "weight": 100,
                    "config": {"region": "eu-west-1"},
                },
                {
                    "name": "aws-secondary",
                    "type": "aws",
                    "enabled": True,
                    "weight": 50,
                    "config": {"region": "eu-west-2"},
                },
            ],
        }

        provider_config = ProviderConfig(**config_data)

        assert provider_config.is_multi_provider_mode()

        active_providers = provider_config.get_active_providers()
        assert len(active_providers) == 2

        # Verify both providers are returned for weighted round robin
        provider_names = [p.name for p in active_providers]
        assert "aws-primary" in provider_names
        assert "aws-secondary" in provider_names
