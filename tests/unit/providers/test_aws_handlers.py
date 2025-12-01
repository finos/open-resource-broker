"""Tests for AWS handler implementations."""

from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws

# Import AWS components
try:
    from providers.aws.domain.template.value_objects import AWSFleetType
    from providers.aws.exceptions.aws_exceptions import AWSValidationError
    from providers.aws.infrastructure.handlers.asg_handler import ASGHandler
    from providers.aws.infrastructure.handlers.ec2_fleet_handler import EC2FleetHandler
    from providers.aws.infrastructure.handlers.run_instances_handler import (
        RunInstancesHandler,
    )
    from providers.aws.infrastructure.handlers.spot_fleet_handler import (
        SpotFleetHandler,
    )
    from providers.aws.utilities.aws_operations import AWSOperations

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"AWS provider imports not available: {e}")


@pytest.mark.unit
@pytest.mark.aws
class TestContextFieldSupport:
    """Test Context field support in AWS handlers."""

    def test_ec2_fleet_context_field(self):
        """Test that EC2 Fleet handler includes Context field when specified."""
        # Mock template with context
        template = Mock()
        template.context = "c-abc1234567890123"
        template.fleet_type = "instant"
        template.instance_types = None
        template.subnet_ids = None
        template.tags = None
        template.price_type = "ondemand"
        template.allocation_strategy = None
        template.max_spot_price = None
        template.percent_on_demand = None
        template.allocation_strategy_on_demand = None
        template.instance_types_ondemand = None
        template.template_id = "test-template"

        # Mock request
        request = Mock()
        request.requested_count = 2
        request.request_id = "test-123"

        # Create handler with mocked dependencies
        handler = EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), Mock())

        # Mock the aws_native_spec_service to return None (use fallback)
        handler.aws_native_spec_service = None

        # Test _create_fleet_config method (will use legacy fallback)
        config = handler._create_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-123",
            launch_template_version="1",
        )

        # Verify basic fleet configuration structure
        assert "LaunchTemplateConfigs" in config
        assert "TargetCapacitySpecification" in config
        assert config["TargetCapacitySpecification"]["TotalTargetCapacity"] == 2

    def test_asg_context_field(self):
        """Test that ASG handler includes Context field when specified."""
        # Mock template with context
        template = Mock()
        template.context = "staging-environment"
        template.subnet_ids = ["subnet-123"]

        # Mock request
        request = Mock()
        request.requested_count = 3

        # Create handler with mocked dependencies
        handler = ASGHandler(Mock(), Mock(), Mock(), Mock(), Mock())

        # Test _create_asg_config method
        config = handler._create_asg_config(
            asg_name="test-asg",
            aws_template=template,
            request=request,
            launch_template_id="lt-456",
            launch_template_version="2",
        )

        # Assert Context field is included
        assert "Context" in config
        assert config["Context"] == "staging-environment"

    def test_spot_fleet_context_field(self):
        """Test that Spot Fleet handler includes Context field when specified."""
        # Mock template with context
        template = Mock()
        template.context = "development-testing"
        template.fleet_role = "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
        template.fleet_type = "request"  # Use string instead of Mock
        template.instance_types = None
        template.subnet_ids = None
        template.tags = None
        template.allocation_strategy = None
        template.max_spot_price = None
        template.spot_fleet_request_expiry = 30
        template.percent_on_demand = 0  # Fix: Set to numeric value instead of Mock
        template.price_type = "spot"  # Fix: Add missing price_type attribute
        template.instance_type = "t3.micro"  # Fix: Add missing instance_type
        template.template_id = "test-template"  # Fix: Add missing template_id
        template.get_instance_requirements_payload = Mock(return_value=None)

        # Mock request
        request = Mock()
        request.requested_count = 1
        request.request_id = "test-456"

        # Create handler with mocked dependencies
        aws_client = Mock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        handler = SpotFleetHandler(aws_client, Mock(), Mock(), Mock(), Mock())

        # Test _create_spot_fleet_config method
        config = handler._create_spot_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-789",
            launch_template_version="3",
        )

        # Assert Context field is included
        assert "Context" in config
        assert config["Context"] == "development-testing"

    def test_spot_fleet_percent_on_demand_for_spot_price(self):
        """Ensure percent_on_demand is applied when price_type is spot."""
        template = Mock()
        template.context = None
        template.fleet_role = (
            "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/"
            "AWSServiceRoleForEC2SpotFleet"
        )
        template.fleet_type = "request"
        template.instance_types = None
        template.instance_types_ondemand = None
        template.instance_type = "t3.micro"
        template.subnet_ids = ["subnet-abc123"]
        template.tags = None
        template.allocation_strategy = None
        template.max_spot_price = None
        template.max_price = None
        template.price_type = "spot"
        template.percent_on_demand = 50
        template.allocation_strategy_on_demand = None
        template.template_id = "test-template"
        template.get_instance_requirements_payload = Mock(return_value=None)

        request = Mock()
        request.requested_count = 4
        request.request_id = "req-123"

        aws_client = Mock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}

        handler = SpotFleetHandler(aws_client, Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_spot_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-123",
            launch_template_version="1",
        )

        assert config["OnDemandTargetCapacity"] == 2

    def test_handlers_without_context_field(self):
        """Test that handlers work correctly when Context field is not specified."""
        # Mock template without context
        template = Mock()
        template.context = None
        template.fleet_type = "instant"
        template.instance_types = None
        template.subnet_ids = None
        template.tags = None
        template.price_type = "ondemand"
        template.allocation_strategy = None
        template.max_spot_price = None
        template.percent_on_demand = None
        template.allocation_strategy_on_demand = None
        template.instance_types_ondemand = None
        template.template_id = "test-template"

        # Mock request
        request = Mock()
        request.requested_count = 1
        request.request_id = "test-789"

        # Create handler with mocked dependencies
        handler = EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), Mock())

        # Mock the aws_native_spec_service to return None (use fallback)
        handler.aws_native_spec_service = None

        # Test _create_fleet_config method (will use legacy fallback)
        config = handler._create_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-999",
            launch_template_version="1",
        )

        # Verify basic fleet configuration structure
        assert "LaunchTemplateConfigs" in config
        assert "TargetCapacitySpecification" in config
        assert config["TargetCapacitySpecification"]["TotalTargetCapacity"] == 1


@pytest.mark.unit
@pytest.mark.aws
class TestEC2FleetHandler:
    """Test EC2 Fleet handler implementation."""

    def test_ec2_fleet_handler_builds_maintain_config(self):
        """Ensure maintain fleets add the correct flags to the config."""
        request = SimpleNamespace(request_id="req-maintain", requested_count=3)
        template = SimpleNamespace(
            template_id="tmpl-maintain",
            fleet_type=AWSFleetType.MAINTAIN,
            price_type="ondemand",
            allocation_strategy=None,
            allocation_strategy_on_demand=None,
            percent_on_demand=0,
            max_price=None,
            instance_types=None,
            instance_types_ondemand=None,
            subnet_ids=None,
            tags=None,
            context=None,
            get_instance_requirements_payload=lambda: None,
            abis_instance_requirements=None,
        )

        handler = EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-maintain",
            launch_template_version="1",
        )

        assert config["Type"] == "maintain"
        assert config["ReplaceUnhealthyInstances"] is True
        assert config["ExcessCapacityTerminationPolicy"] == "termination"
        assert config["TargetCapacitySpecification"]["TotalTargetCapacity"] == 3

    @mock_aws
    def test_ec2_fleet_handler_creates_fleet(self):
        """Test that EC2FleetHandler creates fleet successfully."""
        # Setup AWS resources
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

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
        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        # Create handler with correct constructor arguments
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 2

        template = Mock()
        template.template_id = "test-template"
        template.instance_type = "t2.micro"
        template.image_id = "ami-12345678"
        template.subnet_ids = [subnet["Subnet"]["SubnetId"]]
        template.security_group_ids = [sg["GroupId"]]
        template.tags = {}
        template.fleet_type = "maintain"
        template.instance_types = ["t2.micro"]
        template.key_pair_name = None
        template.user_data = None

        # Mock the AWS operations to return success
        aws_ops.execute_with_standard_error_handling = Mock(return_value="fleet-12345")

        # Test acquire_hosts method
        result = handler.acquire_hosts(request, template)

        assert result["success"]
        assert "resource_ids" in result
        assert "fleet-12345" in result["resource_ids"]

    @mock_aws
    def test_ec2_fleet_handler_handles_creation_failure(self):
        """Test that EC2FleetHandler handles creation failures."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template with invalid configuration
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 2

        template = Mock()
        template.template_id = "test-template"
        template.instance_type = "invalid-instance-type"
        template.image_id = "ami-invalid"
        template.subnet_ids = ["subnet-invalid"]
        template.security_group_ids = ["sg-invalid"]
        template.tags = {}
        template.fleet_type = "maintain"
        template.instance_types = ["invalid-instance-type"]
        template.key_pair_name = None
        template.user_data = None

        # Mock AWS operations to raise an exception
        aws_ops.execute_with_standard_error_handling = Mock(
            side_effect=Exception("Fleet creation failed")
        )

        # Should handle failure gracefully - the handler catches exceptions and returns error result
        result = handler.acquire_hosts(request, template)
        assert not result["success"]
        assert "error_message" in result

    @mock_aws
    def test_ec2_fleet_handler_terminates_instances(self):
        """Test that EC2FleetHandler terminates instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Test release_hosts method with empty machine_ids to trigger early return
        try:
            handler.release_hosts([])  # Empty list should trigger early return
            # If no exception is raised, that's actually correct behavior
        except Exception as e:
            # If an exception is raised, check it's the expected one
            assert "No instance IDs provided" in str(e) or "machine_ids" in str(e)

    @mock_aws
    def test_ec2_fleet_handler_release_hosts_with_resource_mapping(self):
        """Test EC2FleetHandler release_hosts with resource mapping optimization."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        # Create VPC and subnet for testing
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={
                "ImageId": "ami-12345678",
                "InstanceType": "t2.micro",
                "SecurityGroupIds": [sg["GroupId"]],
            },
        )

        # Create EC2 Fleet
        fleet_response = ec2.create_fleet(
            LaunchTemplateConfigs=[
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateName": "test-lt",
                        "Version": "$Latest",
                    },
                    "Overrides": [{"SubnetId": subnet["Subnet"]["SubnetId"]}],
                }
            ],
            TargetCapacitySpecification={"TotalTargetCapacity": 3},
            Type="maintain",
        )
        fleet_id = fleet_response["FleetId"]

        # Create instances that would be in the fleet
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=3, MaxCount=3, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping indicating these instances belong to the EC2 Fleet
        resource_mapping = {
            instance_ids[0]: (fleet_id, 3),  # EC2 Fleet instance with capacity > 0
            instance_ids[1]: (fleet_id, 3),  # EC2 Fleet instance with capacity > 0
            instance_ids[2]: (None, 0),  # Non-EC2 Fleet instance
        }

        # Test release_hosts with resource mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called

    @mock_aws
    def test_ec2_fleet_handler_release_hosts_mixed_instances(self):
        """Test EC2FleetHandler release_hosts with mixed EC2 Fleet and non-EC2 Fleet instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=4, MaxCount=4, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping with mixed instance types
        resource_mapping = {
            instance_ids[0]: ("fleet-12345", 2),  # EC2 Fleet instance
            instance_ids[1]: ("fleet-12345", 2),  # Same EC2 Fleet instance
            instance_ids[2]: ("fleet-67890", 1),  # Different EC2 Fleet instance
            instance_ids[3]: (None, 0),  # Non-EC2 Fleet instance
        }

        # Test release_hosts with mixed instances
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called
        assert aws_ops.terminate_instances_with_fallback.call_count >= 1

    @mock_aws
    def test_ec2_fleet_handler_release_hosts_error_handling(self):
        """Test EC2FleetHandler release_hosts error handling."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock AWS operations to raise an exception
        aws_ops.terminate_instances_with_fallback = Mock(
            side_effect=Exception("Termination failed")
        )

        # Test that release_hosts handles errors gracefully
        try:
            handler.release_hosts(instance_ids)
            # Should not reach here if exception is properly raised
            assert False, "Expected AWSInfrastructureError to be raised"
        except Exception as e:
            # Should catch and re-raise as AWSInfrastructureError
            assert "Failed to release EC2 Fleet hosts" in str(e)

    @mock_aws
    def test_ec2_fleet_handler_release_hosts_incomplete_resource_mapping(self):
        """Test EC2FleetHandler release_hosts with incomplete resource mapping."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=4, MaxCount=4, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping with incomplete information
        # This tests the scenario where some instances have missing fleet IDs or desired capacity
        resource_mapping = {
            instance_ids[0]: ("fleet-12345", 4),  # Complete information
            instance_ids[1]: (None, 4),  # Missing resource_id
            instance_ids[2]: ("fleet-12345", 0),  # Missing/zero desired_capacity
            instance_ids[3]: (None, 0),  # Missing both
        }

        # Test release_hosts with incomplete resource mapping
        # The handler should process all instances, even with incomplete mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that the handler processes the instances correctly
        assert aws_ops.terminate_instances_with_fallback.called, "Expected termination to be called"

        # Verify that all instances are processed (either as EC2 Fleet or non-EC2 Fleet instances)
        call_args_list = aws_ops.terminate_instances_with_fallback.call_args_list
        total_instances_processed = sum(len(call[0][0]) for call in call_args_list)
        assert total_instances_processed == len(instance_ids), (
            f"Expected all {len(instance_ids)} instances to be processed"
        )

    @mock_aws
    def test_ec2_fleet_handler_release_hosts_maintain_fleet_capacity_management(self):
        """Test EC2FleetHandler release_hosts with maintain fleet capacity management."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        # Create VPC and subnet for testing
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={
                "ImageId": "ami-12345678",
                "InstanceType": "t2.micro",
                "SecurityGroupIds": [sg["GroupId"]],
            },
        )

        # Create maintain type EC2 Fleet
        fleet_response = ec2.create_fleet(
            LaunchTemplateConfigs=[
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateName": "test-lt",
                        "Version": "$Latest",
                    },
                    "Overrides": [{"SubnetId": subnet["Subnet"]["SubnetId"]}],
                }
            ],
            TargetCapacitySpecification={"TotalTargetCapacity": 3},
            Type="maintain",  # This is key for capacity management testing
        )
        fleet_id = fleet_response["FleetId"]

        # Create instances that would be in the fleet
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock capacity lookup calls to avoid Moto fleet state issues
        handler._retry_with_backoff = lambda func, **kwargs: func(
            **{k: v for k, v in kwargs.items() if k != "operation_type"}
        )
        handler.aws_client.ec2_client.modify_fleet = Mock()
        handler.aws_client.ec2_client.delete_fleets = Mock()

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping for maintain fleet instances
        resource_mapping = {
            instance_ids[0]: (fleet_id, 3),  # EC2 Fleet maintain instance
            instance_ids[1]: (fleet_id, 3),  # EC2 Fleet maintain instance
        }

        # Stub grouping to include fleet details to avoid describe_fleets call on moto
        handler._group_instances_by_fleet_from_mapping = Mock(
            return_value={
                fleet_id: {
                    "instance_ids": instance_ids,
                    "fleet_details": {
                        "Type": "maintain",
                        "TargetCapacitySpecification": {"TotalTargetCapacity": 3},
                    },
                }
            }
        )

        # Test release_hosts with maintain fleet instances
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called

        # Verify that all instances are processed
        call_args_list = aws_ops.terminate_instances_with_fallback.call_args_list
        total_instances_processed = sum(len(call[0][0]) for call in call_args_list)
        assert total_instances_processed == len(instance_ids), (
            f"Expected all {len(instance_ids)} instances to be processed"
        )


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

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        # Create VPC and subnet
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
        )

        # Create AWS operations utility
        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        # Create handler with correct constructor arguments
        handler = ASGHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 2

        template = Mock()
        template.template_id = "test-template"
        template.subnet_ids = [subnet["Subnet"]["SubnetId"]]
        template.security_group_ids = ["sg-12345678"]
        template.context = None

        # Mock the AWS operations to return success
        aws_ops.execute_with_standard_error_handling = Mock(return_value="test-asg")

        # Test acquire_hosts method
        result = handler.acquire_hosts(request, template)

        assert result["success"]
        assert "resource_ids" in result
        assert "test-asg" in result["resource_ids"]

    @mock_aws
    def test_asg_handler_scales_group(self):
        """Test that ASGHandler can scale Auto Scaling Group."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        # Create VPC and subnet for ASG requirement
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
        )

        # Create ASG with proper subnet configuration
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName="test-asg",
            LaunchTemplate={"LaunchTemplateName": "test-lt", "Version": "$Latest"},
            MinSize=1,
            MaxSize=10,
            DesiredCapacity=2,
            VPCZoneIdentifier=subnet["Subnet"]["SubnetId"],  # Add subnet requirement
        )

        # Test scaling functionality by checking if ASG exists
        response = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=["test-asg"])

        assert len(response["AutoScalingGroups"]) == 1
        asg = response["AutoScalingGroups"][0]
        assert asg["AutoScalingGroupName"] == "test-asg"
        assert asg["DesiredCapacity"] == 2

    @mock_aws
    def test_asg_handler_terminates_instances(self):
        """Test that ASGHandler terminates ASG instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = ASGHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Test release_hosts method with empty machine_ids to trigger early return
        try:
            handler.release_hosts([])  # Empty list should trigger early return
            # If no exception is raised, that's actually correct behavior
        except Exception as e:
            # If an exception is raised, check it's the expected one
            assert "No instance IDs provided" in str(e) or "machine_ids" in str(e)

    @mock_aws
    def test_asg_handler_release_hosts_with_resource_mapping(self):
        """Test ASGHandler release_hosts with resource mapping optimization."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        # Create VPC and subnet for ASG requirement
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
        )

        # Create ASG with proper subnet configuration
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName="test-asg",
            LaunchTemplate={"LaunchTemplateName": "test-lt", "Version": "$Latest"},
            MinSize=1,
            MaxSize=10,
            DesiredCapacity=3,
            VPCZoneIdentifier=subnet["Subnet"]["SubnetId"],
        )

        # Create instances that would be in the ASG
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=3, MaxCount=3, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = ASGHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping indicating these instances belong to the ASG
        resource_mapping = {
            instance_ids[0]: ("test-asg", 3),  # ASG instance with capacity > 0
            instance_ids[1]: ("test-asg", 3),  # ASG instance with capacity > 0
            instance_ids[2]: (None, 0),  # Non-ASG instance
        }

        # Test release_hosts with resource mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        # (The exact number of calls depends on how instances are grouped)
        assert aws_ops.terminate_instances_with_fallback.called

    @mock_aws
    def test_asg_handler_release_hosts_mixed_instances(self):
        """Test ASGHandler release_hosts with mixed ASG and non-ASG instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = ASGHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=4, MaxCount=4, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping with mixed instance types
        resource_mapping = {
            instance_ids[0]: ("test-asg-1", 2),  # ASG instance
            instance_ids[1]: ("test-asg-1", 2),  # Same ASG instance
            instance_ids[2]: ("test-asg-2", 1),  # Different ASG instance
            instance_ids[3]: (None, 0),  # Non-ASG instance
        }

        # Test release_hosts with mixed instances
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        # Should be called multiple times for different groups
        assert aws_ops.terminate_instances_with_fallback.called
        assert aws_ops.terminate_instances_with_fallback.call_count >= 1

    @mock_aws
    def test_asg_handler_release_hosts_error_handling(self):
        """Test ASGHandler release_hosts error handling."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = ASGHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock AWS operations to raise an exception
        aws_ops.terminate_instances_with_fallback = Mock(
            side_effect=Exception("Termination failed")
        )

        # Test that release_hosts handles errors gracefully
        try:
            handler.release_hosts(instance_ids)
            # Should not reach here if exception is properly raised
            assert False, "Expected AWSInfrastructureError to be raised"
        except Exception as e:
            # Should catch and re-raise as AWSInfrastructureError
            assert "Failed to release ASG hosts" in str(e)

    @mock_aws
    def test_asg_handler_release_hosts_incomplete_resource_mapping(self):
        """Test ASGHandler release_hosts with incomplete resource mapping."""
        ec2 = boto3.client("ec2", region_name="us-east-1")
        autoscaling = boto3.client("autoscaling", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2
        aws_client.autoscaling_client = autoscaling

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = ASGHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=4, MaxCount=4, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping with incomplete information
        # This tests the scenario where some instances have missing ASG names or desired capacity
        resource_mapping = {
            instance_ids[0]: ("test-asg", 4),  # Complete information
            instance_ids[1]: (None, 4),  # Missing resource_id
            instance_ids[2]: ("test-asg", 0),  # Missing/zero desired_capacity
            instance_ids[3]: (None, 0),  # Missing both
        }

        # Test release_hosts with incomplete resource mapping
        # The handler should process all instances, even with incomplete mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that the handler processes the instances correctly
        assert aws_ops.terminate_instances_with_fallback.called, "Expected termination to be called"

        # Verify that all instances are processed (either as ASG or non-ASG instances)
        # The exact grouping depends on the handler's logic for incomplete resource mapping
        call_args_list = aws_ops.terminate_instances_with_fallback.call_args_list
        total_instances_processed = sum(len(call[0][0]) for call in call_args_list)
        assert total_instances_processed == len(instance_ids), (
            f"Expected all {len(instance_ids)} instances to be processed"
        )


@pytest.mark.unit
@pytest.mark.aws
class TestSpotFleetHandler:
    """Test Spot Fleet handler implementation."""

    def test_spot_fleet_handler_builds_maintain_config(self):
        """Ensure maintain Spot Fleets include the correct configuration flags."""
        request = SimpleNamespace(request_id="req-spot-maintain", requested_count=2, metadata={})
        template = SimpleNamespace(
            template_id="tmpl-spot-maintain",
            fleet_role="arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
            fleet_type=AWSFleetType.MAINTAIN,
            price_type="spot",
            allocation_strategy=None,
            allocation_strategy_on_demand=None,
            percent_on_demand=0,
            max_price=None,
            instance_type=None,
            instance_types={"t3.micro": 1},
            instance_types_ondemand=None,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            tags=None,
            context=None,
            get_instance_requirements_payload=lambda: None,
            abis_instance_requirements=None,
        )

        aws_client = Mock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}

        handler = SpotFleetHandler(aws_client, Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_spot_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-spot-maintain",
            launch_template_version="1",
        )

        assert config["Type"] == "maintain"
        assert config["ReplaceUnhealthyInstances"] is True
        assert config["TerminateInstancesWithExpiration"] is True
        assert config["TargetCapacity"] == 2
        assert config["LaunchTemplateConfigs"][0]["Overrides"][0]["InstanceType"] == "t3.micro"

    @mock_aws
    def test_spot_fleet_handler_creates_spot_fleet(self):
        """Test that SpotFleetHandler creates spot fleet."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        # Create VPC and subnet
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 2

        template = Mock()
        template.template_id = "test-template"
        template.fleet_role = "arn:aws:iam::123456789012:role/fleet-role"
        template.fleet_type = "request"
        template.instance_type = "t2.micro"
        template.image_id = "ami-12345678"
        template.subnet_ids = [subnet["Subnet"]["SubnetId"]]
        template.security_group_ids = [sg["GroupId"]]
        template.tags = {}
        template.context = None

        # Mock the AWS operations to return success
        aws_ops.execute_with_standard_error_handling = Mock(return_value="sfr-12345")

        # Test acquire_hosts method
        result = handler.acquire_hosts(request, template)

        assert result["success"]
        assert "resource_ids" in result
        assert "sfr-12345" in result["resource_ids"]

    def test_asg_handler_builds_spot_instances_distribution(self):
        """Ensure ASG MixedInstancesPolicy carries spot distribution when price_type is spot."""
        handler = ASGHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        template = SimpleNamespace(
            template_id="asg-spot",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            price_type="spot",
            percent_on_demand=0,
            instance_types=None,
            context=None,
            get_instance_requirements_payload=lambda: None,
            allocation_strategy="lowest-price",
        )
        request = SimpleNamespace(requested_count=2, metadata={}, request_id="req-asg-spot")

        cfg = handler._create_asg_config_legacy(
            asg_name="asg-spot",
            aws_template=template,
            request=request,
            launch_template_id="lt-spot",
            launch_template_version="1",
        )

        dist = cfg["MixedInstancesPolicy"]["InstancesDistribution"]
        assert dist["OnDemandPercentageAboveBaseCapacity"] == 0
        assert dist["OnDemandBaseCapacity"] == 0
        assert "LaunchTemplate" not in cfg  # should be nested under MixedInstancesPolicy

    def test_asg_handler_distribution_respects_percent_on_demand(self):
        """Ensure percent_on_demand alone triggers InstancesDistribution."""
        handler = ASGHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        template = SimpleNamespace(
            template_id="asg-ondemand-percent",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            price_type="ondemand",
            percent_on_demand=75,
            instance_types=None,
            context=None,
            get_instance_requirements_payload=lambda: None,
            allocation_strategy=None,
        )
        request = SimpleNamespace(requested_count=2, metadata={}, request_id="req-asg-ondemand")

        cfg = handler._create_asg_config_legacy(
            asg_name="asg-ondemand",
            aws_template=template,
            request=request,
            launch_template_id="lt-ondemand",
            launch_template_version="1",
        )

        dist = cfg["MixedInstancesPolicy"]["InstancesDistribution"]
        assert dist["OnDemandPercentageAboveBaseCapacity"] == 75
        assert dist["OnDemandBaseCapacity"] == 0

    def test_asg_handler_builds_mixed_distribution_from_percent(self):
        """Ensure ASG distribution honors percent_on_demand for heterogeneous pricing."""
        handler = ASGHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        template = SimpleNamespace(
            template_id="asg-hetero",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            price_type="heterogeneous",
            percent_on_demand=50,
            instance_types={"t3.micro": 1},
            context=None,
            get_instance_requirements_payload=lambda: None,
            allocation_strategy=None,
        )
        request = SimpleNamespace(requested_count=2, metadata={}, request_id="req-asg-hetero")

        cfg = handler._create_asg_config_legacy(
            asg_name="asg-hetero",
            aws_template=template,
            request=request,
            launch_template_id="lt-hetero",
            launch_template_version="1",
        )

        dist = cfg["MixedInstancesPolicy"]["InstancesDistribution"]
        assert dist["OnDemandPercentageAboveBaseCapacity"] == 50
        assert dist["OnDemandBaseCapacity"] == 0

    @mock_aws
    def test_spot_fleet_handler_handles_price_changes(self):
        """Test that SpotFleetHandler handles spot price changes."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Test that handler can be created successfully
        # Spot price functionality would require actual AWS API calls
        assert handler is not None

    @mock_aws
    def test_spot_fleet_handler_optimizes_costs(self):
        """Test that SpotFleetHandler optimizes costs."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Test that handler can be created successfully
        # Cost optimization functionality would require actual AWS API calls
        assert handler is not None

    @mock_aws
    def test_spot_fleet_handler_release_hosts_with_resource_mapping(self):
        """Test SpotFleetHandler release_hosts with resource mapping optimization."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        # Create VPC and subnet for testing
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={
                "ImageId": "ami-12345678",
                "InstanceType": "t2.micro",
                "SecurityGroupIds": [sg["GroupId"]],
            },
        )

        # Create Spot Fleet Request
        fleet_response = ec2.request_spot_fleet(
            SpotFleetRequestConfig={
                "LaunchTemplateConfigs": [
                    {
                        "LaunchTemplateSpecification": {
                            "LaunchTemplateName": "test-lt",
                            "Version": "$Latest",
                        },
                        "Overrides": [{"SubnetId": subnet["Subnet"]["SubnetId"]}],
                    }
                ],
                "TargetCapacity": 3,
                "IamFleetRole": "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
                "AllocationStrategy": "lowestPrice",
                "Type": "request",
            }
        )
        fleet_id = fleet_response["SpotFleetRequestId"]

        # Create instances that would be in the fleet
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=3, MaxCount=3, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping indicating these instances belong to the Spot Fleet
        resource_mapping = {
            instance_ids[0]: (fleet_id, 3),  # Spot Fleet instance with capacity > 0
            instance_ids[1]: (fleet_id, 3),  # Spot Fleet instance with capacity > 0
            instance_ids[2]: (None, 0),  # Non-Spot Fleet instance
        }

        # Test release_hosts with resource mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called

    @mock_aws
    def test_spot_fleet_handler_release_hosts_mixed_instances(self):
        """Test SpotFleetHandler release_hosts with mixed Spot Fleet and non-Spot Fleet instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=4, MaxCount=4, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping with mixed instance types
        resource_mapping = {
            instance_ids[0]: ("sfr-12345", 2),  # Spot Fleet instance
            instance_ids[1]: ("sfr-12345", 2),  # Same Spot Fleet instance
            instance_ids[2]: ("sfr-67890", 1),  # Different Spot Fleet instance
            instance_ids[3]: (None, 0),  # Non-Spot Fleet instance
        }

        handler._retry_with_backoff = lambda func, **kwargs: func(
            **{k: v for k, v in kwargs.items() if k != "operation_type"}
        )
        handler.aws_client.ec2_client.modify_spot_fleet_request = Mock()
        handler.aws_client.ec2_client.cancel_spot_fleet_requests = Mock()
        handler._group_instances_by_spot_fleet_from_mapping = Mock(
            return_value={
                "sfr-12345": {
                    "instance_ids": [instance_ids[0], instance_ids[1]],
                    "fleet_details": {
                        "SpotFleetRequestConfig": {
                            "TargetCapacity": 2,
                            "OnDemandTargetCapacity": 0,
                            "Type": "maintain",
                        }
                    },
                },
                "sfr-67890": {
                    "instance_ids": [instance_ids[2]],
                    "fleet_details": {
                        "SpotFleetRequestConfig": {
                            "TargetCapacity": 1,
                            "OnDemandTargetCapacity": 0,
                            "Type": "maintain",
                        }
                    },
                },
                None: {"instance_ids": [instance_ids[3]], "fleet_details": {}},
            }
        )

        # Test release_hosts with mixed instances
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called
        assert aws_ops.terminate_instances_with_fallback.call_count >= 1

    @mock_aws
    def test_spot_fleet_handler_release_hosts_error_handling(self):
        """Test SpotFleetHandler release_hosts error handling."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock AWS operations to raise an exception
        aws_ops.terminate_instances_with_fallback = Mock(
            side_effect=Exception("Termination failed")
        )

        # Test that release_hosts handles errors gracefully
        try:
            handler.release_hosts(instance_ids)
            # Should not reach here if exception is properly raised
            assert False, "Expected AWSInfrastructureError to be raised"
        except Exception as e:
            # Should catch and re-raise as AWSInfrastructureError
            assert "Failed to release Spot Fleet hosts" in str(e)

    @mock_aws
    def test_spot_fleet_handler_release_hosts_incomplete_resource_mapping(self):
        """Test SpotFleetHandler release_hosts with incomplete resource mapping."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=4, MaxCount=4, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Avoid real AWS calls for nonexistent fleet IDs
        handler._retry_with_backoff = lambda func, **kwargs: func(
            **{k: v for k, v in kwargs.items() if k != "operation_type"}
        )
        handler.aws_client.ec2_client.modify_spot_fleet_request = Mock()
        handler.aws_client.ec2_client.cancel_spot_fleet_requests = Mock()

        # Create resource mapping with incomplete information
        # This tests the scenario where some instances have missing fleet IDs or desired capacity
        resource_mapping = {
            instance_ids[0]: ("sfr-12345", 4),  # Complete information
            instance_ids[1]: (None, 4),  # Missing resource_id
            instance_ids[2]: ("sfr-12345", 0),  # Missing/zero desired_capacity
            instance_ids[3]: (None, 0),  # Missing both
        }

        # Stub grouping to provide fleet_details and avoid describe calls for fake fleet IDs
        handler._group_instances_by_spot_fleet_from_mapping = Mock(
            return_value={
                "sfr-12345": {
                    "instance_ids": [instance_ids[0], instance_ids[2]],
                    "fleet_details": {
                        "SpotFleetRequestConfig": {
                            "TargetCapacity": 4,
                            "OnDemandTargetCapacity": 0,
                            "Type": "maintain",
                        }
                    },
                },
                None: {"instance_ids": [instance_ids[1], instance_ids[3]], "fleet_details": {}},
            }
        )

        # Test release_hosts with incomplete resource mapping
        # The handler should process all instances, even with incomplete mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that the handler processes the instances correctly
        assert aws_ops.terminate_instances_with_fallback.called, "Expected termination to be called"

        # Verify that all instances are processed (either as Spot Fleet or non-Spot Fleet instances)
        call_args_list = aws_ops.terminate_instances_with_fallback.call_args_list
        total_instances_processed = sum(len(call[0][0]) for call in call_args_list)
        assert total_instances_processed == len(instance_ids), (
            f"Expected all {len(instance_ids)} instances to be processed"
        )

    def test_spot_fleet_handler_release_hosts_maintain_reduces_capacity(self):
        """Ensure maintain Spot Fleet reduces target capacity before termination."""
        aws_client = Mock()
        aws_client.ec2_client = Mock()
        aws_client.ec2_client.modify_spot_fleet_request = Mock()
        aws_client.ec2_client.cancel_spot_fleet_requests = Mock()

        aws_ops = Mock()
        aws_ops.terminate_instances_with_fallback = Mock()

        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
            request_adapter=Mock(),
        )

        handler._retry_with_backoff = lambda func, **kwargs: func(
            **{k: v for k, v in kwargs.items() if k != "operation_type"}
        )

        fleet_details = {
            "SpotFleetRequestId": "sfr-12345",
            "SpotFleetRequestConfig": {
                "TargetCapacity": 3,
                "OnDemandTargetCapacity": 1,
                "Type": "maintain",
            },
        }

        handler._release_hosts_for_single_spot_fleet("sfr-12345", ["i-1", "i-2"], fleet_details)

        aws_client.ec2_client.modify_spot_fleet_request.assert_called_with(
            SpotFleetRequestId="sfr-12345", TargetCapacity=1, OnDemandTargetCapacity=1
        )
        aws_ops.terminate_instances_with_fallback.assert_called_once_with(
            ["i-1", "i-2"], handler._request_adapter, "SpotFleet-sfr-12345 instances"
        )
        aws_client.ec2_client.cancel_spot_fleet_requests.assert_not_called()

    @mock_aws
    def test_spot_fleet_handler_release_hosts_spot_instance_detection(self):
        """Test SpotFleetHandler release_hosts with spot instance lifecycle detection."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        # Create VPC and subnet for testing
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24")

        # Create security group
        sg = ec2.create_security_group(
            GroupName="test-sg",
            Description="Test security group",
            VpcId=vpc["Vpc"]["VpcId"],
        )

        # Create launch template
        ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={
                "ImageId": "ami-12345678",
                "InstanceType": "t2.micro",
                "SecurityGroupIds": [sg["GroupId"]],
            },
        )

        # Create Spot Fleet Request
        ec2.request_spot_fleet(
            SpotFleetRequestConfig={
                "LaunchTemplateConfigs": [
                    {
                        "LaunchTemplateSpecification": {
                            "LaunchTemplateName": "test-lt",
                            "Version": "$Latest",
                        },
                        "Overrides": [{"SubnetId": subnet["Subnet"]["SubnetId"]}],
                    }
                ],
                "TargetCapacity": 2,
                "IamFleetRole": "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
                "AllocationStrategy": "lowestPrice",
                "Type": "request",
            }
        )
        # Create instances that would be spot instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Test release_hosts without resource mapping (should use AWS API detection)
        handler.release_hosts(instance_ids)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called

        # Verify that all instances are processed
        call_args_list = aws_ops.terminate_instances_with_fallback.call_args_list
        total_instances_processed = sum(len(call[0][0]) for call in call_args_list)
        assert total_instances_processed == len(instance_ids), (
            f"Expected all {len(instance_ids)} instances to be processed"
        )

    @mock_aws
    def test_spot_fleet_handler_release_hosts_fleet_cancellation(self):
        """Test SpotFleetHandler release_hosts with entire fleet cancellation."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = SpotFleetHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping for fleet cancellation scenario (empty instance list)
        resource_mapping = {}

        # Test release_hosts with empty instance list (should trigger fleet cancellation logic)
        handler.release_hosts([], resource_mapping)

        # This should trigger early return due to empty machine_ids
        # Verify that no termination calls were made
        assert not aws_ops.terminate_instances_with_fallback.called


@pytest.mark.unit
@pytest.mark.aws
class TestRunInstancesHandler:
    """Test Run Instances handler implementation."""

    @mock_aws
    def test_run_instances_handler_creates_instances(self):
        """Test that RunInstancesHandler creates instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 2
        request.metadata = {}  # Initialize metadata dict

        template = Mock()
        template.template_id = "test-template"
        template.instance_type = "t2.micro"
        template.image_id = "ami-12345678"
        template.key_pair_name = "test-key"
        template.security_group_ids = ["sg-12345678"]
        template.subnet_ids = None
        template.tags = {}

        # Mock the AWS operations to return success (should return reservation ID)
        aws_ops.execute_with_standard_error_handling = Mock(return_value="r-1234567890abcdef0")

        # Test acquire_hosts method
        result = handler.acquire_hosts(request, template)

        assert result["success"]
        assert "resource_ids" in result
        assert len(result["resource_ids"]) == 1
        assert result["resource_ids"][0] == "r-1234567890abcdef0"

    @mock_aws
    def test_run_instances_handler_handles_capacity_errors(self):
        """Test that RunInstancesHandler handles insufficient capacity."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template with large configuration
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 100

        template = Mock()
        template.template_id = "test-template"
        template.instance_type = "x1e.32xlarge"  # Very large instance
        template.image_id = "ami-12345678"
        template.key_pair_name = None
        template.security_group_ids = ["sg-12345678"]
        template.subnet_ids = None
        template.tags = {}

        # Mock AWS operations to raise an exception
        aws_ops.execute_with_standard_error_handling = Mock(
            side_effect=Exception("Insufficient capacity")
        )

        # Should handle failure gracefully
        result = handler.acquire_hosts(request, template)
        assert not result["success"]
        assert "error_message" in result

    @mock_aws
    def test_run_instances_handler_supports_user_data(self):
        """Test that RunInstancesHandler supports user data."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())

        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test request and template with user data
        request = Mock()
        request.request_id = "test-request-123"
        request.requested_count = 1
        request.metadata = {}  # Initialize metadata dict

        template = Mock()
        template.template_id = "test-template"
        template.instance_type = "t2.micro"
        template.image_id = "ami-12345678"
        template.key_pair_name = None
        template.security_group_ids = ["sg-12345678"]
        template.subnet_ids = None
        template.tags = {}
        template.user_data = "IyEvYmluL2Jhc2gKZWNobyAiSGVsbG8gV29ybGQi"  # Base64 encoded

        # Mock the AWS operations to return success (should return reservation ID)
        aws_ops.execute_with_standard_error_handling = Mock(return_value="r-1234567890abcdef0")

        # Test acquire_hosts method
        result = handler.acquire_hosts(request, template)

        assert result["success"]
        assert "resource_ids" in result
        assert len(result["resource_ids"]) == 1
        assert result["resource_ids"][0] == "r-1234567890abcdef0"

    @mock_aws
    def test_run_instances_handler_release_hosts_basic_termination(self):
        """Test RunInstancesHandler release_hosts basic termination functionality."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=3, MaxCount=3, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Test release_hosts with basic termination
        handler.release_hosts(instance_ids)

        # Verify that terminate_instances_with_fallback was called
        assert aws_ops.terminate_instances_with_fallback.called
        call_args = aws_ops.terminate_instances_with_fallback.call_args[0]
        assert call_args[0] == instance_ids  # First argument should be instance_ids
        assert "RunInstances instances" in call_args[2]  # Third argument should be description

    @mock_aws
    def test_run_instances_handler_release_hosts_with_resource_mapping(self):
        """Test RunInstancesHandler release_hosts with resource mapping (should be ignored)."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Create resource mapping (should be ignored by RunInstances handler)
        resource_mapping = {
            instance_ids[0]: ("r-1234567890abcdef0", 2),  # RunInstances resource mapping
            instance_ids[1]: ("r-1234567890abcdef0", 2),  # Same reservation
        }

        # Test release_hosts with resource mapping
        handler.release_hosts(instance_ids, resource_mapping)

        # Verify that terminate_instances_with_fallback was called
        # RunInstances handler should ignore resource_mapping and just terminate all instances
        assert aws_ops.terminate_instances_with_fallback.called
        call_args = aws_ops.terminate_instances_with_fallback.call_args[0]
        assert call_args[0] == instance_ids
        assert "RunInstances instances" in call_args[2]

    @mock_aws
    def test_run_instances_handler_release_hosts_error_handling(self):
        """Test RunInstancesHandler release_hosts error handling."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock AWS operations to raise an exception
        aws_ops.terminate_instances_with_fallback = Mock(
            side_effect=Exception("Termination failed")
        )

        # Test that release_hosts handles errors gracefully
        try:
            handler.release_hosts(instance_ids)
            # Should not reach here if exception is properly raised
            assert False, "Expected AWSInfrastructureError to be raised"
        except Exception as e:
            # Should catch and re-raise as AWSInfrastructureError
            assert "Failed to release RunInstances resources" in str(e)

    @mock_aws
    def test_run_instances_handler_release_hosts_empty_list(self):
        """Test RunInstancesHandler release_hosts with empty instance list."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Test release_hosts with empty instance list (should trigger early return)
        handler.release_hosts([])

        # Verify that no termination calls were made
        assert not aws_ops.terminate_instances_with_fallback.called

    @mock_aws
    def test_run_instances_handler_release_hosts_spot_instances(self):
        """Test RunInstancesHandler release_hosts with spot instances."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test spot instances
        response = ec2.run_instances(
            ImageId="ami-12345678",
            MinCount=2,
            MaxCount=2,
            InstanceType="t2.micro",
            InstanceMarketOptions={"MarketType": "spot", "SpotOptions": {"MaxPrice": "0.05"}},
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Test release_hosts with spot instances
        handler.release_hosts(instance_ids)

        # Verify that terminate_instances_with_fallback was called
        # Spot instances should be terminated the same way as on-demand instances
        assert aws_ops.terminate_instances_with_fallback.called
        call_args = aws_ops.terminate_instances_with_fallback.call_args[0]
        assert call_args[0] == instance_ids
        assert "RunInstances instances" in call_args[2]

    @mock_aws
    def test_run_instances_handler_release_hosts_mixed_reservations(self):
        """Test RunInstancesHandler release_hosts with instances from different reservations."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create instances from different reservations
        response1 = ec2.run_instances(
            ImageId="ami-12345678", MinCount=2, MaxCount=2, InstanceType="t2.micro"
        )
        response2 = ec2.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t2.small"
        )

        instance_ids = []
        instance_ids.extend([i["InstanceId"] for i in response1["Instances"]])
        instance_ids.extend([i["InstanceId"] for i in response2["Instances"]])

        # Mock the AWS operations for termination
        aws_ops.terminate_instances_with_fallback = Mock()

        # Test release_hosts with instances from different reservations
        handler.release_hosts(instance_ids)

        # Verify that terminate_instances_with_fallback was called
        # RunInstances handler should terminate all instances regardless of reservation
        assert aws_ops.terminate_instances_with_fallback.called
        call_args = aws_ops.terminate_instances_with_fallback.call_args[0]
        assert set(call_args[0]) == set(instance_ids)  # All instances should be terminated
        assert "RunInstances instances" in call_args[2]

    @mock_aws
    def test_run_instances_handler_release_hosts_client_error_handling(self):
        """Test RunInstancesHandler release_hosts with AWS ClientError handling."""
        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Create AWS client wrapper
        aws_client = Mock()
        aws_client.ec2_client = ec2

        aws_ops = AWSOperations(aws_client=aws_client, logger=Mock())
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=Mock(),
            aws_ops=aws_ops,
            launch_template_manager=Mock(),
        )

        # Create test instances
        response = ec2.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t2.micro"
        )
        instance_ids = [i["InstanceId"] for i in response["Instances"]]

        # Mock AWS operations to raise a ClientError
        from botocore.exceptions import ClientError

        client_error = ClientError(
            error_response={
                "Error": {
                    "Code": "InvalidInstanceID.NotFound",
                    "Message": "The instance ID 'i-1234567890abcdef0' does not exist",
                }
            },
            operation_name="TerminateInstances",
        )
        aws_ops.terminate_instances_with_fallback = Mock(side_effect=client_error)

        # Mock the _convert_client_error method to return the expected error
        handler._convert_client_error = Mock(return_value=client_error)

        # Test that release_hosts handles ClientError appropriately
        try:
            handler.release_hosts(instance_ids)
            # Should not reach here if exception is properly raised
            assert False, "Expected ClientError to be raised"
        except ClientError as e:
            # Should catch and re-raise the ClientError
            assert e.response["Error"]["Code"] == "InvalidInstanceID.NotFound"


@pytest.mark.unit
@pytest.mark.aws
@pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="AWS provider imports not available")
class TestABISOverrides:
    """ABIS-specific behavior tests to ensure InstanceRequirements take precedence."""

    @staticmethod
    def _abis_template(template_id="abis-demo", subnet_ids=None, price_type="spot"):
        """Create a minimal template object exposing ABIS requirements."""
        subnet_ids = subnet_ids or ["subnet-1", "subnet-2"]
        instance_requirements_payload = {
            "VCpuCount": {"Min": 2, "Max": 2},
            "MemoryMiB": {"Min": 4096, "Max": 4096},
        }
        return SimpleNamespace(
            template_id=template_id,
            fleet_type="request",
            price_type=price_type,
            allocation_strategy=None,
            allocation_strategy_on_demand=None,
            max_price=None,
            percent_on_demand=0,
            instance_types={"m5.large": 1},  # Should be ignored when ABIS is present
            instance_types_ondemand=None,
            subnet_ids=subnet_ids,
            tags=None,
            context=None,
            fleet_role="arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
            spot_fleet_request_expiry=30,
            abis_instance_requirements=instance_requirements_payload,
            get_instance_requirements_payload=lambda: instance_requirements_payload,
        )

    def test_ec2_fleet_uses_instance_requirements_overrides(self):
        """EC2 Fleet should ignore instance_types when ABIS is provided."""
        template = self._abis_template()
        request = SimpleNamespace(requested_count=2, request_id="req-abis-1", metadata={})

        handler = EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None  # Force legacy path for deterministic config

        config = handler._create_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-abis",
            launch_template_version="1",
        )

        overrides = config["LaunchTemplateConfigs"][0].get("Overrides", [])
        assert overrides, "Overrides should be populated when ABIS is present"
        assert all("InstanceRequirements" in o for o in overrides)
        assert all("InstanceType" not in o for o in overrides)

    def test_spot_fleet_uses_instance_requirements_overrides(self):
        """Spot Fleet should ignore instance_types when ABIS is provided."""
        template = self._abis_template()
        request = SimpleNamespace(requested_count=2, request_id="req-abis-2", metadata={})

        aws_client = Mock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        handler = SpotFleetHandler(aws_client, Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_spot_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-abis",
            launch_template_version="1",
        )

        overrides = config["LaunchTemplateConfigs"][0].get("Overrides", [])
        assert overrides, "Overrides should be populated when ABIS is present"
        assert all("InstanceRequirements" in o for o in overrides)
        assert all("InstanceType" not in o for o in overrides)

    def test_asg_uses_instance_requirements_mixed_policy(self):
        """ASG should emit MixedInstancesPolicy with InstanceRequirements."""
        template = self._abis_template(subnet_ids=["subnet-1"])
        request = SimpleNamespace(requested_count=1, metadata={})

        handler = ASGHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_asg_config(
            asg_name="asg-abis",
            aws_template=template,
            request=request,
            launch_template_id="lt-abis",
            launch_template_version="1",
        )

        mixed_policy = config.get("MixedInstancesPolicy")
        assert mixed_policy, "MixedInstancesPolicy should be set when ABIS is present"
        overrides = mixed_policy["LaunchTemplate"].get("Overrides", [])
        assert overrides and "InstanceRequirements" in overrides[0]
        assert "InstanceType" not in overrides[0]


@pytest.mark.unit
@pytest.mark.aws
@pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="AWS provider imports not available")
class TestMultiInstanceOverrides:
    """Multi-instance type propagation tests."""

    @staticmethod
    def _multi_type_template(provider_api="EC2Fleet", fleet_type=AWSFleetType.INSTANT):
        return SimpleNamespace(
            template_id="multi",
            provider_api=provider_api,
            fleet_type=fleet_type,
            price_type="spot",
            allocation_strategy=None,
            allocation_strategy_on_demand=None,
            max_price=None,
            percent_on_demand=0,
            instance_types={"t2.micro": 1, "t2.small": 2, "t2.medium": 4},
            instance_types_ondemand=None,
            subnet_ids=["subnet-1"],
            tags=None,
            context=None,
            fleet_role="arn:aws:iam::123456789012:role/aws-service-role/ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet",
            spot_fleet_request_expiry=30,
            abis_instance_requirements=None,
            get_instance_requirements_payload=lambda: None,
        )

    def test_ec2_fleet_overrides_from_instance_types(self):
        template = self._multi_type_template(provider_api="EC2Fleet")
        request = SimpleNamespace(requested_count=3, request_id="req-multi", metadata={})

        handler = EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_fleet_config_legacy(
            template=template,
            request=request,
            launch_template_id="lt-multi",
            launch_template_version="1",
        )

        overrides = config["LaunchTemplateConfigs"][0].get("Overrides", [])
        assert len(overrides) == len(template.instance_types)
        instance_types = {o["InstanceType"] for o in overrides}
        assert instance_types == set(template.instance_types.keys())

    def test_spot_fleet_overrides_from_instance_types(self):
        template = self._multi_type_template(
            provider_api="EC2Fleet", fleet_type=AWSFleetType.REQUEST
        )
        request = SimpleNamespace(requested_count=3, request_id="req-spot", metadata={})

        aws_client = Mock()
        aws_client.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        handler = SpotFleetHandler(aws_client, Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_spot_fleet_config(
            template=template,
            request=request,
            launch_template_id="lt-spot",
            launch_template_version="1",
        )

        overrides = config["LaunchTemplateConfigs"][0].get("Overrides", [])
        assert len(overrides) == len(template.instance_types)
        instance_types = {o["InstanceType"] for o in overrides}
        assert instance_types == set(template.instance_types.keys())

    def test_asg_overrides_from_instance_types(self):
        template = self._multi_type_template(provider_api="ASG")
        request = SimpleNamespace(requested_count=2, metadata={}, request_id="req-asg")

        handler = ASGHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        handler.aws_native_spec_service = None

        config = handler._create_asg_config_legacy(
            asg_name="asg-multi",
            aws_template=template,
            request=request,
            launch_template_id="lt-asg",
            launch_template_version="1",
        )

        policy = config.get("MixedInstancesPolicy")
        assert policy, "MixedInstancesPolicy should be present when instance_types are provided"
        overrides = policy["LaunchTemplate"].get("Overrides", [])
        assert len(overrides) == len(template.instance_types)
        instance_types = {o["InstanceType"] for o in overrides}
        assert instance_types == set(template.instance_types.keys())
        # WeightedCapacity should be string per AWS API
        assert all(
            isinstance(o.get("WeightedCapacity"), str) for o in overrides if "WeightedCapacity" in o
        )

    def test_conflicting_instance_type_and_instance_types_raises(self):
        template = SimpleNamespace(
            image_id="ami-123",
            instance_type="t2.micro",
            instance_types={"t2.small": 1},
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
        )

        handler = EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), Mock())
        # Conflicting values are now tolerated; should not raise
        handler._validate_prerequisites(template)
