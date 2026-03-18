"""
String utility functions for the AWS Host Factory Plugin.

This module contains utility functions for working with strings.
"""


def extract_provider_type(provider_name: str) -> str:
    """Extract provider type from a provider name.

    Handles both underscore-separated (e.g. 'aws_us_east_1' -> 'aws')
    and hyphen-separated (e.g. 'aws-us-east-1' -> 'aws') formats.
    Falls back to the full name if no separator is found.

    Args:
        provider_name: Provider name string

    Returns:
        Provider type (the prefix before the first separator)
    """
    if "_" in provider_name:
        return provider_name.split("_", maxsplit=1)[0]
    if "-" in provider_name:
        return provider_name.split("-", maxsplit=1)[0]
    return provider_name
