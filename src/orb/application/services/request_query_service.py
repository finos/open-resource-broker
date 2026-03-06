"""Request query service for pure data retrieval."""

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import RequestNotFoundError
from orb.domain.request.request_types import RequestType
from orb.domain.request.value_objects import RequestId


class RequestQueryService:
    """Pure query service - only retrieves data from storage."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
    ) -> None:
        self.uow_factory = uow_factory
        self.logger = logger

    async def get_request(self, request_id: str) -> Request:
        """Get request from storage."""
        with self.uow_factory.create_unit_of_work() as uow:
            request_id_obj = RequestId(value=request_id)
            request = uow.requests.get_by_id(request_id_obj)

            if not request:
                raise RequestNotFoundError(request_id)

            return request

    async def get_machines_for_request(self, request: Request) -> list[Machine]:
        """Get machines from storage for a request."""
        try:
            with self.uow_factory.create_unit_of_work() as uow:
                if request.request_type == RequestType.RETURN:
                    machines = uow.machines.find_by_return_request_id(str(request.request_id.value))
                else:
                    machines = uow.machines.find_by_request_id(str(request.request_id.value))
                self.logger.debug(
                    f"Found {len(machines)} machines for request {request.request_id.value}"
                )
                return machines
        except Exception as e:
            self.logger.error(f"Failed to get machines for request {request.request_id.value}: {e}")
            return []
