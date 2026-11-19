"""Keep a request's machines named after its scheduler node names (e.g. Slurm).

``orb machines request <template> N --nodes <hostlist>`` stores the expanded
node names under ``request.metadata["node_names"]``. Each live machine of the
request holds one of them as its ``name`` and as the ``orb:node-name`` tag,
which the Slurm hooks and ``terminate --nodes`` rely on.

The request is the source of truth: ``request.metadata["node_name_assignments"]``
maps machine id to node name. Every write of that map is a version-checked save
of a single request record, so two processes reconciling the same request can
never hand the same name to two machines, whatever the storage backend. Machine
records and provider tags are then brought in line with the map. Both steps are
idempotent and run on every reconcile, so a write that fails or is interrupted
is repaired by the next status sync.

Machines can be recorded from the provider's create response or, for providers
that fulfil asynchronously (Kubernetes pods, AWS Auto Scaling Groups, Spot
Fleets), when a status sync first sees them. Both paths call
:meth:`NodeNameReconciler.reconcile`.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.machine.aggregate import Machine

if TYPE_CHECKING:
    from orb.application.services.provider_registry_service import ProviderRegistryService

NODE_NAMES_METADATA_KEY = "node_names"
ASSIGNMENTS_METADATA_KEY = "node_name_assignments"
ORB_TAG_PREFIX = "orb:"
NODE_NAME_TAG = f"{ORB_TAG_PREFIX}node-name"


def requested_node_names(metadata: Any) -> list[str]:
    """Return the node names recorded on a request's metadata, in request order."""
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get(NODE_NAMES_METADATA_KEY)
    if isinstance(raw, list):
        return [str(name) for name in raw]
    if isinstance(raw, str) and raw:
        return [raw]
    return []


def reconcile_assignments(
    node_names: list[str], assignments: dict[str, str], machines: Sequence[Machine]
) -> dict[str, str]:
    """Return the assignment map that fits the request's current machines.

    Args:
        node_names: The request's node names, in request order.
        assignments: The stored map of machine id to node name.
        machines: The request's machines as stored now.

    A machine in a terminal state releases its name, so a replacement inherits
    the slot. An assignment for a machine not in ``machines`` is kept: the
    machine may be recorded by another process that has not written it yet.
    Live machines without a name take the free names in machine-id order, so the
    result does not depend on the order in which the provider lists machines.
    Machines beyond the number of names keep no assignment.
    """
    by_id: dict[str, Machine] = {}
    for machine in machines:
        by_id.setdefault(str(machine.machine_id.value), machine)

    kept: dict[str, str] = {}
    for machine_id in sorted(assignments):
        name = assignments[machine_id]
        machine = by_id.get(machine_id)
        if name not in node_names or name in kept.values():
            continue
        if machine is not None and machine.status.is_terminal:
            continue
        kept[machine_id] = name

    free = [name for name in node_names if name not in kept.values()]
    for machine_id in sorted(by_id):
        if not free:
            break
        if machine_id in kept or by_id[machine_id].status.is_terminal:
            continue
        kept[machine_id] = free.pop(0)
    return kept


def with_node_name(machine: Machine, name: str) -> Machine:
    """Return ``machine`` carrying ``name`` as its name and ``orb:node-name`` tag."""
    if machine.name == name and machine.tags.tags.get(NODE_NAME_TAG) == name:
        return machine
    return machine.model_copy(update={"name": name, "tags": machine.tags.add(NODE_NAME_TAG, name)})


class NodeNameReconciler:
    """Assigns a request's node names to its machines and keeps them applied."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        provider_registry_service: Optional["ProviderRegistryService"] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._logger = logger
        self._provider_registry_service = provider_registry_service

    async def reconcile(
        self,
        request_id: Any,
        reported: Sequence[Machine] = (),
        *,
        push_tags: bool = True,
    ) -> list[Machine]:
        """Bring the request's machines in line with its node-name assignments.

        Args:
            request_id: The request to reconcile.
            reported: Machines as the provider reported them in this sync. Their
                tags show which assigned names the provider already carries.
            push_tags: Tag provider resources whose reported tags lack their
                node name. The creation path pushes tags itself.

        Returns:
            The machines whose stored record was renamed, as saved.
        """
        assignments = self._update_assignments(request_id)
        if not assignments:
            return []
        renamed = self._apply_assignments(request_id)
        if push_tags:
            await self._push_missing_tags(request_id, assignments, reported)
        return renamed

    def _update_assignments(self, request_id: Any) -> dict[str, str]:
        """Save the request's updated assignment map and return it."""
        for attempt in range(2):
            try:
                with self._uow_factory.create_unit_of_work() as uow:
                    current = uow.requests.get_by_id(request_id)
                    if current is None or current.status.is_terminal():
                        return {}
                    node_names = requested_node_names(current.metadata)
                    if not node_names:
                        return {}
                    stored = dict(current.metadata.get(ASSIGNMENTS_METADATA_KEY) or {})
                    machines = uow.machines.find_by_request_id(str(request_id))
                    updated = reconcile_assignments(node_names, stored, machines)
                    if updated != stored:
                        uow.requests.save(
                            current.update_metadata({ASSIGNMENTS_METADATA_KEY: updated})
                        )
                    return updated
            except ConcurrencyError:
                if attempt == 1:
                    # Another process is reconciling the same request; it or the
                    # next sync will finish the job.
                    self._logger.warning(
                        "Node-name assignment for request %s lost a write race twice; "
                        "leaving it to the next sync",
                        request_id,
                    )
        return {}

    def _apply_assignments(self, request_id: Any) -> list[Machine]:
        """Rename stored machines to match the request's current assignment map."""
        renamed: list[Machine] = []
        try:
            with self._uow_factory.create_unit_of_work() as uow:
                current = uow.requests.get_by_id(request_id)
                if current is None:
                    return []
                assignments = dict(current.metadata.get(ASSIGNMENTS_METADATA_KEY) or {})
                for machine in uow.machines.find_by_request_id(str(request_id)):
                    name = assignments.get(str(machine.machine_id.value))
                    if name is None or machine.status.is_terminal:
                        continue
                    updated = with_node_name(machine, name)
                    if updated is not machine:
                        uow.machines.save(updated)
                        renamed.append(updated)
        except ConcurrencyError:
            self._logger.debug(
                "Machine of request %s changed while being renamed; the next sync retries",
                request_id,
            )
            return []
        return renamed

    async def _push_missing_tags(
        self, request_id: Any, assignments: dict[str, str], reported: Sequence[Machine]
    ) -> None:
        """Tag provider resources whose reported tags lack their assigned node name.

        A compute node's boot script can read the tag to start the scheduler
        daemon under its node name. Providers that cannot tag are skipped.
        Failures are logged; a later sync retries, because the provider will
        still report the tag missing.
        """
        if self._provider_registry_service is None or not reported:
            return
        missing = {
            str(m.machine_id.value): {NODE_NAME_TAG: assignments[str(m.machine_id.value)]}
            for m in reported
            if str(m.machine_id.value) in assignments
            and not m.status.is_terminal
            and m.tags.tags.get(NODE_NAME_TAG) != assignments[str(m.machine_id.value)]
        }
        if not missing:
            return
        provider_name = reported[0].provider_name
        if not provider_name:
            return

        from orb.domain.base.operations import (
            Operation as ProviderOperation,
            OperationType as ProviderOperationType,
        )

        try:
            capabilities = self._provider_registry_service.get_strategy_capabilities(provider_name)
            if not capabilities or not capabilities.supports_operation(
                ProviderOperationType.TAG_INSTANCES
            ):
                return
            operation = ProviderOperation(
                operation_type=ProviderOperationType.TAG_INSTANCES,
                parameters={"instance_tags": missing},
                context={"correlation_id": str(request_id)},
            )
            result = await self._provider_registry_service.execute_operation(
                provider_name, operation
            )
            if result is not None and not getattr(result, "success", True):
                self._logger.error(
                    "Failed to tag %d machines of request %s with node names: %s",
                    len(missing),
                    request_id,
                    getattr(result, "error_message", result),
                )
        except Exception as e:
            self._logger.error(
                "Failed to tag machines of request %s with node names: %s",
                request_id,
                e,
                exc_info=True,
            )
