"""Extended unit tests for Request aggregate covering uncovered branches."""

from datetime import datetime, timezone

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import InvalidRequestStateError, RequestValidationError
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.domain.request.value_objects import RequestId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    request_type=RequestType.ACQUIRE,
    status=RequestStatus.PENDING,
    **kwargs,
):
    rid = RequestId.generate(request_type)
    defaults = dict(
        request_id=rid,
        request_type=request_type,
        provider_type="aws",
        template_id="tpl-001",
        requested_count=2,
    )
    defaults.update(kwargs)
    r = Request(**defaults)
    if status != RequestStatus.PENDING:
        # Bypass state-machine to set desired test start state
        r = r.model_copy(update={"status": status})
    return r


# ---------------------------------------------------------------------------
# create_new_request factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateNewRequest:
    def test_generates_id_when_none_provided(self):
        req = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-001",
            machine_count=3,
            provider_type="aws",
        )
        assert req.request_id.value.startswith("req-")

    def test_uses_provided_id_without_prefix(self):
        import uuid

        bare_uuid = str(uuid.uuid4())
        req = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-001",
            machine_count=1,
            provider_type="aws",
            request_id=bare_uuid,
        )
        assert req.request_id.value == f"req-{bare_uuid}"

    def test_uses_provided_id_with_correct_prefix(self):
        import uuid

        full_id = f"req-{uuid.uuid4()}"
        req = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-001",
            machine_count=1,
            provider_type="aws",
            request_id=full_id,
        )
        assert req.request_id.value == full_id

    def test_wrong_prefix_for_type_raises(self):
        import uuid

        wrong_id = f"ret-{uuid.uuid4()}"
        with pytest.raises(RequestValidationError, match="wrong prefix"):
            Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="tpl-001",
                machine_count=1,
                provider_type="aws",
                request_id=wrong_id,
            )

    def test_return_type_uses_ret_prefix(self):
        req = Request.create_new_request(
            request_type=RequestType.RETURN,
            template_id="tpl-001",
            machine_count=1,
            provider_type="aws",
        )
        assert req.request_id.value.startswith("ret-")

    def test_fires_request_created_event(self):
        req = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-001",
            machine_count=1,
            provider_type="aws",
        )
        event_types = [type(e).__name__ for e in req.get_domain_events()]
        assert "RequestCreatedEvent" in event_types


# ---------------------------------------------------------------------------
# create_return_request factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateReturnRequest:
    def test_creates_return_request(self):
        req = Request.create_return_request(
            machine_ids=["i-1", "i-2"],
            provider_type="aws",
            provider_name="aws-us-east-1",
            provider_api="EC2Fleet",
        )
        assert req.request_type == RequestType.RETURN
        assert req.requested_count == 2
        assert req.machine_ids == ["i-1", "i-2"]

    def test_fires_request_created_event(self):
        req = Request.create_return_request(
            machine_ids=["i-1"],
            provider_type="aws",
            provider_name="aws-us-east-1",
            provider_api="RunInstances",
        )
        event_types = [type(e).__name__ for e in req.get_domain_events()]
        assert "RequestCreatedEvent" in event_types

    def test_uses_provided_request_id(self):
        import uuid

        rid = f"ret-{uuid.uuid4()}"
        req = Request.create_return_request(
            machine_ids=["i-1"],
            provider_type="aws",
            provider_name="aws-us-east-1",
            provider_api="EC2Fleet",
            request_id=rid,
        )
        assert req.request_id.value == rid


# ---------------------------------------------------------------------------
# start_processing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestStartProcessing:
    def test_transitions_pending_to_in_progress(self):
        req = _make_request()
        updated = req.start_processing()
        assert updated.status == RequestStatus.IN_PROGRESS
        assert updated.started_at is not None

    def test_raises_if_not_pending(self):
        req = _make_request(status=RequestStatus.IN_PROGRESS)
        with pytest.raises(InvalidRequestStateError):
            req.start_processing()

    def test_fires_status_changed_event(self):
        req = _make_request()
        updated = req.start_processing()
        event_types = [type(e).__name__ for e in updated.get_domain_events()]
        assert "RequestStatusChangedEvent" in event_types


# ---------------------------------------------------------------------------
# add_failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestAddFailure:
    def test_increments_failed_count(self):
        req = _make_request(requested_count=3, status=RequestStatus.IN_PROGRESS)
        updated = req.add_failure("something broke")
        assert updated.failed_count == 1

    def test_completes_with_failed_status_when_all_fail(self):
        req = _make_request(requested_count=1)
        updated = req.add_failure("error")
        assert updated.status == RequestStatus.FAILED
        assert updated.completed_at is not None

    def test_completes_with_partial_when_some_succeed(self):
        req = _make_request(requested_count=2)
        req = req.model_copy(update={"successful_count": 1})
        updated = req.add_failure("one failed")
        assert updated.status == RequestStatus.PARTIAL

    def test_stores_error_details_when_provided(self):
        req = _make_request(requested_count=5)
        updated = req.add_failure("error", error_details={"code": "E001"})
        assert "error_0" in updated.error_details


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestCancel:
    def test_cancels_pending_request(self):
        req = _make_request()
        updated = req.cancel("user requested")
        assert updated.status == RequestStatus.CANCELLED
        assert updated.status_message == "user requested"
        assert updated.completed_at is not None

    def test_raises_if_already_completed(self):
        req = _make_request(status=RequestStatus.COMPLETED)
        with pytest.raises(InvalidRequestStateError):
            req.cancel("too late")

    def test_raises_if_already_failed(self):
        req = _make_request(status=RequestStatus.FAILED)
        with pytest.raises(InvalidRequestStateError):
            req.cancel("too late")

    def test_raises_if_already_cancelled(self):
        req = _make_request(status=RequestStatus.CANCELLED)
        with pytest.raises(InvalidRequestStateError):
            req.cancel("again")


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestComplete:
    def test_sets_completed_status(self):
        req = _make_request()
        updated = req.complete("all done")
        assert updated.status == RequestStatus.COMPLETED
        assert updated.completed_at is not None

    def test_default_message_when_none(self):
        req = _make_request()
        updated = req.complete()
        assert updated.status_message is not None

    def test_fires_completion_events(self):
        req = _make_request()
        updated = req.complete()
        event_types = [type(e).__name__ for e in updated.get_domain_events()]
        assert "RequestCompletedEvent" in event_types
        assert "RequestStatusChangedEvent" in event_types


# ---------------------------------------------------------------------------
# fail
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestFail:
    def test_sets_failed_status(self):
        req = _make_request()
        updated = req.fail("something broke")
        assert updated.status == RequestStatus.FAILED
        assert updated.status_message == "something broke"

    def test_stores_error_details_when_provided(self):
        req = _make_request()
        updated = req.fail("broken", error_details={"code": "E500"})
        assert updated.error_details == {"code": "E500"}


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestUpdateStatus:
    def test_valid_transition(self):
        req = _make_request()
        updated = req.update_status(RequestStatus.IN_PROGRESS)
        assert updated.status == RequestStatus.IN_PROGRESS

    def test_invalid_transition_raises(self):
        req = _make_request(status=RequestStatus.COMPLETED)
        with pytest.raises(InvalidRequestStateError):
            req.update_status(RequestStatus.PENDING)

    def test_force_bypasses_guard(self):
        req = _make_request(status=RequestStatus.COMPLETED)
        updated = req.update_status(RequestStatus.PENDING, force=True)
        assert updated.status == RequestStatus.PENDING

    def test_stamps_started_at_on_first_non_pending_transition(self):
        req = _make_request()
        assert req.started_at is None
        updated = req.update_status(RequestStatus.IN_PROGRESS)
        assert updated.started_at is not None

    def test_does_not_overwrite_existing_started_at(self):
        existing_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        req = _make_request()
        req = req.model_copy(
            update={"started_at": existing_ts, "status": RequestStatus.IN_PROGRESS}
        )
        updated = req.update_status(RequestStatus.COMPLETED, force=True)
        assert updated.started_at == existing_ts

    def test_stamps_completed_at_for_terminal_statuses(self):
        for terminal in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
            RequestStatus.PARTIAL,
            RequestStatus.TIMEOUT,
        ]:
            req = _make_request()
            updated = req.update_status(terminal, force=True)
            assert updated.completed_at is not None, f"Expected completed_at for {terminal}"


# ---------------------------------------------------------------------------
# resource_id / needs_machine_id_population
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestResourceHelpers:
    def test_resource_id_returns_first(self):
        req = _make_request()
        req = req.add_resource_id("rid-001")
        req = req.add_resource_id("rid-002")
        assert req.resource_id == "rid-001"

    def test_resource_id_none_when_empty(self):
        req = _make_request()
        assert req.resource_id is None

    def test_add_resource_id_idempotent(self):
        req = _make_request()
        req = req.add_resource_id("rid-001")
        req = req.add_resource_id("rid-001")
        assert req.resource_ids.count("rid-001") == 1

    def test_remove_resource_id(self):
        req = _make_request()
        req = req.add_resource_id("rid-001")
        req = req.remove_resource_id("rid-001")
        assert "rid-001" not in req.resource_ids

    def test_remove_nonexistent_resource_id_is_noop(self):
        req = _make_request()
        updated = req.remove_resource_id("nonexistent")
        assert updated is req

    def test_needs_machine_id_population_true_when_resource_ids_but_no_machine_ids(self):
        req = _make_request()
        req = req.add_resource_id("i-001")
        assert req.needs_machine_id_population() is True

    def test_needs_machine_id_population_false_for_return_type(self):
        req = _make_request(request_type=RequestType.RETURN)
        req = req.add_resource_id("i-001")
        assert req.needs_machine_id_population() is False

    def test_needs_machine_id_population_false_when_machine_ids_already_set(self):
        req = _make_request()
        req = req.add_resource_id("i-001")
        req = req.add_machine_ids(["i-001"])
        assert req.needs_machine_id_population() is False


# ---------------------------------------------------------------------------
# is_complete / is_successful / success_rate / duration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestProperties:
    def test_is_complete_for_terminal_states(self):
        for status in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
            RequestStatus.PARTIAL,
        ]:
            req = _make_request(status=status)
            assert req.is_complete is True

    def test_is_not_complete_for_active_states(self):
        for status in [RequestStatus.PENDING, RequestStatus.IN_PROGRESS]:
            req = _make_request(status=status)
            assert req.is_complete is False

    def test_is_successful_only_for_completed(self):
        assert _make_request(status=RequestStatus.COMPLETED).is_successful is True
        assert _make_request(status=RequestStatus.PARTIAL).is_successful is False

    def test_success_rate_zero_when_requested_count_zero(self):
        req = _make_request(requested_count=1)
        req = req.model_copy(update={"requested_count": 0})
        assert req.success_rate == 0.0

    def test_success_rate_calculation(self):
        req = _make_request(requested_count=4)
        req = req.model_copy(update={"successful_count": 2})
        assert req.success_rate == 50.0

    def test_duration_none_when_not_started(self):
        req = _make_request()
        assert req.duration is None

    def test_duration_uses_completed_at_when_available(self):
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
        req = _make_request()
        req = req.model_copy(update={"started_at": start, "completed_at": end})
        assert req.duration == 60

    def test_duration_falls_back_to_now_when_no_completed_at(self):
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        req = _make_request()
        req = req.model_copy(update={"started_at": start})
        assert req.duration is not None
        assert req.duration > 0


# ---------------------------------------------------------------------------
# record_status_check / with_last_fulfilment / with_launch_template_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestMetadataHelpers:
    def test_record_status_check_sets_first(self):
        req = _make_request()
        now = datetime.now(timezone.utc)
        updated = req.record_status_check(now)
        assert updated.first_status_check == now
        assert updated.last_status_check == now

    def test_record_status_check_does_not_overwrite_first(self):
        req = _make_request()
        first = datetime(2024, 1, 1, tzinfo=timezone.utc)
        second = datetime(2024, 1, 2, tzinfo=timezone.utc)
        req = req.record_status_check(first)
        req = req.record_status_check(second)
        assert req.first_status_check == first
        assert req.last_status_check == second

    def test_with_last_fulfilment_stores_snapshot(self):
        req = _make_request()
        snap = {"provider": "aws", "count": 3}
        updated = req.with_last_fulfilment(snap)
        assert updated.metadata["last_fulfilment"] == snap

    def test_with_launch_template_info(self):
        req = _make_request()
        updated = req.with_launch_template_info("lt-abc", "1")
        assert updated.provider_data["launch_template_id"] == "lt-abc"
        assert updated.provider_data["launch_template_version"] == "1"

    def test_update_metadata_merges(self):
        req = _make_request(metadata={"existing": "value"})
        updated = req.update_metadata({"new_key": "new_value"})
        assert updated.metadata["existing"] == "value"
        assert updated.metadata["new_key"] == "new_value"


# ---------------------------------------------------------------------------
# Domain events survive post-transition mutators
# ---------------------------------------------------------------------------


class TestDomainEventsPreservedByMutators:
    """A status transition emits domain events into _domain_events; later
    metadata/provider mutators (model_dump + model_validate based) previously
    DROPPED those events. These guard the mutate-then-save path so subscribers
    still observe the transition."""

    def _transitioned(self):
        """Return a Request that has just transitioned to COMPLETED (carrying a
        RequestStatusChangedEvent + RequestCompletedEvent) with the creation
        event cleared so only transition events remain."""
        req = _make_request()
        req.clear_domain_events()  # drop the RequestCreatedEvent
        completed = req.complete("done")
        # sanity: complete() emits two events
        names = {type(e).__name__ for e in completed.get_domain_events()}
        assert "RequestStatusChangedEvent" in names
        return completed

    def test_update_metadata_preserves_events(self):
        completed = self._transitioned()
        before = {type(e).__name__ for e in completed.get_domain_events()}
        mutated = completed.update_metadata({"k": "v"})
        after = {type(e).__name__ for e in mutated.get_domain_events()}
        assert before == after
        assert "RequestStatusChangedEvent" in after

    def test_with_last_fulfilment_preserves_events(self):
        completed = self._transitioned()
        before = {type(e).__name__ for e in completed.get_domain_events()}
        mutated = completed.with_last_fulfilment({"count": 1})
        after = {type(e).__name__ for e in mutated.get_domain_events()}
        assert before == after

    def test_set_provider_data_preserves_events(self):
        completed = self._transitioned()
        mutated = completed.set_provider_data({"x": 1})
        after = {type(e).__name__ for e in mutated.get_domain_events()}
        assert "RequestStatusChangedEvent" in after

    def test_add_resource_id_preserves_events(self):
        completed = self._transitioned()
        mutated = completed.add_resource_id("fleet-123")
        after = {type(e).__name__ for e in mutated.get_domain_events()}
        assert "RequestStatusChangedEvent" in after

    def test_set_fulfilment_diagnostic_preserves_events(self):
        from datetime import datetime, timezone

        from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic

        completed = self._transitioned()
        diag = FulfilmentDiagnostic(
            category=DiagnosticCategory.CAPACITY,
            summary="s",
            occurred_at=datetime.now(timezone.utc),
        )
        mutated = completed.set_fulfilment_diagnostic(diag)
        after = {type(e).__name__ for e in mutated.get_domain_events()}
        assert "RequestStatusChangedEvent" in after

    def test_chained_mutators_preserve_events_through_save_shape(self):
        """Full mutate-then-save chain: transition → update_metadata →
        set_provider_data still yields the transition event for the repository's
        get_domain_events() extraction."""
        completed = self._transitioned()
        chained = completed.update_metadata({"a": 1}).set_provider_data({"b": 2})
        names = [type(e).__name__ for e in chained.get_domain_events()]
        assert "RequestStatusChangedEvent" in names
        assert "RequestCompletedEvent" in names
