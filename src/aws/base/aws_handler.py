import boto3
from typing import Dict, Any, Optional, List
from ..launch_template.launch_template_handler import LaunchTemplateHandler
from ..iam_role.iam_role_handler import IAMRoleHandler
from ..ec2_fleet.ec2_fleet_handler import EC2FleetHandler
from ..spot_fleet.spot_fleet_handler import SpotFleetHandler
from ..asg.asg_handler import ASGHandler
from ..run_insstances.run_instances_handler import RunInstancesHandler
from ...models.provider.request import Request
from ...config.provider_config_manager import ProviderConfigManager
from ...config.provider_template_manager import ProviderTemplateManager
from src.helpers.logger import setup_logging

logger = setup_logging()


class AWSHandler:
    """
    Main handler for AWS operations. This class serves as an entry point for all AWS-related
    operations and coordinates between different service-specific handlers.
    """

    def __init__(self):
        """
        Initialize the AWSHandler with specific handlers for each AWS service and a configuration object.
        """
        config = ProviderConfigManager.get_config()
        region = config.AWS_REGION

        # Initialize shared dependencies
        template_manager = ProviderTemplateManager()

        # Initialize specific handlers for each AWS service
        self.handlers: Dict[str, Any] = {
            "EC2Fleet": EC2FleetHandler(region),
            "SpotFleet": SpotFleetHandler(region),
            "ASG": ASGHandler(region),
            "RunInstances": RunInstancesHandler(region),
        }
        self.iam_role_handler = IAMRoleHandler(region)
        self.launch_template_handler = LaunchTemplateHandler(region)
        self.ec2_client = boto3.client("ec2", region_name=region)

    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts based on the request type.

        :param request: The request object containing details about the hosts to acquire.
        :return: The updated request object after acquiring hosts.
        """
        # Step 1: Validate the request
        self.validate_request(request)

        # Step 2: Get the provider template
        template_manager = ProviderTemplateManager()
        provider_template = template_manager.get_template(request.templateId)

        # Step 3: Construct the Launch Template data from the provider template
        launch_template_data = self.launch_template_handler._construct_launch_template_data(provider_template)

        # Step 4: Apply request-specific overrides (e.g., tags) to the Launch Template data
        tag_specifications = self._create_tag_specifications(request)
        if tag_specifications:
            launch_template_data["TagSpecifications"] = tag_specifications

        # Step 5: Create a per-request Launch Template using LaunchTemplateHandler
        launch_template = self.launch_template_handler.create_launch_template(
            template_id=request.templateId,
            request_id=request.requestId,
            launch_template_data=launch_template_data,
        )

        # Step 6: Update the request with launch template details
        request.launchTemplateId = launch_template["LaunchTemplateId"]
        request.launchTemplateVersion = launch_template["LatestVersionNumber"]

        # Step 7: Delegate to the appropriate handler to acquire hosts
        handler = self._get_handler(request.awsHandler)
        return handler.acquire_hosts(request)

    def release_hosts(self, request: Request) -> Request:
        """
        Release hosts based on the request type.

        :param request: The request object containing details about the hosts to release.
        :return: The updated request object after releasing hosts.
        """
        handler = self._get_handler(request.awsHandler)
        return handler.release_hosts(request)

    def check_request_status(self, request: Request) -> Request:
        """
        Check the status of a request based on its type.

        :param request: The request object to check the status for.
        :return: The updated request object with the current status.
        """
        handler = self._get_handler(request.awsHandler)
        return handler.check_request_status(request)

    def _get_handler(self, handler_type: str) -> Any:
        """
        Get the appropriate handler based on the AWS handler type.

        :param handler_type: The type of AWS handler to retrieve.
        :return: The corresponding AWS handler.
        :raises ValueError: If an unsupported handler type is provided.
        """
        handler = self.handlers.get(handler_type)
        if not handler:
            raise ValueError(f"Unsupported AWS handler type: {handler_type}")
        return handler

    def validate_request(self, request: Request) -> None:
        """
        Validate a given request. This method ensures that all required fields are present.

        :param request: The Request object to validate.
        :raises ValueError: If validation fails.
        """
        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")

    def _create_tag_specifications(self, request: Request) -> Optional[List[Dict[str, Any]]]:
        """
        Creates tag specifications for resources.

        :param request: The request object containing tag information.
        :return: List of tag specifications or None if no tags are present.
        """
        # Use get_property to dynamically retrieve the 'tags' attribute
        tags = request.get_property("tags")
        if not tags:
            return None

        # Add dynamic tags such as RequestId and AWSHandler
        tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
        tag_list.append({"Key": "RequestId", "Value": request.requestId})
        tag_list.append({"Key": "AWSHandler", "Value": request.awsHandler})

        return [
            {
                "ResourceType": "instance",
                "Tags": tag_list,
            }
        ]
