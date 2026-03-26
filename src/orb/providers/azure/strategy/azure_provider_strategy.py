"""Azure Provider Strategy.

Implements ``ProviderStrategy`` for Azure, routing all seven operation types
to the appropriate handlers via the VMSS / SingleVM infrastructure layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, cast

from orb.application.services.spot_placement_planner import (
    PlacementScore,
    PlacementPlanEntry,
    SpotPlacementPlanner,
)
from orb.application.services.spot_placement_execution import (
    SpotPlacementExecutionService,
    build_planned_execution_metadata,
    create_acquire_request,
)
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
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
from orb.providers.azure.infrastructure.services.spot_placement_score_adapter import (
    AzureSpotPlacementScoreAdapter,
)
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)

class _AzureVmWithName(Protocol):
    name: Optional[str]


@dataclass
class PendingVmssCleanup:
    """Provider-owned follow-up state for empty-VMSS cleanup."""

    resource_group: str
    vmss_name: str
    machine_ids: list[str]
    delete_vmss_when_empty: bool
    delete_submitted: bool = False

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> Optional["PendingVmssCleanup"]:
        resource_group = metadata.get("resource_group")
        vmss_name = metadata.get("vmss_name")
        raw_machine_ids = metadata.get("machine_ids", [])
        if resource_group in (None, "") or vmss_name in (None, ""):
            return None
        if not isinstance(raw_machine_ids, list):
            return None

        machine_ids = []
        for machine_id in raw_machine_ids:
            machine_id_str = str(machine_id)
            if machine_id_str and machine_id_str not in machine_ids:
                machine_ids.append(machine_id_str)

        return cls(
            resource_group=str(resource_group),
            vmss_name=str(vmss_name),
            machine_ids=machine_ids,
            delete_vmss_when_empty=bool(metadata.get("delete_vmss_when_empty", False)),
            delete_submitted=bool(metadata.get("delete_submitted", False)),
        )

    def combine_for_same_vmss(self, other: "PendingVmssCleanup") -> "PendingVmssCleanup":
        merged_machine_ids = list(self.machine_ids)
        for machine_id in other.machine_ids:
            if machine_id not in merged_machine_ids:
                merged_machine_ids.append(machine_id)

        return PendingVmssCleanup(
            resource_group=self.resource_group,
            vmss_name=self.vmss_name,
            machine_ids=merged_machine_ids,
            delete_vmss_when_empty=self.delete_vmss_when_empty or other.delete_vmss_when_empty,
            delete_submitted=self.delete_submitted or other.delete_submitted,
        )


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
    ) -> None:
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
        self._pending_vmss_cleanups: dict[tuple[str, str], PendingVmssCleanup] = {}

    # ------------------------------------------------------------------
    # Lazy-initialised properties
    # ------------------------------------------------------------------

    @property
    def provider_type(self) -> str:
        return "azure"

    @property
    def provider_instance_name(self) -> str:
        return self._provider_instance_name

    @property
    def azure_client(self) -> Optional[AzureClient]:
        """Get the Azure client with lazy initialisation."""
        if self._client is None:
            self._logger.debug("Creating Azure client on first access")
            if self._azure_client_resolver:
                try:
                    self._client = self._azure_client_resolver()
                except Exception as exc:
                    self._logger.warning("Failed to resolve AzureClient lazily: %s", exc)
                    self._client = None
            else:
                self._logger.warning("AzureClient resolver not provided")
        return self._client

    @property
    def resource_manager(self) -> Optional[AzureResourceManager]:
        if self._resource_manager is None and self.azure_client:
            self._logger.debug("Creating Azure resource manager on first access")
            self._resource_manager = AzureResourceManager(
                azure_client=self.azure_client,
                config=self._azure_config,
                logger=self._logger,
            )
        return self._resource_manager

    @property
    def deployment_service(self) -> Optional[Any]:
        if self._deployment_service is None and self.azure_client:
            from orb.providers.azure.infrastructure.services.azure_deployment_service import (
                AzureDeploymentService,
            )

            self._deployment_service = AzureDeploymentService(
                azure_client=self.azure_client,
                logger=self._logger,
            )
        return self._deployment_service

    @property
    def handlers(self) -> dict[str, AzureHandler]:
        """Get handlers with lazy initialisation."""
        if not self._handlers and self.azure_client:
            self._logger.debug("Creating Azure handlers on first access")
            machine_adapter = AzureMachineAdapter(self.azure_client, self._logger)
            self._handlers = {
                AzureProviderApi.VMSS.value: VMSSHandler(
                    azure_client=self.azure_client,
                    logger=self._logger,
                    machine_adapter=machine_adapter,
                ),
                AzureProviderApi.VMSS_UNIFORM.value: VMSSHandler(
                    azure_client=self.azure_client,
                    logger=self._logger,
                    machine_adapter=machine_adapter,
                ),
                AzureProviderApi.SINGLE_VM.value: SingleVMHandler(
                    azure_client=self.azure_client,
                    logger=self._logger,
                    machine_adapter=machine_adapter,
                ),
                AzureProviderApi.CYCLECLOUD.value: CycleCloudHandler(
                    azure_client=self.azure_client,
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
            enhanced_config["location"] = self._azure_config.region
        if enhanced_config.get("subscription_id") in (None, "") and self._azure_config.subscription_id:
            enhanced_config["subscription_id"] = self._azure_config.subscription_id

        enhanced_config.setdefault("provider_type", "azure")
        enhanced_config.setdefault("provider_name", self.provider_instance_name)
        return enhanced_config

    @staticmethod
    def _should_use_spot_placement(template: AzureTemplate) -> bool:
        return getattr(template, "allocation_strategy", None) == "spotPlacementScore"

    def _build_spot_placement_plan(
        self,
        azure_template: AzureTemplate,
        count: int,
    ) -> list[PlacementPlanEntry]:
        adapter = AzureSpotPlacementScoreAdapter(
            azure_client=self.azure_client,
            logger=self._logger,
            subscription_id=azure_template.subscription_id or self._azure_config.subscription_id,
            base_location=azure_template.location or self._azure_config.region,
        )
        scores = adapter.score_candidates(requested_count=count, template=azure_template)
        plan = self._spot_placement_planner.create_plan(
            requested_count=count,
            scores=scores,
            split_strategy=azure_template.placement_split_strategy,
            primary_share_percent=azure_template.placement_primary_share_percent,
        )
        if plan:
            return plan

        if scores:
            self._logger.warning(
                "Azure spot placement scores returned no viable candidates; "
                "falling back to template candidate order"
            )
            return self._build_fallback_spot_placement_plan(scores, count)

        return []

    @staticmethod
    def _build_fallback_spot_placement_plan(
        scores: list[PlacementScore],
        requested_count: int,
    ) -> list[PlacementPlanEntry]:
        if requested_count <= 0 or not scores:
            return []

        fallback_scores = [
            replace(
                score,
                approximate=True,
                metadata={
                    **score.metadata,
                    "fallback_reason": "no_viable_provider_scores",
                },
            )
            for score in scores
        ]
        return [
            PlacementPlanEntry(score=fallback_scores[0], planned_count=requested_count),
            *[
                PlacementPlanEntry(score=score, planned_count=0)
                for score in fallback_scores[1:]
            ],
        ]

    @staticmethod
    def _is_capacity_like_failure(child_result: dict[str, Any]) -> bool:
        error_codes = set(child_result.get("error_codes", []))
        return bool(
            error_codes
            & {
                "AllocationFailed",
                "ZonalAllocationFailed",
                "SkuNotAvailable",
                "OverconstrainedAllocationRequest",
            }
        )

    @staticmethod
    def _clone_template_for_plan_entry(
            azure_template: AzureTemplate,
        plan_entry: PlacementPlanEntry,
    ) -> AzureTemplate:
        cloned_data = azure_template.model_dump(mode="json", exclude_none=True)
        selected_vm_size = plan_entry.score.candidate.instance_type
        cloned_data["vm_size"] = selected_vm_size
        cloned_data["vm_sizes"] = []
        cloned_data["allocation_strategy"] = "capacityOptimized"
        cloned_data["location"] = plan_entry.score.candidate.region or azure_template.location
        cloned_data["zones"] = (
            [plan_entry.score.candidate.zone] if plan_entry.score.candidate.zone else []
        )
        cloned_data["placement_regions"] = []
        cloned_data["placement_zones"] = []
        return AzureTemplate.model_validate(cloned_data)

    def _execute_planned_spot_launches(
        self,
        azure_template: AzureTemplate,
        provider_api: str,
        count: int,
        template_config: dict[str, Any],
        operation: ProviderOperation,
    ) -> ProviderResult:
        handler = self.handlers.get(provider_api)
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api}",
                "HANDLER_NOT_FOUND",
            )

        plan = self._build_spot_placement_plan(azure_template, count)
        if not plan:
            return ProviderResult.error_result(
                "No viable spot placement candidates returned scores",
                "NO_PLACEMENT_CANDIDATES",
            )

        request_metadata = dict(operation.parameters.get("request_metadata", {}) or {})
        base_request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )

        summary = self._spot_placement_execution.execute_plan(
            plan=plan,
            total_count=count,
            build_child_template=lambda plan_entry: self._clone_template_for_plan_entry(
                azure_template, plan_entry
            ),
            build_child_request=lambda requested_for_entry, idx: create_acquire_request(
                template_id=azure_template.template_id,
                count=requested_for_entry,
                provider_type="azure",
                provider_name=self.provider_instance_name,
                provider_api=provider_api,
                request_metadata=request_metadata,
                parent_request_id=base_request_id,
                plan_entry_index=idx,
            ),
            launch_child=lambda child_request, child_template: handler.acquire_hosts(
                child_request, child_template
            ),
            is_capacity_like_failure=self._is_capacity_like_failure,
        )

        provider_data = build_planned_execution_metadata(plan, summary)
        provider_data["fulfillment_final"] = (
            bool(summary.resource_ids)
            and summary.unfulfilled_count == 0
            and not summary.terminal_error_message
            and not summary.terminated_early
        )

        if summary.terminal_error_message and not summary.resource_ids and not summary.instances:
            return ProviderResult.error_result(
                f"Provisioning failed: {summary.terminal_error_message}",
                "PROVISIONING_ADAPTER_ERROR",
                {
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api,
                    "method": "planned_handler",
                    "provider_data": provider_data,
                },
            )

        if not summary.resource_ids and not summary.instances and summary.unfulfilled_count > 0:
            return ProviderResult.error_result(
                "Spot placement plan could not provision any instances",
                "PROVISIONING_ADAPTER_ERROR",
                {
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api,
                    "method": "planned_handler",
                    "provider_data": provider_data,
                },
            )

        return ProviderResult.success_result(
            {
                "resource_ids": summary.resource_ids,
                "instances": summary.instances,
                "provider_api": provider_api,
                "count": count,
                "template_id": azure_template.template_id,
            },
            {
                "operation": "create_instances",
                "template_config": template_config,
                "handler_used": provider_api,
                "method": "planned_handler",
                "provider_data": provider_data,
            },
        )

    # ------------------------------------------------------------------
    # ProviderStrategy contract
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        try:
            self._logger.info(
                "Azure provider strategy ready for region: %s",
                self._azure_config.region,
            )
            self._initialized = True
            self._logger.debug("Azure provider strategy initialized successfully (lazy mode)")
            return True
        except Exception as exc:
            self._logger.error("Failed to initialize Azure provider strategy: %s", exc)
            return False

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
                # TODO: I don't think this does anything yet, placeholder for if dry run is implemented in this way
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
        """
        Get Azure provider capabilities and features.

        Returns:
            Comprehensive capabilities information for Azure provider
        """
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
                "supported_apis": [
                    AzureProviderApi.VMSS.value,
                    AzureProviderApi.VMSS_UNIFORM.value,
                    AzureProviderApi.SINGLE_VM.value,
                    AzureProviderApi.CYCLECLOUD.value,
                ],
                "api_capabilities": {
                    AzureProviderApi.VMSS.value: {
                        "supported_fleet_types": [],
                        "supports_spot": True,
                        "supports_on_demand": True,
                        "max_instances": 1000,
                    },
                    AzureProviderApi.VMSS_UNIFORM.value: {
                        "supported_fleet_types": [],
                        "supports_spot": True,
                        "supports_on_demand": True,
                        "max_instances": 1000,
                    },
                    AzureProviderApi.SINGLE_VM.value: {
                        "supported_fleet_types": [],
                        "supports_spot": True,
                        "supports_on_demand": True,
                        "max_instances": 1000,
                    },
                    AzureProviderApi.CYCLECLOUD.value: {
                        "supported_fleet_types": [],
                        "supports_spot": False,
                        "supports_on_demand": True,
                        "requires_existing_cluster": True,
                        "required_create_fields": ["cluster_name", "node_array"],
                        "capacity_limit_source": "cluster_status.maxCount",
                        "supports_async_operations": True,
                    },
                },
                "instance_management": True,
                "spot_instances": True,
                "fleet_management": True,
                # VMSS now has AWS-comparable elastic-group lifecycle behavior in ORB
                # (group creation, capacity-aware release, and capacity reporting),
                # even though explicit autoscale-policy management is still not exposed.
                "auto_scaling": True,
                # In this repo, "load_balancing" means templates can reference existing
                # backend-pool-style network attachments in provider payloads. It does not
                # mean ORB creates or manages Azure load balancers, probes, NAT rules, or
                # application gateway policy objects.
                "load_balancing": True,
                "vpc_support": True,  # VNet
                "security_groups": True,  # NSG
                "key_pairs": True,  # SSH keys
                "tags_support": True,
                "monitoring": True,
                # TODO: These are example/common regions, not a dynamically discovered or authoritative region list.
                "regions": ["eastus", "eastus2", "westus2", "westeurope", "northeurope"],
                # TODO: These are example/common VM sizes, not a subscription- or region-aware available-sizes query.
                "instance_types": [
                    "Standard_D2s_v5",
                    "Standard_D4s_v5",
                    "Standard_D8s_v5",
                    "Standard_E4s_v5",
                    "Standard_F4s_v2",
                ],
                # Rough VMSS-oriented metadata; real Azure ceilings also depend on image type,
                # quota, throttling, and which handler/provider_api is actually used.
                "max_instances_per_request": 1000,
                # Windows is intentionally unsupported for Azure.
                #
                # Current provider payload generation does not model a usable Windows auth/bootstrap path.
                #
                # If we implement Windows later, the two viable Azure-native
                # directions we found are:
                # 1. Bootstrap with a securely supplied local admin password,
                #    then use Microsoft Entra sign-in for ongoing access:
                #    https://learn.microsoft.com/en-us/entra/identity/devices/howto-vm-sign-in-azure-ad-windows
                # 2. Bootstrap Windows OpenSSH, then push/install SSH keys
                #    after provisioning; Azure docs note keys are not
                #    auto-provisioned for Windows at deploy time:
                #    https://learn.microsoft.com/en-us/azure/virtual-machines/windows/connect-ssh
                #
                # These are both ugly in comparison to AWS/Linux auth
                #
                # Relevant Azure compute schema docs for a future Windows
                # implementation:
                # https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/2018-06-01/virtualmachines
                # https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/2018-04-01/virtualmachinescalesets
                "supports_windows": False,
                "supports_linux": True,
            },
            limitations={
                # Repo-local operational metadata, not an external Azure hard limit.
                "max_concurrent_requests": 100,
                # Repo-local placeholder; real limits come from ARM + Microsoft.Compute throttling.
                "rate_limit_per_second": 20,
                # Repo policy/default, not a known Azure platform lifetime limit.
                "max_instance_lifetime_hours": 8760,
                "requires_vpc": True,  # VNet/subnet required for VMSS
                # True of the repo in it's current state, not a fundamental Azure platform limitation
                "requires_key_pair": True,
            },
            performance_metrics={
                # Heuristic timing metadata, not measured SLOs or Azure guarantees.
                "typical_create_time_seconds": 120,
                "typical_terminate_time_seconds": 60,
                "health_check_timeout_seconds": 15,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        start_time = time.time()

        try:
            azure_client = self.azure_client
            if not azure_client:
                return ProviderHealthStatus.unhealthy(
                    "Azure client initialization failed",
                    {"error": "client_initialization_failed"},
                )

            from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

            if is_dry_run_active():
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.healthy(
                    f"Azure provider healthy (DRY-RUN) - Region: {self._azure_config.region}",
                    response_time_ms,
                )

            # Lightweight subscription list call
            token = azure_client.credential.get_token("https://management.azure.com/.default")

            # TODO: Is this a good health check?
            if token is None:
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.unhealthy(
                    "Azure provider unhealthy - no token found",
                    {"error": "no_token", "response_time_ms": response_time_ms},
                )

            # If we got here, the provider is healthy
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"Azure provider healthy - Token fetched successfully in {response_time_ms}, "
                f"Region: {self._azure_config.region}",
                response_time_ms,
            )

        except Exception as exc:
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Health check error: {exc!s}",
                {"error": str(exc), "response_time_ms": response_time_ms},
            )

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate Azure provider name: {provider_type}_{profile}_{region}"""
        provider_type = self.provider_type  # Use dynamic provider type
        profile = config.get("profile", "default")
        region = config.get("region", "eastus")
        return f"{provider_type}_{profile}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse Azure provider name back to components."""
        parts = provider_name.split("_")
        return {
            "type": parts[0] if len(parts) > 0 else self.provider_type,
            "subscription_id": parts[1] if len(parts) > 1 else "default",
            "region": parts[2] if len(parts) > 2 else "eastus2",
        }

    # TODO: I think this can be arbitrary as I can't
    #  find any specific AWS provider name that matches's it's format.
    #  but I should double check this doesn't need to match some existing Azure standard
    def get_provider_name_pattern(self) -> str:
        return "{type}_{subscription_id}_{region}"

    def cleanup(self) -> None:
        try:
            self._client = None
            self._resource_manager = None
            self._handlers = {}
            self._initialized = False
            self._logger.debug("Azure provider cleaned up")
        except Exception as exc:
            self._logger.warning("Failed during Azure provider cleanup: %s", exc)

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

    async def _handle_create_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        try:
            # TODO: The first section of this function is basically a direct copy of the AWS version
            #  If this is correct, it needs to be abstracted to avoid code duplication.
            template_config = operation.parameters.get("template_config", {})
            count = operation.parameters.get("count", 1)

            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for instance creation",
                    "MISSING_TEMPLATE_CONFIG",
                )

            provider_api = template_config.get("provider_api", AzureProviderApi.VMSS.value)
            handler = self.handlers.get(provider_api)

            if not handler:
                return ProviderResult.error_result(
                    f"No handler available for provider_api: {provider_api}",
                    "HANDLER_NOT_FOUND",
                )

            # Build AzureTemplate domain object from the normalized template payload plus
            # provider-owned Azure defaults that are still authoritative at request time.
            enhanced_config = self._build_azure_template_config(template_config)
            try:
                self._logger.debug("Creating AzureTemplate from config: %s", enhanced_config)
                azure_template = AzureTemplate.model_validate(enhanced_config)
            except Exception as exc:
                self._logger.error("Error validating AzureTemplate: %s", exc)
                return ProviderResult.error_result(
                    f"Invalid template configuration: {exc!s}",
                    "INVALID_TEMPLATE_CONFIG",
                )

            # Build a domain Request
            from orb.domain.request.aggregate import Request
            from orb.domain.request.value_objects import RequestType

            request_metadata = dict(operation.parameters.get("request_metadata", {}) or {})
            request_id = operation.parameters.get("request_id") or (
                operation.context.get("request_id") if operation.context else None
            )
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=azure_template.template_id,
                machine_count=count,
                provider_type="azure",
                provider_name=self.provider_instance_name,
                metadata=request_metadata,
                request_id=request_id,
            )
            request.provider_api = provider_api

            if bool(operation.context and operation.context.get("dry_run", False)):
                return ProviderResult.success_result(
                    {
                        "resource_ids": ["dry-run-resource-id"],
                        "instances": [],
                        "provider_api": provider_api,
                        "count": count,
                        "template_id": azure_template.template_id,
                    },
                    {
                        "operation": "create_instances",
                        "template_config": template_config,
                        "handler_used": provider_api,
                        "method": "dry_run",
                        "provider_data": {"dry_run": True},
                    },
                )

            if self._should_use_spot_placement(azure_template):
                return self._execute_planned_spot_launches(
                    azure_template=azure_template,
                    provider_api=provider_api,
                    count=count,
                    template_config=template_config,
                    operation=operation,
                )

            handler_result = handler.acquire_hosts(request, azure_template)

            if isinstance(handler_result, dict):
                resource_ids = handler_result.get("resource_ids", [])
                instances = handler_result.get("instances", [])
                success = handler_result.get("success", True)
                error_message = handler_result.get("error_message")
                provider_data = handler_result.get("provider_data") or {}

                if not success:
                    return ProviderResult.error_result(
                        f"Provisioning failed: {error_message}",
                        "PROVISIONING_ADAPTER_ERROR",
                        {
                            "operation": "create_instances",
                            "template_config": template_config,
                            "handler_used": provider_api,
                            "method": "handler",
                            "provider_data": provider_data,
                        },
                    )
            else:
                resource_ids = [handler_result] if handler_result else []
                instances = []
                provider_data = {}

            return ProviderResult.success_result(
                {
                    "resource_ids": resource_ids,
                    "instances": instances,
                    "provider_api": provider_api,
                    "count": count,
                    "template_id": azure_template.template_id,
                },
                {
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api,
                    "method": "handler",
                    "provider_data": provider_data,
                },
            )

        except Exception as exc:
            provider_error = self._build_provisioning_error_payload(exc)
            return ProviderResult.error_result(
                f"Failed to create instances: {exc!s}",
                "CREATE_INSTANCES_ERROR",
                {
                    "operation": "create_instances",
                    "template_config": template_config if "template_config" in locals() else {},
                    "handler_used": provider_api if "provider_api" in locals() else None,
                    "method": "handler",
                    "provider_data": {
                        "fleet_errors": [provider_error],
                    },
                },
            )

    # ------------------------------------------------------------------
    # TERMINATE_INSTANCES
    # ------------------------------------------------------------------

    @staticmethod
    def _group_instance_ids_by_resource(
            instance_ids: list[str],
        resource_mapping: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Normalise resource mappings to {resource_id: [instance_ids]} shape."""
        grouped: dict[str, list[str]] = {}
        if not resource_mapping:
            return grouped

        for key, value in resource_mapping.items():
            resource_id: Optional[str] = None
            mapped_ids: list[str] = []

            # Canonical shape from request handler: instance_id -> (resource_id, desired_capacity)
            if isinstance(value, tuple):
                if value:
                    resource_id = value[0] if isinstance(value[0], str) else None
                mapped_ids = [key]
            # Alternate shape: instance_id -> resource_id
            elif isinstance(value, str):
                resource_id = value
                mapped_ids = [key]
            # Legacy shape: resource_id -> [instance_ids]
            elif isinstance(value, list):
                if isinstance(key, str):
                    resource_id = key
                mapped_ids = [str(v) for v in value if v]

            if not resource_id:
                continue

            bucket = grouped.setdefault(resource_id, [])
            for mapped_id in mapped_ids:
                if mapped_id in instance_ids and mapped_id not in bucket:
                    bucket.append(mapped_id)

        return grouped

    def _handle_terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        self._logger.debug("_handle_terminate_instances")
        try:
            instance_ids = operation.parameters.get("instance_ids", [])
            raw_resource_mapping = operation.parameters.get("resource_mapping", {})
            grouped_resource_mapping = self._group_instance_ids_by_resource(
                instance_ids,
                raw_resource_mapping,
            )

            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for termination",
                    "MISSING_INSTANCE_IDS",
                )

            if bool(operation.context and operation.context.get("dry_run", False)):
                return ProviderResult.success_result(
                    {"success": True, "terminated_count": len(instance_ids)},
                    {
                        "operation": "terminate_instances",
                        "instance_ids": instance_ids,
                        "method": "dry_run",
                        "provider_data": {"dry_run": True},
                    },
                )

            resource_group = self._resolve_operation_resource_group(operation)
            default_resource_id = operation.parameters.get("resource_id")
            if not default_resource_id and grouped_resource_mapping:
                default_resource_id = next(iter(grouped_resource_mapping.keys()))

            release_context = self._build_cyclecloud_request_metadata(
                operation=operation,
                resource_group=resource_group,
            )
            release_context["resource_id"] = default_resource_id or "unknown"

            provider_api = operation.parameters.get("provider_api")
            if provider_api in (None, ""):
                return ProviderResult.error_result(
                    "provider_api is required for Azure termination",
                    "MISSING_PROVIDER_API",
                )
            provider_api_value = provider_api.value if hasattr(provider_api, "value") else provider_api
            handler = self.handlers.get(provider_api_value)
            if not handler:
                return ProviderResult.error_result(
                    f"No handler available for provider_api: {provider_api_value}",
                    "HANDLER_NOT_FOUND",
                )

            if handler:
                context = dict(release_context)
                termination_provider_data: list[dict[str, Any]] = []

                if grouped_resource_mapping:
                    for resource_id, mapped_instance_ids in grouped_resource_mapping.items():
                        handler_result = handler.release_hosts(
                            machine_ids=mapped_instance_ids,
                            resource_id=resource_id,
                            context=context,
                        )
                        self._record_pending_vmss_cleanup(handler_result)
                        if isinstance(handler_result, dict):
                            provider_data = handler_result.get("provider_data")
                            if isinstance(provider_data, dict):
                                termination_provider_data.append(provider_data)
                else:
                    handler_result = handler.release_hosts(
                        machine_ids=instance_ids,
                        resource_id=default_resource_id or "unknown",
                        context=context,
                    )
                    self._record_pending_vmss_cleanup(handler_result)
                    if isinstance(handler_result, dict):
                        provider_data = handler_result.get("provider_data")
                        if isinstance(provider_data, dict):
                            termination_provider_data.append(provider_data)

                return ProviderResult.success_result(
                    {"success": True, "terminated_count": len(instance_ids)},
                    {
                        "operation": "terminate_instances",
                        "instance_ids": instance_ids,
                        "method": "handler",
                        "provider_data": {
                            "termination_requests": termination_provider_data,
                        } if termination_provider_data else {},
                    },
                )

            return ProviderResult.error_result(
                "No handler available for termination",
                "HANDLER_NOT_FOUND",
            )

        except Exception as exc:
            return ProviderResult.error_result(
                f"Failed to terminate instances: {exc!s}",
                "TERMINATE_INSTANCES_ERROR",
            )

    # ------------------------------------------------------------------
    # GET_INSTANCE_STATUS
    # ------------------------------------------------------------------

    def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        try:
            instance_ids = operation.parameters.get("instance_ids", [])
            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for status query",
                    "MISSING_INSTANCE_IDS",
                )

            if bool(operation.context and operation.context.get("dry_run", False)):
                return ProviderResult.success_result(
                    {
                        "machines": [
                            {
                                "instance_id": instance_id,
                                "status": "unknown",
                                "provider_type": "azure",
                                "provider_data": {"dry_run": True},
                            }
                            for instance_id in instance_ids
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

            resource_group = self._resolve_operation_resource_group(operation)
            if not resource_group:
                return ProviderResult.error_result(
                    "resource_group is required for status query",
                    "MISSING_RESOURCE_GROUP",
                )

            self._restore_pending_vmss_cleanups(operation)

            handler_machines = self._get_instance_status_via_handlers(
                operation=operation,
                instance_ids=instance_ids,
                resource_group=resource_group,
            )
            if handler_machines is not None:
                provider_api = self._resolve_status_provider_api(operation)
                provider_api_value = (
                    provider_api.value if hasattr(provider_api, "value") else provider_api
                )
                resource_ids: list[str] = []
                if provider_api_value in (
                    AzureProviderApi.VMSS.value,
                    AzureProviderApi.VMSS_UNIFORM.value,
                ):
                    resource_ids = self._status_resource_ids(operation, instance_ids)
                    if resource_ids:
                        self._maybe_cleanup_pending_vmss(
                            resource_group=resource_group,
                            resource_ids=resource_ids,
                            instance_details=handler_machines,
                        )
                metadata = {
                    "operation": "get_instance_status",
                    "instance_ids": instance_ids,
                    "method": "handler",
                }
                if provider_api_value in (
                    AzureProviderApi.VMSS.value,
                    AzureProviderApi.VMSS_UNIFORM.value,
                ):
                    metadata.update(
                        self._vmss_cleanup_status_metadata(
                            resource_group=resource_group,
                            resource_ids=resource_ids,
                        )
                    )
                return ProviderResult.success_result(
                    {"machines": handler_machines, "queried_count": len(instance_ids)},
                    metadata,
                )

            azure_client = self.azure_client
            if not azure_client:
                return ProviderResult.error_result(
                    "Azure client not available", "AZURE_CLIENT_NOT_AVAILABLE"
                )

            machines: list[dict[str, Any]] = []
            compute = azure_client.compute_client

            for vm_id in instance_ids:
                try:
                    vm = compute.virtual_machines.get(
                        resource_group_name=resource_group,
                        vm_name=vm_id,
                        expand="instanceView",
                    )
                    machine = self._convert_azure_instance_to_machine(vm)
                    machines.append(machine)
                except Exception as exc:
                    self._logger.error("Failed to get status for VM '%s': %s", vm_id, exc)
                    machines.append({
                        "instance_id": vm_id,
                        "status": "unknown",
                        "provider_type": "azure",
                        "error": str(exc),
                    })

            return ProviderResult.success_result(
                {"machines": machines, "queried_count": len(instance_ids)},
                {"operation": "get_instance_status", "instance_ids": instance_ids},
            )

        except Exception as exc:
            return ProviderResult.error_result(
                f"Failed to get instance status: {exc!s}",
                "GET_INSTANCE_STATUS_ERROR",
            )

    def _get_instance_status_via_handlers(
        self,
        *,
        operation: ProviderOperation,
        instance_ids: list[str],
        resource_group: str,
    ) -> Optional[list[dict[str, Any]]]:
        """Use Azure handlers for status queries when enough resource context is available."""
        provider_api = self._resolve_status_provider_api(operation)
        provider_api_value = provider_api.value if hasattr(provider_api, "value") else provider_api
        raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
        grouped_resource_mapping = self._group_instance_ids_by_resource(instance_ids, raw_resource_mapping)

        if not provider_api_value:
            return None

        handler = self.handlers.get(provider_api_value) if provider_api_value else None
        if not handler and provider_api_value == AzureProviderApi.VMSS_UNIFORM.value:
            handler = self.handlers.get(AzureProviderApi.VMSS.value)
        if not handler and not grouped_resource_mapping:
            return None

        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )

        def build_metadata(additional: Optional[dict[str, Any]] = None) -> dict[str, Any]:
            metadata = self._build_cyclecloud_request_metadata(
                operation=operation,
                resource_group=resource_group,
            )
            if additional:
                metadata.update(additional)
            return metadata

        def make_request(resource_ids: list[str], metadata: dict[str, Any]) -> Request:
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

        if provider_api_value == AzureProviderApi.SINGLE_VM.value and handler:
            request = make_request(instance_ids, build_metadata())
            return handler.check_hosts_status(request)

        all_results: list[dict[str, Any]] = []
        seen_instance_ids: set[str] = set()

        if grouped_resource_mapping:
            for resource_id, mapped_ids in grouped_resource_mapping.items():
                group_provider_api = provider_api_value
                group_handler = handler
                if not group_handler and group_provider_api:
                    group_handler = self.handlers.get(group_provider_api)
                if not group_handler:
                    continue

                extra_metadata: dict[str, Any] = {}
                if group_provider_api == AzureProviderApi.CYCLECLOUD.value:
                    extra_metadata["node_ids"] = mapped_ids
                request = make_request([resource_id], build_metadata(extra_metadata))
                for machine in self._filter_status_results(group_handler.check_hosts_status(request), mapped_ids):
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
        if provider_api_value == AzureProviderApi.CYCLECLOUD.value:
            extra_metadata = {
                "node_ids": instance_ids,
            }
        request = make_request(
            instance_ids if provider_api_value == AzureProviderApi.SINGLE_VM.value else [resource_id],
            build_metadata(extra_metadata),
        )
        if provider_api_value == AzureProviderApi.SINGLE_VM.value:
            return handler.check_hosts_status(request)
        return self._filter_status_results(handler.check_hosts_status(request), instance_ids)

    @staticmethod
    def _request_metadata(operation: ProviderOperation) -> dict[str, Any]:
        return dict(operation.parameters.get("request_metadata") or {})

    @staticmethod
    def _status_resource_ids(operation: ProviderOperation, instance_ids: list[str]) -> list[str]:
        resource_ids: list[str] = []
        raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
        for resource_id, mapped_ids in AzureProviderStrategy._group_instance_ids_by_resource(
            instance_ids,
            raw_resource_mapping,
        ).items():
            if resource_id and mapped_ids and resource_id not in resource_ids:
                resource_ids.append(resource_id)

        direct_resource_id = operation.parameters.get("resource_id")
        if direct_resource_id not in (None, "") and str(direct_resource_id) not in resource_ids:
            resource_ids.append(str(direct_resource_id))
        return resource_ids

    @staticmethod
    def _resolve_status_provider_api(operation: ProviderOperation) -> Optional[Any]:
        provider_api = operation.parameters.get("provider_api")
        if provider_api not in (None, ""):
            return provider_api
        return None

    @staticmethod
    def _filter_status_results(
        results: list[dict[str, Any]],
        requested_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Filter handler status results to the requested IDs using common Azure identifiers."""
        requested = {str(item) for item in requested_ids}
        filtered: list[dict[str, Any]] = []
        for result in results:
            provider_data = result.get("provider_data") or {}
            candidate_ids = {
                str(result.get("instance_id")),
                str(provider_data.get("vm_id")),
                str(provider_data.get("vmss_instance_id")),
                str(provider_data.get("node_id")),
                str(provider_data.get("vm_name")),
            }
            candidate_ids.discard("None")
            if candidate_ids & requested:
                filtered.append(result)
        return filtered

    @staticmethod
    def _cyclecloud_metadata_keys() -> tuple[str, ...]:
        return (
            "cluster_name",
            "node_array",
            "node_ids",
            "operation_id",
            "operation_location",
            "cyclecloud_url",
            "cyclecloud_credential_path",
            "cyclecloud_verify_ssl",
            "cyclecloud_auth_mode",
            "cyclecloud_aad_scope",
        )

    def _build_cyclecloud_request_metadata(
        self,
        *,
        operation: ProviderOperation,
        resource_group: Optional[str],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"resource_group": resource_group}
        request_metadata = self._request_metadata(operation)
        for key in self._cyclecloud_metadata_keys():
            value = request_metadata.get(key)
            if value not in (None, ""):
                metadata[key] = value
        return metadata

    def _resolve_operation_resource_group(
        self,
        operation: ProviderOperation,
    ) -> Optional[str]:
        request_metadata = self._request_metadata(operation)
        request_resource_group = request_metadata.get("resource_group")
        if request_resource_group not in (None, ""):
            return str(request_resource_group)

        return self._azure_config.resource_group

    @staticmethod
    def _status_candidate_ids(result: dict[str, Any]) -> set[str]:
        provider_data = result.get("provider_data") or {}
        candidate_ids = {
            str(result.get("instance_id")),
            str(provider_data.get("vm_id")),
            str(provider_data.get("vmss_instance_id")),
            str(provider_data.get("node_id")),
            str(provider_data.get("vm_name")),
        }
        candidate_ids.discard("None")
        candidate_ids.discard("")
        return candidate_ids

    def _record_pending_vmss_cleanup(self, handler_result: Any) -> None:
        if not isinstance(handler_result, dict):
            return

        provider_data = handler_result.get("provider_data")
        if not isinstance(provider_data, dict):
            return

        pending_metadata = provider_data.get("pending_vmss_cleanup")
        if not isinstance(pending_metadata, dict):
            return

        pending = PendingVmssCleanup.from_metadata(pending_metadata)
        if pending is None:
            return

        key = (pending.resource_group, pending.vmss_name)
        existing = self._pending_vmss_cleanups.get(key)
        self._pending_vmss_cleanups[key] = (
            pending if existing is None else existing.combine_for_same_vmss(pending)
        )

    def _restore_pending_vmss_cleanups(self, operation: ProviderOperation) -> None:
        """Rebuild pending VMSS cleanup state from durable request metadata."""
        request_metadata = self._request_metadata(operation)

        direct_pending = request_metadata.get("pending_vmss_cleanup")
        if isinstance(direct_pending, dict):
            self._record_pending_vmss_cleanup({"provider_data": request_metadata})

        termination_requests = request_metadata.get("termination_requests")
        if not isinstance(termination_requests, list):
            return

        for termination_request in termination_requests:
            if isinstance(termination_request, dict):
                self._record_pending_vmss_cleanup({"provider_data": termination_request})

    def _has_pending_vmss_cleanup(
        self,
        *,
        resource_group: Optional[str],
        resource_ids: list[str],
    ) -> bool:
        if not resource_group:
            return False

        for resource_id in resource_ids:
            key = (str(resource_group), str(resource_id))
            if key in self._pending_vmss_cleanups:
                return True
        return False

    def _vmss_cleanup_status_metadata(
        self,
        *,
        resource_group: Optional[str],
        resource_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "termination_follow_up_pending": self._has_pending_vmss_cleanup(
                resource_group=resource_group,
                resource_ids=resource_ids,
            )
        }

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

    def _maybe_cleanup_pending_vmss(
        self,
        *,
        resource_group: Optional[str],
        resource_ids: list[str],
        instance_details: list[dict[str, Any]],
    ) -> None:
        if not resource_group or not resource_ids:
            return

        observed_ids: set[str] = set()
        for instance in instance_details:
            observed_ids.update(self._status_candidate_ids(instance))

        seen_vmss_names: set[str] = set()
        for resource_id in resource_ids:
            vmss_name = str(resource_id)
            if not vmss_name or vmss_name in seen_vmss_names:
                continue
            seen_vmss_names.add(vmss_name)

            key = (str(resource_group), vmss_name)
            pending = self._pending_vmss_cleanups.get(key)
            if not pending:
                continue

            requested_ids = set(pending.machine_ids)
            if not requested_ids:
                self._pending_vmss_cleanups.pop(key, None)
                continue

            if pending.delete_submitted:
                vmss_exists = self._vmss_exists(
                    resource_group=str(resource_group),
                    vmss_name=vmss_name,
                )
                if vmss_exists is False:
                    self._pending_vmss_cleanups.pop(key, None)
                continue

            if requested_ids & observed_ids:
                continue

            try:
                if pending.delete_vmss_when_empty:
                    member_count = self._current_vmss_member_count(
                        resource_group=str(resource_group),
                        vmss_name=vmss_name,
                    )
                    if member_count is None or member_count > 0:
                        continue
                    azure_client = self.azure_client
                    if azure_client:
                        azure_client.compute_client.virtual_machine_scale_sets.begin_delete(
                            resource_group_name=str(resource_group),
                            vm_scale_set_name=vmss_name,
                        )
                        pending.delete_submitted = True
                        continue

                self._pending_vmss_cleanups.pop(key, None)
            except Exception as exc:
                self._logger.warning(
                    "Failed to clean up pending VMSS '%s' in '%s': %s",
                    vmss_name,
                    resource_group,
                    exc,
                )

    # ------------------------------------------------------------------
    # DESCRIBE_RESOURCE_INSTANCES
    # ------------------------------------------------------------------

    async def _handle_describe_resource_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        try:
            resource_ids = operation.parameters.get("resource_ids", [])
            provider_api = operation.parameters.get("provider_api")
            provider_api_value = (
                provider_api.value if hasattr(provider_api, "value") else provider_api
            )

            if not resource_ids:
                return ProviderResult.error_result(
                    "Resource IDs are required for instance discovery",
                    "MISSING_RESOURCE_IDS",
                )

            if provider_api_value in (None, ""):
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
                        "provider_api": provider_api_value,
                        "method": "dry_run",
                        "provider_data": {"dry_run": True},
                    },
                )

            handler = self.handlers.get(provider_api_value)
            if not handler:
                return ProviderResult.error_result(
                    f"No handler available for provider_api: {provider_api}",
                    "HANDLER_NOT_FOUND",
                )

            from orb.domain.request.aggregate import Request
            from orb.domain.request.value_objects import RequestType

            request_id = operation.parameters.get("request_id") or (
                operation.context.get("request_id") if operation.context else None
            )
            resource_group = self._resolve_operation_resource_group(operation)
            request_metadata = self._build_cyclecloud_request_metadata(
                operation=operation,
                resource_group=resource_group,
            )
            self._restore_pending_vmss_cleanups(operation)
            if provider_api_value == AzureProviderApi.SINGLE_VM.value:
                deployment_name = self._request_metadata(operation).get("deployment_name")
                if deployment_name not in (None, ""):
                    request_metadata["deployment_name"] = str(deployment_name)
            if provider_api_value in (
                AzureProviderApi.VMSS.value,
                AzureProviderApi.VMSS_UNIFORM.value,
            ) and self._has_pending_vmss_cleanup(
                resource_group=resource_group,
                resource_ids=resource_ids,
            ):
                request_metadata["fail_on_partial_status_error"] = True
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=operation.parameters.get("template_id", "unknown"),
                machine_count=1,
                provider_type="azure",
                provider_name=self.provider_instance_name,
                request_id=request_id,
                metadata=request_metadata,
            )
            request.resource_ids = resource_ids

            instance_details = handler.check_hosts_status(request)
            self._maybe_cleanup_pending_vmss(
                resource_group=resource_group,
                resource_ids=resource_ids,
                instance_details=instance_details,
            )
            cleanup_metadata: dict[str, Any] = {}
            if provider_api_value in (
                AzureProviderApi.VMSS.value,
                AzureProviderApi.VMSS_UNIFORM.value,
            ):
                cleanup_metadata = self._vmss_cleanup_status_metadata(
                    resource_group=resource_group,
                    resource_ids=resource_ids,
                )

            if not instance_details:
                metadata = {
                    "operation": "describe_resource_instances",
                    "resource_ids": resource_ids,
                    "provider_api": provider_api_value,
                    "handler_used": provider_api_value,
                    "instance_count": 0,
                    **cleanup_metadata,
                }
                if provider_api_value in (
                    AzureProviderApi.VMSS.value,
                    AzureProviderApi.VMSS_UNIFORM.value,
                ):
                    vmss_errors = []
                    if resource_group and hasattr(handler, "get_vmss_resource_errors"):
                        for resource_id in resource_ids:
                            for error in handler.get_vmss_resource_errors(resource_group, resource_id):
                                if error not in vmss_errors:
                                    vmss_errors.append(error)
                    if vmss_errors:
                        metadata["fleet_errors"] = vmss_errors
                    self._augment_vmss_capacity_metadata(
                        metadata,
                        resource_ids,
                        resource_group=resource_group,
                    )
                elif provider_api_value == AzureProviderApi.SINGLE_VM.value:
                    self._augment_single_vm_deployment_metadata(
                        metadata,
                        request_metadata,
                        resource_group=resource_group,
                    )
                return ProviderResult.success_result(
                    {"instances": []},
                    metadata,
                )

            fleet_errors: list[dict[str, Any]] = []
            for inst in instance_details:
                provider_data = inst.get("provider_data") or {}
                if isinstance(provider_data, dict):
                    for error in provider_data.get("fleet_errors") or []:
                        if error not in fleet_errors:
                            fleet_errors.append(error)

            metadata: dict[str, Any] = {
                "operation": "describe_resource_instances",
                "resource_ids": resource_ids,
                "provider_api": provider_api_value,
                "handler_used": provider_api_value,
                "instance_count": len(instance_details),
                **cleanup_metadata,
            }
            if fleet_errors:
                metadata["fleet_errors"] = fleet_errors

            # VMSS capacity info
            if provider_api_value in (
                AzureProviderApi.VMSS.value,
                AzureProviderApi.VMSS_UNIFORM.value,
            ):
                self._augment_vmss_capacity_metadata(
                    metadata,
                    resource_ids,
                    resource_group=resource_group,
                )

            self._augment_shortfall_metadata(metadata)

            return ProviderResult.success_result(
                data={"instances": instance_details},
                metadata=metadata,
            )

        except Exception as exc:
            return ProviderResult.error_result(
                f"Failed to describe resource instances: {exc!s}",
                "DESCRIBE_RESOURCE_INSTANCES_ERROR",
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
            return ProviderResult.error_result(
                f"Failed to validate template: {exc!s}",
                "VALIDATE_TEMPLATE_ERROR",
            )

    # ------------------------------------------------------------------
    # GET_AVAILABLE_TEMPLATES
    # ------------------------------------------------------------------

    def _handle_get_available_templates(self, operation: ProviderOperation) -> ProviderResult:
        try:
            templates = self._get_azure_templates()
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
    # VMSS capacity metadata
    # ------------------------------------------------------------------

    def _augment_vmss_capacity_metadata(
        self,
        metadata: dict[str, Any],
        resource_ids: list[str],
        resource_group: Optional[str] = None,
    ) -> None:
        if not resource_ids or not self.resource_manager:
            return

        resource_group = resource_group or self._azure_config.resource_group
        if not resource_group:
            return

        aggregated_target_capacity = 0
        aggregated_fulfilled_capacity = 0
        per_resource_capacity: dict[str, dict[str, Any]] = {}
        capacity_states: list[str] = []

        for vmss_name in resource_ids:
            try:
                capacity_info = self.resource_manager.get_vmss_capacity(
                    resource_group, vmss_name
                )
                provisioned_instance_count = int(
                    capacity_info.get("provisioned_instance_count", 0) or 0
                )
                target_capacity = int(capacity_info.get("capacity", 0) or 0)
                provisioning_state = capacity_info.get("provisioning_state")

                per_resource_capacity[str(vmss_name)] = {
                    "target_capacity_units": target_capacity,
                    "fulfilled_capacity_units": provisioned_instance_count,
                    "provisioned_instance_count": provisioned_instance_count,
                    "state": provisioning_state,
                }
                aggregated_target_capacity += target_capacity
                aggregated_fulfilled_capacity += provisioned_instance_count
                if provisioning_state not in (None, ""):
                    capacity_states.append(str(provisioning_state))
            except Exception as exc:
                self._logger.warning(
                    "Could not fetch VMSS capacity for %s: %s", vmss_name, exc
                )

        if not per_resource_capacity:
            return

        if len(per_resource_capacity) == 1:
            aggregate_state = next(iter(per_resource_capacity.values())).get("state")
        else:
            aggregate_state = (
                capacity_states[0]
                if capacity_states and len(set(capacity_states)) == 1
                else "multiple"
            )
            metadata["fleet_capacity_fulfilment_by_resource"] = per_resource_capacity

        metadata["fleet_capacity_fulfilment"] = {
            "target_capacity_units": aggregated_target_capacity,
            "fulfilled_capacity_units": aggregated_fulfilled_capacity,
            "provisioned_instance_count": aggregated_fulfilled_capacity,
            "state": aggregate_state,
        }

    def _augment_single_vm_deployment_metadata(
        self,
        metadata: dict[str, Any],
        request_metadata: dict[str, Any],
        *,
        resource_group: Optional[str],
    ) -> None:
        deployment_name = request_metadata.get("deployment_name")
        if deployment_name in (None, "") or not resource_group or not self.deployment_service:
            return

        try:
            deployment_status = self.deployment_service.get_deployment_status(
                resource_group=str(resource_group),
                deployment_name=str(deployment_name),
            )
        except Exception as exc:
            self._logger.warning(
                "Could not fetch SingleVM deployment status for %s: %s",
                deployment_name,
                exc,
            )
            return

        if not deployment_status:
            return

        metadata["deployment_name"] = str(deployment_name)
        provisioning_state = deployment_status.get("provisioning_state")
        if provisioning_state not in (None, ""):
            metadata["deployment_provisioning_state"] = provisioning_state

        error_code = deployment_status.get("error_code")
        error_message = deployment_status.get("error_message")
        if str(provisioning_state).lower() == "failed" and error_code in (None, ""):
            error_code = "DeploymentFailed"
        if error_code not in (None, "") or error_message not in (None, ""):
            metadata["fleet_errors"] = [
                {
                    "error_code": error_code or "DeploymentFailed",
                    "error_message": (
                        error_message
                        or f"ARM deployment '{deployment_name}' failed"
                    ),
                    "resource_group": str(resource_group),
                    "instance_id": str(deployment_name),
                }
            ]

    @staticmethod
    def _augment_shortfall_metadata(metadata: dict[str, Any]) -> None:
        """Add a concise shortfall summary when target capacity exceeds fulfillment."""
        capacity = metadata.get("fleet_capacity_fulfilment") or {}
        target = capacity.get("target_capacity_units")
        fulfilled = capacity.get("fulfilled_capacity_units")
        fleet_errors = metadata.get("fleet_errors") or []

        if target is None or fulfilled is None:
            return
        if fulfilled >= target and not fleet_errors:
            return

        missing_capacity = max(int(target) - int(fulfilled), 0)
        likely_causes: list[str] = []
        seen_causes: set[str] = set()

        for error in fleet_errors:
            error_code = str((error or {}).get("error_code") or "")
            cause = error_code or "Unknown"
            if cause not in seen_causes:
                likely_causes.append(cause)
                seen_causes.add(cause)

        metadata["capacity_shortfall"] = {
            "missing_capacity_units": missing_capacity,
            "likely_causes": likely_causes,
            "summary": (
                f"Shortfall {fulfilled}/{target}"
                + (f"; causes={', '.join(likely_causes)}" if likely_causes else "")
            ),
        }

    @staticmethod
    def _build_provisioning_error_payload(exc: Exception) -> dict[str, Any]:
        """Normalize Azure provisioning errors for request metadata/status handling."""
        error_details = extract_azure_error_details(exc)

        return {
            "error_code": canonical_azure_error_code(exc),
            "error_message": error_details["message"],
            "status_code": error_details["status_code"],
            "raw_error_code": error_details["raw_error_code"],
        }

    # ------------------------------------------------------------------
    # Instance conversion
    # ------------------------------------------------------------------

    def _convert_azure_instance_to_machine(self, vm: Any) -> dict[str, Any]:
        """Convert an Azure SDK VM object to the domain machine dict."""
        from orb.domain.machine.machine_status import MachineStatus

        status = "unknown"
        instance_view = getattr(vm, "instance_view", None)
        if instance_view and hasattr(instance_view, "statuses"):
            for s in instance_view.statuses:
                code = getattr(s, "code", "")
                if code.startswith("PowerState/"):
                    state_map = {
                        "PowerState/running": MachineStatus.RUNNING,
                        "PowerState/starting": MachineStatus.PENDING,
                        "PowerState/stopping": MachineStatus.STOPPING,
                        "PowerState/stopped": MachineStatus.STOPPED,
                        "PowerState/deallocating": MachineStatus.SHUTTING_DOWN,
                        "PowerState/deallocated": MachineStatus.STOPPED,
                    }
                    status = state_map.get(code, MachineStatus.UNKNOWN).value
                    break

        hw = getattr(vm, "hardware_profile", None)
        network_identity = self.azure_client.resolve_network_identity_from_vm(vm)
        vm_name = cast(_AzureVmWithName, vm).name

        return {
            "instance_id": getattr(vm, "vm_id", vm_name or ""),
            "status": status,
            "private_ip": network_identity["private_ip"],
            "public_ip": network_identity["public_ip"],
            "launch_time": None,
            "instance_type": getattr(hw, "vm_size", None) if hw else None,
            "subnet_id": network_identity["subnet_id"],
            "vpc_id": network_identity["vnet_id"],
            "availability_zone": (getattr(vm, "zones", None) or [None])[0],
            "provider_type": "azure",
            "provider_data": {
                "vm_name": vm_name,
                "location": getattr(vm, "location", None),
                "provisioning_state": getattr(vm, "provisioning_state", None),
                "nic_id": network_identity["nic_id"],
                "nic_name": network_identity["nic_name"],
                "vnet_id": network_identity["vnet_id"],
            },
        }

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _get_azure_templates(self) -> list[dict[str, Any]]:
        """Load templates via scheduler strategy, falling back to examples."""
        try:
            from orb.infrastructure.scheduler.registry import get_scheduler_registry

            scheduler_registry = get_scheduler_registry()
            scheduler_strategy = scheduler_registry.get_active_strategy()

            if scheduler_strategy:
                template_paths = scheduler_strategy.get_template_paths()
                templates: list[dict[str, Any]] = []
                for path in template_paths:
                    try:
                        templates.extend(scheduler_strategy.load_templates_from_path(path))
                    except Exception as exc:
                        self._logger.warning(
                            "Failed to load templates from %s: %s", path, exc
                        )
                return templates
            else:
                self._logger.warning(
                    "No scheduler strategy available, using fallback templates"
                )
                return self._get_fallback_templates()
        except Exception as exc:
            self._logger.error(
                "Failed to load templates via scheduler strategy: %s", exc
            )
            return self._get_fallback_templates()

    def _get_fallback_templates(self) -> list[dict[str, Any]]:
        return [
            {
                "template_id": "azure-vmss-linux-basic",
                "name": "Azure VMSS Linux Basic",
                "description": "VMSS with Ubuntu 22.04 LTS on Standard_D4s_v5",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.VMSS.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "ssh_key_name": "my-azure-ssh-key",
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 2,
            },
            {
                "template_id": "azure-vmss-spot",
                "name": "Azure VMSS Spot Instances",
                "description": "VMSS with Spot VMs for cost-effective workloads",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.VMSS.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "ssh_key_name": "my-azure-ssh-key",
                "priority": "Spot",
                "eviction_policy": "Deallocate",
                "billing_profile_max_price": -1.0,
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 5,
            },
        ]

    # ------------------------------------------------------------------
    # String representations
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        return (
            f"AzureProviderStrategy(region={self._azure_config.region}, "
            f"initialized={self._initialized})"
        )

    def __repr__(self) -> str:
        return (
            f"AzureProviderStrategy("
            f"region={self._azure_config.region}, "
            f"subscription_id={self._azure_config.subscription_id}, "
            f"initialized={self._initialized}"
            f")"
        )
