"""CLI command handler for REST API server."""

import signal
from typing import Any, Dict

from src.infrastructure.error.decorators import handle_interface_exceptions
from src.infrastructure.logging.logger import get_logger


@handle_interface_exceptions(context="serve_api", interface_type="cli")
async def handle_serve_api(args) -> Dict[str, Any]:
    """
    Handle serve API operations.

    Args:
        args: Argument namespace with resource/action structure

    Returns:
        Server startup results
    """
    logger = get_logger(__name__)

    # Extract parameters from args
    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8000)
    workers = getattr(args, "workers", 1)
    reload = getattr(args, "reload", False)
    log_level = getattr(args, "server_log_level", "info")

    try:
        # Import here to avoid circular dependencies
        from src.config.manager import ConfigurationManager
        from src.interface.rest.server import create_app

        # Get server configuration
        config_manager = ConfigurationManager()
        server_config = config_manager.get_server_config()

        # Override with CLI arguments if provided
        if host:
            server_config.host = host
        if port:
            server_config.port = port
        if workers:
            server_config.workers = workers
        if log_level:
            server_config.log_level = log_level

        logger.info(f"Starting REST API server on {server_config.host}:{server_config.port}")
        logger.info(
            f"Workers: {server_config.workers}, Reload: {reload}, Log Level: {server_config.log_level}"
        )

        # Create and configure the FastAPI app
        app = create_app()

        # Start the server
        import uvicorn

        config = uvicorn.Config(
            app=app,
            host=server_config.host,
            port=server_config.port,
            workers=(
                server_config.workers if not reload else 1
            ),  # Reload mode requires single worker
            reload=reload,
            log_level=server_config.log_level,
            access_log=True,
        )

        server = uvicorn.Server(config)

        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            """Handle shutdown signals gracefully."""
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            server.should_exit = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the server (this blocks until shutdown)
        await server.serve()

        return {
            "message": "Server started successfully",
            "host": server_config.host,
            "port": server_config.port,
            "workers": server_config.workers,
        }

    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        return {"error": str(e), "message": "Failed to start server"}
