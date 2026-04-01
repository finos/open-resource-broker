"""Azure Provider Strategy.

Implements ``ProviderStrategy`` for Azure, routing all seven operation types
to the appropriate handlers via the VMSS / SingleVM infrastructure layer.
"""

from __future__ import annotations

import asyncio
import time
from threading import RLock
from typing import Any, Callable, Optional, cast

from orb.application.services.spot_placement_execution import (
    SpotPlacementExecutionService,
)
from orb.application.services.spot_placement_planner import (
    SpotPlacementPlanner,
)
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from orb.providers.azure.configuration.validator import validate_azure_template
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.adapters.machine_adapter import AzureMachineAdapter
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
)
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.azure.infrastructure.handlers.cyclecloud_handler import CycleCloudHandler
from orb.providers.azure.infrastructure.handlers.single_vm_handler import SingleVMHandler
from orb.providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler
from orb.providers.azure.infrastructure.vmss_cleanup import VmssCleanupCoordinatorFactory
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
from orb.providers.azure.services.capability_service import AzureCapabilityService
from orb.providers.azure.services.health_check_service import AzureHealthCheckService
from orb.providers.azure.services.inventory_service import (
    AzureInventoryService,
    AzureStatusQueryContext,
)
from orb.providers.azure.services.machine_conversion_service import (
    AzureMachineConversionService,
)
from orb.providers.azure.services.provisioning_service import (
    AzureProvisioningService,
    CreateOperationContext,
)
from orb.providers.azure.services.resource_metadata_service import (
    AzureResourceMetadataService,
)
from orb.providers.azure.services.template_catalog_service import AzureTemplateCatalogService
from orb.providers.azure.services.termination_service import AzureTerminationService
from orb.providers.azure.services.termination_dispatch_service import (
    AzureTerminationDispatchService,
)
from orb.providers.azure.services.spot_launch_service import AzureSpotLaunchService
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)

AzureProviderApiRef = AzureProviderApi | str


@injectable
class AzureProviderStrategy(ProviderStrategy):
    """Azure implementation of ``ProviderStrategy``.

    Adapts the Azure infrastructure layer (VMSS / SingleVM handlers,
    AzureClient, AzureResourceManager) to the strategy pattern, enabling
    runtime provider switching and composition with the advanced strategy
    wrappers (fallback, composite, load-balancing).
    """

    def __init__(
        self,
        config: AzureProviderConfig,
        logger: LoggingPort,
        provider_instance_name: str,
        azure_client_resolver: Optional[Callable[[], AzureClient]] = None,
        vmss_cleanup_coordinator_factory: Optional[VmssCleanupCoordinatorFactory] = None,
    ) -> None:
        """Initialise the Azure strategy with config, logger, and optional client resolver."""
        if not isinstance(config, AzureProviderConfig):
            raise ValueError("AzureProviderStrategy requires AzureProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._azure_config = config
        self._provider_instance_name = provider_instance_name
        self._client: Optional[AzureClient] = None
        self._azure_client_resolver = azure_client_resolver
        self._resource_manager: Optional[AzureResourceManager] = None
        self._deployment_service: Optional[Any] = None
        self._handlers: dict[str, AzureHandler] = {}
        self._spot_placement_planner = SpotPlacementPlanner()
        self._spot_placement_execution = SpotPlacementExecutionService()
        self._capability_service = AzureCapabilityService()
        self._health_check_service = AzureHealthCheckService(config=config, logger=logger)
        self._inventory_service = AzureInventoryService(logger=logger)
        self._machine_conversion_service = AzureMachineConversionService(logger=logger)
        self._provisioning_service = AzureProvisioningService()
        self._resource_metadata_service = AzureResourceMetadataService(
            default_resource_group=config.resource_group,
            logger=logger,
        )
        self._spot_launch_service = AzureSpotLaunchService(
            config=config,
            logger=logger,
            planner=self._spot_placement_planner,
            execution_service=self._spot_placement_execution,
        )
        self._template_catalog_service = AzureTemplateCatalogService(logger=logger)
        self._termination_service = AzureTerminationService()
        self._lazy_init_lock = RLock()
        cleanup_coordinator_factory = (
            vmss_cleanup_coordinator_factory or VmssCleanupCoordinatorFactory()
        )
        self._vmss_cleanup_coordinator = cleanup_coordinator_factory.create(
            logger=self._logger,
            get_vmss_member_count=self._current_vmss_member_count,
            vmss_exists=self._vmss_exists,
            begin_delete_vmss=self._begin_delete_vmss,
        )
        self._termination_dispatch_service = AzureTerminationDispatchService(
            logger=logger,
            record_pending_cleanup=self._vmss_cleanup_coordinator.record,
        )

    # ------------------------------------------------------------------
    # Lazy-initialised properties
    # ------------------------------------------------------------------

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "azure"

    @property
    def provider_instance_name(self) -> str:
        """Return the configured name for this provider instance."""
        return self._provider_instance_name

    @property
    def azure_client(self) -> Optional[AzureClient]:
        """Get the Azure client with lazy initialisation."""
        with self._lazy_init_lock:
            if self._client is None:
                self._logger.debug("Creating Azure client on first access")
                if self._azure_client_resolver:
                    try:
                        self._client = self._azure_client_resolver()
                    except Exception as exc:
                        self._logger.warning("Failed to resolve AzureClient lazily: %s", exc, exc_info=True)
                        self._client = None
                else:
                    self._logger.warning("AzureClient resolver not provided")
            return self._client

    @property
    def resource_manager(self) -> Optional[AzureResourceManager]:
        """Get the Azure resource manager with lazy initialisation."""
        with self._lazy_init_lock:
            azure_client = self.azure_client
            if self._resource_manager is None and azure_client:
                self._logger.debug("Creating Azure resource manager on first access")
                self._resource_manager = AzureResourceManager(
                    azure_client=azure_client,
                    config=self._azure_config,
                    logger=self._logger,
                )
            return self._resource_manager

    @property
    def deployment_service(self) -> Optional[Any]:
        """Get the ARM deployment service with lazy initialisation."""
        with self._lazy_init_lock:
            azure_client = self.azure_client
            if self._deployment_service is None and azure_client:
                from orb.providers.azure.infrastructure.services.azure_deployment_service import (
                    AzureDeploymentService,
                )

                self._deployment_service = AzureDeploymentService(
                    azure_client=azure_client,
                    logger=self._logger,
                )
            return self._deployment_service

    @property
    def handlers(self) -> dict[str, AzureHandler]:
        """Get handlers with lazy initialisation."""
        with self._lazy_init_lock:
            azure_client = self.azure_client
            if not self._handlers and azure_client:
                self._logger.debug("Creating Azure handlers on first access")
                machine_adapter = AzureMachineAdapter(azure_client, self._logger)
                self._handlers = {
                    AzureProviderApi.VMSS.value: VMSSHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                        machine_adapter=machine_adapter,
                    ),
                    AzureProviderApi.VMSS_UNIFORM.value: VMSSHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                        machine_adapter=machine_adapter,
                    ),
                    AzureProviderApi.SINGLE_VM.value: SingleVMHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                        machine_adapter=machine_adapter,
                    ),
                    AzureProviderApi.CYCLECLOUD.value: CycleCloudHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                        machine_adapter=machine_adapter,
                    ),
                }
            return self._handlers

    def _build_azure_template_config(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """Coalesce provider-owned and Azure-default fields before AzureTemplate validation."""
        enhanced_config = dict(template_config)

        raw_subnet_id = enhanced_config.get("subnet_id")
        if raw_subnet_id and raw_subnet_id != "default-subnet":
            enhanced_config["subnet_ids"] = [raw_subnet_id]
        elif enhanced_config.get("subnet_ids") == ["default-subnet"]:
            enhanced_config.pop("subnet_ids", None)

        azure_defaults = AzureTemplateExtensionConfig().to_template_defaults()
        for field, value in azure_defaults.items():
            if enhanced_config.get(field) in (None, ""):
                enhanced_config[field] = value

        if enhanced_config.get("resource_group") in (None, "") and self._azure_config.resource_group:
            enhanced_config["resource_group"] = self._azure_config.resource_group
        if enhanced_config.get("location") in (None, "") and self._azure_config.region:
            # Provider config follows the shared ``region`` interface, but the
            # Azure template model is Azure-native and owns ``location``.
            enhanced_config["location"] = self._azure_config.region
        if enhanced_config.get("subscription_id") in (None, "") and self._azure_config.subscription_id:
            enhanced_config["subscription_id"] = self._azure_config.subscription_id

        enhanced_config.setdefault("provider_type", "azure")
        enhanced_config.setdefault("provider_name", self.provider_instance_name)
        return enhanced_config

    def _build_spot_placement_plan(
        self,
        azure_template: AzureTemplate,
        count: int,
    ) -> list[Any]:
        """Compatibility wrapper for tests and callers that patch this seam."""
        return self._spot_launch_service.build_spot_placement_plan(
            azure_template=azure_template,
            count=count,
            azure_client=self.azure_client,
        )

    def _is_capacity_like_failure(self, child_result: dict[str, Any]) -> bool:
        """Compatibility wrapper for tests and callers that patch this seam."""
        return self._spot_launch_service.is_capacity_like_failure(child_result)

    # ------------------------------------------------------------------
    # ProviderStrategy contract
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Mark the strategy as initialised and ready to execute operations."""
        self._logger.info(
            "Azure provider strategy ready for region: %s",
            self._azure_config.region,
        )
        self._initialized = True
        return True

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        self._logger.debug(
            "azure_provider_strategy execute_operation [%s, %s, %s]",
            operation.operation_type,
            operation.parameters,
            operation.context,
        )
        if not self._initialized:
            return ProviderResult.error_result(
                "Azure provider strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()
        is_dry_run = bool(operation.context and operation.context.get("dry_run", False))

        try:
            from orb.providers.azure.infrastructure.dry_run_adapter import azure_dry_run_context

            if is_dry_run:
                # Activates the global dry-run flag checked by is_dry_run_active().
                # Individual operations short-circuit via early returns; the context
                # manager is the integration point for future SDK-level mocking
                # (analogous to the AWS moto adapter).
                with azure_dry_run_context():
                    result = await self._execute_operation_internal(operation)
            else:
                result = await self._execute_operation_internal(operation)

            execution_time_ms = int((time.time() - start_time) * 1000)
            if result.metadata is None:
                result.metadata = {}
            result.metadata.update({
                "execution_time_ms": execution_time_ms,
                "provider": "azure",
                "dry_run": is_dry_run,
            })
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._logger.error("Azure operation failed: %s", exc)
            return ProviderResult.error_result(
                f"Azure operation failed: {exc!s}",
                "OPERATION_FAILED",
                {
                    "execution_time_ms": execution_time_ms,
                    "provider": "azure",
                    "dry_run": is_dry_run,
                },
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Get Azure provider capabilities."""
        return self._capability_service.get_capabilities()

    def check_health(self) -> ProviderHealthStatus:
        """Check Azure connectivity and return the current health status."""
        return self._health_check_service.check_health(self.azure_client)

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate Azure provider name."""
        return self._capability_service.generate_provider_name(config)

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse Azure provider name."""
        return self._capability_service.parse_provider_name(provider_name)

    def get_provider_name_pattern(self) -> str:
        """Return the regex pattern used to validate Azure provider names."""
        return self._capability_service.get_provider_name_pattern()

    def get_supported_apis(self) -> list[str]:
        """Get supported Azure provider APIs."""
        return self._capability_service.get_supported_apis()

    def cleanup(self) -> None:
        """Release Azure client resources and reset all lazily initialised state."""
        if self._client is not None:
            self._client.close()
        self._client = None
        self._resource_manager = None
        self._deployment_service = None
        self._handlers = {}
        self._vmss_cleanup_coordinator.clear()
        self._initialized = False
        self._logger.debug("Azure provider cleaned up")

    @staticmethod
    def _error_result(
        message: str,
        default_code: str,
        exc: Exception,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProviderResult:
        """Build an error result, preserving Azure-specific error codes and details."""
        # getattr: exc may be any Exception subclass, not just AzureError.
        error_code = getattr(exc, "error_code", None) or default_code
        merged = dict(metadata or {})
        merged["error_class"] = type(exc).__name__
        if hasattr(exc, "to_dict"):
            merged["provider_error"] = exc.to_dict()
        return ProviderResult.error_result(message, error_code, merged)

    @staticmethod
    def _normalize_provider_api_value(provider_api: Any) -> Any:
        """Prefer the Azure enum internally and keep unknown values unchanged."""
        if isinstance(provider_api, AzureProviderApi):
            return provider_api
        if isinstance(provider_api, str):
            try:
                return AzureProviderApi(provider_api)
            except ValueError:
                return provider_api
        return provider_api

    # ------------------------------------------------------------------
    # Internal operation dispatch
    # ------------------------------------------------------------------

    async def _execute_operation_internal(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return await self._handle_create_instances(operation)
        elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            return self._handle_terminate_instances(operation)
        elif operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
            return self._handle_get_instance_status(operation)
        elif operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
            return await self._handle_describe_resource_instances(operation)
        elif operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
            return self._handle_validate_template(operation)
        elif operation.operation_type == ProviderOperationType.GET_AVAILABLE_TEMPLATES:
            return self._handle_get_available_templates(operation)
        elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return self._handle_health_check(operation)
        else:
            return ProviderResult.error_result(
                f"Unsupported operation: {operation.operation_type}",
                "UNSUPPORTED_OPERATION",
            )

    # ------------------------------------------------------------------
    # CREATE_INSTANCES
    # ------------------------------------------------------------------

    @staticmethod
    def _provider_api_key(provider_api: AzureProviderApiRef) -> str:
        return AzureProvisioningService.provider_api_key(provider_api)

    @staticmethod
    def _resolve_operation_provider_api(
        operation: ProviderOperation,
    ) -> Optional[AzureProviderApiRef]:
        provider_api = operation.parameters.get("provider_api")
        if provider_api in (None, ""):
            return None
        return cast(
            AzureProviderApiRef,
            AzureProviderStrategy._normalize_provider_api_value(provider_api),
        )

    async def _handle_create_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        template_config: dict[str, Any] = {}
        provider_api_key: Optional[str] = None
        try:
            create_context = self._provisioning_service.build_create_operation_context(
                operation=operation,
                normalize_provider_api=self._normalize_provider_api_value,
                resolve_handler=lambda api: self.handlers.get(self._provider_api_key(api)),
                build_template=lambda tc: AzureTemplate.model_validate(
                    self._build_azure_template_config(tc)
                ),
            )
            if isinstance(create_context, ProviderResult):
                return create_context

            template_config = create_context.template_config
            provider_api_key = create_context.provider_api_key

            if bool(operation.context and operation.context.get("dry_run", False)):
                return AzureProvisioningService.create_instances_dry_run_result(create_context)

            if self._spot_launch_service.should_use_spot_placement(create_context.azure_template):
                api_key = self._provider_api_key(create_context.provider_api)
                return self._spot_launch_service.execute_planned_spot_launches(
                    azure_template=create_context.azure_template,
                    provider_api=create_context.provider_api,
                    provider_api_key=api_key,
                    count=create_context.count,
                    template_config=template_config,
                    operation=operation,
                    provider_instance_name=self.provider_instance_name,
                    handler=self.handlers.get(api_key),
                    azure_client=self.azure_client,
                    plan_override=self._build_spot_placement_plan(
                        create_context.azure_template, create_context.count
                    ),
                    capacity_like_failure_checker=self._is_capacity_like_failure,
                )

            request = self._provisioning_service.build_create_request(
                operation=operation,
                azure_template=create_context.azure_template,
                count=create_context.count,
                provider_api=create_context.provider_api,
                provider_instance_name=self.provider_instance_name,
            )
            return self._provisioning_service.execute_create_handler(
                create_context=create_context,
                request=request,
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_details = extract_azure_error_details(exc)
            fleet_error = {
                "error_code": canonical_azure_error_code(exc),
                "error_message": error_details["message"],
                "status_code": error_details["status_code"],
                "raw_error_code": error_details["raw_error_code"],
            }
            return self._error_result(
                f"Failed to create instances: {exc!s}",
                "CREATE_INSTANCES_ERROR", exc,
                metadata={
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api_key,
                    "provider_data": {"fleet_errors": [fleet_error]},
                },
            )

    # ------------------------------------------------------------------
    # TERMINATE_INSTANCES
    # ------------------------------------------------------------------

    def _handle_terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        self._logger.debug("_handle_terminate_instances")
        try:
            is_dry_run = bool(operation.context and operation.context.get("dry_run", False))
            termination_context = self._termination_service.build_termination_operation_context(
                operation=operation,
                is_dry_run=is_dry_run,
                resolve_operation_provider_api=self._resolve_operation_provider_api,
                provider_api_key=self._provider_api_key,
                handlers=self.handlers,
                group_instance_ids_by_resource=self._inventory_service.group_instance_ids_by_resource,
                build_cyclecloud_request_metadata=self._inventory_service.build_cyclecloud_request_metadata,
                resolve_operation_resource_group=lambda op: self._inventory_service.resolve_operation_resource_group(
                    op, self._azure_config.resource_group,
                ),
            )
            if isinstance(termination_context, ProviderResult):
                return termination_context

            if is_dry_run:
                return self._termination_service.terminate_instances_dry_run_result(
                    termination_context
                )

            termination_provider_data = self._termination_dispatch_service.dispatch(
                handler=termination_context.handler,
                instance_ids=termination_context.instance_ids,
                grouped_resource_mapping=termination_context.grouped_resource_mapping,
                default_resource_id=termination_context.default_resource_id,
                context=termination_context.release_context,
            )

            return self._termination_service.terminate_instances_result(
                instance_ids=termination_context.instance_ids,
                termination_provider_data=termination_provider_data,
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._error_result(
                f"Failed to terminate instances: {exc!s}",
                "TERMINATE_INSTANCES_ERROR", exc,
            )

    # ------------------------------------------------------------------
    # GET_INSTANCE_STATUS
    # ------------------------------------------------------------------

    def _build_handler_request(
        self,
        operation: ProviderOperation,
        resource_group: Optional[str],
        resource_ids: list[str],
        additional_metadata: Optional[dict[str, Any]] = None,
    ) -> Request:

        metadata = self._inventory_service.build_cyclecloud_request_metadata(
            operation=operation,
            resource_group=resource_group,
        )
        if additional_metadata:
            metadata.update(additional_metadata)

        request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=operation.parameters.get("template_id", "unknown"),
            machine_count=1,
            provider_type="azure",
            provider_name=self.provider_instance_name,
            request_id=request_id,
            metadata=metadata,
        )
        request.resource_ids = resource_ids
        return request

    def _get_instance_status_via_handlers(
        self,
        *,
        operation: ProviderOperation,
        instance_ids: list[str],
        resource_group: str,
    ) -> Optional[list[dict[str, Any]]]:
        provider_api = self._resolve_operation_provider_api(operation)
        raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
        grouped_resource_mapping = self._inventory_service.group_instance_ids_by_resource(
            instance_ids, raw_resource_mapping
        )

        if not provider_api:
            return None

        handler = self.handlers.get(self._provider_api_key(provider_api))
        if not handler and provider_api == AzureProviderApi.VMSS_UNIFORM:
            handler = self.handlers.get(AzureProviderApi.VMSS.value)
        if not handler and not grouped_resource_mapping:
            return None

        if provider_api == AzureProviderApi.SINGLE_VM and handler:
            return handler.check_hosts_status(
                self._build_handler_request(operation, resource_group, instance_ids)
            )

        if grouped_resource_mapping:
            all_results: list[dict[str, Any]] = []
            seen_instance_ids: set[str] = set()
            for resource_id, mapped_ids in grouped_resource_mapping.items():
                group_handler = handler
                if not group_handler and provider_api:
                    group_handler = self.handlers.get(self._provider_api_key(provider_api))
                if not group_handler:
                    continue

                extra_metadata: dict[str, Any] = {}
                if provider_api == AzureProviderApi.CYCLECLOUD:
                    extra_metadata["node_ids"] = mapped_ids
                request = self._build_handler_request(
                    operation, resource_group, [resource_id], extra_metadata
                )
                for machine in self._inventory_service.filter_status_results(
                    group_handler.check_hosts_status(request), mapped_ids
                ):
                    machine_id = str(machine.get("instance_id"))
                    if machine_id not in seen_instance_ids:
                        all_results.append(machine)
                        seen_instance_ids.add(machine_id)

            if all_results:
                return all_results

        resource_id = operation.parameters.get("resource_id")
        if not handler or not resource_id:
            return None

        extra_metadata = {}
        if provider_api == AzureProviderApi.CYCLECLOUD:
            extra_metadata = {"node_ids": instance_ids}
        request = self._build_handler_request(
            operation,
            resource_group,
            instance_ids if provider_api == AzureProviderApi.SINGLE_VM else [resource_id],
            extra_metadata,
        )
        if provider_api == AzureProviderApi.SINGLE_VM:
            return handler.check_hosts_status(request)
        return self._inventory_service.filter_status_results(
            handler.check_hosts_status(request), instance_ids
        )

    def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        try:
            instance_ids = operation.parameters.get("instance_ids", [])
            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for status query",
                    "MISSING_INSTANCE_IDS",
                )

            resource_group = self._inventory_service.resolve_operation_resource_group(
                operation, self._azure_config.resource_group,
            )
            if not resource_group:
                return ProviderResult.error_result(
                    "resource_group is required for status query",
                    "MISSING_RESOURCE_GROUP",
                )

            provider_api = self._resolve_operation_provider_api(operation)

            if bool(operation.context and operation.context.get("dry_run", False)):
                return ProviderResult.success_result(
                    {
                        "instances": [
                            {
                                "instance_id": iid,
                                "status": "unknown",
                                "provider_type": "azure",
                                "provider_data": {"dry_run": True},
                            }
                            for iid in instance_ids
                        ],
                        "queried_count": len(instance_ids),
                    },
                    {
                        "operation": "get_instance_status",
                        "instance_ids": instance_ids,
                        "method": "dry_run",
                        "provider_data": {"dry_run": True},
                    },
                )

            self._restore_pending_resource_cleanups(operation)

            handler_machines = self._get_instance_status_via_handlers(
                operation=operation,
                instance_ids=instance_ids,
                resource_group=resource_group,
            )
            if handler_machines is not None:
                is_vmss = provider_api in (
                    AzureProviderApi.VMSS,
                    AzureProviderApi.VMSS_UNIFORM,
                )
                result = ProviderResult.success_result(
                    {
                        "instances": handler_machines,
                        "queried_count": len(instance_ids),
                    },
                    {
                        "operation": "get_instance_status",
                        "instance_ids": instance_ids,
                        "method": "handler",
                    },
                )
                if is_vmss:
                    status_resource_ids = self._inventory_service.status_resource_ids(
                        operation, instance_ids
                    )
                    if status_resource_ids:
                        self._vmss_cleanup_coordinator.reconcile(
                            resource_group=resource_group,
                            resource_ids=status_resource_ids,
                            observed_ids=self._inventory_service.observed_status_ids(
                                handler_machines
                            ),
                        )
                    result.metadata.update(
                        self._vmss_cleanup_coordinator.status_metadata(
                            resource_group=resource_group,
                            resource_ids=status_resource_ids,
                        )
                    )
                return result

            status_context = AzureStatusQueryContext(
                instance_ids=instance_ids,
                resource_group=resource_group,
                provider_api=provider_api,
            )
            return self._inventory_service.sdk_status_result(
                status_context=status_context,
                azure_client=self.azure_client,
                machine_conversion_service=self._machine_conversion_service,
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._error_result(
                f"Failed to get instance status: {exc!s}",
                "GET_INSTANCE_STATUS_ERROR", exc,
            )

    def _restore_pending_resource_cleanups(self, operation: ProviderOperation) -> None:
        """Rebuild pending resource cleanup state from durable request metadata."""
        self._vmss_cleanup_coordinator.restore_from_request_metadata(
            self._inventory_service.request_metadata(operation)
        )

    def _current_vmss_member_count(self, *, resource_group: str, vmss_name: str) -> Optional[int]:
        if not self.resource_manager:
            return None

        return self.resource_manager.get_vmss_member_count(
            resource_group=resource_group,
            vmss_name=vmss_name,
        )

    def _vmss_exists(self, *, resource_group: str, vmss_name: str) -> Optional[bool]:
        if not self.resource_manager:
            return None

        return self.resource_manager.vmss_exists(
            resource_group=resource_group,
            vmss_name=vmss_name,
        )

    def _begin_delete_vmss(self, *, resource_group: str, vmss_name: str) -> None:
        azure_client = self.azure_client
        if not azure_client:
            raise RuntimeError("Azure client not available for VMSS cleanup delete")
        azure_client.compute_client.virtual_machine_scale_sets.begin_delete(
            resource_group_name=resource_group,
            vm_scale_set_name=vmss_name,
        )

    # ------------------------------------------------------------------
    # DESCRIBE_RESOURCE_INSTANCES
    # ------------------------------------------------------------------

    def _describe_resource_instances(
        self,
        *,
        operation: ProviderOperation,
        provider_api: AzureProviderApi | str,
        provider_api_key: str,
        resource_group: Optional[str],
        fail_on_partial_status_error: bool = False,
    ) -> ProviderResult:
        resource_ids = operation.parameters.get("resource_ids", [])
        handler = self.handlers.get(provider_api_key)
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )

        extra_metadata: dict[str, Any] = {}
        if provider_api == AzureProviderApi.SINGLE_VM:
            deployment_name = self._inventory_service.request_metadata(operation).get(
                "deployment_name"
            )
            if deployment_name not in (None, ""):
                extra_metadata["deployment_name"] = str(deployment_name)
        if fail_on_partial_status_error:
            extra_metadata["fail_on_partial_status_error"] = True

        request = self._build_handler_request(
            operation, resource_group, resource_ids, extra_metadata or None
        )

        instance_details = handler.check_hosts_status(request)

        if not instance_details:
            metadata: dict[str, Any] = {
                "operation": "describe_resource_instances",
                "resource_ids": resource_ids,
                "provider_api": provider_api_key,
                "handler_used": provider_api_key,
                "instance_count": 0,
            }
            if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
                vmss_errors: list[dict[str, Any]] = []
                if resource_group and hasattr(handler, "get_vmss_resource_errors"):
                    for resource_id in resource_ids:
                        for error in handler.get_vmss_resource_errors(
                            resource_group, resource_id
                        ):
                            if error not in vmss_errors:
                                vmss_errors.append(error)
                if vmss_errors:
                    metadata["fleet_errors"] = vmss_errors
                self._resource_metadata_service.augment_vmss_capacity_metadata(
                    metadata,
                    resource_ids,
                    resource_manager=self.resource_manager,
                    resource_group=resource_group,
                )
            elif provider_api == AzureProviderApi.SINGLE_VM:
                self._resource_metadata_service.augment_single_vm_deployment_metadata(
                    metadata,
                    extra_metadata,
                    resource_group=resource_group,
                    deployment_service=self.deployment_service,
                )
            return ProviderResult.success_result({"instances": []}, metadata)

        fleet_errors: list[dict[str, Any]] = []
        for inst in instance_details:
            provider_data = inst.get("provider_data") or {}
            if isinstance(provider_data, dict):
                for error in provider_data.get("fleet_errors") or []:
                    if error not in fleet_errors:
                        fleet_errors.append(error)

        metadata = {
            "operation": "describe_resource_instances",
            "resource_ids": resource_ids,
            "provider_api": provider_api_key,
            "handler_used": provider_api_key,
            "instance_count": len(instance_details),
        }
        if fleet_errors:
            metadata["fleet_errors"] = fleet_errors

        if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
            self._resource_metadata_service.augment_vmss_capacity_metadata(
                metadata,
                resource_ids,
                resource_manager=self.resource_manager,
                resource_group=resource_group,
            )

        self._resource_metadata_service.augment_shortfall_metadata(metadata)

        return ProviderResult.success_result(
            data={"instances": instance_details},
            metadata=metadata,
        )

    async def _handle_describe_resource_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        try:
            resource_ids = operation.parameters.get("resource_ids", [])
            provider_api = self._resolve_operation_provider_api(operation)

            if not resource_ids:
                return ProviderResult.error_result(
                    "Resource IDs are required for instance discovery",
                    "MISSING_RESOURCE_IDS",
                )

            if provider_api in (None, ""):
                return ProviderResult.error_result(
                    "provider_api is required for Azure resource discovery",
                    "MISSING_PROVIDER_API",
                )

            if bool(operation.context and operation.context.get("dry_run", False)):
                return ProviderResult.success_result(
                    {"instances": []},
                    {
                        "operation": "describe_resource_instances",
                        "resource_ids": resource_ids,
                        "provider_api": self._provider_api_key(provider_api),
                        "method": "dry_run",
                        "provider_data": {"dry_run": True},
                    },
                )

            provider_api_key = self._provider_api_key(provider_api)
            resource_group = self._inventory_service.resolve_operation_resource_group(
                operation,
                self._azure_config.resource_group,
            )
            is_vmss = provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM)

            self._restore_pending_resource_cleanups(operation)
            fail_on_partial = is_vmss and self._vmss_cleanup_coordinator.has_pending(
                resource_group=resource_group,
                resource_ids=resource_ids,
            )

            result = self._describe_resource_instances(
                operation=operation,
                provider_api=provider_api,
                provider_api_key=provider_api_key,
                resource_group=resource_group,
                fail_on_partial_status_error=fail_on_partial,
            )

            if result.success:
                instance_details = result.data.get("instances", []) if result.data else []
                self._vmss_cleanup_coordinator.reconcile(
                    resource_group=resource_group,
                    resource_ids=resource_ids,
                    observed_ids=self._inventory_service.observed_status_ids(instance_details),
                )
                if is_vmss:
                    result.metadata.update(
                        self._vmss_cleanup_coordinator.status_metadata(
                            resource_group=resource_group,
                            resource_ids=resource_ids,
                        )
                    )

            return result

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._error_result(
                f"Failed to describe resource instances: {exc!s}",
                "DESCRIBE_RESOURCE_INSTANCES_ERROR", exc,
            )

    # ------------------------------------------------------------------
    # VALIDATE_TEMPLATE
    # ------------------------------------------------------------------

    def _handle_validate_template(self, operation: ProviderOperation) -> ProviderResult:
        try:
            template_config = operation.parameters.get("template_config", {})
            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for validation",
                    "MISSING_TEMPLATE_CONFIG",
                )

            validation_result = validate_azure_template(template_config)

            return ProviderResult.success_result(
                validation_result,
                {"operation": "validate_template", "template_config": template_config},
            )
        except Exception as exc:
            return self._error_result(
                f"Failed to validate template: {exc!s}",
                "VALIDATE_TEMPLATE_ERROR", exc,
            )

    # ------------------------------------------------------------------
    # GET_AVAILABLE_TEMPLATES
    # ------------------------------------------------------------------

    def _handle_get_available_templates(self, operation: ProviderOperation) -> ProviderResult:
        try:
            templates = self._template_catalog_service.get_available_templates()
            return ProviderResult.success_result(
                {"templates": templates, "count": len(templates)},
                {"operation": "get_available_templates"},
            )
        except Exception as exc:
            return ProviderResult.error_result(
                f"Failed to get available templates: {exc!s}",
                "GET_TEMPLATES_ERROR",
            )

    # ------------------------------------------------------------------
    # HEALTH_CHECK
    # ------------------------------------------------------------------

    def _handle_health_check(self, operation: ProviderOperation) -> ProviderResult:
        health_status = self.check_health()
        return ProviderResult.success_result(
            {
                "is_healthy": health_status.is_healthy,
                "status_message": health_status.status_message,
                "response_time_ms": health_status.response_time_ms,
            },
            {"operation": "health_check"},
        )

    # ------------------------------------------------------------------
    # String representations
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        return (
            f"AzureProviderStrategy(region={self._azure_config.region}, "
            f"initialized={self._initialized})"
        )
