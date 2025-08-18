"""
String utility functions for the AWS Host Factory Plugin.

This module contains utility functions for working with strings.
"""

import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional


def is_empty(value: Optional[str]) -> bool:
    """
    Check if a string is empty or None.

    Args:
        value: String to check

    Returns:
        True if string is empty or None, False otherwise
    """
    return value is None or value.strip() == ""


def is_not_empty(value: Optional[str]) -> bool:
    """
    Check if a string is not empty and not None.

    Args:
        value: String to check

    Returns:
        True if string is not empty and not None, False otherwise
    """
    return not is_empty(value)


def truncate(value: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length.

    Args:
        value: String to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated string
    """
    if len(value) <= max_length:
        return value
    return value[: max_length - len(suffix)] + suffix


def to_snake_case(value: str) -> str:
    """
    Convert a string to snake_case.

    Args:
        value: String to convert

    Returns:
        String in snake_case
    """
    # Replace non-alphanumeric characters with underscores
    s1 = re.sub(r"[^a-zA-Z0-9]", "_", value)
    # Insert underscores between lowercase and uppercase letters
    s2 = re.sub(r"([a-z])([A-Z])", r"\1_\2", s1)
    # Convert to lowercase
    return s2.lower()


def to_camel_case(value: str) -> str:
    """
    Convert a string to camelCase.

    Args:
        value: String to convert

    Returns:
        String in camelCase
    """
    # Split by non-alphanumeric characters
    words = re.split(r"[^a-zA-Z0-9]", value)
    # Capitalize all words except the first one
    return words[0].lower() + "".join(word.capitalize() for word in words[1:])


def to_pascal_case(value: str) -> str:
    """
    Convert a string to PascalCase.

    Args:
        value: String to convert

    Returns:
        String in PascalCase
    """
    # Split by non-alphanumeric characters
    words = re.split(r"[^a-zA-Z0-9]", value)
    # Capitalize all words
    return "".join(word.capitalize() for word in words)


def to_kebab_case(value: str) -> str:
    """
    Convert a string to kebab-case.

    Args:
        value: String to convert

    Returns:
        String in kebab-case
    """
    # Replace non-alphanumeric characters with hyphens
    s1 = re.sub(r"[^a-zA-Z0-9]", "-", value)
    # Insert hyphens between lowercase and uppercase letters
    s2 = re.sub(r"([a-z])([A-Z])", r"\1-\2", s1)
    # Convert to lowercase
    return s2.lower()


def generate_uuid() -> str:
    """
    Generate a UUID.

    Returns:
        UUID as string
    """
    return str(uuid.uuid4())


def hash_string(value: str, algorithm: str = "sha256") -> str:
    """
    Hash a string using the specified algorithm.

    Args:
        value: String to hash
        algorithm: Hash algorithm to use

    Returns:
        Hashed string
    """
    if algorithm == "md5":
        # Use usedforsecurity=False to indicate this is not for security purposes
        return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()
    elif algorithm == "sha1":
        # Use usedforsecurity=False to indicate this is not for security purposes
        return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()
    elif algorithm == "sha256":
        return hashlib.sha256(value.encode()).hexdigest()
    elif algorithm == "sha512":
        return hashlib.sha512(value.encode()).hexdigest()
    else:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}") from e


def mask_sensitive_data(value: str, mask_char: str = "*", visible_chars: int = 4) -> str:
    """
    Mask sensitive data in a string.

    Args:
        value: String to mask
        mask_char: Character to use for masking
        visible_chars: Number of characters to leave visible at the end

    Returns:
        Masked string
    """
    if len(value) <= visible_chars:
        return value

    masked_length = len(value) - visible_chars
    return mask_char * masked_length + value[-visible_chars:]


def split_by_case(value: str) -> List[str]:
    """
    Split a string by case changes.

    Args:
        value: String to split

    Returns:
        List of words
    """
    # Split by case changes
    return re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", value)


def convert_case(value: str, case_type: str) -> str:
    """
    Convert a string to the specified case type.

    Args:
        value: String to convert
        case_type: Case type to convert to (snake, camel, pascal, kebab)

    Returns:
        Converted string
    """
    if case_type == "snake":
        return to_snake_case(value)
    elif case_type == "camel":
        return to_camel_case(value)
    elif case_type == "pascal":
        return to_pascal_case(value)
    elif case_type == "kebab":
        return to_kebab_case(value)
    else:
        raise ValueError(f"Unsupported case type: {case_type}") from e


def convert_dict_keys(data: Dict[str, Any], case_type: str) -> Dict[str, Any]:
    """
    Convert all dictionary keys to the specified case type.

    Args:
        data: Dictionary to convert
        case_type: Case type to convert to (snake, camel, pascal, kebab)

    Returns:
        Dictionary with converted keys
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        new_key = convert_case(key, case_type)
        if isinstance(value, dict):
            result[new_key] = convert_dict_keys(value, case_type)
        elif isinstance(value, list):
            result[new_key] = [
                convert_dict_keys(item, case_type) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[new_key] = value

    return result
