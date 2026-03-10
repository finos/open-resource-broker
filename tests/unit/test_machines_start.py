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

    with patch("orb.interface.machine_command_handlers.get_container") as mock_get_container:
        mock_container = Mock()
        mock_get_container.return_value = mock_container

        mock_provider_port = Mock()
        mock_provider_port.select_active_provider.return_value = Mock(provider_name="aws-default")
        mock_provider_port.execute_operation = AsyncMock(
            return_value=Mock(success=True, data={"results": {"i-123": True, "i-456": True}})
        )

        mock_command_bus = AsyncMock()

        def mock_get(service_type):
            name = getattr(service_type, "__name__", "")
            if name == "CommandBus":
                return mock_command_bus
            if name == "ProviderSelectionPort":
                return mock_provider_port
            return Mock()

        mock_container.get.side_effect = mock_get

        from orb.interface.machine_command_handlers import handle_start_machines

        result = await handle_start_machines(args)

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

    with patch("orb.interface.machine_command_handlers.get_container") as mock_get_container:
        mock_container = Mock()
        mock_get_container.return_value = mock_container

        mock_query_bus = AsyncMock()
        mock_query_bus.execute.return_value = [
            {"machine_id": "i-stopped1"},
            {"machine_id": "i-stopped2"},
        ]

        mock_provider_port = Mock()
        mock_provider_port.select_active_provider.return_value = Mock(provider_name="aws-default")
        mock_provider_port.execute_operation = AsyncMock(
            return_value=Mock(
                success=True,
                data={"results": {"i-stopped1": True, "i-stopped2": True}},
            )
        )

        mock_command_bus = AsyncMock()

        def mock_get(service_type):
            name = getattr(service_type, "__name__", "")
            if name == "QueryBus":
                return mock_query_bus
            if name == "CommandBus":
                return mock_command_bus
            if name == "ProviderSelectionPort":
                return mock_provider_port
            return Mock()

        mock_container.get.side_effect = mock_get

        from orb.interface.machine_command_handlers import handle_start_machines

        result = await handle_start_machines(args)

        assert result["success"] is True


@pytest.mark.asyncio
async def test_machines_start_validation_errors():
    """Test validation errors."""
    from orb.interface.machine_command_handlers import handle_start_machines

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
