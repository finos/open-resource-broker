"""Test script to verify the injectable decorator migration."""

import os
import sys
import unittest
from typing import Any, Dict

# Add src directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.domain.base.ports import ConfigurationPort, LoggingPort
from src.infrastructure.di.container import DIContainer


class MockConfigurationPort(ConfigurationPort):
    """Mock implementation of ConfigurationPort for testing."""

    def __init__(self):
        """Initialize the instance."""
        self._config = {
            "provider": {
                "type": "aws",
                "aws": {"region": "us-east-1", "profile": "default"},
            },
            "naming": {"prefix": "test"},
            "request": {"timeout": 30},
            "template": {"default_image_id": "ami-12345678"},
            "storage": {"strategy": "memory"},
            "events": {"enabled": True},
            "logging": {"level": "INFO", "console_enabled": True},
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        parts = key.split(".")
        current = self._config
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def get_all(self) -> Dict[str, Any]:
        """Get all configuration values."""
        return self._config

    def set(self, key: str, value: Any) -> None:
        """Set configuration value."""
        parts = key.split(".")
        current = self._config
        for _i, part in enumerate(parts[:-1]):
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def get_naming_config(self) -> Dict[str, Any]:
        """Get naming configuration."""
        return self._config.get("naming", {})

    def get_request_config(self) -> Dict[str, Any]:
        """Get request configuration."""
        return self._config.get("request", {})

    def get_template_config(self) -> Dict[str, Any]:
        """Get template configuration."""
        return self._config.get("template", {})

    def get_provider_config(self) -> Dict[str, Any]:
        """Get provider configuration."""
        return self._config.get("provider", {})

    def get_storage_config(self) -> Dict[str, Any]:
        """Get storage configuration."""
        return self._config.get("storage", {})

    def get_events_config(self) -> Dict[str, Any]:
        """Get events configuration."""
        return self._config.get("events", {})

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return self._config.get("logging", {})


class TestInjectableMigration(unittest.TestCase):
    """Test case for verifying the injectable decorator migration."""

    def setUp(self):
        """Set up the test case."""
        self.container = DIContainer()

        # Register basic dependencies
        from src.infrastructure.logging.logger import get_logger

        self.container.register_factory(LoggingPort, lambda c: get_logger("test"))

        # Register configuration
        self.container.register_singleton(ConfigurationPort, lambda c: MockConfigurationPort())

        # Register AWS config with valid authentication
        from src.providers.aws.configuration.config import AWSConfig

        self.container.register_singleton(
            AWSConfig, lambda c: AWSConfig(region="us-east-1", profile="default")
        )

        # Register AWS client manually
        from src.providers.aws.infrastructure.aws_client import AWSClient

        self.container.register_singleton(
            AWSClient,
            lambda c: AWSClient(config=c.get(ConfigurationPort), logger=c.get(LoggingPort)),
        )

        # Register AWS handler factory manually
        from src.providers.aws.infrastructure.aws_handler_factory import (
            AWSHandlerFactory,
        )

        self.container.register_singleton(
            AWSHandlerFactory,
            lambda c: AWSHandlerFactory(
                aws_client=c.get(AWSClient),
                logger=c.get(LoggingPort),
                config=c.get(AWSConfig),
            ),
        )

    def test_aws_strategy_classes(self):
        """Test that AWS strategy classes are properly registered and injectable."""
        # Register and test AWSProviderAdapter
        from src.providers.aws.strategy.aws_provider_adapter import AWSProviderAdapter

        self.container.register_singleton(AWSProviderAdapter)
        provider_adapter = self.container.get(AWSProviderAdapter)
        self.assertIsNotNone(provider_adapter)

        # Register and test AWSProviderStrategy
        from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        self.container.register_singleton(AWSProviderStrategy)
        provider_strategy = self.container.get(AWSProviderStrategy)
        self.assertIsNotNone(provider_strategy)

    def test_aws_manager_classes(self):
        """Test that AWS manager classes are properly registered and injectable."""
        # Register and test AWSInstanceManager
        from src.providers.aws.managers.aws_instance_manager import AWSInstanceManager

        self.container.register_singleton(AWSInstanceManager)
        instance_manager = self.container.get(AWSInstanceManager)
        self.assertIsNotNone(instance_manager)

        # Register and test AWSResourceManagerImpl
        from src.providers.aws.managers.aws_resource_manager import (
            AWSResourceManagerImpl,
        )

        self.container.register_singleton(AWSResourceManagerImpl)
        resource_manager = self.container.get(AWSResourceManagerImpl)
        self.assertIsNotNone(resource_manager)


if __name__ == "__main__":
    unittest.main()
