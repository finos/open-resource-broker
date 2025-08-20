"""OpenAPI configuration and customization."""

from typing import Any, Dict

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from _package import __version__
from config.schemas.server_schema import ServerConfig

from .examples import get_api_examples
from .security_schemes import get_security_schemes


def configure_openapi(app: FastAPI, server_config: ServerConfig):
    """
    Configure OpenAPI documentation for the FastAPI app.

    Args:
        app: FastAPI application instance
        server_config: Server configuration
    """

    def custom_openapi():
        """Generate custom OpenAPI schema with comprehensive documentation."""
        if app.openapi_schema:
            return app.openapi_schema

        # Generate base OpenAPI schema
        openapi_schema = get_openapi(
            title="Open Host Factory Plugin API",
            version=__version__,
            description=_get_api_description(),
            routes=app.routes,
        )

        # Add security schemes if authentication is enabled
        if server_config.auth.enabled:
            openapi_schema["components"]["securitySchemes"] = get_security_schemes(
                server_config.auth
            )

            # Add global security requirement
            security_scheme_name = _get_security_scheme_name(server_config.auth.strategy)
            if security_scheme_name:
                openapi_schema["security"] = [{security_scheme_name: []}]

        # Add custom info
        openapi_schema["info"].update(
            {
                "contact": {
                    "name": "Open Host Factory Plugin",
                    "url": "https://github.com/your-org/open-hostfactory-plugin",
                    "email": "support@your-org.com",
                },
                "license": {
                    "name": "MIT",
                    "url": "https://opensource.org/licenses/MIT",
                },
            }
        )

        # Add servers information
        openapi_schema["servers"] = [
            {
                "url": f"http://{server_config.host}:{server_config.port}",
                "description": "Development server",
            },
            {"url": "https://api.your-domain.com", "description": "Production server"},
        ]

        # Add tags for better organization
        openapi_schema["tags"] = [
            {
                "name": "System",
                "description": "System health and information endpoints",
            },
            {"name": "Templates", "description": "VM template management operations"},
            {
                "name": "Machines",
                "description": "Machine provisioning and management operations",
            },
            {
                "name": "Requests",
                "description": "Request status and management operations",
            },
            {
                "name": "Authentication",
                "description": "Authentication and authorization operations",
            },
        ]

        # Add examples
        _add_examples_to_schema(openapi_schema)

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi


def _get_api_description() -> str:
    """Get comprehensive API description."""
    return """
## Open Host Factory Plugin REST API

This API provides dynamic cloud resource provisioning capabilities for IBM Spectrum Symphony Host Factory.

### Features

- **Multi-Cloud Support**: Currently supports AWS with extensible architecture for other providers
- **Template Management**: Define and manage VM templates with provider-specific configurations
- **Machine Provisioning**: Request and manage cloud instances with various deployment strategies
- **Request Tracking**: Monitor provisioning requests with detailed status information
- **Authentication**: Multiple authentication strategies including JWT, AWS IAM, and AWS Cognito
- **Real-time Status**: Get real-time status of provisioning operations

### Authentication

This API supports multiple authentication methods:

- **None**: No authentication (development/testing only)
- **Bearer Token**: JWT-based authentication with configurable secrets
- **AWS IAM**: AWS credential-based authentication using IAM roles/users
- **AWS Cognito**: Integration with AWS Cognito User Pools

### Rate Limiting

API requests are subject to rate limiting to ensure fair usage and system stability.

### Error Handling

All endpoints return consistent error responses with appropriate HTTP status codes and detailed error messages.

### Versioning

This API uses URL path versioning (e.g., `/api/v1/`). Breaking changes will result in a new version.
    """.strip()


def _get_security_scheme_name(strategy: str) -> str:
    """Get security scheme name for the given auth strategy."""
    scheme_mapping = {
        "bearer_token": "BearerAuth",
        "iam": "AWSAuth",
        "cognito": "CognitoAuth",
    }
    return scheme_mapping.get(strategy)


def _add_examples_to_schema(openapi_schema: Dict[str, Any]) -> None:
    """Add examples to the OpenAPI schema."""
    examples = get_api_examples()

    # Add examples to components
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    openapi_schema["components"]["examples"] = examples
