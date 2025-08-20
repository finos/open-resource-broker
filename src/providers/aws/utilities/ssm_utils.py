"""
AWS SSM utility functions for the AWS Host Factory Plugin.

This module contains utility functions for working with AWS SSM Parameter Store.
"""

import re
from typing import Any, Dict, List, Optional, Union

from botocore.exceptions import ClientError

from domain.base.exceptions import InfrastructureError
from infrastructure.logging.logger import get_logger
from infrastructure.resilience import retry

# Logger
logger = get_logger(__name__)

# SSM parameter path pattern
SSM_PARAMETER_PATTERN = r"^ssm:(/[\w\-./]+)$"


def is_ssm_parameter_path(value: str) -> bool:
    """
    Check if a string is an SSM parameter path.

    Args:
        value: String to check

    Returns:
        True if string is an SSM parameter path, False otherwise
    """
    if not value:
        return False

    return bool(re.match(SSM_PARAMETER_PATTERN, value))


def extract_ssm_parameter_path(value: str) -> Optional[str]:
    """
    Extract the SSM parameter path from a string.

    Args:
        value: String containing SSM parameter path

    Returns:
        SSM parameter path or None if not found
    """
    if not value:
        return None

    match = re.match(SSM_PARAMETER_PATTERN, value)
    if match:
        return match.group(1)

    return None


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ssm")
def _get_ssm_parameter(ssm_client: Any, parameter_path: str) -> str:
    """Get SSM parameter with retry."""
    response = ssm_client.get_parameter(Name=parameter_path, WithDecryption=True)
    return response["Parameter"]["Value"]


def resolve_ssm_parameter(parameter_path: str, aws_client: Any = None) -> str:
    """
    Resolve an SSM parameter path to its value.

    Args:
        parameter_path: SSM parameter path
        aws_client: AWS client to use

    Returns:
        Parameter value

    Raises:
        InfrastructureError: If parameter cannot be resolved
    """
    # Extract parameter path if it's in the format "ssm:/path/to/parameter"
    path = extract_ssm_parameter_path(parameter_path)
    if not path:
        path = parameter_path

    try:
        # Require AWSClient for consistent configuration
        if not aws_client:
            raise ValueError("AWSClient is required for SSM operations")
        ssm_client = aws_client.ssm_client

        # Use retry-enabled helper function
        return _get_ssm_parameter(ssm_client, path)

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            "Failed to resolve SSM parameter %s: %s - %s",
            path,
            error_code,
            error_message,
            extra={
                "parameter_path": path,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

        raise InfrastructureError(
            "AWS.SSM",
            f"Failed to resolve SSM parameter {path}: {error_code} - {error_message}",
        )

    except Exception as e:
        logger.error(
            "Unexpected error resolving SSM parameter %s: %s",
            path,
            str(e),
            extra={"parameter_path": path, "error": str(e)},
        )

        raise InfrastructureError(
            "AWS.SSM", f"Unexpected error resolving SSM parameter {path}: {str(e)}"
        )


def _get_ssm_parameter_value(ssm_client: Any, parameter_path: str) -> str:
    """
    Get SSM parameter value.

    Args:
        ssm_client: SSM client
        parameter_path: Parameter path

    Returns:
        Parameter value
    """
    response = ssm_client.get_parameter(Name=parameter_path, WithDecryption=True)

    return response["Parameter"]["Value"]


def resolve_ssm_parameters_in_dict(
    data: Dict[str, Any],
    aws_client: Any = None,
) -> Dict[str, Any]:
    """
    Resolve all SSM parameters in a dictionary.

    Args:
        data: Dictionary to process
        aws_client: AWS client to use


    Returns:
        Dictionary with resolved SSM parameters
    """
    result = {}

    for key, value in data.items():
        if isinstance(value, str) and is_ssm_parameter_path(value):
            result[key] = resolve_ssm_parameter(value, aws_client)
        elif isinstance(value, dict):
            result[key] = resolve_ssm_parameters_in_dict(value, aws_client)
        elif isinstance(value, list):
            result[key] = resolve_ssm_parameters_in_list(value, aws_client)
        else:
            result[key] = value

    return result


def resolve_ssm_parameters_in_list(
    data: List[Any],
    aws_client: Any = None,
) -> List[Any]:
    """
    Resolve all SSM parameters in a list.

    Args:
        data: List to process
        aws_client: AWS client to use


    Returns:
        List with resolved SSM parameters
    """
    result = []

    for item in data:
        if isinstance(item, str) and is_ssm_parameter_path(item):
            result.append(resolve_ssm_parameter(item, aws_client))
        elif isinstance(item, dict):
            result.append(resolve_ssm_parameters_in_dict(item, aws_client))
        elif isinstance(item, list):
            result.append(resolve_ssm_parameters_in_list(item, aws_client))
        else:
            result.append(item)

    return result


def resolve_ssm_parameters(
    data: Union[Dict[str, Any], List[Any]],
    aws_client: Any = None,
) -> Union[Dict[str, Any], List[Any]]:
    """
    Resolve all SSM parameters in a dictionary or list.

    Args:
        data: Dictionary or list to process
        aws_client: AWS client to use


    Returns:
        Dictionary or list with resolved SSM parameters
    """
    if isinstance(data, dict):
        return resolve_ssm_parameters_in_dict(data, aws_client)
    elif isinstance(data, list):
        return resolve_ssm_parameters_in_list(data, aws_client)
    else:
        return data


def get_ssm_parameters_by_path(
    path: str,
    recursive: bool = True,
    aws_client: Any = None,
) -> Dict[str, str]:
    """
    Get all SSM parameters under a path.

    Args:
        path: Path to get parameters from
        recursive: Whether to get parameters recursively
        aws_client: AWS client to use


    Returns:
        Dictionary of parameter names to values

    Raises:
        InfrastructureError: If parameters cannot be retrieved
    """
    try:
        # Require AWSClient for consistent configuration
        if not aws_client:
            raise ValueError("AWSClient is required for SSM operations")
        ssm_client = aws_client.ssm_client

        # Use retry-enabled helper function
        return _get_parameters_by_path(ssm_client, path, recursive)

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.error(
            "Failed to get SSM parameters by path %s: %s - %s",
            path,
            error_code,
            error_message,
            extra={
                "path": path,
                "error_code": error_code,
                "error_message": error_message,
            },
        )

        raise InfrastructureError(
            "AWS.SSM",
            f"Failed to get SSM parameters by path {path}: {error_code} - {error_message}",
        )


@retry(strategy="exponential", max_attempts=3, base_delay=1.0, service="ssm")
def _get_parameters_by_path(ssm_client: Any, path: str, recursive: bool = True) -> Dict[str, str]:
    """Get SSM parameters by path with retry."""
    paginator = ssm_client.get_paginator("get_parameters_by_path")
    parameters = {}

    for page in paginator.paginate(Path=path, Recursive=recursive, WithDecryption=True):
        for param in page["Parameters"]:
            parameters[param["Name"]] = param["Value"]

    return parameters
