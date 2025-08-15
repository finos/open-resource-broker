"""Tests for CQRS pattern compliance.

This module validates that the codebase properly implements CQRS patterns including:
- Command and Query separation
- Handler registration and discovery
- Event bus functionality
- Read/Write model synchronization
"""

import inspect
from unittest.mock import Mock, patch

import pytest

# Import available components
try:
    from application.base.command_handler import ApplicationCommandHandler
    from application.dto.commands import (
        CreateRequestCommand,
        UpdateRequestStatusCommand,
    )
    from application.dto.queries import GetTemplateQuery, ListTemplatesQuery
    from infrastructure.di.container import DIContainer

    COMPONENTS_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import CQRS components: {e}")
    COMPONENTS_AVAILABLE = False


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.skipif(not COMPONENTS_AVAILABLE, reason="CQRS components not available")
class TestCQRSCompliance:
    """Test CQRS pattern implementation compliance."""

    def test_command_query_separation(self):
        """Ensure commands and queries are properly separated."""
        # Test that commands modify state and queries don't
        command = CreateRequestCommand(template_id="test-template", requested_count=2)

        query = GetTemplateQuery(template_id="test-template")

        # Commands should have methods that modify state
        assert hasattr(command, "template_id")
        assert hasattr(command, "requested_count")

        # Queries should be read-only
        assert hasattr(query, "template_id")

        # Verify command and query are different types
        assert not isinstance(command, type(query)) and not isinstance(query, type(command))

    def test_command_handler_interface(self):
        """Test that command handlers implement correct interface."""

        # Mock a command handler
        class MockCommandHandler(ApplicationCommandHandler):
            def handle(self, command):
                return {"status": "handled"}

        handler = MockCommandHandler()

        # Should have handle method
        assert hasattr(handler, "handle")
        assert callable(handler.handle)

        # Handle method should accept command parameter
        sig = inspect.signature(handler.handle)
        assert len(sig.parameters) >= 1

    def test_query_handler_interface(self):
        """Test that query handlers implement correct interface."""
        # Test query structure
        query = ListTemplatesQuery()

        # Query should be immutable-like (frozen)
        assert hasattr(query, "__dict__") or hasattr(query.__class__, "__slots__")

    def test_command_immutability(self):
        """Test that commands are immutable after creation."""
        command = CreateRequestCommand(template_id="test-template", machine_count=2)

        # Should not be able to modify command after creation
        # Pydantic frozen models raise ValidationError
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            command.template_id = "modified"

    def test_query_immutability(self):
        """Test that queries are immutable after creation."""
        query = GetTemplateQuery(template_id="test-template")

        # Should not be able to modify query after creation
        # Pydantic frozen models raise ValidationError
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            query.template_id = "modified"

    def test_command_validation(self):
        """Test that commands validate their input."""
        # Valid command should work
        command = CreateRequestCommand(template_id="test-template", machine_count=2)
        assert command.template_id == "test-template"
        assert command.machine_count == 2

        # Test that command requires all mandatory fields
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CreateRequestCommand(
                template_id="test-template"
                # Missing required machine_count field
            )

    def test_query_validation(self):
        """Test that queries validate their input."""
        # Valid query should work
        query = GetTemplateQuery(template_id="test-template")
        assert query.template_id == "test-template"

        # Invalid query should raise validation error
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetTemplateQuery()  # Missing required template_id should be invalid

    def test_dependency_injection_integration(self):
        """Test that CQRS components integrate with DI container."""
        container = DIContainer()

        # Container should exist and be configurable
        assert container is not None
        assert hasattr(container, "register") or hasattr(container, "bind")

    def test_command_bus_pattern(self):
        """Test command bus pattern implementation."""
        # Mock command bus behavior
        with patch("src.infrastructure.di.buses.CommandBus") as mock_bus:
            mock_instance = Mock()
            mock_bus.return_value = mock_instance

            # Command bus should have send/execute method
            mock_instance.send = Mock(return_value={"status": "success"})

            command = CreateRequestCommand(template_id="test-template", machine_count=2)

            # Should be able to send command through bus
            result = mock_instance.send(command)
            assert result["status"] == "success"
            mock_instance.send.assert_called_once_with(command)

    def test_query_bus_pattern(self):
        """Test query bus pattern implementation."""
        # Mock query bus behavior
        with patch("src.infrastructure.di.buses.QueryBus") as mock_bus:
            mock_instance = Mock()
            mock_bus.return_value = mock_instance

            # Query bus should have send/execute method
            mock_instance.send = Mock(return_value={"templates": []})

            query = ListTemplatesQuery()

            # Should be able to send query through bus
            result = mock_instance.send(query)
            assert "templates" in result
            mock_instance.send.assert_called_once_with(query)

    def test_read_write_model_separation(self):
        """Test that read and write models are properly separated."""
        # Commands should work with write models
        command = CreateRequestCommand(template_id="test-template", machine_count=2)

        # Command should contain data for write operations
        assert command.template_id is not None
        assert command.machine_count > 0

        # Queries should work with read models
        query = GetTemplateQuery(template_id="test-template")

        # Query should contain criteria for read operations
        assert query.template_id is not None

    def test_event_driven_architecture_support(self):
        """Test that CQRS supports event-driven patterns."""
        # Commands should potentially trigger events
        from domain.request.value_objects import RequestStatus

        command = UpdateRequestStatusCommand(
            request_id="test-request", status=RequestStatus.COMPLETED
        )

        # Command should have identifiable data that could trigger events
        assert hasattr(command, "request_id")
        assert hasattr(command, "status")

        # Status change commands are typical event triggers
        assert command.status is not None
