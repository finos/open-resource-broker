"""Unit tests for secure_input function.

Coverage targets: lines 35,37-38,40,43-48,51,54-55,57,59-60,66-67,69-74,76
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orb.infrastructure.validation.input_validator import ValidationError
from orb.infrastructure.validation.secure_input import secure_input

pytestmark = pytest.mark.unit


class TestSecureInputHappyPaths:
    def test_returns_stripped_valid_input(self):
        with patch("builtins.input", return_value="  hello  "):
            result = secure_input("Enter: ")
        assert result == "hello"

    def test_uses_default_when_empty_and_default_provided(self):
        with patch("builtins.input", return_value=""):
            result = secure_input("Enter: ", default="my-default")
        assert result == "my-default"

    def test_allows_empty_when_allow_empty_true(self):
        with patch("builtins.input", return_value=""):
            result = secure_input("Enter: ", allow_empty=True)
        assert result == ""

    def test_applies_validator_to_sanitized_input(self):
        def upper_validator(s: str) -> str:
            return s.upper()

        with patch("builtins.input", return_value="hello"):
            result = secure_input("Enter: ", validator=upper_validator)
        assert result == "HELLO"

    def test_max_length_is_passed_to_sanitize(self):
        with patch("builtins.input", return_value="abc"):
            result = secure_input("Enter: ", max_length=5)
        assert result == "abc"


class TestSecureInputErrorPaths:
    def test_empty_input_without_default_and_not_allowed_raises(self):
        with patch("builtins.input", return_value=""), pytest.raises(ValidationError):
            secure_input("Enter: ", allow_empty=False)

    def test_dangerous_chars_raise_validation_error(self):
        with patch("builtins.input", return_value="hello<script>"), pytest.raises(ValidationError):
            secure_input("Enter: ")

    def test_retries_up_to_max_attempts_then_raises(self):
        call_count = 0

        def bad_input(_prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "bad<chars"

        with patch("builtins.input", side_effect=bad_input), pytest.raises(ValidationError):
            secure_input("Enter: ", max_attempts=3)
        assert call_count == 3

    def test_keyboard_interrupt_propagates(self):
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            secure_input("Enter: ")

    def test_unexpected_exception_wraps_validation_error(self):
        with (
            patch("builtins.input", side_effect=OSError("io error")),
            pytest.raises(ValidationError, match="Input error"),
        ):
            secure_input("Enter: ")

    def test_validator_raising_validation_error_triggers_retry(self):
        call_count = 0

        def failing_validator(s: str) -> str:
            nonlocal call_count
            call_count += 1
            raise ValidationError("validator rejected")

        with patch("builtins.input", return_value="good"), pytest.raises(ValidationError):
            secure_input("Enter: ", validator=failing_validator, max_attempts=2)
        assert call_count == 2

    def test_max_attempts_one_raises_immediately_on_failure(self):
        with patch("builtins.input", return_value=""), pytest.raises(ValidationError):
            secure_input("Enter: ", allow_empty=False, max_attempts=1)
