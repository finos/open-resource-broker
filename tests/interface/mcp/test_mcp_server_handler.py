"""Tests for MCP server handler functionality."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.interface.mcp.server.handler import handle_mcp_serve


class TestMCPServerHandler:
    """Test MCP server handler functionality."""

    @pytest.fixture
    def mock_app(self):
        """Create mock application instance."""
        app = Mock()
        app.get_query_bus = Mock(return_value=Mock())
        app.get_command_bus = Mock(return_value=Mock())
        return app

    @pytest.fixture
    def stdio_args(self):
        """Create args for stdio mode."""
        return SimpleNamespace(stdio=True, port=3000, host="localhost")

    @pytest.fixture
    def tcp_args(self):
        """Create args for TCP mode."""
        return SimpleNamespace(stdio=False, port=3000, host="localhost")

    @pytest.mark.asyncio
    async def test_handle_mcp_serve_stdio_mode(self, stdio_args, mock_app):
        """Test MCP serve handler in stdio mode."""
        with patch("src.interface.mcp.server.handler._run_stdio_server") as mock_stdio:
            mock_stdio.return_value = None

            result = await handle_mcp_serve(stdio_args, mock_app)

            assert result["message"] == "MCP server started in stdio mode"
            mock_stdio.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_mcp_serve_tcp_mode(self, tcp_args, mock_app):
        """Test MCP serve handler in TCP mode."""
        with patch("src.interface.mcp.server.handler._run_tcp_server") as mock_tcp:
            mock_tcp.return_value = None

            result = await handle_mcp_serve(tcp_args, mock_app)

            assert result["message"] == "MCP server started on localhost:3000"
            mock_tcp.assert_called_once_with(mock_app, "localhost", 3000)

    @pytest.mark.asyncio
    async def test_stdio_server_message_handling(self, mock_app):
        """Test stdio server message handling."""
        from src.interface.mcp.server.core import OpenHFPluginMCPServer
        from src.interface.mcp.server.handler import _run_stdio_server

        mcp_server = OpenHFPluginMCPServer(app=mock_app)

        # Mock stdin/stdout
        with patch("sys.stdin") as mock_stdin, patch(
            "builtins.print"
        ) as mock_print, patch("asyncio.get_event_loop") as mock_loop:

            # Mock readline to return a test message then EOF
            mock_executor = Mock()
            mock_loop.return_value.run_in_executor = mock_executor
            mock_executor.side_effect = [
                '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}',
                "",  # EOF
            ]

            # Run stdio server (should exit on EOF)
            await _run_stdio_server(mcp_server)

            # Verify print was called (response sent)
            assert mock_print.called

    @pytest.mark.asyncio
    async def test_tcp_server_client_handling(self, mock_app):
        """Test TCP server client handling."""
        from src.interface.mcp.server.handler import _run_tcp_server

        # Mock asyncio.start_server
        with patch("asyncio.start_server") as mock_start_server:
            mock_server = Mock()
            mock_server.sockets = [Mock()]
            mock_server.sockets[0].getsockname.return_value = ("localhost", 3000)
            mock_server.__aenter__ = AsyncMock(return_value=mock_server)
            mock_server.__aexit__ = AsyncMock(return_value=None)
            mock_server.serve_forever = AsyncMock(side_effect=KeyboardInterrupt())
            mock_server.close = Mock()
            mock_server.wait_closed = AsyncMock()

            mock_start_server.return_value = mock_server

            # Run TCP server (should exit on KeyboardInterrupt)
            from src.interface.mcp.server.core import OpenHFPluginMCPServer

            mcp_server = OpenHFPluginMCPServer(app=mock_app)

            await _run_tcp_server(mcp_server, "localhost", 3000)

            # Verify server was started and cleaned up
            mock_start_server.assert_called_once()
            mock_server.close.assert_called_once()
            mock_server.wait_closed.assert_called_once()

    def test_args_extraction(self, stdio_args, tcp_args):
        """Test argument extraction from args object."""
        # Test stdio args
        assert getattr(stdio_args, "stdio", False) is True
        assert getattr(stdio_args, "port", 3000) == 3000
        assert getattr(stdio_args, "host", "localhost") == "localhost"

        # Test TCP args
        assert getattr(tcp_args, "stdio", False) is False
        assert getattr(tcp_args, "port", 3000) == 3000
        assert getattr(tcp_args, "host", "localhost") == "localhost"

    @pytest.mark.asyncio
    async def test_error_handling_in_handler(self, stdio_args, mock_app):
        """Test error handling in MCP serve handler."""
        with patch("src.interface.mcp.server.handler._run_stdio_server") as mock_stdio:
            mock_stdio.side_effect = Exception("Test error")

            # Should raise the exception (not caught in handler)
            with pytest.raises(Exception, match="Test error"):
                await handle_mcp_serve(stdio_args, mock_app)
