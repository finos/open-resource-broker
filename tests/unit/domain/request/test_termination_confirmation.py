"""Validation rules for operator-confirmed termination."""

import pytest

from orb.domain.base.exceptions import ValidationError
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.domain.request.termination_confirmation import (
    CONFIRMED_TERMINATED_KEY,
    MAX_MACHINE_IDS,
    MAX_REASON_LENGTH,
    confirmed_machine_ids,
    normalize_machine_ids,
    sanitize_identity,
    sanitize_reason,
    validate_confirmable,
)


def _return_request(provider_type="oci", machine_ids=("i-1", "i-2"), **kwargs):
    return Request.create_return_request(
        machine_ids=list(machine_ids),
        provider_type=provider_type,
        provider_name=f"{provider_type}-main",
        provider_api="OCICompute",
        **kwargs,
    )


@pytest.mark.unit
class TestSanitizeReason:
    def test_control_characters_and_newlines_are_flattened(self):
        assert sanitize_reason("gone\n\x1b[31mfake\r\tlog line\x00") == "gone [31mfake log line"

    def test_empty_reason_rejected(self):
        with pytest.raises(ValidationError):
            sanitize_reason(" \n\t ")

    def test_oversized_reason_rejected(self):
        with pytest.raises(ValidationError):
            sanitize_reason("x" * (MAX_REASON_LENGTH + 1))

    def test_limit_is_inclusive(self):
        assert len(sanitize_reason("x" * MAX_REASON_LENGTH)) == MAX_REASON_LENGTH


@pytest.mark.unit
class TestNormalizeMachineIds:
    def test_dedupes_in_order(self):
        assert normalize_machine_ids(["b", "a", "b", " a "]) == ["b", "a"]

    def test_requires_at_least_one(self):
        with pytest.raises(ValidationError):
            normalize_machine_ids([" ", ""])
        with pytest.raises(ValidationError):
            normalize_machine_ids(None)

    def test_count_is_capped(self):
        with pytest.raises(ValidationError):
            normalize_machine_ids([f"i-{n}" for n in range(MAX_MACHINE_IDS + 1)])


@pytest.mark.unit
def test_identity_is_never_empty_and_has_no_control_characters():
    assert sanitize_identity("") == "anonymous"
    assert sanitize_identity(None) == "anonymous"
    assert sanitize_identity("a\nb") == "a b"


@pytest.mark.unit
class TestValidateConfirmable:
    def test_accepts_subset_of_return_request(self):
        validate_confirmable(_return_request(), ["i-1"])

    def test_rejects_acquire_request(self):
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="t",
            machine_count=1,
            provider_type="oci",
            provider_name="oci-main",
        )
        with pytest.raises(ValidationError, match="not a return request"):
            validate_confirmable(request, ["i-1"])

    def test_accepts_any_provider(self):
        validate_confirmable(_return_request(provider_type="aws"), ["i-1"])

    def test_rejects_terminal_request(self):
        request = _return_request().model_copy(update={"status": RequestStatus.COMPLETED})
        with pytest.raises(ValidationError, match="nothing to confirm"):
            validate_confirmable(request, ["i-1"])

    def test_rejects_machines_from_another_request_naming_them(self):
        with pytest.raises(ValidationError, match="i-9"):
            validate_confirmable(_return_request(), ["i-1", "i-9"])


@pytest.mark.unit
def test_confirmed_machine_ids_reads_records():
    metadata = {CONFIRMED_TERMINATED_KEY: [{"machine_id": "i-1"}, "junk", {"machine_id": ""}]}
    assert confirmed_machine_ids(metadata) == {"i-1"}
    assert confirmed_machine_ids(None) == set()
