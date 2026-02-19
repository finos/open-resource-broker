"""EC2 Fleet Management Service.

This service handles EC2 Fleet creation and management operations,
extracted from EC2FleetHandler to follow Single Responsibility Principle.
"""

from typing import Any

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.exceptions.aws_exceptions import AWSValidationError
from providers.aws.infrastructure.aws_client import AWSClient


@injectable
class EC2FleetManagementService:
    """Service for EC2 Fleet creation and management operations."""

    def __init__(self, aws_client: AWSClient, logger: LoggingPort) -> None:
        """Initialize the management service.

        Args:
            aws_client: AWS client for fleet operations
            logger: Logger for logging messages
        """
        self._aws_client = aws_client
        self._logger = logger

    def create_fleet_config(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create EC2 Fleet configuration dictionary.

        Args:
            template: AWS template with fleet configuration
            request: Request containing fleet requirements
            launch_template_id: Launch template ID to use
            launch_template_version: Launch template version

        Returns:
            Dictionary with EC2 Fleet configuration
        """
        # Basic fleet configuration
        fleet_config = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                }
            ],
            "TargetCapacitySpecification": {
                "TotalTargetCapacity": request.requested_count,
                "DefaultTargetCapacityType": self._get_default_capacity_type(template.price_type),
            },
            "Type": template.fleet_type.value if template.fleet_type else "request",
        }

        # Add spot configuration if needed
        if template.price_type in {"spot", "mixed"}:
            if template.max_price:
                fleet_config["SpotOptions"] = {
                    "MaxTotalPrice": str(template.max_price * request.requested_count)
                }

        # Add on-demand configuration if needed
        if template.price_type in {"ondemand", "mixed"}:
            fleet_config["OnDemandOptions"] = {"AllocationStrategy": "lowestPrice"}

        return fleet_config

    def validate_fleet_type(self, template: AWSTemplate) -> None:
        """Validate fleet type is supported.

        Args:
            template: AWS template to validate

        Raises:
            AWSValidationError: If fleet type is invalid
        """
        if not template.fleet_type:
            raise AWSValidationError("Fleet type is required for EC2Fleet")

        # Get valid fleet types
        valid_types = ["request", "maintain", "instant"]

        try:
            fleet_type = AWSFleetType(template.fleet_type.lower())
            if fleet_type.value not in valid_types:
                raise ValueError
        except ValueError:
            raise AWSValidationError(
                f"Invalid EC2 fleet type: {template.fleet_type}. "
                f"Must be one of: {', '.join(valid_types)}"
            )

    def create_fleet(self, fleet_config: dict[str, Any]) -> dict[str, Any]:
        """Create EC2 Fleet using AWS API.

        Args:
            fleet_config: Fleet configuration dictionary

        Returns:
            AWS API response from fleet creation
        """
        try:
            response = self._aws_client.ec2_client.create_fleet(**fleet_config)
            self._logger.info(f"Created EC2 Fleet: {response.get('FleetId')}")
            return response
        except Exception as e:
            self._logger.error(f"Failed to create EC2 Fleet: {e}")
            raise

    def _get_default_capacity_type(self, price_type: str) -> str:
        """Get default target capacity type based on price type.

        Args:
            price_type: Price type from template

        Returns:
            Default capacity type for EC2 Fleet
        """
        if price_type == "spot":
            return "spot"
        elif price_type == "ondemand":
            return "on-demand"
        else:  # mixed or None
            return "on-demand"
