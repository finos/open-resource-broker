"""Integration tests for native spec support across all AWS providers."""

from unittest.mock import patch

import pytest

from domain.request.request import Request
from domain.request.value_objects import RequestId
from infrastructure.di.container import DIContainer
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.infrastructure.services.aws_native_spec_service import (
    AWSNativeSpecService,
)


class TestNativeSpecAllProviders:
    """Integration tests for native spec across all AWS provider APIs."""

    def setup_method(self):
        """Set up test fixtures."""
        self.container = DIContainer()
        self.aws_native_spec_service = self.container.get(AWSNativeSpecService)

    def test_ec2fleet_native_spec_integration(self):
        """Test EC2Fleet with native spec integration."""
        # Create template with provider API spec
        template = AWSTemplate(
            template_id="ec2fleet-test",
            image_id="ami-12345",
            instance_type="t3.micro",
            provider_api_spec={
                "Type": "instant",
                "TargetCapacitySpecification": {
                    "TotalTargetCapacity": "{{ requested_count }}",
                    "DefaultTargetCapacityType": "on-demand",
                },
                "TagSpecifications": [
                    {
                        "ResourceType": "fleet",
                        "Tags": [
                            {"Key": "Name", "Value": "test-fleet-{{ request_id }}"},
                            {"Key": "CreatedBy", "Value": "{{ package_name }}"},
                        ],
                    }
                ],
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=3, template_id="ec2fleet-test"
        )

        # Process provider API spec
        result = self.aws_native_spec_service.process_provider_api_spec(template, request)

        assert result is not None
        assert result["Type"] == "instant"
        assert result["TargetCapacitySpecification"]["TotalTargetCapacity"] == "3"
        assert result["TargetCapacitySpecification"]["DefaultTargetCapacityType"] == "on-demand"

        # Check that template variables were rendered
        fleet_tags = result["TagSpecifications"][0]["Tags"]
        name_tag = next(tag for tag in fleet_tags if tag["Key"] == "Name")
        created_by_tag = next(tag for tag in fleet_tags if tag["Key"] == "CreatedBy")

        assert str(request.request_id) in name_tag["Value"]
        assert created_by_tag["Value"] == "open-hostfactory-plugin"

    def test_spotfleet_native_spec_integration(self):
        """Test SpotFleet with native spec integration."""
        template = AWSTemplate(
            template_id="spotfleet-test",
            image_id="ami-67890",
            instance_type="t3.small",
            provider_api_spec={
                "IamFleetRole": "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role",
                "AllocationStrategy": "lowestPrice",
                "TargetCapacity": "{{ requested_count }}",
                "SpotPrice": "0.05",
                "LaunchSpecifications": [
                    {
                        "ImageId": "{{ image_id }}",
                        "InstanceType": "{{ instance_type }}",
                        "KeyName": "test-key",
                        "SecurityGroups": [{"GroupId": "sg-12345"}],
                    }
                ],
                "TagSpecifications": [
                    {
                        "ResourceType": "spot-fleet-request",
                        "Tags": [
                            {"Key": "RequestId", "Value": "{{ request_id }}"},
                            {"Key": "TemplateId", "Value": "{{ template_id }}"},
                        ],
                    }
                ],
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=5, template_id="spotfleet-test"
        )

        result = self.aws_native_spec_service.process_provider_api_spec(template, request)

        assert result is not None
        assert result["AllocationStrategy"] == "lowestPrice"
        assert result["TargetCapacity"] == "5"
        assert result["SpotPrice"] == "0.05"

        # Check launch specification rendering
        launch_spec = result["LaunchSpecifications"][0]
        assert launch_spec["ImageId"] == "ami-67890"
        assert launch_spec["InstanceType"] == "t3.small"

        # Check tag rendering
        tags = result["TagSpecifications"][0]["Tags"]
        request_id_tag = next(tag for tag in tags if tag["Key"] == "RequestId")
        template_id_tag = next(tag for tag in tags if tag["Key"] == "TemplateId")

        assert request_id_tag["Value"] == str(request.request_id)
        assert template_id_tag["Value"] == "spotfleet-test"

    def test_autoscaling_native_spec_integration(self):
        """Test Auto Scaling Group with native spec integration."""
        template = AWSTemplate(
            template_id="asg-test",
            image_id="ami-asg123",
            instance_type="m5.large",
            provider_api_spec={
                "AutoScalingGroupName": "asg-{{ request_id }}",
                "MinSize": 1,
                "MaxSize": "{{ requested_count * 2 }}",
                "DesiredCapacity": "{{ requested_count }}",
                "HealthCheckType": "ELB",
                "HealthCheckGracePeriod": 300,
                "VPCZoneIdentifier": ["subnet-1", "subnet-2"],
                "Tags": [
                    {
                        "Key": "Name",
                        "Value": "asg-instance-{{ request_id }}",
                        "PropagateAtLaunch": True,
                        "ResourceId": "asg-{{ request_id }}",
                        "ResourceType": "auto-scaling-group",
                    }
                ],
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=4, template_id="asg-test"
        )

        result = self.aws_native_spec_service.process_provider_api_spec(template, request)

        assert result is not None
        assert str(request.request_id) in result["AutoScalingGroupName"]
        assert result["MinSize"] == 1
        assert result["MaxSize"] == "8"  # requested_count * 2
        assert result["DesiredCapacity"] == "4"
        assert result["HealthCheckType"] == "ELB"

        # Check tag rendering
        tag = result["Tags"][0]
        assert str(request.request_id) in tag["Value"]
        assert str(request.request_id) in tag["ResourceId"]

    def test_launch_template_native_spec_integration(self):
        """Test Launch Template with native spec integration."""
        template = AWSTemplate(
            template_id="lt-test",
            image_id="ami-lt456",
            instance_type="t3.medium",
            launch_template_spec={
                "LaunchTemplateName": "lt-{{ request_id }}",
                "LaunchTemplateData": {
                    "ImageId": "{{ image_id }}",
                    "InstanceType": "{{ instance_type }}",
                    "KeyName": "test-keypair",
                    "SecurityGroupIds": ["sg-web", "sg-common"],
                    "IamInstanceProfile": {"Name": "test-instance-profile"},
                    "TagSpecifications": [
                        {
                            "ResourceType": "instance",
                            "Tags": [
                                {"Key": "Name", "Value": "instance-{{ request_id }}"},
                                {"Key": "TemplateId", "Value": "{{ template_id }}"},
                                {"Key": "CreatedBy", "Value": "{{ package_name }}"},
                            ],
                        }
                    ],
                },
            },
        )

        request = Request(request_id=RequestId.generate(), requested_count=2, template_id="lt-test")

        result = self.aws_native_spec_service.process_launch_template_spec(template, request)

        assert result is not None
        assert str(request.request_id) in result["LaunchTemplateName"]

        lt_data = result["LaunchTemplateData"]
        assert lt_data["ImageId"] == "ami-lt456"
        assert lt_data["InstanceType"] == "t3.medium"
        assert lt_data["KeyName"] == "test-keypair"
        assert lt_data["SecurityGroupIds"] == ["sg-web", "sg-common"]

        # Check tag rendering
        tags = lt_data["TagSpecifications"][0]["Tags"]
        name_tag = next(tag for tag in tags if tag["Key"] == "Name")
        template_tag = next(tag for tag in tags if tag["Key"] == "TemplateId")
        created_by_tag = next(tag for tag in tags if tag["Key"] == "CreatedBy")

        assert str(request.request_id) in name_tag["Value"]
        assert template_tag["Value"] == "lt-test"
        assert created_by_tag["Value"] == "open-hostfactory-plugin"

    def test_mixed_native_specs_integration(self):
        """Test template with both launch template and provider API specs."""
        template = AWSTemplate(
            template_id="mixed-test",
            image_id="ami-mixed789",
            instance_type="c5.xlarge",
            launch_template_spec={
                "LaunchTemplateName": "mixed-lt-{{ request_id }}",
                "LaunchTemplateData": {
                    "ImageId": "{{ image_id }}",
                    "InstanceType": "{{ instance_type }}",
                    "UserData": "{{ user_data | default('') | b64encode }}",
                },
            },
            provider_api_spec={
                "Type": "maintain",
                "TargetCapacitySpecification": {
                    "TotalTargetCapacity": "{{ requested_count }}",
                    "OnDemandTargetCapacity": "{{ (requested_count * 0.7) | round | int }}",
                    "SpotTargetCapacity": "{{ (requested_count * 0.3) | round | int }}",
                },
                "ReplaceUnhealthyInstances": True,
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=10, template_id="mixed-test"
        )

        # Test launch template spec processing
        lt_result = self.aws_native_spec_service.process_launch_template_spec(template, request)
        assert lt_result is not None
        assert str(request.request_id) in lt_result["LaunchTemplateName"]
        assert lt_result["LaunchTemplateData"]["ImageId"] == "ami-mixed789"
        assert lt_result["LaunchTemplateData"]["InstanceType"] == "c5.xlarge"

        # Test provider API spec processing
        api_result = self.aws_native_spec_service.process_provider_api_spec(template, request)
        assert api_result is not None
        assert api_result["Type"] == "maintain"
        assert api_result["TargetCapacitySpecification"]["TotalTargetCapacity"] == "10"
        # 10 * 0.7
        assert api_result["TargetCapacitySpecification"]["OnDemandTargetCapacity"] == "7"
        # 10 * 0.3
        assert api_result["TargetCapacitySpecification"]["SpotTargetCapacity"] == "3"
        assert api_result["ReplaceUnhealthyInstances"] is True

    @patch("providers.aws.infrastructure.services.aws_native_spec_service.read_json_file")
    def test_file_based_native_specs_integration(self, mock_read_file):
        """Test template with file-based native specs."""
        # Mock file contents
        lt_spec_content = {
            "LaunchTemplateName": "file-lt-{{ request_id }}",
            "LaunchTemplateData": {
                "ImageId": "{{ image_id }}",
                "InstanceType": "{{ instance_type }}",
            },
        }

        api_spec_content = {
            "Type": "instant",
            "TargetCapacitySpecification": {"TotalTargetCapacity": "{{ requested_count }}"},
        }

        def mock_read_side_effect(file_path):
            if "lt-spec.json" in file_path:
                return lt_spec_content
            elif "api-spec.json" in file_path:
                return api_spec_content
            else:
                raise FileNotFoundError(f"File not found: {file_path}")

        mock_read_file.side_effect = mock_read_side_effect

        template = AWSTemplate(
            template_id="file-test",
            image_id="ami-file123",
            instance_type="t3.large",
            launch_template_spec_file="lt-spec.json",
            provider_api_spec_file="api-spec.json",
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=6, template_id="file-test"
        )

        # Test launch template spec from file
        lt_result = self.aws_native_spec_service.process_launch_template_spec(template, request)
        assert lt_result is not None
        assert str(request.request_id) in lt_result["LaunchTemplateName"]
        assert lt_result["LaunchTemplateData"]["ImageId"] == "ami-file123"

        # Test provider API spec from file
        api_result = self.aws_native_spec_service.process_provider_api_spec(template, request)
        assert api_result is not None
        assert api_result["Type"] == "instant"
        assert api_result["TargetCapacitySpecification"]["TotalTargetCapacity"] == "6"

    def test_native_spec_disabled_integration(self):
        """Test behavior when native specs are disabled."""
        template = AWSTemplate(
            template_id="disabled-test",
            image_id="ami-disabled",
            instance_type="t3.micro",
            provider_api_spec={
                "Type": "instant",
                "TargetCapacitySpecification": {"TotalTargetCapacity": "{{ requested_count }}"},
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=1, template_id="disabled-test"
        )

        # Mock native spec service to return disabled
        with patch.object(
            self.aws_native_spec_service.native_spec_service,
            "is_native_spec_enabled",
            return_value=False,
        ):
            lt_result = self.aws_native_spec_service.process_launch_template_spec(template, request)
            api_result = self.aws_native_spec_service.process_provider_api_spec(template, request)

            assert lt_result is None
            assert api_result is None

    def test_complex_jinja_expressions_integration(self):
        """Test complex Jinja2 expressions in native specs."""
        template = AWSTemplate(
            template_id="complex-test",
            image_id="ami-complex",
            instance_type="t3.micro",
            provider_api_spec={
                "Type": "maintain",
                "TargetCapacitySpecification": {
                    "TotalTargetCapacity": "{{ requested_count }}",
                    "OnDemandTargetCapacity": "{{ [1, (requested_count * 0.5) | round | int] | max }}",
                    "SpotTargetCapacity": "{{ requested_count - ([1, (requested_count * 0.5) | round | int] | max) }}",
                },
                "SpotOptions": {
                    "AllocationStrategy": "{% if requested_count > 10 %}diversified{% else %}lowestPrice{% endif %}",
                    "InstanceInterruptionBehavior": "terminate",
                },
            },
        )

        # Test with small request count
        request_small = Request(
            request_id=RequestId.generate(), requested_count=4, template_id="complex-test"
        )

        result_small = self.aws_native_spec_service.process_provider_api_spec(
            template, request_small
        )
        # max(1, 4*0.5)
        assert result_small["TargetCapacitySpecification"]["OnDemandTargetCapacity"] == "2"
        # 4 - 2
        assert result_small["TargetCapacitySpecification"]["SpotTargetCapacity"] == "2"
        # 4 <= 10
        assert result_small["SpotOptions"]["AllocationStrategy"] == "lowestPrice"

        # Test with large request count
        request_large = Request(
            request_id=RequestId.generate(), requested_count=20, template_id="complex-test"
        )

        result_large = self.aws_native_spec_service.process_provider_api_spec(
            template, request_large
        )
        # max(1, 20*0.5)
        assert result_large["TargetCapacitySpecification"]["OnDemandTargetCapacity"] == "10"
        # 20 - 10
        assert result_large["TargetCapacitySpecification"]["SpotTargetCapacity"] == "10"
        # 20 > 10
        assert result_large["SpotOptions"]["AllocationStrategy"] == "diversified"

    def test_error_handling_integration(self):
        """Test error handling in native spec processing."""
        # Template with invalid Jinja2 syntax
        template_invalid = AWSTemplate(
            template_id="invalid-test",
            image_id="ami-invalid",
            instance_type="t3.micro",
            provider_api_spec={
                "Type": "instant",
                "InvalidField": "{{ unclosed_variable",  # Invalid syntax
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=1, template_id="invalid-test"
        )

        # Should handle template syntax errors gracefully
        with pytest.raises(Exception):  # Specific exception type depends on implementation
            self.aws_native_spec_service.process_provider_api_spec(template_invalid, request)

    def test_context_variable_availability(self):
        """Test that all expected context variables are available."""
        template = AWSTemplate(
            template_id="context-test",
            image_id="ami-context",
            instance_type="t3.micro",
            provider_api_spec={
                "Type": "instant",
                "ContextTest": {
                    "RequestId": "{{ request_id }}",
                    "RequestedCount": "{{ requested_count }}",
                    "TemplateId": "{{ template_id }}",
                    "ImageId": "{{ image_id }}",
                    "InstanceType": "{{ instance_type }}",
                    "PackageName": "{{ package_name }}",
                    "PackageVersion": "{{ package_version }}",
                },
            },
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=3, template_id="context-test"
        )

        result = self.aws_native_spec_service.process_provider_api_spec(template, request)

        context_test = result["ContextTest"]
        assert context_test["RequestId"] == str(request.request_id)
        assert context_test["RequestedCount"] == "3"
        assert context_test["TemplateId"] == "context-test"
        assert context_test["ImageId"] == "ami-context"
        assert context_test["InstanceType"] == "t3.micro"
        assert context_test["PackageName"] == "open-hostfactory-plugin"
        assert "PackageVersion" in context_test  # Version may vary
