"""Unit tests for RunInstancesHandler.check_hosts_status."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.handlers.run_instances_handler import RunInstancesHandler


def _make_handler():
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler = RunInstancesHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _make_request(resource_ids=None, instance_ids=None, provider_data=None, metadata=None):
    request = MagicMock()
    request.request_id = "req-ri-123"
    request.resource_ids = resource_ids or []
    request.provider_api = "RunInstances"
    request.provider_data = provider_data if provider_data is not None else {}
    request.metadata = metadata if metadata is not None else {}
    return request


def _make_client_error(code="InternalError"):
    return ClientError(
        {"Error": {"Code": code, "Message": "boom"}}, "DescribeInstances"
    )


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

    def test_check_hosts_status_all_running(self):
        """All running instances → returns all."""
        handler = _make_handler()
        instance_ids = ["i-run1", "i-run2"]
        request = _make_request(
            resource_ids=["r-res1"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-res1"},
        )

        with patch.object(
            handler, "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-res1")
        ):
            with patch.object(handler, "_format_instance_data",
                              side_effect=lambda insts, rid, req, tmpl=None: insts):
                result = handler.check_hosts_status(request)

        assert len(result) == 2
        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_mixed_states(self):
        """Handler returns whatever _get_instance_details gives — no state filtering."""
        handler = _make_handler()
        instance_ids = ["i-running1", "i-stopped1", "i-terminated1"]
        request = _make_request(
            resource_ids=["r-mixed"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-mixed"},
        )

        with patch.object(
            handler, "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-mixed")
        ):
            with patch.object(handler, "_format_instance_data",
                              side_effect=lambda insts, rid, req, tmpl=None: insts):
                result = handler.check_hosts_status(request)

        assert len(result) == 3

    def test_check_hosts_status_empty_resource_ids(self):
        """No resource_ids and no instance_ids → returns []."""
        handler = _make_handler()
        request = _make_request(resource_ids=[], provider_data={}, metadata={})

        with patch.object(handler, "_get_instance_details") as mock_get:
            result = handler.check_hosts_status(request)

        assert result == []
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
            handler, "_get_instance_details",
            side_effect=_make_client_error("InternalError")
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
        )

        with patch.object(
            handler, "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-meta")
        ):
            with patch.object(handler, "_format_instance_data",
                              side_effect=lambda insts, rid, req, tmpl=None: insts):
                result = handler.check_hosts_status(request)

        assert len(result) == 2

    def test_check_hosts_status_returns_correct_count(self):
        """Verify count matches instances returned."""
        handler = _make_handler()
        instance_ids = ["i-r1", "i-r2", "i-r3", "i-r4"]
        request = _make_request(
            resource_ids=["r-cnt"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-cnt"},
        )

        with patch.object(
            handler, "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-cnt")
        ):
            with patch.object(handler, "_format_instance_data",
                              side_effect=lambda insts, rid, req, tmpl=None: insts):
                result = handler.check_hosts_status(request)

        assert len(result) == 4

    def test_check_hosts_status_preserves_instance_ids(self):
        """Instance IDs in result match input."""
        handler = _make_handler()
        instance_ids = ["i-ri-preserve1", "i-ri-preserve2"]
        request = _make_request(
            resource_ids=["r-ids"],
            provider_data={"instance_ids": instance_ids, "reservation_id": "r-ids"},
        )

        with patch.object(
            handler, "_get_instance_details",
            return_value=_formatted_instances(instance_ids, "r-ids")
        ):
            with patch.object(handler, "_format_instance_data",
                              side_effect=lambda insts, rid, req, tmpl=None: insts):
                result = handler.check_hosts_status(request)

        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_state_filtering_is_strict(self):
        """Only instances returned by _get_instance_details appear in result."""
        handler = _make_handler()
        launched_ids = ["i-strict1", "i-strict2"]
        # Only one instance returned (the other was terminated/not found)
        returned_by_aws = _formatted_instances(["i-strict1"], "r-strict")
        request = _make_request(
            resource_ids=["r-strict"],
            provider_data={"instance_ids": launched_ids, "reservation_id": "r-strict"},
        )

        with patch.object(handler, "_get_instance_details", return_value=returned_by_aws):
            with patch.object(handler, "_format_instance_data",
                              side_effect=lambda insts, rid, req, tmpl=None: insts):
                result = handler.check_hosts_status(request)

        assert len(result) == 1
        assert result[0]["instance_id"] == "i-strict1"

    def test_check_hosts_status_uses_resource_ids_when_no_instance_ids(self):
        """Falls back to _find_instances_by_resource_ids when no instance_ids anywhere."""
        handler = _make_handler()
        request = _make_request(
            resource_ids=["r-fallback"],
            provider_data={},
            metadata={},
        )

        fallback_result = _formatted_instances(["i-fb1"], "r-fallback")

        with patch.object(
            handler, "_find_instances_by_resource_ids",
            return_value=fallback_result
        ):
            result = handler.check_hosts_status(request)

        assert len(result) == 1
        assert result[0]["instance_id"] == "i-fb1"
