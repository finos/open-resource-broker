"""Resource naming helper functions."""

from typing import Optional

from config import ResourceConfig
from config.manager import get_config_manager


def get_resource_prefix(resource_type: str, config: Optional[ResourceConfig] = None) -> str:
    """
    Get the prefix for a specific resource type.

    Args:
        resource_type: Type of resource (launch_template, instance, fleet, asg, tag)
        config: Optional resource configuration. If not provided, it will be loaded from the config manager.

    Returns:
        Prefix for the specified resource type
    """
    # Get config if not provided
    if config is None:
        config_manager = get_config_manager()
        config = config_manager.app_config.resource

    # Get the specific prefix if it exists
    if hasattr(config.prefixes, resource_type):
        specific_prefix = getattr(config.prefixes, resource_type)
        # If a specific prefix is defined (even if empty string), use it
        return specific_prefix

    # Otherwise fall back to the default prefix
    return config.default_prefix


def get_launch_template_name(request_id: str) -> str:
    """
    Get the name for a launch template based on the request ID.

    Args:
        request_id: Request ID

    Returns:
        Launch template name
    """
    prefix = get_resource_prefix("launch_template")
    return f"{prefix}{request_id}"


def get_instance_name(request_id: str) -> str:
    """
    Get the name for an instance based on the request ID.

    Args:
        request_id: Request ID

    Returns:
        Instance name
    """
    prefix = get_resource_prefix("instance")
    return f"{prefix}{request_id}"


def get_fleet_name(request_id: str) -> str:
    """
    Get the name for a fleet based on the request ID.

    Args:
        request_id: Request ID

    Returns:
        Fleet name
    """
    prefix = get_resource_prefix("fleet")
    return f"{prefix}{request_id}"


def get_asg_name(request_id: str) -> str:
    """
    Get the name for an Auto Scaling group based on the request ID.

    Args:
        request_id: Request ID

    Returns:
        Auto Scaling group name
    """
    prefix = get_resource_prefix("asg")
    return f"{prefix}{request_id}"


def get_tag_name(request_id: str) -> str:
    """
    Get the name for a tag based on the request ID.

    Args:
        request_id: Request ID

    Returns:
        Tag name
    """
    prefix = get_resource_prefix("tag")
    return f"{prefix}{request_id}"
