"""Cleanup configuration schema."""

from pydantic import BaseModel


class CleanupResourcesConfig(BaseModel):
    """Per-resource-type cleanup toggles."""

    asg: bool = True
    ec2_fleet: bool = True
    spot_fleet: bool = True


class CleanupConfig(BaseModel):
    """Configuration for automatic cleanup of empty AWS resources after full machine return."""

    enabled: bool = True
    delete_launch_template: bool = True
    resources: CleanupResourcesConfig = CleanupResourcesConfig()
    dry_run: bool = False
