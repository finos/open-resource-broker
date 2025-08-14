"""Unit tests for SDK method discovery following existing test patterns."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.sdk.discovery import MethodInfo, SDKMethodDiscovery
from src.sdk.exceptions import HandlerDiscoveryError, MethodExecutionError


class MockQuery:
    """Mock query class for testing."""

    def __init__(self, **kwargs):
        """Initialize the instance."""
        self.kwargs = kwargs


# Set the __name__ attribute properly
MockQuery.__name__ = "ListTemplatesQuery"


class MockCommand:
    """Mock command class for testing."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


# Set the __name__ attribute properly
MockCommand.__name__ = "CreateRequestCommand"


class MockHandler:
    """Mock handler class for testing."""


class TestSDKMethodDiscovery:
    """Test cases for SDKMethodDiscovery following existing test patterns."""

    def test_initialization(self):
        """Test discovery service initialization."""
        discovery = SDKMethodDiscovery()

        assert discovery._method_info_cache == {}
        assert discovery.list_available_methods() == []

    def test_query_to_method_name(self):
        """Test converting query class names to method names."""
        discovery = SDKMethodDiscovery()

        # Test various query name patterns
        assert discovery._query_to_method_name(MockQuery) == "list_templates"

        # Test with different patterns
        class GetRequestStatusQuery:
            __name__ = "GetRequestStatusQuery"

        assert discovery._query_to_method_name(GetRequestStatusQuery) == "get_request_status"

    def test_command_to_method_name(self):
        """Test converting command class names to method names."""
        discovery = SDKMethodDiscovery()

        # Test various command name patterns
        assert discovery._command_to_method_name(MockCommand) == "create_request"

        # Test with different patterns
        class UpdateMachineStatusCommand:
            __name__ = "UpdateMachineStatusCommand"

        assert (
            discovery._command_to_method_name(UpdateMachineStatusCommand) == "update_machine_status"
        )

    def test_camel_to_snake(self):
        """Test camelCase to snake_case conversion."""
        discovery = SDKMethodDiscovery()

        # Test various patterns
        assert discovery._camel_to_snake("ListTemplates") == "list_templates"
        assert discovery._camel_to_snake("GetRequestStatus") == "get_request_status"
        assert discovery._camel_to_snake("CreateRequest") == "create_request"
        assert discovery._camel_to_snake("SimpleWord") == "simple_word"
        assert discovery._camel_to_snake("XMLParser") == "xml_parser"

    def test_generate_method_description(self):
        """Test method description generation."""
        discovery = SDKMethodDiscovery()

        description = discovery._generate_method_description("list_templates", "query")
        assert description == "List Templates - Query operation"

        description = discovery._generate_method_description("create_request", "command")
        assert description == "Create Request - Command operation"

    @pytest.mark.asyncio
    async def test_discover_sdk_methods_success(self):
        """Test successful method discovery."""
        discovery = SDKMethodDiscovery()
        mock_service = Mock()

        # Mock the handler registries
        mock_query_handlers = {MockQuery: MockHandler}
        mock_command_handlers = {MockCommand: MockHandler}

        with patch(
            "src.sdk.discovery.get_registered_query_handlers",
            return_value=mock_query_handlers,
        ):
            with patch(
                "src.sdk.discovery.get_registered_command_handlers",
                return_value=mock_command_handlers,
            ):
                methods = await discovery.discover_sdk_methods(mock_service)

                assert "list_templates" in methods
                assert "create_request" in methods
                assert len(methods) == 2

                # Check that methods are callable
                assert callable(methods["list_templates"])
                assert callable(methods["create_request"])

    @pytest.mark.asyncio
    async def test_discover_sdk_methods_failure(self):
        """Test method discovery failure handling."""
        discovery = SDKMethodDiscovery()
        mock_service = Mock()

        # Mock handler registry to raise exception
        with patch(
            "src.sdk.discovery.get_registered_query_handlers",
            side_effect=Exception("Registry error"),
        ):
            with pytest.raises(HandlerDiscoveryError, match="Failed to discover SDK methods"):
                await discovery.discover_sdk_methods(mock_service)

    def test_get_method_info_existing(self):
        """Test getting method info for existing method."""
        discovery = SDKMethodDiscovery()

        # Add a method info to cache
        method_info = MethodInfo(
            name="test_method",
            description="Test method",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=MockQuery,
        )
        discovery._method_info_cache["test_method"] = method_info

        result = discovery.get_method_info("test_method")

        assert result == method_info
        assert result.name == "test_method"
        assert result.description == "Test method"

    def test_get_method_info_nonexistent(self):
        """Test getting method info for nonexistent method."""
        discovery = SDKMethodDiscovery()

        result = discovery.get_method_info("nonexistent_method")

        assert result is None

    def test_list_available_methods(self):
        """Test listing available methods."""
        discovery = SDKMethodDiscovery()

        # Add methods to cache
        discovery._method_info_cache["method1"] = Mock()
        discovery._method_info_cache["method2"] = Mock()

        methods = discovery.list_available_methods()

        assert "method1" in methods
        assert "method2" in methods
        assert len(methods) == 2

    @pytest.mark.asyncio
    async def test_query_method_execution_success(self):
        """Test successful query method execution."""
        discovery = SDKMethodDiscovery()
        mock_service = Mock()
        mock_service.execute_query = AsyncMock(return_value="query_result")

        # Create method info
        method_info = MethodInfo(
            name="test_query",
            description="Test query",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=MockQuery,
        )

        # Create query method
        method = discovery._create_query_method(mock_service, MockQuery, method_info)

        # Execute method
        result = await method(test_param="value")

        assert result == "query_result"
        mock_service.execute_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_method_execution_failure(self):
        """Test query method execution failure."""
        discovery = SDKMethodDiscovery()
        mock_service = Mock()
        mock_service.execute_query = AsyncMock(side_effect=Exception("Execution error"))

        # Create method info
        method_info = MethodInfo(
            name="test_query",
            description="Test query",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=MockQuery,
        )

        # Create query method
        method = discovery._create_query_method(mock_service, MockQuery, method_info)

        # Execute method should raise MethodExecutionError
        with pytest.raises(MethodExecutionError, match="Failed to execute test_query"):
            await method(test_param="value")

    @pytest.mark.asyncio
    async def test_command_method_execution_success(self):
        """Test successful command method execution."""
        discovery = SDKMethodDiscovery()
        mock_service = Mock()
        mock_service.execute_command = AsyncMock(return_value="command_result")

        # Create method info
        method_info = MethodInfo(
            name="test_command",
            description="Test command",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=MockCommand,
        )

        # Create command method
        method = discovery._create_command_method(mock_service, MockCommand, method_info)

        # Execute method
        result = await method(test_param="value")

        assert result == "command_result"
        mock_service.execute_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_method_execution_failure(self):
        """Test command method execution failure."""
        discovery = SDKMethodDiscovery()
        mock_service = Mock()
        mock_service.execute_command = AsyncMock(side_effect=Exception("Execution error"))

        # Create method info
        method_info = MethodInfo(
            name="test_command",
            description="Test command",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=MockCommand,
        )

        # Create command method
        method = discovery._create_command_method(mock_service, MockCommand, method_info)

        # Execute method should raise MethodExecutionError
        with pytest.raises(MethodExecutionError, match="Failed to execute test_command"):
            await method(test_param="value")
