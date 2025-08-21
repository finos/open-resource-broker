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

    def __init__(self, aws_client: AWSClient, logger: LoggingPort) -> None:
        """
        Initialize the launch template manager.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
        """
        self.aws_client = aws_client
        self._logger = logger

        # Get configuration port from container for package info
        from infrastructure.di.container import get_container

        container = get_container()
        try:
            from domain.base.ports.configuration_port import ConfigurationPort

            self.config_port = container.get(ConfigurationPort)
        except Exception:
            self.config_port = None

        # Get AWS native spec service from container
        from infrastructure.di.container import get_container

        container = get_container()
        try:
            from providers.aws.infrastructure.services.aws_native_spec_service import (
                AWSNativeSpecService,
            )

            self.aws_native_spec_service = container.get(AWSNativeSpecService)
        except Exception:
            # Service not available, native specs disabled
            self.aws_native_spec_service = None

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
                "Launch template %s exists with ID %s. Creating/reusing version.",
                launch_template_name,
                template_id,
            )

            response = self.aws_client.ec2_client.create_launch_template_version(
                LaunchTemplateId=template_id,
                VersionDescription=f"For request {request.request_id}",
                LaunchTemplateData=launch_template_data,
                ClientToken=client_token,  # Key for idempotency!
            )

            version = str(response["LaunchTemplateVersion"]["VersionNumber"])
            self._logger.info("Using version %s of launch template %s", version, template_id)

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
                raise

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

            self._logger.info("Using existing launch template %s version %s", template_id, version)

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
                raise

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
        self._logger.info(
            "Launch template %s does not exist. Creating new template.", template_name
        )

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
        self._logger.info("Created launch template %s", launch_template["LaunchTemplateId"])

        return LaunchTemplateResult(
            template_id=launch_template["LaunchTemplateId"],
            version=str(launch_template["LatestVersionNumber"]),
            template_name=template_name,
            is_new_template=True,
            is_new_version=True,
        )

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> Dict[str, Any]:
        """Prepare context with all computed values for template rendering."""
        
        # Get package name for CreatedBy tag
        created_by = "open-hostfactory-plugin"
        if self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                created_by = package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                pass
        
        # Process custom tags
        custom_tags = []
        if template.tags:
            custom_tags = [{"key": k, "value": v} for k, v in template.tags.items()]
        
        # Get instance name
        instance_name = get_instance_name(request.request_id)
        
        return {
            # Basic values
            "image_id": template.image_id,
            "instance_type": template.instance_type if template.instance_type else list(template.instance_types.keys())[0],
            "request_id": str(request.request_id),
            "template_id": str(template.template_id),
            "instance_name": instance_name,
            
            # Network configuration
            "subnet_id": template.subnet_id if hasattr(template, 'subnet_id') and template.subnet_id else None,
            "security_group_ids": template.security_group_ids or [],
            "associate_public_ip": True,
            
            # Optional configurations
            "key_name": template.key_name if hasattr(template, 'key_name') and template.key_name else None,
            "user_data": template.user_data if hasattr(template, 'user_data') and template.user_data else None,
            "instance_profile": template.instance_profile if hasattr(template, 'instance_profile') and template.instance_profile else None,
            "ebs_optimized": template.ebs_optimized if hasattr(template, 'ebs_optimized') and template.ebs_optimized is not None else None,
            "monitoring_enabled": template.monitoring_enabled if hasattr(template, 'monitoring_enabled') and template.monitoring_enabled is not None else None,
            
            # Conditional flags
            "has_subnet": hasattr(template, 'subnet_id') and bool(template.subnet_id),
            "has_security_groups": bool(template.security_group_ids),
            "has_key_name": hasattr(template, 'key_name') and bool(template.key_name),
            "has_user_data": hasattr(template, 'user_data') and bool(template.user_data),
            "has_instance_profile": hasattr(template, 'instance_profile') and bool(template.instance_profile),
            "has_ebs_optimized": hasattr(template, 'ebs_optimized') and template.ebs_optimized is not None,
            "has_monitoring": hasattr(template, 'monitoring_enabled') and template.monitoring_enabled is not None,
            "has_custom_tags": bool(custom_tags),
            
            # Dynamic values
            "created_by": created_by,
            "custom_tags": custom_tags
        }

    def _create_launch_template_data(
        self, aws_template: AWSTemplate, request: Request
    ) -> Dict[str, Any]:
        """
        Create launch template data from AWS template configuration with native spec support.

        Args:
            aws_template: The AWS template configuration
            request: The associated request

        Returns:
            Dictionary containing launch template data
        """
        # Try native spec processing first
        if self.aws_native_spec_service:
            native_spec = self.aws_native_spec_service.process_launch_template_spec(
                aws_template, request
            )
            if native_spec:
                self._logger.info(
                    "Using native launch template spec for template %s", aws_template.template_id
                )
                return native_spec

            # Use template-driven approach with native spec service
            context = self._prepare_template_context(aws_template, request)
            return self.aws_native_spec_service.render_default_spec("launch-template", context)
        
        # Fallback to legacy logic when native spec service is not available
        return self._create_launch_template_data_legacy(aws_template, request)

    def _create_launch_template_data_legacy(
        self, aws_template: AWSTemplate, request: Request
    ) -> Dict[str, Any]:
        """
        Create launch template data using legacy logic.

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
        self._logger.info("Creating launch template with resolved image_id: %s", image_id)

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

        # Get package name for CreatedBy tag
        created_by = "open-hostfactory-plugin"  # fallback
        if self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                created_by = package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                # Intentionally silent fallback for package info retrieval
                pass

        tags = [
            {"Key": "Name", "Value": instance_name},
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(aws_template.template_id)},
            {"Key": "CreatedBy", "Value": created_by},
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

        # Get package name for CreatedBy tag
        created_by = "open-hostfactory-plugin"  # fallback
        if self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                created_by = package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                # Intentionally silent fallback for package info retrieval
                pass

        return [
            {"Key": "Name", "Value": template_name},
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(aws_template.template_id)},
            {"Key": "CreatedBy", "Value": created_by},
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
