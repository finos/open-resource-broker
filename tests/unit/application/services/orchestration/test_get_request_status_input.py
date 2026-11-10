"""GetRequestStatusInput rejects a call that names no request."""

import pytest

from orb.application.services.orchestration.dtos import GetRequestStatusInput
from orb.domain.request.exceptions import RequestValidationError


def test_no_request_ids_and_not_all_requests_is_rejected():
    with pytest.raises(RequestValidationError, match="request_ids"):
        GetRequestStatusInput(request_ids=[], all_requests=False)


def test_all_requests_needs_no_ids():
    assert GetRequestStatusInput(all_requests=True).request_ids == []


def test_explicit_ids_are_accepted():
    assert GetRequestStatusInput(request_ids=["req-1"]).request_ids == ["req-1"]
