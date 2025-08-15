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
    # nosec B104: Intentional binding for server deployment
    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8000)
    workers = getattr(args, "workers", 1)
    reload = getattr(args, "reload", False)
    log_level = getattr(args, "server_log_level", "info")

    try:
        # Import here to avoid circular dependencies
        from src.api.server import create_fastapi_app
        from src.config.schemas.server_schema import ServerConfig
        from src.domain.base.ports.configuration_port import ConfigurationPort
        from src.infrastructure.di.container import get_container

        # Get configuration through DI
        container = get_container()
        config_manager = container.get(ConfigurationPort)
        server_config = config_manager.get_typed(ServerConfig)

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
        app = create_fastapi_app(server_config)

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
