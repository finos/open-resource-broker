"""Pure domain utility functions with no infrastructure dependencies."""

from datetime import datetime, timezone


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware datetime.

    Accepts the ``Z`` UTC designator, which ``datetime.fromisoformat`` rejects
    before Python 3.11. A value without an offset is taken as UTC.

    Raises:
        ValueError: If *value* is empty or not a valid ISO 8601 timestamp.
    """
    text = value.strip()
    if text and text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
