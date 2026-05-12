"""Unit tests for RequestDTO."""

from datetime import datetime, timezone

import pytest

from orb.application.request.dto import RequestDTO
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.domain.request.value_objects import RequestId


def _make_request(**kwargs) -> Request:
    defaults = dict(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000001"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tmpl-1",
        requested_count=1,
        created_at=datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return Request(**defaults)


@pytest.mark.unit
class TestRequestDTOStatusCheckFields:
    """from_domain passes status check timestamps through from the aggregate."""

    def test_status_check_fields_none_when_not_set(self):
        request = _make_request()
        dto = RequestDTO.from_domain(request)
        assert dto.first_status_check is None
        assert dto.last_status_check is None

    def test_status_check_fields_passed_through(self):
        first = datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc)
        last = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        request = _make_request(first_status_check=first, last_status_check=last)
        dto = RequestDTO.from_domain(request)
        assert dto.first_status_check == first
        assert dto.last_status_check == last

    def test_status_check_fields_not_hardcoded_to_none(self):
        """Regression: fields must come from the domain object, not be hardcoded None."""
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        request = _make_request()
        updated = request.record_status_check(now=now)
        dto = RequestDTO.from_domain(updated)
        assert dto.first_status_check == now
        assert dto.last_status_check == now


@pytest.mark.unit
class TestRequestDTOVerboseField:
    """verbose must not be a model field on RequestDTO."""

    def test_verbose_not_in_model_fields(self):
        assert "verbose" not in RequestDTO.model_fields

    def test_to_dict_default_excludes_detail_fields(self):
        request = _make_request(
            first_status_check=datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc),
            last_status_check=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        dto = RequestDTO.from_domain(request)
        result = dto.to_dict()
        assert "first_status_check" not in result
        assert "last_status_check" not in result
        assert "metadata" not in result

    def test_to_dict_verbose_true_includes_detail_fields(self):
        first = datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc)
        last = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        request = _make_request(first_status_check=first, last_status_check=last)
        dto = RequestDTO.from_domain(request)
        result = dto.to_dict(verbose=True)
        assert "first_status_check" in result
        assert "last_status_check" in result
        assert "metadata" in result
