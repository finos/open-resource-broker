"""Unit tests for LaunchTemplateCleanupService.

Covers:
- get_cleanup_config: defaults when config_port is None; reads from provider config
- cleanup_on_zero_capacity: no-op when cleanup disabled; no-op when resource type excluded
- delete_orb_launch_template: ownership check (orb:managed-by tag); dry_run skips delete;
  happy-path delete; missing template is a no-op; ClientError on describe is warned-only
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.configuration.cleanup_config import CleanupConfig
from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
    LaunchTemplateCleanupService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    *,
    config_port=None,
    describe_response=None,
    delete_raises=None,
) -> tuple[LaunchTemplateCleanupService, MagicMock, MagicMock]:
    """Return (service, mock_ec2_client, mock_logger)."""
    mock_ec2 = MagicMock()
    mock_ec2_client = MagicMock()
    mock_ec2_client.ec2_client = mock_ec2

    if describe_response is not None:
        mock_ec2.describe_launch_templates.return_value = describe_response
    if delete_raises is not None:
        mock_ec2.delete_launch_template.side_effect = delete_raises

    if config_port is not None and not isinstance(
        config_port.get_resource_prefix.return_value, (str, type(None), int)
    ):
        config_port.get_resource_prefix.return_value = "orb-"

    mock_logger = MagicMock()
    svc = LaunchTemplateCleanupService(
        aws_client=mock_ec2_client,
        config_port=config_port,
        logger=mock_logger,
    )
    return svc, mock_ec2, mock_logger


def _orb_lt(lt_id: str, request_id: str, name: str | None = None) -> dict:
    """Build a minimal describe_launch_templates LaunchTemplate entry.

    Tag format matches the EC2 describe_launch_templates response shape:
    each tag is ``{"Key": str, "Value": str}`` (singular Value).
    """
    return {
        "LaunchTemplateId": lt_id,
        "LaunchTemplateName": name or f"orb-{request_id}-tpl",
        "Tags": [
            {"Key": "orb:request-id", "Value": request_id},
            {"Key": "orb:managed-by", "Value": "open-resource-broker"},
        ],
    }


def _unmanaged_lt(lt_id: str) -> dict:
    """A launch template without the ORB ownership tag."""
    return {
        "LaunchTemplateId": lt_id,
        "LaunchTemplateName": "not-orb-managed",
        "Tags": [],
    }


# ---------------------------------------------------------------------------
# get_cleanup_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.providers
class TestGetCleanupConfig:
    def test_returns_default_when_config_port_is_none(self):
        """No config_port → default CleanupConfig (all enabled)."""
        svc, _, _ = _make_service(config_port=None)

        cfg = svc.get_cleanup_config()

        assert isinstance(cfg, CleanupConfig)
        assert cfg.enabled is True
        assert cfg.delete_launch_template is True
        assert cfg.dry_run is False

    def test_reads_from_provider_config(self):
        """Parses CleanupConfig from provider_config when present."""
        mock_config_port = MagicMock()
        cleanup_data = {"enabled": False, "dry_run": True, "delete_launch_template": False}
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup=cleanup_data)}
        )

        svc, _, _ = _make_service(config_port=mock_config_port)
        cfg = svc.get_cleanup_config()

        assert cfg.enabled is False
        assert cfg.dry_run is True
        assert cfg.delete_launch_template is False

    def test_returns_default_when_provider_config_missing_aws_key(self):
        """Falls back to defaults when provider_defaults has no 'aws' entry."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(provider_defaults={})

        svc, _, _ = _make_service(config_port=mock_config_port)
        cfg = svc.get_cleanup_config()

        assert cfg.enabled is True

    def test_returns_default_on_exception(self):
        """Any exception during config read falls back to defaults (and warns)."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.side_effect = RuntimeError("oops")

        svc, _, mock_logger = _make_service(config_port=mock_config_port)
        cfg = svc.get_cleanup_config()

        assert cfg.enabled is True
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# cleanup_on_zero_capacity
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.providers
class TestCleanupOnZeroCapacity:
    def test_no_op_when_config_port_is_none(self):
        """config_port=None → skip silently, no describe/delete calls."""
        svc, mock_ec2, _ = _make_service(config_port=None)

        svc.cleanup_on_zero_capacity("asg", "req-123")

        mock_ec2.describe_launch_templates.assert_not_called()

    def test_no_op_when_cleanup_disabled(self):
        """cleanup.enabled=False → skip before hitting EC2."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": False})}
        )

        svc, mock_ec2, _ = _make_service(config_port=mock_config_port)
        svc.cleanup_on_zero_capacity("asg", "req-123")

        mock_ec2.describe_launch_templates.assert_not_called()

    def test_no_op_when_resource_type_excluded(self):
        """cleanup.resources.asg=False → skip for asg resource type."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={
                "aws": MagicMock(cleanup={"enabled": True, "resources": {"asg": False}})
            }
        )

        svc, mock_ec2, _ = _make_service(config_port=mock_config_port)
        svc.cleanup_on_zero_capacity("asg", "req-123")

        mock_ec2.describe_launch_templates.assert_not_called()

    def test_delegates_to_delete_when_enabled(self):
        """When cleanup is enabled and resource included, delete is attempted."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True})}
        )

        svc, mock_ec2, _ = _make_service(
            config_port=mock_config_port,
            describe_response={"LaunchTemplates": [_orb_lt("lt-abc", "req-789")]},
        )
        svc.cleanup_on_zero_capacity("asg", "req-789")

        mock_ec2.describe_launch_templates.assert_called_once()


# ---------------------------------------------------------------------------
# delete_orb_launch_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.providers
class TestDeleteOrbLaunchTemplate:
    def test_no_op_when_config_port_is_none(self):
        """config_port=None → warns and returns without touching EC2."""
        svc, mock_ec2, mock_logger = _make_service(config_port=None)

        svc.delete_orb_launch_template("req-none")

        mock_ec2.describe_launch_templates.assert_not_called()
        mock_logger.warning.assert_called_once()

    def test_no_op_when_no_templates_found(self):
        """Empty describe response → debug log, no delete call."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True})}
        )

        svc, mock_ec2, mock_logger = _make_service(
            config_port=mock_config_port,
            describe_response={"LaunchTemplates": []},
        )
        svc.delete_orb_launch_template("req-empty")

        mock_ec2.delete_launch_template.assert_not_called()
        mock_logger.debug.assert_called()

    def test_skips_unmanaged_template(self):
        """A template without the orb:managed-by tag is NOT deleted."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True})}
        )

        svc, mock_ec2, mock_logger = _make_service(
            config_port=mock_config_port,
            describe_response={"LaunchTemplates": [_unmanaged_lt("lt-external")]},
        )
        svc.delete_orb_launch_template("req-external")

        mock_ec2.delete_launch_template.assert_not_called()
        mock_logger.warning.assert_called()

    def test_dry_run_skips_delete(self):
        """dry_run=True logs intent but does not call delete_launch_template."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True, "dry_run": True})}
        )

        svc, mock_ec2, mock_logger = _make_service(
            config_port=mock_config_port,
            describe_response={"LaunchTemplates": [_orb_lt("lt-dry", "req-dry")]},
        )
        svc.delete_orb_launch_template("req-dry")

        mock_ec2.delete_launch_template.assert_not_called()
        mock_logger.info.assert_called()  # "[dry-run] Would delete …"

    def test_deletes_orb_managed_template(self):
        """ORB-managed template (correct tag) is deleted and success is logged."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True, "dry_run": False})}
        )

        lt = _orb_lt("lt-real", "req-real")
        svc, mock_ec2, mock_logger = _make_service(
            config_port=mock_config_port,
            describe_response={"LaunchTemplates": [lt]},
        )
        svc.delete_orb_launch_template("req-real")

        mock_ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-real")
        mock_logger.info.assert_called()

    @staticmethod
    def _config_port(prefix="orb-"):
        port = MagicMock()
        port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True, "dry_run": False})}
        )
        port.get_resource_prefix.return_value = prefix
        return port

    def test_template_with_orb_tags_but_foreign_name_is_kept(self):
        """Tags alone do not authorise deletion; the name must match ORB's scheme."""
        lt = _orb_lt("lt-user", "req-1", name="my-own-template")
        svc, mock_ec2, mock_logger = _make_service(
            config_port=self._config_port(), describe_response={"LaunchTemplates": [lt]}
        )

        svc.delete_orb_launch_template("req-1")

        mock_ec2.delete_launch_template.assert_not_called()
        assert any("keeping it" in str(c.args[0]) for c in mock_logger.info.call_args_list)

    def test_template_named_with_a_previous_prefix_is_kept(self):
        lt = _orb_lt("lt-old", "req-1", name="old-req-1-tpl")
        svc, mock_ec2, _ = _make_service(
            config_port=self._config_port("orb-"), describe_response={"LaunchTemplates": [lt]}
        )

        svc.delete_orb_launch_template("req-1")

        mock_ec2.delete_launch_template.assert_not_called()

    def test_empty_prefix_deletes_only_templates_named_for_the_request(self):
        own = _orb_lt("lt-own", "req-1", name="req-1-tpl")
        foreign = _orb_lt("lt-foreign", "req-1", name="something-else")
        svc, mock_ec2, _ = _make_service(
            config_port=self._config_port(""),
            describe_response={"LaunchTemplates": [own, foreign]},
        )

        svc.delete_orb_launch_template("req-1")

        mock_ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-own")

    @pytest.mark.parametrize("prefix", [None, 5])
    def test_unreadable_prefix_deletes_nothing(self, prefix):
        lt = _orb_lt("lt-1", "req-1")
        svc, mock_ec2, mock_logger = _make_service(
            config_port=self._config_port(), describe_response={"LaunchTemplates": [lt]}
        )
        svc._config_port.get_resource_prefix.return_value = prefix

        svc.delete_orb_launch_template("req-1")

        mock_ec2.delete_launch_template.assert_not_called()
        mock_logger.warning.assert_called()

    def test_prefix_lookup_failure_deletes_nothing(self):
        lt = _orb_lt("lt-1", "req-1")
        svc, mock_ec2, _ = _make_service(
            config_port=self._config_port(), describe_response={"LaunchTemplates": [lt]}
        )
        svc._config_port.get_resource_prefix.side_effect = RuntimeError("boom")

        svc.delete_orb_launch_template("req-1")

        mock_ec2.delete_launch_template.assert_not_called()

    def test_request_id_that_extends_another_does_not_cross_delete(self):
        mine = _orb_lt("lt-r1", "R1", name="R1-tpl")
        other = _orb_lt("lt-r1-extra", "R1-extra", name="R1-extra-tpl")
        svc, mock_ec2, _ = _make_service(
            config_port=self._config_port(""),
            describe_response={"LaunchTemplates": [mine, other]},
        )

        svc.delete_orb_launch_template("R1")

        mock_ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-r1")

    def test_client_error_on_delete_is_warned_not_raised(self):
        """ClientError during delete is caught and warned; no exception propagates."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True, "dry_run": False})}
        )

        client_error = ClientError(
            {"Error": {"Code": "InvalidLaunchTemplateId", "Message": "not found"}},
            "DeleteLaunchTemplate",
        )
        lt = _orb_lt("lt-gone", "req-gone")
        svc, mock_ec2, mock_logger = _make_service(
            config_port=mock_config_port,
            describe_response={"LaunchTemplates": [lt]},
            delete_raises=client_error,
        )

        # Must not raise
        svc.delete_orb_launch_template("req-gone")

        mock_logger.warning.assert_called()

    def test_client_error_on_describe_is_warned_not_raised(self):
        """ClientError during describe is caught and warned; no exception propagates."""
        mock_config_port = MagicMock()
        mock_config_port.get_provider_config.return_value = MagicMock(
            provider_defaults={"aws": MagicMock(cleanup={"enabled": True})}
        )

        client_error = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}},
            "DescribeLaunchTemplates",
        )
        mock_ec2_client = MagicMock()
        mock_ec2 = MagicMock()
        mock_ec2.describe_launch_templates.side_effect = client_error
        mock_ec2_client.ec2_client = mock_ec2
        mock_logger = MagicMock()

        svc = LaunchTemplateCleanupService(
            aws_client=mock_ec2_client,
            config_port=mock_config_port,
            logger=mock_logger,
        )

        # Must not raise
        svc.delete_orb_launch_template("req-describe-fail")

        mock_logger.warning.assert_called()
