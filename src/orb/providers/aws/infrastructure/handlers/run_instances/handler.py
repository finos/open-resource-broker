"""AWS RunInstances Handler.

This module provides the RunInstances handler implementation for managing
individual EC2 instance launches through the AWS EC2 RunInstances API.

The RunInstances handler provides direct control over individual EC2 instance
provisioning with support for both On-Demand and Spot instances, offering
simplicity and predictability for straightforward deployment scenarios.

Key Features:
    - Direct EC2 instance control
    - On-Demand and Spot instance support
    - Simple configuration and management
    - Immediate instance provisioning
    - Fine-grained instance control

Classes:
    RunInstancesHandler: Main handler for individual instance operations

Usage:
    This handler is used by the AWS provider to manage individual EC2
    instances for simple, predictable workloads that don't require
    advanced fleet management capabilities.

Note:
    RunInstances is ideal for simple deployments, development environments,
    and workloads that require predictable instance provisioning.
"""

from typing import Any, Optional

from botocore.exceptions import ClientError

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSConfigurationError,
    AWSInfrastructureError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from orb.providers.aws.infrastructure.tags import build_system_tags, merge_tags
from orb.providers.aws.utilities.aws_operations import AWSOperations


@injectable
class RunInstancesHandler(AWSHandler, BaseContextMixin):
    """Handler for direct EC2 instance operations using RunInstances."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,  # type: ignore[assignment]
        machine_adapter: Optional[AWSMachineAdapter] = None,
        error_handler: ErrorHandlingPort = None,  # type: ignore[assignment]
        aws_native_spec_service=None,
        config_port: Optional[ConfigurationPort] = None,
    ) -> None:
        """
        Initialize RunInstances handler with integrated dependencies.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
            error_handler: Optional error handling port for exception management
        """
        # Use integrated base class initialization
        super().__init__(
            aws_client,
            logger,
            aws_ops,
            launch_template_manager,
            request_adapter,
            machine_adapter,
            error_handler,
            aws_native_spec_service=aws_native_spec_service,
            config_port=config_port,
        )

    @handle_infrastructure_exceptions(context="run_instances_creation")
    def _acquire_hosts_internal(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
        """
        Create EC2 instances using RunInstances to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            # Execute RunInstances operation (existing logic)
            response = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_instances_with_response(request, aws_template),
                operation_name="run EC2 instances",
                context="RunInstances",
            )

            resource_id = response["ReservationId"]

            instance_ids = self._extract_instance_ids(
                response, lambda r: [i["InstanceId"] for i in r.get("Instances", [])]
            )

            # Create instances using existing machine adapter
            instances = []
            if self._machine_adapter and response.get("Instances"):
                for instance_data in response.get("Instances", []):
                    try:
                        instances.append(
                            self._machine_adapter.create_machine_from_aws_instance(
                                instance_data, str(request.request_id), "RunInstances", resource_id
                            )
                        )
                    except Exception as e:
                        # If adapter fails with partial data, skip machine creation
                        # Machines will be populated later via status query
                        self._logger.debug("Skipping machine creation with partial data: %s", e)

            return {
                "success": True,
                "resource_ids": [resource_id],
                "instance_ids": instance_ids,  # Store instance IDs for tracking
                "instances": instances,
                "provider_data": {
                    "resource_type": "run_instances",
                    "reservation_id": resource_id,
                    "instance_ids": instance_ids,
                },
            }
        except Exception as e:
            self._logger.error("RunInstances failed: %s", e)
            raise

    def _create_instances_with_response(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
        """Create RunInstances and return full AWS response."""
        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Create RunInstances parameters
        run_params = self._create_run_instances_params(
            aws_template=aws_template,
            request=request,
            launch_template_id=launch_template_result.template_id,
            launch_template_version=launch_template_result.version,
        )

        # Execute RunInstances API call with circuit breaker for critical operation
        response = self._retry_with_backoff(
            self.aws_client.ec2_client.run_instances,
            operation_type="critical",
            **run_params,
        )

        # Validate response
        reservation_id = response.get("ReservationId")
        instance_ids = [instance["InstanceId"] for instance in response.get("Instances", [])]

        if not instance_ids:
            raise AWSInfrastructureError("No instances were created by RunInstances")

        if not reservation_id:
            raise AWSInfrastructureError("No reservation ID returned by RunInstances")

        self._logger.info(
            "Successfully created %d instances via RunInstances with reservation ID %s: %s",
            len(instance_ids),
            reservation_id,
            instance_ids,
        )

        return response

    def _resolve_provider_api(
        self, request: Request, aws_template: Optional[AWSTemplate] = None
    ) -> str:
        """Resolve the provider_api value to stamp onto instance data."""
        if aws_template and aws_template.provider_api is not None:
            return (
                aws_template.provider_api.value
                if hasattr(aws_template.provider_api, "value")
                else str(aws_template.provider_api)
            )
        return request.provider_api or "RunInstances"

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Prepare context with all computed values for template rendering."""

        # Start with base context
        context = self._prepare_base_context(
            template,
            str(request.request_id),
            request.requested_count,
        )

        # Add standard flags
        context.update(self._prepare_standard_flags(template))

        # Add standard tags
        tag_context = self._prepare_standard_tags(template, str(request.request_id))
        context.update(tag_context)

        # Add RunInstances-specific context
        context.update(self._prepare_runinstances_specific_context(template, request))

        return context

    def _prepare_runinstances_specific_context(
        self, template: AWSTemplate, request: Request
    ) -> dict[str, Any]:
        """Prepare RunInstances-specific context."""

        if self.config_port is None:
            raise AWSConfigurationError(
                "config_port must be injected before calling _prepare_runinstances_specific_context"
            )
        return {
            # RunInstances-specific values
            "instance_name": f"{self.config_port.get_resource_prefix('instance')}{request.request_id}",
            # Pricing configuration
            "default_capacity_type": self._get_default_capacity_type(template.price_type),
            "has_spot_options": bool(template.allocation_strategy or template.max_price),
            "max_spot_price": (str(template.max_price) if template.max_price is not None else None),
            "spot_instance_type": (
                self._get_spot_instance_type(template.allocation_strategy)
                if template.allocation_strategy
                else None
            ),
        }

    def _get_spot_instance_type(self, allocation_strategy: str) -> str:
        """Convert allocation strategy to spot instance type for RunInstances."""
        # RunInstances doesn't support all EC2Fleet allocation strategies
        # Map to supported spot instance types
        strategy_map = {
            "lowest_price": "one-time",
            "diversified": "one-time",  # RunInstances doesn't support diversified directly
            "capacity_optimized": "one-time",  # RunInstances doesn't support capacity-optimized directly
            "lowestPrice": "one-time",
            "capacityOptimized": "one-time",
            "capacityOptimizedPrioritized": "one-time",
            "priceCapacityOptimized": "one-time",
        }
        return strategy_map.get(allocation_strategy, "one-time")

    def _create_run_instances_params(
        self,
        aws_template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create RunInstances parameters with native spec support."""
        # Try native spec processing with merge support
        if self.aws_native_spec_service:
            context = self._prepare_template_context(aws_template, request)
            context.update(
                {
                    "launch_template_id": launch_template_id,
                    "launch_template_version": launch_template_version,
                }
            )

            native_spec = self.aws_native_spec_service.process_provider_api_spec_with_merge(
                aws_template, request, "runinstances", context
            )
            if native_spec:
                # Ensure launch template info is in the spec
                if "LaunchTemplate" not in native_spec:
                    native_spec["LaunchTemplate"] = {}
                native_spec["LaunchTemplate"]["LaunchTemplateId"] = launch_template_id
                native_spec["LaunchTemplate"]["Version"] = launch_template_version
                # Ensure MinCount and MaxCount are set
                if "MinCount" not in native_spec:
                    native_spec["MinCount"] = 1
                if "MaxCount" not in native_spec:
                    native_spec["MaxCount"] = request.requested_count
                self._logger.info(
                    "Using native provider API spec with merge for RunInstances template %s",
                    aws_template.template_id,
                )
                return native_spec

            # Use template-driven approach with native spec service
            return self.aws_native_spec_service.render_default_spec("runinstances", context)

        # Fallback to legacy logic when native spec service is not available
        return self._create_run_instances_params_legacy(
            aws_template, request, launch_template_id, launch_template_version
        )

    def _create_run_instances_params_legacy(
        self,
        aws_template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create RunInstances parameters using legacy logic."""

        # Base parameters using launch template
        params = {
            "LaunchTemplate": {
                "LaunchTemplateId": launch_template_id,
                "Version": launch_template_version,
            },
            "MinCount": 1,
            "MaxCount": request.requested_count,
        }

        # Add instance type override if specified (overrides launch template)
        if aws_template.machine_types:
            # Use first machine type for RunInstances (single instance type only)
            params["InstanceType"] = next(iter(aws_template.machine_types.keys()))

        # Handle networking overrides based on launch template source
        if aws_template.launch_template_id:
            # Using existing launch template - need to check what it contains
            # For now, assume we can override (this should be improved to inspect the
            # LT)
            if aws_template.subnet_id:
                params["SubnetId"] = aws_template.subnet_id
            elif aws_template.subnet_ids and len(aws_template.subnet_ids) == 1:
                params["SubnetId"] = aws_template.subnet_ids[0]

            if aws_template.security_group_ids:
                params["SecurityGroupIds"] = aws_template.security_group_ids
        else:
            # We created the launch template ourselves with NetworkInterfaces
            # Don't override networking at API level - AWS will reject it
            # The launch template already contains all networking configuration
            pass

        # Add spot instance configuration if needed
        if aws_template.price_type == "spot":
            params["InstanceMarketOptions"] = {"MarketType": "spot"}

            if aws_template.max_price is not None:
                params["InstanceMarketOptions"]["SpotOptions"] = {  # type: ignore[index]
                    "MaxPrice": str(aws_template.max_price)
                }

        # Add additional tags for instances (beyond launch template)
        if self.config_port is None:
            raise AWSConfigurationError(
                "config_port must be injected before calling _build_run_instances_params"
            )
        instance_tags = merge_tags(
            [
                {
                    "Key": "Name",
                    "Value": f"{self.config_port.get_resource_prefix('instance')}{request.request_id}",
                },
                *(
                    [{"Key": k, "Value": v} for k, v in aws_template.tags.items()]
                    if aws_template.tags
                    else []
                ),
            ],
            build_system_tags(
                request_id=str(request.request_id),
                template_id=str(aws_template.template_id),
                provider_api="RunInstances",
            ),
        )
        tag_specifications: list[dict[str, Any]] = [
            {"ResourceType": "instance", "Tags": instance_tags},
        ]
        if aws_template.price_type == "spot":
            tag_specifications.append(
                {"ResourceType": "spot-instances-request", "Tags": instance_tags}
            )

        params["TagSpecifications"] = tag_specifications

        return params

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check the status of instances created by RunInstances."""
        try:
            # Get instance IDs from provider_data (preferred) or fallback to metadata for backward compatibility
            instance_ids = (
                request.provider_data.get("instance_ids", [])
                if hasattr(request, "provider_data") and request.provider_data
                else request.metadata.get("instance_ids", [])
            )

            if not instance_ids:
                # If no instance IDs in provider_data/metadata, try to find instances using resource IDs
                if hasattr(request, "resource_ids") and request.resource_ids:
                    self._logger.info(
                        "No instance IDs in provider_data, searching by resource IDs: %s",
                        request.resource_ids,
                    )
                    return self._find_instances_by_resource_ids(request, request.resource_ids)
                else:
                    self._logger.info(
                        "No instance IDs or resource IDs found in request %s",
                        request.request_id,
                    )
                    return []

            # Get resource ID from provider_data (preferred) or domain field or fallback to metadata
            resource_id = (
                request.provider_data.get("reservation_id")
                if hasattr(request, "provider_data") and request.provider_data
                else (request.resource_ids[0] if getattr(request, "resource_ids", None) else "")
            )

            # Fallback to metadata for backward compatibility
            if not resource_id:
                metadata = getattr(request, "metadata", {}) or {}
                resource_id = (
                    metadata.get("run_instances_resource_id")
                    or metadata.get("reservation_id")
                    or ""
                )

            # Get detailed instance information using instance IDs
            instance_details = self._get_instance_details(
                instance_ids,
                request_id=str(request.request_id),
                resource_id=resource_id,
                provider_api="RunInstances",
            )

            return self._format_instance_data(
                instance_details, resource_id, self._resolve_provider_api(request)
            )

        except Exception as e:
            self._logger.error("Unexpected error checking RunInstances status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check RunInstances status: {e!s}")

    def _find_instances_by_resource_ids(
        self, request: Request, resource_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Find instances using resource IDs (reservation IDs for RunInstances)."""
        try:
            all_instances: list[dict[str, Any]] = []

            for resource_id in resource_ids:
                try:
                    response = self.aws_client.ec2_client.describe_instances(
                        Filters=[{"Name": "reservation-id", "Values": [resource_id]}]
                    )

                    instance_ids = []
                    for reservation in response.get("Reservations", []):
                        instance_ids.extend(
                            instance["InstanceId"] for instance in reservation.get("Instances", [])
                        )

                    if instance_ids:
                        detailed_instances = self._get_instance_details(
                            instance_ids,
                            request_id=str(request.request_id),
                            resource_id=resource_id,
                            provider_api="RunInstances",
                        )
                        formatted = self._format_instance_data(
                            detailed_instances, resource_id, self._resolve_provider_api(request)
                        )
                        all_instances.extend(formatted)

                except ClientError as e:
                    if e.response["Error"]["Code"] == "InvalidReservationID.NotFound":
                        self._logger.warning("Reservation ID %s not found", resource_id)
                        continue
                    if "Filter dicts have not been implemented" in str(e):
                        self._logger.info(
                            "Reservation-id filter not supported (likely moto), falling back to describe all instances"
                        )
                        return self._find_instances_by_tags_fallback(request, resource_ids)
                    raise
                except Exception as e:
                    if "Filter dicts have not been implemented" in str(e):
                        self._logger.info(
                            "Reservation-id filter not supported (likely moto), falling back to describe all instances"
                        )
                        return self._find_instances_by_tags_fallback(request, resource_ids)
                    raise

            self._logger.info(
                "Normalized %s instances for resource IDs: %s",
                len(all_instances),
                resource_ids,
            )

            return all_instances

        except Exception as e:
            self._logger.error(
                "Error finding instances by resource IDs %s: %s", resource_ids, str(e)
            )
            raise AWSInfrastructureError(
                f"Failed to find instances by resource IDs {resource_ids}: {e!s}"
            )

    def _find_instances_by_tags_fallback(
        self, request: Request, resource_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fallback method to find instances by tags when reservation-id filter is not supported."""
        try:
            response = self.aws_client.ec2_client.describe_instances()
            formatted_instances: list[dict[str, Any]] = []

            for reservation in response.get("Reservations", []):
                reservation_id = reservation.get("ReservationId")
                if reservation_id not in resource_ids:
                    continue

                instance_ids = [
                    instance.get("InstanceId") for instance in reservation.get("Instances", [])
                ]
                instance_ids = [instance_id for instance_id in instance_ids if instance_id]

                if not instance_ids:
                    continue

                detailed_instances = self._get_instance_details(
                    instance_ids,
                    request_id=str(request.request_id),
                    resource_id=reservation_id,
                    provider_api="RunInstances",
                )
                formatted_instances.extend(
                    self._format_instance_data(
                        detailed_instances, reservation_id, self._resolve_provider_api(request)
                    )
                )

            return formatted_instances

        except Exception as e:
            self._logger.error("FALLBACK: Fallback method failed to find instances: %s", e)
            return []

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
        request_id: str = "",
    ) -> None:
        """
        Release hosts created by RunInstances.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity)
            request_id: Original provisioning request ID, used for launch template cleanup
        """
        try:
            if not machine_ids:
                self._logger.warning("No instance IDs provided for RunInstances termination")
                return

            # Use consolidated AWS operations utility for instance termination
            self.aws_ops.terminate_instances_with_fallback(
                machine_ids, self._request_adapter, "RunInstances instances"
            )
            self._logger.info("Terminated RunInstances instances: %s", machine_ids)

            if request_id:
                self._cleanup_on_zero_capacity("run_instances", request_id)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to release RunInstances resources: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error releasing RunInstances resources: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release RunInstances resources: {e!s}")

    def cancel_resource(self, resource_id: str, request_id: str) -> dict[str, Any]:
        """Cancel a RunInstances reservation by terminating its instances.

        RunInstances has no fleet-level cancel concept — instances are
        terminated directly. If no instance IDs are available from the
        resource_id, this is a no-op (instances may not have launched yet).

        Args:
            resource_id: The reservation ID (used to locate instances).
            request_id: The ORB request ID, used for launch template cleanup.

        Returns:
            Dictionary with ``status`` of ``"success"`` or ``"error"``.
        """
        try:
            # Locate instances belonging to this reservation
            instance_ids: list[str] = []
            try:
                response = self.aws_client.ec2_client.describe_instances(
                    Filters=[{"Name": "reservation-id", "Values": [resource_id]}]
                )
                for reservation in response.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        iid = instance.get("InstanceId")
                        if iid:
                            instance_ids.append(iid)
            except Exception as e:
                self._logger.warning(
                    "Could not look up instances for reservation %s: %s", resource_id, e
                )

            if instance_ids:
                self.aws_ops.terminate_instances_with_fallback(
                    instance_ids, self._request_adapter, f"RunInstances reservation {resource_id}"
                )
                self._logger.info(
                    "Terminated RunInstances reservation %s instances: %s",
                    resource_id,
                    instance_ids,
                )
            else:
                self._logger.info(
                    "No instances found for RunInstances reservation %s — nothing to terminate",
                    resource_id,
                )

            if request_id:
                self._cleanup_on_zero_capacity("run_instances", request_id)

            return {
                "status": "success",
                "message": f"RunInstances reservation {resource_id} cancelled",
            }
        except Exception as e:
            self._logger.error("Failed to cancel RunInstances reservation %s: %s", resource_id, e)
            return {
                "status": "error",
                "message": f"Failed to cancel RunInstances reservation {resource_id}: {e!s}",
            }

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Get example templates for RunInstances handler."""
        return [
            AWSTemplate(
                template_id="RunInstances-OnDemand",
                name="Run Instances On-Demand",
                description="On-demand instances using RunInstances API",
                provider_api="RunInstances",
                machine_types={"t3.medium": 1},
                max_instances=5,
                price_type="ondemand",
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "dev"},
            ),
            AWSTemplate(
                template_id="RunInstances-Spot",
                name="Run Instances Spot",
                description="Spot instances using RunInstances API",
                provider_api="RunInstances",
                machine_types={"t3.medium": 1},
                max_instances=10,
                price_type="spot",
                max_price=0.05,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "dev"},
            ),
        ]
