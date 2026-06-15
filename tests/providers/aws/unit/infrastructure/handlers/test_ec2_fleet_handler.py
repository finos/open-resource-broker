"""Unit tests for EC2FleetHandler.check_hosts_status."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler


def _make_handler():
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler = EC2FleetHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _make_request(resource_ids, metadata=None):
    request = MagicMock()
    request.request_id = "req-ec2fleet-123"
    request.resource_ids = resource_ids
    request.metadata = metadata or {}
    return request


def _make_client_error(code="InternalError"):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DescribeFleets")


def _formatted_instances(instance_ids, resource_id="fleet-test"):
    """Return already-formatted instance dicts (as _check_single_fleet_status returns them)."""
    return [
        {
            "instance_id": iid,
            "resource_id": resource_id,
            "status": "running",
            "private_ip": f"10.0.0.{i}",
            "public_ip": None,
            "launch_time": None,
            "instance_type": "t3.medium",
            "image_id": "ami-123",
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
        }
        for i, iid in enumerate(instance_ids)
    ]


class TestEC2FleetHandlerCheckHostsStatus:
    def test_check_hosts_status_all_running(self):
        """All instances running → returns all as active."""
        handler = _make_handler()
        request = _make_request(["fleet-111"], metadata={"fleet_type": "maintain"})
        instance_ids = ["i-aaa", "i-bbb"]

        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_formatted_instances(instance_ids, "fleet-111"),
        ):
            result = handler.check_hosts_status(request)

        assert len(result) == 2
        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_mixed_states(self):
        """describe_fleet_instances only returns active instances — AWS filters for us."""
        handler = _make_handler()
        request = _make_request(["fleet-222"], metadata={"fleet_type": "maintain"})
        active_ids = ["i-running1"]

        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_formatted_instances(active_ids, "fleet-222"),
        ):
            result = handler.check_hosts_status(request)

        assert len(result) == 1
        assert result[0]["instance_id"] == "i-running1"

    def test_check_hosts_status_fleet_not_found(self):
        """_check_single_fleet_status raises → per-fleet exception logged; result is []."""
        handler = _make_handler()
        request = _make_request(["fleet-missing"], metadata={"fleet_type": "maintain"})

        with patch.object(
            handler,
            "_check_single_fleet_status",
            side_effect=AWSInfrastructureError("Fleet not found"),
        ):
            result = handler.check_hosts_status(request)

        assert result == []

    def test_check_hosts_status_multiple_resource_ids(self):
        """Request has 2 fleet IDs → checks both, aggregates results."""
        handler = _make_handler()
        request = _make_request(["fleet-A", "fleet-B"], metadata={"fleet_type": "maintain"})

        ids_a = ["i-a1", "i-a2"]
        ids_b = ["i-b1"]

        def single_fleet_side_effect(fleet_id, req):
            if fleet_id == "fleet-A":
                return _formatted_instances(ids_a, "fleet-A")
            return _formatted_instances(ids_b, "fleet-B")

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=single_fleet_side_effect
        ):
            result = handler.check_hosts_status(request)

        assert len(result) == 3
        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == {"i-a1", "i-a2", "i-b1"}

    def test_check_hosts_status_aws_error(self):
        """ClientError inside per-fleet loop → logged and skipped; result is []."""
        handler = _make_handler()
        request = _make_request(["fleet-err"], metadata={"fleet_type": "maintain"})

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=_make_client_error("InternalError")
        ):
            result = handler.check_hosts_status(request)

        assert result == []

    def test_check_hosts_status_no_resource_ids(self):
        """Empty resource_ids → raises AWSInfrastructureError immediately."""
        handler = _make_handler()
        request = _make_request([])

        with pytest.raises(AWSInfrastructureError):
            handler.check_hosts_status(request)

    def test_check_hosts_status_returns_correct_count(self):
        """Verify count matches active instances returned."""
        handler = _make_handler()
        request = _make_request(["fleet-cnt"], metadata={"fleet_type": "maintain"})
        instance_ids = ["i-1", "i-2", "i-3"]

        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_formatted_instances(instance_ids, "fleet-cnt"),
        ):
            result = handler.check_hosts_status(request)

        assert len(result) == 3

    def test_check_hosts_status_preserves_instance_ids(self):
        """Instance IDs in result match input."""
        handler = _make_handler()
        request = _make_request(["fleet-ids"], metadata={"fleet_type": "maintain"})
        instance_ids = ["i-preserve1", "i-preserve2"]

        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_formatted_instances(instance_ids, "fleet-ids"),
        ):
            result = handler.check_hosts_status(request)

        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_state_filtering_is_strict(self):
        """Only instances returned by _check_single_fleet_status appear in result."""
        handler = _make_handler()
        request = _make_request(["fleet-strict"], metadata={"fleet_type": "maintain"})
        active_ids = ["i-strict-active"]

        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_formatted_instances(active_ids, "fleet-strict"),
        ):
            result = handler.check_hosts_status(request)

        assert len(result) == 1
        assert result[0]["instance_id"] == "i-strict-active"

    def test_check_hosts_status_instant_fleet_no_active_instances(self):
        """Instant fleet with no instances → _check_single_fleet_status returns []."""
        handler = _make_handler()
        request = _make_request(
            ["fleet-instant"],
            metadata={"fleet_type": "instant", "instance_ids": []},
        )

        with patch.object(handler, "_check_single_fleet_status", return_value=[]):
            result = handler.check_hosts_status(request)

        assert result == []


class TestEC2FleetHandlerNameTag:
    def test_fleet_config_instance_tag_uses_config_prefix(self):
        """Instance Name tag in EC2Fleet config uses config_port prefix (instant fleet only)."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        aws_client = MagicMock()
        logger = MagicMock()
        aws_ops = MagicMock()
        launch_template_manager = MagicMock()
        config_port = MagicMock()
        config_port.get_resource_prefix.side_effect = lambda rt: (
            "pfx-" if rt == "fleet" else "inst-"
        )

        handler = EC2FleetHandler(
            aws_client, logger, aws_ops, launch_template_manager, config_port=config_port
        )

        template = MagicMock()
        template.fleet_type = AWSFleetType.INSTANT
        template.tags = {}
        template.price_type = "ondemand"
        template.allocation_strategy = None
        template.max_price = None
        template.machine_types = {"m5.large": 1}
        template.subnet_ids = ["subnet-abc"]
        template.template_id = "tmpl-ec2"
        template.percent_on_demand = None
        template.context = None

        request = MagicMock()
        request.request_id = "req-ec2-001"
        request.requested_count = 2

        fleet_config = handler._create_fleet_config(template, request, "lt-xyz", "$Default")

        instance_ts = next(
            (
                ts
                for ts in fleet_config.get("TagSpecifications", [])
                if ts["ResourceType"] == "instance"
            ),
            None,
        )
        assert instance_ts is not None, "No instance TagSpecification found (instant fleet)"
        name_tag = next((t for t in instance_ts["Tags"] if t["Key"] == "Name"), None)
        assert name_tag is not None, "No Name tag in instance TagSpecification"
        assert "req-ec2-001" in name_tag["Value"]
