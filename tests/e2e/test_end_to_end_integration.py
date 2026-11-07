"""AWS handlers wired with the provider strategy and launch template manager.

The AWS client and operations are mocks; these tests check the wiring and
the request a handler builds, not live AWS behaviour.
"""

import inspect
from unittest.mock import MagicMock, Mock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSFleetType, ProviderApi
from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

_HANDLERS_BY_API = {
    ProviderApi.SPOT_FLEET: SpotFleetHandler,
    ProviderApi.EC2_FLEET: EC2FleetHandler,
    ProviderApi.RUN_INSTANCES: RunInstancesHandler,
    ProviderApi.ASG: ASGHandler,
}


def _make_handler(handler_class, **overrides):
    kwargs = {
        "aws_client": Mock(),
        "logger": Mock(),
        "aws_ops": Mock(),
        "launch_template_manager": Mock(),
    }
    kwargs.update(overrides)
    return handler_class(**kwargs)


def _acquire_request(template_id: str, machine_count: int = 1) -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id=template_id,
        machine_count=machine_count,
        provider_type="aws",
    )


@pytest.mark.parametrize("api", list(_HANDLERS_BY_API), ids=lambda a: a.value)
def test_provider_strategy_integration(api):
    """The AWS strategy builds from config and accepts a template for every API."""
    strategy = AWSProviderStrategy(
        config=AWSProviderConfig(region="us-west-2", profile="default"),  # type: ignore[call-arg]
        logger=Mock(),
    )
    template = AWSTemplate(
        template_id=f"test-{api.value}",
        provider_api=api,
        machine_types={"t2.micro": 1},
        image_id="ami-12345",
        subnet_ids=["subnet-12345"],
    )

    assert strategy.provider_type == "aws"
    assert template.provider_api == api


def test_launch_template_integration():
    """A handler keeps the launch template manager it was built with."""
    lt_manager = AWSLaunchTemplateManager(aws_client=Mock(), logger=Mock())

    handler = _make_handler(SpotFleetHandler, launch_template_manager=lt_manager)

    assert handler.launch_template_manager is lt_manager


@pytest.mark.parametrize(
    ("api", "handler_class"),
    list(_HANDLERS_BY_API.items()),
    ids=[api.value for api in _HANDLERS_BY_API],
)
def test_handler_routing(api, handler_class):
    """Each provider API has a handler exposing the lifecycle methods."""
    handler = _make_handler(handler_class)

    for method_name in ["acquire_hosts", "check_hosts_status", "release_hosts"]:
        assert callable(getattr(handler, method_name))


def test_domain_model_flow():
    """AWSTemplate keeps its identity and API; acquire_hosts takes request and template."""
    aws_template = AWSTemplate(
        template_id="test-template",
        provider_api=ProviderApi.SPOT_FLEET,
        machine_types={"t2.micro": 1},
        image_id="ami-12345",
        subnet_ids=["subnet-12345"],
        fleet_role="arn:aws:iam::123456789012:role/fleet-role",
    )
    handler = _make_handler(SpotFleetHandler)

    assert aws_template.template_id == "test-template"
    assert aws_template.provider_api == ProviderApi.SPOT_FLEET
    assert list(inspect.signature(handler.acquire_hosts).parameters) == [
        "request",
        "aws_template",
    ]


def test_error_handling_integration():
    """Handlers inherit the base error helpers; AWSValidationError keeps its message."""
    handler = _make_handler(SpotFleetHandler)

    for method_name in ["_convert_client_error", "_retry_with_backoff"]:
        assert hasattr(AWSHandler, method_name)
        assert callable(getattr(handler, method_name))

    with pytest.raises(AWSValidationError, match="^Test validation error$"):
        raise AWSValidationError("Test validation error")


def test_performance_metrics_integration():
    """The removed per-handler metrics methods stay removed."""
    for method_name in ["get_metrics", "_record_success_metrics", "_record_failure_metrics"]:
        assert not hasattr(AWSHandler, method_name)


def test_full_end_to_end_flow():
    """acquire_hosts creates a launch template and a Spot Fleet built from it."""
    aws_template = AWSTemplate(
        template_id="e2e-test-template",
        provider_api=ProviderApi.SPOT_FLEET,
        machine_types={"t2.micro": 1},
        image_id="ami-12345",
        subnet_ids=["subnet-12345"],
        security_group_ids=["sg-12345"],
        fleet_role=(
            "arn:aws:iam::123456789012:role/aws-service-role/"
            "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
        ),
        fleet_type=AWSFleetType.REQUEST.value,
    )
    request = _acquire_request("e2e-test-template")

    aws_client = Mock()
    aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
    aws_client.ec2_client.request_spot_fleet.return_value = {"SpotFleetRequestId": "sfr-12345"}
    aws_ops = Mock()
    aws_ops.execute_with_standard_error_handling = Mock(
        side_effect=lambda operation, operation_name, context: operation()
    )
    lt_manager = AWSLaunchTemplateManager(aws_client=aws_client, logger=Mock())
    lt_manager.create_or_update_launch_template = Mock(
        return_value=Mock(template_id="lt-12345", version="1")
    )
    config_port = MagicMock()
    config_port.get_provider_config.return_value = None

    handler = _make_handler(
        SpotFleetHandler,
        aws_client=aws_client,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )

    result = handler.acquire_hosts(request, aws_template)

    assert result["success"] is True
    assert result["resource_ids"] == ["sfr-12345"]
    lt_manager.create_or_update_launch_template.assert_called_once_with(aws_template, request)
    aws_ops.execute_with_standard_error_handling.assert_called_once()
    fleet_config = aws_client.ec2_client.request_spot_fleet.call_args.kwargs[
        "SpotFleetRequestConfig"
    ]
    assert fleet_config["LaunchTemplateConfigs"][0]["LaunchTemplateSpecification"] == {
        "LaunchTemplateId": "lt-12345",
        "Version": "1",
    }
    assert fleet_config["TargetCapacity"] == 1
