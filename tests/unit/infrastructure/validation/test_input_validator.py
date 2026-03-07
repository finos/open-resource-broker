"""Tests for input validation utilities."""

import pytest

from orb.infrastructure.validation import (
    ValidationError,
    sanitize_input,
    validate_alphanumeric,
    validate_aws_region,
    validate_choice,
    validate_integer,
    validate_length,
)


def test_sanitize_input_valid():
    """Test sanitizing valid input."""
    result = sanitize_input("  hello world  ")
    assert result == "hello world"


def test_sanitize_input_dangerous_chars():
    """Test that dangerous characters are rejected."""
    dangerous_inputs = [
        "hello<script>",
        "test; rm -rf /",
        "value | cat /etc/passwd",
        "test`whoami`",
        "value$(command)",
    ]

    for dangerous in dangerous_inputs:
        with pytest.raises(ValidationError):
            sanitize_input(dangerous)


def test_sanitize_input_max_length():
    """Test max length validation."""
    long_input = "a" * 1001

    with pytest.raises(ValidationError):
        sanitize_input(long_input, max_length=1000)


def test_validate_length():
    """Test length validation."""
    assert validate_length("hello", min_length=3, max_length=10) == "hello"

    with pytest.raises(ValidationError):
        validate_length("hi", min_length=3)

    with pytest.raises(ValidationError):
        validate_length("hello world", max_length=5)


def test_validate_alphanumeric():
    """Test alphanumeric validation."""
    assert validate_alphanumeric("hello123") == "hello123"
    assert validate_alphanumeric("hello-world_123", allow_dash=True) == "hello-world_123"

    with pytest.raises(ValidationError):
        validate_alphanumeric("hello world")

    with pytest.raises(ValidationError):
        validate_alphanumeric("hello-world", allow_dash=False)


def test_validate_integer():
    """Test integer validation."""
    assert validate_integer("123") == 123
    assert validate_integer("-456") == -456
    assert validate_integer("100", min_value=0, max_value=200) == 100

    with pytest.raises(ValidationError):
        validate_integer("abc")

    with pytest.raises(ValidationError):
        validate_integer("50", min_value=100)

    with pytest.raises(ValidationError):
        validate_integer("300", max_value=200)


def test_validate_choice():
    """Test choice validation."""
    choices = ["option1", "option2", "option3"]

    assert validate_choice("option1", choices) == "option1"
    assert validate_choice("OPTION2", choices, case_sensitive=False) == "option2"

    with pytest.raises(ValidationError):
        validate_choice("option4", choices)

    with pytest.raises(ValidationError):
        validate_choice("OPTION1", choices, case_sensitive=True)


def test_validate_aws_region():
    """Test AWS region validation."""
    valid_regions = ["us-east-1", "eu-west-2", "ap-south-1"]

    for region in valid_regions:
        assert validate_aws_region(region) == region

    invalid_regions = ["us-east", "invalid", "us_east_1", "US-EAST-1"]

    for region in invalid_regions:
        with pytest.raises(ValidationError):
            validate_aws_region(region)
