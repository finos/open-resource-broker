"""AWS-specific naming patterns configuration."""

from pydantic import BaseModel, Field


class AWSLimitsConfig(BaseModel):
    """AWS service limits and constraints."""

    tag_key_length: int = Field(128, description="Maximum length for AWS tag keys")
    tag_value_length: int = Field(256, description="Maximum length for AWS tag values")
    max_tags_per_resource: int = Field(50, description="Maximum tags per AWS resource")
    max_security_groups: int = Field(5, description="Maximum security groups per instance")
    max_subnets: int = Field(16, description="Maximum subnets per request")


class AWSNamingConfig(BaseModel):
    """AWS naming patterns and validation rules."""

    subnet: str = Field(
        r"^subnet-[0-9a-f]{8,17}$",
        description="Regex pattern for AWS subnet IDs",
    )
    security_group: str = Field(
        r"^sg-[0-9a-f]{8,17}$",
        description="Regex pattern for AWS security group IDs",
    )
    ec2_instance: str = Field(
        r"^i-[0-9a-f]{8,17}$",
        description="Regex pattern for AWS EC2 instance IDs",
    )
    ami: str = Field(
        r"^(ami-[0-9a-f]{8,17}|/aws/service/.+)$",
        description="Regex pattern for AWS AMI IDs (including SSM paths)",
    )
    ec2_fleet: str = Field(
        r"^fleet-[0-9a-f]{8,17}$",
        description="Regex pattern for AWS EC2 fleet IDs",
    )
    launch_template: str = Field(
        r"^lt-[0-9a-f]{8,17}$",
        description="Regex pattern for AWS launch template IDs",
    )
    instance_type: str = Field(
        r"^[a-z][0-9]+[a-z]*\.[a-z0-9]+$",
        description="Regex pattern for AWS instance types",
    )
    tag_key: str = Field(
        r"^[a-zA-Z0-9\s\._:/=+\-@]{1,128}$",
        description="Regex pattern for AWS tag keys",
    )
    arn: str = Field(
        r"^arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:[0-9]{12}:.+$",
        description="Regex pattern for AWS ARNs",
    )
    account_id: str = Field(
        r"^\d{12}$",
        description="Regex pattern for AWS account IDs",
    )
    limits: AWSLimitsConfig = Field(
        default_factory=AWSLimitsConfig,  # type: ignore[call-arg]
        description="AWS service limits and constraints",
    )

    @property
    def patterns(self) -> dict[str, str]:
        """Return patterns as a dict for backward-compatible access."""
        return {k: v for k, v in self.model_dump().items() if k != "limits"}
