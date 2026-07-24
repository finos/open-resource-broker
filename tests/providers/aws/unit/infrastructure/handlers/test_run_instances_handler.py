"""Unit tests for RunInstancesHandler.check_hosts_status and ProviderFulfilment."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError, AWSValidationError
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.launch_template.manager import LTNetworkingState


def _make_handler() -> Any:
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler: Any = RunInstancesHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _make_request(
    resource_ids=None, instance_ids=None, provider_data=None, metadata=None, requested_count=2
):
    request = MagicMock()
    request.request_id = "req-ri-123"
    request.resource_ids = resource_ids or []
    request.provider_api = "RunInstances"
    request.provider_data = provider_data if provider_data is not None else {}
    request.metadata = metadata if metadata is not None else {}
    request.requested_count = requested_count
    return request


def _make_client_error(code="InternalError"):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DescribeInstances")


def _formatted_instances(instance_ids, resource_id="r-test"):
    """Return already-formatted instance dicts (as _format_instance_data returns them)."""
    return [
        {
            "instance_id": iid,
            "resource_id": resource_id,
            "status": "running",
            "private_ip": f"10.0.3.{i}",
            "public_ip": None,
            "launch_time": None,
            "instance_type": "t3.medium",
            "image_id": "ami-run",
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
        }
        for i, iid in enumerate(instance_ids)
    ]


class TestRunInstancesHandlerCheckHostsStatus:
    def test_check_hosts_status_returns_check_hosts_status_result(self):
        """check_hosts_status returns CheckHostsStatusResult (not a plain list)."""
        handler = _make_handler()
        instance_ids = ["i-run1", "i-run2"]
        request = _make_request(
            resource_ids=["r-res1"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-res1"},
            requested_count=2,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-res1"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)
        assert isinstance(result, CheckHostsStatusResult)

    def test_check_hosts_status_all_running_fulfilled(self):
        """All running instances → fulfilment state == 'fulfilled'."""
        handler = _make_handler()
        instance_ids = ["i-run1", "i-run2"]
        request = _make_request(
            resource_ids=["r-res1"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-res1"},
            requested_count=2,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-res1"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result.instances) == 2
        assert result.fulfilment.state == "fulfilled"
        assert result.fulfilment.running_count == 2

    def test_check_hosts_status_all_running_instances_ids(self):
        """All running instances → instance IDs present."""
        handler = _make_handler()
        instance_ids = ["i-run1", "i-run2"]
        request = _make_request(
            resource_ids=["r-res1"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-res1"},
            requested_count=2,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-res1"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_mixed_states(self):
        """Handler returns whatever _get_instance_details gives — no state filtering."""
        handler = _make_handler()
        instance_ids = ["i-running1", "i-stopped1", "i-terminated1"]
        request = _make_request(
            resource_ids=["r-mixed"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-mixed"},
            requested_count=3,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-mixed"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result.instances) == 3

    def test_check_hosts_status_empty_resource_ids(self):
        """No resource_ids and no instance_ids → returns in_progress fulfilment."""
        handler = _make_handler()
        request = _make_request(resource_ids=[], provider_data={}, metadata={})

        with patch.object(handler, "_get_instance_details") as mock_get:
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert result.instances == []
        assert result.fulfilment.state == "in_progress"
        mock_get.assert_not_called()

    def test_check_hosts_status_aws_error(self):
        """Exception from _get_instance_details → raises AWSInfrastructureError."""
        handler = _make_handler()
        instance_ids = ["i-err1"]
        request = _make_request(
            resource_ids=["r-err"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-err"},
        )
        with patch.object(
            handler, "_get_instance_details", side_effect=_make_client_error("InternalError")
        ):
            with pytest.raises(AWSInfrastructureError):
                handler.check_hosts_status(request)

    def test_check_hosts_status_falls_back_to_metadata_instance_ids(self):
        """No provider_data → falls back to metadata instance_ids."""
        handler = _make_handler()
        instance_ids = ["i-meta1", "i-meta2"]
        request = _make_request(
            resource_ids=["r-meta"],
            provider_data={},
            metadata={"instance_ids": instance_ids},
            requested_count=2,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-meta"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result.instances) == 2

    def test_check_hosts_status_returns_correct_count(self):
        """Verify instance count in result matches instances returned."""
        handler = _make_handler()
        instance_ids = ["i-r1", "i-r2", "i-r3", "i-r4"]
        request = _make_request(
            resource_ids=["r-cnt"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-cnt"},
            requested_count=4,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-cnt"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result.instances) == 4

    def test_check_hosts_status_preserves_instance_ids(self):
        """Instance IDs in result match input."""
        handler = _make_handler()
        instance_ids = ["i-ri-preserve1", "i-ri-preserve2"]
        request = _make_request(
            resource_ids=["r-ids"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-ids"},
            requested_count=2,
        )
        with patch.object(
            handler,
            "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-ids"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_state_filtering_is_strict(self):
        """Only instances returned by _get_instance_details appear in result."""
        handler = _make_handler()
        launched_ids = ["i-strict1", "i-strict2"]
        returned_by_aws = _formatted_instances(["i-strict1"], "r-strict")
        request = _make_request(
            resource_ids=["r-strict"],
            provider_data={"instance_ids": launched_ids, "reservation_id": "r-strict"},
            requested_count=2,
        )
        with patch.object(handler, "_get_instance_details", return_value=returned_by_aws):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result.instances) == 1
        assert result.instances[0]["instance_id"] == "i-strict1"

    def test_check_hosts_status_uses_resource_ids_when_no_instance_ids(self):
        """Falls back to _find_instances_by_resource_ids when no instance_ids anywhere."""
        handler = _make_handler()
        request = _make_request(resource_ids=["r-fallback"], provider_data={}, metadata={})
        from orb.domain.base.provider_fulfilment import ProviderFulfilment

        fallback_result = CheckHostsStatusResult(
            instances=_formatted_instances(["i-fb1"], "r-fallback"),
            fulfilment=ProviderFulfilment(state="in_progress", message="test"),
        )
        with patch.object(handler, "_find_instances_by_resource_ids", return_value=fallback_result):
            result = handler.check_hosts_status(request)

        assert len(result.instances) == 1
        assert result.instances[0]["instance_id"] == "i-fb1"


class TestRunInstancesProviderFulfilment:
    """Unit tests for RunInstances fulfilment computation."""

    def _run(self, instances, requested_count):
        handler = _make_handler()
        return handler._compute_run_instances_fulfilment(instances, requested_count)

    def _inst(self, status):
        return {"instance_id": "i-x", "status": status}

    def test_all_running_at_target_is_fulfilled(self):
        instances = [self._inst("running"), self._inst("running")]
        f = self._run(instances, 2)
        assert f.state == "fulfilled"
        assert f.running_count == 2

    def test_running_exceeds_target_is_fulfilled(self):
        instances = [self._inst("running")] * 3
        f = self._run(instances, 2)
        assert f.state == "fulfilled"

    def test_pending_instances_is_in_progress(self):
        instances = [self._inst("running"), self._inst("pending")]
        f = self._run(instances, 2)
        assert f.state == "in_progress"
        assert f.pending_count == 1

    def test_no_instances_is_in_progress(self):
        f = self._run([], 2)
        assert f.state == "in_progress"

    def test_some_running_below_target_is_in_progress(self):
        instances = [self._inst("running")]
        f = self._run(instances, 2)
        # 1 running, no pending → partial. RunInstances is synchronous, so the
        # shortfall is settled: the verdict is final and must terminalise.
        assert f.state == "partial"
        assert f.final is True

    def test_all_failed_is_failed(self):
        instances = [self._inst("failed"), self._inst("failed")]
        f = self._run(instances, 2)
        assert f.state == "failed"


class TestRunInstancesHandlerMachineAdapterContext:
    def test_check_hosts_status_passes_context_to_get_instance_details(self):
        """check_hosts_status passes request_id and resource_id to _get_instance_details."""
        handler = _make_handler()
        instance_ids = ["i-ctx1", "i-ctx2"]
        resource_id = "r-ctx-res"
        request = _make_request(
            resource_ids=[resource_id],
            provider_data={"instance_ids": instance_ids, "reservation_id": resource_id},
            requested_count=2,
        )

        with patch.object(handler, "_get_instance_details", return_value=[]) as mock_details:
            with patch.object(handler, "_format_instance_data", return_value=[]):
                handler.check_hosts_status(request)

        mock_details.assert_called_once_with(
            instance_ids,
            request_id=str(request.request_id),
            resource_id=resource_id,
            provider_api="RunInstances",
        )

    def test_find_instances_by_resource_ids_passes_context(self):
        """_find_instances_by_resource_ids passes request_id and resource_id to _get_instance_details."""
        handler = _make_handler()
        resource_id = "r-find-res"
        request = _make_request(resource_ids=[resource_id])

        handler.aws_client.ec2_client.configure_mock(
            describe_instances=MagicMock(
                return_value={
                    "Reservations": [
                        {
                            "ReservationId": resource_id,
                            "Instances": [{"InstanceId": "i-find1"}],
                        }
                    ]
                }
            )
        )

        with patch.object(handler, "_get_instance_details", return_value=[]) as mock_details:
            with patch.object(handler, "_format_instance_data", return_value=[]):
                handler._find_instances_by_resource_ids(request, [resource_id])

        mock_details.assert_called_once_with(
            ["i-find1"],
            request_id=str(request.request_id),
            resource_id=resource_id,
            provider_api="RunInstances",
        )


def _make_aws_template(*, subnet_ids=None, security_group_ids=None, launch_template_id="lt-abc"):
    """Build a minimal AWSTemplate-like mock for legacy params builder."""
    tmpl = MagicMock()
    tmpl.template_id = "tmpl-1"
    tmpl.launch_template_id = launch_template_id
    tmpl.subnet_ids = subnet_ids or []
    tmpl.security_group_ids = security_group_ids or []
    tmpl.subnet_id = None
    tmpl.machine_types = None
    tmpl.price_type = "ondemand"
    tmpl.max_price = None
    tmpl.tags = None
    return tmpl


def _make_request_for_params(request_id="req-1", count=1):
    request = MagicMock()
    request.request_id = request_id
    request.requested_count = count
    return request


def _handler_with_config_port():
    handler = _make_handler()
    handler.config_port = MagicMock()
    handler.config_port.get_resource_prefix.return_value = "orb-"
    return handler


class TestRunInstancesHandlerNetworkingInject:
    """Regression: networking inject must respect LTNetworkingState.

    Resolves docs/bug-runinstances-unknown-unauthorized-injects-networking.md
    UNKNOWN_UNAUTHORIZED row is the regression.
    """

    @pytest.mark.parametrize(
        "lt_state,expect_subnet,expect_sgs,expect_raises",
        [
            (LTNetworkingState.HAS_NETWORKING, False, False, True),
            (LTNetworkingState.NO_NETWORKING, True, True, False),
            (LTNetworkingState.UNKNOWN_UNAUTHORIZED, False, False, False),
        ],
    )
    def test_inject_respects_lt_state(self, lt_state, expect_subnet, expect_sgs, expect_raises):
        handler = _handler_with_config_port()
        handler.launch_template_manager.inspect_launch_template_networking.return_value = lt_state

        aws_template = _make_aws_template(
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            launch_template_id="lt-abc",
        )
        request = _make_request_for_params()

        if expect_raises:
            with pytest.raises(AWSValidationError):
                handler._create_run_instances_params_legacy(
                    aws_template, request, "lt-abc", "$Latest"
                )
            return

        params = handler._create_run_instances_params_legacy(
            aws_template, request, "lt-abc", "$Latest"
        )

        assert ("SubnetId" in params) is expect_subnet
        assert ("SecurityGroupIds" in params) is expect_sgs
        if expect_subnet:
            assert params["SubnetId"] == "subnet-1"
        if expect_sgs:
            assert params["SecurityGroupIds"] == ["sg-1"]
