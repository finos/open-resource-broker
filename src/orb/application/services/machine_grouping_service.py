"""Service for grouping machines by provider and resource context.

This service extracts machine grouping logic from command handlers,
following the Single Responsibility Principle.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort


class MachineGroupingService:
    """Service for grouping machines by provider and resource context."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        provider_selection_port: ProviderSelectionPort | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            uow_factory: Factory for creating unit of work instances
            logger: Logging port for structured logging
            provider_selection_port: Port used to query provider capabilities
                (e.g. whether a provider supports direct instance-tag
                operations) without the service sniffing machine_id formats.
        """
        self.uow_factory = uow_factory
        self.logger = logger
        self._provider_selection_port = provider_selection_port

    def group_by_provider(self, machine_ids: list[str]) -> dict[tuple[str, str, str], list[str]]:
        """Group machines by (provider_type, provider_name, provider_api).

        provider_api MUST be part of the key — machines from different APIs
        (EC2Fleet, ASG, SpotFleet, RunInstances) require different
        deprovisioning routes even when sharing the same provider+account.
        Lumping them into one bucket produces a return request that can
        carry only one provider_api value, leaving the rest unroutable.

        Args:
            machine_ids: List of machine IDs to group

        Returns:
            Dictionary mapping (provider_type, provider_name, provider_api)
            to list of machine IDs

        Raises:
            EntityNotFoundError: If a machine is not found
            ValueError: If a machine has no provider_api (invariant violation)
        """
        provider_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)

        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", machine_id)

                if not machine.provider_api:
                    # Domain invariant violation: every machine MUST know
                    # which provider API produced it. Raise loudly so the
                    # caller cannot silently route it to a default handler.
                    raise ValueError(
                        f"Machine {machine_id} has no provider_api — "
                        "cannot determine deprovisioning route. This is a "
                        "persistence/migration bug, not a runtime condition."
                    )

                provider_key = (
                    machine.provider_type,
                    machine.provider_name,
                    machine.provider_api,
                )
                provider_groups[provider_key].append(machine_id)

        self.logger.debug(
            "Grouped %d machines into %d provider groups (type, name, api)",
            len(machine_ids),
            len(provider_groups),
        )

        return dict(provider_groups)

    def group_by_resource(
        self, machine_ids: list[str]
    ) -> tuple[dict[tuple[str, str, str], list[Any]], list[str]]:
        """Group machines by (provider_name, provider_api, resource_id).

        This grouping is used for parallel deprovisioning operations where
        machines from the same resource can be terminated together.

        Args:
            machine_ids: List of machine IDs to group

        Returns:
            Tuple of:
            - Dictionary mapping (provider_name, provider_api, resource_id) to list of machine objects
            - List of machine IDs that were skipped (missing provider_api or resource_id)

        Raises:
            ValueError: If machine context cannot be determined
        """
        resource_groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
        skipped_ids: list[str] = []

        for machine_id in machine_ids:
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    machine = uow.machines.find_by_id(machine_id)
                    if not machine:
                        raise ValueError(f"Machine not found: {machine_id}")

                    # Use machine's actual provider context, with fallback for
                    # machines that only have an instance ID (e.g. SLURM node-mapped)
                    provider_api = machine.provider_api
                    resource_id = machine.resource_id
                    if not resource_id:
                        mid_val = getattr(machine.machine_id, "value", str(machine.machine_id))
                        if self._supports_direct_instance_termination(machine.provider_name):
                            # Provider manages raw tagged instances missing
                            # resource context — terminate directly.
                            provider_api = provider_api or ("Run" + "Instances")
                            resource_id = f"direct-{mid_val}"
                        else:
                            self.logger.warning(
                                "Machine %s missing provider context — skipping",
                                machine_id,
                            )
                            skipped_ids.append(machine_id)
                            continue
                    elif not provider_api:
                        self.logger.warning(
                            "Machine %s missing provider context — skipping",
                            machine_id,
                        )
                        skipped_ids.append(machine_id)
                        continue
                    group_key = (
                        machine.provider_name,
                        provider_api,
                        resource_id,
                    )
                    resource_groups[group_key].append(machine)

            except Exception as e:
                self.logger.error(
                    "Failed to get machine context for %s: %s", machine_id, e, exc_info=True
                )
                raise ValueError(f"Cannot determine context for machine {machine_id}: {e}")

        self.logger.info(
            "Grouped machines by resource context: %s",
            {
                f"{pn}-{pa}-{rid}": len(machines)
                for (pn, pa, rid), machines in resource_groups.items()
            },
        )

        return dict(resource_groups), skipped_ids

    def _supports_direct_instance_termination(self, provider_name: str) -> bool:
        """Whether this provider manages raw tagged instances directly.

        Checks the provider's declared TAG_INSTANCES capability rather than
        sniffing the machine_id format (e.g. an "i-" prefix is an AWS
        instance-ID detail that has no business leaking into this layer).
        Returns False (safe default — the caller skips the machine instead
        of guessing) when no provider_selection_port was injected.
        """
        if self._provider_selection_port is None:
            return False
        try:
            from orb.domain.base.operations import OperationType

            capabilities = self._provider_selection_port.get_strategy_capabilities(provider_name)
            return bool(capabilities) and capabilities.supports_operation(
                OperationType.TAG_INSTANCES
            )
        except Exception as e:
            self.logger.debug(
                "Could not determine TAG_INSTANCES capability for provider %s: %s",
                provider_name,
                e,
            )
            return False
