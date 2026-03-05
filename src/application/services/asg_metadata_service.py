"""Service for managing ASG-specific metadata.

This service extracts ASG metadata management logic from command handlers,
following the Single Responsibility Principle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from domain.base import UnitOfWorkFactory
from domain.base.ports import LoggingPort
from domain.base.ports.asg_query_port import ASGQueryPort


class ASGMetadataService:
    """Service for managing Auto Scaling Group specific metadata."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        asg_query_port: ASGQueryPort,
        logger: LoggingPort,
    ) -> None:
        self.uow_factory = uow_factory
        self._asg_query_port = asg_query_port
        self.logger = logger

    async def update_asg_metadata_if_needed(self, request: Any, machines: list[Any]) -> None:
        """Update ASG-specific metadata when capacity changes are detected.

        Args:
            request: Request aggregate to update
            machines: List of machine objects associated with the request
        """
        try:
            # Get current ASG details from AWS if we have resource IDs
            if not request.resource_ids:
                return

            asg_name = request.resource_ids[0]  # ASG name is the resource_id
            current_asg_details = await self._get_current_asg_details(asg_name)

            if not current_asg_details:
                return

            # Compare with stored metadata
            stored_capacity = request.metadata.get("asg_desired_capacity")
            current_capacity = current_asg_details.get("DesiredCapacity")
            current_instances = len(
                [m for m in machines if m.status.value in ["running", "pending"]]
            )

            # Check if capacity has changed or if this is the first time we're tracking it
            capacity_changed = stored_capacity != current_capacity
            first_time_tracking = stored_capacity is None

            if capacity_changed or first_time_tracking:
                # Update metadata with new capacity information
                updated_metadata = request.metadata.copy()
                updated_metadata.update(
                    {
                        "asg_desired_capacity": current_capacity,
                        "asg_current_instances": current_instances,
                        "asg_capacity_last_updated": datetime.utcnow().isoformat(),
                        "asg_capacity_change_detected": capacity_changed,
                    }
                )

                # If this is the first time, also set creation metadata
                if first_time_tracking:
                    updated_metadata.update(
                        {
                            "asg_name": asg_name,
                            "asg_capacity_created_at": datetime.utcnow().isoformat(),
                            "asg_initial_capacity": current_capacity,
                        }
                    )

                # Update request with new metadata
                from domain.request.aggregate import Request

                updated_request = Request.model_validate(
                    {
                        **request.model_dump(),
                        "metadata": updated_metadata,
                        "version": request.version + 1,
                    }
                )

                # Save to database (this is a command, so writes are allowed)
                with self.uow_factory.create_unit_of_work() as uow:
                    uow.requests.save(updated_request)

                action = "Initialized" if first_time_tracking else "Updated"
                self.logger.info(
                    "%s ASG capacity metadata for request %s: %s -> %s (instances: %s)",
                    action,
                    request.request_id,
                    stored_capacity,
                    current_capacity,
                    current_instances,
                )

        except Exception as e:
            self.logger.warning("Failed to update ASG metadata: %s", e, exc_info=True)

    async def _get_current_asg_details(self, asg_name: str) -> dict[str, Any]:
        """Get current ASG details from AWS.

        Args:
            asg_name: Name of the Auto Scaling Group

        Returns:
            Dictionary with ASG details, or empty dict if not found
        """
        try:
            response_dict = await self._asg_query_port.get_asg_details(asg_name)
            if not response_dict:
                return {}
            # Wrap in describe_auto_scaling_groups response format for compatibility
            response = {"AutoScalingGroups": [response_dict]}

            if response.get("AutoScalingGroups"):
                return response["AutoScalingGroups"][0]
            else:
                self.logger.warning("ASG %s not found", asg_name)
                return {}

        except Exception as e:
            self.logger.warning("Failed to get ASG details for %s: %s", asg_name, e, exc_info=True)
            return {}
