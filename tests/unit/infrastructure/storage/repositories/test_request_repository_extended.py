"""Extended unit tests for RequestRepositoryImpl covering uncovered branches."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.domain.base.exceptions import InfrastructureError
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestStatus, RequestType
from orb.infrastructure.storage.repositories.request_repository import (
    RequestRepositoryImpl,
    RequestSerializer,
)

_REQ_ID = "req-8e6bf339-8207-45b6-ab3a-81ee4ab8abc6"
_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_request(
    request_type: str = "acquire",
    status: str = "pending",
) -> Request:
    return Request.create_new_request(
        request_type=RequestType(request_type),
        template_id="tpl-001",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api="RunInstances",
    )


def _minimal_row(request_id: str = _REQ_ID, status: str = "pending") -> dict:
    return {
        "request_id": request_id,
        "template_id": "tpl-001",
        "requested_count": 2,
        "desired_capacity": 2,
        "request_type": "acquire",
        "status": status,
        "created_at": _NOW.isoformat(),
        "version": 0,
        "provider_type": "aws",
    }


def _make_repo(storage: MagicMock | None = None, publisher: MagicMock | None = None):
    if storage is None:
        storage = MagicMock()
        storage.find_by_id.return_value = None
        storage.find_by_criteria.return_value = []
        storage.find_all.return_value = {}
        storage.exists.return_value = False
    return RequestRepositoryImpl(storage, event_publisher=publisher), storage


# ---------------------------------------------------------------------------
# RequestSerializer — from_dict edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestSerializerFromDictEdgeCases:
    def test_request_id_from_stringified_dict(self) -> None:
        s = RequestSerializer()
        row = _minimal_row()
        # Use a stringified dict with a valid req-uuid value
        row["request_id"] = f"{{'value': '{_REQ_ID}'}}"
        request = s.from_dict(row)
        assert request is not None

    def test_request_id_from_value_dict(self) -> None:
        s = RequestSerializer()
        row = _minimal_row()
        row["request_id"] = {"value": _REQ_ID}
        request = s.from_dict(row)
        assert request is not None

    def test_request_id_from_json_dict_format(self) -> None:
        s = RequestSerializer()
        row = _minimal_row()
        import json

        row["request_id"] = json.dumps({"value": _REQ_ID})
        request = s.from_dict(row)
        assert request is not None

    def test_machine_ids_defaults_to_empty_list(self) -> None:
        s = RequestSerializer()
        row = _minimal_row()
        # Don't include machine_ids
        row.pop("machine_ids", None)
        request = s.from_dict(row)
        assert request.machine_ids == []


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplSave:
    def test_save_calls_storage(self) -> None:
        repo, storage = _make_repo()
        request = _make_request()
        repo.save(request)
        storage.save.assert_called_once()

    def test_save_returns_event_list(self) -> None:
        repo, _ = _make_repo()
        events = repo.save(_make_request())
        assert isinstance(events, list)

    def test_save_raises_on_storage_failure(self) -> None:
        repo, storage = _make_repo()
        storage.save.side_effect = RuntimeError("disk full")
        with pytest.raises(InfrastructureError, match="Unexpected error: disk full"):
            repo.save(_make_request())

    def test_save_with_event_publisher_publishes_started_event(self) -> None:
        publisher = MagicMock()
        repo, _ = _make_repo(publisher=publisher)
        repo.save(_make_request())
        assert publisher.publish.called

    def test_save_slow_query_event_triggered_when_threshold_exceeded(self) -> None:
        publisher = MagicMock()
        repo, _ = _make_repo(publisher=publisher)
        repo.slow_query_threshold_ms = 0.0  # force slow-query detection
        repo.save(_make_request())
        # Should have at least 3 publish calls: started + completed + slow_query
        assert publisher.publish.call_count >= 3


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — get_by_id / find_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplGetById:
    def test_get_by_id_returns_none_when_missing(self) -> None:
        repo, _ = _make_repo()
        result = repo.get_by_id(RequestId(value=_REQ_ID))
        assert result is None

    def test_get_by_id_returns_request_when_found(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _minimal_row()
        repo, _ = _make_repo(storage)
        result = repo.get_by_id(RequestId(value=_REQ_ID))
        assert result is not None

    def test_get_by_id_raises_on_storage_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_id.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.get_by_id(RequestId(value=_REQ_ID))

    def test_find_by_id_with_string_wraps_in_request_id(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _minimal_row()
        repo, _ = _make_repo(storage)
        result = repo.find_by_id(_REQ_ID)  # string, not RequestId
        assert result is not None

    def test_find_by_request_id_string(self) -> None:
        storage = MagicMock()
        storage.find_by_id.return_value = _minimal_row()
        repo, _ = _make_repo(storage)
        result = repo.find_by_request_id(_REQ_ID)
        assert result is not None


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — find_by_status / find_by_template_id / find_by_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplFindByCriteria:
    def test_find_by_status_returns_list(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_minimal_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_status(RequestStatus.PENDING)
        assert len(result) == 1

    def test_find_by_status_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_status(RequestStatus.PENDING)

    def test_find_by_template_id_returns_list(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_minimal_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_template_id("tpl-001")
        assert len(result) == 1

    def test_find_by_template_id_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_template_id("tpl-001")

    def test_find_by_type_returns_list(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_minimal_row()]
        repo, _ = _make_repo(storage)
        result = repo.find_by_type(RequestType("acquire"))
        assert len(result) == 1

    def test_find_by_type_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_type(RequestType("acquire"))


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — find_pending / find_active / find_by_date_range
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplFindActive:
    def test_find_pending_requests(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = [_minimal_row(status="pending")]
        repo, _ = _make_repo(storage)
        result = repo.find_pending_requests()
        assert len(result) == 1

    def test_find_active_requests_deduplicates(self) -> None:
        storage = MagicMock()
        # Same request ID returned for both pending and in_progress
        storage.find_by_criteria.return_value = [_minimal_row(status="pending")]
        repo, _ = _make_repo(storage)
        result = repo.find_active_requests()
        # De-duplicated — same row should appear once
        ids = [r.request_id for r in result]
        assert len(ids) == len(set(ids))

    def test_find_active_requests_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_active_requests()

    def test_find_by_date_range_filters_correctly(self) -> None:
        storage = MagicMock()
        storage.find_all.return_value = {_REQ_ID: _minimal_row()}
        repo, _ = _make_repo(storage)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = repo.find_by_date_range(start, end)
        assert len(result) == 1

    def test_find_by_date_range_excludes_out_of_range(self) -> None:
        storage = MagicMock()
        storage.find_all.return_value = {_REQ_ID: _minimal_row()}
        repo, _ = _make_repo(storage)
        # Date range in 2020 — our row is from 2024
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2020, 12, 31, tzinfo=timezone.utc)
        result = repo.find_by_date_range(start, end)
        assert result == []

    def test_find_by_date_range_adds_utc_when_naive(self) -> None:
        storage = MagicMock()
        storage.find_all.return_value = {_REQ_ID: _minimal_row()}
        repo, _ = _make_repo(storage)
        # Pass naive datetimes — method should add UTC
        start = datetime(2024, 1, 1)
        end = datetime(2025, 1, 1)
        result = repo.find_by_date_range(start, end)
        assert len(result) == 1

    def test_find_by_date_range_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_all.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_by_date_range(datetime.now(tz=timezone.utc), datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — find_all / delete / find_by_ids / exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplFindAllDeleteExists:
    def test_find_all_returns_list(self) -> None:
        storage = MagicMock()
        storage.find_all.return_value = {_REQ_ID: _minimal_row()}
        repo, _ = _make_repo(storage)
        result = repo.find_all()
        assert isinstance(result, list)
        assert len(result) == 1

    def test_find_all_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_all.side_effect = RuntimeError("crash")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: crash"):
            repo.find_all()

    def test_delete_calls_storage(self) -> None:
        storage = MagicMock()
        storage.exists.return_value = True
        repo, _ = _make_repo(storage)
        repo.delete(RequestId(value=_REQ_ID))
        storage.delete.assert_called()

    def test_delete_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.delete.side_effect = RuntimeError("boom")
        storage.exists.return_value = True
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.delete(RequestId(value=_REQ_ID))

    def test_find_by_ids_returns_matching(self) -> None:
        req_id_2 = "req-00000000-0000-0000-0000-000000000002"
        storage = MagicMock()
        storage.find_by_id.return_value = _minimal_row()
        repo, _ = _make_repo(storage)
        result = repo.find_by_ids([_REQ_ID, req_id_2])
        assert len(result) == 2

    def test_find_by_ids_skips_missing(self) -> None:
        repo, _ = _make_repo()
        result = repo.find_by_ids([_REQ_ID])  # valid ID, not found in storage → skipped
        assert result == []

    def test_find_by_ids_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.find_by_id.side_effect = RuntimeError("boom")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.find_by_ids([_REQ_ID])

    def test_exists_returns_true(self) -> None:
        storage = MagicMock()
        storage.exists.return_value = True
        repo, _ = _make_repo(storage)
        assert repo.exists(RequestId(value=_REQ_ID)) is True

    def test_exists_returns_false(self) -> None:
        repo, _ = _make_repo()
        # storage.exists returns False by default in _make_repo
        assert repo.exists(RequestId(value=_REQ_ID)) is False

    def test_exists_raises_on_failure(self) -> None:
        storage = MagicMock()
        storage.exists.side_effect = RuntimeError("boom")
        repo, _ = _make_repo(storage)
        with pytest.raises(InfrastructureError, match="Unexpected error: boom"):
            repo.exists(RequestId(value=_REQ_ID))


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — count_by_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplCountByStatus:
    def test_delegates_to_count_by_column_when_available(self) -> None:
        storage = MagicMock()
        storage.count_by_column = MagicMock(return_value={"pending": 2})
        repo, _ = _make_repo(storage)
        result = repo.count_by_status()
        assert result == {"pending": 2}

    def test_falls_back_when_count_by_column_returns_empty(self) -> None:
        storage = MagicMock()
        storage.count_by_column = MagicMock(return_value={})
        storage.find_all.return_value = {}
        repo, _ = _make_repo(storage)
        result = repo.count_by_status()
        assert isinstance(result, dict)
