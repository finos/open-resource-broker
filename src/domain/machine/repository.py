"""Machine repository interface - contract for machine data access."""

from abc import abstractmethod
from typing import List, Optional

from domain.base.domain_interfaces import AggregateRepository
from domain.base.value_objects import InstanceId

from .aggregate import Machine
from .machine_status import MachineStatus


class MachineRepository(AggregateRepository[Machine]):
    """Repository interface for machine aggregates."""

    @abstractmethod
    def find_by_instance_id(self, instance_id: InstanceId) -> Optional[Machine]:
        """Find machine by instance ID."""

    @abstractmethod
    def find_by_template_id(self, template_id: str) -> List[Machine]:
        """Find machines by template ID."""

    @abstractmethod
    def find_by_status(self, status: MachineStatus) -> List[Machine]:
        """Find machines by status."""

    @abstractmethod
    def find_by_request_id(self, request_id: str) -> List[Machine]:
        """Find machines by request ID."""

    @abstractmethod
    def find_active_machines(self) -> List[Machine]:
        """Find all active (non-terminated) machines."""
