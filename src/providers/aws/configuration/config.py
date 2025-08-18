"""AWS provider configuration - single source of truth."""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from infrastructure.interfaces.provider import BaseProviderConfig


class HandlerCapabilityConfig(BaseModel):
    """Handler capability configuration."""

    ec2_fleet: bool = Field(True, description="Enable EC2 Fleet handler")
    spot_fleet: bool = Field(True, description="Enable Spot Fleet handler")
    auto_scaling_group: bool = Field(True, description="Enable Auto Scaling Group handler")
    run_instances: bool = Field(True, description="Enable Run Instances handler")


class HandlerDefaultsConfig(BaseModel):
    """Handler defaults configuration."""

    default_handler: str = Field("ec2_fleet", description="Default handler to use")


class LaunchTemplateConfiguration(BaseModel):
    """Launch template configuration."""

    create_per_request: bool = Field(True, description="Create launch template per request")
    naming_strategy: str = Field("request_based", description="Launch template naming strategy")
    version_strategy: str = Field("incremental", description="Launch template version strategy")
    reuse_existing: bool = Field(True, description="Reuse existing launch templates")
    cleanup_old_versions: bool = Field(False, description="Cleanup old launch template versions")
    max_versions_per_template: int = Field(10, description="Maximum versions per launch template")


class HandlersConfig(BaseModel):
    """Handlers configuration."""

    capabilities: HandlerCapabilityConfig = Field(default_factory=lambda: HandlerCapabilityConfig())
    defaults: HandlerDefaultsConfig = Field(default_factory=lambda: HandlerDefaultsConfig())

    # Legacy fields for backward compatibility
    ec2_fleet: bool = Field(True, description="Enable EC2 Fleet handler (legacy)")
    spot_fleet: bool = Field(True, description="Enable Spot Fleet handler (legacy)")
    auto_scaling_group: bool = Field(True, description="Enable Auto Scaling Group handler (legacy)")
    run_instances: bool = Field(True, description="Enable Run Instances handler (legacy)")

    @model_validator(mode="after")
    def sync_legacy_fields(self) -> "HandlersConfig":
        """Sync legacy fields with capabilities."""
        # Update capabilities from legacy fields if they differ
        if (
            self.ec2_fleet != self.capabilities.ec2_fleet
            or self.spot_fleet != self.capabilities.spot_fleet
            or self.auto_scaling_group != self.capabilities.auto_scaling_group
            or self.run_instances != self.capabilities.run_instances
        ):

            object.__setattr__(self.capabilities, "ec2_fleet", self.ec2_fleet)
            object.__setattr__(self.capabilities, "spot_fleet", self.spot_fleet)
            object.__setattr__(self.capabilities, "auto_scaling_group", self.auto_scaling_group)
            object.__setattr__(self.capabilities, "run_instances", self.run_instances)

        return self


class AWSProviderConfig(BaseProviderConfig):
    """Complete AWS provider configuration - single source of truth.

    This class consolidates all AWS configuration needs:
    - Schema validation for JSON/YAML config files
    - Runtime configuration for AWS provider implementation
    - Authentication, service settings, and legacy Symphony compatibility
    """

    # Provider identification (from BaseProviderConfig)
    provider_type: str = "aws"

    # AWS Authentication
    profile: Optional[str] = Field(None, description="AWS profile")
    role_arn: Optional[str] = Field(None, description="AWS role ARN")
    access_key_id: Optional[str] = Field(None, description="AWS access key ID")
    secret_access_key: Optional[str] = Field(None, description="AWS secret access key")
    session_token: Optional[str] = Field(None, description="AWS session token")

    # AWS Settings
    region: str = Field("us-east-1", description="AWS region")
    endpoint_url: Optional[str] = Field(None, description="AWS endpoint URL")
    max_retries: int = Field(3, description="Maximum number of retries for AWS API calls")
    timeout: int = Field(30, description="Timeout for AWS API calls in seconds")

    # AWS Services
    service_role_spot_fleet: str = Field(
        "AWSServiceRoleForEC2SpotFleet", description="Service role for Spot Fleet"
    )
    ssm_parameter_prefix: str = Field(
        "/hostfactory/templates/", description="SSM parameter prefix for templates"
    )

    # Handler configuration
    handlers: HandlersConfig = Field(default_factory=lambda: HandlersConfig())

    # Launch template configuration
    launch_template: LaunchTemplateConfiguration = Field(
        default_factory=lambda: LaunchTemplateConfiguration()
    )

    # Symphony/Legacy configuration fields
    credential_file: Optional[str] = Field(None, description="Path to AWS credentials file")
    key_file: Optional[str] = Field(None, description="Path to directory containing key pair files")
    proxy_host: Optional[str] = Field(None, description="Proxy server hostname")
    proxy_port: Optional[int] = Field(None, description="Proxy server port")
    connection_timeout_ms: int = Field(10000, description="Connection timeout in milliseconds")
    request_retry_attempts: int = Field(0, description="Number of retry attempts for AWS requests")
    instance_pending_timeout_sec: int = Field(
        180, description="Timeout for pending instances in seconds"
    )
    describe_request_retry_attempts: int = Field(
        0, description="Number of retries for status requests"
    )
    describe_request_interval: int = Field(0, description="Delay between retries in milliseconds")

    @model_validator(mode="after")
    def check_auth_method(self) -> "AWSProviderConfig":
        """
        Validate that at least one authentication method is provided.

        Returns:
            Validated model

        Raises:
            ValueError: If no authentication method is provided
        """
        profile = self.profile
        role_arn = self.role_arn
        access_key = self.access_key_id
        secret_key = self.secret_access_key
        credential_file = self.credential_file

        if profile or role_arn or (access_key and secret_key) or credential_file:
            return self

        raise ValueError(
            "At least one authentication method must be provided: "
            "profile, role_arn, credential_file, or access_key_id + secret_access_key"
        )

    @model_validator(mode="after")
    def validate_proxy_config(self) -> "AWSProviderConfig":
        """
        Validate proxy configuration.

        Returns:
            Validated model

        Raises:
            ValueError: If proxy_host is specified but proxy_port is not
        """
        if self.proxy_host and self.proxy_port is None:
            raise ValueError("proxy_port is required when proxy_host is specified")
        return self
