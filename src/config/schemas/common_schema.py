"""Common configuration schemas."""

from typing import Dict

from pydantic import BaseModel, Field, field_validator, model_validator


class ResourcePrefixConfig(BaseModel):
    """Resource prefix configuration."""

    default: str = Field("", description="Default prefix for all resources")
    request: str = Field("req-", description="Prefix for acquire request IDs")
    return_prefix: str = Field("ret-", description="Prefix for return request IDs")
    launch_template: str = Field("", description="Prefix for launch template names")
    instance: str = Field("", description="Prefix for instance names")
    fleet: str = Field("", description="Prefix for fleet names")
    asg: str = Field("", description="Prefix for Auto Scaling group names")
    tag: str = Field("", description="Prefix for resource tags")


class ResourceConfig(BaseModel):
    """Resource configuration."""

    default_prefix: str = Field("", description="Default prefix for all resources")
    prefixes: ResourcePrefixConfig = Field(
        default_factory=lambda: ResourcePrefixConfig())

    @model_validator(mode="after")
    def set_default_prefix(self) -> "ResourceConfig":
        """Set default prefix if not provided."""
        if not self.default_prefix:
            object.__setattr__(self, "default_prefix", self.prefixes.default)
        return self


class PrefixConfig(BaseModel):
    """Prefix configuration."""

    default: str = Field("", description="Default prefix for all resources")
    request: str = Field("req-", description="Prefix for acquire request IDs")
    return_prefix: str = Field("ret-", description="Prefix for return request IDs")
    launch_template: str = Field("", description="Prefix for launch template names")
    instance: str = Field("", description="Prefix for instance names")
    fleet: str = Field("", description="Prefix for fleet names")
    asg: str = Field("", description="Prefix for Auto Scaling group names")
    tag: str = Field("", description="Prefix for resource tags")


class StatusValuesConfig(BaseModel):
    """Status values configuration."""

    request: Dict[str, str] = Field(
        default_factory=lambda: {
            "pending": "pending",
            "running": "running",
            "complete": "complete",
            "complete_with_error": "complete_with_error",
            "failed": "failed",
        },
        description="Request status values",
    )
    machine: Dict[str, str] = Field(
        default_factory=lambda: {
            "pending": "pending",
            "running": "running",
            "stopping": "stopping",
            "stopped": "stopped",
            "shutting_down": "shutting-down",
            "terminated": "terminated",
            "unknown": "unknown",
        },
        description="Machine status values",
    )
    machine_result: Dict[str, str] = Field(
        default_factory=lambda: {
            "executing": "executing",
            "succeed": "succeed",
            "fail": "fail",
        },
        description="Machine result values",
    )
    circuit_breaker: Dict[str, str] = Field(
        default_factory=lambda: {
            "closed": "closed",
            "open": "open",
            "half_open": "half_open",
        },
        description="Circuit breaker state values",
    )


class LimitsConfig(BaseModel):
    """Limits configuration."""

    tag_key_length: int = Field(128, description="Maximum length of tag keys")
    tag_value_length: int = Field(256, description="Maximum length of tag values")
    max_tags_per_resource: int = Field(
        50, description="Maximum number of tags per resource")
    max_instance_types_per_fleet: int = Field(
        20, description="Maximum number of instance types per fleet"
    )
    max_subnets_per_fleet: int = Field(
        16, description="Maximum number of subnets per fleet")
    max_security_groups_per_instance: int = Field(
        5, description="Maximum number of security groups per instance"
    )
    max_batch_size: int = Field(100, description="Maximum batch size for API calls")
    max_instances_per_request: int = Field(
        1000, description="Maximum number of instances per request"
    )


class NamingConfig(BaseModel):
    """Naming configuration."""

    collections: Dict[str, str] = Field(
        default_factory=lambda: {
            "requests": "requests",
            "machines": "machines",
            "templates": "templates",
        },
        description="Collection names for NoSQL databases",
    )
    tables: Dict[str, str] = Field(
        default_factory=lambda: {
            "requests": "requests",
            "machines": "machines",
            "event_logs": "event_logs",
            "audit_logs": "audit_logs",
            "metrics_logs": "metrics_logs",
        },
        description="Table names for SQL databases",
    )
    handler_types: Dict[str, str] = Field(
        default_factory=lambda: {
            "ec2_fleet": "EC2Fleet",
            "spot_fleet": "SpotFleet",
            "asg": "ASG",
            "run_instances": "RunInstances",
        },
        description="Handler types for different AWS resources",
    )
    fleet_types: Dict[str, str] = Field(
        default_factory=lambda: {
            "instant": "instant",
            "request": "request",
            "maintain": "maintain",
        },
        description="Fleet types for EC2 Fleet and Spot Fleet",
    )
    price_types: Dict[str, str] = Field(
        default_factory=lambda: {
            "ondemand": "ondemand",
            "spot": "spot",
            "heterogeneous": "heterogeneous",
        },
        description="Price types for templates",
    )
    statuses: StatusValuesConfig = Field(default_factory=lambda: StatusValuesConfig())
    patterns: Dict[str, str] = Field(
        default_factory=lambda: {
            "ec2_instance": r"^i-[a-f0-9]+$",
            "spot_fleet": r"^sfr-[a-f0-9]+$",
            "ec2_fleet": r"^fleet-[a-f0-9]+$",
            "asg": r"^[a-zA-Z0-9_-]+$",
            "ami_id": r"^(ami-[a-f0-9]{8,17}|/aws/service/.+)$",
            "subnet": r"^subnet-[a-f0-9]{8,17}$",
            "security_group": r"^sg-[a-f0-9]{8,17}$",
            "instance_type": r"^[a-z][0-9][a-z]?\.[a-z0-9]+$",
            "region": r"^[a-z]{2}-[a-z]+-\d$",
            "account_id": r"^\d{12}$",
            "launch_template": r"^lt-[a-f0-9]{8,17}$",
            "request_id": r"^(req|ret)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            "tag_key": r"^[\w\s+=.@-]+$",
            "cidr_block": r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$",
            "arn": r"^arn:aws:[a-zA-Z0-9\-]+:[a-z0-9\-]*:[0-9]{12}:.+$",
        },
        description="Validation patterns for various resources",
    )
    prefixes: PrefixConfig = Field(default_factory=lambda: PrefixConfig())
    limits: LimitsConfig = Field(default_factory=lambda: LimitsConfig())


class RequestConfig(BaseModel):
    """Request configuration."""

    max_machines_per_request: int = Field(
        100, description="Maximum number of machines per request")

    @field_validator("max_machines_per_request")
    @classmethod
    def validate_max_machines(cls, v: int) -> int:
        """Validate max machines per request."""
        if v < 1:
            raise ValueError("Maximum machines per request must be at least 1")
        return v


class DatabaseConfig(BaseModel):
    """Database configuration."""

    connection_timeout: int = Field(
        30, description="Database connection timeout in seconds")
    query_timeout: int = Field(60, description="Database query timeout in seconds")
    max_connections: int = Field(
        10, description="Maximum number of database connections")

    @field_validator("connection_timeout", "query_timeout")
    @classmethod
    def validate_timeouts(cls, v: int) -> int:
        """Validate timeout values."""
        if v < 1:
            raise ValueError("Timeout must be at least 1 second")
        return v

    @field_validator("max_connections")
    @classmethod
    def validate_max_connections(cls, v: int) -> int:
        """Validate max connections."""
        if v < 1:
            raise ValueError("Maximum connections must be at least 1")
        return v


class EventsConfig(BaseModel):
    """Events configuration."""

    enabled: bool = Field(True, description="Whether events are enabled")
    max_events_per_request: int = Field(
        1000, description="Maximum number of events per request")
    event_retention_days: int = Field(30, description="Number of days to retain events")

    @field_validator("max_events_per_request")
    @classmethod
    def validate_max_events(cls, v: int) -> int:
        """Validate max events per request."""
        if v < 1:
            raise ValueError("Maximum events per request must be at least 1")
        return v

    @field_validator("event_retention_days")
    @classmethod
    def validate_retention_days(cls, v: int) -> int:
        """Validate event retention days."""
        if v < 1:
            raise ValueError("Event retention days must be at least 1")
        return v
