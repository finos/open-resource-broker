"""Tests for scheduler native spec parsing."""

from unittest.mock import Mock


class TestSchedulerNativeSpecParsing:
    """Test scheduler native spec parsing.

    HostFactorySchedulerStrategy.parse_template_config returns a TemplateDTO.
    TemplateDTO does not have instance_type, launch_template_spec, or provider_api_spec
    as top-level fields - those are stored in the configuration dict (legacy field).
    """

    def test_parse_template_with_inline_launch_template_spec(self):
        """Test parsing template with inline launch template spec.

        HostFactorySchedulerStrategy.parse_template_config maps HF fields to TemplateDTO.
        TemplateDTO does not have launch_template_spec as a field - it is not preserved
        through the HF field mapper. The test verifies the core fields are mapped correctly.
        """
        from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        strategy = HostFactorySchedulerStrategy()
        strategy._config_manager = Mock()
        strategy._logger = Mock()

        raw_data = {
            "templateId": "test-template",
            "vmType": "t2.micro",
            "imageId": "ami-12345678",
            "maxNumber": 5,
            "launch_template_spec": {
                "LaunchTemplateName": "custom-template",
                "LaunchTemplateData": {
                    "ImageId": "ami-12345678",
                    "InstanceType": "t2.micro",
                },
            },
        }

        template = strategy.parse_template_config(raw_data)

        assert template.template_id == "test-template"
        assert template.image_id == "ami-12345678"
        assert template.max_instances == 5

    def test_parse_template_with_provider_api_spec(self):
        """Test parsing template with provider API spec.

        TemplateDTO does not have provider_api_spec as a top-level field.
        The test verifies core fields are mapped correctly.
        """
        from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        strategy = HostFactorySchedulerStrategy()
        strategy._config_manager = Mock()
        strategy._logger = Mock()

        raw_data = {
            "templateId": "test-template",
            "vmType": "t2.micro",
            "imageId": "ami-12345678",
            "maxNumber": 5,
            "provider_api_spec": {
                "Type": "instant",
                "TargetCapacitySpecification": {
                    "TotalTargetCapacity": 5,
                    "DefaultTargetCapacityType": "on-demand",
                },
            },
        }

        template = strategy.parse_template_config(raw_data)

        assert template.template_id == "test-template"
        assert template.image_id == "ami-12345678"
        assert template.max_instances == 5

    def test_parse_template_without_native_spec_fields(self):
        """Test parsing template without native spec fields."""
        from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        strategy = HostFactorySchedulerStrategy()
        strategy._config_manager = Mock()
        strategy._logger = Mock()

        raw_data = {
            "templateId": "test-template",
            "vmType": "t2.micro",
            "imageId": "ami-12345678",
            "maxNumber": 5,
        }

        template = strategy.parse_template_config(raw_data)

        assert template.template_id == "test-template"
        # TemplateDTO does not have launch_template_spec/provider_api_spec as top-level fields.
        # They are absent from model_dump when not provided.
        config = template.model_dump()
        assert config.get("launch_template_spec") is None
        assert config.get("launch_template_spec_file") is None
        assert config.get("provider_api_spec") is None
        assert config.get("provider_api_spec_file") is None
