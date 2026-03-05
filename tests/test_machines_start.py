"""Test for machines start command implementation."""

import argparse
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.mark.asyncio
async def test_machines_start_specific_ids():
    """Test starting specific machine IDs."""
    # Arrange
    args = argparse.Namespace()
    args.machine_ids = ["i-123", "i-456"]
    args.all = False

    # Mock the dependencies
    with patch("interface.machine_command_handlers.get_container") as mock_get_container:
        # Setup mocks
        mock_container = Mock()
        mock_get_container.return_value = mock_container

        mock_instance_manager = Mock()
        mock_instance_manager.start_instances.return_value = {"i-123": True, "i-456": True}

        mock_command_bus = AsyncMock()

        def mock_get(service_type):
            if hasattr(service_type, "__name__") and service_type.__name__ == "CommandBus":
                return mock_command_bus
            else:
                return mock_instance_manager

        mock_container.get.side_effect = mock_get

        # Act
        from interface.machine_command_handlers import handle_start_machines

        result = await handle_start_machines(args)

        # Assert
        assert result["success"] is True
        assert "i-123" in result["started_machines"]
        assert "i-456" in result["started_machines"]


@pytest.mark.asyncio
async def test_machines_start_all():
    """Test starting all stopped machines."""
    # Arrange
    args = argparse.Namespace()
    args.machine_ids = []
    args.all = True

    # Mock the dependencies
    with patch("interface.machine_command_handlers.get_container") as mock_get_container:
        # Setup mocks
        mock_container = Mock()
        mock_get_container.return_value = mock_container

        mock_query_bus = AsyncMock()
        mock_query_bus.execute.return_value = [
            {"machine_id": "i-stopped1"},
            {"machine_id": "i-stopped2"},
        ]

        mock_instance_manager = Mock()
        mock_instance_manager.start_instances.return_value = {
            "i-stopped1": True,
            "i-stopped2": True,
        }

        mock_command_bus = AsyncMock()

        def mock_get(service_type):
            if service_type.__name__ == "QueryBus":
                return mock_query_bus
            elif service_type.__name__ == "CommandBus":
                return mock_command_bus
            else:
                return mock_instance_manager

        mock_container.get.side_effect = mock_get

        # Act
        from interface.machine_command_handlers import handle_start_machines

        result = await handle_start_machines(args)

        # Assert
        assert result["success"] is True


@pytest.mark.asyncio
async def test_machines_start_validation_errors():
    """Test validation errors."""
    from interface.machine_command_handlers import handle_start_machines

    # Test: no machine IDs and no --all
    args1 = argparse.Namespace()
    args1.machine_ids = []
    args1.all = False

    result1 = await handle_start_machines(args1)
    assert result1["error"] == "No machines specified"

    # Test: both specific IDs and --all
    args2 = argparse.Namespace()
    args2.machine_ids = ["i-123"]
    args2.all = True

    result2 = await handle_start_machines(args2)
    assert result2["error"] == "Cannot use --all with specific machine IDs"


if __name__ == "__main__":
    pytest.main([__file__])
