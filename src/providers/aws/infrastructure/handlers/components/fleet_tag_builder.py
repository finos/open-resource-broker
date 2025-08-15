"""Fleet tag builder utility for AWS handlers.

This utility provides standardized tag building functionality across all AWS handlers,
eliminating duplication and ensuring consistent tagging patterns.
"""

from datetime import datetime
from typing import Any, Dict, List

from domain.request.aggregate import Request
from domain.template.aggregate import Template


class FleetTagBuilder:
    """Utility for building standardized AWS resource tags."""

    @staticmethod
    def build_common_tags(request: Request, template: Template) -> List[Dict[str, str]]:
        """Build common tags used across all AWS resources.

        Args:
            request: The request containing request information
            template: The template containing template information

        Returns:
            List of tag dictionaries with Key/Value pairs
        """
        return [
            {"Key": "Name", "Value": f"hf-{request.request_id}"},
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(template.template_id)},
            {"Key": "CreatedBy", "Value": "HostFactory"},
            {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
        ]

    @staticmethod
    def build_fleet_tags(
        request: Request, template: Template, fleet_name: str
    ) -> List[Dict[str, str]]:
        """Build tags specific to fleet resources.

        Args:
            request: The request containing request information
            template: The template containing template information
            fleet_name: The name/identifier of the fleet

        Returns:
            List of tag dictionaries with Key/Value pairs
        """
        tags = FleetTagBuilder.build_common_tags(request, template)
        tags[0]["Value"] = f"hf-fleet-{request.request_id}"  # Override Name for fleet
        return tags

    @staticmethod
    def build_instance_tags(request: Request, template: Template) -> List[Dict[str, str]]:
        """Build tags specific to instance resources.

        Args:
            request: The request containing request information
            template: The template containing template information

        Returns:
            List of tag dictionaries with Key/Value pairs
        """
        return FleetTagBuilder.build_common_tags(request, template)

    @staticmethod
    def add_template_tags(
        base_tags: List[Dict[str, str]], template: Template
    ) -> List[Dict[str, str]]:
        """Add template-specific tags to base tags.

        Args:
            base_tags: Base tag list to extend
            template: Template containing additional tags

        Returns:
            Extended list of tag dictionaries
        """
        if not template.tags:
            return base_tags

        # Convert template tags to AWS tag format and add to base tags
        template_tags = [{"Key": k, "Value": v} for k, v in template.tags.items()]
        return base_tags + template_tags

    @staticmethod
    def build_tag_specifications(
        request: Request, template: Template, resource_types: List[str]
    ) -> List[Dict[str, Any]]:
        """Build AWS TagSpecifications for multiple resource types.

        Args:
            request: The request containing request information
            template: The template containing template information
            resource_types: List of AWS resource types to tag

        Returns:
            List of TagSpecification dictionaries
        """
        # Build base tags
        base_tags = FleetTagBuilder.build_common_tags(request, template)

        # Add template tags if any
        all_tags = FleetTagBuilder.add_template_tags(base_tags, template)

        # Create TagSpecifications for each resource type
        tag_specifications = []
        for resource_type in resource_types:
            # Customize Name tag based on resource type
            tags = all_tags.copy()
            for tag in tags:
                if tag["Key"] == "Name":
                    if resource_type == "fleet":
                        tag["Value"] = f"hf-fleet-{request.request_id}"
                    elif resource_type == "spot-fleet-request":
                        tag["Value"] = f"hf-{request.request_id}"
                    elif resource_type == "instance":
                        tag["Value"] = f"hf-{request.request_id}"
                    # Add more resource type customizations as needed
                    break

            tag_specifications.append({"ResourceType": resource_type, "Tags": tags})

        return tag_specifications

    @staticmethod
    def build_asg_tags(request: Request, template: Template) -> List[Dict[str, Any]]:
        """Build tags specific to Auto Scaling Groups.

        ASG tags have a different format with PropagateAtLaunch property.

        Args:
            request: The request containing request information
            template: The template containing template information

        Returns:
            List of ASG tag dictionaries
        """
        base_tags = FleetTagBuilder.build_common_tags(request, template)

        # Add template tags if any
        all_tags = FleetTagBuilder.add_template_tags(base_tags, template)

        # Convert to ASG tag format with PropagateAtLaunch
        asg_tags = []
        for tag in all_tags:
            asg_tags.append(
                {
                    "Key": tag["Key"],
                    "Value": tag["Value"],
                    "PropagateAtLaunch": True,
                    "ResourceId": f"asg-{request.request_id}",
                    "ResourceType": "auto-scaling-group",
                }
            )

        return asg_tags
