"""Additional coverage tests for json_utils.py.

Coverage targets: lines 58,60,83,88,91-94,96,98-100,139-142,144,146-148,
150-153,155,157-159
"""

from __future__ import annotations

import pytest

from orb.infrastructure.utilities.json_utils import (
    JSONParseError,
    safe_json_dumps,
    safe_json_loads,
)

pytestmark = pytest.mark.unit


class TestSafeJsonLoadsMissingBranches:
    """Target the branches in safe_json_loads not covered by the existing suite."""

    def test_wrong_type_raises_when_requested(self):
        # Line 60 — raise_on_error path after non-string type check
        with pytest.raises(JSONParseError):
            safe_json_loads(12345, raise_on_error=True)  # type: ignore[arg-type]

    def test_wrong_type_with_context_in_error_message(self):
        # Line 58-60 — context prepended to error message
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_loads(99.9, raise_on_error=True, context="my-loader")  # type: ignore[arg-type]
        assert "my-loader" in str(exc_info.value)

    def test_json_decode_error_with_context_raises(self):
        # Line 76-78 — context-aware JSONDecodeError with raise_on_error
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_loads("{invalid}", raise_on_error=True, context="parser")
        assert "parser" in str(exc_info.value)
        assert exc_info.value.original_error is not None

    def test_json_decode_error_long_input_truncated_in_log(self):
        # Line 69 — long string gets truncated to 100 chars + "..."
        long_invalid = "x" * 200 + "{"
        result = safe_json_loads(long_invalid, default="fallback")
        assert result == "fallback"

    def test_unicode_decode_error_raises_when_requested(self):
        # Lines 83, 88 — unicode error with raise_on_error
        invalid_utf8 = b"\xff\xfe"
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_loads(invalid_utf8, raise_on_error=True)
        assert exc_info.value.original_error is not None

    def test_unicode_decode_error_with_context(self):
        # Line 82-84 — context prepended to unicode error message
        invalid_utf8 = b"\xff\xfe"
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_loads(invalid_utf8, raise_on_error=True, context="decoder")
        assert "decoder" in str(exc_info.value)


class TestSafeJsonDumpsMissingBranches:
    """Target branches in safe_json_dumps not covered by the existing suite."""

    def test_value_error_returns_default(self):
        # Lines 139-142 — ValueError path via allow_nan=False + infinity
        import math

        result = safe_json_dumps(math.inf, default="ERR", allow_nan=False)
        assert result == "ERR"

    def test_value_error_raises_when_requested(self):
        # Lines 144, 146-148 — ValueError raise_on_error path
        import math

        with pytest.raises(JSONParseError) as exc_info:
            safe_json_dumps(math.inf, raise_on_error=True, allow_nan=False)
        assert exc_info.value.original_error is not None

    def test_value_error_with_context_in_message(self):
        # Line 144 — context prepended in ValueError path
        import math

        with pytest.raises(JSONParseError) as exc_info:
            safe_json_dumps(math.inf, raise_on_error=True, context="encoder-ctx", allow_nan=False)
        assert "encoder-ctx" in str(exc_info.value)

    def test_type_error_with_context_in_message(self):
        # Lines 130-133 — context prepended in TypeError path
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_dumps(object(), raise_on_error=True, context="type-ctx")
        assert "type-ctx" in str(exc_info.value)

    def test_type_error_original_error_preserved(self):
        # Lines 136-137 — original_error is set
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_dumps(object(), raise_on_error=True)
        assert exc_info.value.original_error is not None

    def test_type_error_returns_default_without_raise(self):
        # Line 137 fallback path (no raise)
        result = safe_json_dumps(object(), default="NOPE")
        assert result == "NOPE"
