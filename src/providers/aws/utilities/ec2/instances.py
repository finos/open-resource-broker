"""EC2 instance management utility functions."""

from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError

from domain.base.exceptions import InfrastructureError
from infrastructure.logging.logger import get_logger
from infrastructure.resilience import retry

# Logger
logger = get_logger(__name__)


def get_instance_by_id(instance_id: str, aws_client: Any = None) -> Dict[str, Any]:
    """
    Get an EC2 instance by ID.

    Args:
        instance_id: EC2 instance ID
        aws_client: AWS client to use

    Returns:
        EC2 instance details

    Raises:
        InfrastructureError: If instance cannot be found
    """
    try:
        # Require AWSClient for consistent configuration
        if not aws_client:
            raise ValueError("AWSClient is required for EC2 operations")
        ec2_client = aws_client.ec2_client

        # Call with retry built into the function
        response = _describe_instance(ec2_client, instance_id)

        # Check if instance exists
        if not response["Reservations"] or not response["Reservations"][0]["Instances"]:
            raise InfrastructureError("AWS.EC2", f"EC2 instance {instance_id} not found")

        return response["Reservations"][0]["Instances"][0]

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            f"Failed to get EC2 instance {instance_id}: {error_code} - {error_message}",
            extra={
                "instance_id": instance_id,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

        raise InfrastructureError(
            "AWS.EC2",
            f"Failed to get EC2 instance {instance_id}: {error_code} - {error_message}",
        )

    except Exception as e:
        logger.error(
            f"Unexpected error getting EC2 instance {instance_id}: {str(e)}",
            extra={"instance_id": instance_id, "error": str(e)},
        )

        raise InfrastructureError(
            "AWS.EC2", f"Unexpected error getting EC2 instance {instance_id}: {str(e)}"
        )


def create_instance(
    image_id: str,
    instance_type: str,
    key_name: Optional[str] = None,
    security_groups: Optional[List[str]] = None,
    subnet_id: Optional[str] = None,
    user_data: Optional[str] = None,
    tags: Optional[List[Dict[str, str]]] = None,
    aws_client: Any = None,
) -> Dict[str, Any]:
    """
    Create an EC2 instance.

    Args:
        image_id: AMI ID
        instance_type: Instance type
        key_name: Key pair name
        security_groups: Security group IDs
        subnet_id: Subnet ID
        user_data: User data script
        tags: Instance tags
        aws_client: AWS client to use

    Returns:
        Created instance details

    Raises:
        InfrastructureError: If instance cannot be created
    """
    try:
        # Require AWSClient for consistent configuration
        if not aws_client:
            raise ValueError("AWSClient is required for EC2 operations")
        ec2_client = aws_client.ec2_client

        # Build parameters
        params = {
            "ImageId": image_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
        }

        if key_name:
            params["KeyName"] = key_name

        if security_groups:
            params["SecurityGroupIds"] = security_groups

        if subnet_id:
            params["SubnetId"] = subnet_id

        if user_data:
            params["UserData"] = user_data

        # Create instance with retry built-in
        response = _run_instance(ec2_client, params)

        instance = response["Instances"][0]
        instance_id = instance["InstanceId"]

        # Add tags if provided (with retry built-in)
        if tags:
            _create_tags(ec2_client, instance_id, tags)

        logger.info(
            f"Created EC2 instance {instance_id}",
            extra={
                "instance_id": instance_id,
                "image_id": image_id,
                "instance_type": instance_type,
            },
        )

        return instance

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            f"Failed to create EC2 instance: {error_code} - {error_message}",
            extra={
                "image_id": image_id,
                "instance_type": instance_type,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

        raise InfrastructureError(
            "AWS.EC2", f"Failed to create EC2 instance: {error_code} - {error_message}"
        )

    except Exception as e:
        logger.error(
            f"Unexpected error creating EC2 instance: {str(e)}",
            extra={
                "image_id": image_id,
                "instance_type": instance_type,
                "error": str(e),
            },
        )

        raise InfrastructureError("AWS.EC2", f"Unexpected error creating EC2 instance: {str(e)}")


def terminate_instance(instance_id: str, aws_client: Any = None) -> Dict[str, Any]:
    """
    Terminate an EC2 instance.

    Args:
        instance_id: EC2 instance ID
        aws_client: AWS client to use

    Returns:
        Termination response

    Raises:
        InfrastructureError: If instance cannot be terminated
    """
    try:
        # Require AWSClient for consistent configuration
        if not aws_client:
            raise ValueError("AWSClient is required for EC2 operations")
        ec2_client = aws_client.ec2_client

        # Terminate instance with retry built-in
        response = _terminate_instance(ec2_client, instance_id)

        logger.info(f"Terminated EC2 instance {instance_id}", extra={"instance_id": instance_id})

        return response

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            f"Failed to terminate EC2 instance {instance_id}: {error_code} - {error_message}",
            extra={
                "instance_id": instance_id,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

        raise InfrastructureError(
            "AWS.EC2",
            f"Failed to terminate EC2 instance {instance_id}: {error_code} - {error_message}",
        )

    except Exception as e:
        logger.error(
            f"Unexpected error terminating EC2 instance {instance_id}: {str(e)}",
            extra={"instance_id": instance_id, "error": str(e)},
        )

        raise InfrastructureError(
            "AWS.EC2",
            f"Unexpected error terminating EC2 instance {instance_id}: {str(e)}",
        )


# Helper functions with retry
@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _describe_instance(ec2_client: Any, instance_id: str) -> Dict[str, Any]:
    """Describe an EC2 instance."""
    return ec2_client.describe_instances(InstanceIds=[instance_id])


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _run_instance(ec2_client: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run an EC2 instance."""
    return ec2_client.run_instances(**params)


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _terminate_instance(ec2_client: Any, instance_id: str) -> Dict[str, Any]:
    """Terminate an EC2 instance."""
    return ec2_client.terminate_instances(InstanceIds=[instance_id])


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _create_tags(ec2_client: Any, instance_id: str, tags: List[Dict[str, str]]) -> Dict[str, Any]:
    """Create tags for an EC2 instance."""
    return ec2_client.create_tags(Resources=[instance_id], Tags=tags)
