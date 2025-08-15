"""MCP Server command handler for CLI integration."""

import asyncio
import sys
from typing import Any, Dict

from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_interface_exceptions
from infrastructure.logging.logger import get_logger

from .core import OpenHFPluginMCPServer


@handle_interface_exceptions(context="mcp_server", interface_type="cli")
async def handle_mcp_serve(args) -> Dict[str, Any]:
    """
    Handle MCP server startup.

    Args:
        args: CLI arguments with server configuration

    Returns:
        Server startup results
    """
    logger = get_logger(__name__)

    # Extract server configuration
    port = getattr(args, "port", 3000)
    host = getattr(args, "host", "localhost")
    stdio_mode = getattr(args, "stdio", False)

    # Get application instance from DI container
    container = get_container()

    # Create MCP server instance
    mcp_server = OpenHFPluginMCPServer(app=container)

    if stdio_mode:
        # Run in stdio mode for direct MCP client communication
        logger.info("Starting MCP server in stdio mode")
        await _run_stdio_server(mcp_server)
        return {"message": "MCP server started in stdio mode"}
    else:
        # Run as TCP server (for development/testing)
        logger.info(f"Starting MCP server on {host}:{port}")
        await _run_tcp_server(mcp_server, host, port)
        return {"message": f"MCP server started on {host}:{port}"}


async def _run_stdio_server(mcp_server: OpenHFPluginMCPServer):
    """Run MCP server in stdio mode."""
    logger = get_logger(__name__)

    try:
        # Read from stdin, write to stdout
        while True:
            try:
                # Read line from stdin
                line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)

                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                # Process MCP message
                response = await mcp_server.handle_message(line)

                # Write response to stdout
                print(response, flush=True)  # noqa: MCP protocol output

            except KeyboardInterrupt:
                logger.info("MCP server interrupted by user")
                break
            except Exception as e:
                logger.error(f"Error in stdio server: {e}")
                # Send error response
                error_response = {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": f"Server error: {str(e)}"},
                }
                print(error_response, flush=True)  # noqa: MCP protocol output

    except Exception as e:
        logger.error(f"Fatal error in stdio server: {e}")
        raise


async def _run_tcp_server(mcp_server: OpenHFPluginMCPServer, host: str, port: int):
    """Run MCP server as TCP server."""
    logger = get_logger(__name__)

    async def handle_client(reader, writer):
        """Handle individual client connection."""
        client_addr = writer.get_extra_info("peername")
        logger.info(f"Client connected: {client_addr}")

        try:
            while True:
                # Read message from client
                data = await reader.readline()
                if not data:
                    break

                message = data.decode().strip()
                if not message:
                    continue

                logger.debug(f"Received message: {message}")

                # Process MCP message
                response = await mcp_server.handle_message(message)

                # Send response to client
                writer.write((response + "\n").encode())
                await writer.drain()

                logger.debug(f"Sent response: {response}")

        except Exception as e:
            logger.error(f"Error handling client {client_addr}: {e}")
        finally:
            logger.info(f"Client disconnected: {client_addr}")
            writer.close()
            await writer.wait_closed()

    # Start TCP server
    server = await asyncio.start_server(handle_client, host, port)

    addr = server.sockets[0].getsockname()
    logger.info(f"MCP server listening on {addr[0]}:{addr[1]}")

    try:
        async with server:
            await server.serve_forever()
    except KeyboardInterrupt:
        logger.info("MCP server interrupted by user")
    finally:
        server.close()
        await server.wait_closed()
        logger.info("MCP server stopped")
