"""AWS-specific native spec processing."""

import os
from typing import Any, Dict, Optional

from application.services.native_spec_service import NativeSpecService
from domain.base.dependency_injection import injectable
from domain.base.ports.configuration_port import ConfigurationPort
from domain.request.aggregate import Request
from infrastructure.utilities.file.json_utils import read_json_file
from providers.aws.domain.template.aggregate import AWSTemplate


@injectable
class AWSNativeSpecService:
    """AWS-specific native spec processing."""

    def __init__(self, native_spec_service: NativeSpecService, config_port: ConfigurationPort):
        self.native_spec_service = native_spec_service
        self.config_port = config_port
        self.spec_renderer = native_spec_service.spec_renderer

    def render_default_spec(self, spec_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Render default specification template with context.
        
        Args:
            spec_type: Type of spec (ec2fleet, spotfleet, asg, runinstances, launch-template)
            context: Template context variables
            
        Returns:
            Rendered specification dictionary
        """
        try:
            # Construct path to default spec file
            spec_file_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "..", "..", "..", "..", 
                "specs", "aws", spec_type, "default.json"
            )
            
            # Use spec renderer to render from file
            return self.spec_renderer.render_spec_from_file(spec_file_path, context)
            
        except Exception as e:
            self.native_spec_service.logger.error(f"Failed to render default spec for {spec_type}: {e}")
            raise

    def process_launch_template_spec(
        self, template: AWSTemplate, request: Request
    ) -> Optional[Dict[str, Any]]:
        """Process AWS launch template spec."""
        if not self.native_spec_service.is_native_spec_enabled():
            return None

        spec = self._resolve_launch_template_spec(template)
        if not spec:
            return None

        context = self._build_aws_context(template, request)
        return self.native_spec_service.render_spec(spec, context)

    def process_provider_api_spec(
        self, template: AWSTemplate, request: Request
    ) -> Optional[Dict[str, Any]]:
        """Process AWS provider API spec."""
        if not self.native_spec_service.is_native_spec_enabled():
            return None

        spec = self._resolve_provider_api_spec(template)
        if not spec:
            return None

        context = self._build_aws_context(template, request)
        return self.native_spec_service.render_spec(spec, context)

    def _resolve_launch_template_spec(self, template: AWSTemplate) -> Optional[Dict[str, Any]]:
        """Resolve launch template spec."""
        if template.launch_template_spec:
            return template.launch_template_spec
        elif template.launch_template_spec_file:
            return self._load_spec_file(template.launch_template_spec_file)
        return None

    def _resolve_provider_api_spec(self, template: AWSTemplate) -> Optional[Dict[str, Any]]:
        """Resolve provider API spec."""
        if template.provider_api_spec:
            return template.provider_api_spec
        elif template.provider_api_spec_file:
            return self._load_spec_file(template.provider_api_spec_file)
        return None

    def _load_spec_file(self, file_path: str) -> Dict[str, Any]:
        """Load AWS spec file."""
        provider_config = self.config_port.get_provider_config()
        provider_defaults = provider_config.get("provider_defaults", {}).get("aws", {})
        base_path = (
            provider_defaults.get("extensions", {})
            .get("native_spec", {})
            .get("spec_file_base_path", "specs/aws")
        )
        return read_json_file(f"{base_path}/{file_path}")

    def _build_aws_context(self, template: AWSTemplate, request: Request) -> Dict[str, Any]:
        """Build AWS-specific context."""
        # Get package info for template context
        package_info = self.config_port.get_package_info()

        return {
            "request_id": str(request.request_id),
            "requested_count": request.requested_count,
            "template_id": template.template_id,
            "image_id": template.image_id,
            "instance_type": template.instance_type,
            "package_name": package_info.get("name", "open-hostfactory-plugin"),
            "package_version": package_info.get("version", "unknown"),
        }
