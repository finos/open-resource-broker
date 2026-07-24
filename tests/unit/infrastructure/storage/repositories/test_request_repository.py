"""Unit tests for request_repository: RequestSerializer and RequestRepositoryImpl."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.domain.base.exceptions import InfrastructureError
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestStatus, RequestType
from orb.infrastructure.storage.repositories.request_repository import (
    RequestRepositoryImpl,
    RequestSerializer,
    _id_str,
    _unwrap_provider_data,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ_ID = "req-8e6bf339-8207-45b6-ab3a-81ee4ab8abc6"
_RET_ID = "ret-a8867bd4-7a8c-4401-96f2-63409ccf5588"
_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_request(
    request_type: str = "acquire",
    status: str = "pending",
    provider_api: str | None = "RunInstances",
) -> Request:
    return Request.create_new_request(
        request_type=RequestType(request_type),
        template_id="tpl-001",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api=provider_api,
    )


def _minimal_row(
    request_id: str = _REQ_ID,
    request_type: str = "acquire",
    status: str = "pending",
) -> dict:
    return {
        "request_id": request_id,
        "template_id": "tpl-001",
        "requested_count": 2,
        "desired_capacity": 2,
        "request_type": request_type,
        "status": status,
        "created_at": _NOW.isoformat(),
        "version": 0,
        "provider_type": "aws",
    }


def _make_repo(storage: MagicMock | None = None, publisher: MagicMock | None = None):
    if storage is None:
        storage = MagicMock()
    return RequestRepositoryImpl(storage, event_publisher=publisher), storage


# ---------------------------------------------------------------------------
# _id_str
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdStr:
    """_id_str extracts the string representation of a value object or plain string."""

    def test_extracts_value_from_value_object(self):
        class _FakeVO:
            value = "abc-123"

        assert _id_str(_FakeVO()) == "abc-123"

    def test_returns_string_unchanged(self):
        assert _id_str("plain-str") == "plain-str"

    def test_coerces_integer_to_str(self):
        assert _id_str(42) == "42"


# ---------------------------------------------------------------------------
# _unwrap_provider_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnwrapProviderData:
    """_unwrap_provider_data promotes a nested provider_data envelope to the top level."""

    def test_flat_record_passes_through_unchanged(self):
        raw = {"method": "prov", "target_units": 3}
        result = _unwrap_provider_data(raw)
        assert result == {"method": "prov", "target_units": 3}

    def test_nested_envelope_is_promoted(self):
        raw = {"method": "prov", "provider_data": {"target_units": 3, "fleet_id": "flx-001"}}
        result = _unwrap_provider_data(raw)
        assert "provider_data" not in result
        assert result["target_units"] == 3
        assert result["fleet_id"] == "flx-001"
        assert result["method"] == "prov"

    def test_outer_keys_win_on_collision(self):
        raw = {"key": "outer", "provider_data": {"key": "inner"}}
        result = _unwrap_provider_data(raw)
        assert result["key"] == "outer"

    def test_none_returns_empty_dict(self):
        assert _unwrap_provider_data(None) == {}

    def test_non_dict_nested_value_is_left_alone(self):
        raw = {"provider_data": "not-a-dict"}
        result = _unwrap_provider_data(raw)
        # The nested value is not a dict, so it passes through unchanged
        assert result["provider_data"] == "not-a-dict"

    def test_idempotent_on_already_flat_data(self):
        raw = {"method": "prov", "target_units": 5}
        assert _unwrap_provider_data(_unwrap_provider_data(raw)) == _unwrap_provider_data(raw)


# ---------------------------------------------------------------------------
# RequestSerializer — to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestSerializerToDict:
    """RequestSerializer.to_dict produces a complete serializable dictionary."""

    def _s(self):
        return RequestSerializer()

    def test_produces_required_keys(self):
        request = _make_request()
        data = self._s().to_dict(request)
        for key in (
            "request_id",
            "template_id",
            "requested_count",
            "desired_capacity",
            "request_type",
            "status",
            "created_at",
            "version",
            "provider_type",
            "schema_version",
        ):
            assert key in data, f"Missing key: {key}"

    def test_schema_version_is_2_0_0(self):
        data = self._s().to_dict(_make_request())
        assert data["schema_version"] == "2.0.0"

    def test_status_is_string(self):
        data = self._s().to_dict(_make_request())
        assert isinstance(data["status"], str)

    def test_request_type_is_string(self):
        data = self._s().to_dict(_make_request())
        assert isinstance(data["request_type"], str)

    def test_message_alias_equals_status_message(self):
        data = self._s().to_dict(_make_request())
        assert data["message"] == data["status_message"]

    def test_error_message_alias_equals_status_message(self):
        data = self._s().to_dict(_make_request())
        assert data["error_message"] == data["status_message"]

    def test_legacy_machine_count_key_present(self):
        """machine_count is a legacy alias for requested_count that must remain for compat."""
        data = self._s().to_dict(_make_request())
        assert "machine_count" in data
        assert data["machine_count"] == data["requested_count"]

    def test_metadata_defaults_to_empty_dict(self):
        data = self._s().to_dict(_make_request())
        assert data["metadata"] == {}

    def test_provider_data_defaults_to_empty_dict(self):
        data = self._s().to_dict(_make_request())
        assert data["provider_data"] == {}

    def test_resource_ids_defaults_to_empty_list(self):
        data = self._s().to_dict(_make_request())
        assert data["resource_ids"] == []

    def test_machine_ids_defaults_to_empty_list(self):
        data = self._s().to_dict(_make_request())
        assert data["machine_ids"] == []


# ---------------------------------------------------------------------------
# RequestSerializer — from_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestSerializerFromDict:
    """RequestSerializer.from_dict reconstructs a Request from stored data."""

    def _s(self):
        return RequestSerializer()

    def test_round_trip_preserves_request_type(self):
        request = _make_request(request_type="acquire")
        data = self._s().to_dict(request)
        restored = self._s().from_dict(data)
        assert restored.request_type == RequestType.ACQUIRE

    def test_round_trip_preserves_status(self):
        request = _make_request()
        data = self._s().to_dict(request)
        restored = self._s().from_dict(data)
        assert restored.status == RequestStatus.PENDING

    def test_round_trip_preserves_requested_count(self):
        request = _make_request()
        data = self._s().to_dict(request)
        restored = self._s().from_dict(data)
        assert restored.requested_count == 2

    def test_round_trip_preserves_template_id(self):
        request = _make_request()
        data = self._s().to_dict(request)
        restored = self._s().from_dict(data)
        assert restored.template_id == "tpl-001"

    def test_round_trip_preserves_provider_api(self):
        request = _make_request(provider_api="EC2Fleet")
        data = self._s().to_dict(request)
        restored = self._s().from_dict(data)
        assert restored.provider_api == "EC2Fleet"

    def test_legacy_message_key_used_as_status_message(self):
        row = dict(_minimal_row(), message="legacy message")
        restored = self._s().from_dict(row)
        assert restored.status_message == "legacy message"

    def test_status_message_takes_priority_over_message(self):
        row = dict(_minimal_row(), status_message="canonical", message="legacy")
        restored = self._s().from_dict(row)
        assert restored.status_message == "canonical"

    def test_error_message_is_fallback_when_both_absent(self):
        row = dict(_minimal_row(), error_message="old error format")
        restored = self._s().from_dict(row)
        assert restored.status_message == "old error format"

    def test_legacy_machine_count_key_accepted(self):
        """Rows stored with machine_count instead of requested_count must deserialize."""
        row = _minimal_row()
        row.pop("requested_count", None)
        row["machine_count"] = 5
        restored = self._s().from_dict(row)
        assert restored.requested_count == 5

    def test_provider_data_nested_envelope_unwrapped_on_read(self):
        row = dict(
            _minimal_row(),
            provider_data={"method": "prov", "provider_data": {"fleet_id": "flx-001"}},
        )
        restored = self._s().from_dict(row)
        assert restored.provider_data.get("fleet_id") == "flx-001"
        assert "provider_data" not in restored.provider_data

    def test_none_machine_ids_filtered_out(self):
        row = dict(_minimal_row(), machine_ids=[None, "i-001", None, "i-002"])
        restored = self._s().from_dict(row)
        assert restored.machine_ids == ["i-001", "i-002"]

    def test_missing_optional_timestamps_default_to_none(self):
        row = _minimal_row()
        restored = self._s().from_dict(row)
        assert restored.started_at is None
        assert restored.completed_at is None

    def test_legacy_provider_type_defaults_to_aws(self):
        row = _minimal_row()
        row.pop("provider_type", None)
        # No provider_type in row — falls back via LEGACY_DEFAULT_PROVIDER_TYPE
        restored = self._s().from_dict(row)
        assert restored.provider_type is not None

    def test_unknown_diagnostic_category_degrades_request_still_loads(self):
        """A persisted diagnostic with an unknown category (version skew) must
        NOT make the whole request unloadable — the advisory diagnostic degrades
        to None and the request still loads."""
        row = dict(
            _minimal_row(),
            fulfilment_diagnostic={
                "category": "some_future_category",  # not a valid DiagnosticCategory
                "summary": "from a newer version",
                "occurred_at": _NOW.isoformat(),
            },
        )
        restored = self._s().from_dict(row)
        assert restored.fulfilment_diagnostic is None
        assert restored.status == RequestStatus.PENDING

    def test_diagnostic_missing_required_field_degrades_to_none(self):
        """A valid-JSON dict missing the required occurred_at field degrades to
        None rather than raising on load."""
        row = dict(
            _minimal_row(),
            fulfilment_diagnostic={"category": "capacity", "summary": "no timestamp"},
        )
        restored = self._s().from_dict(row)
        assert restored.fulfilment_diagnostic is None

    def test_valid_diagnostic_survives_load(self):
        """Guard: a well-formed diagnostic still round-trips through the load."""
        from orb.domain.base.diagnostic import DiagnosticCategory

        row = dict(
            _minimal_row(),
            fulfilment_diagnostic={
                "category": "capacity",
                "summary": "Partially fulfilled 2/3",
                "occurred_at": _NOW.isoformat(),
            },
        )
        restored = self._s().from_dict(row)
        assert restored.fulfilment_diagnostic is not None
        assert restored.fulfilment_diagnostic.category == DiagnosticCategory.CAPACITY

    def test_caller_dict_not_mutated(self):
        row = _minimal_row()
        original_keys = set(row.keys())
        self._s().from_dict(row)
        assert set(row.keys()) == original_keys


@pytest.mark.unit
class TestParseFulfilmentDiagnostic:
    """RequestSerializer._parse_fulfilment_diagnostic honours the advisory,
    never-load-critical contract for malformed / version-skewed payloads."""

    def _p(self, raw):
        return RequestSerializer._parse_fulfilment_diagnostic(raw)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_non_json_string_returns_none(self):
        assert self._p("{not json") is None

    def test_json_non_dict_returns_none(self):
        assert self._p("[1, 2, 3]") is None

    def test_unknown_category_dict_returns_none(self):
        raw = {
            "category": "not_a_real_category",
            "summary": "x",
            "occurred_at": _NOW.isoformat(),
        }
        assert self._p(raw) is None

    def test_missing_occurred_at_returns_none(self):
        assert self._p({"category": "capacity", "summary": "x"}) is None

    def test_valid_dict_passes_through(self):
        raw = {"category": "capacity", "summary": "x", "occurred_at": _NOW.isoformat()}
        assert self._p(raw) == raw

    def test_valid_json_string_parses_to_dict(self):
        import json

        raw = {"category": "auth", "summary": "denied", "occurred_at": _NOW.isoformat()}
        assert self._p(json.dumps(raw)) == raw


# ---------------------------------------------------------------------------
# RequestSerializer — _parse_request_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseRequestId:
    """_parse_request_id handles all persisted request_id formats."""

    def _s(self):
        return RequestSerializer()

    def test_plain_string_uuid_format(self):
        rid = self._s()._parse_request_id(_REQ_ID)
        assert str(rid.value) == _REQ_ID

    def test_dict_with_value_key(self):
        rid = self._s()._parse_request_id({"value": _REQ_ID})
        assert str(rid.value) == _REQ_ID

    def test_stringified_dict_with_value_key(self):
        stringified = f"{{'value': '{_REQ_ID}'}}"
        rid = self._s()._parse_request_id(stringified)
        assert str(rid.value) == _REQ_ID

    def test_return_id_format(self):
        rid = self._s()._parse_request_id(_RET_ID)
        assert str(rid.value) == _RET_ID


# ---------------------------------------------------------------------------
# RequestSerializer — _apply_nullable_defaults (additional cases)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyNullableDefaultsAdditional:
    """Additional _apply_nullable_defaults cases not covered in the existing test file."""

    def test_null_metadata_coerced_to_dict(self):
        data = {"metadata": None}
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["metadata"] == {}

    def test_null_error_details_coerced_to_dict(self):
        data = {"error_details": None}
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["error_details"] == {}

    def test_null_provider_data_coerced_to_dict(self):
        data = {"provider_data": None}
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["provider_data"] == {}

    def test_null_resource_ids_coerced_to_list(self):
        data = {"resource_ids": None}
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["resource_ids"] == []

    def test_existing_non_null_values_preserved(self):
        data = {
            "metadata": {"key": "val"},
            "error_details": {"code": 42},
            "provider_data": {"fleet": "flx-001"},
            "resource_ids": ["r-001"],
            "machine_ids": ["i-001"],
            "request_type": "acquire",
        }
        result = RequestSerializer._apply_nullable_defaults(data)
        assert result["metadata"] == {"key": "val"}
        assert result["error_details"] == {"code": 42}
        assert result["resource_ids"] == ["r-001"]
        assert result["machine_ids"] == ["i-001"]


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplSave:
    """RequestRepositoryImpl.save delegates to storage and extracts domain events."""

    def test_save_calls_storage_save(self):
        repo, storage = _make_repo()
        request = _make_request()
        repo.save(request)
        assert storage.save.called

    def test_save_passes_serialized_dict_to_storage(self):
        repo, storage = _make_repo()
        request = _make_request()
        repo.save(request)
        # called with positional args: (entity_id, data)
        args = storage.save.call_args[0]
        assert len(args) == 2
        entity_id, data = args
        assert isinstance(entity_id, str)
        assert isinstance(data, dict)

    def test_save_returns_domain_events(self):
        repo, _ = _make_repo()
        request = _make_request()
        events = repo.save(request)
        # create_new_request emits a RequestCreatedEvent
        assert len(events) >= 1

    def test_save_clears_domain_events_after_extraction(self):
        repo, _ = _make_repo()
        request = _make_request()
        repo.save(request)
        # Events should be cleared on the request after save
        assert request.get_domain_events() == []

    def test_save_publishes_storage_events_when_publisher_configured(self):
        publisher = MagicMock()
        repo, _ = _make_repo(publisher=publisher)
        request = _make_request()
        repo.save(request)
        # At minimum, a started and completed event are published
        assert publisher.publish.call_count >= 2

    def test_save_swallows_publisher_exceptions(self):
        publisher = MagicMock()
        publisher.publish.side_effect = RuntimeError("publish fail")
        repo, _ = _make_repo(publisher=publisher)
        request = _make_request()
        # Must not raise even though the publisher throws
        events = repo.save(request)
        assert isinstance(events, list)

    def test_save_raises_on_storage_failure(self):
        repo, storage = _make_repo()
        storage.save.side_effect = RuntimeError("disk full")
        request = _make_request()
        with pytest.raises(InfrastructureError, match="Unexpected error: disk full"):
            repo.save(request)


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — get_by_id / find_by_id / find_by_request_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplGetById:
    """RequestRepositoryImpl retrieval methods delegate to storage and deserialize."""

    def test_get_by_id_returns_request_when_found(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_row()
        rid = RequestId(value=_REQ_ID)
        result = repo.get_by_id(rid)
        assert result is not None
        assert str(result.request_id.value) == _REQ_ID

    def test_get_by_id_returns_none_when_not_found(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = None
        rid = RequestId(value=_REQ_ID)
        result = repo.get_by_id(rid)
        assert result is None

    def test_find_by_id_string_input_is_wrapped_and_resolved(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_row()
        result = repo.find_by_id(_REQ_ID)
        assert result is not None

    def test_find_by_request_id_delegates_to_get_by_id(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_row()
        result = repo.find_by_request_id(_REQ_ID)
        assert result is not None

    def test_find_by_request_id_returns_none_when_not_found(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = None
        result = repo.find_by_request_id(_REQ_ID)
        assert result is None


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — find_by_status / type / template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplFindByCriteria:
    """find_by_status, find_by_type, find_by_template_id delegate to storage criteria."""

    def test_find_by_status_passes_status_value_to_storage(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_row()]
        results = repo.find_by_status(RequestStatus.PENDING)
        assert len(results) == 1
        storage.find_by_criteria.assert_called_once_with({"status": "pending"})

    def test_find_by_status_returns_empty_list_when_no_results(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        results = repo.find_by_status(RequestStatus.COMPLETED)
        assert results == []

    def test_find_by_type_passes_request_type_to_storage(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_row()]
        repo.find_by_type(RequestType.ACQUIRE)
        storage.find_by_criteria.assert_called_once_with({"request_type": "acquire"})

    def test_find_by_template_id_passes_template_id_to_storage(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_row()]
        repo.find_by_template_id("tpl-001")
        storage.find_by_criteria.assert_called_once_with({"template_id": "tpl-001"})

    def test_find_pending_requests_delegates_to_find_by_status_pending(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_row()]
        results = repo.find_pending_requests()
        assert len(results) == 1
        storage.find_by_criteria.assert_called_with({"status": "pending"})


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — find_active_requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplFindActiveRequests:
    """find_active_requests merges PENDING and IN_PROGRESS without duplicates."""

    def test_deduplicates_requests_appearing_in_both_statuses(self):
        repo, storage = _make_repo()
        row = _minimal_row()
        # Return the same row for both pending and in_progress queries
        storage.find_by_criteria.side_effect = [[row], [row]]
        results = repo.find_active_requests()
        assert len(results) == 1

    def test_combines_pending_and_in_progress(self):
        repo, storage = _make_repo()
        pending_row = _minimal_row(request_id=_REQ_ID, status="pending")
        in_progress_row = _minimal_row(
            request_id=_RET_ID.replace("ret-", "req-"), status="in_progress"
        )
        storage.find_by_criteria.side_effect = [[pending_row], [in_progress_row]]
        results = repo.find_active_requests()
        assert len(results) == 2

    def test_returns_empty_when_no_active(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.side_effect = [[], []]
        results = repo.find_active_requests()
        assert results == []


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — find_by_date_range
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplFindByDateRange:
    """find_by_date_range filters all requests to those within the window."""

    def _rows(self):
        inside = _minimal_row()  # created_at = 2024-06-01
        outside_row = dict(_minimal_row(), created_at="2023-01-01T00:00:00+00:00")
        return inside, outside_row

    def test_returns_only_rows_within_range(self):
        repo, storage = _make_repo()
        inside, outside = self._rows()
        storage.find_all.return_value = [inside, outside]
        results = repo.find_by_date_range(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert len(results) == 1
        assert str(results[0].created_at.year) == "2024"

    def test_naive_start_end_dates_are_treated_as_utc(self):
        repo, storage = _make_repo()
        inside, _ = self._rows()
        storage.find_all.return_value = [inside]
        # Naive datetimes — should not raise
        results = repo.find_by_date_range(
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31),
        )
        assert len(results) == 1

    def test_returns_empty_when_nothing_matches(self):
        repo, storage = _make_repo()
        _, outside = self._rows()
        storage.find_all.return_value = [outside]
        results = repo.find_by_date_range(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert results == []


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — count_by_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplCountByStatus:
    """count_by_status uses SQL fast path when available, else groups in Python."""

    def test_fast_path_delegates_to_count_by_column(self):
        repo, storage = _make_repo()
        storage.count_by_column.return_value = {"pending": 3, "completed": 7}
        counts = repo.count_by_status()
        assert counts == {"pending": 3, "completed": 7}
        storage.count_by_column.assert_called_once_with("status")

    def test_slow_path_used_when_no_count_by_column_method(self):
        storage = MagicMock(
            spec=["find_by_id", "find_by_criteria", "find_all", "delete", "exists", "save"]
        )
        storage.find_all.return_value = [_minimal_row(), _minimal_row()]
        repo = RequestRepositoryImpl(storage)
        counts = repo.count_by_status()
        assert isinstance(counts, dict)

    def test_fast_path_falls_back_when_count_by_column_returns_empty(self):
        repo, storage = _make_repo()
        storage.count_by_column.return_value = {}  # empty — triggers slow path
        storage.find_all.return_value = []
        counts = repo.count_by_status()
        assert isinstance(counts, dict)


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — delete / exists / find_by_ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplDeleteAndExists:
    """delete, exists, and find_by_ids delegate correctly to storage."""

    def test_delete_calls_storage_delete(self):
        repo, storage = _make_repo()
        rid = RequestId(value=_REQ_ID)
        repo.delete(rid)
        storage.delete.assert_called_once_with(_REQ_ID)

    def test_exists_returns_true_when_storage_confirms(self):
        repo, storage = _make_repo()
        storage.exists.return_value = True
        rid = RequestId(value=_REQ_ID)
        assert repo.exists(rid) is True

    def test_exists_returns_false_when_storage_denies(self):
        repo, storage = _make_repo()
        storage.exists.return_value = False
        rid = RequestId(value=_REQ_ID)
        assert repo.exists(rid) is False

    def test_find_by_ids_returns_only_found_requests(self):
        repo, storage = _make_repo()
        storage.find_by_id.side_effect = [_minimal_row(), None]
        results = repo.find_by_ids([_REQ_ID, "req-00000000-0000-0000-0000-000000000000"])
        assert len(results) == 1

    def test_find_by_ids_empty_input_returns_empty_list(self):
        repo, _ = _make_repo()
        results = repo.find_by_ids([])
        assert results == []


# ---------------------------------------------------------------------------
# RequestRepositoryImpl — count_by_date_range / get_metrics_by_date_range
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestRepositoryImplMetrics:
    """Metrics helpers aggregate date-range results correctly."""

    def _setup_two_rows(self, statuses: list[str]):
        repo, storage = _make_repo()
        rows = [dict(_minimal_row(), status=s, created_at=_NOW.isoformat()) for s in statuses]
        storage.find_all.return_value = rows
        return repo

    def test_count_by_date_range_returns_matching_count(self):
        # "pending" and "in_progress" are valid RequestStatus values
        repo = self._setup_two_rows(["pending", "in_progress"])
        count = repo.count_by_date_range(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert count == 2

    def test_count_by_status_and_date_range_filters_by_status(self):
        # RequestStatus.FAILED has value "failed"
        repo = self._setup_two_rows(["pending", "failed"])
        count = repo.count_by_status_and_date_range(
            RequestStatus.FAILED,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert count == 1

    def test_get_metrics_by_date_range_returns_all_buckets(self):
        # Use valid status values: pending, complete, failed, in_progress
        repo = self._setup_two_rows(["pending", "complete", "failed", "in_progress"])
        metrics = repo.get_metrics_by_date_range(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert "total" in metrics
        assert "completed" in metrics
        assert "failed" in metrics
        assert "pending" in metrics
        assert "in_progress" in metrics
        assert metrics["total"] == 4

    def test_get_metrics_counts_each_status_correctly(self):
        # "complete" is the status value for RequestStatus.COMPLETED
        repo = self._setup_two_rows(["complete", "complete", "failed"])
        metrics = repo.get_metrics_by_date_range(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert metrics["completed"] == 2
        assert metrics["failed"] == 1
