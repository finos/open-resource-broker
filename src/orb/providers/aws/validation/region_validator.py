"""AWS region validation."""

import re

from orb.infrastructure.validation.input_validator import ValidationError

AWS_REGION = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")


def validate_aws_region(value: str) -> str:
    """
    Validate AWS region format.

    Args:
        value: Region string

    Returns:
        Validated region

    Raises:
        ValidationError: If region format is invalid
    """
    if not AWS_REGION.match(value):
        raise ValidationError(
            "Invalid AWS region format (expected: us-east-1, eu-west-1, etc.)"
        )
    return value
