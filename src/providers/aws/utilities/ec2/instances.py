"""EC2 instance management utility functions."""

from typing import Any, Optional

from botocore.exceptions import ClientError

from domain.base.exceptions import InfrastructureError
from infrastructure.logging.logger import get_logger
from infrastructure.resilience import retry

# Logger
logger = get_logger(__name__)


def is_private_ip_address(identifier: str) -> bool:
    """
    Check if the given identifier is a private IP address.

    Args:
        identifier: String to check

    Returns:
        True if the identifier is a valid IPv4 private IP address, False otherwise
    """
    import re

    # IPv4 address pattern
    ip_pattern = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    )
    return bool(ip_pattern.match(identifier.strip()))


def is_instance_id(identifier: str) -> bool:
    """
    Check if the given identifier is an EC2 instance ID.

    Args:
        identifier: String to check

    Returns:
        True if the identifier is a valid EC2 instance ID format, False otherwise
    """
    import re

    # EC2 instance ID pattern: i-xxxxxxxxx (8-17 hex characters)
    instance_id_pattern = re.compile(r"^i-[0-9a-f]{8,17}$")
    return bool(instance_id_pattern.match(identifier.strip()))


def get_instance_by_id(instance_id: str, aws_client: Any = None) -> dict[str, Any]:
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
            "Failed to get EC2 instance %s: %s - %s",
            instance_id,
            error_code,
            error_message,
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
            "Unexpected error getting EC2 instance %s: %s",
            instance_id,
            str(e),
            extra={"instance_id": instance_id, "error": str(e)},
        )

        raise InfrastructureError(
            "AWS.EC2", f"Unexpected error getting EC2 instance {instance_id}: {e!s}"
        )


def create_instance(
    image_id: str,
    instance_type: str,
    key_name: Optional[str] = None,
    security_groups: Optional[list[str]] = None,
    subnet_id: Optional[str] = None,
    user_data: Optional[str] = None,
    tags: Optional[list[dict[str, str]]] = None,
    aws_client: Any = None,
) -> dict[str, Any]:
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
            import base64

            params["UserData"] = base64.b64encode(user_data.encode("utf-8")).decode("ascii")

        # Create instance with retry built-in
        response = _run_instance(ec2_client, params)

        instance = response["Instances"][0]
        instance_id = instance["InstanceId"]

        # Add tags if provided (with retry built-in)
        if tags:
            _create_tags(ec2_client, instance_id, tags)

        logger.info(
            "Created EC2 instance %s",
            instance_id,
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
            "Failed to create EC2 instance: %s - %s",
            error_code,
            error_message,
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
            "Unexpected error creating EC2 instance: %s",
            str(e),
            extra={
                "image_id": image_id,
                "instance_type": instance_type,
                "error": str(e),
            },
        )

        raise InfrastructureError("AWS.EC2", f"Unexpected error creating EC2 instance: {e!s}")


def get_instance_by_private_ip(private_ip: str, aws_client: Any = None) -> dict[str, Any]:
    """
    Get an EC2 instance by private IP address.

    Args:
        private_ip: Private IP address of the instance
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
        response = _describe_instances_by_private_ip(ec2_client, private_ip)

        # Check if instance exists
        if not response["Reservations"]:
            raise InfrastructureError(
                "AWS.EC2", f"EC2 instance with private IP {private_ip} not found"
            )

        # Find the instance with matching private IP
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                if instance.get("PrivateIpAddress") == private_ip:
                    return instance

        raise InfrastructureError("AWS.EC2", f"EC2 instance with private IP {private_ip} not found")

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            "Failed to get EC2 instance by private IP %s: %s - %s",
            private_ip,
            error_code,
            error_message,
            extra={
                "private_ip": private_ip,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

        raise InfrastructureError(
            "AWS.EC2",
            f"Failed to get EC2 instance by private IP {private_ip}: {error_code} - {error_message}",
        )

    except Exception as e:
        logger.error(
            "Unexpected error getting EC2 instance by private IP %s: %s",
            private_ip,
            str(e),
            extra={"private_ip": private_ip, "error": str(e)},
        )

        raise InfrastructureError(
            "AWS.EC2", f"Unexpected error getting EC2 instance by private IP {private_ip}: {e!s}"
        )


def resolve_machine_identifiers(identifiers: list[str], aws_client: Any = None) -> list[str]:
    """
    Resolve a list of machine identifiers (instance IDs or private IPs) to instance IDs.

    Args:
        identifiers: List of EC2 instance IDs or private IP addresses
        aws_client: AWS client to use

    Returns:
        List of EC2 instance IDs

    Raises:
        InfrastructureError: If any identifier cannot be resolved
    """
    if not identifiers:
        return []

    # Require AWSClient for consistent configuration
    if not aws_client:
        raise ValueError("AWSClient is required for EC2 operations")

    resolved_instance_ids = []

    for identifier in identifiers:
        identifier = identifier.strip()

        if is_instance_id(identifier):
            # It's already an instance ID, validate it exists
            try:
                get_instance_by_id(identifier, aws_client)
                resolved_instance_ids.append(identifier)
                logger.info(f"Validated instance ID: {identifier}")
            except InfrastructureError:
                logger.error(f"Instance ID {identifier} not found")
                raise InfrastructureError("AWS.EC2", f"Instance ID {identifier} not found")

        elif is_private_ip_address(identifier):
            # It's a private IP address, resolve to instance ID
            try:
                instance = get_instance_by_private_ip(identifier, aws_client)
                instance_id = instance["InstanceId"]
                resolved_instance_ids.append(instance_id)
                logger.info(f"Resolved private IP {identifier} to instance ID: {instance_id}")
            except InfrastructureError:
                logger.error(f"Private IP {identifier} could not be resolved to an instance")
                raise InfrastructureError(
                    "AWS.EC2", f"Private IP {identifier} could not be resolved to an instance"
                )
        else:
            # Invalid format
            raise InfrastructureError(
                "AWS.EC2",
                f"Invalid machine identifier format: {identifier}. Expected EC2 instance ID (i-xxxxxxxxx) or private IP address",
            )

    return resolved_instance_ids


def terminate_instance(instance_id: str, aws_client: Any = None) -> dict[str, Any]:
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

        logger.info(
            "Terminated EC2 instance %s",
            instance_id,
            extra={"instance_id": instance_id},
        )

        return response

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            "Failed to terminate EC2 instance %s: %s - %s",
            instance_id,
            error_code,
            error_message,
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
            "Unexpected error terminating EC2 instance %s: %s",
            instance_id,
            str(e),
            extra={"instance_id": instance_id, "error": str(e)},
        )

        raise InfrastructureError(
            "AWS.EC2",
            f"Unexpected error terminating EC2 instance {instance_id}: {e!s}",
        )


# Helper functions with retry
@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _describe_instance(ec2_client: Any, instance_id: str) -> dict[str, Any]:
    """Describe an EC2 instance."""
    return ec2_client.describe_instances(InstanceIds=[instance_id])


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _describe_instances_by_private_ip(ec2_client: Any, private_ip: str) -> dict[str, Any]:
    """Describe EC2 instances by private IP address."""
    return ec2_client.describe_instances(
        Filters=[{"Name": "private-ip-address", "Values": [private_ip]}]
    )


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _run_instance(ec2_client: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run an EC2 instance."""
    return ec2_client.run_instances(**params)


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _terminate_instance(ec2_client: Any, instance_id: str) -> dict[str, Any]:
    """Terminate an EC2 instance."""
    return ec2_client.terminate_instances(InstanceIds=[instance_id])


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _create_tags(ec2_client: Any, instance_id: str, tags: list[dict[str, str]]) -> dict[str, Any]:
    """Create tags for an EC2 instance."""
    return ec2_client.create_tags(Resources=[instance_id], Tags=tags)
