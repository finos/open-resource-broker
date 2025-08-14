"""AWS utility functions."""

from typing import Any, Callable, Dict, List

from botocore.exceptions import ClientError

from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def paginate(client_method: Callable, result_key: str, **kwargs) -> List[Dict[str, Any]]:
    """
    Handle paginated responses from Boto3 client methods.

    :param client_method: The Boto3 client method to call (e.g., ec2_client.describe_instances).
    :param result_key: The key in the response that contains the desired results (e.g., "Reservations").
    :param kwargs: Arguments to pass to the client method.
    :return: A list of items from all pages of the response.
    """
    paginator = client_method.__self__.get_paginator(client_method.__name__)
    results = []

    try:
        for page in paginator.paginate(**kwargs):
            results.extend(page.get(result_key, []))
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"Failed to paginate {client_method.__name__}: {error_code}")
        raise RuntimeError(
            f"Failed to paginate {client_method.__name__}: {error_code}") from e

    return results


def list_all_instances(ec2_client, filters=None) -> List[Dict[str, Any]]:
    """
    List all EC2 instances with pagination.

    Args:
        ec2_client: EC2 client
        filters: Optional filters

    Returns:
        List of instances
    """
    reservations = paginate(ec2_client.describe_instances,
                            "Reservations", Filters=filters or [])

    instances = []
    for reservation in reservations:
        instances.extend(reservation.get("Instances", []))

    return instances


def list_all_subnets(ec2_client, filters=None) -> List[Dict[str, Any]]:
    """
    List all subnets with pagination.

    Args:
        ec2_client: EC2 client
        filters: Optional filters

    Returns:
        List of subnets
    """
    return paginate(ec2_client.describe_subnets, "Subnets", Filters=filters or [])


def list_all_security_groups(ec2_client, filters=None) -> List[Dict[str, Any]]:
    """
    List all security groups with pagination.

    Args:
        ec2_client: EC2 client
        filters: Optional filters

    Returns:
        List of security groups
    """
    return paginate(ec2_client.describe_security_groups, "SecurityGroups", Filters=filters or [])
