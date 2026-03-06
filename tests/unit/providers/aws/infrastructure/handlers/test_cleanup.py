"""Unit tests for ORB launch template cleanup after full machine return."""

from unittest.mock import MagicMock, patch

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
    port = MagicMock()
    port.get_cleanup_config.return_value = {
        "enabled": enabled,
        "delete_launch_template": delete_lt,
        "dry_run": dry_run,
        "resources": {"asg": asg, "ec2_fleet": ec2_fleet, "spot_fleet": spot_fleet},
    }
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
