"""Secure input function to replace direct input() calls."""

from typing import Callable, Optional

from infrastructure.logging.logger import get_logger

from .input_validator import ValidationError, sanitize_input


def secure_input(
    prompt: str,
    default: Optional[str] = None,
    validator: Optional[Callable[[str], str]] = None,
    max_length: int = 1000,
    allow_empty: bool = True,
    max_attempts: int = 3,
) -> str:
    """
    Secure input function with validation and sanitization.

    Args:
        prompt: Prompt to display to user
        default: Default value if user provides empty input
        validator: Optional validation function
        max_length: Maximum input length
        allow_empty: Whether to allow empty input
        max_attempts: Maximum validation attempts

    Returns:
        Validated and sanitized input

    Raises:
        ValidationError: If validation fails after max attempts
    """
    logger = get_logger(__name__)

    for attempt in range(max_attempts):
        try:
            # Get user input
            user_input = input(prompt).strip()

            # Handle empty input
            if not user_input:
                if default is not None:
                    return default
                if allow_empty:
                    return ""
                raise ValidationError("Input cannot be empty")

            # Sanitize input
            sanitized = sanitize_input(user_input, max_length)

            # Apply custom validator if provided
            if validator:
                sanitized = validator(sanitized)

            return sanitized

        except ValidationError as e:
            logger.warning("Input validation failed (attempt %d/%d, exc_info=True): %s", attempt + 1, max_attempts, e)
            if attempt < max_attempts - 1:
                logger.warning("Invalid input: %s. Please try again.", e)
            else:
                raise ValidationError(f"Input validation failed after {max_attempts} attempts: {e}")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("Unexpected error during input: %s", e, exc_info=True)
            raise ValidationError(f"Input error: {e}")

    raise ValidationError(f"Input validation failed after {max_attempts} attempts")
