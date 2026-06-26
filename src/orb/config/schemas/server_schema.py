"""Server configuration schema for REST API server."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class BearerTokenAuthSubConfig(BaseModel):
    """Typed sub-configuration for the bearer_token auth strategy."""

    model_config = ConfigDict(extra="forbid")

    secret_key: SecretStr = Field(
        ...,
        description=(
            "Secret key for JWT signing/verification (>=32 bytes). "
            "Stored as SecretStr so the value is never exposed in repr() or log output."
        ),
    )
    algorithm: str = Field("HS256", description="JWT algorithm (HS256, HS384, or HS512)")
    token_expiry: int = Field(3600, description="Token expiry in seconds")

    @field_validator("algorithm")
    @classmethod
    def _validate_algorithm(cls, value: str) -> str:
        """Restrict to HMAC algorithms; explicitly reject 'none' and unknown values."""
        if value.lower() == "none":
            raise ValueError(
                "Algorithm 'none' is not permitted — it disables signature verification."
            )
        if value not in _ALLOWED_JWT_ALGORITHMS:
            raise ValueError(
                f"Unsupported JWT algorithm {value!r}. "
                f"Allowed values: {sorted(_ALLOWED_JWT_ALGORITHMS)}"
            )
        return value


class IAMAuthSubConfig(BaseModel):
    """Typed sub-configuration for AWS IAM auth strategy.

    **Security note — assume_permissions:**
    Setting ``assume_permissions=True`` bypasses real AWS IAM evaluation and grants
    every action in ``required_actions`` to any authenticated principal.  This is a
    deliberate development/testing escape hatch and MUST NOT be enabled in production.
    The IAMAuthStrategy enforces this by requiring the environment variable
    ``ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY=true`` to be set alongside the config flag;
    without it the flag is ignored and permissions are denied by default.
    """

    model_config = ConfigDict(extra="forbid")

    region: str = Field("us-east-1", description="AWS region")
    profile: Optional[str] = Field(None, description="AWS profile")
    required_actions: list[str] = Field(default_factory=list, description="Required IAM actions")
    assume_permissions: bool = Field(
        False,
        description=(
            "DEV/TEST ONLY — grant all required_actions without AWS evaluation. "
            "Has no effect unless ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY=true is also set."
        ),
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
    origins: list[str] = Field(
        default_factory=list,
        description=(
            "Allowed CORS origins. Defaults to [] (no origins permitted). "
            "Operators MUST explicitly list permitted origins in production — "
            "use ['*'] only for fully public, unauthenticated APIs."
        ),
    )
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
    trusted_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Trusted Host header values enforced by TrustedHostMiddleware. "
            "Defaults to [] (middleware disabled — all Host values accepted). "
            "Set to ['yourdomain.com', 'www.yourdomain.com'] in production."
        ),
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="IP addresses of trusted reverse proxies. When set, X-Forwarded-For is only "
        "read if the direct client IP matches an entry in this list.",
    )

    # Performance
    request_timeout: int = Field(30, description="Request timeout in seconds")
    max_request_size: int = Field(16 * 1024 * 1024, description="Maximum request size in bytes")

    # Audit logging
    audit_log_enabled: bool = Field(True, description="Emit audit logs for mutating requests")

    # Rate limiting
    rate_limiting: Optional[dict[str, Any]] = Field(None, description="Rate limiting configuration")

    # Read-only mode
    read_only: bool = Field(False, description="Reject all mutating requests (POST, PUT, PATCH, DELETE) with HTTP 403")

    # ── Process lifecycle (orb server start/stop/status) ─────────────────────
    # Paths are optional — when None the daemon module resolves them from
    # platform_dirs (ORB work/logs locations, honouring ORB_WORK_DIR and
    # ORB_LOG_DIR env vars). Override here to pin specific paths per deploy.
    pid_file: Optional[str] = Field(
        None,
        description=(
            "Path to the PID file written by 'orb server start' (daemon mode). "
            "Defaults to <work_dir>/server/orb-server.pid via platform_dirs."
        ),
    )
    log_file: Optional[str] = Field(
        None,
        description=(
            "Path to the combined stdout/stderr log file in daemon mode. "
            "Defaults to <log_dir>/orb-server.log via platform_dirs."
        ),
    )
    working_dir: Optional[str] = Field(
        None,
        description=(
            "Working directory for the daemon process (after chdir). "
            "Defaults to the ORB work_dir from platform_dirs."
        ),
    )
    stop_timeout_seconds: int = Field(
        10,
        description="Seconds to wait for SIGTERM before escalating to SIGKILL on stop",
    )

    @model_validator(mode="after")
    def _check_cors_origins_when_auth_enabled(self) -> "ServerConfig":
        """Reject configs that enable auth without specifying explicit CORS origins.

        When authentication is turned on, leaving CORS origins empty (the secure
        default) is fine — the browser will reject cross-origin preflight requests
        and you must explicitly allow only the UI origins you trust.  However,
        operators sometimes copy examples that have ``origins=['*']`` and forget to
        tighten them.  This validator surfaces the mistake at startup rather than
        silently accepting it.

        If you really need wildcard origins with auth (e.g. an internal API where all
        callers are trusted), set ``cors.origins=['*']`` explicitly — that documents
        the intentional choice.
        """
        if (
            self.auth.enabled
            and self.cors.enabled
            and not self.cors.origins
        ):
            raise ValueError(
                "auth.enabled=true requires cors.origins to be set explicitly. "
                "An empty origins list means the browser will block all cross-origin "
                "requests to authenticated endpoints.  "
                "Set cors.origins to the list of allowed UI origins, or use ['*'] "
                "only if you intentionally allow all origins."
            )
        return self
