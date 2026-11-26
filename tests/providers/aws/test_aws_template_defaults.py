"""Tests for AWS template fleet_type default assignment."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSFleetType, ProviderApi


class TestAWSTemplateDefaults:
    """Test AWS template default assignment logic."""

    def test_ec2_fleet_gets_default_fleet_type(self):
        """Test that EC2Fleet handler gets default fleet_type when not specified."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.EC2_FLEET,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should auto-assign fleet_type based on simple default (no config dependency)
        assert template.fleet_type is not None
        assert isinstance(template.fleet_type, AWSFleetType)
        # Default should be REQUEST (simple fallback)
        assert template.fleet_type == AWSFleetType.REQUEST

    def test_spot_fleet_gets_default_fleet_type(self):
        """Test that SpotFleet handler gets default fleet_type when not specified."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.SPOT_FLEET,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should auto-assign fleet_type based on simple default (no config dependency)
        assert template.fleet_type is not None
        assert isinstance(template.fleet_type, AWSFleetType)
        # Default should be REQUEST (simple fallback)
        assert template.fleet_type == AWSFleetType.REQUEST

    def test_run_instances_no_fleet_type(self):
        """Test that RunInstances handler doesn't get fleet_type."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.RUN_INSTANCES,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should NOT assign fleet_type for RunInstances
        assert template.fleet_type is None

    def test_asg_no_fleet_type(self):
        """Test that ASG handler doesn't get fleet_type."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.ASG,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should NOT assign fleet_type for ASG
        assert template.fleet_type is None

    def test_explicit_fleet_type_preserved(self):
        """Test that explicitly provided fleet_type is preserved."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.EC2_FLEET,
            "fleet_type": AWSFleetType.MAINTAIN,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
        }

        template = AWSTemplate(**template_data)

        # Should preserve explicitly provided fleet_type
        assert template.fleet_type == AWSFleetType.MAINTAIN

    def test_template_accepts_snake_case_abis_requirements(self):
        """Ensure snake_case configs hydrate ABIS instance requirements."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.EC2_FLEET,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
            "abis_instance_requirements": {
                "vcpu_count": {"min": 2, "max": 4},
                "memory_mib": {"min": 4096, "max": 8192},
                "cpu_manufacturers": ["intel"],
                "local_storage": "required",
            },
        }

        template = AWSTemplate(**template_data)

        assert template.abis_instance_requirements is not None
        assert template.abis_instance_requirements.vcpu_count.min == 2
        payload = template.get_instance_requirements_payload()
        assert payload == {
            "VCpuCount": {"Min": 2, "Max": 4},
            "MemoryMiB": {"Min": 4096, "Max": 8192},
            "CpuManufacturers": ["intel"],
            "LocalStorage": "required",
        }

    def test_template_accepts_camel_case_abis_requirements(self):
        """Ensure HostFactory camelCase configs hydrate ABIS requirements."""
        template_data = {
            "template_id": "test-template",
            "provider_api": ProviderApi.SPOT_FLEET,
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345"],
            "instance_type": "t2.micro",
            "abisInstanceRequirements": {
                "VCpuCount": {"Min": 1, "Max": 2},
                "MemoryMiB": {"Min": 2048, "Max": 4096},
                "LocalStorage": "required",
            },
        }

        template = AWSTemplate(**template_data)

        assert template.abis_instance_requirements is not None
        assert template.abis_instance_requirements.memory_mib.max == 4096
        assert template.abis_instance_requirements.local_storage == "required"


class TestAWSTemplateExtensionConfig:
    """Test AWS template extension configuration."""

    def test_fleet_type_field_exists(self):
        """Test that fleet_type field exists in extension config."""
        from orb.providers.aws.configuration.template_extension import (
            AWSTemplateExtensionConfig,
        )

        config = AWSTemplateExtensionConfig()  # type: ignore[call-arg]

        # Should have fleet_type field
        assert hasattr(config, "fleet_type")
        assert config.fleet_type is None  # Default should be None

    def test_fleet_type_in_template_defaults(self):
        """Test that fleet_type is included in template defaults."""
        from orb.providers.aws.configuration.template_extension import (
            AWSTemplateExtensionConfig,
        )

        config = AWSTemplateExtensionConfig(fleet_type="request")  # type: ignore[call-arg]
        defaults = config.to_template_defaults()

        # Should include fleet_type in defaults
        assert "fleet_type" in defaults
        assert defaults["fleet_type"] == "request"

    def test_fleet_type_none_not_in_defaults(self):
        """Test that None fleet_type is not included in defaults."""
        from orb.providers.aws.configuration.template_extension import (
            AWSTemplateExtensionConfig,
        )

        config = AWSTemplateExtensionConfig(fleet_type=None)  # type: ignore[call-arg]
        defaults = config.to_template_defaults()

        # Should NOT include None values in defaults
        assert "fleet_type" not in defaults


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


ABIS_EXAMPLE_IDS = {
    "EC2Fleet-Request-ABIS": "ec2_fleet",
    "SpotFleet-Request-ABIS": "spot_fleet",
    "ASG-ABIS": "asg",
}
_IGNORED_FIELDS = {"created_at", "updated_at", "description", "metadata"}
_BUNDLED_TEMPLATES = Path(__file__).resolve().parents[3] / "config" / "aws_templates.json"


def _generated_example_templates() -> dict[str, AWSTemplate]:
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

    examples = [
        *EC2FleetHandler.get_example_templates(),
        *SpotFleetHandler.get_example_templates(),
        *ASGHandler.get_example_templates(),
    ]
    return {t.template_id: t for t in examples if t.template_id in ABIS_EXAMPLE_IDS}  # type: ignore[misc]


def _bundled_templates() -> dict[str, AWSTemplate]:
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    loaded = HostFactorySchedulerStrategy().load_templates_from_path(str(_BUNDLED_TEMPLATES))
    return {t["template_id"]: AWSTemplate(**t) for t in loaded}


def _comparable(template: AWSTemplate) -> dict:
    dumped = template.model_dump(mode="json", exclude_none=True)
    dumped["abis_payload"] = template.get_instance_requirements_payload()
    dumped.pop("abis_instance_requirements", None)
    return {k: v for k, v in dumped.items() if k not in _IGNORED_FIELDS}


class TestBundledAbisExamples:
    """The bundled AWS templates ship attribute-based instance selection examples."""

    def test_bundled_file_has_every_abis_example(self):
        assert set(ABIS_EXAMPLE_IDS) <= set(_bundled_templates())

    @pytest.mark.parametrize("template_id", sorted(ABIS_EXAMPLE_IDS))
    def test_bundled_entry_matches_generated_example(self, template_id):
        bundled = _bundled_templates()[template_id]
        generated = _generated_example_templates()[template_id]

        assert _comparable(bundled) == _comparable(generated)
        assert bundled.get_instance_requirements_payload() is not None
        assert not bundled.machine_types

    @pytest.mark.parametrize("template_id", sorted(ABIS_EXAMPLE_IDS))
    def test_example_renders_instance_requirements_without_instance_type(self, template_id):
        template = _bundled_templates()[template_id]
        payload = template.get_instance_requirements_payload()
        request = MagicMock()
        request.request_id = "req-1"
        request.requested_count = 2
        config_port = MagicMock()
        config_port.get_resource_prefix.return_value = "orb-"
        config_port.get_package_info.return_value = {"name": "orb"}
        logger = MagicMock()

        if template.provider_api == "ASG":
            from orb.providers.aws.infrastructure.handlers.asg.config_builder import (
                ASGConfigBuilder,
            )

            rendered = ASGConfigBuilder(None, config_port, logger)._build_legacy(
                "orb-asg", template, request, "lt-1", "1"
            )
            overrides = rendered["MixedInstancesPolicy"]["LaunchTemplate"]["Overrides"]
        elif template.provider_api == "SpotFleet":
            from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
                SpotFleetConfigBuilder,
            )

            rendered = SpotFleetConfigBuilder(None, config_port, logger)._build_legacy(
                template, request, "lt-1", "1"
            )
            overrides = rendered["LaunchTemplateConfigs"][0]["Overrides"]
        else:
            from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import (
                EC2FleetConfigBuilder,
            )

            rendered = EC2FleetConfigBuilder(None, config_port, logger)._build_legacy(
                template, request, "lt-1", "1"
            )
            overrides = rendered["LaunchTemplateConfigs"][0]["Overrides"]

        assert overrides == [{"InstanceRequirements": payload}]

    @pytest.mark.parametrize("template_id", sorted(ABIS_EXAMPLE_IDS))
    def test_live_scenarios_tolerate_examples_without_vm_types(self, template_id):
        from tests.providers.aws.live.scenarios import _load_template_vm_types

        assert _load_template_vm_types(template_id) == {}
