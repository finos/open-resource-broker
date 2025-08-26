"""Tests for scheduler configuration schema."""

import os
from unittest.mock import patch

from config.schemas.app_schema import AppConfig
from config.schemas.scheduler_schema import SchedulerConfig


class TestSchedulerConfig:
    """Test scheduler configuration functionality."""

    def test_default_scheduler_config(self):
        """Test default scheduler configuration."""
        config = SchedulerConfig()
        assert config.type == "hostfactory"
        assert config.config_root is None
        assert config.get_config_root() == "config"

    def test_custom_scheduler_config(self):
        """Test custom scheduler configuration."""
        config = SchedulerConfig(type="hf", config_root="/custom/path")
        assert config.type == "hf"
        assert config.config_root == "/custom/path"
        assert config.get_config_root() == "/custom/path"

    def test_scheduler_config_with_env_var(self):
        """Test scheduler configuration with environment variable."""
        with patch.dict(os.environ, {"HF_PROVIDER_CONFDIR": "/env/path"}):
            config = SchedulerConfig(config_root="$HF_PROVIDER_CONFDIR")
            # Note: Environment expansion happens at the loader level
            assert config.config_root == "$HF_PROVIDER_CONFDIR"


class TestAppConfigWithScheduler:
    """Test AppConfig with scheduler integration."""

    def test_app_config_with_default_scheduler(self):
        """Test AppConfig with default scheduler configuration."""
        config_data = {
            "version": "2.0.0",
            "provider": {
                "active_provider": "aws-default",
                "providers": [
                    {
                        "name": "aws-default",
                        "type": "aws",
                        "enabled": True,
                        "config": {},
                    }
                ],
            },
        }
        app_config = AppConfig(**config_data)
        assert app_config.scheduler.type == "hostfactory"
        assert app_config.scheduler.get_config_root() == "config"

    def test_app_config_path_generation(self):
        """Test AppConfig path generation for different providers."""
        config_data = {
            "version": "2.0.0",
            "scheduler": {"type": "hostfactory", "config_root": "/test/path"},
            "provider": {
                "active_provider": "aws-default",
                "providers": [
                    {
                        "name": "aws-default",
                        "type": "aws",
                        "enabled": True,
                        "config": {},
                    }
                ],
            },
        }
        app_config = AppConfig(**config_data)

        # Test AWS provider paths
        assert app_config.get_config_file_path() == "/test/path/awsprov_config.json"
        assert app_config.get_templates_file_path() == "/test/path/awsprov_templates.json"

    def test_app_config_path_generation_provider1(self):
        """Test AppConfig path generation for Provider1 provider."""
        config_data = {
            "version": "2.0.0",
            "scheduler": {"type": "hostfactory", "config_root": "/test/path"},
            "provider": {
                "active_provider": "provider1-default",
                "providers": [
                    {
                        "name": "provider1-default",
                        "type": "provider1",
                        "enabled": True,
                        "config": {},
                    }
                ],
            },
        }
        app_config = AppConfig(**config_data)

        # Test Provider1 provider paths
        assert app_config.get_config_file_path() == "/test/path/provider1prov_config.json"
        assert app_config.get_templates_file_path() == "/test/path/provider1prov_templates.json"

    def test_app_config_path_generation_with_complex_provider_name(self):
        """Test AppConfig path generation with complex provider names."""
        config_data = {
            "version": "2.0.0",
            "scheduler": {"type": "hostfactory", "config_root": "/test/path"},
            "provider": {
                "active_provider": "aws-production-east",
                "providers": [
                    {
                        "name": "aws-production-east",
                        "type": "aws",
                        "enabled": True,
                        "config": {},
                    }
                ],
            },
        }
        app_config = AppConfig(**config_data)

        # Should extract 'aws' from 'aws-production-east'
        assert app_config.get_config_file_path() == "/test/path/awsprov_config.json"
        assert app_config.get_templates_file_path() == "/test/path/awsprov_templates.json"
