"""Unit tests for SpotFleetHandler."""

from typing import Any
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from orb.domain.base.provider_fulfilment import (
    CheckHostsStatusResult,
    FulfilmentState,
    ProviderFulfilment,
)
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler


def _make_handler() -> Any:
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler: Any = SpotFleetHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _make_request(resource_ids, requested_count: int = 3):
    request = MagicMock()
    request.request_id = "req-spot-123"
    request.resource_ids = resource_ids
    request.metadata = {}
    request.requested_count = requested_count
    return request


def _make_client_error(code="InternalError"):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DescribeSpotFleetRequests")


def _formatted_instances(instance_ids, resource_id="sfr-test"):
    """Return already-formatted instance dicts."""
    return [
        {
            "instance_id": iid,
            "resource_id": resource_id,
            "status": "running",
            "private_ip": f"10.0.1.{i}",
            "public_ip": None,
            "launch_time": None,
            "instance_type": "t3.medium",
            "image_id": "ami-456",
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
        }
        for i, iid in enumerate(instance_ids)
    ]


def _fleet_status_result(
    instance_ids,
    resource_id: str = "sfr-test",
    state: FulfilmentState = "fulfilled",
    target_units: int | None = None,
    fulfilled_units: int | None = None,
):
    """Build a CheckHostsStatusResult for mocking _get_spot_fleet_status."""
    instances = _formatted_instances(instance_ids, resource_id)
    n = len(instance_ids)
    return CheckHostsStatusResult(
        instances=instances,
        fulfilment=ProviderFulfilment(
            state=state,
            message="test",
            target_units=target_units if target_units is not None else n,
            fulfilled_units=fulfilled_units if fulfilled_units is not None else n,
            running_count=n,
            pending_count=0,
            failed_count=0,
        ),
    )


class TestSpotFleetHandlerCheckHostsStatus:
    def test_check_hosts_status_all_active(self):
        """All instances active → CheckHostsStatusResult with instances and fulfilled state."""
        handler = _make_handler()
        instance_ids = ["i-s1", "i-s2", "i-s3"]
        request = _make_request(["sfr-111"], requested_count=len(instance_ids))

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            return_value=_fleet_status_result(instance_ids, "sfr-111"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 3
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "fulfilled"

    def test_check_hosts_status_partial_active(self):
        """AWS only returns active instances — terminated excluded; result reflects active set."""
        handler = _make_handler()
        active_ids = ["i-active1", "i-active2"]
        request = _make_request(["sfr-222"], requested_count=4)

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            return_value=_fleet_status_result(
                active_ids, "sfr-222", state="in_progress", target_units=4, fulfilled_units=2
            ),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 2
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(active_ids)
        assert isinstance(result.fulfilment, ProviderFulfilment)

    def test_check_hosts_status_fleet_not_found(self):
        """_get_spot_fleet_status raises → error logged, skipped; result has empty instances."""
        handler = _make_handler()
        request = _make_request(["sfr-missing"])

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            side_effect=AWSInfrastructureError("Fleet not found"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert result.instances == []
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "in_progress"

    def test_check_hosts_status_aws_error(self):
        """Exception inside per-fleet loop → logged and skipped; result has empty instances."""
        handler = _make_handler()
        request = _make_request(["sfr-err"])

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            side_effect=AWSInfrastructureError("AWS error"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert result.instances == []
        assert isinstance(result.fulfilment, ProviderFulfilment)

    def test_check_hosts_status_no_resource_ids(self):
        """Empty resource_ids → returns CheckHostsStatusResult with empty instances, in_progress."""
        handler = _make_handler()
        request = _make_request([])

        with patch.object(handler, "_get_spot_fleet_status") as mock_get:
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert result.instances == []
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "in_progress"
        mock_get.assert_not_called()

    def test_check_hosts_status_returns_correct_count(self):
        """Verify instance count in result matches instances returned."""
        handler = _make_handler()
        instance_ids = ["i-c1", "i-c2", "i-c3", "i-c4"]
        request = _make_request(["sfr-cnt"], requested_count=len(instance_ids))

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            return_value=_fleet_status_result(instance_ids, "sfr-cnt"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 4
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "fulfilled"
        assert result.fulfilment.target_units == 4
        assert result.fulfilment.fulfilled_units == 4

    def test_check_hosts_status_preserves_instance_ids(self):
        """Instance IDs in result.instances match input."""
        handler = _make_handler()
        instance_ids = ["i-spot-preserve1", "i-spot-preserve2"]
        request = _make_request(["sfr-ids"], requested_count=len(instance_ids))

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            return_value=_fleet_status_result(instance_ids, "sfr-ids"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)
        assert isinstance(result.fulfilment, ProviderFulfilment)

    def test_check_hosts_status_no_active_instances(self):
        """Fleet exists but has no active instances → empty instances, in_progress."""
        handler = _make_handler()
        request = _make_request(["sfr-empty"])

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            return_value=CheckHostsStatusResult(
                instances=[],
                fulfilment=ProviderFulfilment(
                    state="in_progress",
                    message="Spot Fleet waiting for instances",
                    target_units=3,
                    fulfilled_units=0,
                    running_count=0,
                    pending_count=0,
                    failed_count=0,
                ),
            ),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert result.instances == []
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "in_progress"

    def test_check_hosts_status_multiple_fleets(self):
        """Multiple fleet IDs → aggregates instances from all; combined fulfilment state."""
        handler = _make_handler()
        request = _make_request(["sfr-A", "sfr-B"], requested_count=3)

        ids_a = ["i-sa1", "i-sa2"]
        ids_b = ["i-sb1"]

        def get_status_side_effect(fleet_id, request_id, requested_count):
            if fleet_id == "sfr-A":
                return _fleet_status_result(ids_a, "sfr-A")
            return _fleet_status_result(ids_b, "sfr-B")

        with patch.object(handler, "_get_spot_fleet_status", side_effect=get_status_side_effect):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 3
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == {"i-sa1", "i-sa2", "i-sb1"}
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "fulfilled"

    def test_check_hosts_status_state_filtering_is_strict(self):
        """Only instances returned by _get_spot_fleet_status appear in result."""
        handler = _make_handler()
        active_ids = ["i-spot-strict-active"]
        request = _make_request(["sfr-strict"], requested_count=1)

        with patch.object(
            handler,
            "_get_spot_fleet_status",
            return_value=_fleet_status_result(active_ids, "sfr-strict"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 1
        assert result.instances[0]["instance_id"] == "i-spot-strict-active"
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.fulfilment.state == "fulfilled"


class TestSpotFleetHandlerNameTag:
    def test_fleet_config_name_tag_uses_config_prefix(self):
        """Name tag in SpotFleet config uses config_port prefix, not hardcoded 'hf-'."""
        aws_client = MagicMock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        logger = MagicMock()
        aws_ops = MagicMock()
        launch_template_manager = MagicMock()
        config_port = MagicMock()
        config_port.get_resource_prefix.return_value = "myorg-"

        handler = SpotFleetHandler(
            aws_client, logger, aws_ops, launch_template_manager, config_port=config_port
        )

        template = MagicMock()
        template.fleet_type = MagicMock()
        template.fleet_type.value = "request"
        template.tags = {}
        template.price_type = "spot"
        template.allocation_strategy = None
        template.max_price = None
        template.machine_types = {"m5.large": 1}
        template.machine_types_ondemand = None
        template.machine_types_priority = None
        template.subnet_ids = ["subnet-abc"]
        template.context = None
        template.template_id = "tmpl-sf-001"
        template.fleet_role = "arn:aws:iam::123456789012:role/SpotFleetRole"
        template.get_instance_requirements_payload = MagicMock(return_value=None)

        request = MagicMock()
        request.request_id = "req-sf-001"
        request.requested_count = 2

        with patch.object(
            handler._config_builder,
            "_calculate_capacity_distribution",
            return_value={"target_capacity": 2, "on_demand_count": 0},
        ):
            with patch(
                "orb.providers.aws.infrastructure.handlers.shared.fleet_override_builder.build_spot_fleet_overrides",
                return_value=[],
            ):
                fleet_config = handler._config_builder._build_legacy(
                    template, request, "lt-abc", "$Default"
                )

        all_tags = []
        for ts in fleet_config.get("TagSpecifications", []):
            all_tags.extend(ts.get("Tags", []))

        name_tags = [t for t in all_tags if t["Key"] == "Name"]
        assert name_tags, "No Name tag found in TagSpecifications"
        for name_tag in name_tags:
            assert name_tag["Value"] == "myorg-req-sf-001"
            assert "hf-" not in name_tag["Value"]


def _make_handler_with_config_port(config_port):
    aws_client = MagicMock()
    aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    return SpotFleetHandler(
        aws_client, logger, aws_ops, launch_template_manager, config_port=config_port
    )


class _FakeProvider:
    """Simple stand-in for ProviderInstanceConfig in unit tests."""

    def __init__(self, name, config=None, template_defaults=None):
        self.name = name
        self.config = config or {}
        self.template_defaults = template_defaults or {}


class _FakeProviderConfig:
    """Simple stand-in for ProviderConfig in unit tests."""

    def __init__(self, providers):
        self.providers = providers


def _make_provider_config(fleet_role_in_config=None, fleet_role_in_template_defaults=None):
    """Build a fake ProviderConfig with fleet_role in the specified location."""
    config = {}
    template_defaults = {}
    if fleet_role_in_config:
        config["fleet_role"] = fleet_role_in_config
    if fleet_role_in_template_defaults:
        template_defaults["fleet_role"] = fleet_role_in_template_defaults
    provider = _FakeProvider(
        "aws_test_eu-west-2", config=config, template_defaults=template_defaults
    )
    return _FakeProviderConfig(providers=[provider])


FULL_SPOT_FLEET_ARN = (
    "arn:aws:iam::740606666446:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)


class TestResolveFleetRole:
    """Tests for SpotFleetHandler._resolve_fleet_role."""

    def _make_template(self, fleet_role=None):
        return AWSTemplate(
            template_id="sf-test",
            image_id="ami-abc123",
            instance_type="t3.medium",
            subnet_ids=["subnet-abc"],
            security_group_ids=["sg-abc"],
            fleet_role=fleet_role,
            fleet_type="request",
            provider_api="SpotFleet",
        )

    def test_fleet_role_already_set_on_template_is_unchanged(self):
        """When fleet_role is already on the template, _resolve_fleet_role returns it unchanged."""
        config_port = MagicMock()
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=FULL_SPOT_FLEET_ARN)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role == FULL_SPOT_FLEET_ARN
        config_port.get_provider_config.assert_not_called()

    def test_fleet_role_resolved_from_provider_config(self):
        """When fleet_role is absent from template but present in provider config, it is injected."""
        config_port = MagicMock()
        config_port.get_provider_config.return_value = _make_provider_config(
            fleet_role_in_config=FULL_SPOT_FLEET_ARN
        )
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role == FULL_SPOT_FLEET_ARN

    def test_fleet_role_not_found_returns_template_unchanged(self):
        """When fleet_role is absent from both template and provider config, template is unchanged."""
        config_port = MagicMock()
        config_port.get_provider_config.return_value = _make_provider_config()
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role is None

    def test_no_config_port_returns_template_unchanged(self):
        """When config_port is None, template is returned unchanged."""
        handler = _make_handler_with_config_port(None)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role is None

    def test_first_provider_with_fleet_role_is_used(self):
        """The first provider entry that has a fleet_role is used when no template role is set."""
        config_port = MagicMock()
        config_port.get_provider_config.return_value = _make_provider_config(
            fleet_role_in_config=FULL_SPOT_FLEET_ARN
        )
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role == FULL_SPOT_FLEET_ARN

    def test_ec2fleet_role_arn_converted_to_spotfleet_arn(self):
        """An EC2Fleet service-linked role ARN is converted to the SpotFleet equivalent."""
        ec2fleet_arn = (
            "arn:aws:iam::123456789012:role/aws-service-role/"
            "ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet"
        )
        config_port = MagicMock()
        config_port.get_provider_config.return_value = _make_provider_config(
            fleet_role_in_config=ec2fleet_arn
        )
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role is not None
        assert "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet" in result.fleet_role
        assert "123456789012" in result.fleet_role

    def test_short_role_name_expanded_to_full_arn(self):
        """The short name 'AWSServiceRoleForEC2SpotFleet' is expanded to a full ARN."""
        config_port = MagicMock()
        config_port.get_provider_config.return_value = _make_provider_config(
            fleet_role_in_config="AWSServiceRoleForEC2SpotFleet"
        )
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role is not None
        assert result.fleet_role.startswith("arn:aws:iam::")
        assert "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet" in result.fleet_role

    def test_get_provider_config_returns_none_leaves_template_unchanged(self):
        """When get_provider_config returns None, template is returned unchanged."""
        config_port = MagicMock()
        config_port.get_provider_config.return_value = None
        handler = _make_handler_with_config_port(config_port)
        template = self._make_template(fleet_role=None)

        result = handler._resolve_fleet_role(template)

        assert result.fleet_role is None
