"""Environment variable expansion utilities."""

import os
from typing import Any, Union


def expand_env_vars(value: Union[str, dict, Any]) -> Union[str, dict, Any]:
    """
    Automatically expand environment variables in configuration values.

    Supports formats:
    - $VAR_NAME
    - ${VAR_NAME}
    - $VAR_NAME/subpath
    - ${VAR_NAME}/subpath

    Args:
        value: Configuration value that may contain environment variables

    Returns:
        Value with environment variables expanded
    """
    if isinstance(value, str):
        return os.path.expandvars(value)
    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    else:
        return value


def expand_config_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """Expand environment variables in entire configuration."""
    return expand_env_vars(config)
