"""Unit tests for orb.interface.mcp.server.handler — all branch paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_mock_app():
    app = MagicMock()
    app.initialize = AsyncMock(return_value=True)
    app._ensure_container = MagicMock()
    app._container = MagicMock()
    return app


# ---------------------------------------------------------------------------
# _flush_telemetry_mcp
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFlushTelemetryMcp:
    def test_happy_path_calls_shutdown_telemetry(self):
        """_flush_telemetry_mcp calls shutdown_telemetry exactly once when available."""
        from orb.interface.mcp.server.handler import _flush_telemetry_mcp

        with patch("orb.bootstrap.telemetry.shutdown_telemetry") as mock_shutdown:
            _flush_telemetry_mcp()

        mock_shutdown.assert_called_once_with()

    def test_exception_in_shutdown_does_not_propagate(self):
        """If shutdown_telemetry raises, _flush_telemetry_mcp must not propagate."""
        from orb.interface.mcp.server.handler import _flush_telemetry_mcp

        with patch(
            "orb.bootstrap.telemetry.shutdown_telemetry",
            side_effect=RuntimeError("otel error"),
        ):
            # Must not raise
            _flush_telemetry_mcp()

    def test_import_error_does_not_propagate(self):
        """If shutdown_telemetry cannot be imported, _flush_telemetry_mcp must not raise."""
        from orb.interface.mcp.server.handler import _flush_telemetry_mcp

        with patch.dict("sys.modules", {"orb.bootstrap.telemetry": None}):
            # Must not raise
            _flush_telemetry_mcp()


# ---------------------------------------------------------------------------
# handle_mcp_serve
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMcpServe:
    @pytest.mark.asyncio
    async def test_stdio_mode_calls_run_stdio_server(self):
        """stdio=True → _run_stdio_server is called, not _run_tcp_server."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = _make_mock_app()
        mock_mcp_server = MagicMock()

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch(
                "orb.interface.mcp.server.handler.OpenResourceBrokerMCPServer",
                return_value=mock_mcp_server,
            ):
                with patch(
                    "orb.interface.mcp.server.handler._run_stdio_server",
                    new_callable=AsyncMock,
                ) as mock_stdio:
                    with patch(
                        "orb.interface.mcp.server.handler._run_tcp_server",
                        new_callable=AsyncMock,
                    ) as mock_tcp:
                        with patch("orb.interface.mcp.server.handler._flush_telemetry_mcp"):
                            args = MagicMock()
                            args.stdio = True
                            args.port = 3000
                            args.host = "localhost"

                            result = await handle_mcp_serve(args)

        mock_stdio.assert_awaited_once()
        mock_tcp.assert_not_awaited()
        assert "stdio" in result["message"]

    @pytest.mark.asyncio
    async def test_tcp_mode_calls_run_tcp_server(self):
        """stdio=False → _run_tcp_server is called with host/port."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = _make_mock_app()
        mock_mcp_server = MagicMock()

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch(
                "orb.interface.mcp.server.handler.OpenResourceBrokerMCPServer",
                return_value=mock_mcp_server,
            ):
                with patch(
                    "orb.interface.mcp.server.handler._run_stdio_server",
                    new_callable=AsyncMock,
                ) as mock_stdio:
                    with patch(
                        "orb.interface.mcp.server.handler._run_tcp_server",
                        new_callable=AsyncMock,
                    ) as mock_tcp:
                        with patch("orb.interface.mcp.server.handler._flush_telemetry_mcp"):
                            args = MagicMock()
                            args.stdio = False
                            args.port = 4000
                            args.host = "0.0.0.0"

                            result = await handle_mcp_serve(args)

        mock_tcp.assert_awaited_once_with(mock_mcp_server, "0.0.0.0", 4000)
        mock_stdio.assert_not_awaited()
        assert "4000" in result["message"]

    @pytest.mark.asyncio
    async def test_app_initialize_failure_raises_error(self):
        """app.initialize() returning False → exception is raised (wrapped by decorator)."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = _make_mock_app()
        mock_app.initialize = AsyncMock(return_value=False)

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch("orb.interface.mcp.server.handler._flush_telemetry_mcp"):
                args = MagicMock()
                args.stdio = False
                args.port = 3000
                args.host = "localhost"

                with pytest.raises(Exception, match="[Ff]ailed"):
                    await handle_mcp_serve(args)

    @pytest.mark.asyncio
    async def test_flush_telemetry_called_in_finally_on_success(self):
        """_flush_telemetry_mcp is called in finally block after successful run."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = _make_mock_app()
        mock_mcp_server = MagicMock()

        flush_calls: list[int] = []

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch(
                "orb.interface.mcp.server.handler.OpenResourceBrokerMCPServer",
                return_value=mock_mcp_server,
            ):
                with patch(
                    "orb.interface.mcp.server.handler._run_stdio_server",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "orb.interface.mcp.server.handler._flush_telemetry_mcp",
                        side_effect=lambda: flush_calls.append(1),
                    ):
                        args = MagicMock()
                        args.stdio = True
                        args.port = 3000
                        args.host = "localhost"

                        await handle_mcp_serve(args)

        assert flush_calls, "_flush_telemetry_mcp must be called in finally block"

    @pytest.mark.asyncio
    async def test_flush_telemetry_called_in_finally_on_error(self):
        """_flush_telemetry_mcp is called in finally block even when _run_tcp_server raises."""
        from orb.interface.mcp.server.handler import handle_mcp_serve

        mock_app = _make_mock_app()
        mock_mcp_server = MagicMock()

        flush_calls: list[int] = []

        with patch("orb.interface.mcp.server.handler.Application", return_value=mock_app):
            with patch(
                "orb.interface.mcp.server.handler.OpenResourceBrokerMCPServer",
                return_value=mock_mcp_server,
            ):
                with patch(
                    "orb.interface.mcp.server.handler._run_tcp_server",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("server crashed"),
                ):
                    with patch(
                        "orb.interface.mcp.server.handler._flush_telemetry_mcp",
                        side_effect=lambda: flush_calls.append(1),
                    ):
                        args = MagicMock()
                        args.stdio = False
                        args.port = 3000
                        args.host = "localhost"

                        with pytest.raises(Exception):
                            await handle_mcp_serve(args)

        assert flush_calls, "_flush_telemetry_mcp must be called in finally even on error"


# ---------------------------------------------------------------------------
# _run_stdio_server
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunStdioServer:
    @pytest.mark.asyncio
    async def test_empty_line_from_stdin_breaks_loop(self):
        """Empty bytes from stdin.readline → loop breaks cleanly."""
        import sys
        from io import StringIO

        from orb.interface.mcp.server.handler import _run_stdio_server

        mock_mcp = MagicMock()
        mock_mcp.handle_message = AsyncMock()

        orig_stdin = sys.stdin
        sys.stdin = StringIO("")
        try:
            await _run_stdio_server(mock_mcp)
        finally:
            sys.stdin = orig_stdin

        mock_mcp.handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_message_calls_handle_message(self):
        """A non-empty message is dispatched to mcp_server.handle_message."""
        import sys
        from io import StringIO

        from orb.interface.mcp.server.handler import _run_stdio_server

        mock_mcp = MagicMock()
        mock_mcp.handle_message = AsyncMock(return_value='{"result": "ok"}')

        orig_stdin = sys.stdin
        # One JSON-RPC message line then EOF
        sys.stdin = StringIO('{"jsonrpc": "2.0", "method": "ping", "id": 1}\n')
        try:
            await _run_stdio_server(mock_mcp)
        finally:
            sys.stdin = orig_stdin

        mock_mcp.handle_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# _run_tcp_server
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunTcpServer:
    @pytest.mark.asyncio
    async def test_keyboard_interrupt_handled_gracefully(self):
        """KeyboardInterrupt during serve_forever is caught; server is stopped."""
        from orb.interface.mcp.server.handler import _run_tcp_server

        mock_mcp = MagicMock()

        mock_server_cm = MagicMock()
        mock_server_cm.__aenter__ = AsyncMock(return_value=mock_server_cm)
        mock_server_cm.__aexit__ = AsyncMock(return_value=False)
        mock_server_cm.serve_forever = AsyncMock(side_effect=KeyboardInterrupt)
        mock_server_cm.close = MagicMock()
        mock_server_cm.wait_closed = AsyncMock()
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = ("127.0.0.1", 3000)
        mock_server_cm.sockets = [mock_sock]

        with patch("asyncio.start_server", new_callable=AsyncMock, return_value=mock_server_cm):
            # Should not raise — KeyboardInterrupt is handled
            await _run_tcp_server(mock_mcp, "127.0.0.1", 3000)

        mock_server_cm.close.assert_called()
