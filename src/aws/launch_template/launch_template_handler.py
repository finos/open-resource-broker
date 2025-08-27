import boto3
from typing import Any, Dict, List, Optional
from src.helpers.utils import resolve_ssm_parameter
from src.models.config.provider_template import ProviderTemplate
from src.helpers.logger import setup_logging

logger = setup_logging()


class LaunchTemplateHandler:
    """
    Handler for managing EC2 Launch Templates.
    """

    def __init__(self, region_name: str):
        """
        Initialize the LaunchTemplateHandler.

        :param region_name: The AWS region to use for EC2 operations.
        """
        self.ec2_client = boto3.client("ec2", region_name=region_name)
        self.ssm_client = boto3.client("ssm", region_name=region_name)

    def create_launch_template(self, template_id: str, request_id: str, launch_template_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a Launch Template based on the specified parameters and provider template.

        :param template_id: The ID of the base template to use.
        :param request_id: The unique ID of the request.
        :param launch_template_data: The provider template launch config.
        :return: A dictionary containing the Launch Template ID and latest version.
        """
        try:
            template_name = f"{template_id}-{request_id}"
            logger.info(f"Creating Launch Template '{template_name}'...")

            response = self.ec2_client.create_launch_template(
                LaunchTemplateName=template_name,
                LaunchTemplateData=launch_template_data
            )

            launch_template = response["LaunchTemplate"]
            logger.info(f"Launch Template '{template_name}' created successfully with ID '{launch_template['LaunchTemplateId']}'.")
            return launch_template

        except Exception as e:
            logger.error(f"Failed to create Launch Template: {e}")
            raise

    def _construct_launch_template_data(self, provider_template: ProviderTemplate) -> Dict[str, Any]:
        """
        Construct LaunchTemplateData dictionary from the ProviderTemplate.

        :param provider_template: The ProviderTemplate object.
        :return: A LaunchTemplateData dictionary.
        """
        image_id = self._resolve_image_id(provider_template.imageId)

        data = {
            "ImageId": image_id,
            "InstanceType": provider_template.vmType,
            "KeyName": provider_template.keyName,
            "SecurityGroupIds": provider_template.securityGroupIds,
        }

        return data

    def _resolve_image_id(self, image_id: str) -> str:
        """
        Resolves the image ID. If it is an SSM parameter path, retrieves its value.

        :param image_id: The image ID string.
        :return: The resolved image ID.
        """
        if image_id.startswith("/aws/service/"):
            try:
                logger.info(f"Resolving SSM parameter for ImageId: {image_id}")
                response = self.ssm_client.get_parameter(Name=image_id, WithDecryption=True)
                resolved_image_id = response["Parameter"]["Value"]
                logger.info(f"Resolved ImageId from SSM Parameter Store: {resolved_image_id}")
                return resolved_image_id
            except Exception as e:
                logger.error(f"Failed to resolve SSM parameter for ImageId '{image_id}': {e}")
                raise ValueError(f"Failed to resolve SSM parameter for ImageId '{image_id}'. Error: {e}")
        elif image_id.startswith("ami-"):
            # If it's already a valid AMI ID, return it as-is
            return image_id
        else:
            raise ValueError(f"Invalid ImageId format: {image_id}. Expected 'ami-xxxxxxxx' or an SSM parameter path.")

    def list_launch_templates(self) -> List[Dict[str, Any]]:
        """
        List all available Launch Templates.

        :return: A list of dictionaries representing available launch templates.
        """
        try:
            paginator = self.ec2_client.get_paginator("describe_launch_templates")
            templates = []

            for page in paginator.paginate():
                for template_data in page.get("LaunchTemplates", []):
                    templates.append(template_data)

            logger.info(f"Retrieved {len(templates)} available launch templates.")
            return templates

        except Exception as e:
            logger.error(f"Failed to list Launch Templates: {str(e)}")
            raise

    def delete_launch_template(self, launch_template_id: str) -> None:
        """
        Delete a specific Launch Template.

        :param launch_template_id: The ID of the launch template to delete.
        """
        try:
            self.ec2_client.delete_launch_template(LaunchTemplateId=launch_template_id)
            logger.info(f"Launch Template {launch_template_id} deleted successfully.")

        except Exception as e:
            logger.error(f"Failed to delete Launch Template {launch_template_id}: {str(e)}")
            raise

    def create_new_version(self, launch_template_id: str, version_description: str, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new version of an existing Launch Template.

        :param launch_template_id: The ID of the existing launch template.
        :param version_description: A description for the new version.
        :param template_data: The configuration data for the new version.
        :return: A dictionary representing the new launch template version.
        """
        try:
            response = self.ec2_client.create_launch_template_version(
                LaunchTemplateId=launch_template_id,
                VersionDescription=version_description,
                LaunchTemplateData=template_data
            )
            version = response["LaunchTemplateVersion"]
            logger.info(f"Created new version for Launch Template {launch_template_id}.")
            return version

        except Exception as e:
            logger.error(f"Failed to create new version for Launch Template {launch_template_id}: {str(e)}")
            raise

    def get_versions(self, launch_template_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all versions of a specific Launch Template.

        :param launch_template_id: The ID of the launch template.
        :return: A list of dictionaries representing all versions of the launch template.
        """
        try:
            paginator = self.ec2_client.get_paginator("describe_launch_template_versions")
            versions = []

            for page in paginator.paginate(LaunchTemplateId=launch_template_id):
                for version_data in page.get("LaunchTemplateVersions", []):
                    versions.append(version_data)

            logger.info(f"Retrieved versions for Launch Template {launch_template_id}.")
            return versions

        except Exception as e:
            logger.error(f"Failed to retrieve versions for Launch Template {launch_template_id}: {str(e)}")
            raise
