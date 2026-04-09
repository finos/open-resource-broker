"""Tests for existing launch template handling in the AWS provider.

Covers:
- _validate_prerequisites passes when launch_template_id is set (no image_id required)
- LT manager uses existing template without creating a new one
- _has_overrides logic
- on_update_failure modes (fail vs warn)
- Tag failure resilience
- Per-request LT reuse when healthy
- TemplateDefaultsService strips network fields when launch_template_id is set
- Image resolution skipped when launch_template_id is set
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.launch_template.manager import (
    _OVERRIDE_FIELDS,
    AWSLaunchTemplateManager,
)

REGION = "eu-west-2"
FAKE_LT_ID = "lt-0123456789abcdef0"


# ---------------------------------------------------------------------------
# Shared helpers (mirror conftest pattern without importing from conftest)
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config_port(prefix: str = "") -> Any:
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = prefix
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    config_port.app_config = None
    return config_port


def _make_aws_client(region: str = REGION) -> Any:
    from orb.providers.aws.infrastructure.aws_client import AWSClient

    client = MagicMock(spec=AWSClient)
    client.ec2_client = boto3.client("ec2", region_name=region)
    client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    client.sts_client = boto3.client("sts", region_name=region)
    client.ssm_client = boto3.client("ssm", region_name=region)
    return client


def _make_request(request_id: str = "req-lt-test-001") -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = 1
    req.template_id = "tpl-lt-test"
    req.metadata = {}
    req.resource_ids = []
    req.provider_data = {}
    req.provider_api = None
    return req


def _make_lt_template(
    subnet_id: str,
    sg_id: str,
    lt_id: str = FAKE_LT_ID,
    image_id: str | None = None,
    machine_types: dict | None = None,
) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-lt-test",
        name="lt-test",
        provider_api="EC2Fleet",
        machine_types=machine_types or {"r5.large": 1},
        image_id=image_id,
        launch_template_id=lt_id,
        max_instances=10,
        price_type="spot",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
    )


def _register_lt_in_moto(ec2_client: Any, lt_id_hint: str = FAKE_LT_ID) -> str:
    """Create a real LT in moto and return its ID."""
    resp = ec2_client.create_launch_template(
        LaunchTemplateName=f"existing-{lt_id_hint}",
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "r5.large"},
    )
    return resp["LaunchTemplate"]["LaunchTemplateId"]


# ---------------------------------------------------------------------------
# TestExistingLTNoOverrides
# ---------------------------------------------------------------------------


class TestExistingLTNoOverrides:
    def test_validate_prerequisites_passes_with_lt_id_only(self, moto_vpc_resources):
        """_validate_prerequisites must not raise when launch_template_id is set and image_id is absent."""

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-no-ami",
            name="no-ami",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id=None,
            launch_template_id=FAKE_LT_ID,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        # Grab a concrete subclass to call the method — EC2FleetHandler is convenient
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler

        aws_client = MagicMock()
        logger = _make_logger()
        config_port = _make_config_port()
        lt_manager = MagicMock(spec=AWSLaunchTemplateManager)
        aws_ops = MagicMock()

        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=lt_manager,
            config_port=config_port,
        )

        # Should not raise
        handler._validate_prerequisites(template)

    def test_lt_manager_uses_existing_template_without_creating_new(
        self, moto_aws, moto_vpc_resources
    ):
        """When launch_template_id is set and no overrides, manager returns existing LT without creating."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        # Register the LT in moto first
        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        # Use a mock template where all _OVERRIDE_FIELDS are None so _has_overrides returns False
        template = MagicMock(spec=AWSTemplate)
        template.launch_template_id = real_lt_id
        template.launch_template_version = None
        for field in _OVERRIDE_FIELDS:
            setattr(template, field, None)

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        request = _make_request()
        result = manager.create_or_update_launch_template(template, request)

        assert result.template_id == real_lt_id
        assert result.is_new_template is False
        assert result.is_new_version is False

    def test_has_overrides_returns_false_when_no_override_fields(self, moto_vpc_resources):
        """_has_overrides returns False when all _OVERRIDE_FIELDS are None on the template."""
        aws_client = MagicMock()
        logger = _make_logger()
        config_port = _make_config_port()

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Use a mock so all _OVERRIDE_FIELDS can be set to None
        # (AWSTemplate model defaults machine_types to {} which is not None)
        template = MagicMock(spec=AWSTemplate)
        template.launch_template_id = FAKE_LT_ID
        for field in _OVERRIDE_FIELDS:
            setattr(template, field, None)

        assert manager._has_overrides(template) is False


# ---------------------------------------------------------------------------
# TestExistingLTWithImageIdOverride
# ---------------------------------------------------------------------------


class TestExistingLTWithImageIdOverride:
    def test_has_overrides_returns_true_when_image_id_set(self, moto_vpc_resources):
        """_has_overrides returns True when image_id is set alongside launch_template_id."""
        aws_client = MagicMock()
        logger = _make_logger()
        config_port = _make_config_port()

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-override-ami",
            name="override-ami",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-override99",
            launch_template_id=FAKE_LT_ID,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        assert manager._has_overrides(template) is True

    def test_new_lt_version_created_when_image_id_override_set(self, moto_aws, moto_vpc_resources):
        """When image_id override is set, a new LT version is created on the existing template."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-img-override",
            name="img-override",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-newimage00",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        request = _make_request()
        result = manager.create_or_update_launch_template(template, request)

        assert result.template_id == real_lt_id
        assert result.is_new_version is True
        # Version 2 was created (version 1 was the original)
        assert int(result.version) >= 2


# ---------------------------------------------------------------------------
# TestExistingLTWithOverridePermissionFailure
# ---------------------------------------------------------------------------


class TestExistingLTWithOverridePermissionFailure:
    def test_on_update_failure_defaults_to_fail_when_config_absent(
        self, moto_aws, moto_vpc_resources
    ):
        """When on_update_failure is not configured, a version-create failure raises."""

        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()
        # Make provider_config return no launch_template attribute
        config_port.get_provider_config.return_value = MagicMock(spec=[])

        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-fail-mode",
            name="fail-mode",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-failmode00",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Patch _create_new_lt_version to simulate a failure
        def _boom(aws_template, request):
            raise RuntimeError("simulated permission denied")

        manager._create_new_lt_version = _boom

        from orb.providers.aws.exceptions.aws_exceptions import InfrastructureError

        with pytest.raises((RuntimeError, InfrastructureError)):
            manager.create_or_update_launch_template(template, _make_request())

    def test_on_update_failure_warn_falls_back_to_existing_lt(self, moto_aws, moto_vpc_resources):
        """When on_update_failure='warn', a version-create failure falls back to the existing LT."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        # Configure on_update_failure = 'warn'
        lt_config = MagicMock()
        lt_config.on_update_failure = "warn"
        provider_config = MagicMock()
        provider_config.launch_template = lt_config
        config_port.get_provider_config.return_value = provider_config

        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-warn-mode",
            name="warn-mode",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-warnmode00",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Patch _create_new_lt_version to simulate a failure
        def _boom(aws_template, request):
            raise RuntimeError("simulated permission denied")

        manager._create_new_lt_version = _boom

        result = manager.create_or_update_launch_template(template, _make_request())

        # Should fall back to the existing template
        assert result.template_id == real_lt_id
        assert result.is_new_template is False
        assert result.is_new_version is False
        logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestTagFailureResilience
# ---------------------------------------------------------------------------


class TestTagFailureResilience:
    def test_create_tags_failure_does_not_abort_lt_creation(self, moto_aws, moto_vpc_resources):
        """A create_tags failure during LT creation is swallowed and the LT result is still returned."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        # Template without launch_template_id so a new one is created
        template = AWSTemplate(
            template_id="tpl-tag-fail",
            name="tag-fail",
            provider_api="EC2Fleet",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            price_type="ondemand",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Make create_tags raise
        aws_client.ec2_client.create_tags = MagicMock(side_effect=Exception("tags denied"))

        request = _make_request("req-tag-fail-001")
        result = manager.create_or_update_launch_template(template, request)

        # LT was still created despite tag failure
        assert result.template_id.startswith("lt-")
        assert result.is_new_template is True

    def test_orb_prefixed_tags_in_build_resource_tags_are_stripped(self):
        """User-supplied tags with 'orb:' prefix are stripped from the merged tag list."""
        from orb.providers.aws.infrastructure.tags import build_resource_tags

        config_port = _make_config_port()
        config_port.get_resource_prefix.return_value = "orb-"

        tags = build_resource_tags(
            config_port=config_port,
            request_id="req-strip-001",
            template_id="tpl-strip",
            resource_prefix_key="fleet",
            provider_api="EC2Fleet",
            template_tags={
                "Environment": "test",
                "orb:custom-user-tag": "should-be-stripped",
            },
        )

        keys = [t["Key"] for t in tags]
        assert "orb:custom-user-tag" not in keys
        assert "Environment" in keys


# ---------------------------------------------------------------------------
# TestLTInDeletingState
# ---------------------------------------------------------------------------


class TestLTInDeletingState:
    def test_per_request_lt_reuses_existing_when_healthy(self, moto_aws, moto_vpc_resources):
        """_create_per_request_version reuses the existing LT when it already exists and is healthy."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-reuse",
            name="reuse",
            provider_api="EC2Fleet",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            price_type="ondemand",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        request = _make_request("req-reuse-001")

        # First call creates the LT
        result1 = manager.create_or_update_launch_template(template, request)
        assert result1.is_new_template is True

        # Second call with same request/template should reuse
        result2 = manager.create_or_update_launch_template(template, request)
        assert result2.is_new_template is False
        assert result2.template_id == result1.template_id


# ---------------------------------------------------------------------------
# TestProviderDefaultsNotBleeding
# ---------------------------------------------------------------------------


class TestProviderDefaultsNotBleeding:
    def _make_template_defaults_service(self, subnet_ids: list[str], sg_id: str):
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        config_manager = MagicMock()
        logger = _make_logger()

        # Global template defaults return nothing relevant
        template_config = MagicMock()
        template_config.model_dump.return_value = {}
        config_manager.get_template_config.return_value = template_config

        # Provider instance defaults include subnet/sg/image
        provider_config = MagicMock()
        provider_config.providers = [
            MagicMock(
                name="aws-test",
                type="aws",
                template_defaults={
                    "subnet_ids": subnet_ids,
                    "security_group_ids": [sg_id],
                    "image_id": "ami-default-from-provider",
                    "machine_types": {"t3.micro": 1},
                },
            )
        ]
        provider_config.provider_defaults = {}
        config_manager.get_provider_config.return_value = provider_config

        return TemplateDefaultsService(config_manager=config_manager, logger=logger)

    def test_template_defaults_service_strips_network_fields_when_lt_set(self, moto_vpc_resources):
        """resolve_template_defaults must not inject subnet_ids/security_group_ids when launch_template_id is set."""
        subnet_ids = moto_vpc_resources["subnet_ids"]
        sg_id = moto_vpc_resources["sg_id"]

        service = self._make_template_defaults_service(subnet_ids, sg_id)

        template_dict = {
            "template_id": "tpl-lt-no-bleed",
            "launch_template_id": FAKE_LT_ID,
            "provider_api": "EC2Fleet",
            "max_instances": 10,
            "price_type": "spot",
        }

        result = service.resolve_template_defaults(template_dict, provider_instance_name="aws-test")

        # Network fields from provider defaults must NOT bleed in
        assert (
            "subnet_ids" not in result
            or result.get("subnet_ids") is None
            or result.get("subnet_ids") == []
        )
        assert (
            "security_group_ids" not in result
            or result.get("security_group_ids") is None
            or result.get("security_group_ids") == []
        )
        # image_id must also be stripped
        assert result.get("image_id") is None or "image_id" not in result

    def test_image_resolution_skipped_when_lt_set(self, moto_vpc_resources):
        """resolve_template_defaults does not inject image_id when launch_template_id is present."""
        subnet_ids = moto_vpc_resources["subnet_ids"]
        sg_id = moto_vpc_resources["sg_id"]

        service = self._make_template_defaults_service(subnet_ids, sg_id)

        template_dict = {
            "template_id": "tpl-lt-no-ami",
            "launch_template_id": FAKE_LT_ID,
            "provider_api": "EC2Fleet",
            "max_instances": 5,
            "price_type": "spot",
        }

        result = service.resolve_template_defaults(template_dict, provider_instance_name="aws-test")

        # image_id from provider defaults must not appear
        assert result.get("image_id") is None or "image_id" not in result
        # launch_template_id must be preserved
        assert result.get("launch_template_id") == FAKE_LT_ID


# ---------------------------------------------------------------------------
# TestExistingLTEdgeCases
# ---------------------------------------------------------------------------


class TestExistingLTEdgeCases:
    def test_lt_exists_without_image_id_in_lt(self, moto_aws, moto_vpc_resources):
        """LT with no ImageId in LaunchTemplateData + AWSTemplate with image_id=None.

        create_or_update_launch_template must return is_new_template=False,
        is_new_version=False and must not raise.
        """
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        # Create a moto LT with NO ImageId
        resp = aws_client.ec2_client.create_launch_template(
            LaunchTemplateName="existing-no-image",
            LaunchTemplateData={"InstanceType": "r5.large"},
        )
        real_lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]

        # AWSTemplate with launch_template_id set, image_id=None — no overrides
        template = MagicMock(spec=AWSTemplate)
        template.launch_template_id = real_lt_id
        template.launch_template_version = None
        for field in _OVERRIDE_FIELDS:
            setattr(template, field, None)

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        result = manager.create_or_update_launch_template(template, _make_request())

        assert result.is_new_template is False
        assert result.is_new_version is False
        assert result.template_id == real_lt_id

    def test_image_id_override_written_to_moto_version(self, moto_aws, moto_vpc_resources):
        """image_id override must appear in the new LT version's LaunchTemplateData."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        # Create moto LT with original ImageId
        resp = aws_client.ec2_client.create_launch_template(
            LaunchTemplateName="existing-with-image",
            LaunchTemplateData={"ImageId": "ami-original", "InstanceType": "r5.large"},
        )
        real_lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-img-written",
            name="img-written",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-newimage00",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        result = manager.create_or_update_launch_template(template, _make_request())

        assert result.is_new_version is True
        assert int(result.version) >= 2

        # Verify the new version actually has the overridden ImageId
        versions = aws_client.ec2_client.describe_launch_template_versions(
            LaunchTemplateId=real_lt_id,
            Versions=[result.version],
        )
        lt_data = versions["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        assert lt_data.get("ImageId") == "ami-newimage00"

    def test_mixed_instance_types_no_instance_type_in_lt_version(
        self, moto_aws, moto_vpc_resources
    ):
        """With multiple machine_types, the LT version carries exactly one InstanceType.

        _create_new_lt_version picks the first key from machine_types as a base type.
        Fleet-level overrides (EC2Fleet LaunchTemplateOverrides) handle the diversity;
        the LT itself carries only a single fallback type, not a list.
        """
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        resp = aws_client.ec2_client.create_launch_template(
            LaunchTemplateName="existing-mixed",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "r5.large"},
        )
        real_lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        # Multiple machine_types — fleet-level concern
        template = AWSTemplate(
            template_id="tpl-mixed-types",
            name="mixed-types",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1, "m5.large": 2},
            image_id="ami-newimage00",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        result = manager.create_or_update_launch_template(template, _make_request())

        assert result.is_new_version is True

        versions = aws_client.ec2_client.describe_launch_template_versions(
            LaunchTemplateId=real_lt_id,
            Versions=[result.version],
        )
        lt_data = versions["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        # When subnet_ids are present, _create_new_lt_version puts network config into
        # NetworkInterfaces. InstanceType is not set at the top level in that path —
        # fleet-level LaunchTemplateOverrides carry the per-type diversity.
        assert "NetworkInterfaces" in lt_data
        assert "InstanceType" not in lt_data

    def test_mixed_purchasing_no_market_options_in_lt(self, moto_aws, moto_vpc_resources):
        """price_type='mixed' must not inject InstanceMarketOptions into the LT version."""
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        resp = aws_client.ec2_client.create_launch_template(
            LaunchTemplateName="existing-mixed-purchasing",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "r5.large"},
        )
        real_lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-mixed-purchasing",
            name="mixed-purchasing",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-newimage00",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="mixed",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        result = manager.create_or_update_launch_template(template, _make_request())

        assert result.is_new_version is True

        versions = aws_client.ec2_client.describe_launch_template_versions(
            LaunchTemplateId=real_lt_id,
            Versions=[result.version],
        )
        lt_data = versions["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        assert "InstanceMarketOptions" not in lt_data


# ---------------------------------------------------------------------------
# TestFailureModeConfig
# ---------------------------------------------------------------------------


class TestFailureModeConfig:
    def _make_config_port_with_lt_config(self, on_update_failure: str) -> Any:
        """Return a config_port whose provider_config has launch_template.on_update_failure set."""
        from orb.providers.aws.configuration.config import LaunchTemplateConfiguration

        config_port = _make_config_port()
        lt_config = LaunchTemplateConfiguration(on_update_failure=on_update_failure)  # type: ignore[call-arg]
        provider_config = MagicMock()
        provider_config.launch_template = lt_config
        config_port.get_provider_config.return_value = provider_config
        return config_port

    def _make_config_port_with_tag_config(self, on_tag_failure: str) -> Any:
        """Return a config_port whose provider_config has tagging.on_tag_failure set."""
        from orb.providers.aws.configuration.config import TaggingConfiguration

        config_port = _make_config_port()
        tag_config = TaggingConfiguration(on_tag_failure=on_tag_failure)  # type: ignore[call-arg]
        provider_config = MagicMock()
        provider_config.tagging = tag_config
        # Also expose launch_template so _get_lt_update_failure_mode doesn't blow up
        from orb.providers.aws.configuration.config import LaunchTemplateConfiguration

        provider_config.launch_template = LaunchTemplateConfiguration()  # type: ignore[call-arg]
        config_port.get_provider_config.return_value = provider_config
        return config_port

    def test_on_update_failure_fail_explicit_config_present(self, moto_aws, moto_vpc_resources):
        """on_update_failure='fail' in explicit config raises InfrastructureError on version failure."""
        from botocore.exceptions import ClientError as BotoCE

        from orb.providers.aws.exceptions.aws_exceptions import InfrastructureError

        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = self._make_config_port_with_lt_config("fail")

        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-fail-explicit",
            name="fail-explicit",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-failexplicit",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Simulate an UnauthorizedOperation ClientError from create_launch_template_version
        error_response = {"Error": {"Code": "UnauthorizedOperation", "Message": "not authorized"}}

        def _boom(aws_template, request):
            raise BotoCE(
                error_response=error_response, operation_name="CreateLaunchTemplateVersion"
            )

        manager._create_new_lt_version = _boom

        with pytest.raises((BotoCE, InfrastructureError)):
            manager.create_or_update_launch_template(template, _make_request())

    def test_on_tag_failure_fail_on_version_path(self, moto_aws, moto_vpc_resources):
        """on_tag_failure='fail' must propagate a create_tags error on the version path."""
        from botocore.exceptions import ClientError as BotoCE

        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = self._make_config_port_with_tag_config("fail")

        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-tag-fail-version",
            name="tag-fail-version",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-tagfailver0",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Patch create_tags to raise AccessDenied
        error_response = {"Error": {"Code": "AccessDenied", "Message": "access denied"}}
        aws_client.ec2_client.create_tags = MagicMock(
            side_effect=BotoCE(error_response=error_response, operation_name="CreateTags")
        )

        with pytest.raises(Exception):
            manager.create_or_update_launch_template(template, _make_request())

    def test_on_tag_failure_warn_on_version_path(self, moto_aws, moto_vpc_resources):
        """on_tag_failure='warn' must not raise on create_tags error; result is still returned."""
        from botocore.exceptions import ClientError as BotoCE

        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = self._make_config_port_with_tag_config("warn")

        real_lt_id = _register_lt_in_moto(aws_client.ec2_client)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-tag-warn-version",
            name="tag-warn-version",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id="ami-tagwarnver0",
            launch_template_id=real_lt_id,
            max_instances=5,
            price_type="spot",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        manager = AWSLaunchTemplateManager(
            aws_client=aws_client,
            logger=logger,
            config_port=config_port,
        )

        # Patch create_tags to raise AccessDenied
        error_response = {"Error": {"Code": "AccessDenied", "Message": "access denied"}}
        aws_client.ec2_client.create_tags = MagicMock(
            side_effect=BotoCE(error_response=error_response, operation_name="CreateTags")
        )

        result = manager.create_or_update_launch_template(template, _make_request())

        assert result is not None
        assert result.template_id == real_lt_id
        logger.warning.assert_called()
