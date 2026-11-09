"""RunInstances retries a spot launch as on-demand when ``fallback_to_ondemand`` is set.

A spot launch that fails for lack of spot capacity is relaunched on-demand from
the same template with ``price_type`` ondemand, on the legacy and default spec
paths. The fallback is flagged on the acquire result. It does not run when the
setting is off, for errors other than spot capacity, or when the market type is
set outside ORB's template (a provider API spec or the launch template), since
RunInstances cannot override it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError

from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LTInspection,
    LTNetworkingState,
)
from orb.providers.aws.infrastructure.services.aws_native_spec_service import AWSNativeSpecService
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.providers.aws.mocked.test_run_instances_gaps import (
    _make_aws_client,
    _make_config_port,
    _make_launch_template_manager,
    _make_logger,
    _make_request,
    _run_instances_template,
)

REGION = "eu-west-2"
pytestmark = [pytest.mark.moto]


def _handler(*, spec: bool, lt_manager: Any = None) -> RunInstancesHandler:
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port()
    config_port.get_package_info.return_value = {"name": "open-resource-broker"}
    native_service = None
    if spec:
        native = MagicMock()
        native.spec_renderer = JinjaSpecRenderer(logger)
        native.is_native_spec_enabled.return_value = False
        native_service = AWSNativeSpecService(native, config_port)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=AWSOperations(aws_client, logger, config_port),
        launch_template_manager=lt_manager or _make_launch_template_manager(aws_client),
        config_port=config_port,
        aws_native_spec_service=native_service,
    )


class _SpotIsOut:
    """run_instances wrapper: spot launches fail with ``code``; others reach moto."""

    def __init__(self, handler: RunInstancesHandler, code: str = "InsufficientInstanceCapacity"):
        self.calls: list[dict[str, Any]] = []
        self._real = handler.aws_client.ec2_client.run_instances
        self._code = code

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("InstanceMarketOptions", {}).get("MarketType") == "spot":
            raise ClientError(
                {"Error": {"Code": self._code, "Message": "no spot capacity"}}, "RunInstances"
            )
        return self._real(**kwargs)


def _acquire(handler: RunInstancesHandler, template: AWSTemplate, spot_out: _SpotIsOut) -> Any:
    """The acquire result, or ``{"success": False}`` when the launch raised."""
    with patch.object(handler.aws_client.ec2_client, "run_instances", side_effect=spot_out):
        try:
            return handler.acquire_hosts(_make_request(request_id="req-fallback"), template)
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def _lifecycles(instance_ids: list[str]) -> set[Any]:
    reservations = boto3.client("ec2", region_name=REGION).describe_instances(
        InstanceIds=instance_ids
    )["Reservations"]
    return {i.get("InstanceLifecycle") for r in reservations for i in r["Instances"]}


def _spot_template(vpc: dict[str, Any], **kwargs: Any) -> AWSTemplate:
    return _run_instances_template(vpc["subnet_ids"][0], vpc["sg_id"], price_type="spot", **kwargs)


@pytest.mark.parametrize("spec", [False, True], ids=["legacy", "default-spec"])
def test_spot_capacity_shortfall_launches_on_demand(moto_aws, moto_vpc_resources, spec) -> None:
    handler = _handler(spec=spec)
    spot_out = _SpotIsOut(handler)

    result = _acquire(
        handler, _spot_template(moto_vpc_resources, fallback_to_ondemand=True), spot_out
    )

    assert result["success"] is True, result
    assert len(spot_out.calls) == 2
    first, retry = spot_out.calls
    assert first["InstanceMarketOptions"]["MarketType"] == "spot"
    assert "InstanceMarketOptions" not in retry
    assert retry["ClientToken"] != first["ClientToken"]
    assert retry["LaunchTemplate"] == first["LaunchTemplate"]
    assert result["provider_data"]["capacity_fallback"] == {
        "from": "spot",
        "to": "ondemand",
        "reason": "InsufficientInstanceCapacity",
    }
    assert _lifecycles(result["instance_ids"]) == {None}
    events = [
        c.kwargs.get("extra", {}).get("event") for c in handler._logger.warning.call_args_list
    ]
    assert "aws.spot_fallback" in events


@pytest.mark.parametrize("spec", [False, True], ids=["legacy", "default-spec"])
def test_without_the_setting_the_spot_failure_stands(moto_aws, moto_vpc_resources, spec) -> None:
    handler = _handler(spec=spec)
    spot_out = _SpotIsOut(handler)

    result = _acquire(handler, _spot_template(moto_vpc_resources), spot_out)

    assert result["success"] is False
    assert len(spot_out.calls) == 1


def test_errors_other_than_spot_capacity_do_not_fall_back(moto_aws, moto_vpc_resources) -> None:
    handler = _handler(spec=False)
    spot_out = _SpotIsOut(handler, code="InvalidParameterValue")

    result = _acquire(
        handler, _spot_template(moto_vpc_resources, fallback_to_ondemand=True), spot_out
    )

    assert result["success"] is False
    assert len(spot_out.calls) == 1


def test_on_demand_templates_never_fall_back(moto_aws, moto_vpc_resources) -> None:
    handler = _handler(spec=False)
    template = _run_instances_template(
        moto_vpc_resources["subnet_ids"][0],
        moto_vpc_resources["sg_id"],
        fallback_to_ondemand=True,
    )
    error = ClientError(
        {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "none"}}, "RunInstances"
    )
    with patch.object(handler.aws_client.ec2_client, "run_instances", side_effect=error) as call:
        with pytest.raises(Exception, match="InsufficientInstanceCapacity"):
            handler.acquire_hosts(_make_request(), template)

    assert call.call_count == 1


def test_provider_api_spec_that_sets_the_market_type_skips_the_fallback(
    moto_aws, moto_vpc_resources
) -> None:
    handler = _handler(spec=True)
    spot_out = _SpotIsOut(handler)
    original = handler._create_run_instances_params

    def spec_sets_market(**kwargs: Any) -> dict[str, Any]:
        params = original(**kwargs)
        params["InstanceMarketOptions"] = {"MarketType": "spot"}
        return params

    with patch.object(handler, "_create_run_instances_params", side_effect=spec_sets_market):
        result = _acquire(
            handler, _spot_template(moto_vpc_resources, fallback_to_ondemand=True), spot_out
        )

    assert result["success"] is False
    assert len(spot_out.calls) == 1
    warnings = " ".join(str(c.args[0]) for c in handler._logger.warning.call_args_list)
    assert "provider API spec" in warnings


def _user_lt_handler(market_options: bool) -> tuple[RunInstancesHandler, str]:
    ec2 = boto3.client("ec2", region_name=REGION)
    data: dict[str, Any] = {"ImageId": "ami-12345678", "InstanceType": "t3.micro"}
    if market_options:
        data["InstanceMarketOptions"] = {"MarketType": "spot"}
    lt_id = ec2.create_launch_template(
        LaunchTemplateName=f"user-lt-{market_options}", LaunchTemplateData=data
    )["LaunchTemplate"]["LaunchTemplateId"]
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port()
    handler = RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=AWSOperations(aws_client, logger, config_port),
        launch_template_manager=AWSLaunchTemplateManager(aws_client, logger, config_port),
        config_port=config_port,
    )
    return handler, lt_id


def _user_lt_template(lt_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-user-lt",
        provider_api="RunInstances",
        max_instances=2,
        price_type="spot",
        fallback_to_ondemand=True,
        launch_template_id=lt_id,
    )


def test_launch_template_that_sets_spot_skips_the_fallback(moto_aws) -> None:
    from orb.providers.aws.infrastructure.launch_template.manager import LaunchTemplateResult

    handler, lt_id = _user_lt_handler(market_options=True)
    spot_out = _SpotIsOut(handler)
    # Launch the user's own version, which sets the spot market type.
    own_version = LaunchTemplateResult(
        template_id=lt_id, version="1", template_name="user-lt-True", is_new_template=False
    )

    with patch.object(
        handler.launch_template_manager,
        "create_or_update_launch_template",
        return_value=own_version,
    ):
        result = _acquire(handler, _user_lt_template(lt_id), spot_out)

    assert result["success"] is False
    assert len(spot_out.calls) == 1


def test_launch_template_without_market_options_falls_back(moto_aws) -> None:
    handler, lt_id = _user_lt_handler(market_options=False)
    spot_out = _SpotIsOut(handler)

    result = _acquire(handler, _user_lt_template(lt_id), spot_out)

    assert result["success"] is True, result
    assert len(spot_out.calls) == 2


def test_unreadable_launch_template_skips_the_fallback(moto_aws) -> None:
    handler, lt_id = _user_lt_handler(market_options=False)
    spot_out = _SpotIsOut(handler)
    denied = LTInspection(
        networking=LTNetworkingState.UNKNOWN_UNAUTHORIZED, sets_market_options=None
    )

    with patch.object(
        handler.launch_template_manager, "inspect_launch_template", return_value=denied
    ):
        result = _acquire(handler, _user_lt_template(lt_id), spot_out)

    assert result["success"] is False
    assert len(spot_out.calls) == 1
