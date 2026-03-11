"""Unit tests for ORB launch template cleanup after full machine return."""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.config.schemas.cleanup_schema import CleanupConfig, CleanupResourcesConfig
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config_port(
    enabled=True, delete_lt=True, dry_run=False, asg=True, ec2_fleet=True, spot_fleet=True
):
    from orb.config.schemas.cleanup_schema import CleanupConfig, CleanupResourcesConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    port = MagicMock()
    cleanup = CleanupConfig(
        enabled=enabled,
        delete_launch_template=delete_lt,
        dry_run=dry_run,
        resources=CleanupResourcesConfig(asg=asg, ec2_fleet=ec2_fleet, spot_fleet=spot_fleet),
    )
    provider_defaults = ProviderDefaults(cleanup=cleanup.model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    port.get_provider_config.return_value = provider_config
    port.get_resource_prefix.return_value = ""
    return port


def _make_asg_handler(config_port=None):
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    lt_manager = MagicMock()
    handler = ASGHandler(aws_client, logger, aws_ops, lt_manager, config_port=config_port)
    handler._machine_adapter = None
    return handler


def _make_ec2_fleet_handler(config_port=None):
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    lt_manager = MagicMock()
    handler = EC2FleetHandler(aws_client, logger, aws_ops, lt_manager, config_port=config_port)
    handler._machine_adapter = None
    return handler


def _make_spot_fleet_handler(config_port=None):
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    lt_manager = MagicMock()
    handler = SpotFleetHandler(aws_client, logger, aws_ops, lt_manager, config_port=config_port)
    handler._machine_adapter = None
    return handler


def _client_error(code="InvalidLaunchTemplateName.NotFoundException"):
    return ClientError({"Error": {"Code": code, "Message": "not found"}}, "DeleteLaunchTemplate")


# ---------------------------------------------------------------------------
# CleanupConfig schema tests
# ---------------------------------------------------------------------------


class TestCleanupConfigSchema:
    def test_defaults(self):
        cfg = CleanupConfig()
        assert cfg.enabled is True
        assert cfg.delete_launch_template is True
        assert cfg.dry_run is False
        assert cfg.resources.asg is True
        assert cfg.resources.ec2_fleet is True
        assert cfg.resources.spot_fleet is True

    def test_disabled(self):
        cfg = CleanupConfig(enabled=False)
        assert cfg.enabled is False

    def test_dry_run(self):
        cfg = CleanupConfig(dry_run=True)
        assert cfg.dry_run is True

    def test_per_resource_toggle(self):
        cfg = CleanupConfig(
            resources=CleanupResourcesConfig(asg=False, ec2_fleet=True, spot_fleet=False)
        )
        assert cfg.resources.asg is False
        assert cfg.resources.ec2_fleet is True
        assert cfg.resources.spot_fleet is False


# ---------------------------------------------------------------------------
# _delete_orb_launch_template helper tests
# ---------------------------------------------------------------------------


class TestDeleteOrbLaunchTemplate:
    def test_deletes_orb_managed_lt(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.return_value = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-abc123",
                    "LaunchTemplateName": "req-test-id",
                    "Tags": [{"Key": "orb:managed-by", "Value": "open-resource-broker"}],
                }
            ]
        }
        handler._delete_orb_launch_template("req-test-id")
        handler.aws_client.ec2_client.delete_launch_template.assert_called_once_with(
            LaunchTemplateId="lt-abc123"
        )

    def test_skips_non_orb_lt(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.return_value = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-xyz",
                    "LaunchTemplateName": "req-test-id",
                    "Tags": [{"Key": "Name", "Value": "something-else"}],
                }
            ]
        }
        handler._delete_orb_launch_template("req-test-id")
        handler.aws_client.ec2_client.delete_launch_template.assert_not_called()

    def test_skips_when_lt_not_found(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.return_value = {
            "LaunchTemplates": []
        }
        handler._delete_orb_launch_template("req-test-id")
        handler.aws_client.ec2_client.delete_launch_template.assert_not_called()

    def test_dry_run_does_not_delete(self):
        handler = _make_asg_handler(config_port=_make_config_port(dry_run=True))
        handler.aws_client.ec2_client.describe_launch_templates.return_value = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-dry",
                    "LaunchTemplateName": "req-dry-id",
                    "Tags": [{"Key": "orb:managed-by", "Value": "open-resource-broker"}],
                }
            ]
        }
        handler._delete_orb_launch_template("req-dry-id")
        handler.aws_client.ec2_client.delete_launch_template.assert_not_called()

    def test_cleanup_disabled_skips(self):
        handler = _make_asg_handler(config_port=_make_config_port(enabled=False))
        handler._delete_orb_launch_template("req-test-id")
        handler.aws_client.ec2_client.describe_launch_templates.assert_not_called()

    def test_delete_lt_flag_false_skips(self):
        handler = _make_asg_handler(config_port=_make_config_port(delete_lt=False))
        handler._delete_orb_launch_template("req-test-id")
        handler.aws_client.ec2_client.describe_launch_templates.assert_not_called()

    def test_aws_error_is_warning_only(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.side_effect = _client_error(
            "InternalError"
        )
        # Must not raise
        handler._delete_orb_launch_template("req-test-id")
        handler._logger.warning.assert_called()

    def test_no_config_port_is_warning_only(self):
        handler = _make_asg_handler(config_port=None)
        # Must not raise
        handler._delete_orb_launch_template("req-test-id")
        handler._logger.warning.assert_called()


# ---------------------------------------------------------------------------
# ASG: cleanup triggered on full return, not on partial
# ---------------------------------------------------------------------------


class TestASGCleanupOnReturn:
    def _asg_details(self, desired=2, min_size=0):
        return {"DesiredCapacity": desired, "MinSize": min_size}

    def test_full_return_triggers_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_asg_handler(config_port=config_port)

        with (
            patch.object(handler, "_delete_asg") as mock_delete_asg,
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_asg(
                "asg-req-abc",
                ["i-1", "i-2"],
                self._asg_details(desired=2),
            )
            mock_delete_asg.assert_called_once_with("asg-req-abc")
            mock_delete_lt.assert_called_once_with("asg-req-abc")

    def test_partial_return_does_not_trigger_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_asg_handler(config_port=config_port)

        with (
            patch.object(handler, "_delete_asg") as mock_delete_asg,
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_asg(
                "asg-req-abc",
                ["i-1"],
                self._asg_details(desired=3),
            )
            mock_delete_asg.assert_not_called()
            mock_delete_lt.assert_not_called()

    def test_asg_cleanup_disabled_skips_lt(self):
        config_port = _make_config_port(asg=False)
        handler = _make_asg_handler(config_port=config_port)

        with (
            patch.object(handler, "_delete_asg") as mock_delete_asg,
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_asg(
                "asg-req-abc",
                ["i-1", "i-2"],
                self._asg_details(desired=2),
            )
            mock_delete_asg.assert_called_once()
            mock_delete_lt.assert_not_called()

    def test_prefix_stripped_from_asg_name(self):
        config_port = _make_config_port()
        config_port.get_resource_prefix.return_value = "orb-asg-"
        handler = _make_asg_handler(config_port=config_port)

        with (
            patch.object(handler, "_delete_asg"),
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_asg(
                "orb-asg-req-abc-123",
                ["i-1"],
                self._asg_details(desired=1),
            )
            mock_delete_lt.assert_called_once_with("req-abc-123")


# ---------------------------------------------------------------------------
# EC2Fleet: cleanup triggered on full return, not on partial
# ---------------------------------------------------------------------------


class TestEC2FleetCleanupOnReturn:
    def _fleet_details(self, fleet_type="maintain", total=2, tags=None):
        resolved_tags = (
            [{"Key": "orb:request-id", "Value": "req-fleet-abc"}] if tags is None else tags
        )
        return {
            "Type": fleet_type,
            "TargetCapacitySpecification": {"TotalTargetCapacity": total},
            "Tags": resolved_tags,
        }

    def test_full_return_triggers_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_ec2_fleet_handler(config_port=config_port)

        with (
            patch.object(handler._fleet_release_manager, "_delete_fleet") as mock_delete_fleet,
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_ec2_fleet(
                "fleet-123",
                ["i-1", "i-2"],
                self._fleet_details(total=2),
            )
            mock_delete_fleet.assert_called_once_with("fleet-123")
            mock_delete_lt.assert_called_once_with("req-fleet-abc")

    def test_partial_return_does_not_trigger_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_ec2_fleet_handler(config_port=config_port)

        with (
            patch.object(handler._fleet_release_manager, "_delete_fleet") as mock_delete_fleet,
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_ec2_fleet(
                "fleet-123",
                ["i-1"],
                self._fleet_details(total=3),
            )
            mock_delete_fleet.assert_not_called()
            mock_delete_lt.assert_not_called()

    def test_ec2_fleet_cleanup_disabled_skips_lt(self):
        config_port = _make_config_port(ec2_fleet=False)
        handler = _make_ec2_fleet_handler(config_port=config_port)

        with (
            patch.object(handler._fleet_release_manager, "_delete_fleet") as mock_delete_fleet,
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_ec2_fleet(
                "fleet-123",
                ["i-1", "i-2"],
                self._fleet_details(total=2),
            )
            mock_delete_fleet.assert_called_once()
            mock_delete_lt.assert_not_called()

    def test_missing_request_id_tag_skips_lt(self):
        config_port = _make_config_port()
        handler = _make_ec2_fleet_handler(config_port=config_port)

        with (
            patch.object(handler._fleet_release_manager, "_delete_fleet"),
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_ec2_fleet(
                "fleet-123",
                ["i-1", "i-2"],
                self._fleet_details(total=2, tags=[]),
            )
            mock_delete_lt.assert_not_called()


# ---------------------------------------------------------------------------
# SpotFleet: cleanup triggered on full return, not on partial
# ---------------------------------------------------------------------------


class TestSpotFleetCleanupOnReturn:
    def _fleet_details(self, fleet_type="maintain", target=2, tags=None):
        resolved_tags = (
            [{"Key": "orb:request-id", "Value": "req-spot-abc"}] if tags is None else tags
        )
        return {
            "SpotFleetRequestConfig": {
                "Type": fleet_type,
                "TargetCapacity": target,
                "OnDemandTargetCapacity": 0,
                "TagSpecifications": [],
            },
            "Tags": resolved_tags,
        }

    def test_full_return_triggers_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_spot_fleet_handler(config_port=config_port)

        with (
            patch.object(handler._release_manager, "_retry", create=True),
            patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt,
        ):
            handler._release_hosts_for_single_spot_fleet(
                "sfr-123",
                ["i-1", "i-2"],
                self._fleet_details(target=2),
            )
            mock_delete_lt.assert_called_once_with("req-spot-abc")

    def test_partial_return_does_not_trigger_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_spot_fleet_handler(config_port=config_port)

        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._release_hosts_for_single_spot_fleet(
                "sfr-123",
                ["i-1"],
                self._fleet_details(target=3),
            )
            mock_delete_lt.assert_not_called()

    def test_spot_fleet_cleanup_disabled_skips_lt(self):
        config_port = _make_config_port(spot_fleet=False)
        handler = _make_spot_fleet_handler(config_port=config_port)

        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._release_hosts_for_single_spot_fleet(
                "sfr-123",
                ["i-1", "i-2"],
                self._fleet_details(target=2),
            )
            mock_delete_lt.assert_not_called()

    def test_missing_request_id_tag_skips_lt(self):
        config_port = _make_config_port()
        handler = _make_spot_fleet_handler(config_port=config_port)

        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._release_hosts_for_single_spot_fleet(
                "sfr-123",
                ["i-1", "i-2"],
                self._fleet_details(target=2, tags=[]),
            )
            mock_delete_lt.assert_not_called()


# ---------------------------------------------------------------------------
# _delete_orb_launch_template: tag-based lookup edge cases
# ---------------------------------------------------------------------------


class TestDeleteOrbLaunchTemplateTagBased:
    """Tests for the tag-based LT lookup path (describe by tag filter)."""

    def _make_handler_with_tag_describe(self, lt_list):
        """Return an ASG handler whose describe_launch_templates returns lt_list."""
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.return_value = {
            "LaunchTemplates": lt_list
        }
        return handler

    def _orb_lt(self, lt_id, lt_name="lt-name"):
        return {
            "LaunchTemplateId": lt_id,
            "LaunchTemplateName": lt_name,
            "Tags": [{"Key": "orb:managed-by", "Value": "open-resource-broker"}],
        }

    def test_single_lt_found_is_deleted(self):
        handler = self._make_handler_with_tag_describe([self._orb_lt("lt-aaa")])
        handler._delete_orb_launch_template("req-1")
        handler.aws_client.ec2_client.delete_launch_template.assert_called_once_with(
            LaunchTemplateId="lt-aaa"
        )

    def test_multiple_lts_all_deleted(self):
        handler = self._make_handler_with_tag_describe(
            [self._orb_lt("lt-aaa"), self._orb_lt("lt-bbb")]
        )
        # Current implementation deletes only the first match — this test documents
        # the expected fixed behaviour where all matching LTs are deleted.
        handler._delete_orb_launch_template("req-multi")
        calls = handler.aws_client.ec2_client.delete_launch_template.call_args_list
        deleted_ids = {c.kwargs.get("LaunchTemplateId") or c.args[0] for c in calls}
        assert "lt-aaa" in deleted_ids
        assert "lt-bbb" in deleted_ids

    def test_empty_describe_result_emits_debug_no_error(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.return_value = {
            "LaunchTemplates": []
        }
        handler._delete_orb_launch_template("req-missing")
        handler.aws_client.ec2_client.delete_launch_template.assert_not_called()
        handler._logger.debug.assert_called()

    def test_describe_client_error_logs_warning_no_raise(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        handler.aws_client.ec2_client.describe_launch_templates.side_effect = _client_error(
            "InternalError"
        )
        handler._delete_orb_launch_template("req-err")
        handler._logger.warning.assert_called()

    def test_delete_client_error_logs_warning_no_raise(self):
        handler = self._make_handler_with_tag_describe([self._orb_lt("lt-del-err")])
        handler.aws_client.ec2_client.delete_launch_template.side_effect = _client_error(
            "InternalError"
        )
        handler._delete_orb_launch_template("req-del-err")
        handler._logger.warning.assert_called()

    def test_delete_client_error_continues_to_next_lt(self):
        """When deleting the first LT fails, the second LT should still be attempted."""
        handler = self._make_handler_with_tag_describe(
            [self._orb_lt("lt-fail"), self._orb_lt("lt-ok")]
        )
        handler.aws_client.ec2_client.delete_launch_template.side_effect = [
            _client_error("InternalError"),
            None,
        ]
        handler._delete_orb_launch_template("req-partial")
        assert handler.aws_client.ec2_client.delete_launch_template.call_count == 2


# ---------------------------------------------------------------------------
# EC2Fleet release manager: per fleet-type cleanup behaviour
# ---------------------------------------------------------------------------


def _make_ec2_fleet_release_manager(cleanup_fn=None):
    aws_client = MagicMock()
    aws_ops = MagicMock()
    logger = MagicMock()
    cleanup_fn = cleanup_fn or MagicMock()
    mgr = _make_ec2_fleet_handler(config_port=_make_config_port())._fleet_release_manager
    # Replace internals with fresh mocks so we control all AWS calls
    mgr._aws_client = aws_client
    mgr._aws_ops = aws_ops
    mgr._logger = logger
    mgr._cleanup_on_zero_capacity = cleanup_fn
    mgr._retry = lambda fn, operation_type="standard", **kw: fn(**kw)
    return mgr, aws_client, cleanup_fn


class TestEC2FleetReleaseManagerFleetTypes:
    def _fleet_details(self, fleet_type, total, request_id="req-ec2-abc"):
        return {
            "Type": fleet_type,
            "TargetCapacitySpecification": {"TotalTargetCapacity": total},
            "Tags": [{"Key": "orb:request-id", "Value": request_id}],
        }

    def test_maintain_at_zero_capacity_deletes_fleet_and_calls_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, _aws_client, _ = _make_ec2_fleet_release_manager(cleanup_fn)
        with patch.object(mgr, "_delete_fleet") as mock_delete:
            mgr.release("fleet-1", ["i-1", "i-2"], self._fleet_details("maintain", 2))
        mock_delete.assert_called_once_with("fleet-1")
        cleanup_fn.assert_called_once_with("ec2_fleet", "req-ec2-abc")

    def test_request_type_deletes_fleet_with_terminate_false_and_calls_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, aws_client, _ = _make_ec2_fleet_release_manager(cleanup_fn)
        with patch.object(mgr, "_delete_fleet") as mock_delete:
            mgr.release("fleet-2", ["i-1"], self._fleet_details("request", 1))
        # request type: no modify_fleet, no _delete_fleet helper — uses delete_fleets directly
        aws_client.ec2_client.modify_fleet.assert_not_called()
        mock_delete.assert_not_called()
        aws_client.ec2_client.delete_fleets.assert_called_once_with(
            FleetIds=["fleet-2"], TerminateInstances=False
        )
        cleanup_fn.assert_called_once_with("ec2_fleet", "req-ec2-abc")

    def test_instant_type_no_fleet_delete_but_calls_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, aws_client, _ = _make_ec2_fleet_release_manager(cleanup_fn)
        with patch.object(mgr, "_delete_fleet") as mock_delete:
            mgr.release("fleet-3", ["i-1"], self._fleet_details("instant", 1))
        mock_delete.assert_not_called()
        aws_client.ec2_client.delete_fleets.assert_not_called()
        cleanup_fn.assert_called_once_with("ec2_fleet", "req-ec2-abc")

    def test_request_type_missing_request_id_tag_skips_cleanup_gracefully(self):
        cleanup_fn = MagicMock()
        mgr, _aws_client, _ = _make_ec2_fleet_release_manager(cleanup_fn)
        details = {
            "Type": "request",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 1},
            "Tags": [],
        }
        mgr.release("fleet-4", ["i-1"], details)
        cleanup_fn.assert_not_called()

    def test_maintain_missing_request_id_tag_skips_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, _aws_client, _ = _make_ec2_fleet_release_manager(cleanup_fn)
        details = {
            "Type": "maintain",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 1},
            "Tags": [],
        }
        with patch.object(mgr, "_delete_fleet"):
            mgr.release("fleet-5", ["i-1"], details)
        cleanup_fn.assert_not_called()


# ---------------------------------------------------------------------------
# SpotFleet release manager: per fleet-type cleanup behaviour
# ---------------------------------------------------------------------------


def _make_spot_fleet_release_manager(cleanup_fn=None):
    aws_client = MagicMock()
    aws_ops = MagicMock()
    logger = MagicMock()
    cleanup_fn = cleanup_fn or MagicMock()
    from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
        SpotFleetReleaseManager,
    )

    mgr = SpotFleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        cleanup_on_zero_capacity_fn=cleanup_fn,
        logger=logger,
    )
    # Make _retry call the function directly (no real retry logic needed in unit tests)
    mgr._aws_ops._retry_with_backoff = None
    return mgr, aws_client, cleanup_fn


class TestSpotFleetReleaseManagerFleetTypes:
    def _fleet_details(self, fleet_type, target, request_id="req-spot-xyz"):
        return {
            "SpotFleetRequestConfig": {
                "Type": fleet_type,
                "TargetCapacity": target,
                "OnDemandTargetCapacity": 0,
                "TagSpecifications": [],
            },
            "Tags": [{"Key": "orb:request-id", "Value": request_id}],
        }

    def test_maintain_at_zero_capacity_cancels_fleet_and_calls_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, aws_client, _ = _make_spot_fleet_release_manager(cleanup_fn)
        mgr.release("sfr-1", ["i-1", "i-2"], self._fleet_details("maintain", 2))
        aws_client.ec2_client.cancel_spot_fleet_requests.assert_called_once_with(
            SpotFleetRequestIds=["sfr-1"],
            TerminateInstances=False,
        )
        cleanup_fn.assert_called_once_with("spot_fleet", "req-spot-xyz")

    def test_request_type_cancels_fleet_with_terminate_false_and_calls_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, aws_client, _ = _make_spot_fleet_release_manager(cleanup_fn)
        mgr.release("sfr-2", ["i-1"], self._fleet_details("request", 1))
        aws_client.ec2_client.modify_spot_fleet_request.assert_not_called()
        aws_client.ec2_client.cancel_spot_fleet_requests.assert_called_once_with(
            SpotFleetRequestIds=["sfr-2"],
            TerminateInstances=False,
        )
        cleanup_fn.assert_called_once_with("spot_fleet", "req-spot-xyz")

    def test_maintain_missing_request_id_tag_skips_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, _aws_client, _ = _make_spot_fleet_release_manager(cleanup_fn)
        details = {
            "SpotFleetRequestConfig": {
                "Type": "maintain",
                "TargetCapacity": 1,
                "OnDemandTargetCapacity": 0,
                "TagSpecifications": [],
            },
            "Tags": [],
        }
        mgr.release("sfr-3", ["i-1"], details)
        cleanup_fn.assert_not_called()


# ---------------------------------------------------------------------------
# cancel_resource: LT cleanup called for EC2Fleet and SpotFleet
# ---------------------------------------------------------------------------


class TestCancelResourceLTCleanup:
    def test_ec2_fleet_cancel_resource_calls_release_with_empty_instances(self):
        config_port = _make_config_port()
        handler = _make_ec2_fleet_handler(config_port=config_port)
        with patch.object(handler._fleet_release_manager, "release") as mock_release:
            handler.cancel_resource("fleet-cancel-1", "req-cancel-1")
        mock_release.assert_called_once_with("fleet-cancel-1", [], {}, request_id="req-cancel-1")

    def test_spot_fleet_cancel_resource_calls_release_with_empty_instances(self):
        config_port = _make_config_port()
        handler = _make_spot_fleet_handler(config_port=config_port)
        with patch.object(handler._release_manager, "release") as mock_release:
            handler.cancel_resource("sfr-cancel-1", "req-cancel-1")
        mock_release.assert_called_once_with("sfr-cancel-1", [], {}, request_id="req-cancel-1")

    def test_ec2_fleet_cancel_resource_returns_success(self):
        config_port = _make_config_port()
        handler = _make_ec2_fleet_handler(config_port=config_port)
        with patch.object(handler._fleet_release_manager, "release"):
            result = handler.cancel_resource("fleet-cancel-2", "req-cancel-2")
        assert result["status"] == "success"

    def test_spot_fleet_cancel_resource_returns_success(self):
        config_port = _make_config_port()
        handler = _make_spot_fleet_handler(config_port=config_port)
        with patch.object(handler._release_manager, "release"):
            result = handler.cancel_resource("sfr-cancel-2", "req-cancel-2")
        assert result["status"] == "success"

    def test_ec2_fleet_cancel_resource_returns_error_on_exception(self):
        config_port = _make_config_port()
        handler = _make_ec2_fleet_handler(config_port=config_port)
        with patch.object(
            handler._fleet_release_manager, "release", side_effect=RuntimeError("boom")
        ):
            result = handler.cancel_resource("fleet-cancel-3", "req-cancel-3")
        assert result["status"] == "error"

    def test_spot_fleet_cancel_resource_returns_error_on_exception(self):
        config_port = _make_config_port()
        handler = _make_spot_fleet_handler(config_port=config_port)
        with patch.object(handler._release_manager, "release", side_effect=RuntimeError("boom")):
            result = handler.cancel_resource("sfr-cancel-3", "req-cancel-3")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# ASG cancel_resource: LT cleanup called
# ---------------------------------------------------------------------------


class TestASGCancelResource:
    def test_cancel_resource_deletes_asg_and_triggers_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_asg_handler(config_port=config_port)
        with (
            patch.object(handler, "_delete_asg") as mock_delete_asg,
            patch.object(handler, "_cleanup_on_zero_capacity") as mock_cleanup,
        ):
            result = handler.cancel_resource("asg-cancel-1", "req-cancel-1")
        mock_delete_asg.assert_called_once_with("asg-cancel-1")
        mock_cleanup.assert_called_once_with("asg", "req-cancel-1")
        assert result["status"] == "success"

    def test_cancel_resource_no_request_id_skips_lt_cleanup(self):
        config_port = _make_config_port()
        handler = _make_asg_handler(config_port=config_port)
        with (
            patch.object(handler, "_delete_asg"),
            patch.object(handler, "_cleanup_on_zero_capacity") as mock_cleanup,
        ):
            handler.cancel_resource("asg-cancel-2", "")
        mock_cleanup.assert_not_called()

    def test_cancel_resource_returns_error_on_exception(self):
        from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError

        config_port = _make_config_port()
        handler = _make_asg_handler(config_port=config_port)
        with patch.object(handler, "_delete_asg", side_effect=RuntimeError("fail")):
            with pytest.raises(AWSInfrastructureError, match="Failed to cancel ASG asg-cancel-3"):
                handler.cancel_resource("asg-cancel-3", "req-cancel-3")


# ---------------------------------------------------------------------------
# ASG capacity manager: asg_details missing path
# ---------------------------------------------------------------------------


def _make_asg_capacity_manager(cleanup_fn=None):
    aws_client = MagicMock()
    aws_ops = MagicMock()
    logger = MagicMock()
    cleanup_fn = cleanup_fn or MagicMock()
    from orb.providers.aws.infrastructure.handlers.asg.capacity_manager import ASGCapacityManager

    mgr = ASGCapacityManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        cleanup_on_zero_capacity_fn=cleanup_fn,
        logger=logger,
        retry_with_backoff=lambda fn, operation_type="standard", **kw: fn(**kw),
        chunk_list=lambda lst, n: [lst[i : i + n] for i in range(0, len(lst), n)],
    )
    return mgr, aws_client, cleanup_fn


class TestASGCapacityManagerMissingDetails:
    def _empty_describe_response(self):
        """Return a describe_auto_scaling_groups response with no groups."""
        return {"AutoScalingGroups": []}

    def test_empty_asg_details_retries_describe_then_terminates(self):
        mgr, aws_client, _cleanup_fn = _make_asg_capacity_manager()
        aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = (
            self._empty_describe_response()
        )
        mgr.release_instances("asg-no-details", ["i-1", "i-2"], {})
        aws_client.autoscaling_client.describe_auto_scaling_groups.assert_called_once()
        cast(MagicMock, mgr._aws_ops.terminate_instances_with_fallback).assert_called_once()

    def test_empty_asg_details_retry_still_empty_calls_delete_and_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, aws_client, _ = _make_asg_capacity_manager(cleanup_fn)
        aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = (
            self._empty_describe_response()
        )
        delete_fn = MagicMock()
        mgr.set_delete_asg_fn(delete_fn)
        mgr.release_instances("asg-no-details", ["i-1"], {})
        delete_fn.assert_called_once_with("asg-no-details")
        cleanup_fn.assert_called_once_with("asg", "asg-no-details")

    def test_empty_asg_details_logs_warning(self):
        mgr, aws_client, _cleanup_fn = _make_asg_capacity_manager()
        aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = (
            self._empty_describe_response()
        )
        mgr.release_instances("asg-no-details", ["i-1"], {})
        cast(MagicMock, mgr._logger.warning).assert_called()

    def test_empty_asg_details_does_not_call_detach(self):
        mgr, aws_client, _cleanup_fn = _make_asg_capacity_manager()
        aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = (
            self._empty_describe_response()
        )
        mgr.release_instances("asg-no-details", ["i-1"], {})
        aws_client.autoscaling_client.detach_instances.assert_not_called()

    def test_empty_asg_details_retry_succeeds_continues_normal_path(self):
        """When the retry describe returns a valid ASG, normal detach+terminate path runs."""
        cleanup_fn = MagicMock()
        mgr, aws_client, _ = _make_asg_capacity_manager(cleanup_fn)
        aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"DesiredCapacity": 1, "MinSize": 0}]
        }
        delete_fn = MagicMock()
        mgr.set_delete_asg_fn(delete_fn)
        mgr.release_instances("asg-retry-ok", ["i-1"], {})
        aws_client.autoscaling_client.detach_instances.assert_called_once()
        cast(MagicMock, mgr._aws_ops.terminate_instances_with_fallback).assert_called_once()

    def test_valid_asg_details_at_zero_capacity_calls_delete_and_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, _aws_client, _ = _make_asg_capacity_manager(cleanup_fn)
        delete_fn = MagicMock()
        mgr.set_delete_asg_fn(delete_fn)
        mgr.release_instances(
            "asg-full-return",
            ["i-1"],
            {"DesiredCapacity": 1, "MinSize": 0},
        )
        delete_fn.assert_called_once_with("asg-full-return")
        cleanup_fn.assert_called_once_with("asg", "asg-full-return")

    def test_valid_asg_details_partial_return_no_delete_no_cleanup(self):
        cleanup_fn = MagicMock()
        mgr, _aws_client, _ = _make_asg_capacity_manager(cleanup_fn)
        delete_fn = MagicMock()
        mgr.set_delete_asg_fn(delete_fn)
        mgr.release_instances(
            "asg-partial",
            ["i-1"],
            {"DesiredCapacity": 3, "MinSize": 0},
        )
        delete_fn.assert_not_called()
        cleanup_fn.assert_not_called()


# ---------------------------------------------------------------------------
# _cleanup_on_zero_capacity: config guard and exception handling
# ---------------------------------------------------------------------------


class TestCleanupOnZeroCapacity:
    def test_no_config_port_returns_without_action(self):
        handler = _make_asg_handler(config_port=None)
        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._cleanup_on_zero_capacity("asg", "req-no-config")
        mock_delete_lt.assert_not_called()

    def test_cleanup_disabled_returns_without_action(self):
        handler = _make_asg_handler(config_port=_make_config_port(enabled=False))
        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._cleanup_on_zero_capacity("asg", "req-disabled")
        mock_delete_lt.assert_not_called()

    def test_resource_type_disabled_returns_without_action(self):
        handler = _make_asg_handler(config_port=_make_config_port(asg=False))
        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._cleanup_on_zero_capacity("asg", "req-asg-off")
        mock_delete_lt.assert_not_called()

    def test_config_port_get_cleanup_config_raises_returns_without_action(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        with patch.object(handler, "_get_cleanup_config", side_effect=RuntimeError("config error")):
            with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
                handler._cleanup_on_zero_capacity("asg", "req-config-err")
        mock_delete_lt.assert_not_called()

    def test_enabled_resource_enabled_delegates_to_delete_lt(self):
        handler = _make_asg_handler(config_port=_make_config_port())
        with patch.object(handler, "_delete_orb_launch_template") as mock_delete_lt:
            handler._cleanup_on_zero_capacity("asg", "req-ok")
        mock_delete_lt.assert_called_once_with("req-ok")
