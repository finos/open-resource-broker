"""
Tests for the injectable decorator functionality.
"""

from typing import Optional
from unittest.mock import Mock, patch

import pytest

from src.domain.base.ports import LoggingPort
from src.infrastructure.di.container import DIContainer
from src.infrastructure.di.decorators import (
    get_injectable_info,
    injectable,
    is_injectable,
)


# Test interfaces
class MockPort:
    """Mock port interface."""


class MockService:
    """Mock service interface."""


class MockAdapter(MockPort):
    """Mock adapter implementation."""

    def __init__(self, name: str = "test"):
        """Initialize the instance."""
        self.name = name


class MockServiceImpl(MockService):
    """Mock service implementation."""

    def __init__(self, value: int = 42):
        self.value = value


class TestInjectableDecorator:
    """Test suite for injectable decorator."""

    def setup_method(self):
        """Set up test environment."""
        self.container = DIContainer()
        self.container.register_singleton(MockPort, lambda c: MockAdapter("injected"))
        self.container.register_singleton(MockService, lambda c: MockServiceImpl(100))

    def test_injectable_decorator_basic(self):
        """Test basic injectable decorator functionality."""

        @injectable
        class BasicService:
            def __init__(self, port: MockPort):
                self.port = port

        # Verify decorator applied
        assert is_injectable(BasicService)
        assert hasattr(BasicService, "_injectable")
        assert hasattr(BasicService, "_original_init")

        # Test manual instantiation still works
        manual_port = MockAdapter("manual")
        service = BasicService(port=manual_port)
        assert service.port.name == "manual"

    def test_injectable_with_container_resolution(self):
        """Test automatic dependency resolution from container."""

        @injectable
        class ServiceWithDependency:
            def __init__(self, port: MockPort, service: MockService):
                self.port = port
                self.service = service

        with patch("src.infrastructure.di.container.get_container", return_value=self.container):
            # Create instance - dependencies should be auto-resolved
            instance = ServiceWithDependency()

            assert isinstance(instance.port, MockAdapter)
            assert instance.port.name == "injected"
            assert isinstance(instance.service, MockServiceImpl)
            assert instance.service.value == 100

    def test_injectable_with_optional_dependencies(self):
        """Test handling of Optional dependencies."""

        @injectable
        class ServiceWithOptional:
            def __init__(self, port: MockPort, optional_service: Optional[MockService] = None):
                self.port = port
                self.optional_service = optional_service

        with patch("src.infrastructure.di.container.get_container", return_value=self.container):
            instance = ServiceWithOptional()

            # Required dependency resolved
            assert isinstance(instance.port, MockAdapter)
            # Optional dependency also resolved since it's available
            assert isinstance(instance.optional_service, MockServiceImpl)

    def test_injectable_with_optional_unavailable(self):
        """Test Optional dependencies when service not available."""

        class UnavailableService:
            pass

        @injectable
        class ServiceWithUnavailableOptional:
            def __init__(self, port: MockPort, unavailable: Optional[UnavailableService] = None):
                self.port = port
                self.unavailable = unavailable

        with patch("src.infrastructure.di.container.get_container", return_value=self.container):
            instance = ServiceWithUnavailableOptional()

            # Required dependency resolved
            assert isinstance(instance.port, MockAdapter)
            # Optional dependency falls back to None
            assert instance.unavailable is None

    def test_injectable_with_mixed_parameters(self):
        """Test mixing auto-resolved and manual parameters."""

        @injectable
        class MixedService:
            def __init__(self, port: MockPort, manual_param: str, service: MockService):
                self.port = port
                self.manual_param = manual_param
                self.service = service

        with patch("src.infrastructure.di.container.get_container", return_value=self.container):
            instance = MixedService(manual_param="test_value")

            # Auto-resolved dependencies
            assert isinstance(instance.port, MockAdapter)
            assert isinstance(instance.service, MockServiceImpl)
            # Manual parameter
            assert instance.manual_param == "test_value"

    def test_injectable_with_defaults(self):
        """Test handling of default parameter values."""

        @injectable
        class ServiceWithDefaults:
            def __init__(
                self,
                port: MockPort,
                default_param: str = "default",
                service: Optional[MockService] = None,
            ):
                self.port = port
                self.default_param = default_param
                self.service = service

        with patch("src.infrastructure.di.container.get_container", return_value=self.container):
            instance = ServiceWithDefaults()

            assert isinstance(instance.port, MockAdapter)
            assert instance.default_param == "default"
            assert isinstance(instance.service, MockServiceImpl)

    def test_injectable_error_handling(self):
        """Test error handling in dependency resolution."""

        @injectable
        class ServiceWithError:
            def __init__(self, port: MockPort):
                self.port = port

        # Mock container that raises exception
        error_container = Mock()
        error_container.get.side_effect = Exception("Container error")

        with patch(
            "src.infrastructure.di.container.get_container",
            return_value=error_container,
        ):
            # Should raise exception since required dependency can't be resolved
            with pytest.raises(TypeError):  # Missing required argument
                ServiceWithError()

    def test_injectable_info_extraction(self):
        """Test extracting information about injectable classes."""

        @injectable
        class InfoMockService:
            def __init__(
                self,
                port: MockPort,
                optional: Optional[MockService] = None,
                manual: str = "default",
            ):
                self.port = port
                self.optional = optional
                self.manual = manual

        info = get_injectable_info(InfoMockService)

        assert info["class_name"] == "InfoMockService"
        assert info["total_dependencies"] == 3

        deps = info["dependencies"]
        assert "port" in deps
        assert deps["port"]["type"] == MockPort
        assert not deps["port"]["optional"]

        assert "optional" in deps
        assert deps["optional"]["optional"]
        assert deps["optional"]["has_default"]

        assert "manual" in deps
        assert deps["manual"]["has_default"]
        assert deps["manual"]["default"] == "default"

    def test_non_injectable_class(self):
        """Test behavior with non-injectable classes."""

        class RegularClass:
            def __init__(self, param: str):
                self.param = param

        assert not is_injectable(RegularClass)
        info = get_injectable_info(RegularClass)
        assert info == {}

    def test_injectable_preserves_original_behavior(self):
        """Test that injectable decorator preserves original constructor behavior."""

        @injectable
        class PreservationTest:
            def __init__(self, required: str, optional: Optional[str] = None):
                self.required = required
                self.optional = optional

        # Manual instantiation should work exactly as before
        instance = PreservationTest("test", "optional_value")
        assert instance.required == "test"
        assert instance.optional == "optional_value"

        # Partial manual instantiation
        instance2 = PreservationTest("test2")
        assert instance2.required == "test2"
        assert instance2.optional is None


class TestInjectableIntegration:
    """Integration tests for injectable decorator with real DI container."""

    def test_injectable_with_real_logging_port(self):
        """Test injectable with actual LoggingPort (the problematic case)."""

        @injectable
        class ServiceWithLogging:
            def __init__(self, logger: Optional[LoggingPort] = None):
                self.logger = logger

        # This should not raise the 'str' object has no attribute '__name__' error
        container = DIContainer()

        # Register a mock LoggingPort
        mock_logger = Mock(spec=LoggingPort)
        container.register_singleton(LoggingPort, lambda c: mock_logger)

        with patch("src.infrastructure.di.container.get_container", return_value=container):
            instance = ServiceWithLogging()
            assert instance.logger == mock_logger

    def test_injectable_command_bus_pattern(self):
        """Test the specific CommandBus pattern that was causing issues."""

        # Mock the EventPublisherPort
        class MockEventPublisher:
            pass

        @injectable
        class TestCommandBus:
            def __init__(
                self,
                logger: Optional[LoggingPort] = None,
                event_publisher: Optional[MockEventPublisher] = None,
            ):
                self.logger = logger
                self.event_publisher = event_publisher

        container = DIContainer()
        container.register_singleton(LoggingPort, lambda c: Mock(spec=LoggingPort))
        container.register_singleton(MockEventPublisher, lambda c: MockEventPublisher())

        with patch("src.infrastructure.di.container.get_container", return_value=container):
            # This should work without the Optional[LoggingPort] error
            bus = TestCommandBus()
            assert bus.logger is not None
            assert bus.event_publisher is not None
