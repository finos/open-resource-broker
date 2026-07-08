"""Server configuration schema for REST API server."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class RateLimitConfig(BaseModel):
    """Typed configuration for the in-process token-bucket rate limiter.

    The rate limiter uses a token-bucket algorithm:
    - ``requests_per_minute`` sets the steady-state refill rate and the
      maximum bucket capacity (tokens per minute added continuously).
    - ``burst`` caps the initial and maximum instantaneous token count so
      short bursts beyond the per-minute average are tolerated but bounded.
      When ``burst`` is smaller than ``requests_per_minute`` the bucket
      starts pre-filled to ``burst`` tokens rather than the full capacity.
    - ``max_buckets`` bounds memory by capping the number of tracked
      identities (LRU eviction once exceeded).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        True, description="Enable the rate limiter (disable for benchmarks or trusted networks)"
    )
    requests_per_minute: int = Field(
        300,
        description="Steady-state refill rate and maximum capacity (requests per minute per identity)",
        ge=1,
    )
    burst: int = Field(
        60,
        description=(
            "Maximum instantaneous token count (burst allowance). "
            "Controls how many back-to-back requests are accepted before throttling begins. "
            "Must be >= 1. When smaller than requests_per_minute the bucket is initialised "
            "to this value rather than the full per-minute capacity."
        ),
        ge=1,
    )
    max_buckets: int = Field(
        10_000,
        description="Maximum number of tracked identities; oldest entry is evicted on overflow (LRU)",
        ge=1,
    )


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

    **Security note — admin_arns:**
    ``admin_arns`` is an explicit allowlist of fully-qualified AWS ARNs that are
    granted the ``admin`` role.  ARNs are compared using **exact equality** after
    normalising both sides to lowercase.  Do NOT rely on the default
    ``admin_role_patterns`` mechanism for production deployments — it grants admin to
    any principal whose resource-name segment matches a short pattern string (e.g.
    ``"Admin"``), which does not scope to a specific AWS account.  Use ``admin_arns``
    to bind admin access to specific, account-scoped principals instead.
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
    admin_arns: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit allowlist of fully-qualified ARNs that receive the admin role. "
            "Matched by exact equality (case-normalised) against the caller ARN returned "
            "by sts:GetCallerIdentity.  When non-empty this list is the COMPLETE set of "
            "admin principals — the unconditional :root grant does NOT apply.  Include "
            "the root ARN explicitly (e.g. 'arn:aws:iam::123456789012:root') if root "
            "must have admin access.  When empty, the legacy name-pattern + :root "
            "fallback is used instead."
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


_NO_AUTH_STRATEGIES: frozenset[str | None] = frozenset({"none", "", None})


class AuthConfig(BaseModel):
    """Authentication configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(True, description="Enable authentication")
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

    @model_validator(mode="after")
    def _reject_enabled_with_no_strategy(self) -> "AuthConfig":
        """Fail hard when auth.enabled=True but no real strategy is configured.

        The "none" strategy (NoAuthStrategy) is a pass-through that grants every
        anonymous caller permissions=["*"].  Combining it with enabled=True is a
        silent fail-open: the server advertises "auth is on" while enforcing
        nothing.  Any construction that would produce this combination is a
        misconfiguration and is rejected at build time so the error surfaces
        immediately rather than at request time.

        To run with authentication disabled, use AuthConfig(enabled=False)
        explicitly.  To enable auth, also set strategy to a real strategy name
        (e.g. "bearer_token", "iam", "cognito").
        """
        if self.enabled and self.strategy in _NO_AUTH_STRATEGIES:
            raise ValueError(
                "auth.enabled=True requires a real authentication strategy. "
                f"Got strategy={self.strategy!r}, which is a pass-through that enforces nothing. "
                "Either set enabled=False (to disable auth explicitly) "
                "or set strategy to a real strategy name "
                "(e.g. 'bearer_token', 'bearer_token_enhanced', 'iam', 'cognito')."
            )
        return self


class CORSConfig(BaseModel):
    """CORS configuration.

    The default ``origins`` value is intentionally restrictive
    (``["http://localhost:8000"]``) — the single-origin embedded-mode default.
    Operators who bind the server to ``0.0.0.0`` (network exposure) MUST also
    update ``origins`` and ``trusted_hosts`` to the actual client origins they
    want to permit.

    **Security note — credentials + wildcard origin:**
    The combination of ``credentials=True`` and ``origins=["*"]`` is rejected at
    validation time.  Browsers refuse to honour ``Access-Control-Allow-Credentials:
    true`` when the server responds with ``Access-Control-Allow-Origin: *``
    (the spec requires an explicit origin in that case).  Accepting this combo
    silently would produce a configuration that either breaks browsers or, in
    environments where the restriction is relaxed, grants credential access to
    any origin.
    """

    enabled: bool = Field(True, description="Enable CORS")
    origins: list[str] = Field(
        ["http://localhost:8000"],
        description=(
            "Allowed CORS origins.  Default is single-origin embedded-mode. "
            "Operators binding to 0.0.0.0 MUST set this to the actual client origins "
            "they trust; leaving the default while network-exposing the server will "
            "block all cross-origin browser requests from non-loopback clients."
        ),
    )
    methods: list[str] = Field(
        ["GET", "POST", "PUT", "DELETE", "OPTIONS"], description="Allowed methods"
    )
    headers: list[str] = Field(["*"], description="Allowed headers")
    credentials: bool = Field(False, description="Allow credentials")

    @model_validator(mode="after")
    def _reject_credentials_with_wildcard_origin(self) -> "CORSConfig":
        """Reject insecure combinations of allow_credentials=True with wildcard origins/headers.

        Browsers reject ``Access-Control-Allow-Credentials: true`` when the
        server sends ``Access-Control-Allow-Origin: *``.  Beyond the bare ``*``
        case, this validator also catches:

        - Whitespace-padded wildcards (e.g. ``" * "`` or ``"  *"``)
        - Subdomain/path wildcards (any origin containing ``*``, e.g.
          ``"https://*.example.com"``) — browsers reject credentials with any
          wildcard origin pattern, not just a bare ``*``
        - ``headers=["*"]`` with ``credentials=True`` — the spec forbids
          ``Access-Control-Allow-Headers: *`` when credentials are in play

        When ``credentials=False`` (the default) none of these restrictions apply.
        """
        if not self.credentials:
            return self

        # Check each origin: strip whitespace and reject any that contain '*'.
        for origin in self.origins:
            if "*" in origin.strip():
                raise ValueError(
                    "cors.credentials=true is incompatible with cors.origins containing "
                    f"a wildcard pattern (got {origin!r}). "
                    "Browsers refuse to send credentials to wildcard origins, including "
                    "subdomain wildcards like 'https://*.example.com'. "
                    "Replace wildcard entries with the explicit origins you want to permit, "
                    "or set cors.credentials=false if credentials are not needed."
                )

        # headers=["*"] with credentials is also forbidden by the CORS spec.
        if "*" in self.headers:
            raise ValueError(
                "cors.credentials=true is incompatible with cors.headers containing '*'. "
                "The CORS spec forbids 'Access-Control-Allow-Headers: *' when credentials "
                "are in play.  List the specific request headers you want to allow, "
                "or set cors.credentials=false if credentials are not needed."
            )

        return self


class DocsConfig(BaseModel):
    """Configuration for the interactive API documentation endpoints.

    When ``require_auth`` is True (the default) and ``server.auth.enabled`` is
    also True, the ``/docs``, ``/redoc``, and ``/openapi.json`` endpoints are
    protected by the auth middleware just like any other API endpoint.

    Set ``require_auth=False`` (or keep ``server.auth.enabled=False``) to leave
    the docs endpoints public — useful for open-source deployments or clusters
    that are not externally reachable.
    """

    model_config = ConfigDict(extra="forbid")

    require_auth: bool = Field(
        True,
        description=(
            "Gate /docs, /redoc, and /openapi.json behind authentication when "
            "auth is enabled.  When False (or when auth is globally disabled) "
            "these endpoints remain publicly accessible.  Default True so that "
            "enabling auth does not accidentally expose the full route map."
        ),
    )


class ServerConfig(BaseModel):
    """REST API server configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(False, description="Enable REST API server")
    # Bind to loopback by default; operators who need network exposure must
    # explicitly set host="0.0.0.0" AND update cors.origins / trusted_hosts.
    host: str = Field(
        "127.0.0.1",
        description="Server host (default loopback; use 0.0.0.0 for network exposure with explicit origins/trusted_hosts)",
    )
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
    docs: DocsConfig = Field(
        default_factory=DocsConfig,  # type: ignore[call-arg]
        description="Documentation endpoint security settings",
    )

    # Authentication and CORS
    #
    # Default: auth disabled.  The "none" strategy is a pass-through that grants
    # every anonymous caller permissions=["*"]; combining it with enabled=True
    # is silently fail-open.  Operators who want auth MUST set both enabled=True
    # AND a real strategy name in their config file.  A bare ServerConfig()
    # therefore boots with auth off (honest posture) rather than appearing to
    # have auth on while enforcing nothing.
    auth: AuthConfig = Field(  # type: ignore[arg-type]
        default_factory=lambda: AuthConfig(enabled=False),  # type: ignore[call-arg]
        description="Authentication configuration",
    )
    cors: CORSConfig = Field(default_factory=CORSConfig, description="CORS configuration")  # type: ignore[arg-type]

    # Security
    require_https: bool = Field(False, description="Require HTTPS for all requests")
    trusted_hosts: list[str] = Field(
        ["localhost", "127.0.0.1", "::1", "testserver", "test"],
        description=(
            "Trusted Host header values.  Default allows loopback (IPv4 and IPv6) "
            "plus the ``testserver`` / ``test`` hostnames used by Starlette's "
            "TestClient and httpx AsyncClient(base_url=...) fixtures.  Operators "
            "binding to 0.0.0.0 MUST add their public hostname(s) here; any "
            "request whose Host header is not in this list will be rejected with 400."
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
    audit_log_file: Optional[str] = Field(
        None,
        description=(
            "Path to a dedicated audit log file.  When set, a rotating JSON-line "
            "handler is attached to the 'orb.audit' logger so audit records are "
            "written to a separate file in addition to (or instead of) the root "
            "handlers.  When None, audit records flow through the root logging "
            "configuration (stdout/stderr in container deployments)."
        ),
    )

    # Rate limiting
    rate_limiting: RateLimitConfig = Field(
        default_factory=RateLimitConfig,  # type: ignore[arg-type]
        description="Token-bucket rate limiter configuration",
    )

    # Read-only mode
    read_only: bool = Field(
        False, description="Reject all mutating requests (POST, PUT, PATCH, DELETE) with HTTP 403"
    )

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
        if self.auth.enabled and self.cors.enabled and not self.cors.origins:
            raise ValueError(
                "auth.enabled=true requires cors.origins to be set explicitly. "
                "An empty origins list means the browser will block all cross-origin "
                "requests to authenticated endpoints.  "
                "Set cors.origins to the list of allowed UI origins, or use ['*'] "
                "only if you intentionally allow all origins."
            )
        return self
