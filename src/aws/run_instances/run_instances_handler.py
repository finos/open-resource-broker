import boto3
from typing import Any, Dict, List
from ..general.base_aws_handler import BaseAWSHandler
from src.models.provider.request import Request, RequestStatus
from ...models.provider.machine import Machine, MachineStatus
from ...models.aws.ec2_instance import EC2Instance
from src.helpers.utils import paginate
from src.helpers.logger import setup_logging

logger = setup_logging()


class RunInstancesHandler(BaseAWSHandler):
    """
    Handler for managing EC2 instances launched directly via the RunInstances API, using Launch Templates.
    """

    def __init__(self, region_name: str):
        """
        Initializes the RunInstancesHandler.

        :param region_name: The AWS region to use for EC2 operations.
        """
        self.ec2_client = boto3.client("ec2", region_name=region_name)

    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts by launching EC2 instances using the RunInstances API, specifying a Launch Template.
        """
        try:
            self.validate_request(request)
            config = self.get_config(request)

            response = self.ec2_client.run_instances(**config)

            # Update the request status based on the outcome
            if "ReservationId" in response:
                reservation_id = response["ReservationId"]
                request.resourceId = reservation_id  # Store the reservation ID as resourceId
                request.update_status(
                    RequestStatus.RUNNING,
                    f"EC2 instances launched with reservation ID {reservation_id} using launch template {request.launchTemplateId}",
                )
                logger.info(f"EC2 instances launched with reservation ID: {reservation_id}")
            else:
                raise ValueError(f"No reservation ID found in the response: {response}")

            return request

        except Exception as e:
            logger.error(f"Failed to launch EC2 instances using launch template: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to launch EC2 instances: {str(e)}")
            return request

    def get_config(self, request: Request) -> Dict[str, Any]:
        """
        Generate the configuration for launching EC2 instances using RunInstances with Launch Template.

        :param request: The Request object containing details about the hosts to acquire.
        :return: A dictionary of configuration parameters for the run_instances API call.
        """
        logger.info(f"Generating configuration for RunInstances request {request.requestId}...")

        # Construct the configuration dictionary with LaunchTemplate specification
        config = {
            "LaunchTemplate": {
                "LaunchTemplateId": request.launchTemplateId,
                "Version": "$Default",  # You can customize the version, e.g., "$Latest"
            },
            "MinCount": request.numRequested,
            "MaxCount": request.numRequested,
        }

        # Log the configuration that will be used
        logger.debug(f"RunInstances configuration generated: {config}")

        return config

    def release_hosts(self, machines: List[Machine]) -> None:
        """
        Release hosts by terminating EC2 instances.

        :param machines: A list of Machine objects representing the instances to terminate.
        """
        if not machines:
            logger.warning("No machines provided to terminate.")
            return

        try:
            # Extract instance IDs from the provided machines
            instance_ids = [machine.machineId for machine in machines if machine.machineId]
            if not instance_ids:
                logger.warning("No valid instance IDs found in the provided machines.")
                return

            logger.info(f"Terminating EC2 instances: {instance_ids}")
            self.ec2_client.terminate_instances(InstanceIds=instance_ids)
            logger.info(f"Termination initiated for instances: {instance_ids}")

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to terminate EC2 instances: {e}")

    def check_request_status(self, request: Request) -> List[Machine]:
        """
        Check the status of EC2 instances launched via RunInstances API.

        :param request: The Request object representing an acquire operation.
        :return: A list of Machine objects representing the current state of each instance.
        """
        if not request.resourceId:
            logger.warning("No Reservation ID found in the request.")
            raise ValueError("No Reservation ID found in the request.")

        try:
            # Fetch reservations from AWS
            reservations = paginate(
                client_method=self.ec2_client.describe_instances,
                result_key="Reservations",
                Filters=[{'Name': 'reservation-id', 'Values': [request.resourceId]}]
            )

            machines = []

            for reservation in reservations:
                for instance_data in reservation.get("Instances", []):
                    instance = EC2Instance.from_describe_instance(instance_data)
                    machine = Machine.from_ec2_instance(instance, request)
                    machines.append(machine)

                    logger.info(f"Fetched machine {machine.machineId} with status {machine.status.value}.")

            return machines

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to check EC2 instance statuses: {e}")
            raise ValueError(f"Failed to check EC2 instance statuses: {str(e)}")

    def validate_request(self, request: Request) -> None:
        """
        Validate a given RunInstances creation or modification request.
        """
        super().validate_request(request)

        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")

        if not (request.launchTemplateId):
            raise ValueError("Launch template ID is required for launching EC2 instances.")
