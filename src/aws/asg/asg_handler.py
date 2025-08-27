import boto3
from typing import Any, Dict, List
from ..general.base_aws_handler import BaseAWSHandler
from ...models.provider.request import Request, RequestStatus
from ...models.provider.machine import Machine
from .asg_model import AutoScalingGroup
from ...models.aws.ec2_instance import EC2Instance
from src.helpers.logger import setup_logging

logger = setup_logging()


class ASGHandler(BaseAWSHandler):
    """
    Handler for managing Auto Scaling Groups (ASGs).
    """

    def __init__(self, region_name: str):
        self.autoscaling_client = boto3.client("autoscaling", region_name=region_name)
        self.ec2_client = boto3.client("ec2", region_name=region_name)

    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts by creating or updating an Auto Scaling Group.
        """
        try:
            self.validate_request(request)
            config = self.get_config(request)

            self.autoscaling_client.create_auto_scaling_group(**config)
            request.resourceId = config["AutoScalingGroupName"]
            request.update_status(RequestStatus.RUNNING, "Auto Scaling Group creation initiated.")

            logger.info(f"Auto Scaling Group creation initiated for request {request.requestId}.")
            return request

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to create Auto Scaling Group: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to create Auto Scaling Group: {str(e)}")
            return request

    def release_hosts(self, request: Request) -> Request:
        """
        Release hosts by deleting an Auto Scaling Group.
        """
        if not request.resourceId:
            logger.warning("No Auto Scaling Group name found in the request.")
            request.update_status(RequestStatus.FAILED, "No Auto Scaling Group name found in the request.")
            return request

        try:
            logger.info(f"Deleting Auto Scaling Group {request.resourceId}...")
            self.autoscaling_client.delete_auto_scaling_group(
                AutoScalingGroupName=request.resourceId,
                ForceDelete=True
            )

            request.update_status(RequestStatus.COMPLETE, "Auto Scaling Group deletion initiated.")
            logger.info(f"Auto Scaling Group deletion initiated for request {request.requestId}.")
            return request

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to delete Auto Scaling Group: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to delete Auto Scaling Group: {str(e)}")
            return request

    def check_request_status(self, request: Request) -> Request:
        """
        Check the status of an Auto Scaling Group.
        """
        if not request.resourceId:
            logger.warning("No Auto Scaling Group name found in the request.")
            request.update_status(RequestStatus.FAILED, "No Auto Scaling Group name found in the request.")
            return request

        try:
            paginator = self.autoscaling_client.get_paginator("describe_auto_scaling_groups")
            asg_data = None

            for page in paginator.paginate(AutoScalingGroupNames=[request.resourceId]):
                if len(page["AutoScalingGroups"]) > 0:
                    asg_data = page["AutoScalingGroups"][0]
                    break

            if not asg_data:
                logger.warning(f"Auto Scaling Group {request.resourceId} not found.")
                request.update_status(RequestStatus.FAILED, f"Auto Scaling Group {request.resourceId} not found.")
                return request

            asg = AutoScalingGroup.from_describe_auto_scaling_group(asg_data)
            instances = EC2Instance.get_instance_details([i["InstanceId"] for i in asg_data.get("Instances", [])], self.ec2_client.meta.region_name)

            for instance in instances:
                machine = Machine.from_ec2_instance(instance, request)
                request.add_machine(machine)

            if len(instances) >= asg.desiredCapacity:
                request.update_status(RequestStatus.COMPLETE, "Desired capacity reached.")
            else:
                request.update_status(RequestStatus.RUNNING, "Auto Scaling Group is scaling.")

            return request

        except boto3.exceptions.Boto3Error as e:
            logger.error(f"Failed to check Auto Scaling Group status: {e}")
            request.update_status(RequestStatus.FAILED, f"Failed to check Auto Scaling Group status: {str(e)}")
            return request

    def get_config(self, request: Request) -> Dict[str, Any]:
        """
        Generate the configuration for creating or updating an Auto Scaling Group.
        """
        logger.info(f"Generating configuration for Auto Scaling Group request {request.requestId}...")

        config = AutoScalingGroup.from_request(request).to_dict()
        
        logger.debug(f"Auto Scaling Group configuration generated: {config}")
        return config

    def validate_request(self, request: Request) -> None:
        """
        Validate a given Auto Scaling Group creation or modification request.
        """
        super().validate_request(request)

        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")

        if not (request.launchTemplateId and request.launchTemplateVersion):
            raise ValueError("Launch template ID and version are required for creating an Auto Scaling Group.")
