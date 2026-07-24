"""Unit tests for RequestStatusManagementService._update_request_status logic."""

from unittest.mock import MagicMock

import pytest

from orb.application.services.provisioning_orchestration_service import ProvisioningResult
from orb.application.services.request_status_management_service import (
    RequestStatusManagementService,
)
from orb.domain.request.request_types import RequestStatus


def _make_service():
    uow_factory = MagicMock()
    logger = MagicMock()
    return RequestStatusManagementService(uow_factory=uow_factory, logger=logger)


def _make_request(requested_count=2):
    req = MagicMock()
    req.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    req.requested_count = requested_count
    req.template_id = "tmpl-001"
    req.provider_type = "aws"
    req.provider_name = "aws-prod"
    req.provider_api = "RunInstances"
    req.metadata = {}
    req.provider_data = {}
    req.add_resource_id = MagicMock(return_value=req)
    req.add_machine_ids = MagicMock(return_value=req)
    req.update_status = MagicMock(return_value=req)
    return req


class TestUpdateRequestStatus:
    def setup_method(self):
        self.svc = _make_service()

    def test_full_success_sets_completed(self):
        req = _make_request(requested_count=2)
        self.svc._update_request_status(
            request=req,
            instance_count=2,
            requested_count=2,
            has_api_errors=False,
            provider_errors=[],
        )
        req.update_status.assert_called_once()
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.COMPLETED

    def test_full_count_with_errors_sets_completed(self):
        """Fleet errors are advisory when all requested capacity is met.

        EC2Fleet returns success-with-errors when one AZ was skipped but
        another absorbed the capacity. Marking the request PARTIAL in that
        case is misleading and locks the request in a non-success
        terminal state. Errors are still persisted under
        request.metadata['fleet_errors'].
        """
        req = _make_request(requested_count=2)
        errors = [{"error_code": "InsufficientCapacity", "error_message": "No capacity"}]
        self.svc._update_request_status(
            request=req,
            instance_count=2,
            requested_count=2,
            has_api_errors=True,
            provider_errors=errors,
        )
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.COMPLETED

    def test_partial_count_no_errors_sets_partial(self):
        req = _make_request(requested_count=5)
        self.svc._update_request_status(
            request=req,
            instance_count=3,
            requested_count=5,
            has_api_errors=False,
            provider_errors=[],
        )
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.PARTIAL

    def test_partial_count_with_errors_sets_partial(self):
        req = _make_request(requested_count=5)
        errors = [{"error_code": "Throttling", "error_message": "Rate exceeded"}]
        self.svc._update_request_status(
            request=req,
            instance_count=2,
            requested_count=5,
            has_api_errors=True,
            provider_errors=errors,
        )
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.PARTIAL

    def test_zero_instances_sets_in_progress(self):
        req = _make_request(requested_count=3)
        self.svc._update_request_status(
            request=req,
            instance_count=0,
            requested_count=3,
            has_api_errors=False,
            provider_errors=[],
        )
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.IN_PROGRESS

    def test_zero_instances_no_resource_ids_sets_failed(self):
        req = _make_request(requested_count=3)
        req.resource_ids = []
        self.svc._update_request_status(
            request=req,
            instance_count=0,
            requested_count=3,
            has_api_errors=False,
            provider_errors=[],
        )
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.FAILED

    def test_full_count_with_errors_message_signals_non_blocking_warnings(self):
        """Full fulfillment + errors → status_message says provisioning OK
        but flags warnings; the error codes themselves live on
        request.metadata['fleet_errors'], not the status_message.

        Verifies the status_message contract here; the metadata persistence
        contract is tested below at the higher entry point
        (``_update_request_status_from_result``) where the actual
        ``update_metadata`` call happens.
        """
        req = _make_request(requested_count=2)
        errors = [{"error_code": "InsufficientCapacity", "error_message": "No capacity"}]
        self.svc._update_request_status(
            request=req,
            instance_count=2,
            requested_count=2,
            has_api_errors=True,
            provider_errors=errors,
        )
        call_args = req.update_status.call_args[0]
        assert "provisioned" in call_args[1].lower()
        assert "warning" in call_args[1].lower()

    @pytest.mark.asyncio
    async def test_provisioning_result_with_fleet_errors_persists_them_in_metadata(self):
        """fleet_errors from provider_data must be persisted on request.metadata.

        Without this the error codes are user-invisible — only the
        status_message would reflect them, and that's deliberately a
        non-blocking summary not the error list. Tests the public entry
        point that does the metadata write.
        """
        from unittest.mock import MagicMock

        req = _make_request(requested_count=2)
        req.metadata = {}
        req.provider_data = {}
        captured: dict = {}

        def _capture_metadata(patch):
            captured.update(patch)
            req.metadata = {**req.metadata, **patch}
            return req

        req.update_metadata = MagicMock(side_effect=_capture_metadata)
        req.set_provider_data = MagicMock(return_value=req)
        req.add_resource_id = MagicMock(return_value=req)
        req.add_machine_ids = MagicMock(return_value=req)

        provisioning_result = MagicMock(
            resource_ids=["fleet-1"],
            # Empty instances list — exercises the metadata-persistence path
            # without dragging in the create-machine-aggregate side-effect.
            instances=[],
            machine_ids=[],
            provider_data={
                "fleet_errors": [
                    {"error_code": "InsufficientCapacity", "error_message": "No capacity"}
                ]
            },
            success=True,
            is_final=True,
        )

        await self.svc.update_request_from_provisioning(req, provisioning_result)

        assert "fleet_errors" in captured
        assert captured["fleet_errors"][0]["error_code"] == "InsufficientCapacity"


class TestAcquireTimeShortfallFinality:
    """Acquire-time settled-shortfall must terminalise for synchronous providers.

    A synchronous launch API (AWS RunInstances / instant fleet / MicroVM) that
    returns fewer instances than requested has *settled* — the missing capacity
    will never arrive. That shortfall must land on terminal PARTIAL immediately,
    not sit in the non-terminal PARTIAL_PENDING holding state (which exists only
    to let an asynchronous provider's transient partial heal within the
    deadline). The finality signal flows from the provider's synchronous outcome
    through ``fulfillment_final`` to the synthesised verdict.
    """

    def setup_method(self):
        self.svc = _make_service()

    def _acquire_in_progress(self, requested_count=5):
        from datetime import datetime, timedelta, timezone

        from orb.domain.request.aggregate import Request
        from orb.domain.request.request_types import RequestStatus
        from orb.domain.request.value_objects import RequestType

        now = datetime.now(timezone.utc)
        return Request.create_new_request(
            RequestType.ACQUIRE, "tmpl-001", requested_count, "aws"
        ).model_copy(
            update={
                "status": RequestStatus.IN_PROGRESS,
                "started_at": now,
                # Comfortably within the grace period so a non-final partial
                # would park in PARTIAL_PENDING — isolating the finality signal
                # as the only thing that can force terminal PARTIAL.
                "deadline_at": now + timedelta(seconds=3600),
            }
        )

    def test_synchronous_shortfall_is_terminal_partial(self):
        from orb.domain.request.request_types import RequestStatus

        request = self._acquire_in_progress(requested_count=5)
        updated = self.svc._update_request_status(
            request=request,
            instance_count=3,
            requested_count=5,
            has_api_errors=False,
            provider_errors=[],
            fulfillment_final=True,
        )
        assert updated.status == RequestStatus.PARTIAL
        assert updated.status.is_terminal()

    def test_synchronous_shortfall_with_api_errors_is_terminal_partial(self):
        from orb.domain.request.request_types import RequestStatus

        request = self._acquire_in_progress(requested_count=5)
        updated = self.svc._update_request_status(
            request=request,
            instance_count=2,
            requested_count=5,
            has_api_errors=True,
            provider_errors=[{"error_code": "Throttling", "error_message": "Rate exceeded"}],
            fulfillment_final=True,
        )
        assert updated.status == RequestStatus.PARTIAL
        assert updated.status.is_terminal()

    def test_asynchronous_shortfall_stays_non_terminal(self):
        """An asynchronous provider shortfall (finality not signalled) must NOT
        terminalise at acquire time — it stays in progress so a later poll can
        promote it (to COMPLETED) or park it in PARTIAL_PENDING."""
        from orb.domain.request.request_types import RequestStatus

        request = self._acquire_in_progress(requested_count=5)
        updated = self.svc._update_request_status(
            request=request,
            instance_count=3,
            requested_count=5,
            has_api_errors=False,
            provider_errors=[],
            fulfillment_final=False,
        )
        assert updated.status == RequestStatus.IN_PROGRESS
        assert not updated.status.is_terminal()

    def test_apply_verdict_final_partial_is_terminal(self):
        """The finality signal threaded onto a synthesised partial verdict drives
        the state machine to terminal PARTIAL."""
        from orb.domain.request.request_types import RequestStatus

        request = self._acquire_in_progress(requested_count=5)
        updated = self.svc._apply_verdict(request, "partial", "3/5 instances", final=True)
        assert updated.status == RequestStatus.PARTIAL
        assert updated.status.is_terminal()

    def test_apply_verdict_nonfinal_partial_holds_in_partial_pending(self):
        """A synthesised partial verdict without the finality signal parks in the
        non-terminal PARTIAL_PENDING holding state while within the deadline."""
        from orb.domain.request.request_types import RequestStatus

        request = self._acquire_in_progress(requested_count=5)
        updated = self.svc._apply_verdict(request, "partial", "3/5 instances", final=False)
        assert updated.status == RequestStatus.PARTIAL_PENDING
        assert not updated.status.is_terminal()


class TestHandleProvisioningFailure:
    def setup_method(self):
        self.svc = _make_service()

    def test_sets_failed_status(self):
        req = _make_request()
        prov_result = MagicMock()
        prov_result.error_message = "Provider timeout"
        self.svc._handle_provisioning_failure(req, prov_result)
        req.update_status.assert_called_once()
        call_args = req.update_status.call_args[0]
        assert call_args[0] == RequestStatus.FAILED

    def test_stores_error_in_metadata(self):
        req = _make_request()
        prov_result = MagicMock()
        prov_result.error_message = "Provider timeout"
        self.svc._handle_provisioning_failure(req, prov_result)
        req.update_metadata.assert_called_once()
        call_kwargs = req.update_metadata.call_args[0][0]
        assert call_kwargs["error_message"] == "Provider timeout"
        assert call_kwargs["error_type"] == "ProvisioningFailure"

    def test_unknown_error_message_fallback(self):
        req = _make_request()
        prov_result = MagicMock()
        prov_result.error_message = None
        self.svc._handle_provisioning_failure(req, prov_result)
        req.update_metadata.assert_called_once()
        call_kwargs = req.update_metadata.call_args[0][0]
        assert call_kwargs["error_message"] == "Provisioning failed (no error details)"

    def test_capacity_failure_classified_not_internal(self):
        """A hard InsufficientInstanceCapacity failure must be categorised
        CAPACITY (not the blanket INTERNAL), and its summary must be the safe
        category template — never the raw provider error message."""
        from orb.domain.base.diagnostic import DiagnosticCategory

        prov_result = MagicMock()
        prov_result.error_message = "Insufficient capacity for m5.large in us-east-1a"
        prov_result.provider_error_code = "InsufficientInstanceCapacity"
        prov_result.provider_error_message = "We currently do not have sufficient m5.large capacity"
        prov_result.provider_data = {}

        diag = self.svc._classify_failure_diagnostic(prov_result, prov_result.error_message)

        assert diag.category == DiagnosticCategory.CAPACITY
        # Safe category template — no raw provider message leaked into summary.
        assert "capacity" in diag.summary.lower()
        assert prov_result.error_message not in diag.summary
        # Raw provider code preserved as detail for operator actionability.
        assert diag.detail == "InsufficientInstanceCapacity"

    def test_auth_failure_from_fleet_errors_classified(self):
        """An UnauthorizedOperation surfaced via fleet_errors is categorised
        AUTH, not INTERNAL."""
        from orb.domain.base.diagnostic import DiagnosticCategory

        prov_result = MagicMock()
        prov_result.error_message = "provisioning failed"
        prov_result.provider_error_code = None
        prov_result.provider_error_message = None
        prov_result.provider_data = {
            "fleet_errors": [
                {"error_code": "UnauthorizedOperation", "error_message": "not authorized"}
            ]
        }

        diag = self.svc._classify_failure_diagnostic(prov_result, prov_result.error_message)

        assert diag.category == DiagnosticCategory.AUTH

    def test_no_provider_signal_stays_internal(self):
        """A generic failure with no provider error signal keeps INTERNAL."""
        from orb.domain.base.diagnostic import DiagnosticCategory

        prov_result = MagicMock()
        prov_result.error_message = "Dispatch timed out"
        prov_result.provider_error_code = None
        prov_result.provider_error_message = None
        prov_result.provider_data = {}

        diag = self.svc._classify_failure_diagnostic(prov_result, prov_result.error_message)

        assert diag.category == DiagnosticCategory.INTERNAL
        assert diag.detail == "Dispatch timed out"


def _make_result(**kwargs) -> ProvisioningResult:
    defaults: dict = dict(
        success=True,
        resource_ids=[],
        machine_ids=[],
        instances=[],
        provider_data={},
        fulfilled_count=0,
        is_final=True,
    )
    defaults.update(kwargs)
    return ProvisioningResult(**defaults)


class TestExtractMachineIds:
    def setup_method(self):
        self.svc = _make_service()

    def test_extract_from_machine_ids_key(self):
        result = _make_result(machine_ids=["i-abc", "i-def"])
        ids = self.svc._extract_machine_ids(result)
        assert ids == ["i-abc", "i-def"]

    def test_extract_from_instances_list(self):
        result = _make_result(instances=[{"instance_id": "i-aaa"}, {"instance_id": "i-bbb"}])
        ids = self.svc._extract_machine_ids(result)
        assert ids == ["i-aaa", "i-bbb"]

    def test_extract_skips_instances_without_id(self):
        result = _make_result(instances=[{"instance_id": "i-aaa"}, {"other_key": "no-id"}])
        ids = self.svc._extract_machine_ids(result)
        assert ids == ["i-aaa"]

    def test_returns_empty_when_no_relevant_keys(self):
        result = _make_result()
        ids = self.svc._extract_machine_ids(result)
        assert ids == []

    def test_machine_ids_takes_precedence_over_instances(self):
        result = _make_result(
            machine_ids=["i-abc"],
            instances=[{"instance_id": "i-aaa"}],
        )
        ids = self.svc._extract_machine_ids(result)
        assert ids == ["i-abc"]


class TestCreateMachineAggregate:
    def setup_method(self):
        self.svc = _make_service()

    def test_creates_machine_with_basic_data(self):
        req = _make_request()
        instance_data = {
            "instance_id": "i-1234567890abcdef0",
            "instance_type": "t3.medium",
            "image_id": "ami-12345678",
        }
        machine = self.svc._create_machine_aggregate(instance_data, req, "tmpl-001")
        assert str(machine.machine_id) == "i-1234567890abcdef0"
        assert str(machine.instance_type) == "t3.medium"

    def test_creates_machine_with_string_launch_time(self):
        req = _make_request()
        instance_data = {
            "instance_id": "i-1234567890abcdef0",
            "instance_type": "t3.medium",
            "image_id": "ami-12345678",
            "launch_time": "2026-01-01T00:00:00",
        }
        machine = self.svc._create_machine_aggregate(instance_data, req, "tmpl-001")
        assert machine.launch_time is not None

    def test_creates_machine_with_invalid_launch_time(self):
        req = _make_request()
        instance_data = {
            "instance_id": "i-1234567890abcdef0",
            "instance_type": "t3.medium",
            "image_id": "ami-12345678",
            "launch_time": "not-a-date",
        }
        machine = self.svc._create_machine_aggregate(instance_data, req, "tmpl-001")
        assert machine.launch_time is None

    def test_default_instance_type_fallback(self):
        req = _make_request()
        instance_data = {
            "instance_id": "i-1234567890abcdef0",
            "image_id": "ami-12345678",
        }
        machine = self.svc._create_machine_aggregate(instance_data, req, "tmpl-001")
        assert str(machine.instance_type) == "t2.micro"


class TestProviderDiagnosticMergedOntoRequest:
    """The AWS handler classifies fleet errors into a FulfilmentDiagnostic and
    stashes it under provider_data['fulfilment_diagnostic']. The status service
    must lift that classified category onto request.fulfilment_diagnostic so the
    'why did it fall short' signal (auth/validation/throttle vs capacity)
    actually surfaces — not just a generic capacity shortfall."""

    def _real_request(self, requested_count=3):
        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        return Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tmpl-001",
            machine_count=requested_count,
            provider_type="aws",
            provider_name="aws-prod",
            provider_api="EC2Fleet",
        )

    def _classified_auth_diagnostic(self):
        from datetime import datetime, timezone

        from orb.providers.aws.infrastructure.handlers.shared.error_classifier import (
            classify_aws_errors,
        )

        errors = [
            {
                "error_code": "UnauthorizedOperation",
                "error_message": "not authorized to perform ec2:CreateFleet",
                "fleet_id": "fleet-abc",
            }
        ]
        return classify_aws_errors(errors, now=datetime.now(timezone.utc))

    @pytest.mark.asyncio
    async def test_unauthorized_fleet_error_surfaces_auth_category(self):
        from unittest.mock import MagicMock

        from orb.domain.base.diagnostic import DiagnosticCategory

        uow_factory = MagicMock()
        # Support the machines.save_batch context-manager path if hit.
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow_factory.create_unit_of_work.return_value = uow

        svc = RequestStatusManagementService(uow_factory=uow_factory, logger=MagicMock())

        request = self._real_request(requested_count=3)
        classified = self._classified_auth_diagnostic()

        result = ProvisioningResult(
            success=True,
            resource_ids=["fleet-abc"],
            machine_ids=[],
            instances=[],  # capacity shortfall — nothing came up
            provider_data={
                "fleet_errors": [{"error_code": "UnauthorizedOperation", "error_message": "no"}],
                "fulfilment_diagnostic": classified.model_dump(mode="json"),
            },
            fulfilled_count=0,
            is_final=True,
        )

        updated = await svc.update_request_from_provisioning(request, result)

        assert updated.fulfilment_diagnostic is not None
        # The classified AUTH category is surfaced — not merely a capacity blip.
        assert updated.fulfilment_diagnostic.category == DiagnosticCategory.AUTH

    @pytest.mark.asyncio
    async def test_no_diagnostic_when_provider_data_has_none(self):
        from unittest.mock import MagicMock

        uow_factory = MagicMock()
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow_factory.create_unit_of_work.return_value = uow

        svc = RequestStatusManagementService(uow_factory=uow_factory, logger=MagicMock())
        request = self._real_request(requested_count=1)

        result = ProvisioningResult(
            success=True,
            resource_ids=["fleet-x"],
            machine_ids=["i-1"],
            instances=[{"instance_id": "i-1"}],
            provider_data={"resource_type": "ec2_fleet"},
            fulfilled_count=1,
            is_final=True,
        )

        updated = await svc.update_request_from_provisioning(request, result)
        # No classified diagnostic present → aggregate diagnostic stays None.
        assert updated.fulfilment_diagnostic is None
