"""Unit tests for RequestCreationService."""

from unittest.mock import MagicMock, patch

import pytest

from orb.application.services.request_creation_service import RequestCreationService
from orb.domain.request.request_types import RequestType


def _make_command(
    template_id="tmpl-001",
    requested_count=3,
    request_id=None,
    metadata=None,
    dry_run=False,
):
    cmd = MagicMock()
    cmd.template_id = template_id
    cmd.requested_count = requested_count
    cmd.request_id = request_id
    cmd.metadata = metadata or {}
    cmd.dry_run = dry_run
    return cmd


def _make_template(provider_api="EC2Fleet"):
    t = MagicMock()
    t.provider_api = provider_api
    return t


def _make_selection_result(
    provider_type="aws",
    provider_name="aws-prod",
    selection_reason="only provider",
    confidence=1.0,
):
    r = MagicMock()
    r.provider_type = provider_type
    r.provider_name = provider_name
    r.selection_reason = selection_reason
    r.confidence = confidence
    return r


class TestRequestCreationService:
    def setup_method(self):
        self.logger = MagicMock()
        self.svc = RequestCreationService(logger=self.logger)

    def test_create_machine_request_returns_request(self):
        cmd = _make_command()
        template = _make_template()
        selection = _make_selection_result()

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            result = self.svc.create_machine_request(cmd, template, selection)

        assert result is fake_request

    def test_create_machine_request_passes_correct_args(self):
        cmd = _make_command(template_id="tmpl-xyz", requested_count=5)
        template = _make_template(provider_api="CreateFleet")
        selection = _make_selection_result(
            provider_type="aws",
            provider_name="aws-us-east",
            selection_reason="best capacity",
            confidence=0.9,
        )

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            self.svc.create_machine_request(cmd, template, selection)

            call_kwargs = MockRequest.create_new_request.call_args[1]
            assert call_kwargs["request_type"] == RequestType.ACQUIRE
            assert call_kwargs["template_id"] == "tmpl-xyz"
            assert call_kwargs["machine_count"] == 5
            assert call_kwargs["provider_type"] == "aws"
            assert call_kwargs["provider_name"] == "aws-us-east"

    def test_provider_api_set_from_template(self):
        cmd = _make_command()
        template = _make_template(provider_api="CreateFleet")
        selection = _make_selection_result()

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            result = self.svc.create_machine_request(cmd, template, selection)

        assert result.provider_api == "CreateFleet"

    def test_raises_value_error_when_provider_api_is_none(self):
        cmd = _make_command()
        template = _make_template(provider_api=None)
        template.template_id = "tmpl-no-api"
        selection = _make_selection_result()

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            with pytest.raises(ValueError, match="tmpl-no-api"):
                self.svc.create_machine_request(cmd, template, selection)

    def test_metadata_includes_dry_run(self):
        cmd = _make_command(dry_run=True)
        template = _make_template()
        selection = _make_selection_result()

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            self.svc.create_machine_request(cmd, template, selection)

            call_kwargs = MockRequest.create_new_request.call_args[1]
            assert call_kwargs["metadata"]["dry_run"] is True

    def test_metadata_includes_selection_reason(self):
        cmd = _make_command()
        template = _make_template()
        selection = _make_selection_result(selection_reason="lowest cost")

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            self.svc.create_machine_request(cmd, template, selection)

            call_kwargs = MockRequest.create_new_request.call_args[1]
            assert call_kwargs["metadata"]["provider_selection_reason"] == "lowest cost"

    def test_metadata_includes_confidence(self):
        cmd = _make_command()
        template = _make_template()
        selection = _make_selection_result(confidence=0.75)

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            self.svc.create_machine_request(cmd, template, selection)

            call_kwargs = MockRequest.create_new_request.call_args[1]
            assert call_kwargs["metadata"]["provider_confidence"] == 0.75

    def test_logger_called(self):
        cmd = _make_command()
        template = _make_template()
        selection = _make_selection_result()

        fake_request = MagicMock()
        fake_request.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request = MagicMock(return_value=fake_request)
            self.svc.create_machine_request(cmd, template, selection)

        assert self.logger.info.call_count >= 2
