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


class MachineGroupingService:
    """Service for grouping machines by provider and resource context."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort) -> None:
        """Initialize the service.

        Args:
            uow_factory: Factory for creating unit of work instances
            logger: Logging port for structured logging
        """
        self.uow_factory = uow_factory
        self.logger = logger

    def group_by_provider(self, machine_ids: list[str]) -> dict[tuple[str, str], list[str]]:
        """Group machines by (provider_type, provider_name).

        Args:
            machine_ids: List of machine IDs to group

        Returns:
            Dictionary mapping (provider_type, provider_name) to list of machine IDs

        Raises:
            EntityNotFoundError: If a machine is not found
        """
        provider_groups: dict[tuple[str, str], list[str]] = defaultdict(list)

        with self.uow_factory.create_unit_of_work() as uow:
            for machine_id in machine_ids:
                machine = uow.machines.get_by_id(machine_id)
                if not machine:
                    raise EntityNotFoundError("Machine", machine_id)

                provider_key = (machine.provider_type, machine.provider_name)
                provider_groups[provider_key].append(machine_id)

        self.logger.debug(
            "Grouped %d machines into %d provider groups",
            len(machine_ids),
            len(provider_groups),
        )

        return dict(provider_groups)

    def group_by_resource(self, machine_ids: list[str]) -> dict[tuple[str, str, str], list[Any]]:
        """Group machines by (provider_name, provider_api, resource_id).

        This grouping is used for parallel deprovisioning operations where
        machines from the same resource can be terminated together.

        Args:
            machine_ids: List of machine IDs to group

        Returns:
            Dictionary mapping (provider_name, provider_api, resource_id) to list of machine objects

        Raises:
            ValueError: If machine context cannot be determined
        """
        resource_groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)

        for machine_id in machine_ids:
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    machine = uow.machines.find_by_id(machine_id)
                    if not machine:
                        raise ValueError(f"Machine not found: {machine_id}")

                    # Use machine's actual provider context
                    if not machine.provider_api:
                        self.logger.warning(
                            "Machine %s has no provider_api — skipping",
                            machine_id,
                        )
                        continue
                    group_key = (
                        machine.provider_name,
                        machine.provider_api,
                        machine.resource_id,
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

        return dict(resource_groups)
