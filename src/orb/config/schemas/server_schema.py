"""Server configuration schema for REST API server."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class BearerTokenAuthSubConfig(BaseModel):
    """Typed sub-configuration for the bearer_token auth strategy."""

    model_config = ConfigDict(extra="forbid")

    secret_key: str = Field(..., description="Secret key for JWT signing/verification (>=32 bytes)")
    algorithm: str = Field("HS256", description="JWT algorithm")
    token_expiry: int = Field(3600, description="Token expiry in seconds")


class IAMAuthSubConfig(BaseModel):
    """Typed sub-configuration for AWS IAM auth strategy."""

    model_config = ConfigDict(extra="forbid")

    region: str = Field("us-east-1", description="AWS region")
    profile: Optional[str] = Field(None, description="AWS profile")
    required_actions: list[str] = Field(default_factory=list, description="Required IAM actions")
    assume_permissions: bool = Field(
        False,
        description="If True, grant all required_actions without evaluation (dev/test only)",
    )


class CognitoAuthSubConfig(BaseModel):
    """Typed sub-configuration for AWS Cognito auth strategy."""

    model_config = ConfigDict(extra="forbid")

    user_pool_id: str = Field("", description="Cognito User Pool ID")
    client_id: str = Field("", description="Cognito App Client ID")
    region: str = Field("us-east-1", description="AWS region")
    jwks_url: Optional[str] = Field(None, description="JWKS URL (auto-generated if omitted)")


class ProviderAuthSubConfig(BaseModel):
    """Typed sub-configuration for provider-specific auth strategies."""

    model_config = ConfigDict(extra="forbid")

    iam: Optional[IAMAuthSubConfig] = Field(None, description="IAM auth sub-configuration")
    cognito: Optional[CognitoAuthSubConfig] = Field(
        None, description="Cognito auth sub-configuration"
    )


class AuthConfig(BaseModel):
    """Authentication configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(False, description="Enable authentication")
    strategy: str = Field(
        "none",
        description="Authentication strategy (none, bearer_token, bearer_token_enhanced, iam, cognito)",
    )

    # Bearer token configuration
    bearer_token: Optional[BearerTokenAuthSubConfig] = Field(
        None, description="Bearer token strategy configuration"
    )

    # OAuth configuration (kept as untyped dict for forward compatibility)
    oauth: Optional[dict[str, Any]] = Field(None, description="OAuth strategy configuration")

    # Provider-specific auth configurations
    provider_auth: Optional[ProviderAuthSubConfig] = Field(
        None, description="Provider-specific auth configuration"
    )


class CORSConfig(BaseModel):
    """CORS configuration."""

    enabled: bool = Field(True, description="Enable CORS")
    origins: list[str] = Field(["*"], description="Allowed origins")
    methods: list[str] = Field(
        ["GET", "POST", "PUT", "DELETE", "OPTIONS"], description="Allowed methods"
    )
    headers: list[str] = Field(["*"], description="Allowed headers")
    credentials: bool = Field(False, description="Allow credentials")


class ServerConfig(BaseModel):
    """REST API server configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(False, description="Enable REST API server")
    # Intentional binding for server deployment
    host: str = Field("0.0.0.0", description="Server host")  # nosec B104 - intentional default for server deployment, overridable via config
    port: int = Field(8000, description="Server port")
    workers: int = Field(1, description="Number of worker processes")
    reload: bool = Field(False, description="Enable auto-reload for development")
    log_level: str = Field("info", description="Server log level")
    access_log: bool = Field(True, description="Enable access logging")

    # Documentation
    docs_enabled: bool = Field(True, description="Enable API documentation")
    docs_url: str = Field("/docs", description="Swagger UI URL")
    redoc_url: str = Field("/redoc", description="ReDoc URL")
    openapi_url: str = Field("/openapi.json", description="OpenAPI schema URL")

    # Authentication and CORS
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Authentication configuration")  # type: ignore[arg-type]
    cors: CORSConfig = Field(default_factory=CORSConfig, description="CORS configuration")  # type: ignore[arg-type]

    # Security
    require_https: bool = Field(False, description="Require HTTPS for all requests")
    trusted_hosts: list[str] = Field(["*"], description="Trusted host headers")
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="IP addresses of trusted reverse proxies. When set, X-Forwarded-For is only "
        "read if the direct client IP matches an entry in this list.",
    )

    # Performance
    request_timeout: int = Field(30, description="Request timeout in seconds")
    max_request_size: int = Field(16 * 1024 * 1024, description="Maximum request size in bytes")

    # Rate limiting (for future implementation)
    rate_limiting: Optional[dict[str, Any]] = Field(None, description="Rate limiting configuration")
