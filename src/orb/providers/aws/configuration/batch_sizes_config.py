"""AWS-specific batch sizes configuration."""

from pydantic import BaseModel, Field, field_validator


class AWSBatchSizesConfig(BaseModel):
    """Batch sizes for AWS EC2 API operations."""

    terminate_instances: int = Field(
        25, description="Batch size for terminate_instances operations"
    )
    create_tags: int = Field(20, description="Batch size for create_tags operations")
    describe_instances: int = Field(25, description="Batch size for describe_instances operations")
    run_instances: int = Field(10, description="Batch size for run_instances operations")
    describe_spot_fleet_instances: int = Field(
        20, description="Batch size for describe_spot_fleet_instances operations"
    )
    describe_auto_scaling_groups: int = Field(
        20, description="Batch size for describe_auto_scaling_groups operations"
    )
    describe_launch_templates: int = Field(
        20, description="Batch size for describe_launch_templates operations"
    )
    describe_spot_fleet_requests: int = Field(
        20, description="Batch size for describe_spot_fleet_requests operations"
    )
    describe_ec2_fleet_instances: int = Field(
        20, description="Batch size for describe_ec2_fleet_instances operations"
    )
    describe_images: int = Field(15, description="Batch size for describe_images operations")
    describe_security_groups: int = Field(
        25, description="Batch size for describe_security_groups operations"
    )
    describe_subnets: int = Field(25, description="Batch size for describe_subnets operations")

    @field_validator(
        "terminate_instances",
        "create_tags",
        "describe_instances",
        "run_instances",
        "describe_spot_fleet_instances",
        "describe_auto_scaling_groups",
        "describe_launch_templates",
        "describe_spot_fleet_requests",
        "describe_ec2_fleet_instances",
        "describe_images",
        "describe_security_groups",
        "describe_subnets",
    )
    @classmethod
    def validate_batch_size(cls, v: int) -> int:
        """Validate batch size is at least 1."""
        if v < 1:
            raise ValueError("Batch size must be at least 1")
        return v
