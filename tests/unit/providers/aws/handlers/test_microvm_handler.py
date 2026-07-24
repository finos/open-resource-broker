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
        import threading

        lock = threading.Lock()
        call_count = [0]

        def side_effect(**kwargs):
            with lock:
                call_count[0] += 1
                current = call_count[0]
            if current == 2:
                raise Exception("capacity error")
            return {
                "microvmId": f"microvm-{current}",
                "state": "PENDING",
                "endpoint": f"https://{current}.example.com",
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
        """If all launches fail, the exception propagates to the decorator."""
        from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError

        handler.aws_client.microvm_client.run_microvm.side_effect = Exception("all fail")

        request = MagicMock()
        request.requested_count = 2
        request.request_id = "req-004"

        with pytest.raises(AWSInfrastructureError):
            handler.acquire_hosts(request, microvm_template)


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

    def test_check_status_suspending_maps_to_running(self, handler):
        """SUSPENDING MicroVMs are reported as running (in-flight suspend)."""
        handler.aws_client.microvm_client.get_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "SUSPENDING",
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


class TestMicroVMHandlerCheckStatusEdgeCases:
    def test_check_status_no_resource_ids_falls_back_to_provider_data(self, handler):
        """When resource_ids is empty, falls back to provider_data.microvm_ids."""
        handler.aws_client.microvm_client.get_microvm.return_value = {
            "microvmId": "microvm-1",
            "state": "RUNNING",
            "endpoint": "https://1.example.com",
            "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
        }

        request = MagicMock()
        request.resource_ids = []
        request.provider_data = {"microvm_ids": ["microvm-1"]}
        request.requested_count = 1

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "fulfilled"
        assert len(result.instances) == 1

    def test_check_status_no_ids_anywhere(self, handler):
        """When no IDs exist anywhere, return in_progress."""
        request = MagicMock()
        request.resource_ids = []
        request.provider_data = {}
        request.requested_count = 1

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "in_progress"
        assert result.instances == []

    def test_check_status_get_microvm_failure_skips(self, handler):
        """When get_microvm fails for one ID, it's skipped gracefully."""
        from orb.providers.aws.exceptions.aws_exceptions import AWSEntityNotFoundError

        def side_effect(**kwargs):
            if kwargs.get("microvmIdentifier") == "microvm-1":
                raise AWSEntityNotFoundError("not found")
            return {
                "microvmId": "microvm-2",
                "state": "RUNNING",
                "endpoint": "https://2.example.com",
                "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            }

        handler.aws_client.microvm_client.get_microvm.side_effect = side_effect

        request = MagicMock()
        request.resource_ids = ["microvm-1", "microvm-2"]
        request.requested_count = 2

        result = handler.check_hosts_status(request)

        assert len(result.instances) == 1
        assert result.fulfilment.state == "partial"
        assert result.fulfilment.final is True

    def test_check_status_partial_fulfilment(self, handler):
        """When some MicroVMs are running and some terminated, report partial."""
        responses = [
            {
                "microvmId": "microvm-1",
                "state": "RUNNING",
                "endpoint": "https://1.example.com",
                "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            },
            {
                "microvmId": "microvm-2",
                "state": "TERMINATED",
                "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            },
        ]
        handler.aws_client.microvm_client.get_microvm.side_effect = [responses[0], responses[1]]

        request = MagicMock()
        request.resource_ids = ["microvm-1", "microvm-2"]
        request.requested_count = 2

        result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "partial"
        assert result.fulfilment.final is True
        assert result.fulfilment.running_count == 1
        assert result.fulfilment.failed_count == 1

    def test_check_status_all_starting_no_results(self, handler):
        """When all get_microvm calls fail, return in_progress with empty instances."""
        handler.aws_client.microvm_client.get_microvm.side_effect = Exception("timeout")

        request = MagicMock()
        request.resource_ids = ["microvm-1"]
        request.requested_count = 1

        result = handler.check_hosts_status(request)

        assert result.instances == []
        assert result.fulfilment.state == "in_progress"


class TestMicroVMHandlerThrottleRetry:
    @patch(
        "orb.providers.aws.infrastructure.handlers.microvm.handler.random.uniform", return_value=0.5
    )
    @patch("orb.providers.aws.infrastructure.handlers.microvm.handler.time.sleep")
    def test_throttle_retries_then_succeeds(self, mock_sleep, mock_uniform, handler):
        """Throttled calls should retry with exponential backoff and succeed."""
        from botocore.exceptions import ClientError

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "RunMicrovm",
        )

        handler.aws_client.microvm_client.run_microvm.side_effect = [
            throttle_error,
            throttle_error,
            {
                "microvmId": "microvm-1",
                "state": "PENDING",
                "endpoint": "https://1.example.com",
                "imageArn": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            },
        ]

        params = {
            "imageIdentifier": "arn:aws:lambda:us-east-1:123:microvm-image:test",
            "clientToken": "test-token",
        }
        result = handler._run_single_microvm(params)

        assert result["microvmId"] == "microvm-1"
        assert handler.aws_client.microvm_client.run_microvm.call_count == 3

        # Verify full-jitter backoff: uniform(0, min(base*2^attempt, 20))
        assert mock_uniform.call_count == 2
        mock_uniform.assert_any_call(0, 1.0)  # attempt 0: cap = 1.0*2^0 = 1.0
        mock_uniform.assert_any_call(0, 2.0)  # attempt 1: cap = 1.0*2^1 = 2.0

        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.5)  # mocked uniform returns 0.5
        mock_sleep.assert_any_call(0.5)

    @patch(
        "orb.providers.aws.infrastructure.handlers.microvm.handler.random.uniform", return_value=0.5
    )
    @patch("orb.providers.aws.infrastructure.handlers.microvm.handler.time.sleep")
    def test_non_throttle_client_error_raises_immediately(self, mock_sleep, mock_uniform, handler):
        """Non-throttle ClientErrors should not be retried."""
        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "InvalidParameterValue", "Message": "bad param"}},
            "RunMicrovm",
        )
        handler.aws_client.microvm_client.run_microvm.side_effect = error

        params = {"imageIdentifier": "test", "clientToken": "test-token"}

        with pytest.raises(ClientError):
            handler._run_single_microvm(params)

        assert handler.aws_client.microvm_client.run_microvm.call_count == 1
        mock_sleep.assert_not_called()

    @patch(
        "orb.providers.aws.infrastructure.handlers.microvm.handler.random.uniform", return_value=0.5
    )
    @patch("orb.providers.aws.infrastructure.handlers.microvm.handler.time.sleep")
    def test_non_client_error_raises_immediately(self, mock_sleep, mock_uniform, handler):
        """Non-ClientError exceptions propagate without retry."""
        handler.aws_client.microvm_client.run_microvm.side_effect = Exception("validation error")

        params = {"imageIdentifier": "test", "clientToken": "test-token"}

        with pytest.raises(Exception, match="validation error"):
            handler._run_single_microvm(params)

        assert handler.aws_client.microvm_client.run_microvm.call_count == 1
        mock_sleep.assert_not_called()


class TestMicroVMHandlerMisc:
    def test_default_provider_api(self, handler):
        """Verify handler returns correct provider API string."""
        assert handler._default_provider_api() == "MicroVM"

    def test_get_example_templates(self):
        """Verify example templates are returned."""
        from orb.providers.aws.infrastructure.handlers.microvm.handler import MicroVMHandler

        templates = MicroVMHandler.get_example_templates()

        assert len(templates) >= 1
        assert templates[0].provider_api.value == "MicroVM"

    def test_build_run_params_includes_falsy_values(self, handler):
        """Falsy but non-None values (0, empty string) must be passed to the API."""
        template = AWSTemplate(
            template_id="falsy-test",
            name="Falsy Test",
            provider_api="MicroVM",
            image_id="arn:aws:lambda:us-east-1:123:microvm-image:test",
            machine_types={"microvm": 1},
            metadata={
                "maximum_duration_in_seconds": 0,
                "run_hook_payload": "",
                "execution_role_arn": "arn:aws:iam::123:role/Role",
            },
        )

        params = handler._build_run_params(template)

        assert params["maximumDurationInSeconds"] == 0
        assert params["runHookPayload"] == ""
        assert params["executionRoleArn"] == "arn:aws:iam::123:role/Role"

    def test_build_run_params_excludes_none_values(self, handler):
        """None values in metadata should not appear in API params."""
        template = AWSTemplate(
            template_id="none-test",
            name="None Test",
            provider_api="MicroVM",
            image_id="arn:aws:lambda:us-east-1:123:microvm-image:test",
            machine_types={"microvm": 1},
            metadata={
                "maximum_duration_in_seconds": None,
                "execution_role_arn": None,
            },
        )

        params = handler._build_run_params(template)

        assert "maximumDurationInSeconds" not in params
        assert "executionRoleArn" not in params
        assert params == {"imageIdentifier": template.image_id}


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


class TestAWSClientMicroVMProperty:
    def test_microvm_client_lazy_init(self):
        """Verify microvm_client property creates the client on first access."""
        import threading

        from orb.providers.aws.infrastructure.aws_client import AWSClient

        client = object.__new__(AWSClient)
        client._microvm_client = None
        client._cache_lock = threading.RLock()
        client.session = MagicMock()
        client.boto_config = MagicMock()
        client._logger = MagicMock()

        mock_boto_client = MagicMock()
        client.session.client.return_value = mock_boto_client

        # First access creates the client
        result = client.microvm_client
        client.session.client.assert_called_once_with("lambda-microvms", config=client.boto_config)
        assert result is mock_boto_client

        # Second access returns cached
        client.session.client.reset_mock()
        result2 = client.microvm_client
        client.session.client.assert_not_called()
        assert result2 is mock_boto_client


class TestImageResolutionARNSkip:
    def test_arn_does_not_need_resolution(self):
        """ARN-format image IDs should not trigger SSM resolution."""
        from orb.providers.aws.infrastructure.services.aws_image_resolution_service import (
            AWSImageResolutionService,
        )

        assert (
            AWSImageResolutionService.is_resolution_needed_static(
                "arn:aws:lambda:us-east-1:123456789012:microvm-image:my-worker"
            )
            is False
        )

    def test_ami_does_not_need_resolution(self):
        """AMI IDs should not trigger resolution."""
        from orb.providers.aws.infrastructure.services.aws_image_resolution_service import (
            AWSImageResolutionService,
        )

        assert (
            AWSImageResolutionService.is_resolution_needed_static("ami-0123456789abcdef0") is False
        )

    def test_ssm_path_needs_resolution(self):
        """SSM parameter paths should trigger resolution."""
        from orb.providers.aws.infrastructure.services.aws_image_resolution_service import (
            AWSImageResolutionService,
        )

        assert (
            AWSImageResolutionService.is_resolution_needed_static(
                "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
            )
            is True
        )


class TestAWSValidationMicroVM:
    def test_validate_provider_api_accepts_microvm(self):
        """MicroVM should be a valid provider API in the AWS validation adapter."""
        from orb.providers.aws.configuration.validator import AWSHandlerConfig

        config = AWSHandlerConfig()
        assert "MicroVM" in config.capabilities
        assert "micro_vm" in config.types
        assert config.types["micro_vm"] == "MicroVM"


class TestAWSTemplateAdapterMicroVMValidation:
    """MicroVM templates must not be rejected for missing EC2-only fields."""

    def _adapter(self):
        from orb.providers.aws.infrastructure.adapters.template_adapter import (
            AWSTemplateAdapter,
        )

        return AWSTemplateAdapter(
            template_config_manager=MagicMock(),
            aws_client=MagicMock(),
            logger=MagicMock(),
        )

    def _microvm_template(self):
        return AWSTemplate(
            template_id="microvm-validate",
            provider_api="MicroVM",
            image_id="arn:aws:lambda:us-east-1:123456789012:microvm-image:worker",
            max_instances=1,
            machine_types={},
            subnet_ids=[],
            security_group_ids=[],
            metadata={},
        )

    def test_validate_template_accepts_microvm_without_machine_types_or_subnets(self):
        """A valid MicroVM template (no machine_types/subnets) should have no errors."""
        adapter = self._adapter()
        errors = adapter.validate_template(self._microvm_template())

        assert errors == []

    def test_required_fields_skips_machine_types_and_subnets_for_microvm(self):
        """_validate_required_fields must not emit EC2-only errors for MicroVM."""
        adapter = self._adapter()
        errors = adapter._validate_required_fields(self._microvm_template())

        assert not any("Machine types are required" in e for e in errors)
        assert not any("subnet ID is required" in e for e in errors)

    def test_required_fields_still_enforced_for_ec2(self):
        """EC2 templates lacking machine_types/subnets should still be rejected."""
        adapter = self._adapter()
        ec2_template = AWSTemplate(
            template_id="ec2-validate",
            provider_api="EC2Fleet",
            image_id="ami-0abcdef1234567890",
            max_instances=1,
            machine_types={},
            subnet_ids=[],
            security_group_ids=[],
            metadata={},
        )

        errors = adapter._validate_required_fields(ec2_template)

        assert any("Machine types are required" in e for e in errors)
        assert any("subnet ID is required" in e for e in errors)


class TestHostFactoryMicroVMFieldMapping:
    def test_hf_microvm_fields_land_in_metadata(self):
        """HF camelCase MicroVM fields should be routed into metadata via dotted paths."""
        from orb.infrastructure.scheduler.hostfactory.field_mapper import (
            HostFactoryFieldMapper,
        )

        mapper = HostFactoryFieldMapper(provider_type="aws")

        hf_template = {
            "templateId": "microvm-test",
            "providerApi": "MicroVM",
            "imageId": "arn:aws:lambda:us-east-1:123456789012:microvm-image:worker",
            "maxNumber": 10,
            "executionRoleArn": "arn:aws:iam::123456789012:role/MyRole",
            "idlePolicy": {
                "maxIdleDurationSeconds": 3600,
                "suspendedDurationSeconds": 3600,
                "autoResumeEnabled": True,
            },
            "maximumDurationInSeconds": 3600,
            "imageVersion": "2",
            "runHookPayload": '{"key": "value"}',
        }

        mapped = mapper.map_input_fields(hf_template)

        assert mapped["metadata"]["execution_role_arn"] == "arn:aws:iam::123456789012:role/MyRole"
        assert mapped["metadata"]["idle_policy"]["maxIdleDurationSeconds"] == 3600
        assert mapped["metadata"]["maximum_duration_in_seconds"] == 3600
        assert mapped["metadata"]["image_version"] == "2"
        assert mapped["metadata"]["run_hook_payload"] == '{"key": "value"}'

    def test_hf_microvm_template_constructs_aws_template(self):
        """Mapped HF MicroVM fields should survive AWSTemplate construction."""
        from orb.infrastructure.scheduler.hostfactory.field_mapper import (
            HostFactoryFieldMapper,
        )

        mapper = HostFactoryFieldMapper(provider_type="aws")

        hf_template = {
            "templateId": "microvm-hf-test",
            "providerApi": "MicroVM",
            "imageId": "arn:aws:lambda:us-east-1:123456789012:microvm-image:worker",
            "maxNumber": 5,
            "executionRoleArn": "arn:aws:iam::123456789012:role/TestRole",
            "maximumDurationInSeconds": 7200,
        }

        mapped = mapper.map_input_fields(hf_template)

        template = AWSTemplate(
            template_id=mapped.get("template_id", "test"),
            provider_api=mapped.get("provider_api", "MicroVM"),
            image_id=mapped.get("image_id", ""),
            max_instances=mapped.get("max_instances", 1),
            machine_types={},
            subnet_ids=[],
            security_group_ids=[],
            metadata=mapped.get("metadata", {}),
        )

        assert template.metadata["execution_role_arn"] == "arn:aws:iam::123456789012:role/TestRole"
        assert template.metadata["maximum_duration_in_seconds"] == 7200
