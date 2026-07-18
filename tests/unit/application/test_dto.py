"""Unit tests for application DTO layer — base, commands, template_generation_dto, paginated_responses, bulk_responses, interface_response."""

from __future__ import annotations

import dataclasses

import pytest

from orb.application.dto.base import (
    BaseCommand,
    BaseDTO,
    BaseQuery,
    BaseResponse,
    PaginatedResponse,
    PaginationMetadata,
)
from orb.application.dto.bulk_responses import (
    BulkMachineResponse,
    BulkRequestResponse,
    BulkTemplateResponse,
)
from orb.application.dto.commands import (
    CancelRequestCommand,
    CleanupAllResourcesCommand,
    CleanupOldRequestsCommand,
    CreateRequestCommand,
    CreateReturnRequestCommand,
    UpdateRequestStatusCommand,
)
from orb.application.dto.interface_response import InterfaceResponse
from orb.application.dto.paginated_responses import (
    PaginatedListResponse,
    PaginatedMachinesResponse,
    PaginatedRequestsResponse,
    PaginatedTemplatesResponse,
)
from orb.application.dto.template_generation_dto import (
    ProviderTemplateResult,
    TemplateGenerationRequest,
    TemplateGenerationResult,
)
from orb.domain.request.value_objects import RequestStatus

# ---------------------------------------------------------------------------
# BaseDTO
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseDTO:
    def _concrete(self, **kw) -> BaseDTO:
        class _C(BaseDTO):
            value: str = "x"

        return _C(**kw)

    def test_to_dict_returns_snake_case(self):
        c = self._concrete(value="hello")
        d = c.to_dict()
        assert d["value"] == "hello"

    def test_from_dict_roundtrip(self):
        class _C(BaseDTO):
            value: str = "default"

        c = _C.from_dict({"value": "roundtrip"})
        assert c.value == "roundtrip"  # type: ignore[attr-defined]

    def test_serialize_enum_returns_string(self):
        from enum import Enum

        class _E(Enum):
            A = "value_a"

        assert BaseDTO.serialize_enum(_E.A) == "value_a"

    def test_serialize_enum_none_returns_none(self):
        assert BaseDTO.serialize_enum(None) is None

    def test_serialize_enum_string_passthrough(self):
        assert BaseDTO.serialize_enum("raw") == "raw"


# ---------------------------------------------------------------------------
# BaseCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseCommand:
    def test_command_is_mutable(self):
        class _Cmd(BaseCommand):
            result: str = ""

        cmd = _Cmd()
        cmd.result = "done"
        assert cmd.result == "done"

    def test_defaults_set(self):
        class _Cmd(BaseCommand):
            pass

        cmd = _Cmd()
        assert cmd.dry_run is False
        assert cmd.metadata == {}


# ---------------------------------------------------------------------------
# BaseQuery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseQuery:
    def test_defaults_set(self):
        class _Q(BaseQuery):
            pass

        q = _Q()
        assert q.filters == {}
        assert q.pagination is None


# ---------------------------------------------------------------------------
# BaseResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseResponse:
    def test_success_defaults_true(self):
        class _R(BaseResponse):
            pass

        r = _R()
        assert r.success is True

    def test_error_code_defaults_none(self):
        class _R(BaseResponse):
            pass

        r = _R()
        assert r.error_code is None


# ---------------------------------------------------------------------------
# PaginationMetadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPaginationMetadata:
    def test_construction(self):
        meta = PaginationMetadata(
            total_count=100,
            limit=10,
            offset=0,
            has_more=True,
            returned_count=10,
        )
        assert meta.total_count == 100
        assert meta.has_more is True

    def test_alias_by_alias_camel_case(self):
        meta = PaginationMetadata(
            total_count=5,
            limit=5,
            offset=0,
            has_more=False,
            returned_count=5,
        )
        d = meta.model_dump(by_alias=True)
        assert "totalCount" in d
        assert "returnedCount" in d


# ---------------------------------------------------------------------------
# PaginatedResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPaginatedResponse:
    def test_defaults(self):
        class _PR(PaginatedResponse):
            pass

        r = _PR()
        assert r.total_count == 0
        assert r.page == 1
        assert r.has_next is False


# ---------------------------------------------------------------------------
# CreateRequestCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateRequestCommand:
    def test_basic_construction(self):
        cmd = CreateRequestCommand(template_id="tmpl-1", requested_count=3)
        assert cmd.template_id == "tmpl-1"
        assert cmd.requested_count == 3

    def test_created_request_id_defaults_none(self):
        cmd = CreateRequestCommand(template_id="t", requested_count=1)
        assert cmd.created_request_id is None

    def test_created_request_id_can_be_set(self):
        cmd = CreateRequestCommand(template_id="t", requested_count=1)
        cmd.created_request_id = "new-id"
        assert cmd.created_request_id == "new-id"

    def test_dry_run_default_false(self):
        cmd = CreateRequestCommand(template_id="t", requested_count=1)
        assert cmd.dry_run is False


# ---------------------------------------------------------------------------
# CreateReturnRequestCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateReturnRequestCommand:
    def test_basic_construction(self):
        cmd = CreateReturnRequestCommand(machine_ids=["m1", "m2"])
        assert cmd.machine_ids == ["m1", "m2"]

    def test_force_return_default_false(self):
        cmd = CreateReturnRequestCommand(machine_ids=["m1"])
        assert cmd.force_return is False

    def test_result_fields_default_none(self):
        cmd = CreateReturnRequestCommand(machine_ids=["m1"])
        assert cmd.created_request_ids is None
        assert cmd.processed_machines is None
        assert cmd.skipped_machines is None

    def test_result_fields_can_be_set(self):
        cmd = CreateReturnRequestCommand(machine_ids=["m1"])
        cmd.created_request_ids = ["ret-1"]
        cmd.processed_machines = ["m1"]
        cmd.skipped_machines = []
        assert cmd.created_request_ids == ["ret-1"]


# ---------------------------------------------------------------------------
# UpdateRequestStatusCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateRequestStatusCommand:
    def test_construction(self):
        cmd = UpdateRequestStatusCommand(
            request_id="req-1",
            status=RequestStatus.IN_PROGRESS,
            message="starting",
        )
        assert cmd.request_id == "req-1"
        assert cmd.status == RequestStatus.IN_PROGRESS

    def test_message_defaults_none(self):
        cmd = UpdateRequestStatusCommand(
            request_id="req-1",
            status=RequestStatus.COMPLETED,
        )
        assert cmd.message is None


# ---------------------------------------------------------------------------
# CancelRequestCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelRequestCommand:
    def test_construction(self):
        cmd = CancelRequestCommand(request_id="req-1", reason="user request")
        assert cmd.request_id == "req-1"
        assert cmd.reason == "user request"
        assert cmd.cancelled is False

    def test_cancelled_can_be_set(self):
        cmd = CancelRequestCommand(request_id="r", reason="test")
        cmd.cancelled = True
        assert cmd.cancelled is True


# ---------------------------------------------------------------------------
# CleanupOldRequestsCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupOldRequestsCommand:
    def test_defaults(self):
        cmd = CleanupOldRequestsCommand()
        assert cmd.older_than_days == 1
        assert cmd.statuses_to_cleanup is None

    def test_result_fields_default_none(self):
        cmd = CleanupOldRequestsCommand()
        assert cmd.requests_cleaned is None


# ---------------------------------------------------------------------------
# CleanupAllResourcesCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupAllResourcesCommand:
    def test_defaults(self):
        cmd = CleanupAllResourcesCommand()
        assert cmd.older_than_days == 1
        assert cmd.include_pending is False

    def test_result_fields_settable(self):
        cmd = CleanupAllResourcesCommand()
        cmd.requests_cleaned = 5
        cmd.machines_cleaned = 10
        cmd.total_cleaned = 15
        assert cmd.total_cleaned == 15


# ---------------------------------------------------------------------------
# TemplateGenerationRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateGenerationRequest:
    def test_defaults(self):
        req = TemplateGenerationRequest()
        assert req.specific_provider is None
        assert req.all_providers is False
        assert req.force_overwrite is False
        assert req.provider_specific is False

    def test_with_specific_provider(self):
        req = TemplateGenerationRequest(specific_provider="aws-prod")
        assert req.specific_provider == "aws-prod"


# ---------------------------------------------------------------------------
# ProviderTemplateResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderTemplateResult:
    def test_construction_created(self):
        r = ProviderTemplateResult(
            provider="aws-prod",
            filename="aws_templates.json",
            templates_count=5,
            path="/tmp/aws_templates.json",
            status="created",
        )
        assert r.status == "created"
        assert r.reason is None

    def test_construction_skipped(self):
        r = ProviderTemplateResult(
            provider="aws-dev",
            filename="aws_templates.json",
            templates_count=0,
            path="/tmp/aws_templates.json",
            status="skipped",
            reason="file_exists",
        )
        assert r.status == "skipped"
        assert r.reason == "file_exists"


# ---------------------------------------------------------------------------
# TemplateGenerationResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateGenerationResult:
    def test_construction_success(self):
        r = TemplateGenerationResult(
            status="success",
            message="done",
            providers=[],
            total_templates=0,
            created_count=0,
            skipped_count=0,
        )
        assert r.status == "success"

    def test_construction_error(self):
        r = TemplateGenerationResult(
            status="error",
            message="failed",
            providers=[],
            total_templates=0,
            created_count=0,
            skipped_count=0,
        )
        assert r.status == "error"


# ---------------------------------------------------------------------------
# InterfaceResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInterfaceResponse:
    def test_default_exit_code_zero(self):
        r = InterfaceResponse(data={"key": "val"})
        assert r.exit_code == 0

    def test_custom_exit_code(self):
        r = InterfaceResponse(data={}, exit_code=1)
        assert r.exit_code == 1

    def test_is_frozen(self):
        r = InterfaceResponse(data={"x": 1})
        # frozen dataclass mutation raises FrozenInstanceError naming the field.
        with pytest.raises(dataclasses.FrozenInstanceError, match="exit_code"):
            r.exit_code = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PaginatedListResponse / PaginatedRequestsResponse etc.
# ---------------------------------------------------------------------------


def _meta() -> PaginationMetadata:
    return PaginationMetadata(total_count=100, limit=10, offset=0, has_more=True, returned_count=10)


@pytest.mark.unit
class TestPaginatedListResponse:
    def test_construction(self):
        r = PaginatedListResponse[str](data=["a", "b"], pagination=_meta())
        assert r.data == ["a", "b"]
        assert r.pagination.total_count == 100

    def test_requests_response(self):
        r = PaginatedRequestsResponse(data=["x"], pagination=_meta())
        assert r.data == ["x"]

    def test_machines_response(self):
        r = PaginatedMachinesResponse(data=[], pagination=_meta())
        assert r.data == []

    def test_templates_response(self):
        r = PaginatedTemplatesResponse(data=[], pagination=_meta())
        assert r.data == []


# ---------------------------------------------------------------------------
# Bulk responses
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkResponses:
    def test_bulk_template_response(self):
        r = BulkTemplateResponse(
            templates=[{"id": "t1"}],
            found_count=1,
            not_found_ids=[],
            total_requested=1,
        )
        assert r.found_count == 1
        assert r.not_found_ids == []

    def test_bulk_request_response_not_found_ids(self):
        r = BulkRequestResponse(
            requests=[],
            found_count=0,
            not_found_ids=["req-missing"],
            total_requested=1,
        )
        assert "req-missing" in r.not_found_ids

    def test_bulk_machine_response_fields(self):
        r = BulkMachineResponse(
            machines=[],
            found_count=0,
            not_found_ids=["m-missing"],
            total_requested=1,
        )
        assert r.total_requested == 1
