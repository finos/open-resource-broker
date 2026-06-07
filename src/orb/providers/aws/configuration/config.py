"""AWS provider configuration - single source of truth."""

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from orb.infrastructure.interfaces.provider import BaseProviderConfig
from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig
from orb.providers.aws.configuration.naming_config import AWSNamingConfig
from orb.providers.aws.domain.template.value_objects import ProviderApi
from orb.providers.aws.storage.config import AWSStorageConfig


class HandlerCapabilityConfig(BaseModel):
    """Handler capability configuration."""

    ec2_fleet: bool = Field(True, description="Enable EC2 Fleet handler")
    spot_fleet: bool = Field(True, description="Enable Spot Fleet handler")
    asg: bool = Field(True, description="Enable Auto Scaling Group handler")
    run_instances: bool = Field(True, description="Enable Run Instances handler")


class HandlerDefaultsConfig(BaseModel):
    """Handler defaults configuration."""

    default_handler: str = Field("EC2Fleet", description="Default handler to use")

    @field_validator("default_handler")
    @classmethod
    def validate_default_handler(cls, v: str) -> str:
        """Reject values that are not valid ProviderApi registry keys."""
        try:
            ProviderApi(v)
        except ValueError:
            valid = [m.value for m in ProviderApi]
            raise ValueError(
                f"default_handler {v!r} is not a valid ProviderApi value; expected one of {valid}"
            )
        return v


class LaunchTemplateConfiguration(BaseModel):
    """Launch template configuration."""

    create_per_request: bool = Field(True, description="Create launch template per request")
    naming_strategy: str = Field("request_based", description="Launch template naming strategy")
    version_strategy: str = Field("incremental", description="Launch template version strategy")
    reuse_existing: bool = Field(True, description="Reuse existing launch templates")
    cleanup_old_versions: bool = Field(False, description="Cleanup old launch template versions")
    max_versions_per_template: int = Field(10, description="Maximum versions per launch template")
    on_update_failure: Literal["fail", "warn"] = Field(
        "fail", description="Behaviour when creating a new LT version fails: fail or warn"
    )


class TaggingConfiguration(BaseModel):
    """Tagging configuration."""

    on_tag_failure: Literal["warn", "fail"] = Field(
        "warn", description="Behaviour when resource tagging fails: warn or fail"
    )


class HandlersConfig(BaseModel):
    """Handlers configuration."""

    capabilities: HandlerCapabilityConfig = Field(default_factory=HandlerCapabilityConfig)  # type: ignore[call-arg]
    defaults: HandlerDefaultsConfig = Field(default_factory=HandlerDefaultsConfig)  # type: ignore[call-arg]

    # Legacy fields for backward compatibility
    ec2_fleet: bool = Field(True, description="Enable EC2 Fleet handler (legacy)")
    spot_fleet: bool = Field(True, description="Enable Spot Fleet handler (legacy)")
    asg: bool = Field(True, description="Enable Auto Scaling Group handler (legacy)")
    run_instances: bool = Field(True, description="Enable Run Instances handler (legacy)")

    @model_validator(mode="after")
    def sync_legacy_fields(self) -> "HandlersConfig":
        """Sync legacy fields with capabilities."""
        # Update capabilities from legacy fields if they differ
        if (
            self.ec2_fleet != self.capabilities.ec2_fleet
            or self.spot_fleet != self.capabilities.spot_fleet
            or self.asg != self.capabilities.asg
            or self.run_instances != self.capabilities.run_instances
        ):
            object.__setattr__(self.capabilities, "ec2_fleet", self.ec2_fleet)
            object.__setattr__(self.capabilities, "spot_fleet", self.spot_fleet)
            object.__setattr__(self.capabilities, "asg", self.asg)
            object.__setattr__(self.capabilities, "run_instances", self.run_instances)

        return self


class AWSProviderConfig(BaseSettings, BaseProviderConfig):  # type: ignore[misc]
    """Complete AWS provider configuration - single source of truth.

    This class consolidates all AWS configuration needs:
    - Schema validation for JSON/YAML config files
    - Runtime configuration for AWS provider implementation
    - Authentication, service settings, and legacy Symphony compatibility
    - Environment variable support with ORB_AWS_ prefix
    """

    model_config = SettingsConfigDict(  # type: ignore[assignment]
        env_prefix="ORB_AWS_",
        case_sensitive=False,
        populate_by_name=True,
        env_nested_delimiter="__",  # Enable nested environment variables
        extra="allow",
    )

    # Provider identification (from BaseProviderConfig)
    provider_type: str = "aws"

    # AWS Authentication
    profile: Optional[str] = Field(None, description="AWS profile")
    role_arn: Optional[str] = Field(None, description="AWS role ARN")
    access_key_id: Optional[str] = Field(None, description="AWS access key ID")
    secret_access_key: Optional[str] = Field(None, description="AWS secret access key")
    session_token: Optional[str] = Field(None, description="AWS session token")

    # AWS Settings
    region: str = Field("us-east-1", description="AWS region")  # type: ignore[assignment]
    endpoint_url: Optional[str] = Field(None, description="AWS endpoint URL")
    aws_max_retries: int = Field(
        3,
        description="Maximum number of retries for AWS API calls",
        validation_alias="max_retries",
    )
    aws_read_timeout: int = Field(
        30,
        description="Read timeout for AWS API calls in seconds",
        validation_alias="timeout",
    )

    # AWS Services
    service_role_spot_fleet: str = Field(
        "AWSServiceRoleForEC2SpotFleet", description="Service role for Spot Fleet"
    )
    # Handler configuration
    handlers: HandlersConfig = Field(default_factory=HandlersConfig)  # type: ignore[call-arg]

    # Launch template configuration
    launch_template: LaunchTemplateConfiguration = Field(
        default_factory=LaunchTemplateConfiguration  # type: ignore[call-arg]
    )

    # Tagging configuration
    tagging: TaggingConfiguration = Field(
        default_factory=TaggingConfiguration  # type: ignore[call-arg]
    )

    # AWS-specific batch sizes configuration
    batch_sizes: AWSBatchSizesConfig = Field(
        default_factory=AWSBatchSizesConfig,  # type: ignore[call-arg]
        description="Batch sizes for AWS EC2 API operations",
    )

    # AWS-specific naming patterns configuration
    naming: AWSNamingConfig = Field(
        default_factory=AWSNamingConfig,  # type: ignore[call-arg]
        description="Regex patterns for validating AWS resource IDs",
    )

    # AWS storage backend configuration
    storage: AWSStorageConfig = Field(
        default_factory=AWSStorageConfig,  # type: ignore[call-arg]
        description="AWS storage backend configuration",
    )

    # Symphony/Legacy configuration fields
    credential_file: Optional[str] = Field(None, description="Path to AWS credentials file")
    key_file: Optional[str] = Field(None, description="Path to directory containing key pair files")
    proxy_host: Optional[str] = Field(None, description="Proxy server hostname")
    proxy_port: Optional[int] = Field(None, description="Proxy server port")
    aws_connect_timeout: int = Field(
        10,
        description="Connection timeout in seconds",
    )
    request_retry_attempts: int = Field(0, description="Number of retry attempts for AWS requests")
    instance_pending_timeout_sec: int = Field(
        180, description="Timeout for pending instances in seconds"
    )
    describe_request_retry_attempts: int = Field(
        0, description="Number of retries for status requests"
    )
    describe_request_interval: int = Field(0, description="Delay between retries in milliseconds")

    @field_validator("handlers", mode="before")
    @classmethod
    def parse_handlers_json(cls, v):
        """Parse handlers configuration from JSON string if needed."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v

    @field_validator("storage", mode="before")
    @classmethod
    def parse_storage_json(cls, v: Any) -> Any:
        """Parse storage configuration from JSON string if needed."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON for storage config: {e}") from e
        return v

    @field_validator("launch_template", mode="before")
    @classmethod
    def parse_launch_template_json(cls, v):
        """Parse launch template configuration from JSON string if needed."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v

    @model_validator(mode="before")
    @classmethod
    def normalize_connect_timeout(cls, data: Any) -> Any:
        """Normalize legacy connection timeout fields to seconds."""
        if not isinstance(data, dict):
            return data

        if "aws_connect_timeout" in data:
            return data

        raw_timeout = None
        if "aws_connection_timeout" in data:
            raw_timeout = data.get("aws_connection_timeout")
        elif "connection_timeout_ms" in data:
            raw_timeout = data.get("connection_timeout_ms")

        if raw_timeout is None:
            return data

        updated = dict(data)
        try:
            updated["aws_connect_timeout"] = int(float(raw_timeout) / 1000)
        except Exception:
            updated["aws_connect_timeout"] = raw_timeout

        return updated

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
