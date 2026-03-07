"""Tests verifying that fleet_role resolution runs before validation in SpotFleetHandler.

The regression: _validate_spot_prerequisites ran before _resolve_fleet_role, so
validation failed with "Fleet role ARN is required" even when a fleet_role was
available in config.
"""

from unittest.mock import MagicMock

import pytest

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.validator import SpotFleetValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)


def _make_template(fleet_role=None, **kwargs) -> AWSTemplate:
    defaults = dict(
        template_id="tmpl-spot-1",
        name="test-spot",
        provider_api="SpotFleet",
        machine_types={"t3.medium": 1},
        image_id="ami-12345678",
        subnet_ids=["subnet-aaa"],
        security_group_ids=["sg-bbb"],
        price_type="spot",
        fleet_type="request",
        fleet_role=fleet_role,
    )
    defaults.update(kwargs)
    return AWSTemplate(**defaults)


def _make_handler(config_port=None, validator=None) -> SpotFleetHandler:
    """Build a SpotFleetHandler with all AWS dependencies mocked."""
    aws_client = MagicMock()
    aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}

    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    launch_template_manager.create_or_update_launch_template.return_value = MagicMock(
        template_id="lt-abc", version="1"
    )

    handler = SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=launch_template_manager,
        config_port=config_port,
        spot_fleet_validator=validator,
    )
    return handler


# ---------------------------------------------------------------------------
# Call-order tests
# ---------------------------------------------------------------------------


class TestResolveBeforeValidate:
    """_resolve_fleet_role must be called before _validate_spot_prerequisites."""

    def test_resolve_called_before_validate(self):
        """Track call order via side effects on the handler's own methods."""
        handler = _make_handler()

        call_order = []

        def tracking_resolve(template):
            call_order.append("resolve")
            # Return template with fleet_role populated so validation passes
            return template.model_copy(update={"fleet_role": VALID_FLEET_ROLE})

        def tracking_validate(template):
            call_order.append("validate")

        handler._resolve_fleet_role = tracking_resolve
        handler._validate_spot_prerequisites = tracking_validate

        # Stub out the rest of _create_spot_fleet_with_response after validation
        handler.launch_template_manager.create_or_update_launch_template.return_value = MagicMock(
            template_id="lt-1", version="1"
        )
        handler._config_builder = MagicMock()
        handler._config_builder.build.return_value = {}
        handler._retry_with_backoff = MagicMock(return_value={"SpotFleetRequestId": "sfr-test-123"})

        template = _make_template(fleet_role=None)
        request = MagicMock()
        request.request_id = "req-test"

        handler._create_spot_fleet_with_response(request, template)

        assert call_order == ["resolve", "validate"], (
            f"Expected resolve then validate, got: {call_order}"
        )

    def test_validate_receives_resolved_fleet_role(self):
        """The template passed to _validate_spot_prerequisites must have fleet_role set."""
        handler = _make_handler()

        validated_templates = []

        def tracking_validate(template):
            validated_templates.append(template)

        handler._validate_spot_prerequisites = tracking_validate
        handler._config_builder = MagicMock()
        handler._config_builder.build.return_value = {}
        handler._retry_with_backoff = MagicMock(return_value={"SpotFleetRequestId": "sfr-test-456"})

        # Template starts with no fleet_role; _resolve_fleet_role will populate it
        # by calling STS (aws_client is already mocked to return account 123456789012)
        template = _make_template(
            fleet_role="AWSServiceRoleForEC2SpotFleet"  # short name triggers resolution
        )
        request = MagicMock()
        request.request_id = "req-test"

        handler._create_spot_fleet_with_response(request, template)

        assert validated_templates, "validate was never called"
        validated = validated_templates[0]
        assert validated.fleet_role is not None, (
            "fleet_role must be populated before validation runs"
        )
        assert validated.fleet_role != "AWSServiceRoleForEC2SpotFleet", (
            "fleet_role must be resolved to a full ARN before validation"
        )


# ---------------------------------------------------------------------------
# Functional: template with resolvable fleet_role passes validation
# ---------------------------------------------------------------------------


class TestFleetRoleResolutionPassesValidation:
    """End-to-end: a template with no fleet_role but config-provided role succeeds."""

    def test_config_fleet_role_resolves_and_passes(self):
        """When fleet_role is None but config provides a short name, it resolves and passes."""
        config_port = MagicMock()
        provider_config = MagicMock()
        provider = MagicMock()
        provider.name = "my-provider"
        # Use the short name so _resolve_fleet_role expands it to a full ARN via STS
        provider.config = {"fleet_role": "AWSServiceRoleForEC2SpotFleet"}
        provider_config.providers = [provider]
        config_port.get_provider_config.return_value = provider_config
        config_port.get_active_provider_override.return_value = None

        # Use a real validator so we confirm it actually passes
        aws_client = MagicMock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        logger = MagicMock()
        validator = SpotFleetValidator(aws_client=aws_client, logger=logger)

        handler = _make_handler(config_port=config_port, validator=validator)
        handler.aws_client = aws_client
        handler._config_builder = MagicMock()
        handler._config_builder.build.return_value = {}
        handler._retry_with_backoff = MagicMock(
            return_value={"SpotFleetRequestId": "sfr-config-role"}
        )

        template = _make_template(fleet_role=None)
        request = MagicMock()
        request.request_id = "req-config-role"

        # Should not raise — fleet_role resolved from config before validation
        result = handler._create_spot_fleet_with_response(request, template)
        assert result["SpotFleetRequestId"] == "sfr-config-role"

    def test_no_fleet_role_anywhere_fails_validation(self):
        """When fleet_role is None and config has none either, validation raises."""
        from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError

        config_port = MagicMock()
        config_port.get_provider_config.return_value = None

        aws_client = MagicMock()
        logger = MagicMock()
        validator = SpotFleetValidator(aws_client=aws_client, logger=logger)

        handler = _make_handler(config_port=config_port, validator=validator)
        handler.aws_client = aws_client

        template = _make_template(fleet_role=None)
        request = MagicMock()
        request.request_id = "req-no-role"

        with pytest.raises(AWSValidationError, match="Fleet role ARN is required"):
            handler._create_spot_fleet_with_response(request, template)


# ---------------------------------------------------------------------------
# Validator unit tests (standalone)
# ---------------------------------------------------------------------------


class TestSpotFleetValidatorFleetRole:
    """SpotFleetValidator.validate() fleet_role checks."""

    def setup_method(self):
        self.aws_client = MagicMock()
        self.logger = MagicMock()
        self.validator = SpotFleetValidator(aws_client=self.aws_client, logger=self.logger)

    def test_missing_fleet_role_raises(self):
        from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError

        template = _make_template(fleet_role=None)
        with pytest.raises(AWSValidationError, match="Fleet role ARN is required"):
            self.validator.validate(template)

    def test_valid_service_linked_role_passes(self):
        template = _make_template(fleet_role=VALID_FLEET_ROLE)
        # Should not raise
        self.validator.validate(template)

    def test_valid_tagging_role_passes(self):
        tagging_role = "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role"
        template = _make_template(fleet_role=tagging_role)
        self.validator.validate(template)

    def test_fleet_role_populated_after_resolution_passes(self):
        """Simulate what _resolve_fleet_role does: short name → full ARN."""
        template = _make_template(fleet_role=VALID_FLEET_ROLE)
        # Should not raise
        self.validator.validate(template)
