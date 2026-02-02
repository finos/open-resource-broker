"""Machine repository interface - contract for machine data access."""

from abc import abstractmethod
from typing import Any, Optional

from domain.base.domain_interfaces import AggregateRepository
from domain.machine.machine_identifiers import MachineId

from .aggregate import Machine
from .machine_status import MachineStatus


class MachineRepository(AggregateRepository[Machine]):
    """Repository interface for machine aggregates."""

    @abstractmethod
    def save_batch(self, machines: list[Machine]) -> list[Any]:
        """Save multiple machines in a single operation."""

    @abstractmethod
    def find_by_instance_id(self, instance_id: MachineId) -> Optional[Machine]:
        """Find machine by instance ID (backward compatibility)."""

    @abstractmethod
    def find_by_machine_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by machine ID."""

    @abstractmethod
    def find_by_template_id(self, template_id: str) -> list[Machine]:
        """Find machines by template ID."""

    @abstractmethod
    def find_by_status(self, status: MachineStatus) -> list[Machine]:
        """Find machines by status."""

    @abstractmethod
    def find_by_request_id(self, request_id: str) -> list[Machine]:
        """Find machines by request ID."""

    @abstractmethod
    def find_active_machines(self) -> list[Machine]:
        """Find all active (non-terminated) machines."""
