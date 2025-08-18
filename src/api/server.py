"""FastAPI server factory and application setup."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from _package import __version__
from api.documentation import configure_openapi
from api.middleware import AuthMiddleware, LoggingMiddleware
from config.schemas.server_schema import ServerConfig
from infrastructure.auth.registry import get_auth_registry
from infrastructure.error.exception_handler import get_exception_handler
from infrastructure.logging.logger import get_logger


def create_fastapi_app(server_config: ServerConfig) -> FastAPI:
    """
    Create and configure FastAPI application.

    Args:
        server_config: Server configuration

    Returns:
        Configured FastAPI application
    """
    # Create FastAPI app with configuration
    app = FastAPI(
        title="Open Host Factory Plugin API",
        description="REST API for Open Host Factory Plugin - Dynamic cloud resource provisioning",
        version=__version__,
        docs_url=server_config.docs_url if server_config.docs_enabled else None,
        redoc_url=server_config.redoc_url if server_config.docs_enabled else None,
        openapi_url=server_config.openapi_url if server_config.docs_enabled else None,
    )

    logger = get_logger(__name__)

    # Add trusted host middleware if configured
    if server_config.trusted_hosts and server_config.trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=server_config.trusted_hosts)

    # Add CORS middleware
    if server_config.cors.enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=server_config.cors.origins,
            allow_credentials=server_config.cors.credentials,
            allow_methods=server_config.cors.methods,
            allow_headers=server_config.cors.headers,
        )
        logger.info("CORS middleware enabled")

    # Add logging middleware
    app.add_middleware(LoggingMiddleware)
    logger.info("Logging middleware enabled")

    # Add authentication middleware if enabled
    if server_config.auth.enabled:
        auth_strategy = _create_auth_strategy(server_config.auth)
        if auth_strategy:
            app.add_middleware(AuthMiddleware, auth_port=auth_strategy, require_auth=True)
            logger.info(
                "Authentication middleware enabled with strategy: %s", auth_strategy.get_strategy_name()
            )
        else:
            logger.warning("Authentication enabled but strategy creation failed")

    # Add global exception handler
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Global exception handler for all unhandled exceptions."""
        try:
            # Use the existing exception handler infrastructure
            error_response = exception_handler.handle_error_for_http(exc)
            return JSONResponse(
                status_code=error_response.http_status or 500,
                content={
                    "success": False,
                    "error": {
                        "code": (
                            error_response.error_code.value
                            if hasattr(error_response.error_code, "value")
                            else str(error_response.error_code)
                        ),
                        "message": error_response.message,
                        "details": error_response.details,
                    },
                    "timestamp": error_response.timestamp,
                    "request_id": getattr(request.state, "request_id", "unknown"),
                },
            )
        except Exception as handler_error:
            # Fallback error response
            logger.error("Exception handler failed: %s", handler_error)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "An internal server error occurred",
                    },
                },
            )

    # Add health check endpoint
    @app.get("/health", tags=["System"])
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "service": "open-hostfactory-plugin",
            "version": __version__,
        }

    # Add info endpoint
    @app.get("/info", tags=["System"])
    async def info():
        """Service information endpoint."""
        return {
            "service": "open-hostfactory-plugin",
            "version": __version__,
            "description": "REST API for Open Host Factory Plugin",
            "auth_enabled": server_config.auth.enabled,
            "auth_strategy": (server_config.auth.strategy if server_config.auth.enabled else None),
        }

    # Register API routers
    _register_routers(app)

    # Configure OpenAPI documentation
    configure_openapi(app, server_config)

    logger.info("FastAPI application created with %s routes", len(app.routes))
    return app


def _create_auth_strategy(auth_config):
    """
    Create authentication strategy based on configuration.

    Args:
        auth_config: Authentication configuration

    Returns:
        Authentication strategy instance or None
    """
    logger = get_logger(__name__)

    try:
        auth_registry = get_auth_registry()
        strategy_name = auth_config.strategy

        if strategy_name == "none":
            return auth_registry.get_strategy("none", enabled=False)

        elif strategy_name == "bearer_token":
            bearer_config = auth_config.bearer_token or {}
            return auth_registry.get_strategy(
                "bearer_token",
                secret_key=bearer_config.get("secret_key", "default-secret-change-me"),
                algorithm=bearer_config.get("algorithm", "HS256"),
                token_expiry=bearer_config.get("token_expiry", 3600),
                enabled=True,
            )

        elif strategy_name == "iam":
            iam_config = auth_config.iam or {}
            # Register AWS IAM strategy if not already registered
            try:
                from providers.aws.auth.iam_strategy import IAMAuthStrategy

                auth_registry.register_strategy("iam", IAMAuthStrategy)
            except ImportError:
                logger.warning("AWS IAM strategy not available")
                return None

            return auth_registry.get_strategy(
                "iam",
                region=iam_config.get("region", "us-east-1"),
                profile=iam_config.get("profile"),
                required_actions=iam_config.get("required_actions", []),
                enabled=True,
            )

        elif strategy_name == "cognito":
            cognito_config = auth_config.cognito or {}
            # Register AWS Cognito strategy if not already registered
            try:
                from providers.aws.auth.cognito_strategy import CognitoAuthStrategy

                auth_registry.register_strategy("cognito", CognitoAuthStrategy)
            except ImportError:
                logger.warning("AWS Cognito strategy not available")
                return None

            return auth_registry.get_strategy(
                "cognito",
                user_pool_id=cognito_config.get("user_pool_id", ""),
                client_id=cognito_config.get("client_id", ""),
                region=cognito_config.get("region", "us-east-1"),
                enabled=True,
            )

        else:
            logger.error("Unknown authentication strategy: %s", strategy_name)
            return None

    except Exception as e:
        logger.error("Failed to create auth strategy: %s", e)
        return None


def _register_routers(app: FastAPI):
    """
    Register API routers.

    Args:
        app: FastAPI application
    """
    try:
        from api.routers import machines, requests, templates

        app.include_router(templates.router, prefix="/api/v1")
        app.include_router(machines.router, prefix="/api/v1")
        app.include_router(requests.router, prefix="/api/v1")

    except ImportError as e:
        logger = get_logger(__name__)
        logger.error("Failed to import routers: %s", e)
        # Continue without routers - they might not be fully implemented yet
