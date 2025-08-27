from dataclasses import fields
import boto3
import os
import json
from botocore.exceptions import ClientError
from typing import Callable, Dict, Any, List, Tuple
from dataclasses import fields, is_dataclass
from enum import Enum
from src.helpers.logger import setup_logging

logger = setup_logging()

def map_known_and_additional_fields(data_class, raw_data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Map known fields from a dataclass and capture additional fields dynamically.

    :param data_class: The dataclass to map fields to.
    :param raw_data: The raw input data (e.g., API response).
    :return: A tuple containing two dictionaries:
             - Known fields mapped to their values.
             - Additional properties not explicitly defined in the dataclass.
    """
    known_fields = {field.name for field in data_class.__dataclass_fields__.values()}
    known_data = {k: v for k, v in raw_data.items() if k in known_fields}
    additional_properties = {k: v for k, v in raw_data.items() if k not in known_fields}
    
    return known_data, additional_properties

def serialize_to_dict(obj: Any) -> Dict[str, Any]:
    """
    Serialize a dataclass object to a dictionary, including additional options.

    :param obj: The dataclass object to serialize.
    :return: A dictionary representation of the object.
    """
    result = {}

    # Serialize fields defined in the dataclass
    for field in fields(obj):
        value = getattr(obj, field.name)

        # Skip None values
        if value is None:
            continue

        # Handle enums
        if isinstance(value, Enum):
            result[field.name] = value.value

        # Handle nested dataclasses
        elif is_dataclass(value):
            result[field.name] = serialize_to_dict(value)

        # Handle lists of nested objects
        elif isinstance(value, list):
            result[field.name] = [
                serialize_to_dict(item) if is_dataclass(item) else item for item in value
            ]

        # Handle dictionaries with nested objects
        elif isinstance(value, dict):
            result[field.name] = {
                key: serialize_to_dict(val) if is_dataclass(val) else val for key, val in value.items()
            }

        # Primitive types (int, float, str, bool)
        else:
            result[field.name] = value

    # Include additional options if present
    if hasattr(obj, "additional_options") and isinstance(obj.additional_options, dict):
        result.update(obj.additional_options)

    return result

def paginate(client_method: Callable, result_key: str, **kwargs) -> List[Dict[str, Any]]:
    """
    Utility function to handle paginated responses from Boto3 client methods.

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
        raise RuntimeError(f"Failed to paginate {client_method.__name__}: {error_code}") from e

    return results

def list_subnets() -> List[Dict[str, Any]]:
    """
    List all available subnets in the AWS account using pagination.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing subnet details.
    """
    ec2 = boto3.client('ec2')
    try:
        subnets = paginate(ec2.describe_subnets, 'Subnets')
        return [
            {
                'SubnetId': subnet['SubnetId'],
                'VpcId': subnet['VpcId'],
                'AvailabilityZone': subnet['AvailabilityZone']
            }
            for subnet in subnets
        ]
    except ClientError as e:
        print(f"Error listing subnets: {e}")
        return []

def list_security_groups() -> List[Dict[str, Any]]:
    """
    List all available security groups in the AWS account using pagination.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing security group details.
    """
    ec2 = boto3.client('ec2')
    try:
        security_groups = paginate(ec2.describe_security_groups, 'SecurityGroups')
        return [
            {
                'GroupId': sg['GroupId'],
                'GroupName': sg.get('GroupName', ''),
                'VpcId': sg.get('VpcId', None)
            }
            for sg in security_groups
        ]
    except ClientError as e:
        print(f"Error listing security groups: {e}")
        return []

def check_aws_key_pair_exists(key_name: str) -> bool:
    """
    Check if a key pair exists on AWS.

    Args:
        key_name (str): The name of the key pair to check.

    Returns:
        bool: True if the key pair exists, False otherwise.
    """
    ec2 = boto3.client('ec2')
    try:
        ec2.describe_key_pairs(KeyNames=[key_name])
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
            return False
        else:
            raise

def create_aws_key_pair(key_path: str, key_name: str) -> None:
    """
    Create a new key pair on AWS and save the private key locally.

    Args:
        key_path (str): The path to save the private key file.
        key_name (str): The name of the AWS Key Pair.
    
    Raises:
        Exception: If there is an error creating the key pair on AWS.
    """
    ec2 = boto3.client('ec2')
    try:
        response = ec2.create_key_pair(KeyName=key_name)
        
        # Save private key to file
        with open(key_path, 'w') as file:
            file.write(response['KeyMaterial'])
        
        os.chmod(key_path, 0o600)
        print(f"Key pair '{key_name}' created successfully and saved to '{key_path}'.")
    
    except ClientError as e:
        print(f"Error creating AWS Key Pair '{key_name}': {e}")
        raise

def ensure_directory_exists(path: str) -> None:
    """
    Ensure that a directory exists. If it does not exist, create it.

    Args:
        path (str): The directory path to check or create.
    """
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        print(f"Created directory: {path}")

def load_json_data(json_str: str = None, json_file: str = None) -> Any:
    """
    Load JSON data from a string or file.

    Args:
        json_str (str): JSON string input.
        json_file (str): Path to a JSON file.

    Returns:
        Any: Parsed JSON data as a Python object.
    
    Raises:
        ValueError: If neither `json_str` nor `json_file` is provided.
    """
    if json_str:
        return json.loads(json_str)
    
    if json_file:
        with open(json_file, 'r') as f:
            return json.load(f)
    
    raise ValueError("Either `json_str` or `json_file` must be provided.")

def setup_directories() -> None:
    """
    Set up required directories based on environment variables.
    
    Ensures that all required directories (config, workdir, logs) exist.
    """
    provider_name = os.environ.get("HFPROVIDERNAME", "default_provider")
    
    conf_dir = os.environ.get("HFPROVIDERCONFDIR", f"./{provider_name}/config")
    work_dir = os.environ.get("HFPROVIDERWORKDIR", f"./{provider_name}/workdir")
    log_dir = os.environ.get("HFPROVIDERLOGDIR", f"./{provider_name}/logs")
    
    for dir_path in [conf_dir, work_dir, log_dir]:
        ensure_directory_exists(dir_path)

def resolve_ssm_parameter(ssm_client, parameter_path: str) -> str:
    """
    Resolves an AWS SSM parameter to its actual value.
    :param ssm_client: Boto3 SSM client.
    :param parameter_path: The path to the SSM parameter, enclosed in double curly braces (e.g., "{{ssm:/path/to/parameter}}").
    :return: The value of the SSM parameter.
    """
    # Extract the parameter path from the input string
    start_index = parameter_path.find("{{ssm:") + 6
    end_index = parameter_path.find("}}")
    if start_index == -1 or end_index == -1:
        raise ValueError("Invalid SSM parameter path format. Must be in the format '{{ssm:/path/to/parameter}}'.")

    path = parameter_path[start_index:end_index]
    logger.info(f"Attempting to retrieve SSM parameter value from path: {path}")
    try:
        response = ssm_client.get_parameter(Name=path, WithDecryption=True)
        parameter_value = response["Parameter"]["Value"]
        logger.info(f"Successfully retrieved SSM parameter value from path: {path}")
        return parameter_value
    except Exception as e:
        logger.error(f"Failed to retrieve SSM parameter from path: {path}. Error: {e}")
        raise

def handle_long_flag(obj: Any, long: bool) -> Any:
    """
    Utility function to handle the --long flag for API responses.

    :param obj: The object to format.
    :param long: Whether to include all fields in the response.
    :return: The formatted object based on the --long flag.
    """
    if long:
        if hasattr(obj, '__dict__'):
            return {k: handle_long_flag(v, long) for k, v in obj.__dict__.items()}
        elif isinstance(obj, list):
            return [handle_long_flag(item, long) for item in obj]
        elif isinstance(obj, dict):
            return {k: handle_long_flag(v, long) for k, v in obj.items()}
    return obj