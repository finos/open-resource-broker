"""Input validation utilities for CLI and API inputs."""

import re
from typing import Optional


class ValidationError(Exception):
    """Input validation error."""

    pass


class InputValidator:
    """Input validation utilities."""

    # Character whitelists
    ALPHANUMERIC = re.compile(r"^[a-zA-Z0-9]+$")
    ALPHANUMERIC_DASH = re.compile(r"^[a-zA-Z0-9\-_]+$")
    INTEGER = re.compile(r"^-?\d+$")

    # Dangerous characters that could indicate injection attacks
    DANGEROUS_CHARS = ["<", ">", "&", "|", ";", "`", "$", "(", ")", "{", "}", "[", "]", "\n", "\r"]

    @staticmethod
    def sanitize_input(value: str, max_length: int = 1000) -> str:
        """
        Sanitize user input by removing dangerous characters.

        Args:
            value: Input value to sanitize
            max_length: Maximum allowed length

        Returns:
            Sanitized input

        Raises:
            ValidationError: If input contains dangerous characters
        """
        if not isinstance(value, str):
            raise ValidationError("Input must be a string")

        # Check length
        if len(value) > max_length:
            raise ValidationError(f"Input exceeds maximum length of {max_length}")

        # Check for dangerous characters
        for char in InputValidator.DANGEROUS_CHARS:
            if char in value:
                raise ValidationError(f"Input contains dangerous character: {char}")

        # Strip whitespace
        return value.strip()

    @staticmethod
    def validate_length(value: str, min_length: int = 0, max_length: int = 1000) -> str:
        """
        Validate input length.

        Args:
            value: Input value
            min_length: Minimum allowed length
            max_length: Maximum allowed length

        Returns:
            Validated input

        Raises:
            ValidationError: If length is invalid
        """
        if len(value) < min_length:
            raise ValidationError(f"Input must be at least {min_length} characters")

        if len(value) > max_length:
            raise ValidationError(f"Input must be at most {max_length} characters")

        return value

    @staticmethod
    def validate_alphanumeric(value: str, allow_dash: bool = False) -> str:
        """
        Validate that input contains only alphanumeric characters.

        Args:
            value: Input value
            allow_dash: Whether to allow dashes and underscores

        Returns:
            Validated input

        Raises:
            ValidationError: If input contains non-alphanumeric characters
        """
        pattern = InputValidator.ALPHANUMERIC_DASH if allow_dash else InputValidator.ALPHANUMERIC

        if not pattern.match(value):
            allowed = (
                "alphanumeric characters, dashes, and underscores"
                if allow_dash
                else "alphanumeric characters"
            )
            raise ValidationError(f"Input must contain only {allowed}")

        return value

    @staticmethod
    def validate_integer(
        value: str, min_value: Optional[int] = None, max_value: Optional[int] = None
    ) -> int:
        """
        Validate and convert input to integer.

        Args:
            value: Input value
            min_value: Minimum allowed value
            max_value: Maximum allowed value

        Returns:
            Validated integer

        Raises:
            ValidationError: If input is not a valid integer
        """
        if not InputValidator.INTEGER.match(value):
            raise ValidationError("Input must be a valid integer")

        try:
            int_value = int(value)
        except ValueError:
            raise ValidationError("Input must be a valid integer")

        if min_value is not None and int_value < min_value:
            raise ValidationError(f"Value must be at least {min_value}")

        if max_value is not None and int_value > max_value:
            raise ValidationError(f"Value must be at most {max_value}")

        return int_value

    @staticmethod
    def validate_choice(value: str, choices: list[str], case_sensitive: bool = False) -> str:
        """
        Validate that input is one of allowed choices.

        Args:
            value: Input value
            choices: List of allowed choices
            case_sensitive: Whether comparison is case-sensitive

        Returns:
            Validated input

        Raises:
            ValidationError: If input is not in choices
        """
        if not case_sensitive:
            value_lower = value.lower()
            choices_lower = [c.lower() for c in choices]
            if value_lower not in choices_lower:
                raise ValidationError(f"Input must be one of: {', '.join(choices)}")
            # Return original case from choices
            return choices[choices_lower.index(value_lower)]
        else:
            if value not in choices:
                raise ValidationError(f"Input must be one of: {', '.join(choices)}")
            return value

# Convenience functions
def sanitize_input(value: str, max_length: int = 1000) -> str:
    """Sanitize user input."""
    return InputValidator.sanitize_input(value, max_length)


def validate_length(value: str, min_length: int = 0, max_length: int = 1000) -> str:
    """Validate input length."""
    return InputValidator.validate_length(value, min_length, max_length)


def validate_alphanumeric(value: str, allow_dash: bool = False) -> str:
    """Validate alphanumeric input."""
    return InputValidator.validate_alphanumeric(value, allow_dash)


def validate_integer(
    value: str, min_value: Optional[int] = None, max_value: Optional[int] = None
) -> int:
    """Validate integer input."""
    return InputValidator.validate_integer(value, min_value, max_value)


def validate_choice(value: str, choices: list[str], case_sensitive: bool = False) -> str:
    """Validate choice input."""
    return InputValidator.validate_choice(value, choices, case_sensitive)
