"""
AWS Launch Template Manager - Handles AWS-specific launch template operations.

This module provides centralized management of AWS launch templates,
moving AWS-specific logic out of the base handler to maintain clean architecture.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from infrastructure.utilities.common.resource_naming import (
    get_instance_name,
    get_launch_template_name,
)
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import (
    AWSValidationError,
    InfrastructureError,
)
from providers.aws.infrastructure.aws_client import AWSClient


@dataclass
class LaunchTemplateResult:
    """Result of launch template creation/update operation."""

    template_id: str
    version: str
    template_name: str
    is_new_template: bool = False
    is_new_version: bool = False


@injectable
class AWSLaunchTemplateManager:
    """Manages AWS launch template creation and updates."""

    def __init__(self, aws_client: AWSClient, logger: LoggingPort):
        """
        Initialize the launch template manager.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
        """
        self.aws_client = aws_client
        self._logger = logger

    def create_or_update_launch_template(
        self, aws_template: AWSTemplate, request: Request
    ) -> LaunchTemplateResult:
        """
        Create an EC2 launch template or a new version if it already exists.
        Uses ClientToken for idempotency to prevent duplicate versions.

        Args:
            aws_template: The AWS template configuration
            request: The associated request

        Returns:
            LaunchTemplateResult containing template ID, version, and metadata

        Raises:
            AWSValidationError: If the template configuration is invalid
            InfrastructureError: For AWS API errors
        """
        try:
            # Check if template specifies existing launch template to use
            if aws_template.launch_template_id:
                return self._use_existing_template_strategy(aws_template)

            # Determine strategy based on configuration
            # For now, default to per-request version strategy
            return self._create_per_request_version(aws_template, request)

        except ClientError as e:
            error_msg = f"Failed to create/update launch template: {e.response['Error']['Message']}"
            self._logger.error(error_msg)
            raise InfrastructureError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error in launch template management: {str(e)}"
            self._logger.error(error_msg)
            raise InfrastructureError(error_msg)

    def _create_per_request_version(
        self, aws_template: AWSTemplate, request: Request
    ) -> LaunchTemplateResult:
        """
        Create a new version of launch template for each request.
        This ensures each request gets its own template version for tracking.

        Args:
            aws_template: The AWS template configuration
            request: The associated request

        Returns:
            LaunchTemplateResult with template details
        """
        # Create launch template data
        launch_template_data = self._create_launch_template_data(aws_template, request)

        # Get the launch template name using the helper function
        launch_template_name = get_launch_template_name(request.request_id)

        # Generate a deterministic client token for idempotency
        client_token = self._generate_client_token(request, aws_template)

        # First try to describe the launch template to see if it exists
        try:
            existing_template = self.aws_client.ec2_client.describe_launch_templates(
                LaunchTemplateNames=[launch_template_name]
            )

            # Template exists, create a new version
            template_id = existing_template["LaunchTemplates"][0]["LaunchTemplateId"]
            self._logger.info(
                f"Launch template {launch_template_name} exists with ID {template_id}. Creating/reusing version."
            )

            response = self.aws_client.ec2_client.create_launch_template_version(
                LaunchTemplateId=template_id,
                VersionDescription=f"For request {request.request_id}",
                LaunchTemplateData=launch_template_data,
                ClientToken=client_token,  # Key for idempotency!
            )

            version = str(response["LaunchTemplateVersion"]["VersionNumber"])
            self._logger.info(f"Using version {version} of launch template {template_id}")

            return LaunchTemplateResult(
                template_id=template_id,
                version=version,
                template_name=launch_template_name,
                is_new_template=False,
                is_new_version=True,
            )

        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidLaunchTemplateName.NotFoundException":
                # Template doesn't exist, create it
                return self._create_new_launch_template(
                    launch_template_name,
                    launch_template_data,
                    client_token,
                    request,
                    aws_template,
                )
            else:
                # Some other error
                raise e

    def _create_or_reuse_base_template(self, aws_template: AWSTemplate) -> LaunchTemplateResult:
        """
        Create or reuse a base launch template (not per-request).
        This strategy creates one template per template_id and reuses it.

        Args:
            aws_template: The AWS template configuration

        Returns:
            LaunchTemplateResult with template details
        """
        # This would be implemented for base template strategy
        # For now, not implemented as we're using per-request strategy
        raise NotImplementedError("Base template strategy not yet implemented")

    def _use_existing_template_strategy(self, aws_template: AWSTemplate) -> LaunchTemplateResult:
        """
        Use an existing launch template specified in the template configuration.

        Args:
            aws_template: The AWS template configuration with launch_template_id

        Returns:
            LaunchTemplateResult with existing template details
        """
        template_id = aws_template.launch_template_id
        version = aws_template.launch_template_version or "$Latest"

        try:
            # Validate that the template exists
            response = self.aws_client.ec2_client.describe_launch_templates(
                LaunchTemplateIds=[template_id]
            )

            template_name = response["LaunchTemplates"][0]["LaunchTemplateName"]

            self._logger.info(f"Using existing launch template {template_id} version {version}")

            return LaunchTemplateResult(
                template_id=template_id,
                version=version,
                template_name=template_name,
                is_new_template=False,
                is_new_version=False,
            )

        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidLaunchTemplateId.NotFound":
                raise AWSValidationError(f"Launch template {template_id} not found")
            else:
                raise e

    def _create_new_launch_template(
        self,
        template_name: str,
        template_data: Dict[str, Any],
        client_token: str,
        request: Request,
        aws_template: AWSTemplate,
    ) -> LaunchTemplateResult:
        """
        Create a completely new launch template.

        Args:
            template_name: Name for the new template
            template_data: Launch template data
            client_token: Client token for idempotency
            request: The associated request
            aws_template: The AWS template configuration

        Returns:
            LaunchTemplateResult with new template details
        """
        self._logger.info(f"Launch template {template_name} does not exist. Creating new template.")

        response = self.aws_client.ec2_client.create_launch_template(
            LaunchTemplateName=template_name,
            VersionDescription=f"Created for request {request.request_id}",
            LaunchTemplateData=template_data,
            ClientToken=client_token,  # Key for idempotency!
            TagSpecifications=[
                {
                    "ResourceType": "launch-template",
                    "Tags": self._create_launch_template_tags(aws_template, request),
                }
            ],
        )

        launch_template = response["LaunchTemplate"]
        self._logger.info(f"Created launch template {launch_template['LaunchTemplateId']}")

        return LaunchTemplateResult(
            template_id=launch_template["LaunchTemplateId"],
            version=str(launch_template["LatestVersionNumber"]),
            template_name=template_name,
            is_new_template=True,
            is_new_version=True,
        )

    def _create_launch_template_data(
        self, aws_template: AWSTemplate, request: Request
    ) -> Dict[str, Any]:
        """
        Create launch template data from AWS template configuration.

        Args:
            aws_template: The AWS template configuration
            request: The associated request

        Returns:
            Dictionary containing launch template data
        """
        # Template should already contain resolved AMI ID from boundary resolution
        image_id = aws_template.image_id
        if not image_id:
            error_msg = f"Template {aws_template.template_id} has no image_id specified"
            self._logger.error(error_msg)
            raise AWSValidationError(error_msg)

        # Log the image_id being used
        self._logger.info(f"Creating launch template with resolved image_id: {image_id}")

        # Get instance name using the helper function
        get_instance_name(request.request_id)

        launch_template_data = {
            "ImageId": image_id,
            "InstanceType": (
                aws_template.instance_type
                if aws_template.instance_type
                else list(aws_template.instance_types.keys())[0]
            ),
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": self._create_instance_tags(aws_template, request),
                }
            ],
        }

        # Add optional configurations
        if aws_template.subnet_id:
            launch_template_data["NetworkInterfaces"] = [
                {
                    "DeviceIndex": 0,
                    "SubnetId": aws_template.subnet_id,
                    "AssociatePublicIpAddress": True,
                }
            ]

        if aws_template.key_name:
            launch_template_data["KeyName"] = aws_template.key_name

        if aws_template.user_data:
            launch_template_data["UserData"] = aws_template.user_data

        if aws_template.instance_profile:
            launch_template_data["IamInstanceProfile"] = {"Name": aws_template.instance_profile}

        # Add EBS optimization if specified (check if attribute exists)
        if hasattr(aws_template, "ebs_optimized") and aws_template.ebs_optimized is not None:
            launch_template_data["EbsOptimized"] = aws_template.ebs_optimized

        # Add monitoring if specified
        if (
            hasattr(aws_template, "monitoring_enabled")
            and aws_template.monitoring_enabled is not None
        ):
            launch_template_data["Monitoring"] = {"Enabled": aws_template.monitoring_enabled}

        return launch_template_data

    def _create_instance_tags(
        self, aws_template: AWSTemplate, request: Request
    ) -> List[Dict[str, str]]:
        """
        Create instance tags for the launch template.

        Args:
            aws_template: The AWS template configuration
            request: The associated request

        Returns:
            List of tag dictionaries
        """
        # Get instance name using the helper function
        instance_name = get_instance_name(request.request_id)

        tags = [
            {"Key": "Name", "Value": instance_name},
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(aws_template.template_id)},
            {"Key": "CreatedBy", "Value": "HostFactory"},
        ]

        # Add template tags if any
        if aws_template.tags:
            template_tags = [{"Key": k, "Value": str(v)} for k, v in aws_template.tags.items()]
            tags.extend(template_tags)

        return tags

    def _create_launch_template_tags(
        self, aws_template: AWSTemplate, request: Request
    ) -> List[Dict[str, str]]:
        """
        Create tags for the launch template resource itself.

        Args:
            aws_template: The AWS template configuration
            request: The associated request

        Returns:
            List of tag dictionaries
        """
        template_name = get_launch_template_name(request.request_id)

        return [
            {"Key": "Name", "Value": template_name},
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(aws_template.template_id)},
            {"Key": "CreatedBy", "Value": "HostFactory"},
        ]

    def _generate_client_token(self, request: Request, aws_template: AWSTemplate) -> str:
        """
        Generate a deterministic client token for idempotency.

        Args:
            request: The associated request
            aws_template: The AWS template configuration

        Returns:
            Client token string
        """
        # Generate a deterministic client token based on the request ID, template ID, and image ID
        # This ensures idempotency - identical requests will return the same result
        token_input = f"{request.request_id}:{aws_template.template_id}:{aws_template.image_id}"
        # Truncate to 32 chars
        return hashlib.sha256(token_input.encode()).hexdigest()[:32]
