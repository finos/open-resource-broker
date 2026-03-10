"""Cleanup configuration schema.

NOTE: This schema is AWS-specific (asg, ec2_fleet, spot_fleet, run_instances are all
AWS resource types). It lives here rather than in providers/aws/configuration/ because
ProviderDefaults in provider_strategy_schema.py (a generic layer) holds a CleanupConfig
field, and moving it would require updating that generic schema plus 10+ test imports.
Known debt: if a second provider ever needs cleanup config, extract a generic base class.
"""

from pydantic import BaseModel


class CleanupResourcesConfig(BaseModel):
    """Per-resource-type cleanup toggles."""

    asg: bool = True
    ec2_fleet: bool = True
    spot_fleet: bool = True
    run_instances: bool = True


class CleanupConfig(BaseModel):
    """Configuration for automatic cleanup of empty AWS resources after full machine return."""

    enabled: bool = True
    delete_launch_template: bool = True
    resources: CleanupResourcesConfig = CleanupResourcesConfig()
    dry_run: bool = False
