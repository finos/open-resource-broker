from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from ..models.provider.request import Request
from ..models.provider.machine import Machine

class Database(ABC):
    @abstractmethod
    def add_request(self, request: Request) -> None:
        """Add a new request to the database."""
        pass

    @abstractmethod
    def get_request(self, request_id: str) -> Optional[Request]:
        """Retrieve a request from the database by its ID."""
        pass

    @abstractmethod
    def update_request(self, request: Request) -> None:
        """Update an existing request in the database."""
        pass

    @abstractmethod
    def delete_request(self, request_id: str) -> None:
        """Delete a request from the database."""
        pass

    @abstractmethod
    def get_all_requests(self) -> List[Request]:
        """Retrieve all requests from the database."""
        pass

    @abstractmethod
    def get_requests_by_status(self, status: str) -> List[Request]:
        """Retrieve all requests with a specific status."""
        pass

    @abstractmethod
    def add_machine(self, machine: Machine) -> None:
        """Add a new machine to the database."""
        pass

    @abstractmethod
    def get_machine(self, machine_id: str) -> Optional[Machine]:
        """Retrieve a machine from the database by its ID."""
        pass

    @abstractmethod
    def update_machine(self, machine: Machine) -> None:
        """Update an existing machine in the database."""
        pass

    @abstractmethod
    def delete_machine(self, machine_id: str) -> None:
        """Delete a machine from the database."""
        pass

    @abstractmethod
    def get_machines_by_request_id(self, request_id: str) -> List[Machine]:
        """Retrieve all machines associated with a specific request."""
        pass

    @abstractmethod
    def get_machines_by_status(self, status: str) -> List[Machine]:
        """Retrieve all machines with a specific status."""
        pass

    @abstractmethod
    def clean_old_requests(self, max_age_seconds: int) -> None:
        """Remove requests older than the specified age."""
        pass

    @abstractmethod
    def add_or_update_request(self, request: Request) -> None:
        """Add a new request or update an existing one in the database."""
        pass

    @abstractmethod
    def add_machine_to_request(self, request_id: str, machine: Machine) -> None:
        """Add a machine to a specific request in the database."""
        pass

    @abstractmethod
    def remove_machine_from_request(self, request_id: str, machine_id: str) -> None:
        """Remove a machine from a specific request in the database."""
        pass
