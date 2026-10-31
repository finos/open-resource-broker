"""Environment variable expansion utilities."""

import os
import re
from typing import Any


def expand_env_vars(value: str | dict | Any) -> str | dict | Any:
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
    return expand_env_vars(config)  # type: ignore[return-value]


_UNEXPANDED_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def reject_unexpanded_placeholder(value: str | None, field_name: str) -> str | None:
    """Reject a secret setting that still holds a ``${VAR}`` placeholder.

    Call this on a value that has already been through environment expansion.
    Expansion leaves ``${VAR}`` untouched when ``VAR`` is not set, so a secret
    that references a missing variable would otherwise be used as the literal
    placeholder text. The error names the setting and the variable and never
    includes the value.

    Only the braced ``${VAR}`` form is checked; a bare ``$word`` may be part of a
    legitimate secret.

    Raises:
        ValueError: if ``value`` contains an unexpanded ``${VAR}`` placeholder.
    """
    if value is None:
        return value
    match = _UNEXPANDED_PLACEHOLDER.search(value)
    if match:
        # Only the variable name is echoed: anything after it (a default, an
        # operator) could be part of the secret.
        name = re.match(r"[A-Za-z_][A-Za-z0-9_]*", match.group(1))
        subject = f"environment variable {name.group(0)!r}" if name else "an environment variable"
        raise ValueError(
            f"{field_name} references {subject}, "
            "which is not set. Set the variable or remove the placeholder."
        )
    return value
