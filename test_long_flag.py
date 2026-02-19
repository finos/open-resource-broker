#!/usr/bin/env python3
"""Test for --long flag in templates list command."""

import pytest
from unittest.mock import Mock, patch
import argparse

from application.dto.queries import ListTemplatesQuery
from interface.template_command_handlers import handle_list_templates


@pytest.mark.asyncio
async def test_templates_list_long_flag_adds_detailed_fields():
    """Test that --long flag includes detailed fields in query."""
    # Arrange
    args = argparse.Namespace()
    args.provider_api = None
    args.active_only = True
    args.long = True  # The new flag

    mock_query_bus = Mock()
    mock_container = Mock()
    mock_container.get.return_value = mock_query_bus

    # Mock templates response
    mock_templates = [
        Mock(
            model_dump=lambda: {
                "template_id": "test-template",
                "name": "Test Template",
                "description": "Test description",
                "provider_api": "EC2Fleet",
                "max_instances": 10,
                # Detailed fields that should be included with --long
                "storage_encryption": True,
                "network_zones": ["us-east-1a"],
                "security_group_ids": ["sg-123"],
                "tags": {"env": "test"},
                "metadata": {"version": "1.0"},
            }
        )
    ]
    mock_query_bus.execute.return_value = mock_templates

    # Act
    with patch("interface.template_command_handlers.get_container", return_value=mock_container):
        await handle_list_templates(args)

    # Assert
    # Verify the query was created with include_detailed_fields=True
    call_args = mock_query_bus.execute.call_args[0][0]
    assert isinstance(call_args, ListTemplatesQuery)
    assert hasattr(call_args, "include_detailed_fields")
    assert call_args.include_detailed_fields is True


@pytest.mark.asyncio
async def test_templates_list_without_long_flag_excludes_detailed_fields():
    """Test that without --long flag, detailed fields are excluded."""
    # Arrange
    args = argparse.Namespace()
    args.provider_api = None
    args.active_only = True
    # No long attribute (default behavior)

    mock_query_bus = Mock()
    mock_container = Mock()
    mock_container.get.return_value = mock_query_bus

    mock_templates = []
    mock_query_bus.execute.return_value = mock_templates

    # Act
    with patch("interface.template_command_handlers.get_container", return_value=mock_container):
        await handle_list_templates(args)

    # Assert
    # Verify the query was created with include_detailed_fields=False (default)
    call_args = mock_query_bus.execute.call_args[0][0]
    assert isinstance(call_args, ListTemplatesQuery)
    assert hasattr(call_args, "include_detailed_fields")
    assert call_args.include_detailed_fields is False


if __name__ == "__main__":
    import asyncio

    async def run_tests():
        await test_templates_list_long_flag_adds_detailed_fields()
        await test_templates_list_without_long_flag_excludes_detailed_fields()
        print("All tests passed!")

    asyncio.run(run_tests())
