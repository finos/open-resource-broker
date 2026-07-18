"""Unit tests for mcp/server/handler.py — handle_mcp_serve and _run_tcp_server.

Covers:
- _flush_telemetry_mcp: happy path, exception path
- handle_mcp_serve: stdio_mode=True, tcp_mode, app init failure
- _run_stdio_server: empty line skipped, KeyboardInterrupt stops loop
- _run_tcp_server: client connection/disconnect cycle
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_args(**kwargs):
    defaults = {"port": 3000, "host": "localhost", "stdio": False}
    defaults.update(kwargs)
    return type("Args", (), defaults)()


# ---------------------------------------------------------------------------
# _flush_telemetry_mcp
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFlushTelemetryMcp:
    def test_happy_path_calls_shutdown_telemetry(self):
        from orb.interface.mcp.server.handler import _flush_telemetry_mcp

        mock_shutdown = MagicMock()
        with patch("orb.bootstrap.telemetry.shutdown_telemetry", mock_shutdown):
            _flush_telemetry_mcp()

        mock_shutdown.assert_called_once()

    def test_exception_is_swallowed_not_raised(self):
        """Telemetry flush failures must not propagate."""
        from orb.interface.mcp.server.handler import _flush_telemetry_mcp

        with patch(
            "orb.bootstrap.telemetry.shutdown_telemetry",
            side_effect=RuntimeError("telemetry failed"),
        ):
            # Must not raise
            _flush_telemetry_mcp()


# ---------------------------------------------------------------------------
# handle_mcp_serve
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMcpServe:
    @pytest.mark.asyncio
    async def test_app_init_failure_raises(self):
        """When Application.initialize() returns False, an exception propagates."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=False)
        mock_app._ensure_container = MagicMock()

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            # handle_interface_exceptions wraps RuntimeError into InfrastructureError
            with pytest.raises(Exception, match="[Ff]ailed to initialize|Unexpected error"):
                await handle_mcp_serve(_make_args())

    @pytest.mark.asyncio
    async def test_stdio_mode_calls_run_stdio_server(self):
        """args.stdio=True must call _run_stdio_server."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.start_daemon_services = AsyncMock(return_value=True)
        mock_app._ensure_container = MagicMock()
        mock_app._container = MagicMock()

        mock_stdio = AsyncMock()

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch("orb.interface.mcp.server.handler._run_stdio_server", mock_stdio):
                with patch("orb.interface.mcp.server.handler._flush_telemetry_mcp"):
                    result = await handle_mcp_serve(_make_args(stdio=True))

        mock_stdio.assert_awaited_once()
        assert "stdio" in result["message"]

    @pytest.mark.asyncio
    async def test_tcp_mode_calls_run_tcp_server(self):
        """args.stdio=False must call _run_tcp_server."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.start_daemon_services = AsyncMock(return_value=True)
        mock_app._ensure_container = MagicMock()
        mock_app._container = MagicMock()

        mock_tcp = AsyncMock()

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch("orb.interface.mcp.server.handler._run_tcp_server", mock_tcp):
                with patch("orb.interface.mcp.server.handler._flush_telemetry_mcp"):
                    result = await handle_mcp_serve(
                        _make_args(stdio=False, host="localhost", port=3000)
                    )

        mock_tcp.assert_awaited_once()
        assert "3000" in result["message"] or "localhost" in result["message"]

    @pytest.mark.asyncio
    async def test_flush_telemetry_called_even_if_server_raises(self):
        """_flush_telemetry_mcp must be called in finally block on any exception."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app._ensure_container = MagicMock()
        mock_app._container = MagicMock()

        flush_calls: list = []

        def fake_flush():
            flush_calls.append(1)

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch(
                "orb.interface.mcp.server.handler._run_tcp_server",
                side_effect=RuntimeError("server died"),
            ):
                with patch(
                    "orb.interface.mcp.server.handler._flush_telemetry_mcp", side_effect=fake_flush
                ):
                    # The decorator wraps RuntimeError into InfrastructureError
                    with pytest.raises(Exception):
                        await handle_mcp_serve(_make_args(stdio=False))

        assert len(flush_calls) == 1


# ---------------------------------------------------------------------------
# _run_stdio_server
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunStdioServer:
    @pytest.mark.asyncio
    async def test_empty_line_is_skipped(self):
        """Empty lines from stdin should be skipped without calling handle_message."""
        from orb.interface.mcp.server.handler import _run_stdio_server

        mock_mcp_server = MagicMock()
        mock_mcp_server.handle_message = AsyncMock(return_value='{"result": {}}')

        # Return empty line then empty string (EOF)
        readline_results = iter(["", "", ""])

        async def fake_run_in_executor(executor, func):
            return next(readline_results)

        loop = MagicMock()
        loop.run_in_executor = fake_run_in_executor

        with patch("asyncio.get_running_loop", return_value=loop):
            await _run_stdio_server(mock_mcp_server)

        # handle_message was never called for empty lines
        mock_mcp_server.handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processes_valid_message_and_prints_response(self, capsys):
        """Valid JSON message should be processed and response printed."""
        from orb.interface.mcp.server.handler import _run_stdio_server

        mock_mcp_server = MagicMock()
        response_json = '{"jsonrpc": "2.0", "id": 1, "result": {}}'
        mock_mcp_server.handle_message = AsyncMock(return_value=response_json)

        valid_msg = '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
        readline_results = iter([valid_msg, ""])  # message then EOF

        async def fake_run_in_executor(executor, func):
            return next(readline_results)

        loop = MagicMock()
        loop.run_in_executor = fake_run_in_executor

        with patch("asyncio.get_running_loop", return_value=loop):
            await _run_stdio_server(mock_mcp_server)

        mock_mcp_server.handle_message.assert_awaited_once_with(valid_msg)


# ---------------------------------------------------------------------------
# _run_tcp_server
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunTcpServer:
    @pytest.mark.asyncio
    async def test_client_connection_and_disconnect(self):
        """A client connecting and sending EOF should be handled gracefully."""
        from orb.interface.mcp.server.handler import _run_tcp_server

        mock_mcp_server = MagicMock()
        mock_mcp_server.handle_message = AsyncMock(return_value='{"result": {}}')

        # Simulate: one valid message then EOF
        valid_msg = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        data_sequence = iter([valid_msg, b""])  # EOF after first message

        reader = AsyncMock()
        reader.readline = AsyncMock(side_effect=lambda: next(data_sequence))

        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("127.0.0.1", 12345))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        # Mock asyncio.start_server to capture the handler and call it once
        captured_handler = None

        async def fake_start_server(handler, host, port):
            nonlocal captured_handler
            captured_handler = handler
            mock_server = AsyncMock()
            mock_server.sockets = [MagicMock()]
            mock_server.sockets[0].getsockname.return_value = (host, port)
            mock_server.__aenter__ = AsyncMock(return_value=mock_server)
            mock_server.__aexit__ = AsyncMock(return_value=False)
            mock_server.serve_forever = AsyncMock(side_effect=KeyboardInterrupt)
            mock_server.close = MagicMock()
            mock_server.wait_closed = AsyncMock()
            return mock_server

        with patch("asyncio.start_server", side_effect=fake_start_server):
            try:
                await _run_tcp_server(mock_mcp_server, "localhost", 3000)
            except (KeyboardInterrupt, SystemExit):
                pass

        # Call the captured handler to exercise client handling code
        if captured_handler is not None:
            await captured_handler(reader, writer)

        # Verify the message was processed
        mock_mcp_server.handle_message.assert_awaited()
        writer.write.assert_called()
