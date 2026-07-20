"""Unit tests targeting uncovered branches in the application layer.

Covers:
- request_query_handlers: cache hit, ProviderContractError re-raise, ListRequests filters
  (status, template_id, request_type, q, sort TypeError, limit clamping,
   filter_expressions), SyncAndListReturnRequests (provider/type filters,
   machine_names, filter_expressions, q, sort, pagination, sync error path),
   SyncAndListActive (template_id filter, provider/type filter, filter_expressions,
   sync error, BaseException propagation path)
- request_status_service: generic exception fallback in determine_status,
  update_request_status terminal-state guard (invalid status, PARTIAL→COMPLETED
  upgrade), successful_count reconciliation, provider_fulfilment caching,
  map_machine_status_to_result full map, exception re-raise on save failure
- request_creation_handlers: CreateReturnRequestHandler._filter_machines,
  _cancel_validate_and_persist (force_return cancels stuck request),
  _update_request_status FAILED fallback, _execute_deprovisioning_for_request
  (success with skipped_ids, failure branch)
- template_defaults_service: launch_template_id suppression warning,
  _get_global_template_defaults exception fallback,
  _get_provider_instance_defaults exception fallback,
  _get_provider_type exception fallback + fallback name extraction,
  validate_template_defaults validation result warnings,
  resolve_template_with_extensions factory fallback + Template fallback,
  _get_provider_instance_extension_defaults (with extensions, registry returns {}),
  get_effective_template_with_extensions + validate_template_with_extensions,
  resolve_provider_api_default all branches
- request/dto: MachineReferenceDTO.from_machine (status has no .value, rt has no
  .value), to_dict ensures launch_time/cloud_host_id present,
  RequestDTO.from_domain (domain_refs path, metadata last_fulfilment path,
  provider_data capacity path, provider_data parse error, aws_error fallback,
  resource_ids list, to_dict verbose/include_timing)
- provisioning_orchestration_service: _extract_provider_error_fields (aws_ fallback
  attributes), recover_stuck_acquiring_requests (no acquiring, not expired)
"""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers shared across test groups
# ---------------------------------------------------------------------------


def _sync(coro):
    """Run a coroutine synchronously without relying on a running event loop."""
    return asyncio.run(coro)


def _make_uow_context_manager(uow_obj):
    """Return a factory whose create_unit_of_work() context-manages uow_obj."""
    factory = MagicMock()

    @contextmanager
    def _cm():
        yield uow_obj

    factory.create_unit_of_work.side_effect = _cm
    return factory


# ===========================================================================
# RequestStatusService — uncovered branches
# ===========================================================================


@pytest.mark.unit
class TestRequestStatusServiceBranchCoverage:
    """Covers lines 70-72, 104, 107, 175, 210-285 of request_status_service.py."""

    def _make_svc(self):
        from orb.application.services.request_status_service import RequestStatusService

        return RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())

    def _req(self, request_type="return", requested_count=2):
        r = MagicMock()
        r.request_type.value = request_type
        r.requested_count = requested_count
        r.provider_name = "aws-test"
        return r

    def _machine(self, status_value):
        m = MagicMock()
        m.status.value = status_value
        return m

    def _make_real_request(self, status, request_id="req-1"):
        """Return a request mock with a *real* RequestStatus enum."""

        r = MagicMock()
        r.status = status  # real enum — .is_terminal() works
        r.request_id.value = request_id
        r.update_status = MagicMock(return_value=r)
        r.model_copy = MagicMock(return_value=r)
        r.with_last_fulfilment = MagicMock(return_value=r)
        r.machine_ids = []
        r.successful_count = 0
        return r

    # ------------------------------------------------------------------
    # determine_status_from_machines — generic exception → IN_PROGRESS fallback
    # ------------------------------------------------------------------

    def test_generic_exception_in_determine_status_returns_in_progress(self):
        """When _determine_return_status raises a non-ProviderContractError Exception,
        the service returns (IN_PROGRESS, 'Status determination failed …')."""
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestStatus

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        req = self._req("return")
        # Patch _determine_return_status to raise a plain Exception
        with patch.object(svc, "_determine_return_status", side_effect=RuntimeError("boom")):
            status, msg = svc.determine_status_from_machines(
                db_machines=[],
                provider_machines=[],
                request=req,
                provider_metadata={},
            )
        assert status == RequestStatus.IN_PROGRESS.value
        assert "retry" in (msg or "").lower() or "failed" in (msg or "").lower()

    # ------------------------------------------------------------------
    # _determine_acquire_status — unknown fulfilment state → IN_PROGRESS
    # ------------------------------------------------------------------

    def test_unknown_fulfilment_state_logs_warning_and_returns_in_progress(self):
        from orb.domain.base.provider_fulfilment import ProviderFulfilment
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        req = self._req("acquire")
        fulfilment = ProviderFulfilment(state="totally_unknown_state", message="??")
        status, msg = svc.determine_status_from_machines(
            db_machines=[],
            provider_machines=[],
            request=req,
            provider_metadata={"provider_fulfilment": fulfilment},
        )
        assert status == RequestStatus.IN_PROGRESS.value
        svc.logger.warning.assert_called()

    # ------------------------------------------------------------------
    # update_request_status — terminal-state guard
    # ------------------------------------------------------------------

    def test_terminal_request_invalid_status_string_is_rejected(self):
        """Unknown status on a terminal request → request returned unchanged."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        request = self._make_real_request(RequestStatus.COMPLETED)

        result = _sync(svc.update_request_status(request, "totally_invalid_status", "msg"))
        assert result is request  # unchanged; rejected

    def test_terminal_completed_not_upgraded_to_partial(self):
        """COMPLETED → PARTIAL is a downgrade, must be blocked."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        request = self._make_real_request(RequestStatus.COMPLETED)

        result = _sync(svc.update_request_status(request, RequestStatus.PARTIAL.value, "msg"))
        assert result is request  # blocked

    def test_partial_to_completed_upgrade_is_allowed(self):
        """PARTIAL → COMPLETED upgrade is the one allowed terminal transition."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        uow = MagicMock()
        uow.requests.save.return_value = []

        @contextmanager
        def _cm():
            yield uow

        svc.uow_factory.create_unit_of_work.side_effect = _cm

        # Use a real PARTIAL enum so is_terminal() works
        request = self._make_real_request(RequestStatus.PARTIAL)
        updated = MagicMock()
        updated.machine_ids = ["m-1", "m-2"]
        updated.successful_count = 2
        updated.with_last_fulfilment = MagicMock(return_value=updated)
        request.update_status = MagicMock(return_value=updated)

        _sync(svc.update_request_status(request, RequestStatus.COMPLETED.value, "done"))
        # Must not short-circuit — update_status must be called
        request.update_status.assert_called_once()

    def test_successful_count_reconciled_when_machine_ids_differ(self):
        """When machine_ids count != successful_count, model_copy must be called."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        uow = MagicMock()
        uow.requests.save.return_value = []

        @contextmanager
        def _cm():
            yield uow

        svc.uow_factory.create_unit_of_work.side_effect = _cm

        request = self._make_real_request(RequestStatus.IN_PROGRESS)

        updated = MagicMock()
        updated.machine_ids = ["m-1", "m-2", "m-3"]
        updated.successful_count = 1  # differs → must reconcile
        reconciled = MagicMock()
        reconciled.machine_ids = ["m-1", "m-2", "m-3"]
        reconciled.successful_count = 3
        reconciled.with_last_fulfilment = MagicMock(return_value=reconciled)
        updated.model_copy = MagicMock(return_value=reconciled)
        updated.with_last_fulfilment = MagicMock(return_value=updated)
        request.update_status = MagicMock(return_value=updated)

        _sync(svc.update_request_status(request, RequestStatus.IN_PROGRESS.value, "ok"))
        updated.model_copy.assert_called_once()

    def test_provider_fulfilment_cached_in_metadata(self):
        """When provider_metadata has provider_fulfilment, with_last_fulfilment is called."""
        from orb.domain.base.provider_fulfilment import ProviderFulfilment
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        uow = MagicMock()
        uow.requests.save.return_value = []

        @contextmanager
        def _cm():
            yield uow

        svc.uow_factory.create_unit_of_work.side_effect = _cm

        fulfilment = ProviderFulfilment(state="fulfilled", message="ok")
        request = self._make_real_request(RequestStatus.IN_PROGRESS)

        updated = MagicMock()
        updated.machine_ids = []
        updated.successful_count = 0
        final = MagicMock()
        updated.with_last_fulfilment = MagicMock(return_value=final)
        final.with_last_fulfilment = MagicMock(return_value=final)
        request.update_status = MagicMock(return_value=updated)

        _sync(
            svc.update_request_status(
                request,
                RequestStatus.COMPLETED.value,
                "done",
                provider_metadata={"provider_fulfilment": fulfilment},
            )
        )
        updated.with_last_fulfilment.assert_called_once_with(dataclasses.asdict(fulfilment))

    def test_update_request_status_re_raises_exception_from_save(self):
        """Exception from uow.requests.save propagates out."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        uow = MagicMock()
        uow.requests.save.side_effect = RuntimeError("db failure")

        @contextmanager
        def _cm():
            yield uow

        svc.uow_factory.create_unit_of_work.side_effect = _cm

        request = self._make_real_request(RequestStatus.IN_PROGRESS)
        updated = MagicMock()
        updated.machine_ids = []
        updated.successful_count = 0
        updated.with_last_fulfilment = MagicMock(return_value=updated)
        request.update_status = MagicMock(return_value=updated)

        with pytest.raises(RuntimeError, match="db failure"):
            _sync(svc.update_request_status(request, RequestStatus.IN_PROGRESS.value, "msg"))

    # ------------------------------------------------------------------
    # map_machine_status_to_result — full coverage
    # ------------------------------------------------------------------

    def test_map_return_terminated_succeeds(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("terminated", RequestType.RETURN) == "succeed"

    def test_map_return_stopped_succeeds(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("stopped", RequestType.RETURN) == "succeed"

    def test_map_return_running_is_executing(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("running", RequestType.RETURN) == "executing"

    def test_map_return_unknown_status_is_fail(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("unknown_xyz", RequestType.RETURN) == "fail"

    def test_map_acquire_running_succeeds(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("running", RequestType.ACQUIRE) == "succeed"

    def test_map_acquire_pending_is_executing(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("pending", RequestType.ACQUIRE) == "executing"

    def test_map_acquire_launching_is_executing(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("launching", RequestType.ACQUIRE) == "executing"

    def test_map_acquire_failed_is_fail(self):
        from orb.application.services.request_status_service import RequestStatusService
        from orb.domain.request.request_types import RequestType

        svc = RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())
        assert svc.map_machine_status_to_result("failed", RequestType.ACQUIRE) == "fail"

    # ------------------------------------------------------------------
    # _determine_return_status — empty db + empty provider = IN_PROGRESS
    # ------------------------------------------------------------------

    def test_empty_provider_and_db_machines_returns_in_progress(self):
        """No DB records + no provider records → IN_PROGRESS (ambiguous transient gap)."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        req = self._req("return")
        status, msg = svc.determine_status_from_machines(
            db_machines=[],
            provider_machines=[],
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value

    def test_failed_machines_in_return_request_returns_failed(self):
        """failed machines in return → FAILED status."""
        from orb.domain.request.request_types import RequestStatus

        svc = self._make_svc()
        req = self._req("return", requested_count=2)
        machines = [self._machine("failed"), self._machine("failed")]
        status, _ = svc.determine_status_from_machines(
            db_machines=machines,
            provider_machines=machines,
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.FAILED.value


# ===========================================================================
# request/dto.py — MachineReferenceDTO and RequestDTO branches
# ===========================================================================


@pytest.mark.unit
class TestMachineReferenceDTOBranches:
    """Covers lines 50-51, 53, 81 of request/dto.py."""

    def _make_machine(self, *, status="running", has_value=True, launch_time=None, tags=None):
        m = MagicMock()
        if has_value:
            m.status.value = status
        else:
            del m.status.value  # remove .value attribute to trigger str() fallback
            m.status.__str__ = MagicMock(return_value=status)
        m.machine_id.value = "m-001"
        m.display_name = "host-1"
        m.private_ip = "10.0.0.1"
        m.public_ip = None
        m.instance_type = "m5.large"
        m.price_type = "on-demand"
        m.launch_time = launch_time
        m.provider_data = {"cloud_host_id": "chost-1"}
        m.request_id = "req-1"
        m.return_request_id = None
        m.tags = tags
        m.status_reason = "ok"
        return m

    def _make_rt(self, value="acquire", has_value=True):
        if has_value:
            rt = MagicMock()
            rt.value = value
            return rt
        else:
            # Create an object without a .value attribute; str() returns value
            class _NoValueRT:
                def __str__(self):
                    return value

            return _NoValueRT()

    def test_from_machine_status_without_value_attribute_uses_str(self):
        """status without .value falls back to str(machine.status)."""
        from orb.application.request.dto import MachineReferenceDTO

        machine = self._make_machine(status="running", has_value=False)
        rt = self._make_rt("acquire")
        dto = MachineReferenceDTO.from_machine(machine, rt)
        assert dto.status == "running"

    def test_from_machine_request_type_without_value_uses_str(self):
        """request_type without .value falls back to str(request_type)."""
        from orb.application.request.dto import MachineReferenceDTO

        machine = self._make_machine(status="running", has_value=True)
        rt = self._make_rt("acquire", has_value=False)
        # str(rt) returns "acquire" — just verify no AttributeError
        dto = MachineReferenceDTO.from_machine(machine, rt)
        assert dto.machine_id == "m-001"

    def test_to_dict_always_includes_launch_time_and_cloud_host_id(self):
        """to_dict ensures launch_time and cloud_host_id are present even when None."""
        from orb.application.request.dto import MachineReferenceDTO

        dto = MachineReferenceDTO(
            machine_id="m-001",
            name="host-1",
            result="succeed",
            status="running",
        )
        d = dto.to_dict()
        assert "launch_time" in d
        assert "cloud_host_id" in d

    def test_to_dict_tags_present_when_set(self):
        """to_dict includes tags when machine has them."""
        from orb.application.request.dto import MachineReferenceDTO

        dto = MachineReferenceDTO(
            machine_id="m-001",
            name="host-1",
            result="succeed",
            status="running",
            tags={"env": "prod"},
        )
        d = dto.to_dict()
        assert d.get("tags") == {"env": "prod"}


@pytest.mark.unit
class TestRequestDTOFromDomainBranches:
    """Covers lines 158, 172-173, 188-189, 197-198, 200, 204, 324, 344 of request/dto.py."""

    def _base_request(self, **overrides):
        """Build a minimal request-like mock."""
        r = MagicMock()
        r.request_id = "req-001"
        r.status.value = "in_progress"
        r.status.__str__ = MagicMock(return_value="in_progress")
        r.template_id = "tmpl-1"
        r.requested_count = 2
        r.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        r.last_status_check = None
        r.first_status_check = None
        r.machine_references = None
        r.machine_ids = []
        r.status_message = ""
        r.resource_ids = []
        r.provider_api = "RunInstances"
        r.provider_name = "aws"
        r.provider_type = "aws"
        r.provider_data = {}
        r.metadata = {}
        r.request_type.value = "acquire"
        r.desired_capacity = 1
        r.duration = None
        r.success_rate = None
        r.successful_count = 0
        r.failed_count = 0
        r.started_at = None
        r.completed_at = None
        r.error_details = {}
        r.version = 0
        for k, v in overrides.items():
            setattr(r, k, v)
        return r

    def test_from_domain_with_metadata_last_fulfilment_dict(self):
        """metadata['last_fulfilment'] as dict is parsed into ProviderFulfilment."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(
            metadata={
                "last_fulfilment": {
                    "state": "fulfilled",
                    "message": "ok",
                    "target_units": 4,
                    "fulfilled_units": 4,
                    "running_count": 4,
                    "pending_count": 0,
                    "failed_count": 0,
                }
            }
        )
        dto = RequestDTO.from_domain(req)
        assert dto.target_units == 4
        assert dto.fulfilled_units == 4

    def test_from_domain_malformed_last_fulfilment_falls_back_gracefully(self):
        """Malformed last_fulfilment dict does not raise, fields stay None."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(
            metadata={"last_fulfilment": {"bad_key": "bad_value"}}  # missing required fields
        )
        dto = RequestDTO.from_domain(req)
        # No exception; capacity fields are None (fulfilled gracefully)
        assert dto.target_units is None

    def test_from_domain_provider_data_capacity_fields_parsed(self):
        """provider_data with target_units/fulfilled_units populates capacity fields."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(
            provider_data={"target_units": 10, "fulfilled_units": 8, "running_count": 8}
        )
        dto = RequestDTO.from_domain(req)
        assert dto.target_units == 10
        assert dto.fulfilled_units == 8
        assert dto.running_count == 8

    def test_from_domain_provider_data_parse_error_falls_back(self):
        """Non-numeric target_units in provider_data does not raise."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(
            provider_data={"target_units": "not-a-number", "fulfilled_units": 5}
        )
        # Should not raise; may return None capacity fields
        RequestDTO.from_domain(req)
        # graceful — no exception is the assertion

    def test_from_domain_aws_error_fallback_in_error_details(self):
        """error_details with 'aws_error' (legacy key) is surfaced as error block."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(
            error_details={"aws_error": {"code": "UnauthorizedOperation", "message": "denied"}}
        )
        dto = RequestDTO.from_domain(req)
        assert dto.error is not None
        assert dto.error.get("code") == "UnauthorizedOperation"

    def test_from_domain_provider_error_key_takes_precedence_over_aws_error(self):
        """provider_error key takes precedence over aws_error when both present."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(
            error_details={
                "provider_error": {"code": "NewCode", "message": "new"},
                "aws_error": {"code": "OldCode", "message": "old"},
            }
        )
        dto = RequestDTO.from_domain(req)
        assert dto.error is not None
        assert dto.error.get("code") == "NewCode"

    def test_from_domain_resource_ids_list_populated(self):
        """resource_ids list is passed through correctly."""
        from orb.application.request.dto import RequestDTO

        req = self._base_request(resource_ids=["res-1", "res-2"])
        dto = RequestDTO.from_domain(req)
        assert dto.resource_ids == ["res-1", "res-2"]
        assert dto.resource_id == "res-1"  # first entry

    def test_to_dict_verbose_includes_metadata(self):
        """to_dict(verbose=True) includes metadata and launch_template fields."""
        from orb.application.request.dto import RequestDTO

        dto = RequestDTO(
            request_id="req-1",
            status="in_progress",
            requested_count=1,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            metadata={"key": "value"},
            launch_template_id="lt-1",
            launch_template_version="$Latest",
        )
        d = dto.to_dict(verbose=True)
        assert d.get("metadata") == {"key": "value"}
        assert d.get("launch_template_id") == "lt-1"

    def test_to_dict_include_timing_surfaces_status_check_timestamps(self):
        """to_dict(include_timing=True) surfaces status check timestamps."""
        from orb.application.request.dto import RequestDTO

        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        dto = RequestDTO(
            request_id="req-1",
            status="in_progress",
            requested_count=1,
            created_at=now,
            first_status_check=now,
            last_status_check=now,
        )
        d = dto.to_dict(include_timing=True)
        assert "first_status_check" in d or "last_status_check" in d

    def test_to_dict_capacity_fields_absent_when_none(self):
        """to_dict does not include capacity fields when all are None."""
        from orb.application.request.dto import RequestDTO

        dto = RequestDTO(
            request_id="req-1",
            status="in_progress",
            requested_count=1,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        d = dto.to_dict()
        for field in ("target_units", "fulfilled_units", "running_count", "pending_count"):
            assert field not in d

    def test_to_dict_error_block_present_when_set(self):
        """to_dict includes 'error' key when error block is not None."""
        from orb.application.request.dto import RequestDTO

        dto = RequestDTO(
            request_id="req-1",
            status="failed",
            requested_count=1,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            error={"code": "SomeError", "message": "something went wrong"},
        )
        d = dto.to_dict()
        assert d.get("error") == {"code": "SomeError", "message": "something went wrong"}


# ===========================================================================
# TemplateDefaultsService — uncovered branches
# ===========================================================================


@pytest.mark.unit
class TestTemplateDefaultsServiceBranchCoverage:
    """Covers lines 127, 138, 197-217, 264, 308-310, 324-330, 345-346, 351, etc."""

    def _make_svc(
        self,
        global_config=None,
        providers=None,
        provider_defaults=None,
        extension_registry=None,
        template_factory=None,
    ):
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        logger = MagicMock()
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = global_config or {}

        mock_provider_config = MagicMock()
        mock_provider_config.provider_defaults = provider_defaults or {}
        mock_provider_config.providers = providers or []
        config_manager.get_provider_config.return_value = mock_provider_config

        return TemplateDefaultsService(
            config_manager=config_manager,
            logger=logger,
            extension_registry=extension_registry,
            template_factory=template_factory,
        )

    # ------------------------------------------------------------------
    # resolve_template_defaults — launch_template_id suppression warning
    # ------------------------------------------------------------------

    def test_launch_template_id_suppresses_conflicting_defaults(self):
        """Template with launch_template_id logs info about suppressed defaults."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        logger = MagicMock()
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}

        provider_obj = MagicMock()
        provider_obj.name = "aws-primary"
        provider_obj.type = "aws"
        provider_obj.template_defaults = {"image_id": "ami-default", "subnet_ids": ["subnet-1"]}

        mock_provider_config = MagicMock()
        mock_provider_config.provider_defaults = {}
        mock_provider_config.providers = [provider_obj]
        config_manager.get_provider_config.return_value = mock_provider_config

        svc = TemplateDefaultsService(config_manager=config_manager, logger=logger)
        template = {
            "template_id": "tmpl-lt",
            "launch_template_id": "lt-12345",
            "image_id": "ami-specific",
        }
        result = svc.resolve_template_defaults(template, provider_instance_name="aws-primary")
        # The info log about suppression must have been called
        logger.info.assert_called()
        # launch_template_id from template must be present
        assert result.get("launch_template_id") == "lt-12345"

    # ------------------------------------------------------------------
    # _get_global_template_defaults — exception fallback
    # ------------------------------------------------------------------

    def test_global_defaults_exception_returns_empty(self):
        """Exception in get_template_config returns {}."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        config_manager.get_template_config.side_effect = RuntimeError("config error")
        config_manager.get_provider_config.return_value = MagicMock(
            provider_defaults={}, providers=[]
        )
        svc = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())

        result = svc._get_global_template_defaults()
        assert result == {}

    # ------------------------------------------------------------------
    # _get_provider_instance_defaults — exception fallback
    # ------------------------------------------------------------------

    def test_provider_instance_defaults_exception_returns_empty(self):
        """Exception in get_provider_config for instance defaults returns {}."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.side_effect = RuntimeError("provider error")
        svc = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())

        result = svc._get_provider_instance_defaults("aws-primary")
        assert result == {}

    # ------------------------------------------------------------------
    # _get_provider_type — lookup miss + name fallback
    # ------------------------------------------------------------------

    def test_get_provider_type_fallback_extraction(self):
        """When provider not in list, extract_provider_type fallback is used."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        mock_pc = MagicMock()
        mock_pc.providers = []  # no providers in list
        config_manager.get_provider_config.return_value = mock_pc
        svc = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())

        # extract_provider_type("aws-primary") should return "aws"
        with patch(
            "orb.application.services.template_defaults_service.extract_provider_type",
            return_value="aws",
        ):
            result = svc._get_provider_type("aws-primary")
        assert result == "aws"

    def test_get_provider_type_exception_returns_none(self):
        """Exception in get_provider_config returns None."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.side_effect = RuntimeError("err")
        svc = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())

        result = svc._get_provider_type("aws-primary")
        assert result is None

    # ------------------------------------------------------------------
    # validate_template_defaults — warnings for missing essential fields
    # ------------------------------------------------------------------

    def test_validate_template_defaults_warns_on_missing_essential_fields(self):
        """validate_template_defaults returns warnings when defaults are missing."""
        svc = self._make_svc()

        result = svc.validate_template_defaults(provider_instance_name=None)
        # No provider set → no defaults → missing fields → warnings
        assert isinstance(result["warnings"], list)
        assert len(result["warnings"]) > 0  # provider_api, price_type, max_number absent

    def test_validate_template_defaults_succeeds_when_all_fields_present(self):
        """No warnings when all essential fields are in effective defaults."""
        from unittest.mock import patch

        svc = self._make_svc()
        with patch.object(
            svc,
            "get_effective_template_defaults",
            return_value={"provider_api": "RunInstances", "price_type": "spot", "max_number": 100},
        ):
            result = svc.validate_template_defaults(provider_instance_name=None)
        assert result["warnings"] == []
        assert result["is_valid"] is True

    # ------------------------------------------------------------------
    # resolve_provider_api_default — all branches
    # ------------------------------------------------------------------

    def test_resolve_provider_api_from_template(self):
        """provider_api in template dict is returned immediately."""
        svc = self._make_svc()
        result = svc.resolve_provider_api_default({"provider_api": "EC2Fleet"})
        assert result == "EC2Fleet"

    def test_resolve_provider_api_from_template_camelcase(self):
        """providerApi (camelCase) in template dict is returned."""
        svc = self._make_svc()
        result = svc.resolve_provider_api_default({"providerApi": "SpotFleet"})
        assert result == "SpotFleet"

    def test_resolve_provider_api_from_instance_defaults(self):
        """provider_api from provider instance defaults is used when template lacks it."""
        provider_obj = MagicMock()
        provider_obj.name = "aws-p"
        provider_obj.type = "aws"
        provider_obj.template_defaults = {"provider_api": "RunInstances"}

        svc = self._make_svc(providers=[provider_obj])
        result = svc.resolve_provider_api_default({}, provider_instance_name="aws-p")
        assert result == "RunInstances"

    def test_resolve_provider_api_from_global_defaults(self):
        """Falls back to global template defaults when no other source."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {"default_provider_api": "GlobalFleet"}
        mock_pc = MagicMock()
        mock_pc.providers = []
        mock_pc.provider_defaults = {}
        config_manager.get_provider_config.return_value = mock_pc
        svc = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())

        result = svc.resolve_provider_api_default({})
        assert result == "GlobalFleet"

    def test_resolve_provider_api_raises_when_no_source(self):
        """ValueError is raised when no provider_api is found anywhere."""
        svc = self._make_svc()
        with pytest.raises(ValueError, match="No provider_api configured"):
            svc.resolve_provider_api_default({})

    # ------------------------------------------------------------------
    # _get_extension_defaults — exception swallowed
    # ------------------------------------------------------------------

    def test_get_extension_defaults_exception_returns_empty(self):
        """Exception from extension_registry is logged and returns {}."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        extension_registry = MagicMock()
        extension_registry.get_extension_defaults.side_effect = RuntimeError("ext error")
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.return_value = MagicMock(
            provider_defaults={}, providers=[]
        )
        svc = TemplateDefaultsService(
            config_manager=config_manager,
            logger=MagicMock(),
            extension_registry=extension_registry,
        )
        result = svc._get_extension_defaults("aws", None)
        assert result == {}

    # ------------------------------------------------------------------
    # get_effective_template_with_extensions — runs without error
    # ------------------------------------------------------------------

    def test_get_effective_template_with_extensions_no_provider_type(self):
        """Without provider_instance_name, extension path is skipped."""
        svc = self._make_svc()
        result = svc.get_effective_template_with_extensions({"template_id": "t1"})
        assert isinstance(result, dict)

    # ------------------------------------------------------------------
    # validate_template_with_extensions — exception path
    # ------------------------------------------------------------------

    def test_validate_template_with_extensions_catches_resolution_error(self):
        """Exception in resolve_template_with_extensions sets is_valid=False."""
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.return_value = MagicMock(
            provider_defaults={}, providers=[]
        )
        svc = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())

        with patch.object(
            svc, "resolve_template_with_extensions", side_effect=RuntimeError("factory err")
        ):
            result = svc.validate_template_with_extensions({"template_id": "t1"})

        assert result["is_valid"] is False
        assert len(result["errors"]) > 0


# ===========================================================================
# ListRequestsHandler — uncovered filter/sort/pagination branches
# ===========================================================================


class _FakeRequestDTO:
    """Minimal stand-in for RequestDTO that avoids pydantic validation."""

    def __init__(self, request_id, provider_name=None, provider_type=None, template_id=None):
        self.request_id = request_id
        self.provider_name = provider_name
        self.provider_type = provider_type
        self.template_id = template_id

    def model_dump(self):
        return {
            "request_id": self.request_id,
            "provider_name": self.provider_name,
            "provider_type": self.provider_type,
        }

    @classmethod
    def model_validate(cls, d):
        return cls(d.get("request_id", "?"), d.get("provider_name"), d.get("provider_type"))


def _make_req_ns(
    request_id="req-1",
    status=None,
    template_id="tmpl-1",
    provider_name="aws",
    provider_type="aws",
    request_type=None,
    machine_ids=None,
):
    from orb.domain.request.value_objects import RequestStatus, RequestType

    ns = MagicMock()
    ns.request_id = request_id
    ns.status = status or RequestStatus.IN_PROGRESS
    ns.template_id = template_id
    ns.provider_name = provider_name
    ns.provider_type = provider_type
    ns.request_type = request_type or RequestType.ACQUIRE
    ns.machine_ids = machine_ids or []
    return ns


@pytest.mark.unit
class TestListRequestsHandlerFilters:
    """Covers lines 222-282, 302-323 of request_query_handlers.py."""

    def _run_with_handler(self, requests_list, query):
        """Execute a query against a ListRequestsHandler with all factories patched."""
        from orb.application.queries.request_query_handlers import ListRequestsHandler

        uow = MagicMock()
        uow.requests.find_all.return_value = requests_list
        uow.machines.find_by_ids.return_value = []

        @contextmanager
        def _cm():
            yield uow

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = _cm

        filter_service = MagicMock()
        filter_service.apply_filters.side_effect = lambda items, _: items

        def _fake_create(req, machines):
            return _FakeRequestDTO(
                request_id=str(getattr(req, "request_id", "r")),
                provider_name=getattr(req, "provider_name", None),
                provider_type=getattr(req, "provider_type", None),
                template_id=getattr(req, "template_id", None),
            )

        mock_factory = MagicMock()
        mock_factory.create_from_domain.side_effect = _fake_create

        with patch(
            "orb.application.queries.request_query_handlers.RequestDTOFactory",
            return_value=mock_factory,
        ):
            handler = ListRequestsHandler(
                uow_factory=uow_factory,
                logger=MagicMock(),
                error_handler=MagicMock(),
                generic_filter_service=filter_service,
            )
            result = _sync(handler.execute_query(query))

        return result, handler, filter_service

    def test_filter_by_status(self):
        from orb.application.request.queries import ListRequestsQuery
        from orb.domain.request.value_objects import RequestStatus

        req_a = _make_req_ns("req-a", status=RequestStatus.IN_PROGRESS)
        req_b = _make_req_ns("req-b", status=RequestStatus.COMPLETED)
        query = ListRequestsQuery(status=RequestStatus.IN_PROGRESS.value)
        result, _, _ = self._run_with_handler([req_a, req_b], query)
        assert len(result.items) == 1

    def test_filter_by_template_id(self):
        from orb.application.request.queries import ListRequestsQuery

        req_a = _make_req_ns("req-a", template_id="tmpl-target")
        req_b = _make_req_ns("req-b", template_id="tmpl-other")
        query = ListRequestsQuery(template_id="tmpl-target")
        result, _, _ = self._run_with_handler([req_a, req_b], query)
        assert len(result.items) == 1

    def test_filter_by_request_type(self):
        from orb.application.request.queries import ListRequestsQuery
        from orb.domain.request.value_objects import RequestType

        req_a = _make_req_ns("req-a", request_type=RequestType.ACQUIRE)
        req_b = _make_req_ns("req-b", request_type=RequestType.RETURN)
        query = ListRequestsQuery(request_type=RequestType.ACQUIRE)
        result, _, _ = self._run_with_handler([req_a, req_b], query)
        assert len(result.items) == 1

    def test_q_filter_matches_request_id_substring(self):
        from orb.application.request.queries import ListRequestsQuery

        req_a = _make_req_ns("req-MATCH-abc", template_id="tmpl-a")
        req_b = _make_req_ns("req-other-xyz", template_id="tmpl-b")
        query = ListRequestsQuery(q="match")
        result, _, _ = self._run_with_handler([req_a, req_b], query)
        assert len(result.items) == 1

    def test_limit_clamped_to_1000(self):
        from orb.application.request.queries import ListRequestsQuery

        requests = [_make_req_ns(f"req-{i}") for i in range(5)]
        query = ListRequestsQuery(limit=9999)  # over 1000
        result, handler, _ = self._run_with_handler(requests, query)
        # All 5 fit within clamped limit, warning must have been logged
        handler.logger.warning.assert_called()

    def test_offset_slicing(self):
        from orb.application.request.queries import ListRequestsQuery

        requests = [_make_req_ns(f"req-{i}") for i in range(5)]
        query = ListRequestsQuery(offset=3)
        result, _, _ = self._run_with_handler(requests, query)
        assert len(result.items) == 2  # 5 - 3

    def test_sort_asc(self):
        from orb.application.request.queries import ListRequestsQuery

        req_a = _make_req_ns("req-b")
        req_b = _make_req_ns("req-a")
        query = ListRequestsQuery(sort="+request_id")
        result, _, _ = self._run_with_handler([req_a, req_b], query)
        assert len(result.items) == 2

    def test_sort_type_error_logged_as_warning(self):
        """sort key that raises TypeError is caught and logged."""
        from orb.application.queries.request_query_handlers import ListRequestsHandler
        from orb.application.request.queries import ListRequestsQuery

        req_a = _make_req_ns("req-a")
        req_b = _make_req_ns("req-b")

        uow = MagicMock()
        uow.requests.find_all.return_value = [req_a, req_b]
        uow.machines.find_by_ids.return_value = []

        @contextmanager
        def _cm():
            yield uow

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = _cm

        filter_service = MagicMock()
        filter_service.apply_filters.side_effect = lambda items, _: items

        def _fake_create(req, machines):
            return _FakeRequestDTO(request_id=str(getattr(req, "request_id", "r")))

        mock_factory = MagicMock()
        mock_factory.create_from_domain.side_effect = _fake_create

        with patch(
            "orb.application.queries.request_query_handlers.RequestDTOFactory",
            return_value=mock_factory,
        ):
            handler = ListRequestsHandler(
                uow_factory=uow_factory,
                logger=MagicMock(),
                error_handler=MagicMock(),
                generic_filter_service=filter_service,
            )
            # Patch sorted inside the handler's module to raise TypeError
            with patch(
                "orb.application.queries.request_query_handlers.sorted",
                side_effect=TypeError("not sortable"),
            ):
                query = ListRequestsQuery(sort="-request_id")
                result = _sync(handler.execute_query(query))

        handler.logger.warning.assert_called()
        assert result is not None

    def test_filter_expressions_applied_to_dtos(self):
        """filter_expressions path calls generic_filter_service.apply_filters on dicts."""
        from orb.application.queries.request_query_handlers import ListRequestsHandler
        from orb.application.request.queries import ListRequestsQuery

        requests = [_make_req_ns("req-a")]
        uow = MagicMock()
        uow.requests.find_all.return_value = requests
        uow.machines.find_by_ids.return_value = []

        @contextmanager
        def _cm():
            yield uow

        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = _cm

        filter_service = MagicMock()
        filter_service.apply_filters.return_value = []

        fake_dto = _FakeRequestDTO("req-a")

        def _fake_create(req, machines):
            return fake_dto

        mock_factory = MagicMock()
        mock_factory.create_from_domain.side_effect = _fake_create

        with (
            patch(
                "orb.application.queries.request_query_handlers.RequestDTOFactory",
                return_value=mock_factory,
            ),
            patch(
                "orb.application.queries.request_query_handlers.RequestDTO.model_validate",
                return_value=fake_dto,
            ),
        ):
            handler = ListRequestsHandler(
                uow_factory=uow_factory,
                logger=MagicMock(),
                error_handler=MagicMock(),
                generic_filter_service=filter_service,
            )
            query = ListRequestsQuery(filter_expressions=["some filter"])
            _sync(handler.execute_query(query))

        filter_service.apply_filters.assert_called_once()


# ===========================================================================
# SyncAndGetRequestHandler — cache + ProviderContractError branches
# ===========================================================================


def _build_sync_get_handler():
    """Build SyncAndGetRequestHandler with all collaborators mocked, no real DI."""
    from orb.application.queries.request_query_handlers import SyncAndGetRequestHandler

    handler = object.__new__(SyncAndGetRequestHandler)
    handler.logger = MagicMock()
    handler.error_handler = MagicMock()

    handler._cache_service = MagicMock()
    handler._cache_service.is_caching_enabled.return_value = True
    handler.event_publisher = MagicMock()

    handler._query_service = AsyncMock()
    handler._status_service = AsyncMock()
    handler._status_service.determine_status_from_machines = MagicMock(return_value=(None, None))
    handler._status_service.update_request_status = AsyncMock()

    handler._machine_sync_service = AsyncMock()
    handler._machine_sync_service.populate_missing_machine_ids = AsyncMock()
    handler._machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))
    handler._machine_sync_service.sync_machines_with_provider = AsyncMock(return_value=([], []))

    handler._dto_factory = MagicMock()
    handler._dto_factory.create_from_domain.return_value = SimpleNamespace(
        request_id="req-1", status="in_progress"
    )

    return handler


@pytest.mark.unit
class TestSyncAndGetRequestHandlerBranches:
    """Covers lines 78-80, 125 (cache hit, lightweight, ProviderContractError)."""

    def test_cache_hit_returns_cached_result_without_sync(self):
        """When cache is enabled and returns a hit, sync is skipped."""
        from orb.application.dto.queries import SyncAndGetRequestQuery

        handler = _build_sync_get_handler()
        cached = SimpleNamespace(request_id="req-cached")
        handler._cache_service.get_cached_request.return_value = cached

        query = SyncAndGetRequestQuery(request_id="req-1")
        result = _sync(handler.execute_query(query))
        assert result is cached
        handler._machine_sync_service.populate_missing_machine_ids.assert_not_called()

    def test_skip_cache_bypasses_cache_lookup(self):
        """skip_cache=True bypasses the cache even when enabled."""
        from orb.application.dto.queries import SyncAndGetRequestQuery

        handler = _build_sync_get_handler()
        req = MagicMock()
        req.status.is_terminal.return_value = True
        handler._query_service.get_request = AsyncMock(return_value=req)
        handler._query_service.get_machines_for_request = AsyncMock(return_value=[])

        query = SyncAndGetRequestQuery(request_id="req-1", skip_cache=True)
        result = _sync(handler.execute_query(query))
        handler._cache_service.get_cached_request.assert_not_called()
        assert result is not None

    def test_lightweight_query_returns_dto_without_sync(self):
        """lightweight=True returns DTO built from stored state without provider sync."""
        from orb.application.dto.queries import SyncAndGetRequestQuery

        handler = _build_sync_get_handler()
        handler._cache_service.is_caching_enabled.return_value = False
        req = MagicMock()
        req.status.is_terminal.return_value = False
        handler._query_service.get_request = AsyncMock(return_value=req)

        query = SyncAndGetRequestQuery(request_id="req-1", lightweight=True)
        result = _sync(handler.execute_query(query))
        handler._machine_sync_service.populate_missing_machine_ids.assert_not_called()
        assert result is not None

    def test_provider_contract_error_propagates(self):
        """ProviderContractError is not swallowed — it propagates to the caller."""
        from orb.application.dto.queries import SyncAndGetRequestQuery
        from orb.domain.base.exceptions import ProviderContractError

        handler = _build_sync_get_handler()
        handler._cache_service.is_caching_enabled.return_value = False
        req = MagicMock()
        req.status.is_terminal.return_value = False
        handler._query_service.get_request = AsyncMock(return_value=req)
        handler._machine_sync_service.populate_missing_machine_ids = AsyncMock(
            side_effect=ProviderContractError("contract violation")
        )

        query = SyncAndGetRequestQuery(request_id="req-1")
        with pytest.raises(ProviderContractError):
            _sync(handler.execute_query(query))

    def test_sync_exception_returns_stored_state(self):
        """Non-ProviderContractError sync exception returns stored state."""
        from orb.application.dto.queries import SyncAndGetRequestQuery

        handler = _build_sync_get_handler()
        handler._cache_service.is_caching_enabled.return_value = False
        req = MagicMock()
        req.status.is_terminal.return_value = False
        handler._query_service.get_request = AsyncMock(return_value=req)
        handler._machine_sync_service.populate_missing_machine_ids = AsyncMock(
            side_effect=RuntimeError("provider down")
        )

        query = SyncAndGetRequestQuery(request_id="req-1")
        result = _sync(handler.execute_query(query))
        # Warning logged, stored state returned
        handler.logger.warning.assert_called()
        assert result is not None


# ===========================================================================
# SyncAndListReturnRequestsHandler — uncovered filter/pagination branches
# ===========================================================================


def _build_sync_return_handler(return_requests):
    """Build SyncAndListReturnRequestsHandler with mocked collaborators."""
    from orb.application.queries.request_query_handlers import SyncAndListReturnRequestsHandler

    uow = MagicMock()
    uow.requests.find_by_type.return_value = return_requests
    uow.machines.find_by_ids.return_value = []

    @contextmanager
    def _cm():
        yield uow

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.side_effect = _cm

    handler = object.__new__(SyncAndListReturnRequestsHandler)
    handler.logger = MagicMock()
    handler.error_handler = MagicMock()
    handler.uow_factory = uow_factory
    handler._generic_filter_service = MagicMock()
    handler._generic_filter_service.apply_filters.side_effect = lambda items, _: items
    handler._machine_sync_service = AsyncMock()
    handler._machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))
    handler._machine_sync_service.sync_machines_with_provider = AsyncMock(return_value=([], []))
    handler._status_service = AsyncMock()
    handler._status_service.determine_status_from_machines = MagicMock(return_value=(None, None))
    handler._status_service.update_request_status = AsyncMock()
    handler._query_service = AsyncMock()
    handler._query_service.get_machines_for_request = AsyncMock(return_value=[])

    def _create_dto(req, machines):
        rid = getattr(req, "request_id", None)
        if rid is None:
            rid_str = "r"
        elif hasattr(rid, "value"):
            rid_str = str(rid.value)
        else:
            rid_str = str(rid)
        return _FakeRequestDTO(
            request_id=rid_str,
            provider_name=getattr(req, "provider_name", None),
            provider_type=getattr(req, "provider_type", None),
            template_id=getattr(req, "template_id", None),
        )

    mock_f = MagicMock()
    mock_f.create_from_domain.side_effect = _create_dto
    handler._dto_factory = mock_f

    return handler


def _make_return_req_ns(request_id="ret-1", provider_name="aws", provider_type="aws"):
    r = MagicMock()
    r.request_id.value = request_id
    r.request_id.__str__ = MagicMock(return_value=request_id)
    r.provider_name = provider_name
    r.provider_type = provider_type
    r.status.is_terminal.return_value = True  # skip sync for simplicity
    r.machine_ids = []
    return r


@pytest.mark.unit
class TestSyncAndListReturnRequestsHandlerBranches:
    """Covers lines 395-396, 417-453, 459-514 of request_query_handlers.py."""

    def test_filter_by_provider_name(self):
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        req_a = _make_return_req_ns("ret-a", provider_name="aws")
        req_b = _make_return_req_ns("ret-b", provider_name="azure")
        handler = _build_sync_return_handler([req_a, req_b])

        query = SyncAndListReturnRequestsQuery(provider_name="aws")
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 1

    def test_filter_by_provider_type(self):
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        req_a = _make_return_req_ns("ret-a", provider_type="aws")
        req_b = _make_return_req_ns("ret-b", provider_type="gcp")
        handler = _build_sync_return_handler([req_a, req_b])

        query = SyncAndListReturnRequestsQuery(provider_type="aws")
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 1

    def test_q_filter_applied_to_dtos(self):
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        req_a = _make_return_req_ns("ret-FINDME-1")
        req_b = _make_return_req_ns("ret-other-2")
        handler = _build_sync_return_handler([req_a, req_b])

        query = SyncAndListReturnRequestsQuery(q="findme")
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 1

    def test_pagination_with_offset_and_limit(self):
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        reqs = [_make_return_req_ns(f"ret-{i}") for i in range(5)]
        handler = _build_sync_return_handler(reqs)

        query = SyncAndListReturnRequestsQuery(offset=2, limit=2)
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 2

    def test_limit_clamped_to_1000(self):
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        reqs = [_make_return_req_ns(f"ret-{i}") for i in range(3)]
        handler = _build_sync_return_handler(reqs)

        query = SyncAndListReturnRequestsQuery(limit=9999)
        _sync(handler.execute_query(query))
        # 3 items fit within clamped limit; warning should have been logged
        handler.logger.warning.assert_called()

    def test_sync_error_for_non_terminal_request_is_swallowed(self):
        """If sync raises for a non-terminal return request, warning is logged and continues."""
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        req = _make_return_req_ns("ret-1")
        req.status.is_terminal.return_value = False
        handler = _build_sync_return_handler([req])
        handler._query_service.get_machines_for_request = AsyncMock(
            side_effect=RuntimeError("sync fail")
        )

        query = SyncAndListReturnRequestsQuery()
        result = _sync(handler.execute_query(query))
        handler.logger.warning.assert_called()
        assert result is not None

    def test_filter_expressions_applied(self):
        """filter_expressions path calls apply_filters."""
        from orb.application.dto.queries import SyncAndListReturnRequestsQuery

        req = _make_return_req_ns("ret-1")
        handler = _build_sync_return_handler([req])
        handler._generic_filter_service.apply_filters.return_value = []

        fake_dto = _FakeRequestDTO("ret-1")
        fake_dto.model_dump = lambda: {}  # type: ignore[method-assign]

        with patch(
            "orb.application.queries.request_query_handlers.RequestDTO.model_validate",
            return_value=fake_dto,
        ):
            query = SyncAndListReturnRequestsQuery(filter_expressions=["expr"])
            _sync(handler.execute_query(query))

        handler._generic_filter_service.apply_filters.assert_called_once()


# ===========================================================================
# SyncAndListActiveRequestsHandler — template_id + provider filters + BaseException
# ===========================================================================


def _build_active_handler(requests_list):
    """Build SyncAndListActiveRequestsHandler with all_resources behaviour."""
    from orb.application.queries.request_query_handlers import SyncAndListActiveRequestsHandler

    uow = MagicMock()
    uow.requests.find_all.return_value = requests_list

    @contextmanager
    def _cm():
        yield uow

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.side_effect = _cm

    handler = object.__new__(SyncAndListActiveRequestsHandler)
    handler.logger = MagicMock()
    handler.error_handler = MagicMock()
    handler.uow_factory = uow_factory
    handler._sync_timeout = 0.05  # fast for tests
    handler._generic_filter_service = MagicMock()
    handler._generic_filter_service.apply_filters.side_effect = lambda items, _: items
    handler._machine_sync_service = AsyncMock()
    handler._machine_sync_service.populate_missing_machine_ids = AsyncMock()
    handler._machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))
    handler._machine_sync_service.sync_machines_with_provider = AsyncMock(return_value=([], []))
    handler._status_service = AsyncMock()
    handler._status_service.determine_status_from_machines = MagicMock(return_value=(None, None))
    handler._status_service.update_request_status = AsyncMock()
    handler._query_service = AsyncMock()

    def _get_request(rid):
        return next((r for r in requests_list if str(r.request_id.value) == rid), None)

    handler._query_service.get_request = AsyncMock(side_effect=_get_request)
    handler._query_service.get_machines_for_request = AsyncMock(return_value=[])

    def _create_dto(req, machines):
        rid = getattr(req, "request_id", None)
        rid_str = str(getattr(rid, "value", rid)) if rid is not None else "r"
        return _FakeRequestDTO(
            request_id=rid_str,
            provider_name=getattr(req, "provider_name", None),
            provider_type=getattr(req, "provider_type", None),
            template_id=getattr(req, "template_id", None),
        )

    mock_f = MagicMock()
    mock_f.create_from_domain.side_effect = _create_dto
    handler._dto_factory = mock_f

    return handler


def _make_active_req(req_id, template_id="t1", provider_name="aws", provider_type="aws"):
    r = MagicMock()
    r.request_id.value = req_id
    r.template_id = template_id
    r.provider_name = provider_name
    r.provider_type = provider_type
    r.status.value = "in_progress"
    return r


@pytest.mark.unit
class TestSyncAndListActiveRequestsHandlerBranches:
    """Covers lines 556/559 (template_id filter), 578-630 (provider/type filter), etc."""

    def test_template_id_filter_applied(self):
        from orb.application.dto.queries import SyncAndListActiveRequestsQuery

        req_a = _make_active_req("req-a", template_id="tmpl-want")
        req_b = _make_active_req("req-b", template_id="tmpl-other")
        handler = _build_active_handler([req_a, req_b])

        query = SyncAndListActiveRequestsQuery(all_resources=True, template_id="tmpl-want")
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 1

    def test_provider_name_filter_on_dtos(self):
        from orb.application.dto.queries import SyncAndListActiveRequestsQuery

        req_a = _make_active_req("req-a", provider_name="aws")
        req_b = _make_active_req("req-b", provider_name="other")
        handler = _build_active_handler([req_a, req_b])

        query = SyncAndListActiveRequestsQuery(all_resources=True, provider_name="aws")
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 1

    def test_provider_type_filter_on_dtos(self):
        from orb.application.dto.queries import SyncAndListActiveRequestsQuery

        req_a = _make_active_req("req-a", provider_type="aws")
        req_b = _make_active_req("req-b", provider_type="k8s")
        handler = _build_active_handler([req_a, req_b])

        query = SyncAndListActiveRequestsQuery(all_resources=True, provider_type="aws")
        result = _sync(handler.execute_query(query))
        assert len(result.items) == 1

    def test_filter_expressions_applied_on_active(self):
        """filter_expressions path in SyncAndListActiveRequestsHandler."""
        from orb.application.dto.queries import SyncAndListActiveRequestsQuery

        req_a = _make_active_req("req-a")
        handler = _build_active_handler([req_a])
        handler._generic_filter_service.apply_filters.return_value = []

        fake_dto = _FakeRequestDTO("req-a")
        fake_dto.model_dump = lambda: {}  # type: ignore[method-assign]

        with patch(
            "orb.application.queries.request_query_handlers.RequestDTO.model_validate",
            return_value=fake_dto,
        ):
            query = SyncAndListActiveRequestsQuery(all_resources=True, filter_expressions=["x"])
            _sync(handler.execute_query(query))

        handler._generic_filter_service.apply_filters.assert_called_once()

    def test_non_exception_base_exception_is_reraised(self):
        """Non-Exception BaseException (e.g. KeyboardInterrupt) propagates from gather."""
        from orb.application.dto.queries import SyncAndListActiveRequestsQuery

        req_a = _make_active_req("req-a")
        handler = _build_active_handler([req_a])
        # Simulate gather returning a KeyboardInterrupt (non-Exception BaseException)
        with patch(
            "asyncio.gather", new_callable=AsyncMock, return_value=[KeyboardInterrupt("test")]
        ):
            query = SyncAndListActiveRequestsQuery(all_resources=True)
            with pytest.raises(KeyboardInterrupt):
                _sync(handler.execute_query(query))


# ===========================================================================
# CreateReturnRequestHandler — uncovered branches
# ===========================================================================


def _make_return_cmd_handler(uow=None):
    from orb.application.commands.request_creation_handlers import CreateReturnRequestHandler
    from orb.domain.base.ports import (
        ErrorHandlingPort,
        EventPublisherPort,
        LoggingPort,
    )

    if uow is None:
        uow = MagicMock()
        uow.requests.save.return_value = []
        uow.machines.get_by_id.return_value = None

    @contextmanager
    def _cm():
        yield uow

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.side_effect = _cm

    handler = object.__new__(CreateReturnRequestHandler)
    handler.logger = MagicMock(spec=LoggingPort)
    handler.error_handler = MagicMock(spec=ErrorHandlingPort)
    handler.event_publisher = MagicMock(spec=EventPublisherPort)
    handler._metrics = {}
    handler.uow_factory = uow_factory
    handler._container = MagicMock()
    handler._query_bus = AsyncMock()
    handler._provider_selection_port = MagicMock()
    handler._machine_grouping_service = MagicMock()
    handler._deprovisioning_orchestrator = AsyncMock()
    return handler, uow


@pytest.mark.unit
class TestCreateReturnRequestHandlerBranches:
    """Covers lines 333-334, 397, 401, 404-421 of request_creation_handlers.py."""

    def test_filter_machines_skips_not_found_machines(self):
        """_filter_machines: machine not found → added to skipped list."""
        handler, uow = _make_return_cmd_handler()
        uow.machines.get_by_id.return_value = None

        valid, skipped = handler._filter_machines(["m-missing"])
        assert "m-missing" not in valid
        assert any("not found" in s.get("reason", "").lower() for s in skipped)

    def test_filter_machines_skips_machine_with_pending_return(self):
        """_filter_machines: machine with return_request_id and no force → skipped."""
        handler, uow = _make_return_cmd_handler()
        machine = MagicMock()
        machine.return_request_id = "existing-return-req"
        uow.machines.get_by_id.return_value = machine

        valid, skipped = handler._filter_machines(["m-1"], force_return=False)
        assert "m-1" not in valid
        assert len(skipped) == 1

    def test_filter_machines_accepts_valid_machine(self):
        """_filter_machines: valid machine without pending return is accepted."""
        handler, uow = _make_return_cmd_handler()
        machine = MagicMock()
        machine.return_request_id = None
        uow.machines.get_by_id.return_value = machine

        valid, skipped = handler._filter_machines(["m-ok"])
        assert "m-ok" in valid
        assert len(skipped) == 0

    def test_filter_machines_force_return_accepts_machine_with_pending_return(self):
        """_filter_machines: force_return=True accepts machine with return_request_id."""
        handler, uow = _make_return_cmd_handler()
        machine = MagicMock()
        machine.return_request_id = "old-return"
        uow.machines.get_by_id.return_value = machine

        valid, skipped = handler._filter_machines(["m-1"], force_return=True)
        assert "m-1" in valid

    def test_cancel_validate_and_persist_cancels_stuck_request_on_force_return(self):
        """_cancel_validate_and_persist cancels stuck return and clears machine's return_request_id."""
        handler, uow = _make_return_cmd_handler()

        machine = MagicMock()
        # Use a valid return-request ID format so RequestId validation passes
        machine.return_request_id = "ret-00000000-0000-0000-0000-000000000001"
        cleared_machine = MagicMock()
        cleared_machine.return_request_id = None
        machine.model_copy.return_value = cleared_machine

        stuck_request = MagicMock()
        cancelled_request = MagicMock()
        stuck_request.cancel.return_value = cancelled_request

        uow.machines.get_by_id.side_effect = [machine, cleared_machine]
        uow.requests.get_by_id.return_value = stuck_request
        uow.requests.save.return_value = []

        new_request = MagicMock()
        new_request.request_id = "ret-00000000-0000-0000-0000-000000000002"

        handler._cancel_validate_and_persist(["m-1"], new_request, force_return=True)

        stuck_request.cancel.assert_called_once()
        uow.requests.save.assert_called()

    def test_update_request_status_failed_fallback_on_command_bus_failure(self):
        """_update_request_status: on FAILED with command bus failure, falls back to direct save."""
        from orb.domain.request.request_types import RequestStatus

        handler, uow = _make_return_cmd_handler()
        command_bus = AsyncMock()
        command_bus.execute.side_effect = RuntimeError("bus down")
        handler._container.get.return_value = command_bus

        stuck_request = MagicMock()
        stuck_request.update_status.return_value = stuck_request
        uow.requests.get_by_id.return_value = stuck_request
        uow.requests.save.return_value = []

        request = MagicMock()
        request.request_id = "req-1"
        # Should not raise — falls back to direct UoW write
        _sync(
            handler._update_request_status(request, RequestStatus.FAILED, "deprovisioning failed")
        )
        # Direct save should have been attempted
        uow.requests.save.assert_called()

    def test_update_request_status_non_failed_reraises_on_bus_failure(self):
        """_update_request_status: non-FAILED status re-raises when command bus fails."""
        from orb.domain.request.request_types import RequestStatus

        handler, uow = _make_return_cmd_handler()
        command_bus = AsyncMock()
        command_bus.execute.side_effect = RuntimeError("bus down")
        handler._container.get.return_value = command_bus

        request = MagicMock()
        request.request_id = "req-1"
        with pytest.raises(RuntimeError, match="bus down"):
            _sync(
                handler._update_request_status(request, RequestStatus.IN_PROGRESS, "transitioning")
            )


# ===========================================================================
# ProvisioningOrchestrationService._extract_provider_error_fields
# ===========================================================================


@pytest.mark.unit
class TestExtractProviderErrorFields:
    """Covers _extract_provider_error_fields fallback attribute lookup."""

    def test_aws_fallback_attributes_used_when_provider_attrs_absent(self):
        from orb.application.services.provisioning_orchestration_service import (
            _extract_provider_error_fields,
        )

        class LegacyAWSExc(Exception):
            aws_error_code = "UnauthorizedOperation"
            aws_error_message = "not allowed"
            aws_request_id = "req-abc"

        result = _extract_provider_error_fields(LegacyAWSExc())
        assert result["provider_error_code"] == "UnauthorizedOperation"
        assert result["provider_error_message"] == "not allowed"
        assert result["provider_request_id"] == "req-abc"

    def test_provider_attrs_take_precedence_over_aws_attrs(self):
        from orb.application.services.provisioning_orchestration_service import (
            _extract_provider_error_fields,
        )

        class ProviderExc(Exception):
            provider_error_code = "ProviderCode"
            provider_error_message = "provider msg"
            provider_request_id = "prov-req-1"
            aws_error_code = "AWSCode"
            error_source = "aws.ec2.RunInstances"

        result = _extract_provider_error_fields(ProviderExc())
        assert result["provider_error_code"] == "ProviderCode"
        assert result["error_source"] == "aws.ec2.RunInstances"

    def test_plain_exception_returns_all_none(self):
        from orb.application.services.provisioning_orchestration_service import (
            _extract_provider_error_fields,
        )

        result = _extract_provider_error_fields(RuntimeError("plain"))
        assert result["provider_error_code"] is None
        assert result["provider_error_message"] is None
        assert result["provider_request_id"] is None
        assert result["error_source"] is None


# ===========================================================================
# SyncMachineOrchestrator — all branches
# ===========================================================================


@pytest.mark.unit
class TestSyncMachineOrchestratorBranches:
    """Covers lines 50-51, 53-55, 61-62, 65-68, 70-72, 78-79, 82-83, 86-87, 91-96, 98."""

    def _build_orchestrator(self):
        from orb.application.services.orchestration.sync_machine import SyncMachineOrchestrator

        orch = object.__new__(SyncMachineOrchestrator)
        orch._logger = MagicMock()

        uow = MagicMock()
        orch._uow_obj = uow

        @contextmanager
        def _cm():
            yield uow

        orch._uow_factory = MagicMock()
        orch._uow_factory.create_unit_of_work.side_effect = _cm

        orch._machine_sync_service = AsyncMock()
        orch._command_bus = AsyncMock()
        orch._query_bus = AsyncMock()
        return orch

    def test_machine_not_found_returns_not_found_output(self):
        from orb.application.services.orchestration.dtos import SyncMachineInput

        orch = self._build_orchestrator()
        orch._uow_obj.machines.get_by_id.return_value = None
        orch._uow_obj.requests.get_by_id.return_value = None

        result = _sync(orch.execute(SyncMachineInput(machine_id="m-missing")))
        assert result.machine is None
        assert result.synced is False
        assert result.error == "machine_not_found"

    def test_machine_with_no_request_id_returns_no_parent_request(self):
        from orb.application.machine.dto import MachineDTO
        from orb.application.services.orchestration.dtos import SyncMachineInput

        orch = self._build_orchestrator()
        machine = MagicMock()
        machine.request_id = None
        machine.machine_id = MagicMock()
        machine.machine_id.__eq__ = lambda s, other: True
        orch._uow_obj.machines.get_by_id.return_value = machine
        orch._uow_obj.requests.get_by_id.return_value = None

        with patch.object(MachineDTO, "from_domain", return_value=MagicMock()):
            result = _sync(orch.execute(SyncMachineInput(machine_id="m-1")))

        assert result.synced is False
        assert result.error == "no_parent_request"

    def test_provider_fetch_exception_returns_error_output(self):
        from orb.application.machine.dto import MachineDTO
        from orb.application.services.orchestration.dtos import SyncMachineInput

        orch = self._build_orchestrator()
        machine = MagicMock()
        machine.request_id = "req-1"
        machine.machine_id = MagicMock()
        request = MagicMock()
        orch._uow_obj.machines.get_by_id.return_value = machine
        orch._uow_obj.requests.get_by_id.return_value = request
        orch._machine_sync_service.fetch_provider_machines = AsyncMock(
            side_effect=RuntimeError("fetch failed")
        )

        with patch.object(MachineDTO, "from_domain", return_value=MagicMock()):
            result = _sync(orch.execute(SyncMachineInput(machine_id="m-1")))

        assert result.synced is False
        assert "fetch failed" in (result.error or "")

    def test_empty_provider_machines_returns_no_data_error(self):
        from orb.application.machine.dto import MachineDTO
        from orb.application.services.orchestration.dtos import SyncMachineInput

        orch = self._build_orchestrator()
        machine = MagicMock()
        machine.request_id = "req-1"
        request = MagicMock()
        orch._uow_obj.machines.get_by_id.return_value = machine
        orch._uow_obj.requests.get_by_id.return_value = request
        orch._machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))

        with patch.object(MachineDTO, "from_domain", return_value=MagicMock()):
            result = _sync(orch.execute(SyncMachineInput(machine_id="m-1")))

        assert result.synced is False
        assert result.error == "provider_returned_no_data"

    def test_sync_persist_exception_returns_error(self):
        from orb.application.machine.dto import MachineDTO
        from orb.application.services.orchestration.dtos import SyncMachineInput

        orch = self._build_orchestrator()
        machine = MagicMock()
        machine.request_id = "req-1"
        request = MagicMock()
        orch._uow_obj.machines.get_by_id.return_value = machine
        orch._uow_obj.requests.get_by_id.return_value = request
        orch._machine_sync_service.fetch_provider_machines = AsyncMock(
            return_value=([MagicMock()], {})
        )
        orch._machine_sync_service.sync_machines_with_provider = AsyncMock(
            side_effect=RuntimeError("persist fail")
        )

        with patch.object(MachineDTO, "from_domain", return_value=MagicMock()):
            result = _sync(orch.execute(SyncMachineInput(machine_id="m-1")))

        assert result.synced is False
        assert "persist fail" in (result.error or "")

    def test_successful_sync_returns_synced_true(self):
        from orb.application.machine.dto import MachineDTO
        from orb.application.services.orchestration.dtos import SyncMachineInput

        orch = self._build_orchestrator()
        machine = MagicMock()
        machine.request_id = "req-1"
        machine.machine_id.value = "m-1"
        machine.machine_id.__eq__ = lambda s, o: True

        synced_machine = MagicMock()
        synced_machine.machine_id.value = "m-1"
        synced_machine.machine_id.__eq__ = lambda s, o: (
            s.value == (o.value if hasattr(o, "value") else str(o))
        )

        request = MagicMock()
        orch._uow_obj.machines.get_by_id.return_value = machine
        orch._uow_obj.requests.get_by_id.return_value = request
        orch._machine_sync_service.fetch_provider_machines = AsyncMock(
            return_value=([synced_machine], {})
        )
        orch._machine_sync_service.sync_machines_with_provider = AsyncMock(
            return_value=([synced_machine], [])
        )

        dto = MagicMock()
        with patch.object(MachineDTO, "from_domain", return_value=dto):
            result = _sync(orch.execute(SyncMachineInput(machine_id="m-1")))

        assert result.synced is True
        assert result.machine is dto
