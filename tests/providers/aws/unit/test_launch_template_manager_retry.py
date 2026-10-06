"""Unit tests for launch template creation retrying through the handler's
retry wrapper instead of calling the AWS API directly.

create_or_update_launch_template is the first AWS call made by every
acquire operation, so a transient throttling error on either
CreateLaunchTemplate or CreateLaunchTemplateVersion must be retried the
same way the handler's other create/describe calls are retried.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager

REQUEST_ID = "req-00000000-0000-0000-0000-000000000456"


@pytest.fixture(autouse=True)
def _reset_circuit_states():
    """Circuit state is class-level shared — clear it around each test."""
    CircuitBreakerStrategy._circuit_states.clear()
    yield
    CircuitBreakerStrategy._circuit_states.clear()


def _throttle_error(operation_name: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}},
        operation_name,
    )


def _make_handler_with_real_manager() -> tuple[EC2FleetHandler, MagicMock]:
    aws_client = MagicMock()
    aws_client.region_name = "us-east-1"
    logger = MagicMock()
    aws_ops = MagicMock()

    manager_config_port = MagicMock()
    manager_config_port.get_resource_prefix.return_value = ""
    provider_config = MagicMock()
    provider_config.provider_defaults = {}
    manager_config_port.get_provider_config.return_value = provider_config

    launch_template_manager = AWSLaunchTemplateManager(
        aws_client=aws_client,
        logger=logger,
        config_port=manager_config_port,
    )
    # The handler's own config_port only feeds its circuit-breaker config
    # (via _get_circuit_breaker_config); leave it unset so that lookup falls
    # back to its int defaults instead of a mock-typed value.
    handler = EC2FleetHandler(
        aws_client,
        logger,
        aws_ops,
        launch_template_manager,
        config_port=None,
    )
    return handler, aws_client


@pytest.fixture
def sample_request() -> Request:
    return Request(
        request_id=RequestId(value=REQUEST_ID),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="test-template",
        requested_count=2,
    )


@pytest.mark.unit
class TestLaunchTemplateRetryWiring:
    def test_handler_wires_its_retry_method_into_the_manager(self):
        """AWSHandler.__init__ must wire its retry wrapper into the launch
        template manager, since the manager is constructed before the
        handler and has no retry logic of its own."""
        handler, _ = _make_handler_with_real_manager()
        assert handler.launch_template_manager._retry_with_backoff == handler._retry_with_backoff

    def test_create_new_launch_template_retries_on_throttling(self, sample_request):
        handler, aws_client = _make_handler_with_real_manager()
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _throttle_error("CreateLaunchTemplate")
            return {
                "LaunchTemplate": {
                    "LaunchTemplateId": "lt-123",
                    "LatestVersionNumber": 1,
                }
            }

        aws_client.ec2_client.create_launch_template.side_effect = flaky

        aws_template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345678",
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            key_name="test-key",
        )

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep") as sleep:
            result = handler.launch_template_manager._create_new_launch_template(
                "orb-" + REQUEST_ID,
                {"ImageId": "ami-12345678"},
                "client-token-abc",
                sample_request,
                aws_template,
            )

        assert calls["n"] == 2
        assert sleep.call_count >= 1
        assert result.template_id == "lt-123"

    def test_create_new_lt_version_retries_on_throttling(self, sample_request):
        handler, aws_client = _make_handler_with_real_manager()
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _throttle_error("CreateLaunchTemplateVersion")
            return {
                "LaunchTemplateVersion": {
                    "VersionNumber": 2,
                    "LaunchTemplateName": "orb-" + REQUEST_ID,
                }
            }

        aws_client.ec2_client.create_launch_template_version.side_effect = flaky

        aws_template = AWSTemplate(
            template_id="test-template",
            image_id="ami-99999999",
            launch_template_id="lt-existing",
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            key_name="test-key",
        )

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep") as sleep:
            result = handler.launch_template_manager._create_new_lt_version(
                aws_template, sample_request
            )

        assert calls["n"] == 2
        assert sleep.call_count >= 1
        assert result.version == "2"

    def test_direct_call_when_retry_method_not_wired(self, sample_request):
        """A manager used standalone (no handler) still works, calling the
        AWS API directly instead of raising for a missing retry method."""
        aws_client = MagicMock()
        logger = MagicMock()
        config_port = MagicMock()
        config_port.get_resource_prefix.return_value = ""
        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )
        aws_client.ec2_client.create_launch_template.return_value = {
            "LaunchTemplate": {"LaunchTemplateId": "lt-standalone", "LatestVersionNumber": 1}
        }

        aws_template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345678",
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            key_name="test-key",
        )

        result = manager._create_new_launch_template(
            "orb-" + REQUEST_ID,
            {"ImageId": "ami-12345678"},
            "client-token-abc",
            sample_request,
            aws_template,
        )

        assert result.template_id == "lt-standalone"
        aws_client.ec2_client.create_launch_template.assert_called_once()
