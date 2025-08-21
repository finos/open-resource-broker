"""Tests for scheduler native spec parsing."""

from unittest.mock import Mock, patch


class TestSchedulerNativeSpecParsing:
    """Test scheduler native spec parsing."""

    def test_parse_template_with_inline_launch_template_spec(self):
        """Test parsing template with inline launch template spec."""
        # Import here to avoid DI issues
        from infrastructure.scheduler.hostfactory.strategy import (
            HostFactorySchedulerStrategy,
        )

        # Mock the dependencies to avoid DI container issues
        with patch("infrastructure.scheduler.hostfactory.strategy.get_container") as mock_container:
            mock_container.return_value.get.return_value = Mock()

            config_manager = Mock()
            logger = Mock()
            strategy = HostFactorySchedulerStrategy(config_manager, logger)

            raw_data = {
                "templateId": "test-template",
                "vmType": "t2.micro",
                "imageId": "ami-12345678",
                "maxNumber": 5,
                "launch_template_spec": {
                    "LaunchTemplateName": "custom-template",
                    "LaunchTemplateData": {"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
                },
            }

            template = strategy.parse_template_config(raw_data)

            assert template.template_id == "test-template"
            assert template.instance_type == "t2.micro"
            assert template.launch_template_spec == {
                "LaunchTemplateName": "custom-template",
                "LaunchTemplateData": {"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
            }

    def test_parse_template_with_provider_api_spec(self):
        """Test parsing template with provider API spec."""
        from infrastructure.scheduler.hostfactory.strategy import (
            HostFactorySchedulerStrategy,
        )

        with patch("infrastructure.scheduler.hostfactory.strategy.get_container") as mock_container:
            mock_container.return_value.get.return_value = Mock()

            config_manager = Mock()
            logger = Mock()
            strategy = HostFactorySchedulerStrategy(config_manager, logger)

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
            assert template.provider_api_spec == {
                "Type": "instant",
                "TargetCapacitySpecification": {
                    "TotalTargetCapacity": 5,
                    "DefaultTargetCapacityType": "on-demand",
                },
            }

    def test_parse_template_without_native_spec_fields(self):
        """Test parsing template without native spec fields."""
        from infrastructure.scheduler.hostfactory.strategy import (
            HostFactorySchedulerStrategy,
        )

        with patch("infrastructure.scheduler.hostfactory.strategy.get_container") as mock_container:
            mock_container.return_value.get.return_value = Mock()

            config_manager = Mock()
            logger = Mock()
            strategy = HostFactorySchedulerStrategy(config_manager, logger)

            raw_data = {
                "templateId": "test-template",
                "vmType": "t2.micro",
                "imageId": "ami-12345678",
                "maxNumber": 5,
            }

            template = strategy.parse_template_config(raw_data)

            assert template.template_id == "test-template"
            assert template.launch_template_spec is None
            assert template.launch_template_spec_file is None
            assert template.provider_api_spec is None
            assert template.provider_api_spec_file is None
