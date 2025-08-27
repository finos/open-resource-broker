import boto3
import json
from typing import Any, Dict, List
from ..general.base_aws_handler import BaseAWSHandler
from ...models.provider.request import Request, RequestStatus
from ...models.provider.machine import Machine
from .ec2_fleet_model import EC2Fleet
from ...models.aws.ec2_instance import EC2Instance
from ...database.database_handler import DatabaseHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class EC2FleetHandler(BaseAWSHandler):
    """
    Handler for managing EC2 Fleets.
    """

    def __init__(self, region_name: str):
        """
        Initialize the EC2FleetHandler.

        :param region_name: The AWS region to use for EC2 operations.
        """
        self.ec2_client = boto3.client("ec2", region_name=region_name)

    def get_config(self, request: Request) -> Dict[str, Any]:
        """
        Generate the configuration for an EC2 Fleet based on the request.

        :param request: The request object containing details about the fleet to create.
        :return: A dictionary representing the fleet configuration.
        """
        logger.info(f"Generating fleet configuration for request {request.requestId}...")
        
        # Use EC2Fleet model to generate fleet configuration
        config = EC2Fleet.from_request(request).to_dict()
        
        logger.debug(f"Fleet configuration generated: {config}")
        return config

    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts by creating an EC2 Fleet.

        :param request: The request object containing details about the fleet to create.
        :return: The updated request object after acquiring hosts.
        """
        logger.info(f"Starting acquisition of hosts for request {request.requestId}...")
        
        try:
            self.validate_request(request)
            logger.info(f"Generating fleet configuration for request {request.requestId}...")
            
            # Use EC2Fleet model to generate fleet configuration
            config = EC2Fleet.from_request(request).to_dict()
            
            logger.debug(f"Fleet configuration: {json.dumps(config, indent=4)}")
            response = self.ec2_client.create_fleet(**config)
            
            request.resourceId = response["FleetId"]
            request.update_status(RequestStatus.RUNNING, "Fleet created successfully.")
            
            logger.info(f"EC2 Fleet {request.resourceId} created successfully.")
            return request

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to create EC2 Fleet: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to create EC2 Fleet: {str(e)}")
            return request

    def release_hosts(self, request: Request) -> Request:
        """
        Release hosts by deleting an EC2 Fleet.

        :param request: The request object containing details about the fleet to delete.
        :return: The updated request object after releasing hosts.
        """
        if not request.resourceId:
            logger.warning("No Fleet ID found in the request.")
            request.update_status(RequestStatus.FAILED, "No Fleet ID found in the request.")
            return request

        try:
            logger.info(f"Deleting EC2 Fleet {request.resourceId}...")
            self.ec2_client.delete_fleets(
                FleetIds=[request.resourceId],
                TerminateInstances=True
            )
            
            request.update_status(RequestStatus.COMPLETE, "Fleet deleted successfully.")
            logger.info(f"EC2 Fleet {request.resourceId} deleted successfully.")
            return request

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to delete EC2 Fleet: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to delete EC2 Fleet: {str(e)}")
            return request

    def check_request_status(self, request: Request) -> Request:
        """
        Check the status of an EC2 Fleet.

        :param request: The request object to check the status for.
        :return: The updated request object with the current status.
        """
        if not request.resourceId:
            logger.warning("No Fleet ID found in the request.")
            request.update_status(RequestStatus.FAILED, "No Fleet ID found in the request.")
            return request

        try:
            response = self.ec2_client.describe_fleets(FleetIds=[request.resourceId])
            
            if len(response["Fleets"]) == 0:
                logger.warning(f"Fleet {request.resourceId} not found.")
                request.update_status(RequestStatus.FAILED, f"Fleet {request.resourceId} not found.")
                return request

            fleet_data = response["Fleets"][0]
            
            # Use EC2Fleet model to parse fleet data
            fleet = EC2Fleet.from_describe_fleet(fleet_data)
            
            if fleet.fleetState == "active":
                logger.info(f"Fleet {request.resourceId} is active.")
                
                # Retrieve instances using centralized method in EC2Instance model
                instances = EC2Instance.get_instance_details(
                    [i["InstanceId"] for i in fleet_data.get("Instances", [])],
                    region_name=self.ec2_client.meta.region_name,
                )
                
                for instance in instances:
                    machine = Machine.from_ec2_instance(instance, request)
                    request.add_machine(machine)

                return request
            
            elif fleet.fleetState in ["deleted", "cancelled"]:
                logger.info(f"Fleet {request.resourceId} has been terminated.")
                request.update_status(RequestStatus.COMPLETE, "Fleet has been terminated.")
                return request
            
            else:
                logger.warning(f"Fleet {request.resourceId} is in state {fleet.fleetState}.")
                request.update_status(RequestStatus.COMPLETE_WITH_ERRORS, f"Fleet is in state {fleet.fleetState}.")
                return request

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to check EC2 Fleet status: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to check EC2 Fleet status: {str(e)}")
            return request

    def validate_request(self, request: Request) -> None:
        """
        Validate a given EC2 Fleet creation or modification request.

        :param request: The Request object to validate.
        :raises ValueError: If validation fails.
        """
        super().validate_request(request)
        
        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")
        
        if not (request.launchTemplateId and request.launchTemplateVersion):
            raise ValueError("Launch template ID and version are required for creating a fleet.")
