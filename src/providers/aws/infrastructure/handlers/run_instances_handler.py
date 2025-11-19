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

from datetime import datetime
from typing import Any, Optional

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.utilities.common.resource_naming import get_resource_prefix
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.base_context_mixin import BaseContextMixin
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class RunInstancesHandler(AWSHandler, BaseContextMixin):
    """Handler for direct EC2 instance operations using RunInstances."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,
        machine_adapter: Optional[AWSMachineAdapter] = None,
        error_handler: ErrorHandlingPort = None,
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
        )

        # Get AWS native spec service from container
        from infrastructure.di.container import get_container

        container = get_container()
        try:
            from providers.aws.infrastructure.services.aws_native_spec_service import (
                AWSNativeSpecService,
            )

            self.aws_native_spec_service = container.get(AWSNativeSpecService)
            # Get config port for package info
            from domain.base.ports.configuration_port import ConfigurationPort

            self.config_port = container.get(ConfigurationPort)

            if self._machine_adapter is None:
                from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter

                self._machine_adapter = container.get(AWSMachineAdapter)
        except Exception:
            # Service not available, native specs disabled
            self.aws_native_spec_service = None
            self.config_port = None

    @handle_infrastructure_exceptions(context="run_instances_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> dict[str, Any]:
        """
        Create EC2 instances using RunInstances to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            resource_id = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_instances_internal(request, aws_template),
                operation_name="run EC2 instances",
                context="RunInstances",
            )

            # Get instance details immediately
            instance_ids = request.metadata.get("instance_ids", [])
            instance_details = self._get_instance_details(instance_ids)
            instances = self._format_instance_data(
                instance_details, resource_id, request, aws_template
            )

            return {
                "success": True,
                "resource_ids": [resource_id],
                "instances": instances,
                "provider_data": {"resource_type": "run_instances"},
            }
        except Exception as e:
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _create_instances_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create RunInstances with pure business logic."""
        # Validate prerequisites
        self._validate_prerequisites(aws_template)

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Store launch template info in request (if request has this method)
        if hasattr(request, "set_launch_template_info"):
            request.set_launch_template_info(
                launch_template_result.template_id, launch_template_result.version
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

        # Extract reservation ID and instance IDs from response
        reservation_id = response.get("ReservationId")
        instance_ids = [instance["InstanceId"] for instance in response.get("Instances", [])]

        if not instance_ids:
            raise AWSInfrastructureError("No instances were created by RunInstances")

        if not reservation_id:
            raise AWSInfrastructureError("No reservation ID returned by RunInstances")

        # Use the actual AWS reservation ID as the resource ID
        resource_id = reservation_id

        # Store instance IDs and reservation ID in request metadata for later retrieval
        if not hasattr(request, "metadata"):
            request.metadata = {}
        request.metadata["instance_ids"] = instance_ids
        request.metadata["reservation_id"] = reservation_id
        request.metadata["run_instances_resource_id"] = resource_id

        self._logger.info(
            "Successfully created %s instances via RunInstances with reservation ID %s: %s",
            len(instance_ids),
            reservation_id,
            instance_ids,
        )

        return resource_id

    def _format_instance_data(
        self,
        instance_details: list[dict[str, Any]],
        resource_id: str,
        request: Request,
        aws_template: Optional[AWSTemplate] = None,
    ) -> list[dict[str, Any]]:
        """Format AWS instance details to standard structure."""
        metadata = getattr(request, "metadata", {}) or {}
        if aws_template and aws_template.provider_api is not None:
            provider_api_value = (
                aws_template.provider_api.value
                if hasattr(aws_template.provider_api, "value")
                else str(aws_template.provider_api)
            )
        else:
            provider_api_value = metadata.get("provider_api", "RunInstances")

        if self._machine_adapter:
            try:
                return [
                    self._machine_adapter.create_machine_from_aws_instance(
                        inst,
                        request_id=str(request.request_id),
                        provider_api=provider_api_value,
                        resource_id=resource_id,
                    )
                    for inst in instance_details
                ]
            except Exception as exc:
                self._logger.error("Failed to normalize instances with machine adapter: %s", exc)
                raise AWSInfrastructureError(
                    "Failed to normalize instance data with AWS machine adapter"
                ) from exc

        return [
            self._build_fallback_machine_payload(inst, resource_id) for inst in instance_details
        ]

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

        return {
            # RunInstances-specific values
            "instance_name": f"{get_resource_prefix('instance')}{request.request_id}",
        }

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
        if aws_template.instance_type:
            params["InstanceType"] = aws_template.instance_type

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
                params["InstanceMarketOptions"]["SpotOptions"] = {
                    "MaxPrice": str(aws_template.max_price)
                }

        # Add additional tags for instances (beyond launch template)
        # Get package name for CreatedBy tag
        created_by = self._get_package_name()

        tag_specifications = [
            {
                "ResourceType": "instance",
                "Tags": [
                    {
                        "Key": "Name",
                        "Value": f"{get_resource_prefix('instance')}{request.request_id}",
                    },
                    {"Key": "RequestId", "Value": str(request.request_id)},
                    {"Key": "TemplateId", "Value": str(aws_template.template_id)},
                    {"Key": "CreatedBy", "Value": created_by},
                    {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
                    {"Key": "ProviderApi", "Value": "RunInstances"},
                ],
            }
        ]

        # Add template tags if any
        if aws_template.tags:
            instance_tags = [{"Key": k, "Value": v} for k, v in aws_template.tags.items()]
            tag_specifications[0]["Tags"].extend(instance_tags)

        params["TagSpecifications"] = tag_specifications

        return params

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check the status of instances created by RunInstances."""
        try:
            # Get instance IDs from request metadata
            instance_ids = request.metadata.get("instance_ids", [])

            if not instance_ids:
                # If no instance IDs in metadata, try to find instances using resource
                # IDs (reservation IDs)
                if hasattr(request, "resource_ids") and request.resource_ids:
                    self._logger.info(
                        "No instance IDs in metadata, searching by resource IDs: %s",
                        request.resource_ids,
                    )
                    return self._find_instances_by_resource_ids(request, request.resource_ids)
                else:
                    self._logger.info(
                        "No instance IDs or resource IDs found in request %s",
                        request.request_id,
                    )
                    return []

            # Get detailed instance information using instance IDs
            instance_details = self._get_instance_details(instance_ids)
            metadata = getattr(request, "metadata", {}) or {}
            resource_id = (
                metadata.get("run_instances_resource_id")
                or metadata.get("reservation_id")
                or (request.resource_ids[0] if getattr(request, "resource_ids", None) else "")
            )

            return self._format_instance_data(instance_details, resource_id, request, None)

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
                        detailed_instances = self._get_instance_details(instance_ids)
                        formatted = self._format_instance_data(
                            detailed_instances, resource_id, request, None
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

                detailed_instances = self._get_instance_details(instance_ids)
                formatted_instances.extend(
                    self._format_instance_data(detailed_instances, reservation_id, request, None)
                )

            return formatted_instances

        except Exception as e:
            self._logger.error("FALLBACK: Fallback method failed to find instances: %s", e)
            return []

    def release_hosts(
        self, machine_ids: list[str], resource_mapping: list[tuple[str, str, int]] = None
    ) -> None:
        """
        Release hosts created by RunInstances.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: List of tuples (instance_id, resource_id or None, desired_capacity) for intelligent resource management
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

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to release RunInstances resources: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error releasing RunInstances resources: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release RunInstances resources: {e!s}")
