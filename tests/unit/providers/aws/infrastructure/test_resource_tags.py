"""Tests verifying consistent orb: tag application across all AWS resource creation paths."""

from unittest.mock import MagicMock

import pytest

from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from providers.aws.infrastructure.tags import build_resource_tags, build_system_tags, merge_tags

ORB_SYSTEM_KEYS = {
    "orb:managed-by",
    "orb:request-id",
    "orb:template-id",
    "orb:provider-api",
    "orb:created-at",
}


def _tag_dict(tags: list[dict]) -> dict[str, str]:
    """Convert AWS tag list to plain dict for easy assertion."""
    return {t["Key"]: t["Value"] for t in tags}


def _make_template(fleet_type=AWSFleetType.MAINTAIN, tags=None):
    t = MagicMock()
    t.template_id = "tmpl-abc"
    t.fleet_type = fleet_type
    t.tags = tags
    t.fleet_role = "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role"
    t.allocation_strategy = "lowest_price"
    t.price_type = "spot"
    t.max_price = None
    t.percent_on_demand = 0
    t.machine_types = {"t3.medium": 1}
    t.machine_types_ondemand = None
    t.machine_types_priority = None
    t.subnet_ids = ["subnet-111"]
    t.security_group_ids = ["sg-111"]
    t.context = None
    t.get_instance_requirements_payload = MagicMock(return_value=None)
    return t


def _make_request():
    r = MagicMock()
    r.request_id = "req-00000000-0000-0000-0000-000000000001"
    r.requested_count = 2
    return r


def _make_config_port(prefix=""):
    cp = MagicMock()
    cp.get_resource_prefix.return_value = prefix
    return cp


# ---------------------------------------------------------------------------
# tags.py unit tests
# ---------------------------------------------------------------------------


class TestBuildSystemTags:
    def test_returns_all_five_orb_keys(self):
        tags = build_system_tags("req-1", "tmpl-1", "ASG")
        keys = {t["Key"] for t in tags}
        assert keys == ORB_SYSTEM_KEYS

    def test_values_are_set_correctly(self):
        tags = build_system_tags(
            "req-1", "tmpl-1", "SpotFleet", created_at="2026-01-01T00:00:00+00:00"
        )
        d = _tag_dict(tags)
        assert d["orb:managed-by"] == "open-resource-broker"
        assert d["orb:request-id"] == "req-1"
        assert d["orb:template-id"] == "tmpl-1"
        assert d["orb:provider-api"] == "SpotFleet"
        assert d["orb:created-at"] == "2026-01-01T00:00:00+00:00"

    def test_created_at_defaults_to_now(self):
        tags = build_system_tags("req-1", "tmpl-1", "EC2Fleet")
        d = _tag_dict(tags)
        assert d["orb:created-at"]  # non-empty


class TestBuildResourceTagsReservedNamespace:
    def test_orb_prefixed_template_tag_raises(self):
        cp = _make_config_port()
        with pytest.raises(ValueError, match="orb:"):
            build_resource_tags(
                config_port=cp,
                request_id="req-1",
                template_id="tmpl-1",
                resource_prefix_key="fleet",
                provider_api="EC2Fleet",
                template_tags={"orb:request-id": "spoofed"},
            )

    def test_multiple_orb_prefixed_keys_reported(self):
        cp = _make_config_port()
        with pytest.raises(ValueError, match="orb:"):
            build_resource_tags(
                config_port=cp,
                request_id="req-1",
                template_id="tmpl-1",
                resource_prefix_key="fleet",
                provider_api="EC2Fleet",
                template_tags={"orb:managed-by": "x", "orb:template-id": "y"},
            )

    def test_plain_template_tags_are_accepted(self):
        cp = _make_config_port()
        tags = build_resource_tags(
            config_port=cp,
            request_id="req-1",
            template_id="tmpl-1",
            resource_prefix_key="fleet",
            provider_api="EC2Fleet",
            template_tags={"env": "prod"},
        )
        d = _tag_dict(tags)
        assert d["env"] == "prod"

    def test_none_template_tags_are_accepted(self):
        cp = _make_config_port()
        tags = build_resource_tags(
            config_port=cp,
            request_id="req-1",
            template_id="tmpl-1",
            resource_prefix_key="fleet",
            provider_api="EC2Fleet",
            template_tags=None,
        )
        keys = {t["Key"] for t in tags}
        assert ORB_SYSTEM_KEYS.issubset(keys)


class TestMergeTags:
    def test_user_orb_keys_are_stripped(self):
        user = [{"Key": "orb:request-id", "Value": "spoofed"}, {"Key": "env", "Value": "prod"}]
        system = build_system_tags("req-real", "tmpl-1", "ASG")
        merged = merge_tags(user, system)
        d = _tag_dict(merged)
        assert d["orb:request-id"] == "req-real"
        assert d["env"] == "prod"

    def test_system_tags_win_on_duplicate_keys(self):
        user = [{"Key": "orb:managed-by", "Value": "attacker"}]
        system = build_system_tags("req-1", "tmpl-1", "ASG")
        merged = merge_tags(user, system)
        d = _tag_dict(merged)
        assert d["orb:managed-by"] == "open-resource-broker"

    def test_empty_user_tags(self):
        system = build_system_tags("req-1", "tmpl-1", "RunInstances")
        merged = merge_tags([], system)
        assert {t["Key"] for t in merged} == ORB_SYSTEM_KEYS


# ---------------------------------------------------------------------------
# ASG handler tag tests
# ---------------------------------------------------------------------------


class TestASGHandlerTags:
    def _make_handler(self):
        handler = ASGHandler(
            aws_client=MagicMock(),
            logger=MagicMock(),
            aws_ops=MagicMock(),
            launch_template_manager=MagicMock(),
            config_port=_make_config_port(),
        )
        handler._retry_with_backoff = MagicMock()
        return handler

    def test_tag_asg_uses_orb_prefixed_keys(self):
        handler = self._make_handler()
        template = _make_template()
        template.tags = None

        handler._tag_asg("asg-test", template, "req-123")

        call_kwargs = handler._retry_with_backoff.call_args[1]
        tags = call_kwargs["Tags"]
        keys = {t["Key"] for t in tags}
        assert "orb:managed-by" in keys
        assert "orb:request-id" in keys
        assert "orb:template-id" in keys
        assert "orb:provider-api" in keys
        assert "orb:created-at" in keys
        # No legacy PascalCase keys
        assert "RequestId" not in keys
        assert "TemplateId" not in keys
        assert "CreatedBy" not in keys
        assert "ProviderApi" not in keys

    def test_tag_asg_provider_api_is_asg(self):
        handler = self._make_handler()
        template = _make_template()
        template.tags = None

        handler._tag_asg("asg-test", template, "req-123")

        call_kwargs = handler._retry_with_backoff.call_args[1]
        tags = call_kwargs["Tags"]
        provider_api = next(t["Value"] for t in tags if t["Key"] == "orb:provider-api")
        assert provider_api == "ASG"

    def test_tag_asg_propagate_at_launch_true(self):
        handler = self._make_handler()
        template = _make_template()
        template.tags = None

        handler._tag_asg("asg-test", template, "req-123")

        call_kwargs = handler._retry_with_backoff.call_args[1]
        tags = call_kwargs["Tags"]
        assert all(t["PropagateAtLaunch"] is True for t in tags)

    def test_tag_asg_user_tags_included(self):
        handler = self._make_handler()
        template = _make_template()
        template.tags = {"env": "staging"}

        handler._tag_asg("asg-test", template, "req-123")

        call_kwargs = handler._retry_with_backoff.call_args[1]
        tags = call_kwargs["Tags"]
        keys = {t["Key"] for t in tags}
        assert "env" in keys

    def test_tag_asg_orb_prefixed_template_tag_logs_warning(self):
        # _tag_asg is best-effort: it catches all exceptions and logs a warning.
        # An orb: tag key in template.tags causes build_resource_tags to raise
        # ValueError, which _tag_asg catches and logs — it does not re-raise.
        handler = self._make_handler()
        template = _make_template()
        template.tags = {"orb:request-id": "spoofed"}

        # Should not raise — the error is swallowed and logged
        handler._tag_asg("asg-test", template, "req-real")

        # Warning was logged with the reserved-namespace message
        handler._logger.warning.assert_called_once()
        warning_msg = str(handler._logger.warning.call_args)
        assert "orb:" in warning_msg or "Failed to tag" in warning_msg


# ---------------------------------------------------------------------------
# EC2Fleet handler tag tests
# ---------------------------------------------------------------------------


class TestEC2FleetHandlerTags:
    def _make_handler(self):
        handler = EC2FleetHandler(
            aws_client=MagicMock(),
            logger=MagicMock(),
            aws_ops=MagicMock(),
            launch_template_manager=MagicMock(),
            config_port=_make_config_port(),
        )
        return handler

    def _call_legacy(self, handler, fleet_type=AWSFleetType.MAINTAIN):
        template = _make_template(fleet_type=fleet_type)
        request = _make_request()
        return handler._fleet_config_builder._build_legacy(template, request, "lt-123", "1")

    def test_fleet_tags_use_orb_keys(self):
        handler = self._make_handler()
        config = self._call_legacy(handler)
        fleet_spec = next(s for s in config["TagSpecifications"] if s["ResourceType"] == "fleet")
        keys = {t["Key"] for t in fleet_spec["Tags"]}
        assert ORB_SYSTEM_KEYS.issubset(keys)
        assert "RequestId" not in keys
        assert "TemplateId" not in keys
        assert "CreatedBy" not in keys
        assert "ProviderApi" not in keys

    def test_fleet_provider_api_is_ec2fleet(self):
        handler = self._make_handler()
        config = self._call_legacy(handler)
        fleet_spec = next(s for s in config["TagSpecifications"] if s["ResourceType"] == "fleet")
        provider_api = next(
            t["Value"] for t in fleet_spec["Tags"] if t["Key"] == "orb:provider-api"
        )
        assert provider_api == "EC2Fleet"

    def test_instance_tags_absent_for_maintain(self):
        """AWS rejects ResourceType='instance' in CreateFleet for maintain fleets."""
        handler = self._make_handler()
        config = self._call_legacy(handler, fleet_type=AWSFleetType.MAINTAIN)
        resource_types = {s["ResourceType"] for s in config["TagSpecifications"]}
        assert "instance" not in resource_types

    def test_instance_tags_absent_for_request(self):
        """AWS rejects ResourceType='instance' in CreateFleet for request fleets."""
        handler = self._make_handler()
        config = self._call_legacy(handler, fleet_type=AWSFleetType.REQUEST)
        resource_types = {s["ResourceType"] for s in config["TagSpecifications"]}
        assert "instance" not in resource_types

    def test_instance_tags_present_for_instant(self):
        handler = self._make_handler()
        config = self._call_legacy(handler, fleet_type=AWSFleetType.INSTANT)
        resource_types = {s["ResourceType"] for s in config["TagSpecifications"]}
        assert "instance" in resource_types

    def test_instance_tags_use_orb_keys_for_instant(self):
        """Instance tags are only present for instant fleets — verify they use orb keys."""
        handler = self._make_handler()
        config = self._call_legacy(handler, fleet_type=AWSFleetType.INSTANT)
        inst_spec = next(s for s in config["TagSpecifications"] if s["ResourceType"] == "instance")
        keys = {t["Key"] for t in inst_spec["Tags"]}
        assert ORB_SYSTEM_KEYS.issubset(keys)


# ---------------------------------------------------------------------------
# SpotFleet handler tag tests
# ---------------------------------------------------------------------------


class TestSpotFleetHandlerTags:
    def _make_handler(self):
        handler = SpotFleetHandler(
            aws_client=MagicMock(),
            logger=MagicMock(),
            aws_ops=MagicMock(),
            launch_template_manager=MagicMock(),
            config_port=_make_config_port(),
        )
        return handler

    def _call_legacy(self, handler):
        template = _make_template(fleet_type=AWSFleetType.MAINTAIN)
        request = _make_request()
        return handler._config_builder._build_legacy(template, request, "lt-123", "1")

    def test_spot_fleet_request_tags_use_orb_keys(self):
        handler = self._make_handler()
        config = self._call_legacy(handler)
        sfr_spec = next(
            s for s in config["TagSpecifications"] if s["ResourceType"] == "spot-fleet-request"
        )
        keys = {t["Key"] for t in sfr_spec["Tags"]}
        assert ORB_SYSTEM_KEYS.issubset(keys)

    def test_spot_fleet_provider_api_is_spotfleet(self):
        handler = self._make_handler()
        config = self._call_legacy(handler)
        sfr_spec = next(
            s for s in config["TagSpecifications"] if s["ResourceType"] == "spot-fleet-request"
        )
        provider_api = next(t["Value"] for t in sfr_spec["Tags"] if t["Key"] == "orb:provider-api")
        assert provider_api == "SpotFleet"

    def test_instance_tags_absent(self):
        """AWS RequestSpotFleet rejects ResourceType='instance' in TagSpecifications."""
        handler = self._make_handler()
        config = self._call_legacy(handler)
        resource_types = {s["ResourceType"] for s in config["TagSpecifications"]}
        assert "instance" not in resource_types

    def test_only_spot_fleet_request_resource_type(self):
        """SpotFleet TagSpecifications should only contain 'spot-fleet-request'."""
        handler = self._make_handler()
        config = self._call_legacy(handler)
        resource_types = {s["ResourceType"] for s in config["TagSpecifications"]}
        assert resource_types == {"spot-fleet-request"}


# ---------------------------------------------------------------------------
# LaunchTemplate manager tag tests
# ---------------------------------------------------------------------------


class TestLaunchTemplateManagerTags:
    def _make_manager(self):
        manager = AWSLaunchTemplateManager.__new__(AWSLaunchTemplateManager)
        manager.aws_client = MagicMock()
        manager._logger = MagicMock()
        manager.config_port = _make_config_port()
        manager.aws_native_spec_service = None
        return manager

    def _make_template(self):
        t = MagicMock()
        t.template_id = "tmpl-lt"
        t.tags = None
        return t

    def _make_request(self):
        r = MagicMock()
        r.request_id = "req-lt-001"
        return r

    def test_default_provider_api_is_launch_template(self):
        manager = self._make_manager()
        tags = manager._create_instance_tags(self._make_template(), self._make_request())
        d = _tag_dict(tags)
        assert d["orb:provider-api"] == "LaunchTemplate"

    def test_custom_provider_api_is_respected(self):
        manager = self._make_manager()
        tags = manager._create_instance_tags(
            self._make_template(), self._make_request(), provider_api="ASG"
        )
        d = _tag_dict(tags)
        assert d["orb:provider-api"] == "ASG"

    def test_instance_tags_contain_all_orb_keys(self):
        manager = self._make_manager()
        tags = manager._create_instance_tags(self._make_template(), self._make_request())
        keys = {t["Key"] for t in tags}
        assert ORB_SYSTEM_KEYS.issubset(keys)
