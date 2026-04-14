"""Azure Provider Strategy.

Implements ``ProviderStrategy`` for Azure, routing all seven operation types
to the appropriate handlers via the VMSS / SingleVM infrastructure layer.
"""

from __future__ import annotations

import asyncio
import time
from threading import Condition, RLock
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional

from orb.application.services.spot_placement_execution import (
    SpotPlacementExecutionService,
)
from orb.application.services.spot_placement_planner import (
    SpotPlacementPlanner,
)
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from orb.providers.azure.configuration.validator import validate_azure_template
from orb.providers.azure.capabilities import (
    get_supported_api_capabilities,
    get_supported_apis,
)
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions import AzureValidationError
from orb.providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
)
from orb.providers.azure.infrastructure.vmss_cleanup import VmssCleanupCoordinator
from orb.providers.azure.services.health_check_service import AzureHealthCheckService
from orb.providers.azure.services.cyclecloud_request_context_service import (
    CycleCloudRequestLookup,
    resolve_cyclecloud_request_metadata,
)
from orb.providers.azure.services.inventory_service import (
    AzureReadOperationContext,
    AzureStatusResult,
    AzureStatusQueryContext,
    build_read_handler_request,
    build_read_operation_context,
    filter_status_results,
    group_instance_ids_by_resource,
    normalize_status_results,
    observed_status_ids,
    resolve_operation_resource_group,
    sdk_status_result,
)
from orb.providers.azure.services.machine_conversion_service import (
    AzureMachineConversionService,
)
from orb.providers.azure.services.provisioning_service import (
    AzureProvisioningService,
    create_instances_dry_run_result,
    provider_api_key,
)
from orb.providers.azure.services.resource_metadata_service import (
    AzureResourceMetadataService,
)
from orb.providers.azure.services.spot_launch_service import AzureSpotLaunchService
from orb.providers.azure.services.template_catalog_service import AzureTemplateCatalogService
from orb.providers.azure.services.termination_dispatch_service import (
    AzureTerminationDispatchService,
)
from orb.providers.azure.services.termination_service import AzureTerminationService
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)

if TYPE_CHECKING:
    from orb.providers.azure.infrastructure.azure_client import AzureClient
    from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
    from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
    from orb.providers.azure.infrastructure.services.azure_deployment_service import (
        AzureDeploymentService,
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
        azure_client_resolver: Optional[Callable[[], "AzureClient"]] = None,
        vmss_cleanup_coordinator: Optional[VmssCleanupCoordinator] = None,
        cyclecloud_request_lookup: Optional[CycleCloudRequestLookup] = None,
    ) -> None:
        """Initialise the Azure strategy with config, logger, and optional client resolver."""
        if not isinstance(config, AzureProviderConfig):
            raise ValueError("AzureProviderStrategy requires AzureProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._azure_config = config
        self._provider_instance_name = provider_instance_name
        self._client: Optional["AzureClient"] = None
        self._azure_client_resolver = azure_client_resolver
        self._resource_manager: Optional["AzureResourceManager"] = None
        self._deployment_service: Optional[AzureDeploymentService] = None
        self._handlers: dict[str, "AzureHandler"] = {}
        self._spot_placement_planner = SpotPlacementPlanner()
        self._spot_placement_execution = SpotPlacementExecutionService()
        self._health_check_service = AzureHealthCheckService(config=config, logger=logger)
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
        self._lifecycle_lock = RLock()
        self._lifecycle_condition = Condition(self._lifecycle_lock)
        self._active_operations = 0
        self._cleanup_requested = False
        self._vmss_cleanup_coordinator = vmss_cleanup_coordinator or VmssCleanupCoordinator(
            logger=self._logger,
            get_vmss_member_count=self._current_vmss_member_count,
            vmss_exists=self._vmss_exists,
            begin_delete_vmss=self._begin_delete_vmss,
        )
        self._cyclecloud_request_lookup = cyclecloud_request_lookup
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
    def azure_client(self) -> Optional["AzureClient"]:
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
    def resource_manager(self) -> Optional["AzureResourceManager"]:
        """Get the Azure resource manager with lazy initialisation."""
        with self._lazy_init_lock:
            azure_client = self.azure_client
            if self._resource_manager is None and azure_client:
                self._logger.debug("Creating Azure resource manager on first access")
                from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager

                self._resource_manager = AzureResourceManager(
                    azure_client=azure_client,
                    config=self._azure_config,
                    logger=self._logger,
                )
            return self._resource_manager

    @property
    def deployment_service(self) -> Optional["AzureDeploymentService"]:
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
    def handlers(self) -> dict[str, "AzureHandler"]:
        """Get handlers with lazy initialisation."""
        with self._lazy_init_lock:
            azure_client = self.azure_client
            if not self._handlers and azure_client:
                self._logger.debug("Creating Azure handlers on first access")
                from orb.providers.azure.infrastructure.handlers.cyclecloud_handler import (
                    CycleCloudHandler,
                )
                from orb.providers.azure.infrastructure.handlers.single_vm_handler import (
                    SingleVMHandler,
                )
                from orb.providers.azure.infrastructure.handlers.vmss_handler import (
                    VMSSHandler,
                )

                self._handlers = {
                    AzureProviderApi.VMSS.value: VMSSHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                    ),
                    AzureProviderApi.VMSS_UNIFORM.value: VMSSHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                    ),
                    AzureProviderApi.SINGLE_VM.value: SingleVMHandler(
                        azure_client=azure_client,
                        logger=self._logger,
                    ),
                    AzureProviderApi.CYCLECLOUD.value: CycleCloudHandler(
                        azure_client=azure_client,
                        logger=self._logger,
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
        with self._lifecycle_condition:
            self._cleanup_requested = False
            self._initialized = True
        self._logger.info(
            "Azure provider strategy ready for region: %s",
            self._azure_config.region,
        )
        return True

    def _begin_operation(self) -> bool:
        """Reserve an execution slot unless cleanup has already started."""
        with self._lifecycle_condition:
            if self._cleanup_requested:
                return False
            self._active_operations += 1
            return True

    def _end_operation(self) -> None:
        """Release an execution slot and wake any waiting cleanup path."""
        with self._lifecycle_condition:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._lifecycle_condition.notify_all()

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
        if not self._begin_operation():
            return ProviderResult.error_result(
                "Azure provider strategy is shutting down",
                "STRATEGY_SHUTTING_DOWN",
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
            self._logger.error("Azure operation failed: %s", exc, exc_info=True)
            return ProviderResult.error_result(
                f"Azure operation failed: {exc!s}",
                "OPERATION_FAILED",
                {
                    "execution_time_ms": execution_time_ms,
                    "provider": "azure",
                    "dry_run": is_dry_run,
                },
            )
        finally:
            self._end_operation()

    def get_capabilities(self) -> ProviderCapabilities:
        """Get Azure provider capabilities."""
        # TODO: Keep Azure and AWS capability metadata dynamic together.
        # These example regions / instance types and operational heuristics are
        # still hard-coded in both providers, we should evaluate if they can be made
        # dynamic
        return ProviderCapabilities(
            provider_type="azure",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.GET_AVAILABLE_TEMPLATES,
                ProviderOperationType.HEALTH_CHECK,
            ],
            features={
                "supported_apis": get_supported_apis(),
                "api_capabilities": get_supported_api_capabilities(),
                "instance_management": True,
                "spot_instances": True,
                "fleet_management": True,
                "auto_scaling": True,
                "load_balancing": True,
                "vpc_support": True,
                "security_groups": True,
                "key_pairs": True,
                "tags_support": True,
                "monitoring": True,
                "regions": ["eastus", "eastus2", "westus2", "westeurope", "northeurope"],
                "instance_types": [
                    "Standard_D2s_v5",
                    "Standard_D4s_v5",
                    "Standard_D8s_v5",
                    "Standard_E4s_v5",
                    "Standard_F4s_v2",
                ],
                "max_instances_per_request": 1000,
                "supports_windows": False,
                "supports_linux": True,
            },
            limitations={
                "max_concurrent_requests": 100,
                "rate_limit_per_second": 20,
                "max_instance_lifetime_hours": 8760,
                "requires_vpc": True,
                "requires_key_pair": True,
            },
            performance_metrics={
                "typical_create_time_seconds": 120,
                "typical_terminate_time_seconds": 60,
                "health_check_timeout_seconds": 15,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check Azure connectivity and return the current health status."""
        return self._health_check_service.check_health(self.azure_client)

    def get_available_credential_sources(self) -> list[dict]:
        """Return Azure credential source options."""
        return [{"name": None, "description": "DefaultAzureCredential / managed identity"}]

    def get_credential_requirements(self) -> dict:
        """Azure auth uses ambient credentials and does not require pre-auth prompts."""
        return {}

    def get_operational_requirements(self) -> dict:
        """Return the Azure values init must collect to build a working provider config."""
        return {
            "subscription_id": {"required": True, "description": "Azure subscription ID"},
            "resource_group": {"required": True, "description": "Azure resource group"},
            "region": {"required": True, "description": "Azure location"},
            "client_id": {
                "required": False,
                "prompt": True,
                "description": "Managed identity client ID (optional)",
            },
        }

    def get_default_region(self) -> str:
        """Return the default Azure location for CLI prompts."""
        return "eastus2"

    def get_cli_provider_config(self, args: Any) -> dict[str, Any]:
        """Extract Azure provider config from init CLI args."""
        args_dict = vars(args)
        provider_config: dict[str, Any] = {
            "region": args_dict.get("azure_location") or self.get_default_region(),
        }

        field_map = {
            "subscription_id": "azure_subscription_id",
            "resource_group": "azure_resource_group",
            "client_id": "azure_client_id",
        }
        for config_key, arg_name in field_map.items():
            value = args_dict.get(arg_name)
            if value not in (None, ""):
                provider_config[config_key] = value

        cyclecloud: dict[str, Any] = {}
        cyclecloud_field_map = {
            "url": "azure_cyclecloud_url",
            "credential_path": "azure_cyclecloud_credential_path",
            "auth_mode": "azure_cyclecloud_auth_mode",
            "aad_scope": "azure_cyclecloud_aad_scope",
        }
        for config_key, arg_name in cyclecloud_field_map.items():
            value = args_dict.get(arg_name)
            if value not in (None, ""):
                cyclecloud[config_key] = value

        if args_dict.get("azure_cyclecloud_verify_ssl"):
            cyclecloud["verify_ssl"] = True
        elif args_dict.get("azure_cyclecloud_no_verify_ssl"):
            cyclecloud["verify_ssl"] = False

        if cyclecloud:
            provider_config["cyclecloud"] = cyclecloud

        return provider_config

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Validate Azure credentials by performing a health check."""
        del credential_source
        health = self._health_check_service.check_health(self.azure_client)
        if health.is_healthy:
            return {"success": True}
        return {
            "success": False,
            "error": health.status_message,
            "details": health.error_details or {},
        }

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate Azure provider name."""
        subscription_id = config.get("subscription_id", "default")
        region = config.get("region", "eastus")
        return f"azure_{subscription_id}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse Azure provider name."""
        parts = provider_name.split("_")
        return {
            "type": parts[0] if len(parts) > 0 else "azure",
            "subscription_id": parts[1] if len(parts) > 1 else "default",
            "region": parts[2] if len(parts) > 2 else "eastus2",
        }

    def get_provider_name_pattern(self) -> str:
        """Return the regex pattern used to validate Azure provider names."""
        return "{type}_{subscription_id}_{region}"

    def get_supported_apis(self) -> list[str]:
        """Get supported Azure provider APIs."""
        return get_supported_apis()

    def cleanup(self) -> None:
        """Release Azure client resources and reset all lazily initialised state."""
        with self._lifecycle_condition:
            self._cleanup_requested = True
            while self._active_operations > 0:
                self._lifecycle_condition.wait()

            client = self._client
            self._client = None
            self._resource_manager = None
            self._deployment_service = None
            self._handlers = {}
            self._vmss_cleanup_coordinator.clear()
            self._initialized = False

        if client is not None:
            client.close()
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
        return provider_api_key(provider_api)

    @staticmethod
    def _resolve_operation_provider_api(
        operation: ProviderOperation,
    ) -> Optional[AzureProviderApiRef]:
        provider_api = operation.parameters.get("provider_api")
        if provider_api in (None, ""):
            return None
        normalized_provider_api = AzureProviderStrategy._normalize_provider_api_value(provider_api)
        if isinstance(normalized_provider_api, (AzureProviderApi, str)):
            return normalized_provider_api
        return None

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

            template_config = create_context.template_config
            provider_api_key = create_context.provider_api_key

            if bool(operation.context and operation.context.get("dry_run", False)):
                return create_instances_dry_run_result(create_context)

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
        except AzureValidationError as exc:
            return self._error_result(
                str(exc),
                "CREATE_INSTANCES_ERROR",
                exc,
            )
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
            operation = self._resolve_cyclecloud_operation(operation)
            is_dry_run = bool(operation.context and operation.context.get("dry_run", False))
            termination_context = self._termination_service.build_termination_operation_context(
                operation=operation,
                is_dry_run=is_dry_run,
                resolve_operation_provider_api=self._resolve_operation_provider_api,
                provider_api_key=self._provider_api_key,
                handlers=self.handlers,
                group_instance_ids_by_resource=group_instance_ids_by_resource,
                resolve_operation_resource_group=lambda op: resolve_operation_resource_group(
                    op, self._azure_config.resource_group,
                ),
            )

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
        except AzureValidationError as exc:
            return self._error_result(
                str(exc),
                "TERMINATE_INSTANCES_ERROR",
                exc,
            )
        except Exception as exc:
            return self._error_result(
                f"Failed to terminate instances: {exc!s}",
                "TERMINATE_INSTANCES_ERROR", exc,
            )

    def _resolve_cyclecloud_operation(self, operation: ProviderOperation) -> ProviderOperation:
        """Merge durable CycleCloud follow-up context into an operation on demand."""
        provider_api = self._resolve_operation_provider_api(operation)
        if provider_api != AzureProviderApi.CYCLECLOUD:
            return operation

        resolved_request_metadata = resolve_cyclecloud_request_metadata(
            operation=operation,
            lookup_request_by_id=self._cyclecloud_request_lookup,
        )
        return ProviderOperation(
            operation_type=operation.operation_type,
            parameters={**operation.parameters, "request_metadata": resolved_request_metadata},
            context=operation.context,
        )

    # ------------------------------------------------------------------
    # GET_INSTANCE_STATUS
    # ------------------------------------------------------------------

    def _get_instance_status_via_handlers(
        self,
        *,
        read_context: AzureReadOperationContext,
    ) -> Optional[list[AzureStatusResult]]:
        provider_api = read_context.provider_api
        grouped_resource_mapping = read_context.grouped_resource_mapping

        if not provider_api:
            return None

        handler = self.handlers.get(self._provider_api_key(provider_api))
        if not handler and provider_api == AzureProviderApi.VMSS_UNIFORM:
            handler = self.handlers.get(AzureProviderApi.VMSS.value)
        if not handler and not grouped_resource_mapping:
            return None

        if provider_api == AzureProviderApi.SINGLE_VM and handler:
            return normalize_status_results(
                handler.check_hosts_status(
                    build_read_handler_request(
                        read_context=read_context,
                        provider_name=self.provider_instance_name,
                        resource_ids=read_context.instance_ids,
                    )
                ),
            )

        if grouped_resource_mapping:
            all_results: list[AzureStatusResult] = []
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
                request = build_read_handler_request(
                    read_context=read_context,
                    provider_name=self.provider_instance_name,
                    resource_ids=[resource_id],
                    additional_metadata=extra_metadata,
                )
                for machine in filter_status_results(
                    normalize_status_results(group_handler.check_hosts_status(request)),
                    mapped_ids,
                ):
                    machine_id = str(machine.get("instance_id"))
                    if machine_id not in seen_instance_ids:
                        all_results.append(machine)
                        seen_instance_ids.add(machine_id)

            if all_results:
                return all_results

        resource_id = read_context.direct_resource_id
        if not handler or not resource_id:
            return None

        extra_metadata = {}
        if provider_api == AzureProviderApi.CYCLECLOUD:
            extra_metadata = {"node_ids": read_context.instance_ids}
        request = build_read_handler_request(
            read_context=read_context,
            provider_name=self.provider_instance_name,
            resource_ids=(
                read_context.instance_ids
                if provider_api == AzureProviderApi.SINGLE_VM
                else [resource_id]
            ),
            additional_metadata=extra_metadata,
        )
        if provider_api == AzureProviderApi.SINGLE_VM:
            return normalize_status_results(handler.check_hosts_status(request))
        return filter_status_results(
            normalize_status_results(handler.check_hosts_status(request)),
            read_context.instance_ids,
        )

    def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        try:
            operation = self._resolve_cyclecloud_operation(operation)
            read_context = build_read_operation_context(
                operation=operation,
                operation_name="get_instance_status",
                default_resource_group=self._azure_config.resource_group,
                normalize_provider_api=self._normalize_provider_api_value,
            )

            resource_group = read_context.resource_group
            if resource_group is None:
                raise RuntimeError(
                    "build_read_operation_context must provide resource_group for status queries"
                )
            instance_ids = read_context.instance_ids
            provider_api = read_context.provider_api

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

            self._restore_pending_resource_cleanups(read_context.request_metadata)

            handler_machines = self._get_instance_status_via_handlers(
                read_context=read_context,
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
                if is_vmss and read_context.resource_ids:
                    self._vmss_cleanup_coordinator.reconcile(
                        resource_group=resource_group,
                        resource_ids=read_context.resource_ids,
                        observed_ids=observed_status_ids(handler_machines),
                    )
                    result.metadata.update(
                        self._vmss_cleanup_coordinator.status_metadata(
                            resource_group=resource_group,
                            resource_ids=read_context.resource_ids,
                        )
                    )
                return result

            status_context = AzureStatusQueryContext(
                instance_ids=instance_ids,
                resource_group=resource_group,
                provider_api=provider_api,
            )
            return sdk_status_result(
                status_context=status_context,
                azure_client=self.azure_client,
                machine_conversion_service=self._machine_conversion_service,
                logger=self._logger,
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._error_result(
                f"Failed to get instance status: {exc!s}",
                "GET_INSTANCE_STATUS_ERROR", exc,
            )

    def _restore_pending_resource_cleanups(self, metadata: Mapping[str, object]) -> None:
        """Rebuild pending resource cleanup state from durable request metadata."""
        self._vmss_cleanup_coordinator.restore_from_request_metadata(metadata)

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
        read_context: AzureReadOperationContext,
    ) -> ProviderResult:
        resource_ids = read_context.resource_ids
        provider_api = read_context.provider_api
        provider_api_key = read_context.provider_api_key or ""
        resource_group = read_context.resource_group

        handler = self.handlers.get(provider_api_key)
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )

        extra_metadata: dict[str, Any] = {}
        if provider_api == AzureProviderApi.SINGLE_VM:
            deployment_name = read_context.request_metadata.get("deployment_name")
            if deployment_name not in (None, ""):
                extra_metadata["deployment_name"] = str(deployment_name)
        if read_context.fail_on_partial_status_error:
            extra_metadata["fail_on_partial_status_error"] = True

        request = build_read_handler_request(
            read_context=read_context,
            provider_name=self.provider_instance_name,
            resource_ids=resource_ids,
            additional_metadata=extra_metadata or None,
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
                    read_context.request_metadata,
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
            operation = self._resolve_cyclecloud_operation(operation)
            read_context = build_read_operation_context(
                operation=operation,
                operation_name="describe_resource_instances",
                default_resource_group=self._azure_config.resource_group,
                normalize_provider_api=self._normalize_provider_api_value,
            )
            if bool(operation.context and operation.context.get("dry_run", False)):
                return ProviderResult.success_result(
                    {"instances": []},
                    {
                        "operation": "describe_resource_instances",
                        "resource_ids": read_context.resource_ids,
                        "provider_api": read_context.provider_api_key,
                        "method": "dry_run",
                        "provider_data": {"dry_run": True},
                    },
                )

            provider_api = read_context.provider_api
            resource_group = read_context.resource_group
            is_vmss = provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM)

            self._restore_pending_resource_cleanups(read_context.request_metadata)
            read_context.fail_on_partial_status_error = bool(
                is_vmss and self._vmss_cleanup_coordinator.has_pending(
                    resource_group=resource_group,
                    resource_ids=read_context.resource_ids,
                )
            )

            result = self._describe_resource_instances(
                read_context=read_context,
            )

            if result.success and is_vmss:
                instance_details = result.data.get("instances", []) if result.data else []
                self._vmss_cleanup_coordinator.reconcile(
                    resource_group=resource_group,
                    resource_ids=read_context.resource_ids,
                    observed_ids=observed_status_ids(instance_details),
                )
                if result.metadata is not None:
                    result.metadata.update(
                        self._vmss_cleanup_coordinator.status_metadata(
                            resource_group=resource_group,
                            resource_ids=read_context.resource_ids,
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
