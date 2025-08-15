"""OpenAPI security schemes for different authentication strategies."""

from typing import Any, Dict

from config.schemas.server_schema import AuthConfig


def get_security_schemes(auth_config: AuthConfig) -> Dict[str, Any]:
    """
    Get OpenAPI security schemes based on authentication configuration.

    Args:
        auth_config: Authentication configuration

    Returns:
        Dictionary of security schemes for OpenAPI
    """
    schemes = {}

    if auth_config.strategy == "bearer_token":
        schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT Bearer token authentication. Include the token in the Authorization header as 'Bearer <token>'",
        }

    elif auth_config.strategy == "iam":
        schemes["AWSAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "AWS IAM authentication using AWS credentials. The system will validate your AWS credentials and IAM permissions.",
        }

    elif auth_config.strategy == "cognito":
        schemes["CognitoAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "AWS Cognito authentication using Cognito User Pool tokens. Include the Cognito access token in the Authorization header.",
        }

    elif auth_config.strategy == "oauth":
        schemes["OAuth2"] = {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "https://your-oauth-provider.com/oauth/authorize",
                    "tokenUrl": "https://your-oauth-provider.com/oauth/token",
                    "scopes": {
                        "read": "Read access to resources",
                        "write": "Write access to resources",
                        "admin": "Administrative access",
                    },
                }
            },
            "description": "OAuth 2.0 authentication with authorization code flow",
        }

    # Add API Key scheme as alternative
    schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API Key authentication. Include your API key in the X-API-Key header.",
    }

    return schemes


def get_auth_examples() -> Dict[str, Any]:
    """
    Get authentication examples for documentation.

    Returns:
        Dictionary of authentication examples
    """
    return {
        "bearer_token_example": {
            "summary": "Bearer Token Authentication",
            "description": "Example of using JWT Bearer token authentication",
            "value": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."},
        },
        "aws_iam_example": {
            "summary": "AWS IAM Authentication",
            "description": "Example of using AWS IAM credentials",
            "value": {
                "Authorization": "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20230101/us-east-1/execute-api/aws4_request..."
            },
        },
        "cognito_example": {
            "summary": "AWS Cognito Authentication",
            "description": "Example of using AWS Cognito access token",
            "value": {"Authorization": "Bearer eyJraWQiOiJLTzRVMWZs..."},
        },
        "api_key_example": {
            "summary": "API Key Authentication",
            "description": "Example of using API key authentication",
            "value": {"X-API-Key": "your-api-key-here"},
        },
    }
