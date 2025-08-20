"""AWS Instance Manager implementation."""

from typing import Any, Dict, List

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context


@injectable
class AWSInstanceManager:
    """AWS implementation of InstanceManagerPort."""

    def __init__(
        self, aws_client: AWSClient, config: AWSProviderConfig, logger: LoggingPort
    ) -> None:
        """Initialize AWS instance manager."""
        self._aws_client = aws_client
        self._config = config
        self._logger = logger

    def create_instances(self, template_config: Dict[str, Any], count: int) -> List[str]:
        """Create instances based on template configuration."""
        with aws_dry_run_context():
            try:
                ec2_client = self._aws_client.ec2_client

                # Use only internal domain field names (scheduler strategy handles
                # translation)
                instance_type = template_config.get("instance_type", "t2.micro")
                image_id = template_config.get("image_id", "ami-0b3e7dd7b2a99b08d")

                # Build run_instances parameters using domain fields
                params = {
                    "ImageId": image_id,
                    "InstanceType": instance_type,
                    "MinCount": count,
                    "MaxCount": count,
                }

                # Add optional parameters using domain field names
                if template_config.get("user_data"):
                    params["UserData"] = template_config["user_data"]

                # Add subnet configuration
                subnet_ids = template_config.get("subnet_ids")
                if subnet_ids:
                    if isinstance(subnet_ids, list):
                        params["SubnetId"] = subnet_ids[0]
                    else:
                        params["SubnetId"] = subnet_ids

                # Add security group configuration
                security_groups = template_config.get("security_group_ids")
                if security_groups:
                    params["SecurityGroupIds"] = (
                        security_groups if isinstance(security_groups, list) else [security_groups]
                    )

                # Add key name if specified
                key_name = template_config.get("key_name")
                if key_name:
                    params["KeyName"] = key_name

                # Create instances
                response = ec2_client.run_instances(**params)

                # Extract instance IDs
                instance_ids = [instance["InstanceId"] for instance in response["Instances"]]

                # Add tags if specified
                if template_config.get("tags") and instance_ids:
                    tags = [{"Key": k, "Value": v} for k, v in template_config["tags"].items()]
                    ec2_client.create_tags(Resources=instance_ids, Tags=tags)

                return instance_ids

            except Exception as e:
                self._logger.error("Failed to create instances: %s", e)
                return []

    def terminate_instances(self, instance_ids: List[str]) -> bool:
        """Terminate instances by ID."""
        with aws_dry_run_context():
            try:
                ec2_client = self._aws_client.ec2_client
                # Terminate instances (mocked if dry-run is active)
                response = ec2_client.terminate_instances(InstanceIds=instance_ids)

                # Check if all instances are terminating
                terminating_count = len(response.get("TerminatingInstances", []))
                return terminating_count == len(instance_ids)

            except Exception as e:
                self._logger.error("Failed to terminate instances: %s", e)
                return False

    def get_instance_status(self, instance_ids: List[str]) -> Dict[str, str]:
        """Get status of instances."""
        with aws_dry_run_context():
            try:
                ec2_client = self._aws_client.ec2_client
                # Describe instances (mocked if dry-run is active)
                response = ec2_client.describe_instances(InstanceIds=instance_ids)

                status_map = {}
                for reservation in response["Reservations"]:
                    for aws_instance in reservation["Instances"]:
                        instance_id = aws_instance["InstanceId"]
                        state = aws_instance["State"]["Name"]
                        status_map[instance_id] = state

                return status_map

            except Exception as e:
                self._logger.error("Failed to get instance status: %s", e)
                return {instance_id: "error" for instance_id in instance_ids}

    def start_instances(self, instance_ids: List[str]) -> Dict[str, bool]:
        """Start stopped instances."""
        try:
            ec2_client = self._aws_client.ec2_client
            response = ec2_client.start_instances(InstanceIds=instance_ids)

            results = {}
            for instance in response.get("StartingInstances", []):
                instance_id = instance["InstanceId"]
                current_state = instance["CurrentState"]["Name"]
                results[instance_id] = current_state in ["pending", "running"]

            return results

        except Exception as e:
            self._logger.error("Failed to start instances: %s", e)
            return {instance_id: False for instance_id in instance_ids}

    def stop_instances(self, instance_ids: List[str]) -> Dict[str, bool]:
        """Stop running instances."""
        try:
            ec2_client = self._aws_client.ec2_client
            response = ec2_client.stop_instances(InstanceIds=instance_ids)

            results = {}
            for instance in response.get("StoppingInstances", []):
                instance_id = instance["InstanceId"]
                current_state = instance["CurrentState"]["Name"]
                results[instance_id] = current_state in ["stopping", "stopped"]

            return results

        except Exception as e:
            self._logger.error("Failed to stop instances: %s", e)
            return {instance_id: False for instance_id in instance_ids}

    def get_instances_by_tags(self, tags: Dict[str, str]) -> List[str]:
        """Find instance IDs by tags."""
        with aws_dry_run_context():
            try:
                ec2_client = self._aws_client.ec2_client

                # Build filters for tags
                filters = []
                for key, value in tags.items():
                    filters.append({"Name": f"tag:{key}", "Values": [value]})

                response = ec2_client.describe_instances(Filters=filters)

                instance_ids = []
                for reservation in response["Reservations"]:
                    for aws_instance in reservation["Instances"]:
                        instance_ids.append(aws_instance["InstanceId"])

                return instance_ids

            except Exception as e:
                self._logger.error("Failed to get instances by tags: %s", e)
                return []
