"""AWS-specific naming patterns configuration."""

from pydantic import BaseModel, Field

from orb.providers.aws.domain.template import naming_patterns as _np


class AWSLimitsConfig(BaseModel):
    """AWS service limits and constraints."""

    tag_key_length: int = Field(_np.TAG_KEY_LENGTH, description="Maximum length for AWS tag keys")
    tag_value_length: int = Field(
        _np.TAG_VALUE_LENGTH, description="Maximum length for AWS tag values"
    )
    max_tags_per_resource: int = Field(
        _np.MAX_TAGS_PER_RESOURCE, description="Maximum tags per AWS resource"
    )
    max_security_groups: int = Field(
        _np.MAX_SECURITY_GROUPS, description="Maximum security groups per instance"
    )
    max_subnets: int = Field(_np.MAX_SUBNETS, description="Maximum subnets per request")


class AWSNamingConfig(BaseModel):
    """AWS naming patterns and validation rules."""

    subnet: str = Field(
        _np.PATTERNS["subnet"],
        description="Regex pattern for AWS subnet IDs",
    )
    security_group: str = Field(
        _np.PATTERNS["security_group"],
        description="Regex pattern for AWS security group IDs",
    )
    ec2_instance: str = Field(
        _np.PATTERNS["ec2_instance"],
        description="Regex pattern for AWS EC2 instance IDs",
    )
    ami: str = Field(
        _np.PATTERNS["ami"],
        description="Regex pattern for AWS AMI IDs (including SSM paths)",
    )
    ec2_fleet: str = Field(
        _np.PATTERNS["ec2_fleet"],
        description="Regex pattern for AWS EC2 fleet IDs",
    )
    launch_template: str = Field(
        _np.PATTERNS["launch_template"],
        description="Regex pattern for AWS launch template IDs",
    )
    instance_type: str = Field(
        _np.PATTERNS["instance_type"],
        description="Regex pattern for AWS instance types",
    )
    tag_key: str = Field(
        _np.PATTERNS["tag_key"],
        description="Regex pattern for AWS tag keys",
    )
    arn: str = Field(
        _np.PATTERNS["arn"],
        description="Regex pattern for AWS ARNs",
    )
    account_id: str = Field(
        _np.PATTERNS["account_id"],
        description="Regex pattern for AWS account IDs",
    )
    region: str = Field(
        _np.PATTERNS["region"],
        description="Regex pattern for AWS region names (e.g. us-east-1, eu-west-2)",
    )
    limits: AWSLimitsConfig = Field(
        default_factory=AWSLimitsConfig,  # type: ignore[call-arg]
        description="AWS service limits and constraints",
    )

    @property
    def patterns(self) -> dict[str, str]:
        """Return patterns as a dict for backward-compatible access."""
        return {k: v for k, v in self.model_dump().items() if k != "limits"}
