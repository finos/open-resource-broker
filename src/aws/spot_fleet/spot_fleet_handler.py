import boto3
from typing import Any, Dict, List
from ..general.base_aws_handler import BaseAWSHandler
from ...models.provider.request import Request, RequestStatus
from ...models.provider.machine import Machine
from .spot_fleet_model import SpotFleet
from ...models.aws.ec2_instance import EC2Instance
from src.helpers.logger import setup_logging

logger = setup_logging()


class SpotFleetHandler(BaseAWSHandler):
    """
    Handler for managing Spot Fleets.
    """

    def __init__(self, region_name: str):
        """
        Initialize the SpotFleetHandler.

        :param region_name: The AWS region to use for EC2 operations.
        """
        self.ec2_client = boto3.client("ec2", region_name=region_name)

    def get_config(self, request: Request) -> Dict[str, Any]:
        """
        Generate the configuration for a Spot Fleet based on the request.

        :param request: The request object containing details about the fleet to create.
        :return: A dictionary representing the fleet configuration.
        """
        logger.info(f"Generating Spot Fleet configuration for request {request.requestId}...")

        config = SpotFleet.from_request(request).to_dict()

        logger.debug(f"Spot Fleet configuration generated: {config}")
        return config

    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts by creating a Spot Fleet request.

        :param request: The request object containing details about the fleet to create.
        :return: The updated request object after acquiring hosts.
        """
        logger.info(f"Starting acquisition of hosts for request {request.requestId}...")

        try:
            self.validate_request(request)

            config = self.get_config(request)
            response = self.ec2_client.request_spot_fleet(SpotFleetRequestConfig=config)

            request.resourceId = response["SpotFleetRequestId"]
            request.update_status(RequestStatus.RUNNING, "Spot Fleet creation initiated.")

            logger.info(f"Spot Fleet creation initiated for request {request.requestId}.")
            return request

        except Exception as e:
            logger.error(f"Failed to create Spot Fleet: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to create Spot Fleet: {str(e)}")
            return request

    def release_hosts(self, request: Request) -> Request:
        """
        Release hosts by canceling a Spot Fleet request.

        :param request: The request object containing details about the fleet to cancel.
        :return: The updated request object after releasing hosts.
        """
        if not request.resourceId:
            logger.warning("No Spot Fleet Request ID found in the request.")
            request.update_status(RequestStatus.FAILED, "No Spot Fleet Request ID found in the request.")
            return request

        try:
            logger.info(f"Cancelling Spot Fleet {request.resourceId}...")
            self.ec2_client.cancel_spot_fleet_requests(
                SpotFleetRequestIds=[request.resourceId],
                TerminateInstances=True
            )

            request.update_status(RequestStatus.COMPLETE, "Spot Fleet cancellation initiated.")
            logger.info(f"Spot Fleet cancellation initiated for request {request.requestId}.")
            return request

        except Exception as e:
            logger.error(f"Failed to cancel Spot Fleet: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to cancel Spot Fleet: {str(e)}")
            return request

    def check_request_status(self, request: Request) -> Request:
        """
        Check the status of a Spot Fleet request.

        :param request: The request object to check the status for.
        :return: The updated request object with the current status.
        """
        if not request.resourceId:
            logger.warning("No Spot Fleet Request ID found in the request.")
            request.update_status(RequestStatus.FAILED, "No Spot Fleet Request ID found in the request.")
            return request

        try:
            paginator = self.ec2_client.get_paginator("describe_spot_fleet_requests")

            fleet_data = None
            for page in paginator.paginate(SpotFleetRequestIds=[request.resourceId]):
                if len(page["SpotFleetRequestConfigs"]) > 0:
                    fleet_data = page["SpotFleetRequestConfigs"][0]
                    break

            if not fleet_data:
                logger.warning(f"Spot Fleet Request {request.resourceId} not found.")
                request.update_status(RequestStatus.FAILED, f"Spot Fleet Request {request.resourceId} not found.")
                return request

            spot_fleet = SpotFleet.from_describe_spot_fleet_request(fleet_data)

            if spot_fleet.spotFleetRequestState == "active":
                logger.info(f"Spot Fleet {request.resourceId} is active.")

                instance_ids = [i["InstanceId"] for i in fleet_data.get("ActiveInstances", [])]
                instances = EC2Instance.get_instance_details(instance_ids, self.ec2_client.meta.region_name)

                for instance in instances:
                    machine = Machine.from_ec2_instance(instance, request)
                    request.add_machine(machine)

                return request

            elif spot_fleet.spotFleetRequestState in ["cancelled", "terminated"]:
                logger.info(f"Spot Fleet {request.resourceId} has been terminated.")
                request.update_status(RequestStatus.COMPLETE, "Spot Fleet has been terminated.")
                return request

            else:
                logger.warning(f"Spot Fleet {request.resourceId} is in state {spot_fleet.spotFleetRequestState}.")
                request.update_status(RequestStatus.COMPLETE_WITH_ERRORS,
                                      f"Spot Fleet is in state {spot_fleet.spotFleetRequestState}.")
                return request

        except Exception as e:
            logger.error(f"Failed to check Spot Fleet status: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to check Spot Fleet status: {str(e)}")
            return request

    def validate_request(self, request: Request) -> None:
        """
        Validate a given Spot Fleet creation or modification request.

        :param request: The Request object to validate.
        :raises ValueError: If validation fails.
        """
        super().validate_request(request)

        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")
