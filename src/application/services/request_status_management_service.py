"""Service for managing request status updates and persistence."""

from typing import Any

from domain.base.ports import LoggingPort
from domain.base import UnitOfWorkFactory
from domain.machine.aggregate import Machine
from .provisioning_orchestration_service import ProvisioningResult


class RequestStatusManagementService:
    """Service for managing request status updates and persistence."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort):
        self._uow_factory = uow_factory
        self._logger = logger

    async def update_request_from_provisioning(
        self,
        request: Any,
        provisioning_result: ProvisioningResult
    ) -> Any:
        """Update request status and data from provisioning results."""
        
        if not provisioning_result.success:
            return self._handle_provisioning_failure(request, provisioning_result)
        
        # Store resource IDs and provider metadata
        resource_ids = provisioning_result.resource_ids
        instances = provisioning_result.instances
        provider_data = provisioning_result.provider_data

        self._logger.info(
            "Processing provisioning success: %d resources, %d instances",
            len(resource_ids), len(instances)
        )

        # Store provider API in domain field
        # Provider API already set by RequestCreationService

        # Add resource IDs to request
        for resource_id in resource_ids:
            if isinstance(resource_id, str):
                request = request.add_resource_id(resource_id)

        # Populate machine IDs if available
        instance_ids = self._extract_instance_ids(provisioning_result)
        if instance_ids:
            request = request.add_machine_ids(instance_ids)
            self._logger.info("Populated %d machine IDs immediately", len(instance_ids))

        # Store provider-specific data
        if provider_data:
            request.provider_data.update(provider_data)

        # Handle provider errors for partial success
        provider_errors = provider_data.get("fleet_errors", []) if isinstance(provider_data, dict) else []
        has_api_errors = bool(provider_errors)
        
        if has_api_errors and not request.metadata.get("fleet_errors"):
            request.metadata["fleet_errors"] = provider_errors

        # Create and save machine aggregates
        if instances:
            machines_to_save = []
            for instance_data in instances:
                machine = self._create_machine_aggregate(instance_data, request, request.template_id)
                machines_to_save.append(machine)

            if machines_to_save:
                with self._uow_factory.create_unit_of_work() as uow:
                    uow.machines.save_batch(machines_to_save)

        # Update request status based on fulfillment
        return self._update_request_status(request, len(instances), request.requested_count, has_api_errors, provider_errors)

    def _handle_provisioning_failure(self, request: Any, provisioning_result: Dict[str, Any]) -> Any:
        """Handle provisioning failure."""
        from domain.request.value_objects import RequestStatus
        
        error_message = provisioning_result.error_message or "Unknown error"
        request = request.update_status(RequestStatus.FAILED, f"Provisioning failed: {error_message}")
        
        request.metadata["error_message"] = error_message
        request.metadata["error_type"] = "ProvisioningFailure"
        
        return request

    def _update_request_status(
        self, 
        request: Any, 
        instance_count: int, 
        requested_count: int, 
        has_api_errors: bool, 
        provider_errors: List[Dict]
    ) -> Any:
        """Update request status based on fulfillment and errors."""
        from domain.request.value_objects import RequestStatus
        
        error_summary = None
        if has_api_errors:
            error_summary = "; ".join(
                f"{err.get('error_code', 'Unknown')}: {err.get('error_message', 'No message')}"
                for err in provider_errors
            ) or "Unknown API errors"

        if instance_count == requested_count:
            if has_api_errors:
                request = request.update_status(
                    RequestStatus.PARTIAL,
                    f"Partial success: {instance_count}/{requested_count} instances created with API errors: {error_summary}",
                )
            else:
                request = request.update_status(
                    RequestStatus.COMPLETED,
                    "All instances provisioned successfully",
                )
        elif instance_count > 0:
            if has_api_errors:
                request = request.update_status(
                    RequestStatus.PARTIAL,
                    f"Partial success: {instance_count}/{requested_count} instances created with API errors: {error_summary}",
                )
            else:
                request = request.update_status(
                    RequestStatus.PARTIAL,
                    f"Partially fulfilled: {instance_count}/{requested_count} instances",
                )
        else:
            request = request.update_status(
                RequestStatus.IN_PROGRESS,
                "Resources created, instances pending",
            )

        return request

    def _extract_instance_ids(self, result: Dict[str, Any]) -> List[str]:
        """Extract instance IDs if available in provider result."""
        try:
            if result.get("instance_ids"):
                return result["instance_ids"]
            elif result.get("instances"):
                instances = result["instances"]
                if isinstance(instances, list) and instances:
                    return [
                        instance.get("instance_id") 
                        for instance in instances 
                        if instance.get("instance_id")
                    ]
            return []
        except Exception as e:
            self._logger.debug("Could not extract instance IDs: %s", e)
            return []

    def _create_machine_aggregate(self, instance_data: Dict[str, Any], request: Any, template_id: str) -> Machine:
        """Create machine aggregate from instance data."""
        from datetime import datetime
        from domain.base.value_objects import InstanceType
        from domain.machine.machine_identifiers import MachineId
        from domain.machine.machine_status import MachineStatus

        # Parse launch_time if it's a string
        launch_time = instance_data.get("launch_time")
        if isinstance(launch_time, str):
            try:
                launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None

        return Machine(
            machine_id=MachineId(value=instance_data["instance_id"]),
            request_id=str(request.request_id),
            template_id=template_id,
            provider_type=request.provider_type,
            provider_name=request.provider_name,
            provider_api=request.provider_api,
            resource_id=instance_data.get("resource_id"),
            instance_type=InstanceType(value=instance_data.get("instance_type", "t2.micro")),
            image_id=instance_data.get("image_id", "unknown"),
            status=MachineStatus.PENDING,
            private_ip=instance_data.get("private_ip"),
            public_ip=instance_data.get("public_ip"),
            launch_time=launch_time,
            metadata=instance_data.get("metadata", {}),
        )