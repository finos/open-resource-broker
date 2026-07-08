"""Unit tests for RequestSerializer._apply_nullable_defaults.

Focuses on the NULL machine_ids invariant for return vs acquire requests.
"""

import pytest

from orb.infrastructure.storage.repositories.request_repository import RequestSerializer


def _serializer():
    return RequestSerializer()


@pytest.mark.unit
class TestApplyNullableDefaultsMachineIds:
    """_apply_nullable_defaults must enforce the machine_ids invariant by request type."""

    def test_return_request_null_machine_ids_raises(self):
        """NULL machine_ids on a return request is a domain invariant violation — must raise."""
        data = {
            "request_id": "ret-corrupt-001",
            "request_type": "return",
            "machine_ids": None,
        }
        with pytest.raises(ValueError, match="domain-model invariant violation"):
            RequestSerializer._apply_nullable_defaults(data)

    def test_acquire_request_null_machine_ids_coerced_to_empty_list(self):
        """NULL machine_ids on an acquire request is acceptable — coerce to []."""
        data = {
            "request_id": "acq-001",
            "request_type": "acquire",
            "machine_ids": None,
        }
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["machine_ids"] == []

    def test_acquire_request_existing_machine_ids_preserved(self):
        """Non-NULL machine_ids must not be modified for acquire requests."""
        data = {
            "request_id": "acq-002",
            "request_type": "acquire",
            "machine_ids": ["i-abc123", "i-def456"],
        }
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["machine_ids"] == ["i-abc123", "i-def456"]

    def test_return_request_existing_machine_ids_preserved(self):
        """Non-NULL machine_ids on a return request must pass through untouched."""
        data = {
            "request_id": "ret-valid-001",
            "request_type": "return",
            "machine_ids": ["i-aaa000000001", "i-bbb000000002"],
        }
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["machine_ids"] == ["i-aaa000000001", "i-bbb000000002"]

    def test_unknown_request_type_null_machine_ids_coerced_to_empty_list(self):
        """For non-return request types, NULL machine_ids is coerced to [] (safe default)."""
        data = {
            "request_id": "other-001",
            "request_type": "provision",
            "machine_ids": None,
        }
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["machine_ids"] == []

    def test_missing_request_type_null_machine_ids_coerced(self):
        """When request_type is absent (old record), NULL machine_ids is coerced to []."""
        data = {
            "request_id": "legacy-001",
            "machine_ids": None,
        }
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["machine_ids"] == []
