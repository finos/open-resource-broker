"""A failed create removes the version ORB added to a user's launch template.

Every handler that launches from a user launch template with overrides adds one
version for the request. When AWS definitively rejects the create (a
``ClientError`` it will not retry), nothing can use that version and it is
deleted. A retryable error that outlasted the retries, a timeout or an open
circuit breaker may follow a lost response that did launch capacity, so the
version is kept. An existing Auto Scaling Group with the request's name may be
the request's own and is never treated as a failure that removes the version.

moto does not implement DeleteLaunchTemplateVersions, so the delete is recorded.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from orb.infrastructure.resilience import CircuitBreakerOpenError
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.providers.aws.mocked.test_cleanup_e2e import (
    _make_aws_client,
    _make_cleanup_config_port,
    _make_logger,
    _make_request,
)

REGION = "eu-west-2"
REQUEST_ID = "req-aaaaaaaa-1111-2222-3333-444444444444"
OTHER_REQUEST_ID = "req-bbbbbbbb-1111-2222-3333-444444444444"
pytestmark = [pytest.mark.moto]

# Handler class, the client attribute and method of the create call.
_HANDLERS: dict[str, tuple[type, str, str]] = {
    "EC2Fleet": (EC2FleetHandler, "ec2_client", "create_fleet"),
    "SpotFleet": (SpotFleetHandler, "ec2_client", "request_spot_fleet"),
    "ASG": (ASGHandler, "autoscaling_client", "create_auto_scaling_group"),
    "RunInstances": (RunInstancesHandler, "ec2_client", "run_instances"),
}


def _client_error(code: str, operation: str = "Create") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class _Setup:
    def __init__(self, provider_api: str, vpc: dict[str, Any]) -> None:
        self.ec2 = boto3.client("ec2", region_name=REGION)
        self.user_lt = self.ec2.create_launch_template(
            LaunchTemplateName=f"user-template-{provider_api.lower()}",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
        )["LaunchTemplate"]["LaunchTemplateId"]

        base_client = _make_aws_client()
        aws_client = MagicMock(spec=AWSClient)
        aws_client.sts_client = base_client.sts_client
        self.recorded = MagicMock(wraps=self.ec2)
        self.recorded.delete_launch_template_versions = MagicMock(
            return_value={"UnsuccessfullyDeletedLaunchTemplateVersions": []}
        )
        aws_client.ec2_client = self.recorded
        self.autoscaling = MagicMock(wraps=base_client.autoscaling_client)
        aws_client.autoscaling_client = self.autoscaling
        self.aws_client = aws_client
        logger = _make_logger()
        config_port = _make_cleanup_config_port()
        handler_cls, self.client_attr, self.create_method = _HANDLERS[provider_api]
        self.handler = handler_cls(
            aws_client=aws_client,
            logger=logger,
            aws_ops=AWSOperations(aws_client, logger, config_port),
            launch_template_manager=AWSLaunchTemplateManager(aws_client, logger, config_port),
            config_port=config_port,
        )
        self.template = AWSTemplate(
            template_id=f"tpl-{provider_api.lower()}",
            name="user-lt",
            provider_api=provider_api,
            machine_types={"t3.micro": 1},
            max_instances=5,
            price_type="spot" if provider_api == "SpotFleet" else "ondemand",
            fleet_role="arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role",
            launch_template_id=self.user_lt,
            # An override: ORB adds a version to the user's template.
            machine_image="ami-87654321",
            # RunInstances puts networking into the version, so the request sets none.
            subnet_ids=[] if provider_api == "RunInstances" else [vpc["subnet_ids"][0]],
            security_group_ids=[] if provider_api == "RunInstances" else [vpc["sg_id"]],
        )

    def versions(self) -> dict[str, str]:
        """``{description: version}`` of the user's launch template."""
        return {
            v.get("VersionDescription", ""): str(v["VersionNumber"])
            for v in self.ec2.describe_launch_template_versions(LaunchTemplateId=self.user_lt)[
                "LaunchTemplateVersions"
            ]
        }

    def add_other_request_version(self) -> str:
        return str(
            self.ec2.create_launch_template_version(
                LaunchTemplateId=self.user_lt,
                SourceVersion="1",
                VersionDescription=f"Override for request {OTHER_REQUEST_ID}",
                LaunchTemplateData={"ImageId": "ami-11111111"},
            )["LaunchTemplateVersion"]["VersionNumber"]
        )

    def fail_create_with(self, error: BaseException) -> Any:
        client = getattr(self.aws_client, self.client_attr)
        return patch.object(client, self.create_method, side_effect=error)

    def acquire(self) -> Any:
        return self.handler.acquire_hosts(_make_request(request_id=REQUEST_ID), self.template)

    def our_version(self) -> str:
        return self.versions()[f"Override for request {REQUEST_ID}"]


@pytest.fixture
def make_setup(moto_aws, moto_vpc_resources) -> Callable[[str], _Setup]:
    def _make(provider_api: str) -> _Setup:
        return _Setup(provider_api, moto_vpc_resources)

    return _make


def _call(setup: _Setup) -> None:
    """Run an acquire that must fail; a failed create either raises or reports it."""
    try:
        result = setup.acquire()
    except Exception:
        # The handlers raise for some of these failures instead of returning one.
        return
    assert isinstance(result, dict) and result.get("success") is False, result


@pytest.mark.parametrize("provider_api", list(_HANDLERS))
@pytest.mark.parametrize("code", ["InvalidParameterValue", "InsufficientInstanceCapacity"])
def test_rejected_create_deletes_exactly_the_requests_version(
    make_setup, provider_api: str, code: str
) -> None:
    setup = make_setup(provider_api)
    other = setup.add_other_request_version()

    with setup.fail_create_with(_client_error(code)):
        _call(setup)

    ours = setup.our_version()
    setup.recorded.delete_launch_template_versions.assert_called_once_with(
        LaunchTemplateId=setup.user_lt, Versions=[ours]
    )
    assert ours not in ("1", other)


@pytest.mark.parametrize("provider_api", list(_HANDLERS))
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ReadTimeoutError(endpoint_url="https://ec2.example"), id="timeout"),
        pytest.param(CircuitBreakerOpenError("ec2", 5, 0.0), id="breaker-open"),
        pytest.param(
            AWSInfrastructureError("retries exhausted"), id="typed-error-without-client-error"
        ),
    ],
)
def test_undecided_failures_keep_the_version(make_setup, provider_api: str, error) -> None:
    setup = make_setup(provider_api)

    with patch.object(setup.handler, "_retry_with_backoff", side_effect=error):
        _call(setup)

    setup.recorded.delete_launch_template_versions.assert_not_called()


@pytest.mark.parametrize("provider_api", list(_HANDLERS))
def test_retryable_error_that_outlasted_the_retries_keeps_the_version(
    make_setup, provider_api: str
) -> None:
    setup = make_setup(provider_api)
    exhausted = AWSInfrastructureError("throttled")
    exhausted.__cause__ = _client_error("Throttling")

    with patch.object(setup.handler, "_retry_with_backoff", side_effect=exhausted):
        _call(setup)

    setup.recorded.delete_launch_template_versions.assert_not_called()


def test_asg_already_exists_never_deletes_the_version(make_setup) -> None:
    setup = make_setup("ASG")

    with (
        setup.fail_create_with(_client_error("AlreadyExists")),
        patch.object(setup.handler, "_asg_belongs_to_request", return_value=False),
    ):
        _call(setup)

    setup.recorded.delete_launch_template_versions.assert_not_called()


def test_instant_fleet_that_returned_only_errors_deletes_the_version(make_setup) -> None:
    setup = make_setup("EC2Fleet")
    setup.template = setup.template.model_copy(update={"fleet_type": "instant"})
    response = {
        "FleetId": "fleet-00000000-0000-0000-0000-000000000001",
        "Errors": [{"ErrorCode": "InsufficientInstanceCapacity", "ErrorMessage": "none"}],
        "Instances": [],
    }

    with patch.object(setup.aws_client.ec2_client, "create_fleet", return_value=response):
        _call(setup)

    setup.recorded.delete_launch_template_versions.assert_called_once_with(
        LaunchTemplateId=setup.user_lt, Versions=[setup.our_version()]
    )


def test_failed_fallback_check_raises_the_spot_error_and_deletes_the_version(make_setup) -> None:
    setup = make_setup("RunInstances")
    setup.template = setup.template.model_copy(
        update={"price_type": "spot", "fallback_to_ondemand": True}
    )
    spot_error = _client_error("InsufficientInstanceCapacity", "RunInstances")
    expected = setup.handler._convert_client_error(spot_error, "run_instances")

    with (
        setup.fail_create_with(spot_error),
        patch.object(
            setup.handler.launch_template_manager,
            "inspect_launch_template",
            side_effect=_client_error("RequestLimitExceeded", "DescribeLaunchTemplateVersions"),
        ) as inspect,
        pytest.raises(type(expected)) as raised,
    ):
        setup.acquire()

    inspect.assert_called_once()

    # The acquire path prefixes the converted error; the spot error text must survive intact.
    assert str(expected) in str(raised.value)
    assert "RequestLimitExceeded" not in str(raised.value)
    setup.recorded.delete_launch_template_versions.assert_called_once_with(
        LaunchTemplateId=setup.user_lt, Versions=[setup.our_version()]
    )
