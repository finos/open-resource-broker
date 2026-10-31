"""Bulk query handlers for CQRS compliance."""

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.bulk_queries import (
    GetMultipleMachinesQuery,
    GetMultipleRequestsQuery,
    GetMultipleTemplatesQuery,
)
from orb.application.dto.bulk_responses import (
    BulkMachineResponse,
    BulkRequestResponse,
    BulkTemplateResponse,
)
from orb.application.ports.query_bus_port import QueryBusPort
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort


@query_handler(GetMultipleRequestsQuery)
class GetMultipleRequestsHandler(BaseQueryHandler[GetMultipleRequestsQuery, BulkRequestResponse]):
    """Handler for bulk request retrieval."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory

        from orb.application.factories.request_dto_factory import RequestDTOFactory
        from orb.application.services.request_query_service import RequestQueryService

        self._query_service = RequestQueryService(uow_factory, logger)
        self._dto_factory = RequestDTOFactory()

    async def execute_query(self, query: GetMultipleRequestsQuery) -> BulkRequestResponse:
        """Execute bulk request retrieval."""
        requests = []
        not_found_ids = []

        for request_id in query.request_ids:
            try:
                request = await self._query_service.get_request(request_id)
                machines = []
                if query.include_machines:
                    machines = await self._query_service.get_machines_for_request(request)

                request_dto = self._dto_factory.create_from_domain(request, machines)
                requests.append(request_dto)
            except EntityNotFoundError:
                not_found_ids.append(request_id)

        return BulkRequestResponse(
            requests=requests,
            found_count=len(requests),
            not_found_ids=not_found_ids,
            total_requested=len(query.request_ids),
        )


@query_handler(GetMultipleTemplatesQuery)
class GetMultipleTemplatesHandler(
    BaseQueryHandler[GetMultipleTemplatesQuery, BulkTemplateResponse]
):
    """Handler for bulk template retrieval."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        query_bus: QueryBusPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        # Reuse the single-template read path (GetTemplateQuery) so bulk
        # retrieval stays consistent with defaults resolution and DTO shaping
        # rather than duplicating that logic here.
        self._query_bus = query_bus

    async def execute_query(self, query: GetMultipleTemplatesQuery) -> BulkTemplateResponse:
        """Execute bulk template retrieval."""
        from orb.application.dto.queries import GetTemplateQuery

        templates = []
        not_found_ids = []

        for template_id in query.template_ids:
            try:
                template = await self._query_bus.execute(
                    GetTemplateQuery(
                        template_id=template_id,
                        provider_name=query.provider_name,
                    )
                )
                if query.active_only and not getattr(template, "is_active", True):
                    not_found_ids.append(template_id)
                    continue

                templates.append(template)
            except EntityNotFoundError:
                not_found_ids.append(template_id)

        return BulkTemplateResponse(
            templates=templates,
            found_count=len(templates),
            not_found_ids=not_found_ids,
            total_requested=len(query.template_ids),
        )


@query_handler(GetMultipleMachinesQuery)
class GetMultipleMachinesHandler(BaseQueryHandler[GetMultipleMachinesQuery, BulkMachineResponse]):
    """Handler for bulk machine retrieval."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        query_bus: QueryBusPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        # Reuse the single-machine read path (GetMachineQuery) so bulk
        # retrieval returns the same MachineDTO shape as the single-fetch path.
        self._query_bus = query_bus

    async def execute_query(self, query: GetMultipleMachinesQuery) -> BulkMachineResponse:
        """Execute bulk machine retrieval."""
        from orb.application.dto.queries import GetMachineQuery

        machines = []
        not_found_ids = []

        for machine_id in query.machine_ids:
            try:
                machine_dto = await self._query_bus.execute(
                    GetMachineQuery(
                        machine_id=machine_id,
                        provider_name=query.provider_name,
                    )
                )
                machines.append(machine_dto)
            except EntityNotFoundError:
                not_found_ids.append(machine_id)

        return BulkMachineResponse(
            machines=machines,
            found_count=len(machines),
            not_found_ids=not_found_ids,
            total_requested=len(query.machine_ids),
        )
