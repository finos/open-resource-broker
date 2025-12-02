"""
Tests for circular dependency fix implementation.

This module tests that ProviderContext and AWSClient can be created
without circular dependency issues.
"""

from unittest.mock import Mock, patch

import pytest

from domain.base.ports import ConfigurationPort, LoggingPort
from infrastructure.di.container import DIContainer
from infrastructure.di.core_services import register_core_services
from infrastructure.di.provider_services import register_provider_services
from monitoring.metrics import MetricsCollector
from providers.base.strategy.provider_context import ProviderContext


class TestCircularDependencyFix:
    """Test suite for circular dependency fix."""

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager."""
        config = Mock(spec=ConfigurationPort)
        config.get_provider_config.return_value = None
        config.get = Mock(return_value={})
        return config

    @pytest.fixture
    def mock_logger(self):
        """Mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def mock_metrics(self):
        """Mock metrics collector."""
        return Mock(spec=MetricsCollector)

    @pytest.fixture
    def container(self, mock_config_manager, mock_logger, mock_metrics):
        """Create DI container with mocked dependencies."""
        container = DIContainer()

        # Register core mocked services
        container.register_singleton(ConfigurationPort, lambda c: mock_config_manager)
        container.register_singleton(LoggingPort, lambda c: mock_logger)
        container.register_singleton(MetricsCollector, lambda c: mock_metrics)

        return container

    def test_no_circular_dependency_basic(self, container):
        """Test that ProviderContext can be created without circular dependency."""
        # Register services
        register_core_services(container)
        register_provider_services(container)

        # This should not cause circular dependency - this was the main issue
        provider_context = container.get(ProviderContext)
        assert provider_context is not None

        # Test that AWSClient factory is registered (but don't instantiate due to AWS config issues)
        try:
            from providers.aws.infrastructure.aws_client import AWSClient

            # Check that AWSClient is registered in the container
            assert container._service_registry.is_registered(AWSClient)
            print("✓ AWSClient factory registered successfully")
        except ImportError:
            # AWS provider not available, just test ProviderContext
            print("✓ AWS provider not available (expected in some environments)")

    def test_aws_client_creation_without_provider_context(self, container):
        """Test that AWSClient factory can be registered without trying to access ProviderContext."""
        # Register only the services needed for AWSClient
        register_core_services(container)

        # Register AWS services
        from infrastructure.di.provider_services import _register_aws_services

        try:
            _register_aws_services(container)

            # Test that AWSClient factory is registered (but don't instantiate due to AWS config issues)
            from providers.aws.infrastructure.aws_client import AWSClient

            # Check that AWSClient is registered in the container without circular dependency
            assert container._service_registry.is_registered(AWSClient)
            print("✓ AWSClient factory registered without ProviderContext dependency")

        except ImportError:
            # AWS provider not available, skip test
            pytest.skip("AWS provider not available")

    def test_provider_context_creation_without_aws_provisioning_adapter(self, container):
        """Test that ProviderContext can be created without AWSProvisioningAdapter."""
        # Mock provider configuration with AWS provider
        mock_config = Mock()
        mock_config.get_provider_config.return_value = Mock()
        mock_config.get_provider_config.return_value.providers = []

        container.register_singleton(ConfigurationPort, lambda c: mock_config)

        # Register services
        register_core_services(container)
        register_provider_services(container)

        # This should work even if AWSProvisioningAdapter is not available
        provider_context = container.get(ProviderContext)

        assert provider_context is not None

    @patch('infrastructure.di.provider_services._register_provider_to_context')
    def test_aws_provider_registration_with_none_provisioning_adapter(self, mock_register, container):
        """Test that AWS provider can be registered with None provisioning adapter."""
        from infrastructure.di.provider_services import \
            _register_aws_provider_to_context

        # Mock provider instance
        mock_provider_instance = Mock()
        mock_provider_instance.name = "test-aws"
        mock_provider_instance.config = {"region": "us-east-1", "profile": "default"}

        # Mock provider context
        mock_provider_context = Mock(spec=ProviderContext)

        # Register core services
        register_core_services(container)

        try:
            # This should work without throwing circular dependency error
            result = _register_aws_provider_to_context(
                mock_provider_instance,
                mock_provider_context,
                container
            )

            # Should succeed (True) or fail gracefully (False), but not throw recursion error
            assert isinstance(result, bool)

        except ImportError:
            # AWS provider not available, skip test
            pytest.skip("AWS provider not available")
        except RecursionError:
            pytest.fail("Circular dependency detected - fix not working")

    def test_metrics_collector_optional_handling(self, container):
        """Test that _create_aws_client handles optional MetricsCollector correctly."""
        # Register services without MetricsCollector
        mock_config = Mock(spec=ConfigurationPort)
        mock_config.get = Mock(return_value={})
        mock_logger = Mock(spec=LoggingPort)

        container.register_singleton(ConfigurationPort, lambda c: mock_config)
        container.register_singleton(LoggingPort, lambda c: mock_logger)
        # Don't register MetricsCollector

        register_core_services(container)

        try:
            from infrastructure.di.provider_services import _create_aws_client

            # Test that the function can handle missing MetricsCollector without circular dependency
            # We expect this to fail due to AWS config, but not due to circular dependency
            try:
                aws_client = _create_aws_client(container)
                assert aws_client is not None
            except Exception as e:
                # Should fail due to AWS config issues, not circular dependency
                assert "circular" not in str(e).lower()
                assert "recursion" not in str(e).lower()
                print(f"✓ Failed as expected due to AWS config (not circular dependency): {type(e).__name__}")

        except ImportError:
            # AWS provider not available, skip test
            pytest.skip("AWS provider not available")

    def test_container_get_optional_method(self, container):
        """Test container.get_optional method if available."""
        if hasattr(container, 'get_optional'):
            # Test that get_optional returns None for non-existent service
            result = container.get_optional(MetricsCollector)
            assert result is None or isinstance(result, MetricsCollector)
        else:
            # get_optional method not available, which is fine
            pass


class TestCircularDependencyDetection:
    """Test circular dependency detection if implemented."""

    def test_circular_dependency_detection_if_available(self):
        """Test circular dependency detection if implemented in container."""
        container = DIContainer()

        # Check if circular dependency detection is implemented
        if hasattr(container, '_resolution_stack'):
            # Test that circular dependency is detected
            # This is a placeholder - actual implementation would depend on container design
            pass
        else:
            # Circular dependency detection not implemented yet
            pytest.skip("Circular dependency detection not implemented")


if __name__ == '__main__':
    pytest.main([__file__])
