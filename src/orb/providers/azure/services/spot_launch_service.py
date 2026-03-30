"""Azure spot-placement planning and execution helpers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional

from orb.application.services.spot_placement_execution import (
    build_planned_execution_metadata,
    create_acquire_request,
)
from orb.application.services.spot_placement_planner import (
    PlacementPlanEntry,
    PlacementScore,
    SpotPlacementPlanner,
)
from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.azure.infrastructure.services.spot_placement_score_adapter import (
    AzureSpotPlacementScoreAdapter,
)
from orb.providers.base.strategy import ProviderOperation, ProviderResult


AzureProviderApiRef = AzureProviderApi | str


class AzureSpotLaunchService:
    """Own Azure spot-placement planning and planned launch execution."""

    def __init__(
        self,
        *,
        config: AzureProviderConfig,
        logger: LoggingPort,
        planner: SpotPlacementPlanner,
        execution_service: Any,
    ) -> None:
        self._config = config
        self._logger = logger
        self._planner = planner
        self._execution_service = execution_service

    @staticmethod
    def should_use_spot_placement(template: AzureTemplate) -> bool:
        return getattr(template, "allocation_strategy", None) == "spotPlacementScore"

    def build_spot_placement_plan(
        self,
        *,
        azure_template: AzureTemplate,
        count: int,
        azure_client: Any,
    ) -> list[PlacementPlanEntry]:
        adapter = AzureSpotPlacementScoreAdapter(
            azure_client=azure_client,
            logger=self._logger,
            subscription_id=azure_template.subscription_id or self._config.subscription_id,
            base_location=azure_template.location.value or self._config.region,
        )
        scores = adapter.score_candidates(requested_count=count, template=azure_template)
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
    def build_fallback_spot_placement_plan(
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
    def is_capacity_like_failure(child_result: dict[str, Any]) -> bool:
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
        cloned_data = azure_template.model_dump(mode="json", exclude_none=True)
        selected_vm_size = plan_entry.score.candidate.instance_type
        cloned_data["vm_size"] = selected_vm_size
        cloned_data["vm_sizes"] = []
        cloned_data["allocation_strategy"] = "capacityOptimized"
        cloned_data["location"] = (
            plan_entry.score.candidate.region or azure_template.location.value
        )
        cloned_data["zones"] = (
            [plan_entry.score.candidate.zone] if plan_entry.score.candidate.zone else []
        )
        cloned_data["placement_regions"] = []
        cloned_data["placement_zones"] = []
        return AzureTemplate.model_validate(cloned_data)

    def execute_planned_spot_launches(
        self,
        *,
        azure_template: AzureTemplate,
        provider_api: AzureProviderApiRef,
        provider_api_key: str,
        count: int,
        template_config: dict[str, Any],
        operation: ProviderOperation,
        provider_instance_name: str,
        handler: Optional[AzureHandler],
        azure_client: Any,
        plan_override: Optional[list[PlacementPlanEntry]] = None,
        capacity_like_failure_checker: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> ProviderResult:
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )

        plan = plan_override or self.build_spot_placement_plan(
            azure_template=azure_template,
            count=count,
            azure_client=azure_client,
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

        summary = self._execution_service.execute_plan(
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
            launch_child=lambda child_request, child_template: handler.acquire_hosts(
                child_request, child_template
            ),
            is_capacity_like_failure=capacity_like_failure_checker or self.is_capacity_like_failure,
        )

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
                "template_id": azure_template.template_id,
            },
            {
                "operation": "create_instances",
                "template_config": template_config,
                "handler_used": provider_api_key,
                "method": "planned_handler",
                "provider_data": provider_data,
            },
        )
