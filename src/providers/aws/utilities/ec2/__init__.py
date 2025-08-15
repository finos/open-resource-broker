"""EC2 utility functions organized by responsibility."""

# Import all functions from submodules
from providers.aws.utilities.ec2.instances import *

# Re-export commonly used functions
__all__ = [
    # Instance management functions
    "get_instance_by_id",
    "create_instance",
    "terminate_instance",
]
