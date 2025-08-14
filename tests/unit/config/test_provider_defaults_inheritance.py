"""Unit tests for provider defaults inheritance system."""

from src.config.schemas.provider_strategy_schema import (
    HandlerConfig,
    ProviderConfig,
    ProviderInstanceConfig,
)


class TestProviderDefaultsInheritance:
    """Test suite for provider defaults inheritance functionality."""

    def test_basic_inheritance(self):
        """Test basic inheritance from provider defaults."""
        config_data = {
            "provider_defaults": {
                "aws": {
                    "handlers": {
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supports_spot": True,
                            "supports_ondemand": True,
                        },
                        "SpotFleet": {
                            "handler_class": "SpotFleetHandler",
                            "supports_spot": True,
                            "supports_ondemand": False,
                        },
                    }
                }
            },
            "providers": [
                {
                    "name": "aws-standard",
                    "type": "aws",
                    "enabled": True,
                    "config": {"region": "us-east-1"},
                }
            ],
        }

        provider_config = ProviderConfig(**config_data)
        provider = provider_config.providers[0]
        aws_defaults = provider_config.provider_defaults.get("aws")

        effective_handlers = provider.get_effective_handlers(aws_defaults)

        assert len(effective_handlers) == 2
        assert "EC2Fleet" in effective_handlers
        assert "SpotFleet" in effective_handlers

    def test_handler_override_merging(self):
        """Test partial handler overrides merge correctly with defaults."""
        config_data = {
            "provider_defaults": {
                "aws": {
                    "handlers": {
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supports_spot": True,
                            "supports_ondemand": True,
                            "max_instances": 1000,
                        }
                    }
                }
            },
            "providers": [
                {
                    "name": "aws-limited",
                    "type": "aws",
                    "enabled": True,
                    "config": {"region": "us-gov-west-1"},
                    "handler_overrides": {
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supports_spot": False,
                            "max_instances": 100,
                        }
                    },
                }
            ],
        }

        provider_config = ProviderConfig(**config_data)
        provider = provider_config.providers[0]
        aws_defaults = provider_config.provider_defaults.get("aws")

        effective_handlers = provider.get_effective_handlers(aws_defaults)
        ec2_handler = effective_handlers.get("EC2Fleet")

        handler_dict = ec2_handler.model_dump()
        assert handler_dict.get("supports_spot") is False  # Overridden
        assert handler_dict.get("supports_ondemand") is True  # Inherited
        assert handler_dict.get("max_instances") == 100  # Overridden

    def test_handler_removal_via_null_override(self):
        """Test null overrides remove handlers from inheritance."""
        config_data = {
            "provider_defaults": {
                "aws": {
                    "handlers": {
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supports_spot": True,
                        },
                        "SpotFleet": {
                            "handler_class": "SpotFleetHandler",
                            "supports_spot": True,
                        },
                        "ASG": {"handler_class": "ASGHandler", "supports_spot": True},
                    }
                }
            },
            "providers": [
                {
                    "name": "aws-restricted",
                    "type": "aws",
                    "enabled": True,
                    "config": {"region": "cn-north-1"},
                    "handler_overrides": {"SpotFleet": None, "ASG": None},
                }
            ],
        }

        provider_config = ProviderConfig(**config_data)
        provider = provider_config.providers[0]
        aws_defaults = provider_config.provider_defaults.get("aws")

        effective_handlers = provider.get_effective_handlers(aws_defaults)

        assert len(effective_handlers) == 1
        assert "EC2Fleet" in effective_handlers
        assert "SpotFleet" not in effective_handlers
        assert "ASG" not in effective_handlers

    def test_full_handler_override_ignores_defaults(self):
        """Test complete handler replacement ignores defaults."""
        config_data = {
            "provider_defaults": {
                "aws": {
                    "handlers": {
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supports_spot": True,
                        },
                        "SpotFleet": {
                            "handler_class": "SpotFleetHandler",
                            "supports_spot": True,
                        },
                    }
                }
            },
            "providers": [
                {
                    "name": "aws-custom",
                    "type": "aws",
                    "enabled": True,
                    "config": {"region": "us-west-2"},
                    "handlers": {
                        "CustomHandler": {
                            "handler_class": "CustomHandler",
                            "custom_feature": True,
                        }
                    },
                }
            ],
        }

        provider_config = ProviderConfig(**config_data)
        provider = provider_config.providers[0]
        aws_defaults = provider_config.provider_defaults.get("aws")

        effective_handlers = provider.get_effective_handlers(aws_defaults)

        assert len(effective_handlers) == 1
        assert "CustomHandler" in effective_handlers
        assert "EC2Fleet" not in effective_handlers

    def test_multi_provider_type_inheritance(self):
        """Test inheritance works independently for different provider types."""
        config_data = {
            "provider_defaults": {
                "aws": {
                    "handlers": {
                        "EC2Fleet": {"handler_class": "EC2FleetHandler"},
                        "SpotFleet": {"handler_class": "SpotFleetHandler"},
                    }
                },
                "provider1": {
                    "handlers": {
                        "VMSS": {"handler_class": "VMSSHandler"},
                        "VM": {"handler_class": "VMHandler"},
                    }
                },
            },
            "providers": [
                {
                    "name": "aws-east",
                    "type": "aws",
                    "enabled": True,
                    "config": {"region": "us-east-1"},
                },
                {
                    "name": "provider1-west",
                    "type": "provider1",
                    "enabled": True,
                    "config": {"region": "westus2"},
                },
            ],
        }

        provider_config = ProviderConfig(**config_data)

        aws_provider = provider_config.providers[0]
        provider1_provider = provider_config.providers[1]

        aws_defaults = provider_config.provider_defaults.get("aws")
        provider1_defaults = provider_config.provider_defaults.get("provider1")

        aws_handlers = aws_provider.get_effective_handlers(aws_defaults)
        provider1_handlers = provider1_provider.get_effective_handlers(
            provider1_defaults)

        assert len(aws_handlers) == 2
        assert "EC2Fleet" in aws_handlers
        assert "SpotFleet" in aws_handlers

        assert len(provider1_handlers) == 2
        assert "VMSS" in provider1_handlers
        assert "VM" in provider1_handlers

    def test_complex_regional_limitations_scenario(self):
        """Test realistic multi-region AWS scenario with various limitations."""
        config_data = {
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
                            "max_instances": 1000,
                        },
                        "SpotFleet": {
                            "handler_class": "SpotFleetHandler",
                            "supported_fleet_types": ["request", "maintain"],
                            "default_fleet_type": "request",
                            "supports_spot": True,
                            "supports_ondemand": False,
                            "max_instances": 1000,
                        },
                        "ASG": {
                            "handler_class": "ASGHandler",
                            "supports_spot": True,
                            "supports_ondemand": True,
                            "max_instances": 5000,
                        },
                        "RunInstances": {
                            "handler_class": "RunInstancesHandler",
                            "supports_spot": False,
                            "supports_ondemand": True,
                            "max_instances": 100,
                        },
                    }
                }
            },
            "providers": [
                {
                    "name": "aws-production",
                    "type": "aws",
                    "enabled": True,
                    "weight": 100,
                    "config": {"region": "us-east-1"},
                },
                {
                    "name": "aws-govcloud",
                    "type": "aws",
                    "enabled": True,
                    "weight": 50,
                    "config": {"region": "us-gov-west-1"},
                    "handler_overrides": {
                        "SpotFleet": None,
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "supports_spot": False,
                            "max_instances": 500,
                        },
                    },
                },
                {
                    "name": "aws-beta-region",
                    "type": "aws",
                    "enabled": True,
                    "weight": 25,
                    "config": {"region": "us-beta-1"},
                    "handler_overrides": {
                        "ASG": None,
                        "EC2Fleet": {
                            "handler_class": "EC2FleetHandler",
                            "max_instances": 50,
                        },
                        "RunInstances": {
                            "handler_class": "RunInstancesHandler",
                            "max_instances": 10,
                        },
                    },
                },
            ],
        }

        provider_config = ProviderConfig(**config_data)
        aws_defaults = provider_config.provider_defaults.get("aws")

        production = provider_config.providers[0]
        govcloud = provider_config.providers[1]
        beta = provider_config.providers[2]

        prod_handlers = production.get_effective_handlers(aws_defaults)
        gov_handlers = govcloud.get_effective_handlers(aws_defaults)
        beta_handlers = beta.get_effective_handlers(aws_defaults)

        # Production should have all 4 handlers
        assert len(prod_handlers) == 4

        # GovCloud should have 3 handlers (no SpotFleet)
        assert len(gov_handlers) == 3
        assert "SpotFleet" not in gov_handlers
        assert gov_handlers["EC2Fleet"].model_dump().get("supports_spot") is False

        # Beta should have 3 handlers (no ASG)
        assert len(beta_handlers) == 3
        assert "ASG" not in beta_handlers
        assert "SpotFleet" in beta_handlers  # Should inherit this
        assert beta_handlers["EC2Fleet"].model_dump().get("max_instances") == 50

    def test_handler_config_merge_functionality(self):
        """Test HandlerConfig merge_with method works correctly."""
        base_config = HandlerConfig(
            handler_class="EC2FleetHandler",
            supports_spot=True,
            supports_ondemand=True,
            max_instances=1000,
        )

        override_config = HandlerConfig(
            handler_class="EC2FleetHandler", supports_spot=False, max_instances=100
        )

        merged_config = base_config.merge_with(override_config)
        merged_dict = merged_config.model_dump()

        assert merged_dict["handler_class"] == "EC2FleetHandler"
        assert merged_dict["supports_spot"] is False  # Overridden
        assert merged_dict["supports_ondemand"] is True  # Preserved from base
        assert merged_dict["max_instances"] == 100  # Overridden

    def test_inheritance_with_missing_defaults(self):
        """Test behavior when provider type has no defaults defined."""
        provider = ProviderInstanceConfig(
            name="test-provider",
            type="provider2",  # Valid type but no defaults provided
            enabled=True,
            config={"region": "test-region"},
        )

        effective_handlers = provider.get_effective_handlers(None)

        assert len(effective_handlers) == 0
        assert isinstance(effective_handlers, dict)
