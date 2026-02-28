"""Spot Fleet prerequisite validator."""

import re
from typing import Any, Optional

from domain.base.ports import LoggingPort
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSValidationError
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.utilities.aws_operations import AWSOperations


class SpotFleetValidator:
    """Validates Spot Fleet prerequisites before fleet creation."""

    def __init__(
        self, aws_client: AWSClient, logger: LoggingPort, aws_ops: Optional[AWSOperations] = None
    ) -> None:
        self._aws_client = aws_client
        self._logger = logger
        self._aws_ops = aws_ops

    def validate(self, template: AWSTemplate) -> None:
        """Validate Spot Fleet specific prerequisites.

        Args:
            template: The AWS template to validate.

        Raises:
            AWSValidationError: If any prerequisite is not met.
        """
        errors: list[str] = []

        self._logger.debug(
            "Starting Spot Fleet prerequisites validation for template: %s",
            template.template_id,
        )

        # Validate fleet role
        if not hasattr(template, "fleet_role") or not template.fleet_role:
            errors.append("Fleet role ARN is required for Spot Fleet")
        elif "AWSServiceRoleForEC2SpotFleet" in template.fleet_role:
            if not self._is_valid_service_role(template.fleet_role):
                errors.append(
                    f"Invalid Spot Fleet service-linked role format: {template.fleet_role}. "
                    f"Expected full ARN: arn:aws:iam::<account_id>:role/aws-service-role/"
                    f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
                )
        elif self._is_valid_tagging_role(template.fleet_role):
            self._logger.debug("Valid Spot Fleet tagging role: %s", template.fleet_role)
        else:
            # Custom role — validate via IAM
            try:
                role_name = template.fleet_role.split("/")[-1]
                iam_client = self._aws_client.session.client(
                    "iam", config=self._aws_client.boto_config
                )
                self._retry(iam_client.get_role, operation_type="read_only", RoleName=role_name)
            except Exception as e:
                errors.append(f"Invalid custom fleet role: {e!s}")

        # Validate price type
        if hasattr(template, "price_type") and template.price_type:
            valid_options = ["spot", "ondemand", "heterogeneous"]
            if template.price_type not in valid_options:
                errors.append(
                    f"Invalid price type: {template.price_type}. "
                    f"Must be one of: {', '.join(valid_options)}"
                )

        # Validate percent_on_demand for heterogeneous
        if (
            hasattr(template, "price_type")
            and template.price_type == "heterogeneous"
            and (not hasattr(template, "percent_on_demand") or template.percent_on_demand is None)
        ):
            errors.append("percent_on_demand is required for heterogeneous price type")

        # Validate machine_types_ondemand for heterogeneous
        if (
            hasattr(template, "price_type")
            and template.price_type == "heterogeneous"
            and hasattr(template, "machine_types_ondemand")
            and template.machine_types_ondemand
        ):
            if not hasattr(template, "machine_types") or not template.machine_types:
                errors.append("machine_types must be specified when using machine_types_ondemand")

            for instance_type, weight in template.machine_types_ondemand.items():
                if not isinstance(weight, int) or weight <= 0:
                    errors.append(
                        f"Weight for on-demand instance type {instance_type} must be a positive integer"
                    )

        # Validate spot price
        if hasattr(template, "max_price") and template.max_price is not None:
            try:
                price = float(template.max_price)
                if price <= 0:
                    errors.append("Spot price must be greater than zero")
            except ValueError:
                errors.append("Invalid spot price format")

        if errors:
            self._logger.error("Validation errors found: %s", errors)
            raise AWSValidationError("\n".join(errors))

        self._logger.debug("All Spot Fleet prerequisites validation passed")

    def _retry(self, func: Any, operation_type: str = "standard", **kwargs: Any) -> Any:
        """Delegate to AWSOperations retry if available, else call directly."""
        retry_method = getattr(self._aws_ops, "_retry_with_backoff", None)
        if retry_method is not None:
            return retry_method(func, operation_type=operation_type, **kwargs)
        return func(**kwargs)

    def _is_valid_service_role(self, arn: str) -> bool:
        """Return True if the ARN matches the Spot Fleet service-linked role pattern."""
        pattern = (
            r"^arn:aws:iam::\d{12}:role/aws-service-role/"
            r"spotfleet\.amazonaws\.com/AWSServiceRoleForEC2SpotFleet$"
        )
        if re.match(pattern, arn):
            self._logger.debug("Valid Spot Fleet service-linked role: %s", arn)
            return True
        return False

    def _is_valid_tagging_role(self, arn: str) -> bool:
        """Return True if the ARN matches the EC2 Spot Fleet tagging role pattern."""
        pattern = r"^arn:aws:iam::\d{12}:role/aws-ec2-spot-fleet-tagging-role$"
        if re.match(pattern, arn):
            self._logger.debug("Valid Spot Fleet tagging role: %s", arn)
            return True
        return False
