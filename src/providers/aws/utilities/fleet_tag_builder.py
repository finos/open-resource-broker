"""Fleet tag builder utility for AWS handlers.

This utility provides standardized tag building functionality across all AWS handlers,
eliminating duplication and ensuring consistent tagging patterns.
"""

from datetime import datetime
from typing import Any, Dict, List

from domain.request.aggregate import Request
from domain.template.aggregate import Template
from infrastructure.utilities.common.resource_naming import get_resource_prefix


class FleetTagBuilder:
    """Utility for building standardized AWS resource tags."""

    @staticmethod
    def build_base_tags(
        request: Request, template: Template, package_name: str = "open-hostfactory-plugin"
    ) -> Dict[str, str]:
        """Build base tags used across all AWS resources.

        Args:
            request: The request containing request information
            template: The template containing template information
            package_name: Package name for CreatedBy tag

        Returns:
            Dictionary of tag key-value pairs
        """
        return {
            "RequestId": str(request.request_id),
            "TemplateId": str(template.template_id),
            "CreatedBy": package_name,
            "CreatedAt": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def build_resource_tags(
        request: Request,
        template: Template,
        resource_type: str,
        package_name: str = "open-hostfactory-plugin",
    ) -> Dict[str, str]:
        """Build complete tags for a specific resource type.

        Args:
            request: The request containing request information
            template: The template containing template information
            resource_type: Type of resource (fleet, instance, etc.)
            package_name: Package name for CreatedBy tag

        Returns:
            Dictionary of tag key-value pairs including Name tag
        """
        tags = FleetTagBuilder.build_base_tags(request, template, package_name)

        # Add resource-specific Name tag using configuration
        prefix = get_resource_prefix(resource_type)
        tags["Name"] = f"{prefix}{request.request_id}"

        # Add template tags if any
        if hasattr(template, "tags") and template.tags:
            tags.update({str(k): str(v) for k, v in template.tags.items()})

        return tags

    @staticmethod
    def format_for_aws(tags: Dict[str, str]) -> List[Dict[str, str]]:
        """Convert tag dictionary to AWS TagSpecifications format.

        Args:
            tags: Dictionary of tag key-value pairs

        Returns:
            List of tag dictionaries with Key/Value pairs for AWS APIs
        """
        return [{"Key": k, "Value": v} for k, v in tags.items()]

    @staticmethod
    def build_tag_specifications(
        request: Request,
        template: Template,
        resource_types: List[str],
        package_name: str = "open-hostfactory-plugin",
    ) -> List[Dict[str, Any]]:
        """Build AWS TagSpecifications for multiple resource types.

        Args:
            request: The request containing request information
            template: The template containing template information
            resource_types: List of AWS resource types to tag
            package_name: Package name for CreatedBy tag

        Returns:
            List of TagSpecification dictionaries for AWS APIs
        """
        tag_specifications = []

        for resource_type in resource_types:
            # Build tags for this resource type
            if resource_type in ["fleet", "spot-fleet-request", "instance"]:
                tags = FleetTagBuilder.build_resource_tags(
                    request, template, resource_type, package_name
                )
            else:
                tags = FleetTagBuilder.build_base_tags(request, template, package_name)

            tag_specifications.append(
                {"ResourceType": resource_type, "Tags": FleetTagBuilder.format_for_aws(tags)}
            )

        return tag_specifications

    # Legacy compatibility methods
    @staticmethod
    def build_common_tags(request: Request, template: Template) -> List[Dict[str, str]]:
        """Legacy method for backward compatibility."""
        tags = FleetTagBuilder.build_base_tags(request, template)
        tags["Name"] = f"hf-{request.request_id}"  # Legacy naming
        return FleetTagBuilder.format_for_aws(tags)

    @staticmethod
    def build_fleet_tags(
        request: Request, template: Template, fleet_name: str
    ) -> List[Dict[str, str]]:
        """Legacy method for backward compatibility."""
        tags = FleetTagBuilder.build_base_tags(request, template)
        tags["Name"] = fleet_name
        return FleetTagBuilder.format_for_aws(tags)

    @staticmethod
    def build_instance_tags(request: Request, template: Template) -> List[Dict[str, str]]:
        """Legacy method for backward compatibility."""
        return FleetTagBuilder.build_common_tags(request, template)

    @staticmethod
    def add_template_tags(
        base_tags: List[Dict[str, str]], template: Template
    ) -> List[Dict[str, str]]:
        """Legacy method for backward compatibility."""
        if not hasattr(template, "tags") or not template.tags:
            return base_tags

        template_tags = [{"Key": k, "Value": str(v)} for k, v in template.tags.items()]
        return base_tags + template_tags
