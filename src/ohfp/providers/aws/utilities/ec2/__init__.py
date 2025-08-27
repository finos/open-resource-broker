"""EC2 utility functions organized by responsibility."""

# Import all functions from submodules
from providers.aws.utilities.ec2.instances import (
    create_instance,
    get_instance_by_id,
    terminate_instance,
)

# Re-export commonly used functions
__all__: list[str] = [
    "create_instance",
    # Instance management functions
    "get_instance_by_id",
    "terminate_instance",
]
