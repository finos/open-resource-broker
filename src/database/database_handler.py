from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from .database_handler_interface import BaseDatabaseHandler
from .json_handler import JSONHandler  # Default backend
from ..models.provider.request import Request
from ..models.provider.machine import Machine
from ..models.database_interface import Database
from src.helpers.logger import setup_logging

logger = setup_logging()


class DatabaseHandler(Database):
    """
    A concrete implementation of the Database interface that bridges high-level data operations
    (requests and machines) with low-level database backend operations.
    """

    def __init__(self, backend: BaseDatabaseHandler = None):
        """
        Initialize the DatabaseHandler with a specific backend.

        :param backend: An instance of a class implementing BaseDatabaseHandler.
                        Defaults to JSONHandler if no backend is provided.
        """
        self.backend = backend or JSONHandler()  # Default to JSON-based storage

    # -------------------- Request Operations --------------------

    def add_request(self, request: Request) -> None:
        """
        Add a new request to the database.

        :param request: The Request object to add.
        """
        self.backend.insert("requests", request.requestId, request.to_dict())
        logger.info(f"Added request {request.requestId} to the database.")

    def get_request(self, request_id: str) -> Optional[Request]:
        """
        Retrieve a specific request by its ID.

        :param request_id: The ID of the request to retrieve.
        :return: A Request object or None if not found.
        """
        request_data = self.backend.get("requests", request_id)
        if not request_data:
            return None
        return Request.from_dict(request_data)

    def update_request(self, request: Request) -> None:
        """
        Update an existing request in the database.

        :param request: The updated Request object.
        """
        self.backend.update("requests", request.requestId, request.to_dict())
        logger.info(f"Updated request {request.requestId} in the database.")

    def delete_request(self, request_id: str) -> None:
        """
        Delete a request from the database.

        :param request_id: The unique ID of the request to delete.
        """
        self.backend.delete("requests", request_id)
        logger.info(f"Deleted request {request_id} from the database.")

    def get_all_requests(self) -> List[Request]:
        """
        Retrieve all requests from the database.

        :return: A list of valid Request objects.
        """
        all_requests = []
        for data in self.backend.scan("requests"):
            try:
                all_requests.append(Request.from_dict(data))
            except ValueError as e:
                logger.warning(f"Skipping invalid request entry: {e}")
        return all_requests

    def get_requests_by_status(self, status: str) -> List[Request]:
        """
        Retrieve all requests with a specific status.

        :param status: The status to filter requests by (e.g., "running", "complete").
        :return: A list of Request objects matching the specified status.
        """
        results = self.backend.query("requests", {"status": status})
        return [Request.from_dict(data) for data in results]

    def add_or_update_request(self, request: Request) -> None:
        """
        Add a new request or update an existing one in the database.

        :param request: The Request object to add or update.
        """
        existing_request = self.get_request(request.requestId)
        if existing_request:
            self.update_request(request)
        else:
            self.add_request(request)

    # -------------------- Machine Operations --------------------

    def add_machine(self, machine: Machine) -> None:
        """
        Add a new machine to the machines table in the database.

        :param machine: The Machine object to add.
        """
        self.backend.insert("machines", machine.machineId, machine.to_dict())
        logger.info(f"Added machine {machine.machineId} to the database.")

    def update_machine(self, machine: Machine) -> None:
        """
        Update an existing machine in the machines table.

        :param machine: The updated Machine object.
        """
        self.backend.update("machines", machine.machineId, machine.to_dict())
        logger.info(f"Updated machine {machine.machineId} in the database.")

    def get_machine(self, machine_id: str) -> Optional[Machine]:
        """
        Retrieve a machine from the machines table by its ID.

        :param machine_id: The ID of the machine to retrieve.
        :return: The corresponding Machine object, or None if not found.
        """
        data = self.backend.get("machines", machine_id)
        return Machine.from_dict(data) if data else None

    def update_machine_status(self, machine_id: str, new_status: str) -> None:
        """
        Update the status of a machine in the database.

        :param machine_id: The ID of the machine to update.
        :param new_status: The new status to set for the machine.
        """
        # Retrieve the machine from the database
        logger.debug(f"Attempting to update status for machine ID {machine_id} to {new_status}.")
        machine_data = self.backend.get("machines", machine_id)
        
        if not machine_data:
            logger.error(f"Machine with ID {machine_id} not found in database.")
            raise ValueError(f"Machine with ID {machine_id} not found in database.")

        # Update the status
        logger.debug(f"Current machine data: {machine_data}")
        machine_data["status"] = new_status

        # Save the updated machine back to the database
        self.backend.update("machines", machine_id, machine_data)
        logger.info(f"Updated status of machine {machine_id} to {new_status}.")

    def delete_machine(self, machine_id: str) -> None:
        """
        Delete a machine from the database.

        :param machine_id: The unique ID of the machine to delete.
        """
        self.backend.delete("machines", machine_id)
        logger.info(f"Deleted machine {machine_id} from the database.")

    def get_machines_by_request_id(self, request_id: str) -> List[Machine]:
        """
        Retrieve all machines associated with a specific request.

        :param request_id: The ID of the associated request.
        :return: A list of Machine objects associated with the specified request.
        """
        logger.debug(f"Fetching machines for Request ID: {request_id}")

        results = self.backend.query("machines", {"requestId": request_id})
        if not results:
            logger.warning(f"No machines found for Request ID: {request_id}")

        return [Machine.from_dict(data) for data in results]

    def get_machines_by_status(self, status: str) -> List[Machine]:
        """
        Retrieve all machines with a specific status.

        :param status: The status to filter machines by (e.g., "running", "terminated").
        :return: A list of Machine objects matching the specified status.
        """
        results = self.backend.query("machines", {"status": status})
        return [Machine.from_dict(data) for data in results]

    def list_all_machines(self) -> List[Dict[str, Any]]:
        """
        List all machines in the database.

        :return: A list of all machines.
        """
        return self.backend.scan("machines")

    # -------------------- Cleanup Operations --------------------

    def clean_old_requests(self, max_age_seconds: int) -> None:
        """
        Remove requests older than the specified age.

        :param max_age_seconds: The maximum age (in seconds) of requests to keep.
        """
        current_time = int(datetime.now(timezone.utc).timestamp())
        all_requests = self.backend.scan("requests")
        old_requests = [
            req["requestId"]
            for req in all_requests
            if current_time - req.get("requestedTime", 0) > max_age_seconds
        ]
        for req_id in old_requests:
            self.delete_request(req_id)

    def add_machine_to_request(self, request_id: str, machine: Machine) -> None:
        """
        Add a machine to a specific request in the database.

        :param request_id: The ID of the request to associate the machine with.
        :param machine: The Machine object to add.
        """
        machine.requestId = request_id
        self.add_machine(machine)

    def remove_machine_from_request(self, request_id: str, machine_id: str) -> None:
        """
        Remove a machine from a specific request in the database.

        :param request_id: The ID of the request to remove the machine from.
        :param machine_id: The ID of the machine to remove.
        """
        machine = self.get_machine(machine_id)
        if machine and machine.requestId == request_id:
            self.delete_machine(machine_id)

    def clean_return_requests(self, grace_period_seconds: int) -> None:
        """
        Clean up return requests that have exceeded their grace period.

        :param grace_period_seconds: The grace period (in seconds) for return requests.
        """
        current_time = int(datetime.now(timezone.utc).timestamp())
        return_requests = self.get_requests_by_status("RETURNING")
        for request in return_requests:
            if current_time - request.returnRequestedTime > grace_period_seconds:
                self.delete_request(request.requestId)
                # Also delete associated machines
                machines = self.get_machines_by_request_id(request.requestId)
                for machine in machines:
                    self.delete_machine(machine.machineId)
