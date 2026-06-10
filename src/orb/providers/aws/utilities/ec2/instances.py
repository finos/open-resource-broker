"""EC2 instance management utility functions."""

import threading
from typing import Any, Optional

from botocore.exceptions import ClientError

from orb.domain.base.exceptions import InfrastructureError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.resilience import retry

# Logger
logger = get_logger(__name__)


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


# Instance type spec lookup — API cache with heuristic fallback

_instance_spec_cache: dict[str, tuple[int, int]] | None = None  # {type: (vcpus, memory_mib)}
_cache_lock = threading.Lock()

_SIZE_TO_VCPUS: dict[str, int] = {
    "nano": 2,
    "micro": 2,
    "small": 2,
    "medium": 2,
    "large": 2,
    "xlarge": 4,
    "2xlarge": 8,
    "4xlarge": 16,
    "8xlarge": 32,
    "9xlarge": 36,
    "12xlarge": 48,
    "16xlarge": 64,
    "18xlarge": 72,
    "24xlarge": 96,
    "32xlarge": 128,
    "48xlarge": 192,
    "56xlarge": 224,
    "96xlarge": 384,
    "112xlarge": 448,
    "metal": 96,
}

# Family letter → GiB per vCPU
_FAMILY_MEM_RATIO: dict[str, int] = {
    "c": 2,  # compute-optimized
    "m": 4,  # general purpose
    "r": 8,  # memory-optimized
    "i": 8,  # storage-optimized
    "d": 8,  # dense storage
    "z": 8,  # high frequency
    "x": 16,  # extreme memory
    "p": 12,  # GPU/ML training
    "g": 4,  # graphics/inference
}

# t-family special cases (burstable — ratio varies by size and generation)
_T2_SPECS: dict[str, tuple[int, int]] = {
    "nano": (1, 512),
    "micro": (1, 1024),
    "small": (1, 2048),
    "medium": (2, 4096),
    "large": (2, 8192),
    "xlarge": (4, 16384),
    "2xlarge": (8, 32768),
}
_T3_SPECS: dict[str, tuple[int, int]] = {
    "nano": (2, 512),
    "micro": (2, 1024),
    "small": (2, 2048),
    "medium": (2, 4096),
    "large": (2, 8192),
    "xlarge": (4, 16384),
    "2xlarge": (8, 32768),
}
_T_FAMILY_SPECS: dict[str, dict[str, tuple[int, int]]] = {
    "t2": _T2_SPECS,
    "t3": _T3_SPECS,
    "t3a": _T3_SPECS,
    "t4g": _T3_SPECS,
}


def _load_instance_specs(ec2_client: Any) -> dict[str, tuple[int, int]]:
    """Load all instance type specs from EC2 API."""
    specs: dict[str, tuple[int, int]] = {}
    try:
        paginator = ec2_client.get_paginator("describe_instance_types")
        for page in paginator.paginate():
            for itype in page["InstanceTypes"]:
                specs[itype["InstanceType"]] = (
                    itype["VCpuInfo"]["DefaultVCpus"],
                    itype["MemoryInfo"]["SizeInMiB"],
                )
    except Exception as exc:
        logger.warning(
            "Failed to load instance type specs from EC2 API, falling back to heuristic: %s",
            exc,
        )
    return specs


def _heuristic_cpu_ram(instance_type: str) -> tuple[str, str]:
    """Estimate vCPU/RAM from instance type string using family and size patterns."""
    try:
        family_gen, size = instance_type.split(".", 1)
    except ValueError:
        return ("1", "1024")  # Unparseable — safe default

    family = family_gen[0].lower()

    # t-family: special cases (ratio varies by size and generation)
    if family == "t":
        gen_specs = _T_FAMILY_SPECS.get(family_gen)
        if gen_specs and size in gen_specs:
            vcpus, mem_mib = gen_specs[size]
            return (str(vcpus), str(mem_mib))
        # Unknown t-generation — fall through to standard heuristic

    # Standard families
    vcpus = _SIZE_TO_VCPUS.get(size, 2)
    gib_per_vcpu = _FAMILY_MEM_RATIO.get(family, 4)
    mem_mib = vcpus * gib_per_vcpu * 1024

    return (str(vcpus), str(mem_mib))


def derive_cpu_ram_from_instance_type(
    instance_type: str,
    ec2_client: Any | None = None,
) -> tuple[str, str]:
    """Derive vCPU count and RAM (MiB) for an EC2 instance type.

    Primary: looks up from ec2.describe_instance_types() cache.
    Fallback: heuristic based on instance family and size.

    Returns: (vcpus_str, ram_mib_str)
    """
    global _instance_spec_cache

    # Try API cache first
    if ec2_client is not None and _instance_spec_cache is None:
        with _cache_lock:
            if _instance_spec_cache is None:
                _instance_spec_cache = _load_instance_specs(ec2_client)

    if _instance_spec_cache and instance_type in _instance_spec_cache:
        vcpus, mem_mib = _instance_spec_cache[instance_type]
        return (str(vcpus), str(mem_mib))

    # Heuristic fallback
    return _heuristic_cpu_ram(instance_type)


# Helper functions with retry
@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ec2")
def _describe_instance(ec2_client: Any, instance_id: str) -> dict[str, Any]:
    """Describe an EC2 instance."""
    return ec2_client.describe_instances(InstanceIds=[instance_id])


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
