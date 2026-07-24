"""Unit tests for EC2FleetHandler.check_hosts_status and ProviderFulfilment."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.domain.base.provider_fulfilment import (
    CheckHostsStatusResult,
    FulfilmentState,
    ProviderFulfilment,
)
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


def _make_request(resource_ids, metadata=None, requested_count=2):
    request = MagicMock()
    request.request_id = "req-ec2fleet-123"
    request.resource_ids = resource_ids
    request.metadata = metadata or {}
    request.requested_count = requested_count
    return request


def _make_client_error(code="InternalError"):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DescribeFleets")


def _inst(iid, resource_id="fleet-test", status="running"):
    return {
        "instance_id": iid,
        "resource_id": resource_id,
        "status": status,
        "private_ip": "10.0.0.1",
        "public_ip": None,
        "launch_time": None,
        "instance_type": "t3.medium",
        "image_id": "ami-123",
        "subnet_id": None,
        "security_group_ids": [],
        "vpc_id": None,
    }


def _formatted_instances(instance_ids, resource_id="fleet-test"):
    return [_inst(iid, resource_id) for iid in instance_ids]


def _fleet_result(
    instance_ids,
    resource_id="fleet-test",
    state: FulfilmentState = "fulfilled",
    final: bool = False,
    *,
    target_units=None,
    fulfilled_units=None,
    running_count=None,
    pending_count=None,
    failed_count=None,
    diagnostic=None,
):
    """Build a CheckHostsStatusResult for mocking _check_single_fleet_status."""
    return CheckHostsStatusResult(
        instances=_formatted_instances(instance_ids, resource_id),
        fulfilment=ProviderFulfilment(
            state=state,
            message="test",
            final=final,
            target_units=target_units,
            fulfilled_units=fulfilled_units,
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
            diagnostic=diagnostic,
        ),
    )


class TestEC2FleetHandlerCheckHostsStatus:
    def test_check_hosts_status_returns_check_hosts_status_result(self):
        """check_hosts_status returns CheckHostsStatusResult (not a plain list)."""
        handler = _make_handler()
        request = _make_request(["fleet-111"], metadata={"fleet_type": "maintain"})
        with patch.object(
            handler, "_check_single_fleet_status", return_value=_fleet_result(["i-aaa"])
        ):
            result = handler.check_hosts_status(request)
        assert isinstance(result, CheckHostsStatusResult)

    def test_check_hosts_status_all_running(self):
        """All instances running → instances present and fulfilled."""
        handler = _make_handler()
        request = _make_request(["fleet-111"], metadata={"fleet_type": "maintain"})
        instance_ids = ["i-aaa", "i-bbb"]
        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_fleet_result(instance_ids, "fleet-111"),
        ):
            result = handler.check_hosts_status(request)

        assert len(result.instances) == 2
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_mixed_states(self):
        """Active instances returned as reported."""
        handler = _make_handler()
        request = _make_request(["fleet-222"], metadata={"fleet_type": "maintain"})
        with patch.object(
            handler,
            "_check_single_fleet_status",
            return_value=_fleet_result(["i-running1"], "fleet-222"),
        ):
            result = handler.check_hosts_status(request)

        assert len(result.instances) == 1
        assert result.instances[0]["instance_id"] == "i-running1"

    def test_check_hosts_status_fleet_not_found(self):
        """_check_single_fleet_status raises → exception logged; result has empty instances."""
        handler = _make_handler()
        request = _make_request(["fleet-missing"], metadata={"fleet_type": "maintain"})
        with patch.object(
            handler,
            "_check_single_fleet_status",
            side_effect=AWSInfrastructureError("Fleet not found"),
        ):
            result = handler.check_hosts_status(request)

        assert result.instances == []
        assert result.fulfilment.state == "in_progress"

    def test_check_hosts_status_multiple_resource_ids(self):
        """Request has 2 fleet IDs → checks both, aggregates instances."""
        handler = _make_handler()
        request = _make_request(["fleet-A", "fleet-B"], metadata={"fleet_type": "maintain"})
        ids_a = ["i-a1", "i-a2"]
        ids_b = ["i-b1"]

        def single_fleet_side_effect(fleet_id, req):
            if fleet_id == "fleet-A":
                return _fleet_result(ids_a, "fleet-A")
            return _fleet_result(ids_b, "fleet-B")

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=single_fleet_side_effect
        ):
            result = handler.check_hosts_status(request)

        assert len(result.instances) == 3
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == {"i-a1", "i-a2", "i-b1"}

    def test_check_hosts_status_multi_partial_all_final_is_terminal(self):
        """All partial contributors final → aggregate partial is final (terminalise)."""
        handler = _make_handler()
        request = _make_request(["fleet-A", "fleet-B"], metadata={"fleet_type": "instant"})

        def single_fleet_side_effect(fleet_id, req):
            if fleet_id == "fleet-A":
                return _fleet_result(["i-a1"], "fleet-A", state="partial", final=True)
            return _fleet_result(["i-b1"], "fleet-B", state="partial", final=True)

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=single_fleet_side_effect
        ):
            result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "partial"
        assert result.fulfilment.final is True

    def test_check_hosts_status_multi_partial_one_non_final_stays_pending(self):
        """A non-final partial contributor → aggregate partial stays non-final (keep healing)."""
        handler = _make_handler()
        request = _make_request(["fleet-A", "fleet-B"], metadata={"fleet_type": "maintain"})

        def single_fleet_side_effect(fleet_id, req):
            if fleet_id == "fleet-A":
                return _fleet_result(["i-a1"], "fleet-A", state="partial", final=True)
            # async/still-reconciling fleet: partial but not settled
            return _fleet_result(["i-b1"], "fleet-B", state="partial", final=False)

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=single_fleet_side_effect
        ):
            result = handler.check_hosts_status(request)

        assert result.fulfilment.state == "partial"
        assert result.fulfilment.final is False

    def test_check_hosts_status_multi_partial_propagates_diagnostic_and_capacity(self):
        """Multi-fleet partial carries merged diagnostic + summed capacity counts.

        Regression: the aggregate must carry the SAME contract as the
        single-fleet path — a bare ProviderFulfilment(state, message, final)
        dropped the diagnostic and every capacity count, so the status API
        surfaced fulfilment_diagnostic=null and omitted capacity units for
        multi-instant-fleet partials.
        """
        from datetime import datetime, timezone

        from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic

        handler = _make_handler()
        request = _make_request(
            ["fleet-A", "fleet-B"], metadata={"fleet_type": "instant"}, requested_count=7
        )

        diag_a = FulfilmentDiagnostic(
            category=DiagnosticCategory.CAPACITY,
            summary="Partially fulfilled: 1/3 capacity",
            occurred_at=datetime.now(timezone.utc),
        )
        diag_b = FulfilmentDiagnostic(
            category=DiagnosticCategory.CAPACITY,
            summary="Partially fulfilled: 2/4 capacity",
            occurred_at=datetime.now(timezone.utc),
        )

        def single_fleet_side_effect(fleet_id, req):
            if fleet_id == "fleet-A":
                return _fleet_result(
                    ["i-a1"],
                    "fleet-A",
                    state="partial",
                    final=True,
                    target_units=3,
                    fulfilled_units=1,
                    running_count=1,
                    pending_count=0,
                    failed_count=0,
                    diagnostic=diag_a,
                )
            return _fleet_result(
                ["i-b1", "i-b2"],
                "fleet-B",
                state="partial",
                final=True,
                target_units=4,
                fulfilled_units=2,
                running_count=2,
                pending_count=0,
                failed_count=0,
                diagnostic=diag_b,
            )

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=single_fleet_side_effect
        ):
            result = handler.check_hosts_status(request)

        f = result.fulfilment
        assert f.state == "partial"
        assert f.final is True
        # Diagnostic is merged (not dropped) so the request DTO gets stamped.
        assert f.diagnostic is not None
        assert f.diagnostic.category == DiagnosticCategory.CAPACITY
        # target_units is the whole-request target (requested_count), NOT the
        # sum of per-fleet targets (which double-counts fallback values).
        assert f.target_units == 7
        # Observed counts ARE summed across both fleets (whole-request view).
        assert f.fulfilled_units == 3
        assert f.running_count == 3
        assert f.pending_count == 0
        assert f.failed_count == 0

    def test_check_hosts_status_multi_partial_all_none_capacity_stays_none(self):
        """When every contributor reports None for an OBSERVED field the aggregate stays None.

        Preserves 'unknown' vs 'zero' for the summed observed counts — an
        all-None field must not collapse to 0.  ``target_units`` is the
        exception: it is always the known whole-request target
        (``requested_count``), never a sum, so it is never None here.
        """
        handler = _make_handler()
        request = _make_request(
            ["fleet-A", "fleet-B"], metadata={"fleet_type": "instant"}, requested_count=5
        )

        def single_fleet_side_effect(fleet_id, req):
            resource = "fleet-A" if fleet_id == "fleet-A" else "fleet-B"
            return _fleet_result([f"i-{resource}"], resource, state="partial", final=True)

        with patch.object(
            handler, "_check_single_fleet_status", side_effect=single_fleet_side_effect
        ):
            result = handler.check_hosts_status(request)

        f = result.fulfilment
        assert f.state == "partial"
        assert f.final is True
        # target_units is the whole-request target, not a sum of None contributors.
        assert f.target_units == 5
        assert f.fulfilled_units is None
        assert f.running_count is None
        assert f.pending_count is None
        assert f.failed_count is None
        # No contributor carried a diagnostic → merged diagnostic is None.
        assert f.diagnostic is None

    def test_check_hosts_status_aws_error(self):
        """ClientError inside per-fleet loop → logged and skipped; empty instances."""
        handler = _make_handler()
        request = _make_request(["fleet-err"], metadata={"fleet_type": "maintain"})
        with patch.object(
            handler, "_check_single_fleet_status", side_effect=_make_client_error("InternalError")
        ):
            result = handler.check_hosts_status(request)

        assert result.instances == []

    def test_check_hosts_status_no_resource_ids(self):
        """Empty resource_ids → raises AWSInfrastructureError immediately."""
        handler = _make_handler()
        request = _make_request([])
        with pytest.raises(AWSInfrastructureError):
            handler.check_hosts_status(request)

    def test_check_hosts_status_returns_correct_count(self):
        """Verify instance count in result matches instances returned."""
        handler = _make_handler()
        request = _make_request(["fleet-cnt"], metadata={"fleet_type": "maintain"})
        with patch.object(
            handler, "_check_single_fleet_status", return_value=_fleet_result(["i-1", "i-2", "i-3"])
        ):
            result = handler.check_hosts_status(request)

        assert len(result.instances) == 3

    def test_check_hosts_status_instant_fleet_no_active_instances(self):
        """Instant fleet with no instances → empty instances, in_progress."""
        handler = _make_handler()
        request = _make_request(
            ["fleet-instant"], metadata={"fleet_type": "instant", "instance_ids": []}
        )
        empty_result = CheckHostsStatusResult(
            instances=[],
            fulfilment=ProviderFulfilment(state="in_progress", message="waiting"),
        )
        with patch.object(handler, "_check_single_fleet_status", return_value=empty_result):
            result = handler.check_hosts_status(request)

        assert result.instances == []
        assert result.fulfilment.state == "in_progress"


class TestEC2FleetFulfilment:
    """Unit tests for EC2Fleet fulfilment computation."""

    def _handler(self):
        return _make_handler()

    def _inst_dict(self, status):
        return {"instance_id": "i-x", "status": status}

    def test_maintain_fleet_fulfilled_when_capacity_met_and_no_pending(self):
        """Maintain: FulfilledCapacity >= TargetCapacity AND pending==0 → fulfilled."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        h = self._handler()
        instances = [self._inst_dict("running"), self._inst_dict("running")]
        f = h._compute_ec2fleet_fulfilment(
            fleet_type=AWSFleetType.MAINTAIN,
            instances=instances,
            target_capacity=4,
            fulfilled_capacity=4.0,
            requested_count=4,
        )
        assert f.state == "fulfilled"
        assert f.running_count == 2
        assert f.fulfilled_units == 4

    def test_maintain_fleet_in_progress_when_pending_exists(self):
        """Maintain: capacity met but pending instances → in_progress."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        h = self._handler()
        instances = [self._inst_dict("running"), self._inst_dict("pending")]
        f = h._compute_ec2fleet_fulfilment(
            fleet_type=AWSFleetType.MAINTAIN,
            instances=instances,
            target_capacity=4,
            fulfilled_capacity=4.0,
            requested_count=4,
        )
        assert f.state == "in_progress"

    def test_maintain_fleet_in_progress_when_capacity_below_target(self):
        """Maintain: FulfilledCapacity < TargetCapacity → in_progress."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        h = self._handler()
        instances = [self._inst_dict("running")]
        f = h._compute_ec2fleet_fulfilment(
            fleet_type=AWSFleetType.MAINTAIN,
            instances=instances,
            target_capacity=4,
            fulfilled_capacity=2.0,
            requested_count=4,
        )
        assert f.state == "in_progress"
        assert f.fulfilled_units == 2

    def test_instant_fleet_fulfilled_when_count_meets_requested(self):
        """Instant: running_count >= requested_count → fulfilled."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        h = self._handler()
        instances = [self._inst_dict("running"), self._inst_dict("running")]
        f = h._compute_ec2fleet_fulfilment(
            fleet_type=AWSFleetType.INSTANT,
            instances=instances,
            target_capacity=2,
            fulfilled_capacity=2.0,
            requested_count=2,
        )
        assert f.state == "fulfilled"

    def test_instant_fleet_partial_when_fewer_running_no_pending(self):
        """Instant: running < requested, no pending → partial (final)."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        h = self._handler()
        instances = [self._inst_dict("running")]
        f = h._compute_ec2fleet_fulfilment(
            fleet_type=AWSFleetType.INSTANT,
            instances=instances,
            target_capacity=4,
            fulfilled_capacity=1.0,
            requested_count=4,
        )
        assert f.state == "partial"

    def test_instant_fleet_in_progress_when_no_instances_yet(self):
        """Instant: no instances yet → in_progress."""
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSFleetType

        h = self._handler()
        f = h._compute_ec2fleet_fulfilment(
            fleet_type=AWSFleetType.INSTANT,
            instances=[],
            target_capacity=2,
            fulfilled_capacity=0.0,
            requested_count=2,
        )
        assert f.state == "in_progress"


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
