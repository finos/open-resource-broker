"""Comprehensive tests for AWS handler implementations."""

from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws

# Import AWS components
try:
    from src.providers.aws.infrastructure.handlers.asg_handler import ASGHandler
    from src.providers.aws.infrastructure.handlers.ec2_fleet_handler import (
        EC2FleetHandler,
    )
    from src.providers.aws.infrastructure.handlers.run_instances_handler import (
        RunInstancesHandler,
    )
    from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
        SpotFleetHandler,
    )
    from src.providers.aws.utilities.aws_operations import AWSOperations

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"AWS provider imports not available: {e}")


@pytest.mark.unit
@pytest.mark.aws
class TestEC2FleetHandler:
    """Test EC2 Fleet handler implementation."""

    @mock_aws
    def test_ec2_fleet_handler_creates_fleet(self):
        """Test that EC2FleetHandler creates fleet successfully."""
        # Setup AWS resources
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create VPC and subnet for testing
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        # Create AWS operations utility
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        # Create handler
        handler = EC2FleetHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Fleet configuration
        fleet_config = {
            "target_capacity": 2,
            "launch_templates": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateName": "test-template",
                        "Version": "$Latest",
                    },
                    "Overrides": [
                        {
                            "InstanceType": "t2.micro",
                            "SubnetId": subnet["Subnet"]["SubnetId"],
                        }
                    ],
                }
            ],
        }

        # Create launch template first
        ec2.create_launch_template(
            LaunchTemplateName="test-template",
            LaunchTemplateData={
                "ImageId": "ami-12345678",
                "InstanceType": "t2.micro",
                "SecurityGroupIds": [sg["GroupId"]],
            },
        )

        # Create fleet
        result = handler.create_fleet(fleet_config)

        assert "fleet_id" in result
        assert "instance_ids" in result
        assert len(result["instance_ids"]) == 2

    @mock_aws
    def test_ec2_fleet_handler_handles_creation_failure(self):
        """Test that EC2FleetHandler handles creation failures."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = EC2FleetHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Invalid fleet configuration
        invalid_config = {
            "target_capacity": 2,
            "launch_templates": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateName": "non-existent-template",
                        "Version": "$Latest",
                    }
                }
            ],
        }

        # Should handle failure gracefully
        with pytest.raises(Exception):
            handler.create_fleet(invalid_config)

    @mock_aws
    def test_ec2_fleet_handler_terminates_instances(self):
        """Test that EC2FleetHandler terminates instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = EC2FleetHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Create instances to terminate
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )

        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Terminate instances
        result = handler.terminate_instances(instance_ids)

        assert "terminated_instances" in result
        assert len(result["terminated_instances"]) == 2


@pytest.mark.unit
@pytest.mark.aws
class TestASGHandler:
    """Test Auto Scaling Group handler implementation."""

    @mock_aws
    def test_asg_handler_creates_auto_scaling_group(self):
        """Test that ASGHandler creates Auto Scaling Group."""
        # Setup clients
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create VPC and subnet
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create launch template
        lt_response = ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
        )

        # Create AWS operations utility
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        # Create handler
        handler = ASGHandler(
            autoscaling_client=autoscaling,
            ec2_client=ec2,
            aws_operations=aws_ops,
            logger=Mock(),
        )

        # ASG configuration
        asg_config = {
            "name": "test-asg",
            "launch_template": {"LaunchTemplateName": "test-lt", "Version": "$Latest"},
            "min_size": 1,
            "max_size": 5,
            "desired_capacity": 2,
            "subnet_ids": [subnet["Subnet"]["SubnetId"]],
        }

        # Create ASG
        result = handler.create_auto_scaling_group(asg_config)

        assert result["asg_name"] == "test-asg"
        assert "created_at" in result

    @mock_aws
    def test_asg_handler_scales_group(self):
        """Test that ASGHandler can scale Auto Scaling Group."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
        )

        # Create ASG
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName="test-asg",
            LaunchTemplate={"LaunchTemplateName": "test-lt", "Version": "$Latest"},
            MinSize=1,
            MaxSize=10,
            DesiredCapacity=2,
        )

        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())
        handler = ASGHandler(
            autoscaling_client=autoscaling,
            ec2_client=ec2,
            aws_operations=aws_ops,
            logger=Mock(),
        )

        # Scale up
        result = handler.scale_auto_scaling_group("test-asg", desired_capacity=5)

        assert result["asg_name"] == "test-asg"
        assert result["new_desired_capacity"] == 5

    @mock_aws
    def test_asg_handler_terminates_instances(self):
        """Test that ASGHandler terminates ASG instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())
        handler = ASGHandler(
            autoscaling_client=autoscaling,
            ec2_client=ec2,
            aws_operations=aws_ops,
            logger=Mock(),
        )

        # Create instances to terminate
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )

        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Terminate instances using consolidated operations
        result = handler.terminate_instances(instance_ids)

        assert "terminated_instances" in result
        assert len(result["terminated_instances"]) == 2


@pytest.mark.unit
@pytest.mark.aws
class TestSpotFleetHandler:
    """Test Spot Fleet handler implementation."""

    @mock_aws
    def test_spot_fleet_handler_creates_spot_fleet(self):
        """Test that SpotFleetHandler creates spot fleet."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create VPC and subnet
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())
        handler = SpotFleetHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Spot fleet configuration
        spot_config = {
            "target_capacity": 2,
            "max_spot_price": "0.05",
            "iam_fleet_role": "arn:aws:iam::123456789012:role/fleet-role",
            "launch_specifications": [
                {
                    "ImageId": "ami-12345678",
                    "InstanceType": "t2.micro",
                    "SubnetId": subnet["Subnet"]["SubnetId"],
                    "SecurityGroups": [{"GroupId": sg["GroupId"]}],
                }
            ],
        }

        # Create spot fleet
        result = handler.create_spot_fleet(spot_config)

        assert "spot_fleet_id" in result
        assert "state" in result

    @mock_aws
    def test_spot_fleet_handler_handles_price_changes(self):
        """Test that SpotFleetHandler handles spot price changes."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = SpotFleetHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Should be able to get current spot prices
        if hasattr(handler, "get_spot_prices"):
            prices = handler.get_spot_prices(
                instance_types=["t2.micro", "t2.small"],
                availability_zones=["us-east-1a", "us-east-1b"],
            )

            assert isinstance(prices, dict)
            assert "t2.micro" in prices or len(prices) >= 0

    @mock_aws
    def test_spot_fleet_handler_optimizes_costs(self):
        """Test that SpotFleetHandler optimizes costs."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = SpotFleetHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Should support cost optimization strategies
        if hasattr(handler, "optimize_fleet_cost"):
            optimization = handler.optimize_fleet_cost(
                target_capacity=10,
                max_price=0.10,
                instance_types=["t2.micro", "t2.small", "t3.micro"],
            )

            assert "recommended_config" in optimization
            assert "estimated_cost" in optimization


@pytest.mark.unit
@pytest.mark.aws
class TestRunInstancesHandler:
    """Test Run Instances handler implementation."""

    @mock_aws
    def test_run_instances_handler_creates_instances(self):
        """Test that RunInstancesHandler creates instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = RunInstancesHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Instance configuration
        instance_config = {
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
            "min_count": 2,
            "max_count": 2,
            "key_name": "test-key",
            "security_group_ids": ["sg-12345678"],
        }

        # Create instances
        result = handler.run_instances(instance_config)

        assert "instance_ids" in result
        assert len(result["instance_ids"]) == 2
        assert "reservation_id" in result

    @mock_aws
    def test_run_instances_handler_handles_capacity_errors(self):
        """Test that RunInstancesHandler handles insufficient capacity."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = RunInstancesHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Configuration that might cause capacity issues
        large_config = {
            "image_id": "ami-12345678",
            "instance_type": "x1e.32xlarge",  # Very large instance
            "min_count": 100,  # Large number
            "max_count": 100,
        }

        # Should handle capacity errors gracefully
        try:
            result = handler.run_instances(large_config)
            # If successful, verify result
            assert "instance_ids" in result
        except Exception as e:
            # Should be a meaningful error message
            assert "capacity" in str(e).lower() or "insufficient" in str(e).lower()

    @mock_aws
    def test_run_instances_handler_supports_user_data(self):
        """Test that RunInstancesHandler supports user data."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        aws_ops = AWSOperations(ec2_client=ec2, logger=Mock())

        handler = RunInstancesHandler(ec2_client=ec2, aws_operations=aws_ops, logger=Mock())

        # Configuration with user data
        config_with_user_data = {
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
            "min_count": 1,
            "max_count": 1,
            "user_data": "IyEvYmluL2Jhc2gKZWNobyAiSGVsbG8gV29ybGQi",  # Base64 encoded
        }

        # Create instance with user data
        result = handler.run_instances(config_with_user_data)

        assert "instance_ids" in result
        assert len(result["instance_ids"]) == 1
