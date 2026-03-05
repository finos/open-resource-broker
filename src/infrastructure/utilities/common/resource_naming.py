"""Resource naming helper functions."""

from typing import Optional

from config.schemas.common_schema import ResourceConfig


def get_resource_prefix(resource_type: str, config: Optional[ResourceConfig] = None) -> str:
    """
    Get the prefix for a specific resource type.

    Args:
        resource_type: Type of resource (launch_template, instance, fleet, asg, tag)
        config: Resource configuration. Required — raises if not provided.

    Returns:
        Prefix for the specified resource type

    Raises:
        ValueError: If config is not provided
    """
    if config is None:
        raise ValueError(
            f"get_resource_prefix() requires a config argument. "
            f"Use config_port.get_resource_prefix('{resource_type}') instead."
        )

    if hasattr(config.prefixes, resource_type):
        return getattr(config.prefixes, resource_type)

    return config.default_prefix
