"""Azure spot-placement planning and execution helpers."""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol

from orb.providers.azure.services.spot_placement_execution import (
    SpotPlacementExecutionSummary,
    build_planned_execution_metadata,
    create_acquire_request,
)
from orb.providers.azure.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
    SpotPlacementPlanner,
)
from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import (
    AzureLocationName,
    AzureProviderApi,
)
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.azure.infrastructure.services.spot_placement_score_adapter import (
    AzureSpotPlacementScoreAdapter,
)
from orb.domain.request.aggregate import Request
from orb.providers.base.strategy import ProviderOperation, ProviderResult

class SpotPlacementExecutionPort(Protocol):
    """Structural subset of SpotPlacementExecutionService used by Azure spot launches."""

    async def execute_plan_async(
        self,
        plan: list[PlacementPlanEntry],
        total_count: int,
        build_child_template: Callable[[PlacementPlanEntry], AzureTemplate],
        build_child_request: Callable[[int, int], Request],
        launch_child: Callable[[Request, AzureTemplate], Awaitable[Mapping[str, Any]]],
        is_capacity_like_failure: Callable[[dict[str, Any]], bool],
    ) -> SpotPlacementExecutionSummary:
        """Execute a placement plan asynchronously and return the aggregated summary."""
        ...


@dataclass
class AzureSpotPlacementTemplateView:
    """Template view matching the adapter's structural spot-placement input."""

    vm_size: str
    location: AzureLocationName
    placement_regions: list[str]
    placement_zones: list[str]
    zones: list[str]
    candidate_vm_sizes: list[str]


class AzureSpotLaunchService:
    """Own Azure spot-placement planning and planned launch execution."""

    def __init__(
        self,
        *,
        config: AzureProviderConfig,
        logger: LoggingPort,
        planner: SpotPlacementPlanner,
        execution_service: SpotPlacementExecutionPort,
    ) -> None:
        self._config = config
        self._logger = logger
        self._planner = planner
        self._execution_service = execution_service

    @staticmethod
    def should_use_spot_placement(template: AzureTemplate) -> bool:
        """Return whether the template opts into Spot Placement Score planning."""
        return template.spot_placement_score_enabled

    def build_spot_placement_plan(
        self,
        *,
        azure_template: AzureTemplate,
        count: int,
        azure_client: AzureClient | None,
    ) -> list[PlacementPlanEntry]:
        """Score candidate regions/zones and build a placement plan for spot launches."""
        template_view = self._template_view(azure_template)
        if azure_client is None:
            return self._fallback_plan_for_missing_client(template_view, count)

        adapter = AzureSpotPlacementScoreAdapter(
            azure_client=azure_client,
            logger=self._logger,
            subscription_id=azure_template.subscription_id or self._config.subscription_id,
            base_location=azure_template.location.value or self._config.region,
        )
        scores = adapter.score_candidates(requested_count=count, template=template_view)
        return self._plan_from_scores(azure_template=azure_template, count=count, scores=scores)

    async def build_spot_placement_plan_async(
        self,
        *,
        azure_template: AzureTemplate,
        count: int,
        azure_client: AzureClient | None,
    ) -> list[PlacementPlanEntry]:
        """Score candidate regions/zones without blocking the async create flow."""
        template_view = self._template_view(azure_template)
        if azure_client is None:
            return self._fallback_plan_for_missing_client(template_view, count)

        adapter = AzureSpotPlacementScoreAdapter(
            azure_client=azure_client,
            logger=self._logger,
            subscription_id=azure_template.subscription_id or self._config.subscription_id,
            base_location=azure_template.location.value or self._config.region,
        )
        scores = await adapter.score_candidates_async(requested_count=count, template=template_view)
        return self._plan_from_scores(azure_template=azure_template, count=count, scores=scores)

    @staticmethod
    def _template_view(azure_template: AzureTemplate) -> AzureSpotPlacementTemplateView:
        """Build the structural template view used by the scoring adapter."""
        return AzureSpotPlacementTemplateView(
            vm_size=azure_template.vm_size,
            location=azure_template.location,
            placement_regions=list(azure_template.placement_regions or []),
            placement_zones=list(azure_template.placement_zones or []),
            zones=list(azure_template.zones or []),
            candidate_vm_sizes=list(azure_template.candidate_vm_sizes),
        )

    def _fallback_plan_for_missing_client(
        self,
        template_view: AzureSpotPlacementTemplateView,
        count: int,
    ) -> list[PlacementPlanEntry]:
        """Build a deterministic fallback plan when live scoring cannot run."""
        self._logger.warning(
            "Azure client not available; falling back to template candidate order"
        )
        return self.build_fallback_spot_placement_plan(
            self._build_approximate_template_scores(template_view),
            count,
        )

    def _plan_from_scores(
        self,
        *,
        azure_template: AzureTemplate,
        count: int,
        scores: list[PlacementScore],
    ) -> list[PlacementPlanEntry]:
        """Create a placement plan from scores with a deterministic fallback."""
        plan = self._planner.create_plan(
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
            return self.build_fallback_spot_placement_plan(scores, count)
        return []

    @staticmethod
    def _build_approximate_template_scores(
        template: AzureSpotPlacementTemplateView,
    ) -> list[PlacementScore]:
        """Build approximate scores in template order when live scoring cannot run."""
        regions = template.placement_regions or [template.location.value]
        zones = template.placement_zones or template.zones or [None]

        return [
            PlacementScore(
                candidate=PlacementCandidate(
                    candidate_id=f"azure:{region}:{zone or 'regional'}:{vm_size}",
                    instance_type=vm_size,
                    region=region,
                    zone=zone,
                ),
                raw_score="DataNotFoundOrStale",
                normalized_score=0.0,
                approximate=True,
                metadata={
                    "fallback_reason": "azure_client_unavailable",
                    "raw_entry": {"score": "DataNotFoundOrStale"},
                },
            )
            for region in regions
            for vm_size in template.candidate_vm_sizes
            for zone in zones
        ]

    @staticmethod
    def build_fallback_spot_placement_plan(
        scores: list[PlacementScore],
        requested_count: int,
    ) -> list[PlacementPlanEntry]:
        """Build a single-candidate fallback plan when the planner returns no viable entries."""
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
    def is_capacity_like_failure(child_result: dict[str, Any]) -> bool:
        """Return whether the child result error codes indicate a capacity-related failure."""
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
    def clone_template_for_plan_entry(
        azure_template: AzureTemplate,
        plan_entry: PlacementPlanEntry,
    ) -> AzureTemplate:
        """Clone a template with the VM size, location, and zone from a plan entry."""
        cloned_data = azure_template.model_dump(mode="json", exclude_none=True)
        selected_vm_size = plan_entry.score.candidate.instance_type
        cloned_data["vm_size"] = selected_vm_size
        cloned_data["vm_sizes"] = []
        cloned_data["vm_size_preferences"] = []
        cloned_data.pop("allocation_strategy", None)
        cloned_data["spot_placement_score_enabled"] = False
        cloned_data["location"] = (
            plan_entry.score.candidate.region or azure_template.location.value
        )
        cloned_data["zones"] = (
            [plan_entry.score.candidate.zone] if plan_entry.score.candidate.zone else []
        )
        cloned_data["placement_regions"] = []
        cloned_data["placement_zones"] = []
        return AzureTemplate.model_validate(cloned_data)

    @staticmethod
    def _planned_child_result_with_fulfillment(
        *,
        provider_api_key: str,
        requested_count: int,
        raw_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = dict(raw_result)
        if "fulfilled_count" in result:
            return result

        if not result.get("success", True):
            result["fulfilled_count"] = 0
            return result

        if provider_api_key == "CycleCloud":
            provider_data = result.get("provider_data")
            added_count = (
                provider_data.get("added_count")
                if isinstance(provider_data, Mapping)
                else None
            )
            result["fulfilled_count"] = int(added_count or 0)
            return result

        result["fulfilled_count"] = requested_count
        return result

    async def execute_planned_spot_launches_async(
        self,
        *,
        azure_template: AzureTemplate,
        provider_api: AzureProviderApi,
        provider_api_key: str,
        count: int,
        template_config: dict[str, Any],
        operation: ProviderOperation,
        provider_instance_name: str,
        handler: Optional[AzureHandler],
        azure_client: AzureClient | None,
        plan_override: Optional[list[PlacementPlanEntry]] = None,
        capacity_like_failure_checker: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> ProviderResult:
        """Async variant of planned spot launches using the async handler contract."""
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )

        plan = (
            plan_override
            if plan_override is not None
            else await self.build_spot_placement_plan_async(
                azure_template=azure_template,
                count=count,
                azure_client=azure_client,
            )
        )
        if not plan:
            return ProviderResult.error_result(
                "No viable spot placement candidates returned scores",
                "NO_PLACEMENT_CANDIDATES",
            )

        request_metadata = dict(operation.parameters.get("request_metadata", {}) or {})
        base_request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )

        summary = await self._execution_service.execute_plan_async(
            plan=plan,
            total_count=count,
            build_child_template=lambda plan_entry: self.clone_template_for_plan_entry(
                azure_template, plan_entry
            ),
            build_child_request=lambda requested_for_entry, idx: create_acquire_request(
                template_id=azure_template.template_id,
                count=requested_for_entry,
                provider_type="azure",
                provider_name=provider_instance_name,
                provider_api=provider_api_key,
                request_metadata=request_metadata,
                parent_request_id=base_request_id,
                plan_entry_index=idx,
            ),
            launch_child=lambda child_request, child_template: self._launch_planned_child_async(
                handler=handler,
                provider_api_key=provider_api_key,
                child_request=child_request,
                child_template=child_template,
            ),
            is_capacity_like_failure=capacity_like_failure_checker or self.is_capacity_like_failure,
        )
        return self._planned_execution_result(
            plan=plan,
            summary=summary,
            count=count,
            template_config=template_config,
            provider_api_key=provider_api_key,
            template_id=azure_template.template_id,
        )

    async def _launch_planned_child_async(
        self,
        *,
        handler: AzureHandler,
        provider_api_key: str,
        child_request: Request,
        child_template: AzureTemplate,
    ) -> dict[str, Any]:
        """Launch one planned child request through the async Azure handler contract."""
        return self._planned_child_result_with_fulfillment(
            provider_api_key=provider_api_key,
            requested_count=child_request.requested_count,
            raw_result=await handler.acquire_hosts_async(child_request, child_template),
        )

    def _planned_execution_result(
        self,
        *,
        plan: list[PlacementPlanEntry],
        summary: SpotPlacementExecutionSummary,
        count: int,
        template_config: dict[str, Any],
        provider_api_key: str,
        template_id: str,
    ) -> ProviderResult:
        """Build the final ProviderResult for planned spot execution."""
        provider_data = build_planned_execution_metadata(plan, summary)
        provider_data["fulfillment_final"] = (
            bool(summary.resource_ids)
            and summary.unfulfilled_count == 0
            and not summary.terminal_error_message
            and not summary.terminated_early
        )

        if (
            not summary.resource_ids
            and not summary.instances
            and (summary.terminal_error_message or summary.unfulfilled_count > 0)
        ):
            error_message = (
                f"Provisioning failed: {summary.terminal_error_message}"
                if summary.terminal_error_message
                else "Spot placement plan could not provision any instances"
            )
            return ProviderResult.error_result(
                error_message,
                "PROVISIONING_ADAPTER_ERROR",
                {
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api_key,
                    "method": "planned_handler",
                    "provider_data": provider_data,
                },
            )

        return ProviderResult.success_result(
            {
                "resource_ids": summary.resource_ids,
                "instances": summary.instances,
                "provider_api": provider_api_key,
                "count": count,
                "template_id": template_id,
            },
            {
                "operation": "create_instances",
                "template_config": template_config,
                "handler_used": provider_api_key,
                "method": "planned_handler",
                "provider_data": provider_data,
            },
        )
