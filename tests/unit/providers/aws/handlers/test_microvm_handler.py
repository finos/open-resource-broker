"""Unit tests for the MicroVM handler."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.microvm.handler import MicroVMHandler


@pytest.fixture
def mock_aws_client():
    client = MagicMock()
    client.region_name = "us-east-1"
    client.microvm_client = MagicMock()
    client.ec2_client = MagicMock()
    return client


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def handler(mock_aws_client, mock_logger):
    return MicroVMHandler(
        aws_client=mock_aws_client,
        logger=mock_logger,
        aws_ops=MagicMock(),
        launch_template_manager=MagicMock(),
        config_port=MagicMock(),
    )


@pytest.fixture
def microvm_template():
    return AWSTemplate(
        template_id="test-microvm",
        name="Test MicroVM",
        description="Test template",
        provider_api="MicroVM",
        image_id="arn:aws:lambda:us-east-1:123456789012:microvm-image:test",
        machine_types={},
        max_instances=10,
        subnet_ids=[],
        security_group_ids=[],
        metadata={
            "image_version": "1",
            "execution_role_arn": "arn:aws:iam::123456789012:role/TestRole",
            "idle_policy": {
                "maxIdleDurationSeconds": 300,
                "suspendedDurationSeconds": 60,
                "autoResumeEnabled": True,
            },
            "maximum_duration_in_seconds": 3600,
            "run_hook_payload": '{"key": "value"}',
        },
    )


class TestMicroVMHandlerAcquire:
    def test_acquire_launches_microvms_in_parallel(self, handler, microvm_template):
        """Verify acquire calls run_microvm for each requested machine."""
        handler.aws_client.microvm_client.run_microvm.return_value = {
            "microvmId": "microvm-abc123",
            "state": "PENDING",
            "endpoint": "https://abc123.lambda-microvms.us-east-1.on.aws",
            "imageArn": microvm_template.image_id,
            "imageVersion": "1",
            "startedAt": datetime(2026, 7, 13, tzinfo=timezone.utc),
        }

        request = MagicMock()
        request.requested_count = 3
        request.request_id = "req-001"

        result = handler.acquire_hosts(request, microvm_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 3
        assert len(result["instances"]) == 3
        assert result["provider_data"]["resource_type"] == "microvm"
        assert result["provider_data"]["requires_async_polling"] is True
        assert handler.aws_client.microvm_client.run_microvm.call_count == 3

    def test_acquire_builds_correct_params(self, handler, microvm_template):
        """Verify the run_microvm params are built from template fields + metadata."""
        handler.aws_client.microvm_client.run_microvm.return_value = {
            "microvmId": "microvm-xyz",
            "state": "PENDING",
            "endpoint": "https://xyz.example.com",
            "imageArn": microvm_template.image_id,
        }

        request = MagicMock()
        request.requested_count = 1
        request.request_id = "req-002"

        handler.acquire_hosts(request, microvm_template)

        call_kwargs = handler.aws_client.microvm_client.run_microvm.call_args[1]
        assert call_kwargs["imageIdentifier"] == microvm_template.image_id
        assert call_kwargs["imageVersion"] == "1"
        assert call_kwargs["executionRoleArn"] == "arn:aws:iam::123456789012:role/TestRole"
        assert call_kwargs["idlePolicy"]["maxIdleDurationSeconds"] == 300
        assert call_kwargs["maximumDurationInSeconds"] == 3600
        assert call_kwargs["runHookPayload"] == '{"key": "value"}'
        assert "clientToken" in call_kwargs

    def test_acquire_partial_failure(self, handler, microvm_template):
        """If some launches fail, result still contains successful ones."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("capacity error")
            return {
                "microvmId": f"microvm-{call_count[0]}",
                "state": "PENDING",
                "endpoint": f"https://{call_count[0]}.example.com",
                "imageArn": microvm_template.image_id,
            }

        handler.aws_client.microvm_client.run_microvm.side_effect = side_effect

        request = MagicMock()
        request.requested_count = 3
        request.request_id = "req-003"

        result = handler.acquire_hosts(request, microvm_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 2

    def test_acquire_total_failure(self, handler, microvm_template):
        """If all launches fail, result is unsuccessful."""
        handler.aws_client.microvm_client.run_microvm.side_effect = Exception("all fail")

        request = MagicMock()
        request.requested_count = 2
        request.request_id = "req-004"

        result = handler.acquire_hosts(request, microvm_template)

        assert result["success"] is False
        assert result["resource_ids"] == []


class TestMicroVMHandlerCheckStatus:
    def test_check_status_all_running(self, handler):
        """Verify fulfilled state when all MicroVMs are RUNNING."""
        handler.aws_client.microvm_client.get_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "RUNNING",
            "endpoint": "https://1.example.com",
            "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            "startedAt": datetime(2026, 7, 13, tzinfo=timezone.utc),
        }

        request = MagicMock()
        request.resource_ids = ["microvm-1", "microvm-2"]
        request.requested_count = 2

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "fulfilled"
        assert len(result.instances) == 2
        assert result.instances[0]["status"] == "running"
        assert result.instances[0]["provider_data"]["endpoint"] == "https://1.example.com"

    def test_check_status_pending(self, handler):
        """Verify in_progress when MicroVMs are still PENDING."""
        handler.aws_client.microvm_client.get_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "PENDING",
            "endpoint": None,
            "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
        }

        request = MagicMock()
        request.resource_ids = ["microvm-1"]
        request.requested_count = 1

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "in_progress"
        assert result.instances[0]["status"] == "pending"

    def test_check_status_suspended_maps_to_running(self, handler):
        """SUSPENDED MicroVMs are reported as running (auto-resume)."""
        handler.aws_client.microvm_client.get_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "SUSPENDED",
            "endpoint": "https://1.example.com",
            "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            "startedAt": datetime(2026, 7, 13, tzinfo=timezone.utc),
        }

        request = MagicMock()
        request.resource_ids = ["microvm-1"]
        request.requested_count = 1

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "fulfilled"
        assert result.instances[0]["status"] == "running"

    def test_check_status_all_terminated(self, handler):
        """Verify failed state when all MicroVMs are TERMINATED."""
        handler.aws_client.microvm_client.get_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "TERMINATED",
            "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
        }

        request = MagicMock()
        request.resource_ids = ["microvm-1"]
        request.requested_count = 1

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "failed"


class TestMicroVMHandlerRelease:
    def test_release_terminates_all(self, handler):
        """Verify release calls terminate_microvm for each ID."""
        handler.aws_client.microvm_client.terminate_microvm.return_value = {}

        handler.release_hosts(["microvm-1", "microvm-2", "microvm-3"])

        assert handler.aws_client.microvm_client.terminate_microvm.call_count == 3

    def test_release_empty_list_is_noop(self, handler):
        """Empty machine_ids should not call any API."""
        handler.release_hosts([])
        handler.aws_client.microvm_client.terminate_microvm.assert_not_called()

    def test_release_partial_failure_raises(self, handler):
        """If some terminations fail, raise with details."""
        from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError

        def side_effect(**kwargs):
            if kwargs.get("microvmIdentifier") == "microvm-2":
                raise AWSInfrastructureError("termination failed")
            return {}

        handler.aws_client.microvm_client.terminate_microvm.side_effect = side_effect

        with pytest.raises(AWSInfrastructureError):
            handler.release_hosts(["microvm-1", "microvm-2"])


class TestMicroVMHandlerCancel:
    def test_cancel_success(self, handler):
        """Verify cancel terminates the MicroVM."""
        handler.aws_client.microvm_client.terminate_microvm.return_value = {}

        result = handler.cancel_resource("microvm-abc", "req-001")

        assert result["status"] == "success"
        handler.aws_client.microvm_client.terminate_microvm.assert_called_once_with(
            microvmIdentifier="microvm-abc"
        )

    def test_cancel_failure(self, handler):
        """Verify cancel returns error dict on failure."""
        handler.aws_client.microvm_client.terminate_microvm.side_effect = Exception("boom")

        result = handler.cancel_resource("microvm-abc", "req-001")

        assert result["status"] == "error"


class TestMicroVMHandlerValidation:
    def test_missing_image_id_raises(self, handler):
        """Template without image_id should fail validation."""
        from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError

        template = AWSTemplate(
            template_id="bad-template",
            name="Bad",
            provider_api="MicroVM",
            image_id="",
            machine_types={},
            subnet_ids=[],
            security_group_ids=[],
        )

        with pytest.raises(AWSValidationError):
            handler.acquire_hosts(MagicMock(), template)

    def test_valid_template_passes(self, handler):
        """Template with image_id should pass validation."""
        template = AWSTemplate(
            template_id="good-template",
            name="Good",
            provider_api="MicroVM",
            image_id="arn:aws:lambda:us-east-1:123:microvm-image:test",
            machine_types={},
            subnet_ids=[],
            security_group_ids=[],
        )

        handler.aws_client.microvm_client.run_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "PENDING",
            "endpoint": "https://1.example.com",
            "imageArn": template.image_id,
        }

        request = MagicMock()
        request.requested_count = 1
        request.request_id = "req-001"

        result = handler.acquire_hosts(request, template)
        assert result["success"] is True
