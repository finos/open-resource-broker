"""OpenAPI configuration and customization."""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from orb._package import AUTHOR, EMAIL, REPO_NAME, REPO_ORG, __version__
from orb.api.models.hostfactory import HF_MODELS, HF_OPERATION_MODELS
from orb.config.schemas.server_schema import ServerConfig

from .examples import get_api_examples
from .security_schemes import get_all_security_scheme_definitions, get_security_schemes


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
            title="Open Resource Broker API",
            version=__version__,
            description=_get_api_description(),
            routes=app.routes,
        )

        # Always include the full securitySchemes catalogue so SDK generators
        # can emit typed auth helpers regardless of whether auth is enabled in
        # the running server.  The global security requirement (which enforces
        # auth on every operation) is only added when auth is actually enabled.
        if "components" not in openapi_schema:
            openapi_schema["components"] = {}
        openapi_schema["components"]["securitySchemes"] = get_all_security_scheme_definitions()

        if server_config.auth.enabled:
            # Override with the auth-strategy-specific active schemes.
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
                    "name": AUTHOR,
                    "url": f"https://github.com/{REPO_ORG}/{REPO_NAME}",
                    "email": EMAIL,
                },
                "license": {
                    "name": "Apache-2.0",
                    "url": "https://www.apache.org/licenses/LICENSE-2.0",
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

        # Add tags for better organization.
        # NOTE: tag names here must exactly match the tags= strings on each
        # router/decorator.  me.py uses tags=["Auth"] (not "Authentication"),
        # config.py uses tags=["Configuration"], events.py uses tags=["Events"],
        # observability.py uses tags=["Observability"],
        # providers.py uses tags=["Providers"], admin.py uses tags=["Admin"].
        openapi_schema["tags"] = [
            {
                "name": "System",
                "description": "System health, metrics, and service information endpoints",
            },
            {
                "name": "Templates",
                "description": "VM template management operations",
            },
            {
                "name": "Machines",
                "description": "Machine provisioning and management operations",
            },
            {
                "name": "Requests",
                "description": "Request status and management operations",
            },
            {
                "name": "Auth",
                "description": "Authenticated-user introspection (current user identity and role)",
            },
            {
                "name": "Events",
                "description": "Server-Sent Events stream for real-time push updates",
            },
            {
                "name": "Observability",
                "description": "Machine metrics, request timelines, and telemetry status",
            },
            {
                "name": "Providers",
                "description": "Provider configuration, health, and UI column schema",
            },
            {
                "name": "Admin",
                "description": "Destructive administrative operations (wipe, cleanup, init, reload)",
            },
            {
                "name": "Configuration",
                "description": "Runtime configuration inspection and in-memory override",
            },
        ]

        # Add examples
        _add_examples_to_schema(openapi_schema)

        _add_format_schemas(openapi_schema)

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi


# Response formats the server can render. Static on purpose: the exported spec
# must not depend on which schedulers are registered or configured at export time.
BUILT_IN_FORMATS = ("default", "hostfactory", "slurm")


def _add_format_schemas(openapi_schema: dict[str, Any]) -> None:
    """Register the HostFactory response schemas and map them per operation.

    ``x-orb-formats`` on an operation names the schema of its response in each
    built-in format. The default and slurm formats use the operation's own
    response schema; HostFactory uses the ``Hf*`` component.
    """
    schemas = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    for model in HF_MODELS:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema

    for path_item in openapi_schema.get("paths", {}).values():
        for operation in path_item.values():
            model = HF_OPERATION_MODELS.get(operation.get("operationId", ""))
            if model is None:
                continue
            native = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            hf = {"$ref": f"#/components/schemas/{model.__name__}"}
            operation["x-orb-formats"] = {
                name: hf if name == "hostfactory" else native for name in BUILT_IN_FORMATS
            }


def _get_api_description() -> str:
    """Get comprehensive API description."""
    return """
## Open Resource Broker REST API

This API provides dynamic cloud resource provisioning capabilities for IBM Spectrum Symphony Host Factory.

### Features

- **Multi-Cloud Support**: Currently supports AWS with extensible architecture for other providers
- **Template Management**: Define and manage VM templates with provider-specific configurations
- **Machine Provisioning**: Request and manage cloud instances with various deployment strategies
- **Request Tracking**: Monitor provisioning requests with detailed status information
- **Authentication**: Multiple authentication strategies including JWT, AWS IAM, and AWS Cognito
- **Real-time Status**: Get real-time status of provisioning operations

### Response format

Responses use the format of the configured scheduler (`default`, `hostfactory` or
`slurm`). A caller may name another registered format for one call with the
`X-ORB-Scheduler` header or the `scheduler` query parameter; an unknown name is a
400. Every response names the format it used in the `X-ORB-Scheduler` header. The
`x-orb-formats` extension on an operation lists the response schema of each
built-in format.

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
    return scheme_mapping.get(strategy) or ""


def _add_examples_to_schema(openapi_schema: dict[str, Any]) -> None:
    """Add examples to the OpenAPI schema."""
    examples = get_api_examples()

    # Add examples to components
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    openapi_schema["components"]["examples"] = examples
