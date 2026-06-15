"""Unit tests: AWS ClientError details are captured and propagated to the request.

Covers:
  - _convert_client_error preserves aws_error_code, aws_error_message,
    aws_request_id, error_source on every mapped exception subclass.
  - Access-key redaction in error messages.
  - _extract_aws_error_fields helper extracts attrs from AWSError subclasses
    and returns None values for non-AWSError exceptions.
  - ProvisioningResult carries AWS error fields through from the exception.
  - RequestStatusManagementService._handle_provisioning_failure writes
    error_details["aws_error"] onto the Request aggregate.
  - RequestDTO.from_domain exposes error_details["aws_error"] as the top-level
    error field; to_dict includes the error block only when present.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_error(
    code: str,
    message: str,
    request_id: str = "req-abc-123",
    http_status: int = 403,
) -> ClientError:
    """Build a botocore ClientError with realistic response metadata."""
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {
                "RequestId": request_id,
                "HTTPStatusCode": http_status,
            },
        },
        "RunInstances",
    )


def _make_handler():
    """Minimal AWSHandler-like object exposing _convert_client_error."""
    from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler

    # AWSHandler is abstract; build via a minimal concrete subclass
    class _Handler(AWSHandler):
        def _acquire_hosts_internal(self, request, aws_template):
            pass  # type: ignore[return]

        def check_hosts_status(self, request):
            pass  # type: ignore[return]

        def release_hosts(self, machine_ids, resource_mapping=None, request_id=""):
            pass

        def cancel_resource(self, resource_id, request_id):
            pass  # type: ignore[return]

        @classmethod
        def get_example_templates(cls):
            return []

    return _Handler(
        aws_client=MagicMock(),
        logger=MagicMock(),
        aws_ops=MagicMock(),
        launch_template_manager=MagicMock(),
    )


# ---------------------------------------------------------------------------
# _convert_client_error — error field propagation
# ---------------------------------------------------------------------------


class TestConvertClientError:
    """_convert_client_error transfers AWS error metadata to the domain exception."""

    def setup_method(self):
        self.handler = _make_handler()

    def _check_aws_fields(self, exc, expected_code, expected_message, expected_request_id):
        assert getattr(exc, "aws_error_code", None) == expected_code
        assert getattr(exc, "aws_error_message", None) == expected_message
        assert getattr(exc, "aws_request_id", None) == expected_request_id
        assert getattr(exc, "error_source", None) is not None

    def test_unauthorized_operation_carries_aws_fields(self):
        err = _make_client_error("UnauthorizedOperation", "You are not authorized.", "rid-001")
        exc = self.handler._convert_client_error(err, "run_instances")
        self._check_aws_fields(exc, "UnauthorizedOperation", "You are not authorized.", "rid-001")

    def test_access_denied_carries_aws_fields(self):
        err = _make_client_error("AccessDenied", "Access denied.", "rid-002")
        exc = self.handler._convert_client_error(err, "create_fleet")
        self._check_aws_fields(exc, "AccessDenied", "Access denied.", "rid-002")

    def test_insufficient_instance_capacity_carries_aws_fields(self):
        err = _make_client_error(
            "InsufficientInstanceCapacity", "Insufficient capacity.", "rid-003"
        )
        exc = self.handler._convert_client_error(err, "run_instances")
        # InsufficientInstanceCapacity falls to the AWSInfrastructureError else-branch
        # which also carries AWS error fields.
        self._check_aws_fields(
            exc, "InsufficientInstanceCapacity", "Insufficient capacity.", "rid-003"
        )

    def test_quota_exceeded_carries_aws_fields(self):
        err = _make_client_error("InstanceLimitExceeded", "Limit exceeded.", "rid-004")
        exc = self.handler._convert_client_error(err, "run_instances")
        self._check_aws_fields(exc, "InstanceLimitExceeded", "Limit exceeded.", "rid-004")

    def test_error_source_contains_operation_name(self):
        err = _make_client_error("UnauthorizedOperation", "Denied.", "rid-005")
        exc = self.handler._convert_client_error(err, "run_instances")
        assert "run_instances" in (getattr(exc, "error_source", "") or "")

    def test_missing_response_metadata_does_not_raise(self):
        """Gracefully handle ClientError without ResponseMetadata.RequestId."""
        client_err = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "Denied."}},
            "RunInstances",
        )
        exc = self.handler._convert_client_error(client_err, "run_instances")
        # aws_request_id should be None (key absent) but must not raise
        assert getattr(exc, "aws_request_id", "sentinel") is None

    def test_access_key_redacted_in_message(self):
        """AWS error messages containing access-key tokens are redacted."""
        message = "User AKIAIOSFODNN7EXAMPLE is not authorized to perform this operation."
        err = _make_client_error("UnauthorizedOperation", message, "rid-006")
        exc = self.handler._convert_client_error(err, "run_instances")
        assert "AKIAIOSFODNN7EXAMPLE" not in str(exc)
        assert getattr(exc, "aws_error_message", "") is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in (getattr(exc, "aws_error_message", "") or "")

    def test_sts_access_key_prefix_redacted(self):
        """Temporary credential key IDs (ASIA prefix) are also redacted."""
        message = "ASIAIOSFODNN7EXAMPLE is invalid."
        err = _make_client_error("AccessDenied", message, "rid-007")
        exc = self.handler._convert_client_error(err, "run_instances")
        assert "ASIAIOSFODNN7EXAMPLE" not in (getattr(exc, "aws_error_message", "") or "")


# ---------------------------------------------------------------------------
# _extract_aws_error_fields helper
# ---------------------------------------------------------------------------


class TestExtractAwsErrorFields:
    """_extract_aws_error_fields returns correct dict for AWSError and falls back for others."""

    def test_returns_aws_fields_from_aws_error(self):
        from orb.application.services.provisioning_orchestration_service import (
            _extract_aws_error_fields,
        )
        from orb.providers.aws.exceptions.aws_exceptions import AuthorizationError

        exc = AuthorizationError(
            "denied",
            aws_error_code="UnauthorizedOperation",
            aws_error_message="You are not authorized.",
            aws_request_id="rid-xyz",
            error_source="aws.ec2.run_instances",
        )
        result = _extract_aws_error_fields(exc)
        assert result["aws_error_code"] == "UnauthorizedOperation"
        assert result["aws_error_message"] == "You are not authorized."
        assert result["aws_request_id"] == "rid-xyz"
        assert result["error_source"] == "aws.ec2.run_instances"

    def test_returns_none_values_for_generic_exception(self):
        from orb.application.services.provisioning_orchestration_service import (
            _extract_aws_error_fields,
        )

        result = _extract_aws_error_fields(ValueError("some error"))
        assert result["aws_error_code"] is None
        assert result["aws_error_message"] is None
        assert result["aws_request_id"] is None
        assert result["error_source"] is None

    def test_partial_aws_error_attrs(self):
        from orb.application.services.provisioning_orchestration_service import (
            _extract_aws_error_fields,
        )
        from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError

        exc = AWSInfrastructureError(
            "infra error",
            aws_error_code="InsufficientInstanceCapacity",
        )
        result = _extract_aws_error_fields(exc)
        assert result["aws_error_code"] == "InsufficientInstanceCapacity"
        assert result["aws_error_message"] is None


# ---------------------------------------------------------------------------
# RequestStatusManagementService._handle_provisioning_failure
# ---------------------------------------------------------------------------


class TestHandleProvisioningFailure:
    """error_details["aws_error"] is written when provisioning fails with AWS context."""

    def _make_service(self):
        from orb.application.services.request_status_management_service import (
            RequestStatusManagementService,
        )

        return RequestStatusManagementService(uow_factory=MagicMock(), logger=MagicMock())

    def _make_provisioning_result(self, **kwargs):
        from orb.application.services.provisioning_orchestration_service import ProvisioningResult

        defaults = dict(
            success=False,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            error_message="Provisioning failed",
        )
        defaults.update(kwargs)
        return ProvisioningResult(**defaults)

    def _make_real_request(self):
        """Build a real Request aggregate (not a mock) so model_copy works."""
        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        # Let Request generate its own UUID-based ID
        return Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-test",
            machine_count=1,
            provider_type="aws",
        )

    def test_no_aws_fields_leaves_error_details_clean(self):
        """When no AWS-specific fields present, error_details stays clean."""
        svc = self._make_service()
        request = self._make_real_request()
        result = self._make_provisioning_result(error_message="generic error")
        updated = svc._handle_provisioning_failure(request, result)
        # aws_error key should be absent when no AWS details are available
        assert "aws_error" not in updated.error_details

    def test_aws_error_code_written_to_error_details(self):
        """UnauthorizedOperation is stored in error_details["aws_error"]["code"]."""
        svc = self._make_service()
        request = self._make_real_request()
        result = self._make_provisioning_result(
            error_message="AWS error",
            aws_error_code="UnauthorizedOperation",
            aws_error_message="You are not authorized.",
            aws_request_id="rid-aws-001",
            error_source="aws.ec2.run_instances",
        )
        updated = svc._handle_provisioning_failure(request, result)
        aws_err = updated.error_details.get("aws_error", {})
        assert aws_err["code"] == "UnauthorizedOperation"
        assert aws_err["message"] == "You are not authorized."
        assert aws_err["aws_request_id"] == "rid-aws-001"
        assert aws_err["source"] == "aws.ec2.run_instances"

    def test_partial_aws_fields_stored(self):
        """Only non-None AWS fields are stored — missing ones are absent from the block."""
        svc = self._make_service()
        request = self._make_real_request()
        result = self._make_provisioning_result(
            error_message="AWS error",
            aws_error_code="InsufficientInstanceCapacity",
            # aws_error_message, aws_request_id, error_source intentionally absent
        )
        updated = svc._handle_provisioning_failure(request, result)
        aws_err = updated.error_details.get("aws_error", {})
        assert aws_err["code"] == "InsufficientInstanceCapacity"
        assert "message" not in aws_err
        assert "aws_request_id" not in aws_err


# ---------------------------------------------------------------------------
# RequestDTO.from_domain — error field population
# ---------------------------------------------------------------------------


class TestRequestDTOErrorField:
    """RequestDTO.from_domain exposes aws_error as the top-level error field."""

    def _make_request_with_error(self, aws_error_block: dict):
        from datetime import datetime, timezone

        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-1",
            machine_count=1,
            provider_type="aws",
        )
        # inject error_details
        from orb.domain.request.request_types import RequestStatus

        updated_fields = request.model_dump()
        updated_fields["error_details"] = {"aws_error": aws_error_block}
        updated_fields["status"] = RequestStatus.FAILED
        return Request.model_validate(updated_fields)

    def test_error_field_present_when_aws_error_in_details(self):
        from orb.application.request.dto import RequestDTO

        request = self._make_request_with_error(
            {"code": "UnauthorizedOperation", "message": "not authorized"}
        )
        dto = RequestDTO.from_domain(request)
        assert dto.error is not None
        assert dto.error["code"] == "UnauthorizedOperation"
        assert dto.error["message"] == "not authorized"

    def test_error_field_none_when_no_aws_error(self):
        from datetime import datetime, timezone

        from orb.application.request.dto import RequestDTO
        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-1",
            machine_count=1,
            provider_type="aws",
        )
        dto = RequestDTO.from_domain(request)
        assert dto.error is None

    def test_to_dict_includes_error_block_when_present(self):
        from orb.application.request.dto import RequestDTO

        request = self._make_request_with_error(
            {"code": "AccessDenied", "source": "aws.ec2.run_instances"}
        )
        dto = RequestDTO.from_domain(request)
        d = dto.to_dict()
        assert "error" in d
        assert d["error"]["code"] == "AccessDenied"

    def test_to_dict_omits_error_key_when_no_error(self):
        from datetime import datetime, timezone

        from orb.application.request.dto import RequestDTO
        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="tpl-1",
            machine_count=1,
            provider_type="aws",
        )
        dto = RequestDTO.from_domain(request)
        d = dto.to_dict()
        assert "error" not in d
