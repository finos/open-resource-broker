"""Orchestrator for watching request status in a polling loop."""

from __future__ import annotations

import asyncio

from orb.application.dto.queries import (
    GetRequestQuery,
    GetTemplateQuery,
    ListMachinesQuery,
)
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    WatchRequestStatusInput,
    WatchRequestStatusOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.request.request_types import RequestStatus


class WatchRequestStatusOrchestrator(
    OrchestratorBase[WatchRequestStatusInput, WatchRequestStatusOutput]
):
    """Fetch a single request status snapshot for watch display."""

    def __init__(self, query_bus: QueryBusPort, logger: LoggingPort) -> None:
        self._query_bus = query_bus
        self._logger = logger
        self._template_cache: dict[str, tuple[dict[str, int], bool]] = {}

    @staticmethod
    def _is_terminal(status_str: str) -> bool:
        try:
            return RequestStatus(status_str.lower()).is_terminal()
        except ValueError:
            return False

    async def _load_template_weights(self, template_id: str) -> tuple[dict[str, int], bool]:
        """Load machine_types weights from template, cached by template_id."""
        if template_id in self._template_cache:
            return self._template_cache[template_id]
        try:
            template = await self._query_bus.execute(GetTemplateQuery(template_id=template_id))
            mt = getattr(template, "machine_types", None) or {}
            machine_types = dict(mt) if mt else {}
            weighted = bool(machine_types)
            self._template_cache[template_id] = (machine_types, weighted)
            return machine_types, weighted
        except Exception as exc:
            self._logger.warning("Failed to load template %s: %s", template_id, exc)
            self._template_cache[template_id] = ({}, False)
            return {}, False

    async def execute(self, input: WatchRequestStatusInput) -> WatchRequestStatusOutput:  # type: ignore[return]
        # Fetch request (membership + lifecycle) and machine snapshots in parallel.
        # awaits resolve at slightly different timestamps, which is benign for a
        # display loop late arrivals just bucket as "unknown" for one tick.
        # Refresh request: status drives the watch loop's terminal check; skip cache to see live transitions.
        request_query = GetRequestQuery(
            request_id=input.request_id, lightweight=False, skip_cache=True
        )
        # ListMachinesQuery(lightweight=True) skips the AWS describe sync; we only
        # need provider_data already persisted on the Machine aggregate which is immutable post-launch;
        machines_query = ListMachinesQuery(
            request_id=input.request_id, lightweight=True, limit=1000
        )
        result, machine_dtos = await asyncio.gather(
            self._query_bus.execute(request_query),
            self._query_bus.execute(machines_query),
        )

        provider_data_by_id: dict[str, dict] = {
            dto.machine_id: (dto.provider_data or {}) for dto in (machine_dtos or [])
        }

        template_id = getattr(result, "template_id", None)
        machine_types: dict[str, int] = {}
        weighted = False
        if template_id:
            machine_types, weighted = await self._load_template_weights(template_id)

        status_val = getattr(result, "status", None)
        status_str = (
            status_val.value
            if status_val is not None and hasattr(status_val, "value")
            else str(status_val or "unknown")
        )

        requested_count = getattr(result, "requested_count", 0) or 0
        machine_refs = getattr(result, "machine_references", None) or []
        machine_ids = getattr(result, "machine_ids", None) or []
        fulfilled_count = len(machine_refs) or len(machine_ids)

        fulfilled_vcpus = 0
        od_vcpus = 0
        spot_vcpus = 0
        fulfilled_capacity = 0
        od_capacity = 0
        spot_capacity = 0
        od_machines = 0
        spot_machines = 0
        az_stats: dict[str, dict[str, int]] = {}

        for ref in machine_refs:
            instance_type = getattr(ref, "instance_type", None)
            price_type = (getattr(ref, "price_type", None) or "").lower()
            pd = provider_data_by_id.get(getattr(ref, "machine_id", ""), {})
            az = pd.get("availability_zone") or "unknown"
            is_spot = price_type == "spot"

            if is_spot:
                spot_machines += 1
            else:
                od_machines += 1

            if az not in az_stats:
                az_stats[az] = {
                    "od_vcpus": 0,
                    "spot_vcpus": 0,
                    "od_cap": 0,
                    "spot_cap": 0,
                    "od_machines": 0,
                    "spot_machines": 0,
                }

            if is_spot:
                az_stats[az]["spot_machines"] += 1
            else:
                az_stats[az]["od_machines"] += 1

            if instance_type:
                vcpus = pd.get("vcpus") or 0
                fulfilled_vcpus += vcpus
                if is_spot:
                    spot_vcpus += vcpus
                    az_stats[az]["spot_vcpus"] += vcpus
                else:
                    od_vcpus += vcpus
                    az_stats[az]["od_vcpus"] += vcpus
                if weighted and machine_types:
                    weight = machine_types.get(instance_type, 0)
                    fulfilled_capacity += weight
                    if is_spot:
                        spot_capacity += weight
                        az_stats[az]["spot_cap"] += weight
                    else:
                        od_capacity += weight
                        az_stats[az]["od_cap"] += weight

        created_at = getattr(result, "created_at", None)
        created_at_str = created_at.isoformat() if created_at else None

        return WatchRequestStatusOutput(
            request_id=input.request_id,
            status=status_str,
            terminal=self._is_terminal(status_str),
            requested_count=requested_count,
            fulfilled_count=fulfilled_count,
            fulfilled_vcpus=fulfilled_vcpus,
            od_vcpus=od_vcpus,
            spot_vcpus=spot_vcpus,
            fulfilled_capacity=fulfilled_capacity,
            od_capacity=od_capacity,
            spot_capacity=spot_capacity,
            od_machines=od_machines,
            spot_machines=spot_machines,
            weighted=weighted,
            az_stats=az_stats,
            created_at=created_at_str,
        )
